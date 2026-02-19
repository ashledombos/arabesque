#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arabesque — Live Engine.

Assemble :
  - PriceFeedManager : connexion cTrader, ticks temps réel
  - BarAggregator    : ticks → barres H1 → signaux (via signal_gen Python pur)
  - OrderDispatcher  : surveillance niveaux d'entrée, dispatch multi-comptes
  - create_all_brokers() : tous les comptes (cTrader + TradeLocker)

Flux :
  cTrader ticks → BarAggregator → Signal → OrderDispatcher
                                              → Guards
                                              → cTrader compte 1
                                              → cTrader compte 2
                                              → TradeLocker

Usage :
    python -m arabesque.live.engine
    python -m arabesque.live.engine --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal as sys_signal
from typing import Optional

logger = logging.getLogger("arabesque.live.engine")


class LiveEngine:
    """
    Moteur live : colle PriceFeedManager + BarAggregator + OrderDispatcher + brokers.
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
        self._bar_aggregator = None
        self._dispatcher = None
        self._brokers = {}
        self._running = False
        self._account_refresh_task: Optional[asyncio.Task] = None

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
        if self._running:
            logger.warning("LiveEngine already running")
            return

        logger.info("[Engine] 🚀 Démarrage Arabesque live...")
        logger.info(f"[Engine] Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")

        # 1. Connecter les brokers
        await self._connect_brokers()
        if not self._brokers:
            raise RuntimeError(
                "Aucun broker connecté. Vérifiez config/settings.yaml et config/secrets.yaml."
            )
        logger.info(
            f"[Engine] {len(self._brokers)} broker(s): {list(self._brokers.keys())}"
        )

        # 2. Dispatcher
        self._dispatcher = self._make_dispatcher()

        # 3. BarAggregator branché sur receive_signal
        await self._start_bar_aggregator()

        # 4. Price feed branché sur bar_aggregator.on_tick
        await self._start_price_feed()

        # 5. État initial des comptes
        await self._refresh_account_state()
        self._account_refresh_task = asyncio.create_task(
            self._account_refresh_loop()
        )

        self._running = True
        logger.info(
            "[Engine] ✅ Moteur prêt — "
            "ticks → barres H1 → signaux → ordres multi-comptes"
        )

    async def stop(self) -> None:
        self._running = False
        if self._account_refresh_task:
            self._account_refresh_task.cancel()
        if self._price_feed:
            await self._price_feed.stop()
        for broker_id, broker in self._brokers.items():
            try:
                await broker.disconnect()
            except Exception:
                pass
        logger.info("[Engine] Arrêté.")

    async def run_forever(self) -> None:
        await self.start()
        loop = asyncio.get_event_loop()
        for sig in (sys_signal.SIGINT, sys_signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    sig, lambda: asyncio.create_task(self._shutdown())
                )
            except (NotImplementedError, RuntimeError):
                pass
        try:
            while self._running:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.stop()

    async def _shutdown(self):
        self._running = False

    # ------------------------------------------------------------------
    # API : injection de signaux (tests, replay parquet)
    # ------------------------------------------------------------------

    async def receive_signal(self, signal) -> bool:
        if not self._dispatcher:
            logger.error("[Engine] Dispatcher non initialisé")
            return False
        return await self._dispatcher.receive_signal(signal)

    async def get_stats(self) -> dict:
        stats = {
            "engine": {
                "running": self._running,
                "dry_run": self.dry_run,
                "brokers": list(self._brokers.keys()),
            }
        }
        if self._price_feed:
            stats["price_feed"] = self._price_feed.get_stats()
        if self._bar_aggregator:
            stats["bar_aggregator"] = self._bar_aggregator.get_stats()
        if self._dispatcher:
            stats["dispatcher"] = await self._dispatcher.get_stats()
        return stats

    # ------------------------------------------------------------------
    # Connexion brokers
    # ------------------------------------------------------------------

    async def _connect_brokers(self) -> None:
        from arabesque.broker.factory import create_all_brokers
        brokers_raw = create_all_brokers(self.settings, self.secrets)
        for broker_id, broker in brokers_raw.items():
            try:
                connected = await broker.connect()
                if connected:
                    self._brokers[broker_id] = broker
                    logger.info(f"[Engine] ✅ {broker_id} connecté")
                else:
                    logger.warning(f"[Engine] ❌ {broker_id} connexion échouée")
            except Exception as e:
                logger.error(f"[Engine] ❌ {broker_id}: {e}")

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------

    def _make_dispatcher(self):
        from arabesque.live.order_dispatcher import OrderDispatcher
        from arabesque.guards import PropConfig, ExecConfig

        filters = self.settings.get("filters", {})
        general = self.settings.get("general", {})
        exec_raw = self.settings.get("execution", {})
        delay_cfg = exec_raw.get("delay_between_brokers", {})
        delay_ms = (
            (delay_cfg.get("min_ms", 500), delay_cfg.get("max_ms", 3000))
            if delay_cfg.get("enabled", True) else (0, 0)
        )

        prop_cfg = PropConfig(
            max_daily_dd_pct=filters.get("max_daily_drawdown_percent", 4.0),
            max_total_dd_pct=filters.get("max_total_drawdown_percent", 9.0),
            max_positions=filters.get("max_open_positions", 5),
            max_open_risk_pct=general.get("max_open_risk_pct", 2.0),
            max_daily_trades=filters.get("max_pending_orders", 10),
            risk_per_trade_pct=general.get("risk_percent", 0.5),
        )

        dispatcher = OrderDispatcher(
            brokers=self._brokers,
            instruments_cfg=self.instruments,
            prop_config=prop_cfg,
            delay_ms=delay_ms,
            dry_run=self.dry_run,
            on_order_result=self._on_order_result,
        )
        dispatcher._price_feed = None
        return dispatcher

    # ------------------------------------------------------------------
    # BarAggregator
    # ------------------------------------------------------------------

    async def _start_bar_aggregator(self) -> None:
        from arabesque.live.bar_aggregator import BarAggregator, BarAggregatorConfig

        pf_cfg = self.settings.get("price_feed", {})
        source_broker_id = pf_cfg.get("source_broker", "")
        symbols = pf_cfg.get("symbols", [])

        if not symbols and source_broker_id:
            symbols = [
                sym for sym, data in self.instruments.items()
                if isinstance(data, dict) and source_broker_id in data
            ]

        agg_cfg = BarAggregatorConfig(
            instruments=symbols,
            signal_strategy=self.settings.get("strategy", {}).get("type", "combined"),
        )

        # Le broker source fournit get_history()
        source_broker = self._brokers.get(source_broker_id)

        self._bar_aggregator = BarAggregator(
            config=agg_cfg,
            on_signal=self.receive_signal,  # Signal directement au dispatcher
            broker=source_broker,
        )

        # Précharger l'historique
        await self._bar_aggregator.initialize()
        logger.info(
            f"[Engine] 📊 BarAggregator prêt — {len(symbols)} instrument(s)"
        )

    # ------------------------------------------------------------------
    # Price feed
    # ------------------------------------------------------------------

    async def _start_price_feed(self) -> None:
        from arabesque.live.price_feed import PriceFeedManager

        pf_cfg = self.settings.get("price_feed", {})
        source_broker_id = pf_cfg.get("source_broker", "")

        if not source_broker_id or source_broker_id not in self._brokers:
            logger.warning("[Engine] Price feed désactivé (broker source non trouvé)")
            return

        symbols = self._bar_aggregator.cfg.instruments if self._bar_aggregator else []
        if not symbols:
            logger.warning("[Engine] Aucun symbole à surveiller")
            return

        brokers_cfg = self.settings.get("brokers", {})
        broker_cfg = dict(brokers_cfg.get(source_broker_id, {}))
        if source_broker_id in self.secrets:
            broker_cfg.update(self.secrets[source_broker_id])
        broker_cfg["instruments_mapping"] = {
            sym: data[source_broker_id]
            for sym, data in self.instruments.items()
            if isinstance(data, dict) and source_broker_id in data
        }

        self._price_feed = PriceFeedManager(
            broker_id=source_broker_id,
            broker_cfg=broker_cfg,
            symbols=symbols,
        )

        # Brancher bar_aggregator.on_tick sur chaque symbole
        for sym in symbols:
            await self._price_feed.subscribe(sym, self._bar_aggregator.on_tick)

        # Aussi brancher le dispatcher (pour le déclenchement d'ordres pendants)
        for sym in symbols:
            await self._price_feed.subscribe(sym, self._dispatcher.on_tick)

        self._dispatcher._price_feed = self._price_feed

        await self._price_feed.start()
        logger.info(
            f"[Engine] 📡 Price feed actif — {len(symbols)} symbole(s)"
        )

    # ------------------------------------------------------------------
    # État des comptes
    # ------------------------------------------------------------------

    async def _refresh_account_state(self) -> None:
        if not self._brokers or not self._dispatcher:
            return
        primary_id = list(self._brokers.keys())[0]
        try:
            info = await self._brokers[primary_id].get_account_info()
            if info:
                from arabesque.guards import AccountState
                state = AccountState(
                    balance=info.balance,
                    equity=info.equity,
                    start_balance=info.balance,
                    daily_start_balance=info.balance,
                )
                self._dispatcher.update_account_state(state)
                logger.info(
                    f"[Engine] 💰 {primary_id}: "
                    f"balance={info.balance:.2f} equity={info.equity:.2f} {info.currency}"
                )
        except Exception as e:
            logger.warning(f"[Engine] _refresh_account_state: {e}")

    async def _account_refresh_loop(self) -> None:
        while self._running:
            await asyncio.sleep(300)
            if self._running:
                await self._refresh_account_state()

    # ------------------------------------------------------------------
    # Callback ordre
    # ------------------------------------------------------------------

    async def _on_order_result(self, broker_id, signal, result) -> None:
        status = "✅" if result.success else "❌"
        if result.success:
            logger.info(
                f"[Engine] {status} {broker_id} | {signal.instrument} "
                f"{signal.side.value} order_id={result.order_id}"
            )
        else:
            logger.warning(
                f"[Engine] {status} {broker_id} | {signal.instrument} "
                f"{signal.side.value} FAILED: {result.message}"
            )
        # TODO: notification Telegram/ntfy


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
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--secrets", default="config/secrets.yaml")
    parser.add_argument("--instruments", default="config/instruments.yaml")
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
