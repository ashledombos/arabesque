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
        self._position_monitor = None
        self._brokers = {}
        self._running = False
        self._account_refresh_task: Optional[asyncio.Task] = None
        self._reconcile_task: Optional[asyncio.Task] = None

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

        # 3. Position monitor (BE + trailing live)
        if not self.dry_run:
            self._position_monitor = self._make_position_monitor()

        # 4. BarAggregator branché sur receive_signal
        await self._start_bar_aggregator()

        # 5. Price feed branché sur bar_aggregator.on_tick
        await self._start_price_feed()

        # 6. État initial des comptes
        await self._refresh_account_state()
        self._account_refresh_task = asyncio.create_task(
            self._account_refresh_loop()
        )
        if self._position_monitor:
            # 6b. Réconciliation au démarrage : enregistrer les positions déjà ouvertes
            await self._reconcile_existing_positions()
            self._reconcile_task = asyncio.create_task(
                self._reconcile_loop()
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
        if self._reconcile_task:
            self._reconcile_task.cancel()
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
        if self._position_monitor:
            stats["position_monitor"] = self._position_monitor.get_stats()
        return stats

    # ------------------------------------------------------------------
    # Connexion brokers
    # ------------------------------------------------------------------

    async def _connect_brokers(self) -> None:
        from arabesque.broker.factory import create_all_brokers
        brokers_raw = create_all_brokers(
            self.settings, self.secrets, self.instruments
        )
        for broker_id, broker in brokers_raw.items():
            try:
                connected = await broker.connect()
                if connected:
                    self._brokers[broker_id] = broker
                    mapping_count = len(broker.config.get("instruments_mapping", {}))
                    logger.info(
                        f"[Engine] ✅ {broker_id} connecté "
                        f"({mapping_count} instruments mappés)"
                    )
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

    def _make_position_monitor(self):
        from arabesque.live.position_monitor import LivePositionMonitor, MonitorConfig
        monitor = LivePositionMonitor(
            brokers=self._brokers,
            config=MonitorConfig(),
        )
        logger.info("[Engine] 📋 Position monitor actif (BE 0.3/0.20R + trailing)")
        return monitor

    async def _reconcile_loop(self) -> None:
        """Nettoie périodiquement les positions fermées du monitor."""
        while self._running:
            await asyncio.sleep(120)  # toutes les 2 minutes
            if self._running and self._position_monitor:
                try:
                    await self._position_monitor.reconcile()
                except Exception as e:
                    logger.warning(f"[Engine] Reconcile error: {e}")

    async def _reconcile_existing_positions(self) -> None:
        """Au démarrage, enregistre les positions déjà ouvertes dans le monitor.

        Permet de reprendre le BE/trailing sur des positions ouvertes lors
        d'un redémarrage de l'engine (crash, mise à jour, etc.).
        """
        if not self._position_monitor:
            return

        from arabesque.broker.base import OrderSide
        from arabesque.models import Side

        total = 0
        for broker_id, broker in self._brokers.items():
            try:
                positions = await broker.get_positions()
                if not positions:
                    continue

                for pos in positions:
                    # Convertir OrderSide → Side
                    side = Side.LONG if pos.side == OrderSide.BUY else Side.SHORT

                    # Digits du symbole
                    digits = 5
                    try:
                        sinfo = await broker.get_symbol_info(pos.symbol)
                        if sinfo:
                            digits = sinfo.digits
                    except Exception:
                        pass

                    # SL et TP depuis le broker
                    sl = pos.stop_loss or 0.0
                    tp = pos.take_profit or 0.0

                    if sl <= 0:
                        logger.warning(
                            f"[Engine] ⚠️ Position {pos.symbol} {pos.position_id} "
                            f"sans SL — non enregistrée dans le monitor"
                        )
                        continue

                    self._position_monitor.register_position(
                        broker_id=broker_id,
                        position_id=str(pos.position_id),
                        symbol=pos.symbol,
                        side=side,
                        entry=pos.entry_price,
                        sl=sl,
                        tp=tp,
                        volume=pos.volume,
                        digits=digits,
                    )
                    total += 1
                    logger.info(
                        f"[Engine] 📋 Réconciliation: {pos.symbol} "
                        f"{side.value} entry={pos.entry_price:.5f} "
                        f"SL={sl} TP={tp} vol={pos.volume:.3f}L "
                        f"({broker_id}:{pos.position_id})"
                    )

            except Exception as e:
                logger.error(
                    f"[Engine] Erreur réconciliation {broker_id}: {e}"
                )

        if total:
            logger.info(
                f"[Engine] ✅ Réconciliation: {total} position(s) existante(s) enregistrée(s)"
            )
        else:
            logger.info("[Engine] 📋 Réconciliation: aucune position ouverte")

    # ------------------------------------------------------------------
    # BarAggregator
    # ------------------------------------------------------------------

    async def _start_bar_aggregator(self) -> None:
        from arabesque.live.bar_aggregator import BarAggregator, BarAggregatorConfig

        pf_cfg = self.settings.get("price_feed", {})
        source_broker_id = pf_cfg.get("source_broker", "")

        # Priorité 1 : liste explicite dans price_feed.symbols
        symbols = pf_cfg.get("symbols", []) or []

        # Priorité 2 : instruments avec follow: true (ou follow absent)
        # pour le broker source
        if not symbols and source_broker_id:
            symbols = [
                sym for sym, data in self.instruments.items()
                if (
                    isinstance(data, dict)
                    and source_broker_id in data
                    and data.get("follow", True)
                )
            ]

        agg_cfg = BarAggregatorConfig(
            instruments=symbols,
            signal_strategy=self.settings.get("strategy", {}).get("type", "combined"),
        )

        # Le broker source fournit get_history()
        source_broker = self._brokers.get(source_broker_id)

        # Pré-charger les symboles pour éviter les appels concurrents
        # pendant le chargement de l'historique
        if source_broker and hasattr(source_broker, 'get_symbols'):
            try:
                symbols_list = await source_broker.get_symbols()
                logger.info(
                    f"[Engine] 📋 Symboles pré-chargés: {len(symbols_list)} "
                    f"depuis {source_broker_id}"
                )
            except Exception as e:
                logger.warning(f"[Engine] Pré-chargement symboles: {e}")

        self._bar_aggregator = BarAggregator(
            config=agg_cfg,
            on_signal=self.receive_signal,
            broker=source_broker,
        )

        await self._bar_aggregator.initialize()

        # Enregistrer le callback du position monitor sur chaque fermeture de bougie
        if self._position_monitor:
            self._bar_aggregator.add_bar_closed_callback(
                self._position_monitor.on_bar_closed
            )

        logger.info(
            f"[Engine] 📊 BarAggregator prêt — {len(symbols)} instrument(s): "
            f"{', '.join(symbols)}"
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

        # Réutiliser le broker cTrader déjà connecté pour le price feed
        # (évite une 2e connexion TCP → ALREADY_LOGGED_IN)
        source_broker = self._brokers[source_broker_id]

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
            existing_broker=source_broker,
        )

        for sym in symbols:
            await self._price_feed.subscribe(sym, self._bar_aggregator.on_tick)

        for sym in symbols:
            await self._price_feed.subscribe(sym, self._dispatcher.on_tick)

        # Brancher le position monitor sur les ticks pour BE/trailing en temps réel
        if self._position_monitor:
            for sym in symbols:
                await self._price_feed.subscribe(
                    sym, self._position_monitor.on_tick
                )

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
                # Récupérer les positions ouvertes pour les guards
                open_instruments = []
                open_positions = 0
                open_risk_cash = 0.0
                try:
                    positions = await self._brokers[primary_id].get_positions()
                    open_positions = len(positions)
                    open_instruments = [p.symbol for p in positions]
                    # Estimer le risk cash des positions ouvertes
                    # (approximation: chaque position risque ~$400)
                    open_risk_cash = open_positions * 400.0
                except Exception:
                    pass

                # Compléter avec les positions du monitor si disponible
                if self._position_monitor:
                    for pos in self._position_monitor.open_positions:
                        if pos.symbol not in open_instruments:
                            open_instruments.append(pos.symbol)

                from arabesque.guards import AccountState
                state = AccountState(
                    balance=info.balance,
                    equity=info.equity,
                    start_balance=info.balance,
                    daily_start_balance=info.balance,
                    open_positions=open_positions,
                    open_instruments=open_instruments,
                    open_risk_cash=open_risk_cash,
                )
                self._dispatcher.update_account_state(state)
                logger.debug(
                    f"[Engine] 💰 {primary_id}: "
                    f"balance={info.balance:.2f} equity={info.equity:.2f} {info.currency} "
                    f"| {open_positions} position(s) ouvertes: {open_instruments}"
                )
        except Exception as e:
            logger.warning(f"[Engine] _refresh_account_state: {e}")

    async def _account_refresh_loop(self) -> None:
        while self._running:
            await asyncio.sleep(120)  # Toutes les 2 minutes (positions changent vite)
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
            # Enregistrer la position dans le monitor pour BE/trailing
            if self._position_monitor and result.order_id:
                await self._register_position_in_monitor(
                    broker_id, signal, result
                )
            # Rafraîchir l'état du compte (open_instruments, open_positions)
            # pour que les guards bloquent les doublons
            await self._refresh_account_state()
        else:
            logger.warning(
                f"[Engine] {status} {broker_id} | {signal.instrument} "
                f"{signal.side.value} FAILED: {result.message}"
            )
        # TODO: notification via channels (Telegram/ntfy)
        await self._notify_order(broker_id, signal, result)

    async def _register_position_in_monitor(self, broker_id, signal, result):
        """Enregistre une position fraîchement ouverte dans le position monitor.

        Valide que le fill correspond bien au signal (détection mismatch).
        """
        try:
            broker = self._brokers.get(broker_id)
            if not broker:
                return

            # Chercher la position avec retry (le broker peut mettre du temps)
            entry = signal.close
            volume = 0.01
            found = False
            pos = None

            for attempt in range(3):
                await asyncio.sleep(2.0 * (attempt + 1))
                positions = await broker.get_positions()
                matching = [
                    p for p in positions
                    if str(p.position_id) == str(result.order_id)
                ]
                if matching:
                    pos = matching[0]
                    entry = pos.entry_price
                    volume = pos.volume
                    found = True
                    break

            if not found:
                logger.warning(
                    f"[Engine] Position {result.order_id} not found after 3 attempts, "
                    f"registering with signal values "
                    f"(entry={entry}, vol=estimated)"
                )

            # Validation fill mismatch : si le slip est absurde, c'est un bug de routage
            if found:
                slip = abs(entry - signal.close)
                risk_distance = abs(signal.close - signal.sl) if signal.sl else 1.0
                slip_in_r = slip / risk_distance if risk_distance > 0 else 0

                if slip_in_r > 5.0:
                    logger.error(
                        f"[Engine] 🔴 FILL MISMATCH DÉTECTÉ: {signal.instrument} "
                        f"signal_close={signal.close:.5f} fill_entry={entry:.5f} "
                        f"slip={slip:.5f} ({slip_in_r:.1f}R) — position NON enregistrée. "
                        f"Vérifier manuellement position_id={result.order_id}"
                    )
                    return  # Ne PAS enregistrer une position corrompue

                logger.info(
                    f"[Engine] 📋 Fill confirmé: {signal.instrument} "
                    f"{signal.side.value} {volume:.3f}L "
                    f"entry={entry:.5f} (signal={signal.close:.5f} "
                    f"slip={slip:.5f}) "
                    f"SL={pos.stop_loss} TP={pos.take_profit}"
                )

            # Digits du symbole
            digits = 5  # défaut
            try:
                sinfo = await broker.get_symbol_info(signal.instrument)
                if sinfo:
                    digits = sinfo.digits
            except Exception:
                pass

            # Utiliser SL/TP du broker si disponibles (plus fiables que le signal)
            sl = pos.stop_loss if (found and pos.stop_loss) else signal.sl
            tp = pos.take_profit if (found and pos.take_profit) else signal.tp_indicative

            from arabesque.models import Side
            self._position_monitor.register_position(
                broker_id=broker_id,
                position_id=str(result.order_id),
                symbol=signal.instrument,
                side=signal.side,
                entry=entry,
                sl=sl,
                tp=tp,
                volume=volume,
                digits=digits,
            )
        except Exception as e:
            logger.error(
                f"[Engine] Failed to register position in monitor: {e}"
            )

    async def _notify_order(self, broker_id, signal, result) -> None:
        """Envoie une notification si les channels sont configurés."""
        try:
            notif_settings = self.settings.get("notifications", {})
            if not notif_settings.get("enabled", False):
                return
            if result.success and not notif_settings.get("on_order_placed", True):
                return
            if not result.success and not notif_settings.get("on_order_error", True):
                return

            # Channels : settings en priorité, sinon secrets
            channels = notif_settings.get("channels") or []

            if not channels:
                return

            status = "✅" if result.success else "❌"
            msg = (
                f"{status} {broker_id} | {signal.instrument} "
                f"{signal.side.value}"
            )
            if result.success:
                msg += f" | order_id={result.order_id}"
            else:
                msg += f" | FAILED: {result.message}"

            for channel in channels:
                try:
                    import apprise
                    a = apprise.Apprise()
                    a.add(channel)
                    await a.async_notify(body=msg, title="Arabesque")
                except ImportError:
                    logger.warning(
                        "[Engine] apprise non installé — pip install apprise"
                    )
                    break
                except Exception as e:
                    logger.warning(f"[Engine] Notification échouée ({channel}): {e}")
        except Exception as e:
            logger.warning(f"[Engine] _notify_order: {e}")


# =============================================================================
# CLI
# =============================================================================

# Instruments FTMO viables par défaut (résultat pipeline 2026-02-20)
_DEFAULT_INSTRUMENTS = [
    "AAVUSD","ALGUSD","BCHUSD","DASHUSD","GRTUSD","ICPUSD","IMXUSD",
    "LNKUSD","NEOUSD","NERUSD","SOLUSD","UNIUSD","VECUSD","XAUUSD",
    "XLMUSD","XRPUSD","XTZUSD",
]


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        description="Arabesque Live Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes d'utilisation :
  Dry-run parquet (offline, P2) :
    python -m arabesque.live.engine \
      --source parquet --start 2025-10-01 --end 2026-01-01

  Dry-run cTrader (vrais ticks, P3) :
    python -m arabesque.live.engine --dry-run

  Live (P4, compte test seulement) :
    python -m arabesque.live.engine
""",
    )
    parser.add_argument(
        "--source", choices=["parquet", "ctrader"], default="ctrader",
        help="Source de barres : parquet=replay local offline, ctrader=stream live",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Dry-run cTrader : vrais ticks, zéro ordre envoyé",
    )
    parser.add_argument(
        "--start", default=None,
        help="Début du replay parquet (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end", default=None,
        help="Fin du replay parquet (YYYY-MM-DD, défaut=aujourd'hui)",
    )
    parser.add_argument(
        "--instruments", nargs="+", default=None,
        help="Instruments à trader (défaut: 17 viables du pipeline)",
    )
    parser.add_argument(
        "--strategy", choices=["mean_reversion", "trend", "combined"],
        default="combined",
        help="Stratégie de signal (défaut: combined)",
    )
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--secrets", default="config/secrets.yaml")
    parser.add_argument("--instruments-cfg", default="config/instruments.yaml",
                        dest="instruments_cfg")
    parser.add_argument("--balance", type=float, default=100_000.0,
                        help="Balance de départ pour le dry-run parquet")
    parser.add_argument("--data-root", default=None, dest="data_root",
                        help="Répertoire Parquet (défaut: auto-détection)")
    args = parser.parse_args()

    if args.source == "parquet":
        _run_parquet_replay(args)
    else:
        engine = LiveEngine.from_config(
            settings_path=args.config,
            secrets_path=args.secrets,
            instruments_path=args.instruments_cfg,
            dry_run=args.dry_run,
        )
        asyncio.run(engine.run_forever())


def _run_parquet_replay(args) -> None:
    """Lance un replay complet sur données Parquet — aucune connexion réseau."""
    import logging
    from arabesque.broker.adapters import DryRunAdapter
    from arabesque.guards import PropConfig, ExecConfig, AccountState
    from arabesque.live.parquet_clock import ParquetClock
    from arabesque.webhook.orchestrator import Orchestrator
    from arabesque.config import ArabesqueConfig

    logger = logging.getLogger("arabesque.engine.replay")

    instruments = args.instruments or _DEFAULT_INSTRUMENTS
    logger.info(f"[Replay] Source: parquet | {len(instruments)} instruments")
    logger.info(f"[Replay] Période: {args.start or 'début'} → {args.end or 'fin'}")
    logger.info(f"[Replay] Stratégie: {args.strategy}")

    cfg = ArabesqueConfig(
        mode="dry_run",
        start_balance=args.balance,
        max_daily_dd_pct=4.0,
        max_total_dd_pct=9.0,
        max_positions=5,
        risk_per_trade_pct=0.40,   # v3.3: 0.5→0.40 (DD 10.3%→8.2%)
        max_daily_trades=999,
    )

    broker = DryRunAdapter(start_balance=args.balance)
    brokers = {"dry_run": broker}

    orchestrator = Orchestrator(config=cfg, brokers=brokers)

    if args.strategy == "mean_reversion":
        from arabesque.backtest.signal_gen import BacktestSignalGenerator, SignalGenConfig
        sig_gen = BacktestSignalGenerator(SignalGenConfig(), live_mode=False)
    elif args.strategy == "trend":
        from arabesque.backtest.signal_gen_trend import TrendSignalGenerator, TrendSignalConfig
        sig_gen = TrendSignalGenerator(TrendSignalConfig())
    else:
        from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
        sig_gen = CombinedSignalGenerator()

    clock = ParquetClock(
        instruments=instruments,
        start=args.start,
        end=args.end,
        replay_speed=0.0,
        signal_generator=sig_gen,
        data_root=args.data_root,
    )

    logger.info("[Replay] Démarrage du replay Parquet (Ctrl+C pour arrêter)...")
    clock.run(orchestrator, blocking=True)
    logger.info("[Replay] ✅ Terminé")


if __name__ == "__main__":
    main()
