#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arabesque — Live Engine.

Point d'entrée principal qui assemble :
  - PriceFeedManager : connexion cTrader, souscription ticks
  - OrderDispatcher  : surveillance des niveaux d'entrée, dispatch multi-comptes
  - create_all_brokers() : instanciation de tous les comptes (cTrader + TradeLocker)

Usage :
    python -m arabesque.live.engine
    python -m arabesque.live.engine --dry-run
    python -m arabesque.live.engine --config config/settings.yaml

Pour injecter des signaux depuis l'extérieur (webhook, screener, etc.) :
    engine = LiveEngine.from_config()
    await engine.start()
    await engine.receive_signal(signal)  # depuis n'importe quelle coroutine
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal as sys_signal
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("arabesque.live.engine")


class LiveEngine:
    """
    Moteur live : colle PriceFeedManager + OrderDispatcher + brokers.

    Le flux est entièrement asynchrone :
      1. start() — connecte les brokers, démarre le feed
      2. Les ticks arrivent via price_feed → dispatcher.on_tick()
      3. receive_signal() peut être appelé depuis le webhook ou le screener
      4. stop() — arrêt propre
    """

    def __init__(
        self,
        settings: dict,
        secrets: dict,
        instruments: dict,
        dry_run: bool = False,
    ):
        self.settings = settings
        self.secrets = secrets
        self.instruments = instruments
        self.dry_run = dry_run

        self._price_feed = None
        self._dispatcher = None
        self._brokers = {}
        self._running = False
        self._account_refresh_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        settings_path: str = "config/settings.yaml",
        secrets_path: str = "config/secrets.yaml",
        instruments_path: str = "config/instruments.yaml",
        dry_run: bool = False,
    ) -> "LiveEngine":
        from arabesque.config import load_full_config
        settings, secrets, instruments = load_full_config(
            settings_path, secrets_path, instruments_path
        )
        return cls(settings, secrets, instruments, dry_run=dry_run)

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le moteur : connexion brokers + price feed."""
        if self._running:
            logger.warning("LiveEngine already running")
            return

        logger.info("[Engine] 🚀 Démarrage du moteur Arabesque live...")
        logger.info(f"[Engine] Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")

        # 1. Instancier et connecter tous les brokers
        await self._connect_brokers()

        if not self._brokers:
            raise RuntimeError(
                "Aucun broker connecté. Vérifiez config/settings.yaml et config/secrets.yaml."
            )

        logger.info(f"[Engine] {len(self._brokers)} broker(s) connecté(s): "
                    f"{list(self._brokers.keys())}")

        # 2. Instancier le dispatcher
        from arabesque.live.order_dispatcher import OrderDispatcher
        from arabesque.guards import PropConfig, ExecConfig

        general = self.settings.get("general", {})
        filters = self.settings.get("filters", {})
        exec_cfg_raw = self.settings.get("execution", {})
        delay_cfg = exec_cfg_raw.get("delay_between_brokers", {})
        delay_enabled = delay_cfg.get("enabled", True)
        delay_ms = (
            (delay_cfg.get("min_ms", 500), delay_cfg.get("max_ms", 3000))
            if delay_enabled else (0, 0)
        )

        prop_cfg = PropConfig(
            max_daily_dd_pct=filters.get("max_daily_drawdown_percent", 4.0),
            max_total_dd_pct=filters.get("max_total_drawdown_percent", 9.0),
            max_positions=filters.get("max_open_positions", 5),
            max_open_risk_pct=general.get("max_open_risk_pct", 2.0),
            max_daily_trades=filters.get("max_pending_orders", 10),
            risk_per_trade_pct=general.get("risk_percent", 0.5),
        )

        self._dispatcher = OrderDispatcher(
            brokers=self._brokers,
            instruments_cfg=self.instruments,
            prop_config=prop_cfg,
            delay_ms=delay_ms,
            dry_run=self.dry_run,
            on_order_result=self._on_order_result,
        )
        # Injecter la référence au feed pour accès aux ticks courants
        self._dispatcher._price_feed = None  # sera injecté après start du feed

        # 3. Démarrer le price feed
        await self._start_price_feed()
        self._dispatcher._price_feed = self._price_feed

        # 4. Charger l'état initial des comptes
        await self._refresh_account_state()

        # 5. Tâche de rafraîchissement périodique de l'état des comptes (toutes les 5 min)
        self._account_refresh_task = asyncio.create_task(
            self._account_refresh_loop()
        )

        self._running = True
        logger.info("[Engine] ✅ Moteur démarré — en attente de signaux et de ticks")

    async def stop(self) -> None:
        """Arrêt propre de tous les composants."""
        self._running = False
        logger.info("[Engine] Arrêt en cours...")

        if self._account_refresh_task:
            self._account_refresh_task.cancel()

        if self._price_feed:
            await self._price_feed.stop()

        for broker_id, broker in self._brokers.items():
            try:
                await broker.disconnect()
                logger.info(f"[Engine] {broker_id} déconnecté")
            except Exception as e:
                logger.debug(f"[Engine] Erreur déconnexion {broker_id}: {e}")

        logger.info("[Engine] Arrêté.")

    async def run_forever(self) -> None:
        """Démarre et attend (Ctrl+C pour arrêter)."""
        await self.start()

        loop = asyncio.get_event_loop()
        for sig in (sys_signal.SIGINT, sys_signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    sig, lambda: asyncio.create_task(self._shutdown())
                )
            except (NotImplementedError, RuntimeError):
                pass  # Windows

        try:
            while self._running:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.stop()

    async def _shutdown(self):
        logger.info("[Engine] Signal système reçu, arrêt...")
        self._running = False

    # ------------------------------------------------------------------
    # API : injection de signaux
    # ------------------------------------------------------------------

    async def receive_signal(self, signal) -> bool:
        """
        Injecter un signal dans le dispatcher.
        Appelé depuis le webhook, le screener interne, ou les tests.
        """
        if not self._dispatcher:
            logger.error("[Engine] Dispatcher non initialisé — appeler start() d'abord")
            return False
        return await self._dispatcher.receive_signal(signal)

    async def get_stats(self) -> dict:
        """Statistiques consolidées (feed + dispatcher)."""
        stats = {
            "engine": {
                "running": self._running,
                "dry_run": self.dry_run,
                "brokers": list(self._brokers.keys()),
            }
        }
        if self._price_feed:
            stats["price_feed"] = self._price_feed.get_stats()
        if self._dispatcher:
            stats["dispatcher"] = await self._dispatcher.get_stats()
        return stats

    # ------------------------------------------------------------------
    # Connexion des brokers
    # ------------------------------------------------------------------

    async def _connect_brokers(self) -> None:
        """Instancie et connecte tous les brokers activés."""
        from arabesque.broker.factory import create_all_brokers

        logger.info("[Engine] Connexion des brokers...")
        brokers_raw = create_all_brokers(self.settings, self.secrets)

        for broker_id, broker in brokers_raw.items():
            try:
                logger.info(f"[Engine] Connexion à {broker_id}...")
                connected = await broker.connect()
                if connected:
                    self._brokers[broker_id] = broker
                    logger.info(f"[Engine] ✅ {broker_id} connecté")
                else:
                    logger.warning(f"[Engine] ❌ {broker_id} connexion échouée, ignoré")
            except Exception as e:
                logger.error(f"[Engine] ❌ {broker_id} erreur: {e}")

    # ------------------------------------------------------------------
    # Price feed
    # ------------------------------------------------------------------

    async def _start_price_feed(self) -> None:
        """Démarre le price feed et branche le dispatcher."""
        from arabesque.live.price_feed import PriceFeedManager

        pf_cfg = self.settings.get("price_feed", {})
        source_broker_id = pf_cfg.get("source_broker", "")

        if not source_broker_id:
            logger.warning(
                "[Engine] price_feed.source_broker non défini — "
                "le price feed est désactivé (ordres au marché uniquement)"
            )
            return

        if source_broker_id not in self._brokers:
            logger.warning(
                f"[Engine] Broker source du price feed '{source_broker_id}' "
                f"non connecté — feed désactivé"
            )
            return

        # Symboles à surveiller
        symbols = pf_cfg.get("symbols", [])
        if not symbols:
            symbols = [
                sym for sym, data in self.instruments.items()
                if isinstance(data, dict) and source_broker_id in data
            ]

        # Récupérer le broker déjà connecté (pas en recréer un)
        source_broker = self._brokers[source_broker_id]

        # Construire la config broker pour PriceFeedManager
        brokers_cfg = self.settings.get("brokers", {})
        broker_cfg = dict(brokers_cfg.get(source_broker_id, {}))
        if source_broker_id in self.secrets:
            broker_cfg.update(self.secrets[source_broker_id])

        instruments_mapping = {
            sym: data[source_broker_id]
            for sym, data in self.instruments.items()
            if isinstance(data, dict) and source_broker_id in data
        }
        broker_cfg["instruments_mapping"] = instruments_mapping

        self._price_feed = PriceFeedManager(
            broker_id=source_broker_id,
            broker_cfg=broker_cfg,
            symbols=symbols,
        )

        # Brancher le dispatcher sur chaque symbole
        for sym in symbols:
            await self._price_feed.subscribe(sym, self._dispatcher.on_tick)

        await self._price_feed.start()
        logger.info(
            f"[Engine] 📡 Price feed actif — {len(symbols)} symbole(s) surveillé(s)"
        )

    # ------------------------------------------------------------------
    # État des comptes
    # ------------------------------------------------------------------

    async def _refresh_account_state(self) -> None:
        """Récupère l'état consolidaté de tous les comptes et met à jour le dispatcher."""
        from arabesque.guards import AccountState

        total_balance = 0.0
        total_equity = 0.0
        n_accounts = 0

        for broker_id, broker in self._brokers.items():
            try:
                info = await broker.get_account_info()
                if info:
                    total_balance += info.balance
                    total_equity += info.equity
                    n_accounts += 1
                    logger.debug(
                        f"[Engine] {broker_id}: balance={info.balance:.2f} "
                        f"equity={info.equity:.2f} {info.currency}"
                    )
            except Exception as e:
                logger.warning(f"[Engine] Impossible de lire compte {broker_id}: {e}")

        if n_accounts > 0:
            # Utiliser le premier compte comme référence pour les guards
            # (les guards sont par-compte en réalité, simplification ici)
            primary_id = list(self._brokers.keys())[0]
            primary_broker = self._brokers[primary_id]
            try:
                info = await primary_broker.get_account_info()
                if info and self._dispatcher:
                    from arabesque.guards import AccountState
                    state = AccountState(
                        balance=info.balance,
                        equity=info.equity,
                        start_balance=info.balance,
                        daily_start_balance=info.balance,
                    )
                    self._dispatcher.update_account_state(state)
                    logger.info(
                        f"[Engine] 💰 État compte ({primary_id}): "
                        f"balance={info.balance:.2f} equity={info.equity:.2f} "
                        f"{info.currency}"
                    )
            except Exception as e:
                logger.warning(f"[Engine] _refresh_account_state: {e}")

    async def _account_refresh_loop(self) -> None:
        """Rafraîchit l'état des comptes toutes les 5 minutes."""
        while self._running:
            await asyncio.sleep(300)
            if self._running:
                await self._refresh_account_state()

    # ------------------------------------------------------------------
    # Callback ordre
    # ------------------------------------------------------------------

    async def _on_order_result(
        self, broker_id: str, signal, result
    ) -> None:
        """Appelé après chaque placement d'ordre, pour logging et notifications."""
        status = "✅" if result.success else "❌"
        sym = signal.instrument
        side = signal.side.value

        if result.success:
            logger.info(
                f"[Engine] {status} {broker_id} | {sym} {side} "
                f"order_id={result.order_id}"
            )
        else:
            logger.warning(
                f"[Engine] {status} {broker_id} | {sym} {side} "
                f"FAILED: {result.message}"
            )

        # TODO : envoyer notification Telegram/ntfy si activé


# =============================================================================
# CLI
# =============================================================================

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Arabesque Live Engine")
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Mode simulation : aucun ordre réel envoyé"
    )
    parser.add_argument(
        "--config", default="config/settings.yaml",
        help="Chemin vers settings.yaml (défaut: config/settings.yaml)"
    )
    parser.add_argument(
        "--secrets", default="config/secrets.yaml",
        help="Chemin vers secrets.yaml (défaut: config/secrets.yaml)"
    )
    parser.add_argument(
        "--instruments", default="config/instruments.yaml",
        help="Chemin vers instruments.yaml (défaut: config/instruments.yaml)"
    )
    args = parser.parse_args()

    engine = LiveEngine.from_config(
        settings_path=args.config,
        secrets_path=args.secrets,
        instruments_path=args.instruments,
        dry_run=args.dry_run,
    )

    asyncio.run(engine.run_forever())


if __name__ == "__main__":
    main()
