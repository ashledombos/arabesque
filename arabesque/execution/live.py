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
import json
import logging
import signal as sys_signal
import time
from pathlib import Path
from typing import Optional

from arabesque.notifications import select_notification_channels

logger = logging.getLogger("arabesque.live.engine")
TRADE_JOURNAL_PATH = Path("logs/trade_journal.jsonl")


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
        self._bar_aggregator = None  # Primary aggregator (legacy compat)
        self._bar_aggregators: dict = {}  # (TF, strategy) → BarAggregator
        self._dispatcher = None
        self._position_monitor = None
        self._live_monitor = None
        self._brokers = {}
        self._running = False
        self._account_refresh_task: Optional[asyncio.Task] = None
        self._reconcile_task: Optional[asyncio.Task] = None
        self._snapshot_task: Optional[asyncio.Task] = None
        self._snapshotter = None

        # DD tracking — persistent across refreshes, per-broker
        self._initial_balance: Optional[float] = None      # primary, from accounts.yaml
        self._daily_start_balance: Optional[float] = None   # primary, balance at start of UTC day
        self._daily_start_date: Optional[str] = None        # "YYYY-MM-DD" of current day
        self._accounts_config: dict = {}                    # per-account config cache
        # Per-broker DD tracking (secondary brokers)
        self._broker_initial_balance: dict[str, float] = {}
        self._broker_daily_start_balance: dict[str, float] = {}
        self._broker_daily_start_date: dict[str, str] = {}

        # Pending orders en attente de fill (STOP/LIMIT non immédiats).
        # key = "broker_id:order_id" → {broker_id, order_id, signal, ts}
        # record_entry n'est appelé qu'après confirmation du fill via get_positions().
        self._pending_fills: dict[str, dict] = {}
        self._pending_fills_path = Path("logs/pending_fills.json")

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

        # 3. Live monitor (trade journal, equity, drift, margin, protection)
        self._live_monitor = self._make_live_monitor()

        # 3b. Position monitor (BE + trailing live)
        if not self.dry_run:
            self._position_monitor = self._make_position_monitor()

        # 3c. Wire live monitor dependencies (needs dispatcher + position_monitor)
        if self._live_monitor:
            self._live_monitor.set_dispatcher(self._dispatcher)
            if self._position_monitor:
                self._live_monitor.set_position_monitor(self._position_monitor)

        # 4. BarAggregator branché sur receive_signal
        await self._start_bar_aggregator()

        # 5. Price feed branché sur bar_aggregator.on_tick
        await self._start_price_feed()

        # 6. Initialiser les balances de référence pour le DD
        await self._init_dd_tracking()

        # Restaurer les réservations STOP/LIMIT avant le premier calcul de
        # risque : sinon leurs order_id broker paraissent inconnus au refresh
        # et le compte est bloqué à tort jusqu'au cycle suivant.
        self._load_pending_fills()

        # 6b. État initial des comptes (peut déclencher CAUTION/DANGER)
        await self._refresh_account_state()
        # 6b-bis. Notification de démarrage (résumé tous comptes)
        await self._notify_startup_state()
        if self._position_monitor:
            # 6b. Réconciliation au démarrage : enregistrer les positions déjà ouvertes
            await self._reconcile_existing_positions()
            # 6c. Restaurer l'état sauvegardé (MFE, BE, trailing)
            self._position_monitor.load_state()
            # 6d. Détecter les trades fermés pendant le downtime
            await self._reconcile_missed_exits()
            # 6d-bis. Phase 2.5 — boucle backup BE indépendante du PriceFeed.
            # No-op si live.be_polling_backup=false (défaut).
            try:
                await self._position_monitor.start_be_polling()
            except Exception as e:
                logger.warning(f"[Engine] be_polling start error: {e}")
            # 6e. Snapshots multi-broker des symboles en position
            from arabesque.execution.broker_snapshot import BrokerPriceSnapshotter
            self._snapshotter = BrokerPriceSnapshotter(
                brokers=self._brokers,
                position_monitor=self._position_monitor,
            )

        # Les boucles ci-dessous utilisent ``while self._running``. Elles
        # doivent être créées APRES ce passage à True : auparavant, les awaits
        # de la réconciliation de démarrage donnaient la main au health loop
        # alors que le flag valait encore False, et la task mourait sans log.
        self._running = True
        self._account_refresh_task = asyncio.create_task(
            self._account_refresh_loop()
        )
        if self._position_monitor:
            self._reconcile_task = asyncio.create_task(
                self._reconcile_loop()
            )
            self._snapshot_task = asyncio.create_task(
                self._snapshotter.run_forever(lambda: self._running)
            )
        tf_summary = ", ".join(
            f"{agg._timeframe_label()}/{agg.cfg.signal_strategy}({len(agg.cfg.instruments)})"
            for agg in self._bar_aggregators.values()
        )
        logger.info(
            f"[Engine] ✅ Moteur prêt — "
            f"ticks → barres [{tf_summary}] → signaux → ordres multi-comptes"
        )

    async def stop(self) -> None:
        self._running = False
        # Phase 2.5 — arrêt propre de la boucle polling BE avant tout
        # (avant la déconnexion brokers, pour éviter qu'un cycle en vol
        # tape sur un broker en cours de disconnect).
        if self._position_monitor:
            try:
                await self._position_monitor.stop_be_polling()
            except Exception as e:
                logger.warning(f"[Engine] be_polling stop error: {e}")
            try:
                await self._position_monitor.stop_broker_reconcile()
            except Exception as e:
                logger.warning(f"[Engine] broker_reconcile stop error: {e}")
        # Sauvegarder l'état du position monitor AVANT de déconnecter
        if self._position_monitor:
            try:
                self._position_monitor.save_state()
            except Exception as e:
                logger.error(f"[Engine] Erreur sauvegarde état positions: {e}")
        try:
            self._save_pending_fills()
        except Exception as e:
            logger.error(f"[Engine] Erreur sauvegarde pending_fills: {e}")
        if self._account_refresh_task:
            self._account_refresh_task.cancel()
        if self._reconcile_task:
            self._reconcile_task.cancel()
        if self._snapshot_task:
            self._snapshot_task.cancel()
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
        # Live monitor gate: freeze check
        if self._live_monitor:
            ok, reason = self._live_monitor.should_accept_signal()
            if not ok:
                logger.warning(
                    f"[Engine] 🔒 Signal bloqué par LiveMonitor: "
                    f"{signal.instrument} — {reason}"
                )
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
        if self._live_monitor:
            stats["live_monitor"] = self._live_monitor.get_stats()
        return stats

    # ------------------------------------------------------------------
    # Connexion brokers
    # ------------------------------------------------------------------

    async def _connect_brokers(self) -> None:
        from arabesque.broker.factory import create_all_brokers
        brokers_raw = create_all_brokers(
            self.settings, self.secrets, self.instruments
        )

        max_retries = 5
        retry_delays = [5, 10, 20, 30, 60]  # backoff en secondes

        for broker_id, broker in brokers_raw.items():
            connected = False
            is_ctrader = str(broker.config.get("type", "")).lower() == "ctrader"
            for attempt in range(max_retries):
                try:
                    connected = await broker.connect()
                    if connected:
                        self._brokers[broker_id] = broker
                        mapping_count = len(broker.config.get("instruments_mapping", {}))
                        logger.info(
                            f"[Engine] ✅ {broker_id} connecté "
                            f"({mapping_count} instruments mappés)"
                        )
                        break
                    else:
                        logger.warning(
                            f"[Engine] ❌ {broker_id} connexion échouée "
                            f"(tentative {attempt + 1}/{max_retries})"
                        )
                except Exception as e:
                    logger.error(
                        f"[Engine] ❌ {broker_id}: {e} "
                        f"(tentative {attempt + 1}/{max_retries})"
                    )

                if attempt < max_retries - 1:
                    delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                    if is_ctrader:
                        # Incident reboot 2026-05-27 : une tentative cTrader
                        # ayant timeoute peut finir son auth serveur apres le
                        # retour local. Un retry a 5s recree alors une session
                        # concurrente ALREADY_LOGGED_IN. Le cleanup broker est
                        # complete par cette grace de liberation serveur.
                        delay = max(delay, 60)
                    logger.info(
                        f"[Engine] ⏳ Retry {broker_id} dans {delay}s..."
                    )
                    await asyncio.sleep(delay)

            if not connected:
                logger.error(
                    f"[Engine] ❌ {broker_id} inaccessible après "
                    f"{max_retries} tentatives — moteur démarré sans ce broker"
                )

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------

    def _make_dispatcher(self):
        from arabesque.execution.order_dispatcher import OrderDispatcher
        from arabesque.core.guards import PropConfig, ExecConfig

        filters = self.settings.get("filters", {})
        general = self.settings.get("general", {})
        exec_raw = self.settings.get("execution", {})
        delay_cfg = exec_raw.get("delay_between_brokers", {})
        delay_ms = (
            (delay_cfg.get("min_ms", 500), delay_cfg.get("max_ms", 3000))
            if delay_cfg.get("enabled", True) else (0, 0)
        )

        # Per-account overrides from accounts.yaml (risk, DD limits).
        # The primary config remains the signal-admission default; each
        # broker receives its own config again at the pre-order safety gate.
        primary_id = next(iter(self._brokers), "")
        acct_overrides = self._load_account_overrides(primary_id)

        def make_prop_cfg(overrides: dict) -> PropConfig:
            return PropConfig(
                max_daily_dd_pct=overrides.get(
                    "max_daily_dd_pct",
                    filters.get("max_daily_drawdown_percent", 4.0),
                ),
                max_total_dd_pct=overrides.get(
                    "max_total_dd_pct",
                    filters.get("max_total_drawdown_percent", 9.0),
                ),
                max_positions=filters.get("max_open_positions", 5),
                max_open_risk_pct=general.get("max_open_risk_pct", 2.0),
                max_daily_trades=filters.get("max_pending_orders", 10),
                risk_per_trade_pct=overrides.get(
                    "risk_per_trade_pct",
                    general.get("risk_percent", 0.45),
                ),
            )

        prop_cfg = make_prop_cfg(acct_overrides)
        prop_configs_by_broker = {
            broker_id: make_prop_cfg(self._load_account_overrides(broker_id))
            for broker_id in self._brokers
        }
        logger.info(
            f"[Engine] PropConfig: risk={prop_cfg.risk_per_trade_pct}%, "
            f"daily_dd={prop_cfg.max_daily_dd_pct}%, "
            f"total_dd={prop_cfg.max_total_dd_pct}%"
        )

        # Per-timeframe risk multipliers (e.g. H4 → 1.22 for higher risk)
        tf_risk = general.get("risk_multiplier_by_timeframe", {})
        if tf_risk:
            logger.info(f"[Engine] Risk multipliers by TF: {tf_risk}")

        # Rodage config (risk réduit pour stratégies non validées en live)
        rodage_cfg = self.settings.get("rodage", {})

        # Slippage guard config
        max_slippage_atr = self.settings.get("max_slippage_atr", 0.5)

        dispatcher = OrderDispatcher(
            brokers=self._brokers,
            instruments_cfg=self.instruments,
            prop_config=prop_cfg,
            delay_ms=delay_ms,
            dry_run=self.dry_run,
            on_order_result=self._on_order_result,
            on_slippage_reject=self._on_slippage_reject,
            risk_multiplier_fn=self._get_risk_multiplier,
            risk_multiplier_by_tf=tf_risk,
            rodage_config=rodage_cfg,
            max_slippage_atr=max_slippage_atr,
            max_executed_risk_ratio=exec_raw.get(
                "max_executed_risk_ratio", 1.25
            ),
            settings=self.settings,
            prop_configs_by_broker=prop_configs_by_broker,
        )
        dispatcher._price_feed = None
        return dispatcher

    def _load_account_overrides(self, broker_id: str | None = None) -> dict:
        """Charge les overrides per-account depuis accounts.yaml.

        Champs supportés : risk_per_trade_pct, max_daily_dd_pct, max_total_dd_pct.
        Permet de configurer 0.80% pour un challenge vs 0.45% pour un funded.
        """
        from pathlib import Path
        try:
            import yaml
            path = Path("config/accounts.yaml")
            if not path.exists():
                return {}
            with open(path) as f:
                cfg = yaml.safe_load(f) or {}
            accounts = cfg.get("accounts", {})
            # Default to the primary broker for legacy callers.
            broker_keys = list(self._brokers.keys())
            active_key = broker_id or (broker_keys[0] if broker_keys else "")
            if not active_key:
                return {}
            acct = accounts.get(active_key, {})
            overrides = {}
            for key in ("risk_per_trade_pct", "max_daily_dd_pct", "max_total_dd_pct"):
                if key in acct:
                    overrides[key] = float(acct[key])
                    logger.info(f"[Engine] Account override: {key}={acct[key]}")
            return overrides
        except Exception as e:
            logger.warning(f"[Engine] Could not load account overrides: {e}")
            return {}

    def _get_risk_multiplier(self) -> float:
        """Retourne le multiplicateur de risque du LiveMonitor."""
        if self._live_monitor:
            return self._live_monitor.risk_multiplier
        return 1.0

    def _make_live_monitor(self):
        from arabesque.execution.live_monitor import LiveMonitor, MonitorConfig as LMConfig

        # Notification channels from secrets.yaml
        notif_secrets = self.secrets.get("notifications", {})
        channels = notif_secrets.get("channels", []) or []
        telegram_ch = ""
        ntfy_ch = ""
        for ch in channels:
            if isinstance(ch, str):
                if "tgram://" in ch or "telegram://" in ch:
                    telegram_ch = ch
                elif "ntfy" in ch:
                    ntfy_ch = ch

        default_strategy = (
            self.settings.get("strategy", {}).get("type", "extension")
        )
        assigned_strategies = set(
            (self.settings.get("strategy_assignments", {}) or {}).keys()
        )
        active_strategies = tuple(sorted({default_strategy, *assigned_strategies}))

        cfg = LMConfig(
            telegram_channel=telegram_ch,
            ntfy_channel=ntfy_ch,
            # Cabriole is still present in historical journal records but is
            # disabled in settings. Its old losing streak must not control
            # risk for the Phase 4 bis live strategies.
            consecutive_loss_strategies=active_strategies,
        )
        monitor = LiveMonitor(config=cfg)
        # Inject broker access for active protection (close positions)
        monitor.set_brokers(self._brokers)
        if telegram_ch or ntfy_ch:
            logger.info(
                f"[Engine] 📊 Live monitor actif (protection + notifications: "
                f"{'TG ' if telegram_ch else ''}{'ntfy' if ntfy_ch else ''})"
            )
        else:
            logger.info(
                "[Engine] 📊 Live monitor actif (protection, pas de notifications configurées)"
            )
        logger.info(
            "[Engine] Guard pertes consecutives: strategies actives = "
            f"{', '.join(active_strategies)}"
        )
        return monitor

    def _make_position_monitor(self):
        from arabesque.execution.position_monitor import LivePositionMonitor, MonitorConfig

        # Callback quand une position est fermée → notifier LiveMonitor
        def on_closed(**kwargs):
            if self._live_monitor:
                self._live_monitor.record_exit(**kwargs)

        # Phase 2.5 — audit event JSONL pour BE armé via polling backup
        def on_audit(payload: dict):
            if self._live_monitor:
                self._live_monitor.record_be_polling_armed(payload)

        # Étage 0 (incident DASHUSD 2026-05-21) — notif Telegram+ntfy quand un
        # amend SL est abandonné (max_amend_retries échoués). Cooldown 30 min
        # par position géré dans LivePositionMonitor.
        def on_amend_abandoned(payload: dict):
            if not self._live_monitor:
                return
            sym = payload.get("symbol", "?")
            pid = payload.get("position_id", "?")
            bid = payload.get("broker_id", "?")
            target = payload.get("target_sl", 0.0)
            current = payload.get("current_sl", 0.0)
            err = payload.get("last_error", "")
            mfe = payload.get("mfe_r", 0.0)
            tier = payload.get("trailing_tier", 0)
            be = payload.get("breakeven_set", False)
            msg = (
                f"🚨 SL amend ABANDONED — {sym} ({bid})\n"
                f"position_id={pid} side={payload.get('side', '?')}\n"
                f"SL courant broker={current:.5f} cible={target:.5f}\n"
                f"MFE={mfe:.2f}R BE={'oui' if be else 'non'} trail_tier={tier}\n"
                f"erreur: {err}\n"
                f"→ canal trading injoignable, vérifier journalctl + reconnect"
            )
            asyncio.ensure_future(self._live_monitor._notify_telegram(msg))
            asyncio.ensure_future(self._live_monitor._notify_ntfy(msg))

        # Hot Path #1 (incident DASHUSD 2026-05-21) — alerte URGENT quand le
        # broker confirme qu'une position locale n'existe plus côté serveur
        # après ``broker_reconcile_missing_threshold`` cycles consécutifs.
        # Pas de cooldown : 1 seul event par position (retrait immédiat).
        def on_position_missing_broker(payload: dict):
            if not self._live_monitor:
                return
            sym = payload.get("symbol", "?")
            pid = payload.get("position_id", "?")
            bid = payload.get("broker_id", "?")
            entry = payload.get("entry", 0.0)
            sl = payload.get("sl", 0.0)
            mfe = payload.get("mfe_r", 0.0)
            be = payload.get("breakeven_set", False)
            cycles = payload.get("missing_cycles", 0)
            msg = (
                f"🚨 URGENT — position absente broker — {sym} ({bid})\n"
                f"position_id={pid} side={payload.get('side', '?')}\n"
                f"entry={entry:.5f} SL={sl:.5f} MFE_max={mfe:.2f}R "
                f"BE={'oui' if be else 'non'}\n"
                f"absente depuis {cycles} cycles ReconcileReq consécutifs\n"
                f"→ fermée broker-side à notre insu (SL/TP touché, "
                f"close manuel ou liquidation) ; lance /suivi pour reconstituer"
            )
            asyncio.ensure_future(self._live_monitor._notify_telegram(msg))
            asyncio.ensure_future(self._live_monitor._notify_ntfy(msg))

        live_cfg = self.settings.get("live", {}) or {}
        monitor_cfg = MonitorConfig(
            be_polling_enabled=bool(live_cfg.get("be_polling_backup", False)),
            be_polling_interval_s=float(live_cfg.get("be_polling_interval_s", 60.0)),
            be_polling_freshness_threshold_s=float(
                live_cfg.get("be_polling_freshness_threshold_s", 300.0)
            ),
            broker_reconcile_enabled=bool(
                live_cfg.get("broker_reconcile_active", False)
            ),
            broker_reconcile_interval_s=float(
                live_cfg.get("broker_reconcile_interval_s", 60.0)
            ),
            broker_reconcile_timeout_s=float(
                live_cfg.get("broker_reconcile_timeout_s", 10.0)
            ),
            broker_reconcile_missing_threshold=int(
                live_cfg.get("broker_reconcile_missing_threshold", 3)
            ),
        )
        monitor = LivePositionMonitor(
            brokers=self._brokers,
            config=monitor_cfg,
            on_position_closed=on_closed,
            on_audit_event=on_audit,
            on_amend_abandoned=on_amend_abandoned,
            on_position_missing_broker=on_position_missing_broker,
        )
        if monitor_cfg.be_polling_enabled:
            logger.info(
                "[Engine] 📋 Position monitor actif (BE 0.3/0.20R + trailing + polling backup ON)"
            )
        else:
            logger.info("[Engine] 📋 Position monitor actif (BE 0.3/0.20R + trailing)")
        return monitor

    async def _reconcile_loop(self) -> None:
        """Nettoie périodiquement les positions fermées du monitor.

        Détecte aussi les fills tardifs des ordres STOP/LIMIT (Cabriole Donchian
        breakout : un STOP peut prendre plusieurs heures avant d'être touché).
        """
        while self._running:
            await asyncio.sleep(120)  # toutes les 2 minutes
            if not self._running:
                continue
            if self._position_monitor:
                try:
                    await self._position_monitor.reconcile()
                except Exception as e:
                    logger.warning(f"[Engine] Reconcile error: {e}")
            # Pending fills : détecte les STOP/LIMIT qui viennent de toucher
            try:
                await self._poll_pending_fills()
            except Exception as e:
                logger.warning(f"[Engine] Pending fills poll error: {e}")

    async def _poll_pending_fills(self) -> None:
        """Vérifie si les ordres pending sont fillés. Loggue entry à ce moment.

        Pour chaque pending : appelle ``broker.get_positions()`` et cherche un
        ``position_id`` qui matche l'``order_id`` placé. Si trouvé, loggue
        ``record_entry`` + register_position. Si l'ordre a > 24h ET qu'il n'est
        plus dans la liste des pending broker-side, loggue ``pending_expired``
        et retire de ``_pending_fills``.
        """
        if not self._pending_fills:
            return

        from arabesque.core.models import Side

        items = list(self._pending_fills.items())
        changed = False
        for key, info in items:
            broker_id = info["broker_id"]
            order_id = info["order_id"]
            broker = self._brokers.get(broker_id)
            if not broker:
                continue
            try:
                positions = await broker.get_positions()
            except Exception as e:
                logger.debug(f"[Engine] poll {broker_id} get_positions: {e}")
                continue

            match = next(
                (p for p in positions if str(p.position_id) == str(order_id)),
                None,
            )
            resolved_position_id = str(order_id)
            if match is None:
                # TradeLocker pending orders receive an order ID while they
                # are working, then expose a distinct position ID after fill.
                # Without this lookup the filled position is never registered
                # and may be auto-closed as an orphan (XAUUSD, 2026-05-26).
                resolve_position_id = getattr(
                    broker, "resolve_position_id_from_order_id", None
                )
                if resolve_position_id:
                    try:
                        resolved = await resolve_position_id(str(order_id))
                    except Exception as e:
                        logger.warning(
                            f"[Engine] Pending fill position lookup failed "
                            f"for {broker_id}:{order_id}: {e}"
                        )
                        resolved = None
                    if resolved:
                        resolved_position_id = str(resolved)
                        match = next(
                            (p for p in positions
                             if str(p.position_id) == resolved_position_id),
                            None,
                        )
            if match:
                # Fill confirmé. Reconstruit un signal-like objet pour record_entry.
                class _StubSignal:
                    pass
                stub = _StubSignal()
                stub.signal_id = info.get("signal_id", "")
                stub.instrument = info["instrument"]
                stub.strategy_type = info.get("strategy_type", "unknown")
                stub.side = Side.LONG if info["side"].upper() == "LONG" else Side.SHORT
                stub.close = info["signal_close"]
                stub.sl = info["signal_sl"]
                stub.tp_indicative = info.get("signal_tp", 0.0)
                slip_in_r = await self._flag_extreme_fill_if_needed(
                    broker_id, stub, resolved_position_id, match.entry_price
                )
                effective_sl, effective_tp = await self._confirm_post_fill_protection(
                    broker_id, broker, stub, resolved_position_id, match
                )

                if self._live_monitor:
                    bid_e, ask_e = 0.0, 0.0
                    try:
                        tick = await broker.get_quote(info["instrument"])
                        if tick:
                            bid_e, ask_e = float(tick.bid or 0), float(tick.ask or 0)
                    except Exception:
                        pass
                    self._live_monitor.record_entry(
                        signal=stub,
                        broker_id=broker_id,
                        position_id=resolved_position_id,
                        entry_price=match.entry_price,
                        volume=match.volume,
                        risk_cash=info.get("risk_cash", 0.0),
                        broker_bid=bid_e,
                        broker_ask=ask_e,
                    )

                if self._position_monitor:
                    digits = 5
                    try:
                        sinfo = await broker.get_symbol_info(info["instrument"])
                        if sinfo:
                            digits = sinfo.digits
                    except Exception:
                        pass
                    self._position_monitor.register_position(
                        broker_id=broker_id,
                        position_id=resolved_position_id,
                        symbol=info["instrument"],
                        side=stub.side,
                        entry=match.entry_price,
                        sl=effective_sl,
                        tp=effective_tp,
                        volume=match.volume,
                        digits=digits,
                    )

                logger.info(
                    f"[Engine] 🎯 Pending fill confirmé: {info['instrument']} "
                    f"{info['side']} entry={match.entry_price:.5f} "
                    f"slip={slip_in_r:.2f}R "
                    f"({broker_id}:order={order_id} position={resolved_position_id})"
                )
                self._pending_fills.pop(key, None)
                changed = True
                continue

            # Pas trouvé : après 24h, ne libérer le budget que si le broker
            # confirme que l'ordre n'est plus working. L'âge seul n'est pas
            # une preuve qu'un STOP/LIMIT serveur a expiré.
            age = time.time() - info.get("ts_placed", time.time())
            if age > 24 * 3600:
                try:
                    pending_orders = await broker.get_pending_orders()
                except Exception as e:
                    logger.warning(
                        f"[Engine] Pending expiry non confirme "
                        f"{broker_id}:{order_id}: {e}"
                    )
                    continue
                if any(str(order.order_id) == str(order_id) for order in pending_orders):
                    continue
                if self._live_monitor:
                    self._live_monitor.record_pending_expired(
                        broker_id, str(order_id), info["instrument"],
                        reason="timeout_24h",
                    )
                logger.info(
                    f"[Engine] ⌛ Pending expiré (24h): "
                    f"{info['instrument']} ({broker_id}:{order_id})"
                )
                self._pending_fills.pop(key, None)
                changed = True

        if changed:
            self._save_pending_fills()

    def _save_pending_fills(self) -> None:
        try:
            self._pending_fills_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._pending_fills_path, "w") as f:
                json.dump(self._pending_fills, f)
        except Exception as e:
            logger.warning(f"[Engine] save pending_fills: {e}")

    def _load_pending_fills(self) -> None:
        if not self._pending_fills_path.exists():
            return
        try:
            with open(self._pending_fills_path) as f:
                self._pending_fills = json.load(f) or {}
            if self._pending_fills:
                logger.info(
                    f"[Engine] Restauré {len(self._pending_fills)} pending fill(s) "
                    f"depuis {self._pending_fills_path}"
                )
        except Exception as e:
            logger.warning(f"[Engine] load pending_fills: {e}")
            self._pending_fills = {}

    async def _reconcile_existing_positions(self) -> None:
        """Au démarrage, enregistre les positions déjà ouvertes dans le monitor.

        Permet de reprendre le BE/trailing sur des positions ouvertes lors
        d'un redémarrage de l'engine (crash, mise à jour, etc.). Recharge
        également les entries encore ouvertes dans LiveMonitor : sans cette
        étape, le health report annoncerait zéro position après reboot et
        ``record_exit`` ignorerait leur fermeture jusqu'au reboot suivant.
        """
        if not self._position_monitor:
            return

        from arabesque.broker.base import OrderSide
        from arabesque.core.models import Side

        journal_open_entries: dict[str, dict] = {}
        live_monitor = getattr(self, "_live_monitor", None)
        if live_monitor and TRADE_JOURNAL_PATH.exists():
            journal_exits: set[str] = set()
            with open(TRADE_JOURNAL_PATH) as journal:
                for line in journal:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    broker_id = record.get("broker_id", "")
                    position_id = record.get("position_id", "")
                    if not broker_id or not position_id:
                        continue
                    key = f"{broker_id}:{position_id}"
                    if record.get("event") == "entry":
                        journal_open_entries[key] = record
                    elif record.get("event") == "exit":
                        journal_exits.add(key)
            journal_open_entries = {
                key: record for key, record in journal_open_entries.items()
                if key not in journal_exits
            }

        total = 0
        unavailable_brokers: list[str] = []
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
                        # TradeLocker exposes attached protective orders
                        # separately from the Position payload.  Recover them
                        # on restart so a protected live position keeps BE /
                        # trailing surveillance instead of being abandoned.
                        read_protection = getattr(
                            broker, "get_position_protection", None
                        )
                        if read_protection:
                            try:
                                protection = await read_protection(
                                    str(pos.position_id)
                                )
                            except Exception as e:
                                logger.warning(
                                    f"[Engine] Protection lookup failed for "
                                    f"{broker_id}:{pos.position_id}: {e}"
                                )
                                protection = None
                            if protection:
                                sl = protection[0] or sl
                                tp = protection[1] or tp
                                logger.info(
                                    f"[Engine] Protection récupérée via ordres "
                                    f"liés: {pos.symbol} {pos.position_id} "
                                    f"SL={sl} TP={tp} ({broker_id})"
                                )

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
                    if live_monitor:
                        journal_key = f"{broker_id}:{pos.position_id}"
                        entry_record = journal_open_entries.get(journal_key)
                        if entry_record:
                            live_monitor.restore_open_trade(entry_record)
                        else:
                            logger.warning(
                                f"[Engine] Position ouverte sans entry journal "
                                f"à restaurer: {broker_id}:{pos.position_id}"
                            )
                    total += 1
                    logger.info(
                        f"[Engine] 📋 Réconciliation: {pos.symbol} "
                        f"{side.value} entry={pos.entry_price:.5f} "
                        f"SL={sl} TP={tp} vol={pos.volume:.3f}L "
                        f"({broker_id}:{pos.position_id})"
                    )

            except Exception as e:
                unavailable_brokers.append(broker_id)
                logger.error(
                    f"[Engine] Erreur réconciliation {broker_id}: {e}"
                )

        if total:
            logger.info(
                f"[Engine] ✅ Réconciliation: {total} position(s) existante(s) enregistrée(s)"
            )
        elif unavailable_brokers:
            logger.warning(
                "[Engine] Réconciliation incomplète - état positions inconnu "
                f"pour {unavailable_brokers}"
            )
        else:
            logger.info("[Engine] 📋 Réconciliation: aucune position ouverte")

    async def _reconstruct_exit_from_history(
        self,
        broker_id: str,
        position_id: str,
        entry_record: dict,
    ) -> dict:
        """Reconstruit l'exit d'une position fermée pendant un downtime.

        Sources, par ordre :
          1. broker.get_closed_position_detail() → vrai prix d'exit + ts
          2. bars min1 (parquet) entre entry_ts et exit_ts → vrai MFE
          3. fallback estimé (ancien comportement) si rien de tout cela

        Retourne dict {exit_price, exit_reason, mfe_r, be_set, trailing_tier,
        source}. Le préfixe "reconciled_*" est conservé sur exit_reason pour
        identifier les exits non observés en temps réel.
        """
        import pandas as pd

        instrument = entry_record.get("instrument", "")
        side_str = str(entry_record.get("side", "LONG")).upper()
        entry_price = entry_record.get("entry_price", 0.0)
        sl = entry_record.get("sl", 0.0)
        tp = entry_record.get("tp", 0.0)
        entry_ts_str = entry_record.get("ts", "")

        is_long = side_str == "LONG"
        R = abs(entry_price - sl) if sl else 0.0

        # 1. Vrai prix d'exit côté broker
        real_fill = None
        broker = self._brokers.get(broker_id)
        if broker:
            try:
                real_fill = await broker.get_closed_position_detail(position_id)
            except Exception as e:
                logger.debug(
                    f"[Reconcile] get_closed_position_detail failed for "
                    f"{position_id}: {e}"
                )
        real_exit_price = real_fill.get("exit_price") if real_fill else None
        real_exit_time = real_fill.get("exit_time") if real_fill else None
        # P&L réalisé broker (additif, best-effort) — None si broker injoignable
        real_gross_profit = real_fill.get("gross_profit") if real_fill else None
        real_commission = real_fill.get("commission") if real_fill else None
        real_swap = real_fill.get("swap") if real_fill else None

        # 2. Reconstruction MFE depuis bars min1
        mfe_r = 0.0
        bars_used = 0
        if R > 0 and entry_ts_str and instrument:
            try:
                from arabesque.data.store import load_ohlc
                entry_ts = pd.Timestamp(entry_ts_str)
                if entry_ts.tzinfo is None:
                    entry_ts = entry_ts.tz_localize("UTC")
                if real_exit_time:
                    end_ts = pd.Timestamp(real_exit_time)
                    if end_ts.tzinfo is None:
                        end_ts = end_ts.tz_localize("UTC")
                else:
                    end_ts = pd.Timestamp.now(tz="UTC")
                start_str = (entry_ts - pd.Timedelta(hours=2)).strftime("%Y-%m-%d")
                end_str = (end_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                df = load_ohlc(
                    instrument, interval="1m", start=start_str, end=end_str
                )
                if df is not None and len(df) > 0:
                    df = df[(df.index >= entry_ts) & (df.index <= end_ts)]
                    bars_used = len(df)
                    if bars_used > 0:
                        if is_long:
                            max_fav = float(df["High"].max())
                            mfe_r = (max_fav - entry_price) / R
                        else:
                            min_fav = float(df["Low"].min())
                            mfe_r = (entry_price - min_fav) / R
                        mfe_r = max(mfe_r, 0.0)
            except Exception as e:
                logger.debug(
                    f"[Reconcile] bars reconstruction failed for "
                    f"{instrument}: {e}"
                )

        be_set = mfe_r >= 0.3

        # 3. Choisir exit_price + reason
        if real_exit_price:
            exit_price = real_exit_price
            be_target = entry_price + (0.20 * R if is_long else -0.20 * R)
            tol = 0.30 * R if R > 0 else 0.0
            tp_hit = (
                tp > 0 and R > 0 and (
                    (is_long and exit_price >= tp - tol)
                    or (not is_long and exit_price <= tp + tol)
                )
            )
            sl_hit = (
                sl > 0 and R > 0 and (
                    (is_long and exit_price <= sl + tol)
                    or (not is_long and exit_price >= sl - tol)
                )
            )
            if tp_hit:
                exit_reason = "reconciled_take_profit"
            elif be_set and abs(exit_price - be_target) <= tol:
                exit_reason = "reconciled_breakeven_exit"
            elif sl_hit:
                exit_reason = "reconciled_stop_loss"
            else:
                exit_reason = "reconciled_other"
            source = "broker_detail"
        elif be_set:
            # Pas de confirmation broker mais MFE >= 0.3R observé → BE armé
            # avant la coupure ; SL avait été remonté à entry + 0.20R, donc
            # exit probable = BE.
            exit_price = entry_price + (0.20 * R if is_long else -0.20 * R)
            exit_reason = "reconciled_breakeven_exit"
            source = "bars_reconstruction"
        else:
            # Fallback : pas de broker, pas de MFE significatif → SL plein
            exit_price = sl
            exit_reason = "reconciled_stop_loss"
            source = "estimated_fallback"

        # 4. Dériver be_source — sémantique du tracking BE (cf. DECISIONS.md §3
        # "be_source"). Taxonomie STRICTE :
        #   - broker_armed : RÉSERVÉ au path live (position_monitor.
        #     _check_breakeven) après succès observé de amend_position_sltp.
        #     JAMAIS posé dans ce path reconcile post-hoc.
        #   - broker_evidence : reconcile, broker_detail confirme exit ≈
        #     be_target → preuve forte indirecte que le SL a été amendé
        #     (sinon l'exit serait au SL plein ou TP). Distinct de
        #     broker_armed : on n'a PAS observé l'amend, on le DÉDUIT.
        #   - inferred_from_mfe : MFE parquet >= seuil sans preuve broker
        #     (exit ≠ be_target OU pas de broker_detail). Pattern XAUUSD
        #     14-05 : engine down 7h36 → _check_breakeven jamais appelé →
        #     SL plein hit malgré MFE=0.91R observé post-hoc.
        #   - not_armed : ni preuve, ni inférence.
        # be_set (mfe_r >= 0.3) reste pour rétrocompat, mais be_source fait
        # foi dans les invariants critiques.
        if real_exit_price:
            if exit_reason == "reconciled_breakeven_exit":
                be_source = "broker_evidence"
            elif be_set:
                # MFE >= 0.3 mais broker a confirmé exit ailleurs (SL/TP/other).
                # → SL n'a pas été amendé en pratique (sinon exit aurait été
                # à be_target). BE est purement théorique.
                be_source = "inferred_from_mfe"
            else:
                be_source = "not_armed"
        elif source == "bars_reconstruction":
            # Pas de broker, MFE >= 0.3 → on suppose BE armé avant coupure.
            # C'est une INFÉRENCE depuis les bars parquet, pas un état broker.
            be_source = "inferred_from_mfe"
        else:
            # estimated_fallback : ni broker, ni MFE → pas armé
            be_source = "not_armed"

        return {
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "mfe_r": round(mfe_r, 3),
            "be_set": be_set,
            "be_source": be_source,
            "trailing_tier": 0,
            "source": source,
            "bars_used": bars_used,
            "real_fill": bool(real_fill),
            # P&L réalisé broker (additif, best-effort) — None si broker injoignable
            "gross_profit": real_gross_profit,
            "commission": real_commission,
            "swap": real_swap,
        }

    async def _reconcile_missed_exits(self) -> None:
        """Détecte les trades entrés avant le dernier arrêt mais fermés pendant le downtime.

        Lit le journal pour trouver les entry sans exit correspondant,
        vérifie si la position existe encore chez le broker, et logue
        l'exit manquant si la position a disparu. Le vrai prix et le MFE
        sont reconstruits via broker.get_closed_position_detail() et les
        bars min1 (parquet) — cf. _reconstruct_exit_from_history().
        """
        import json
        from pathlib import Path

        journal_path = Path("logs/trade_journal.jsonl")
        if not journal_path.exists():
            return

        # Collecter les entries et exits du journal (par clé broker:position)
        entries_by_key: dict[str, dict] = {}
        exits_keys: set[str] = set()

        with open(journal_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = record.get("event")
                broker_id = record.get("broker_id", "")
                position_id = record.get("position_id", "")
                if not broker_id or not position_id:
                    continue
                key = f"{broker_id}:{position_id}"
                if event == "entry":
                    entries_by_key[key] = record
                elif event == "exit":
                    exits_keys.add(key)

        # Trades orphelins : entry sans exit
        orphans = {
            k: v for k, v in entries_by_key.items()
            if k not in exits_keys
        }
        if not orphans:
            return

        # Collecter les positions ouvertes chez chaque broker.
        # Distinction CRITIQUE :
        #   - set() (potentiellement vide) → broker répond, on connaît la liste
        #     réelle. Une absence dans cette liste signifie "vraiment fermée".
        #   - None → broker injoignable (auth error, TCP timeout, etc.). On ne
        #     SAIT PAS si les positions sont ouvertes ou fermées. Différer le
        #     reconcile, ne JAMAIS inventer un exit (cf. incident 2026-05-14
        #     ETHUSD cabriole : faux `reconciled_stop_loss` pondu pendant que
        #     FTMO refusait l'auth, alors que la position était toujours
        #     ouverte côté broker).
        broker_open_ids: dict[str, set[str] | None] = {}
        for broker_id, broker in self._brokers.items():
            try:
                positions = await broker.get_positions()
                broker_open_ids[broker_id] = {
                    str(p.position_id) for p in (positions or [])
                }
            except Exception as e:
                logger.warning(
                    f"[Engine] Réconciliation exits: broker {broker_id} "
                    f"injoignable ({type(e).__name__}: {e}) — reconcile "
                    f"différé pour ce broker (pas de faux exit inventé)"
                )
                broker_open_ids[broker_id] = None  # marqueur "incertain"

        reconciled = 0
        deferred = 0
        for key, entry_record in orphans.items():
            broker_id = entry_record["broker_id"]
            position_id = entry_record["position_id"]

            open_ids = broker_open_ids.get(broker_id)
            # Broker injoignable → on ne sait pas si la position est close.
            # On NE crée PAS d'exit, on attendra le prochain boot du moteur
            # où la fonction sera rappelée. Mieux vaut un orphan persistant
            # qu'un faux exit dans le journal.
            if open_ids is None:
                deferred += 1
                logger.warning(
                    f"[Engine] 🕓 Reconcile différé (broker injoignable): "
                    f"{entry_record.get('instrument')} "
                    f"{entry_record.get('side')} "
                    f"({broker_id}:{position_id})"
                )
                continue

            # Si toujours ouvert, pas d'exit à créer
            if position_id in open_ids:
                continue

            # La position a disparu → fermée pendant le downtime
            recon = await self._reconstruct_exit_from_history(
                broker_id, position_id, entry_record
            )

            if self._live_monitor:
                # record_exit attend que le trade soit dans _open_trades,
                # donc on doit d'abord l'y ajouter
                lm_key = f"{broker_id}:{position_id}"
                if lm_key not in self._live_monitor._open_trades:
                    from arabesque.execution.live_monitor import LiveTrade
                    trade = LiveTrade(
                        trade_id=entry_record.get("trade_id", ""),
                        signal_id="",
                        instrument=entry_record.get("instrument", ""),
                        strategy=entry_record.get("strategy", "unknown"),
                        side=entry_record.get("side", ""),
                        entry_price=entry_record.get("entry_price", 0.0),
                        sl=entry_record.get("sl", 0.0),
                        tp=entry_record.get("tp", 0.0),
                        volume=entry_record.get("volume", 0.0),
                        risk_cash=entry_record.get("risk_cash", 0.0),
                        broker_id=broker_id,
                        position_id=position_id,
                        ts_entry=entry_record.get("ts", ""),
                    )
                    self._live_monitor._open_trades[lm_key] = trade

                self._live_monitor.record_exit(
                    broker_id=broker_id,
                    position_id=position_id,
                    exit_price=recon["exit_price"],
                    exit_reason=recon["exit_reason"],
                    mfe_r=recon["mfe_r"],
                    be_set=recon["be_set"],
                    be_source=recon["be_source"],
                    trailing_tier=recon["trailing_tier"],
                    exit_price_source="reconciled",
                    broker_gross_profit=recon.get("gross_profit"),
                    broker_commission=recon.get("commission"),
                    broker_swap=recon.get("swap"),
                )

            reconciled += 1
            logger.warning(
                f"[Engine] 🔄 Exit manquant réconcilié: "
                f"{entry_record.get('instrument')} {entry_record.get('side')} "
                f"entry={entry_record.get('entry_price', 0.0)} "
                f"exit={recon['exit_price']} reason={recon['exit_reason']} "
                f"MFE={recon['mfe_r']:.2f}R BE={'✓' if recon['be_set'] else '✗'} "
                f"src={recon['source']} bars={recon['bars_used']} "
                f"({broker_id}:{position_id})"
            )

        if reconciled:
            logger.info(
                f"[Engine] 🔄 Réconciliation: {reconciled} exit(s) manquant(s) récupéré(s)"
            )
        if deferred:
            logger.warning(
                f"[Engine] 🕓 Réconciliation: {deferred} exit(s) différé(s) "
                f"(broker injoignable, retry au prochain boot)"
            )

    # ------------------------------------------------------------------
    # BarAggregator
    # ------------------------------------------------------------------

    async def _start_bar_aggregator(self) -> None:
        from arabesque.execution.bar_aggregator import BarAggregator, BarAggregatorConfig

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

        # Grouper les instruments par (timeframe, stratégie)
        # Chaque instrument peut déclarer timeframe: "H4" dans instruments.yaml
        # Par défaut : "H1" (3600s), stratégie par défaut depuis strategy.type
        _TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
                        "H1": 3600, "H4": 14400, "D1": 86400}

        strategy_type = self.settings.get("strategy", {}).get("type", "combined")

        # Clé = (tf_seconds, strategy_name), valeur = liste de symboles
        by_tf_strat: dict[tuple[int, str], list[str]] = {}

        # 1) Instruments par défaut → stratégie Extension (strategy.type)
        for sym in symbols:
            inst_data = self.instruments.get(sym, {})
            tf_str = inst_data.get("timeframe", "H1").upper() if isinstance(inst_data, dict) else "H1"
            tf_s = _TF_SECONDS.get(tf_str, 3600)
            by_tf_strat.setdefault((tf_s, strategy_type), []).append(sym)

        # 2) strategy_assignments → aggregators additionnels par stratégie
        strat_assignments = self.settings.get("strategy_assignments", {}) or {}
        for strat_name, strat_cfg in strat_assignments.items():
            if not isinstance(strat_cfg, dict):
                continue
            tf_str = strat_cfg.get("timeframe", "H1").upper()
            tf_s = _TF_SECONDS.get(tf_str, 3600)
            assigned_instruments = strat_cfg.get("instruments", []) or []
            # Ne garder que les instruments présents dans le price feed
            valid = [s for s in assigned_instruments if s in symbols]
            if valid:
                by_tf_strat.setdefault((tf_s, strat_name), []).append(None)  # placeholder
                by_tf_strat[(tf_s, strat_name)] = valid
                logger.info(
                    f"[Engine] 🎯 Assignment {strat_name} {tf_str}: "
                    f"{', '.join(valid)}"
                )

        # Le broker source fournit get_history()
        source_broker = self._brokers.get(source_broker_id)

        # Pré-charger les symboles pour éviter les appels concurrents
        if source_broker and hasattr(source_broker, 'get_symbols'):
            try:
                symbols_list = await source_broker.get_symbols()
                logger.info(
                    f"[Engine] 📋 Symboles pré-chargés: {len(symbols_list)} "
                    f"depuis {source_broker_id}"
                )
            except Exception as e:
                logger.warning(f"[Engine] Pré-chargement symboles: {e}")

        # Créer un BarAggregator par (timeframe, stratégie)
        self._bar_aggregators = {}
        for (tf_s, strat), tf_symbols in sorted(by_tf_strat.items()):
            agg_cfg = BarAggregatorConfig(
                instruments=tf_symbols,
                timeframe_s=tf_s,
                signal_strategy=strat,
            )
            agg = BarAggregator(
                config=agg_cfg,
                on_signal=self.receive_signal,
                broker=source_broker,
            )
            await agg.initialize()

            if self._position_monitor:
                agg.add_bar_closed_callback(self._position_monitor.on_bar_closed)

            self._bar_aggregators[(tf_s, strat)] = agg
            tf_label = agg._timeframe_label()
            logger.info(
                f"[Engine] 📊 BarAggregator {tf_label}/{strat} prêt — "
                f"{len(tf_symbols)} instrument(s): {', '.join(tf_symbols)}"
            )

        # Compatibilité : self._bar_aggregator pointe sur le premier
        if self._bar_aggregators:
            self._bar_aggregator = next(iter(self._bar_aggregators.values()))

    # ------------------------------------------------------------------
    # Price feed
    # ------------------------------------------------------------------

    def _on_source_broker_replaced(self, new_broker) -> None:
        """Le PriceFeed a recréé le broker source (force-reconnect après feed
        stale). Basculer la référence partagée pour que le canal trading
        (lecture pending orders, ordres, reconcile) utilise la nouvelle
        connexion et non l'ancien broker zombie (`_connected=False`).

        `self._brokers` est partagé par référence avec position_monitor /
        dispatcher / live_monitor → un seul update du dict les couvre tous (ils
        font un lookup par broker_id à chaque appel). Les BarAggregators gardent
        leur propre attribut `broker` (get_history) → mis à jour aussi.
        Incident fondateur 2026-06-08 : sans ça, le feed se reconnecte mais le
        trading reste bloqué fail-closed (~22h observées).
        """
        pf_cfg = self.settings.get("price_feed", {})
        source_broker_id = pf_cfg.get("source_broker", "")
        if not source_broker_id:
            return
        old = self._brokers.get(source_broker_id)
        if old is new_broker:
            return
        self._brokers[source_broker_id] = new_broker
        for agg in (self._bar_aggregators or {}).values():
            if getattr(agg, "broker", None) is old:
                agg.broker = new_broker
        logger.warning(
            f"[Engine] 🔄 Broker source '{source_broker_id}' remplacé après "
            f"force-reconnect feed — canal trading rebasculé sur la nouvelle "
            f"connexion (fix incident 2026-06-08)."
        )

    async def _start_price_feed(self) -> None:
        from arabesque.execution.price_feed import PriceFeedManager

        pf_cfg = self.settings.get("price_feed", {})
        source_broker_id = pf_cfg.get("source_broker", "")

        if not source_broker_id or source_broker_id not in self._brokers:
            logger.warning("[Engine] Price feed désactivé (broker source non trouvé)")
            return

        # Collecter tous les symboles de tous les aggregators
        symbols = []
        for agg in self._bar_aggregators.values():
            symbols.extend(agg.cfg.instruments)
        symbols = list(dict.fromkeys(symbols))  # Déduplique en préservant l'ordre

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
        broker_cfg["instruments_config"] = self.instruments

        self._price_feed = PriceFeedManager(
            broker_id=source_broker_id,
            broker_cfg=broker_cfg,
            symbols=symbols,
            existing_broker=source_broker,
            on_broker_replaced=self._on_source_broker_replaced,
        )

        # Subscribe ticks to the correct aggregator(s) for each symbol
        # Each aggregator only processes symbols in its own instrument list
        for agg in self._bar_aggregators.values():
            for sym in agg.cfg.instruments:
                await self._price_feed.subscribe(sym, agg.on_tick)

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

    async def _init_dd_tracking(self) -> None:
        """Initialise les balances de référence pour le calcul du DD.

        - initial_balance : depuis accounts.yaml (ex: 100000 pour FTMO)
        - daily_start_balance : balance réelle du broker au démarrage
        - Per-broker : même logique pour chaque broker connecté
        """
        from datetime import datetime, timezone
        from pathlib import Path
        import yaml

        # 1. Charger accounts.yaml
        try:
            path = Path("config/accounts.yaml")
            if path.exists():
                with open(path) as f:
                    cfg = yaml.safe_load(f) or {}
                self._accounts_config = cfg.get("accounts", {})
        except Exception as e:
            logger.warning(f"[Engine] Could not load accounts.yaml: {e}")

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 2. Initialiser DD tracking pour CHAQUE broker
        for broker_id, broker in self._brokers.items():
            acct = self._accounts_config.get(broker_id, {})
            initial = float(acct.get("initial_balance", 0))

            try:
                info = await broker.get_account_info()
                if info:
                    if not initial:
                        initial = info.balance
                        logger.warning(
                            f"[Engine] {broker_id}: initial_balance not in "
                            f"accounts.yaml, using broker balance: {info.balance:.2f}"
                        )
                    daily_start = info.balance
                else:
                    daily_start = initial or 100_000.0
            except Exception as e:
                logger.warning(f"[Engine] {broker_id}: could not fetch balance: {e}")
                daily_start = initial or 100_000.0

            if not initial:
                initial = 100_000.0
                logger.warning(f"[Engine] {broker_id}: fallback initial_balance=100000")

            self._broker_initial_balance[broker_id] = initial
            self._broker_daily_start_balance[broker_id] = daily_start
            self._broker_daily_start_date[broker_id] = today

            logger.info(
                f"[Engine] DD tracking {broker_id}: "
                f"initial={initial:.0f}, daily_start={daily_start:.0f}, "
                f"date={today}"
            )

        # Rétro-compat : primary broker → champs legacy
        primary_id = list(self._brokers.keys())[0]
        self._initial_balance = self._broker_initial_balance.get(primary_id)
        self._daily_start_balance = self._broker_daily_start_balance.get(primary_id)
        self._daily_start_date = self._broker_daily_start_date.get(primary_id)

    async def _refresh_account_state(self) -> None:
        if not self._brokers or not self._dispatcher:
            return
        primary_id = list(self._brokers.keys())[0]
        from datetime import datetime, timezone
        from arabesque.core.guards import AccountState
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for broker_id, broker in self._brokers.items():
            try:
                # Positions ouvertes
                open_instruments = []
                open_positions = 0
                open_risk_cash = 0.0
                try:
                    positions = await broker.get_positions()
                    open_positions = len(positions)
                    open_instruments = [p.symbol for p in positions]
                    broker_pending = await broker.get_pending_orders()
                except Exception as e:
                    logger.warning(
                        f"[Engine] {broker_id}: positions indisponibles - "
                        f"etat risque invalide ({e})"
                    )
                    self._dispatcher.invalidate_account_state(broker_id)
                    continue

                info = await broker.get_account_info()
                if not info:
                    self._dispatcher.invalidate_account_state(broker_id)
                    continue

                # Compléter avec les positions du monitor si disponible
                if self._position_monitor:
                    for pos in self._position_monitor.open_positions:
                        if pos.broker_id == broker_id and pos.symbol not in open_instruments:
                            open_instruments.append(pos.symbol)

                # Reserve actual known risk for tracked positions and accepted
                # pending orders. A server-side STOP/LIMIT can fill without a
                # new Python decision, so excluding it from the budget allows
                # several orders to breach together.
                tracked_risk_by_position: dict[str, float] = {}
                if self._live_monitor:
                    tracked_risk_by_position = {
                        str(t.get("position_id")): float(
                            t.get("risk_cash", 0.0) or 0.0
                        )
                        for t in self._live_monitor.get_open_trades()
                        if t.get("broker_id") == broker_id and t.get("position_id")
                    }
                # Only broker-confirmed positions count as open. Match the
                # journal by position_id; equal list lengths are not proof of
                # identity when a stale tracked position masks an orphan.
                open_risk_cash = sum(
                    tracked_risk_by_position.get(str(pos.position_id), 400.0)
                    for pos in positions
                )

                pending_for_broker = [
                    pending
                    for pending in self._pending_fills.values()
                    if pending.get("broker_id") == broker_id
                ]
                tracked_pending_ids = {
                    str(pending.get("order_id"))
                    for pending in pending_for_broker
                    if pending.get("order_id")
                }
                open_position_ids = {
                    str(pos.position_id) for pos in positions
                    if getattr(pos, "position_id", None)
                }
                unknown_pending_ids = [
                    str(order.order_id)
                    for order in broker_pending
                    if str(order.order_id) not in tracked_pending_ids
                    and str((getattr(order, "raw_data", None) or {}).get("position_id", ""))
                    not in open_position_ids
                ]
                if unknown_pending_ids:
                    logger.warning(
                        f"[Engine] {broker_id}: pending broker non trackes "
                        f"{unknown_pending_ids} - etat risque invalide"
                    )
                    self._dispatcher.invalidate_account_state(broker_id)
                    continue
                open_risk_cash += sum(
                    float(pending.get("risk_cash", 0.0) or 0.0)
                    for pending in pending_for_broker
                )
                open_positions += len(pending_for_broker)
                for pending in pending_for_broker:
                    symbol = pending.get("instrument", "")
                    if symbol and symbol not in open_instruments:
                        open_instruments.append(symbol)

                # Daily rollover: reset daily_start_balance at UTC midnight
                broker_daily_date = self._broker_daily_start_date.get(broker_id)
                if broker_daily_date and today != broker_daily_date:
                    old_daily = self._broker_daily_start_balance.get(broker_id, info.balance)
                    self._broker_daily_start_balance[broker_id] = info.balance
                    self._broker_daily_start_date[broker_id] = today
                    logger.info(
                        f"[Engine] 📅 New day {today} {broker_id}: "
                        f"daily_start_balance {old_daily:.2f} → {info.balance:.2f}"
                    )
                    # Rétro-compat primary
                    if broker_id == primary_id:
                        self._daily_start_balance = info.balance
                        self._daily_start_date = today

                initial_bal = self._broker_initial_balance.get(
                    broker_id, info.balance
                )
                daily_start_bal = self._broker_daily_start_balance.get(
                    broker_id, info.balance
                )

                state = AccountState(
                    balance=info.balance,
                    equity=info.equity,
                    start_balance=initial_bal,
                    daily_start_balance=daily_start_bal,
                    open_positions=open_positions,
                    open_instruments=open_instruments,
                    open_risk_cash=open_risk_cash,
                    daily_trades=self._count_daily_trades(broker_id, today),
                )

                # The dispatcher performs a fail-closed pre-order gate for
                # every broker, so it needs every broker's current state.
                self._dispatcher.update_account_state(state, broker_id=broker_id)

                logger.debug(
                    f"[Engine] 💰 {broker_id}: "
                    f"balance={info.balance:.2f} equity={info.equity:.2f} "
                    f"{info.currency} | {open_positions} pos | "
                    f"daily_dd={state.daily_dd_pct:.1f}% "
                    f"total_dd={state.total_dd_pct:.1f}%"
                )

                # Live monitor: evaluate protection before persisting the
                # snapshot so its level represents the metric just measured.
                if self._live_monitor:
                    free_margin = getattr(info, 'margin_free', 0.0) or 0.0
                    await self._live_monitor.check_protection(
                        daily_dd_pct=state.daily_dd_pct,
                        total_dd_pct=state.total_dd_pct,
                        equity=info.equity,
                        free_margin=free_margin,
                        broker_id=broker_id,
                    )
                    self._live_monitor.record_equity_snapshot(
                        balance=info.balance,
                        equity=info.equity,
                        free_margin=free_margin,
                        open_positions=open_positions,
                        daily_dd_pct=state.daily_dd_pct,
                        total_dd_pct=state.total_dd_pct,
                        broker_id=broker_id,
                    )

            except Exception as e:
                self._dispatcher.invalidate_account_state(broker_id)
                logger.warning(f"[Engine] {broker_id} refresh: {e}")
                # Alerte Telegram si un broker est injoignable
                if self._live_monitor:
                    asyncio.ensure_future(
                        self._live_monitor._notify_telegram(
                            f"⚠️ {broker_id} injoignable: {e}"
                        )
                    )

    def _count_daily_trades(self, broker_id: str, day: str) -> int:
        """Count today's distinct submitted trades for a broker.

        `pending_order` and its later `entry` carry the same trade_id and
        therefore consume a single daily slot. Reading the small append-only
        journal at the account refresh cadence keeps this count valid across
        process restarts.
        """
        if not TRADE_JOURNAL_PATH.exists():
            return 0
        seen: set[str] = set()
        try:
            with open(TRADE_JOURNAL_PATH) as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("event") not in {"entry", "pending_order"}:
                        continue
                    if event.get("broker_id") != broker_id:
                        continue
                    if not str(event.get("ts", "")).startswith(day):
                        continue
                    key = event.get("trade_id") or event.get("order_id")
                    if key:
                        seen.add(str(key))
        except OSError as e:
            raise RuntimeError(
                f"daily trades journal unavailable for {broker_id}: {e}"
            ) from e
        return len(seen)

    async def _account_refresh_loop(self) -> None:
        # Task fire-and-forget (cf. _start line ~149) : toute exception non
        # rattrapée tue la boucle silencieusement. Incident 2026-05-18 :
        # health_report n'a plus été émis pendant 16h après un restart parce
        # qu'un await intérieur levait. Désormais on isole CHAQUE await pour
        # que ni le refresh ni le report ne puisse condamner la boucle.
        while self._running:
            await asyncio.sleep(120)  # Toutes les 2 minutes (positions changent vite)
            if not self._running:
                break
            try:
                await self._refresh_account_state()
            except Exception as e:
                logger.warning(
                    f"[Engine] _refresh_account_state error (loop kept alive): {e}",
                    exc_info=True,
                )
            # Health report périodique — isolé pour ne pas tuer la boucle
            # si emit_health_report lève (ex: append_journal IO error).
            try:
                if self._live_monitor and self._live_monitor.should_emit_health_report():
                    self._live_monitor.emit_health_report()
            except Exception as e:
                logger.warning(
                    f"[Engine] emit_health_report error (loop kept alive): {e}",
                    exc_info=True,
                )

    async def _notify_startup_state(self) -> None:
        """Envoie un résumé Telegram de l'état de chaque broker au démarrage."""
        if not self._live_monitor:
            return
        from arabesque.core.guards import AccountState
        broker_states = {}
        for broker_id, broker in self._brokers.items():
            try:
                info = await broker.get_account_info()
                if not info:
                    continue
                positions = []
                positions_known = True
                try:
                    positions = await broker.get_positions()
                except Exception as e:
                    positions_known = False
                    logger.warning(
                        f"[Engine] startup state {broker_id}: "
                        f"positions inconnues ({e})"
                    )
                initial_bal = self._broker_initial_balance.get(broker_id, info.balance)
                daily_start = self._broker_daily_start_balance.get(broker_id, info.balance)
                state = AccountState(
                    balance=info.balance,
                    equity=info.equity,
                    start_balance=initial_bal,
                    daily_start_balance=daily_start,
                    open_positions=len(positions),
                    open_instruments=[p.symbol for p in positions],
                )
                broker_states[broker_id] = {
                    "balance": info.balance,
                    "equity": info.equity,
                    "daily_dd_pct": state.daily_dd_pct,
                    "total_dd_pct": state.total_dd_pct,
                    "open_positions": len(positions),
                    "positions_known": positions_known,
                    "protection": self._live_monitor._protection_per_broker.get(
                        broker_id, "normal"
                    ),
                }
            except Exception as e:
                logger.warning(f"[Engine] startup state {broker_id}: {e}")
        if broker_states:
            await self._live_monitor.notify_startup(broker_states)

    # ------------------------------------------------------------------
    # Callback ordre
    # ------------------------------------------------------------------

    async def _alert_execution_integrity(self, message: str) -> None:
        """Alert without allowing telemetry failures to interrupt monitoring."""
        logger.error(message)
        if not self._live_monitor:
            return
        for notify in (
            self._live_monitor._notify_telegram,
            self._live_monitor._notify_ntfy,
        ):
            try:
                await notify(message)
            except Exception as e:
                logger.warning(f"[Engine] execution integrity notification: {e}")

    async def _confirm_post_fill_protection(
        self,
        broker_id: str,
        broker,
        signal,
        position_id: str,
        position,
    ) -> tuple[float, float]:
        """Verify TradeLocker linked SL/TP after fill, repairing once if needed.

        TradeLocker omits SL/TP from its Position payload and exposes them as
        attached working orders. A real filled position is always monitored,
        but new entries on that broker are quarantined if its protection
        cannot be proven server-side.
        """
        expected_sl = float(signal.sl or 0.0)
        expected_tp = float(getattr(signal, "tp_indicative", 0.0) or 0.0)
        observed_sl = getattr(position, "stop_loss", None)
        observed_tp = getattr(position, "take_profit", None)
        if broker.config.get("type", "") != "tradelocker":
            return observed_sl or expected_sl, observed_tp or expected_tp

        get_protection = getattr(broker, "get_position_protection", None)
        if get_protection and (not observed_sl or not observed_tp):
            linked = await get_protection(str(position_id))
            if linked:
                observed_sl, observed_tp = linked

        tolerance = 1e-5
        try:
            info = await broker.get_symbol_info(signal.instrument)
            if info:
                tolerance = max(tolerance, float(info.tick_size or 0.0) * 1.1)
        except Exception:
            pass
        for observed in (observed_sl, observed_tp):
            # TradeLocker often reports linked SL/TP rounded to display
            # precision (e.g. BCHUSD 313.14 vs expected 313.140714). Infer the
            # displayed increment so harmless formatting does not quarantine
            # a broker while still rejecting materially different levels.
            if observed is None:
                continue
            text = f"{float(observed):.10f}".rstrip("0").rstrip(".")
            if "." in text:
                increment = 10 ** -len(text.split(".", 1)[1])
                tolerance = max(tolerance, increment * 0.55)

        def matches(actual: float | None, expected: float) -> bool:
            if expected <= 0:
                return True
            return actual is not None and abs(float(actual) - expected) <= tolerance

        confirmed = matches(observed_sl, expected_sl) and matches(
            observed_tp, expected_tp
        )
        action = "verified"
        if not confirmed:
            action = "amend_attempted"
            try:
                amend = await broker.amend_position_sltp(
                    str(position_id),
                    stop_loss=expected_sl or None,
                    take_profit=expected_tp or None,
                )
                if amend.success and get_protection:
                    linked = await get_protection(str(position_id))
                    if linked:
                        observed_sl, observed_tp = linked
                        confirmed = matches(observed_sl, expected_sl) and matches(
                            observed_tp, expected_tp
                        )
                action = "amend_confirmed" if confirmed else "amend_unconfirmed"
            except Exception as e:
                action = f"amend_failed:{e}"

        if self._live_monitor:
            self._live_monitor.record_protection_check(
                broker_id=broker_id,
                position_id=str(position_id),
                instrument=signal.instrument,
                expected_sl=expected_sl,
                expected_tp=expected_tp,
                observed_sl=observed_sl,
                observed_tp=observed_tp,
                confirmed=confirmed,
                action=action,
            )
        if not confirmed:
            reason = f"protection non confirmée {signal.instrument} position={position_id}"
            if getattr(self, "_dispatcher", None):
                self._dispatcher.block_broker_entries(broker_id, reason)
            await self._alert_execution_integrity(
                f"🚨 GFT protection non confirmée — {signal.instrument}\n"
                f"position_id={position_id} attendu SL={expected_sl} TP={expected_tp}\n"
                f"observé SL={observed_sl} TP={observed_tp} action={action}\n"
                "Nouvelles entrées GFT bloquées ; position conservée sous surveillance."
            )
        return observed_sl or expected_sl, observed_tp or expected_tp

    async def _estimate_position_risk_cash(
        self,
        broker,
        instrument: str,
        entry: float,
        sl: float,
        volume: float,
    ) -> tuple[float | None, float | None, float | None, str]:
        """Estimate broker-side cash risk from the confirmed position.

        Use the same currency-conversion semantics as the order sizer. Broker
        metadata is useful for lot bounds and broker-specific pip sizes, but
        some APIs expose a raw ``pip_value`` that is not denominated like our
        risk model. TradeLocker AUDJPY 2026-05-29 reported pip_size=0.0001 and
        pip_value=10, which made a correctly-sized fill look 166x over-risk
        and triggered a false emergency close. For cross pairs and USD-quoted
        symbols with calibrated YAML values, prefer YAML pip value rescaled to
        the broker pip size.
        """
        if entry <= 0 or sl <= 0 or volume <= 0:
            return None, None, None, "missing_position_fields"
        try:
            info = await broker.get_symbol_info(instrument)
        except Exception as e:
            logger.warning(f"[Engine] risk integrity symbol info failed: {e}")
            return None, None, None, "symbol_info_error"
        if not info or info.pip_size <= 0:
            return None, None, None, "symbol_info_missing"

        pip_size = float(info.pip_size)
        lot_size = float(info.lot_size or 0.0)
        symbol = instrument.upper()
        quote_ccy = symbol[-3:] if len(symbol) >= 6 else ""
        base_ccy = symbol[:3] if len(symbol) >= 6 else ""
        inst = self.instruments.get(instrument, {}) if hasattr(self, "instruments") else {}
        yaml_pip_size = float(inst.get("pip_size", 0.0) or 0.0)
        yaml_pip_value = float(inst.get("pip_value_per_lot", 0.0) or 0.0)
        pip_ratio = pip_size / yaml_pip_size if yaml_pip_size > 0 else 1.0
        broker_pip_value = float(getattr(info, "pip_value", 0.0) or 0.0)
        pip_value = 0.0
        source = "pip_value_missing"

        if yaml_pip_value > 0 and quote_ccy == "USD":
            pip_value = yaml_pip_value * pip_ratio
            source = "yaml_usd_rescaled" if pip_ratio != 1 else "yaml_usd"
        elif yaml_pip_value > 0 and base_ccy != "USD":
            pip_value = yaml_pip_value * pip_ratio
            source = "yaml_cross_rescaled" if pip_ratio != 1 else "yaml_cross"
        elif lot_size > 0:
            raw_pip_value = lot_size * pip_size
            if quote_ccy == "USD":
                pip_value = raw_pip_value
                source = "broker_lot_size_usd"
            elif base_ccy == "USD" and entry > 0:
                pip_value = raw_pip_value / entry
                source = "broker_lot_size_usd_base"
            else:
                pip_value = broker_pip_value
                source = "broker_pip_value_cross_fallback"
        else:
            pip_value = broker_pip_value
            source = "broker_pip_value"

        if pip_value <= 0:
            return None, pip_size, None, "pip_value_missing"
        pips = abs(float(entry) - float(sl)) / pip_size
        return pips * pip_value * float(volume), pip_size, pip_value, source

    async def _check_post_fill_risk_integrity(
        self,
        broker_id: str,
        broker,
        signal,
        position_id: str,
        entry: float,
        sl: float,
        volume: float,
        expected_risk_cash: float,
    ) -> bool:
        """Validate actual post-fill risk and close only on material over-risk.

        Returns True when the position should continue into normal monitoring.
        Under-risk is an audit/calibration problem; over-risk is capital safety.
        """
        actual, pip_size, pip_value, source = await self._estimate_position_risk_cash(
            broker, signal.instrument, entry, sl, volume
        )
        ratio = (
            actual / expected_risk_cash
            if actual is not None and expected_risk_cash > 0
            else None
        )
        status = "unknown"
        action = "none"
        if ratio is not None:
            if ratio < 0.50:
                status = "under_risk"
                action = "journal_only"
            elif ratio <= 1.25:
                status = "ok"
            elif ratio <= 1.50:
                status = "over_risk_warning"
                action = "block_broker_entries"
            else:
                status = "over_risk_critical"
                action = "close_position"

        if self._live_monitor:
            self._live_monitor.record_risk_integrity_check(
                broker_id=broker_id,
                position_id=str(position_id),
                instrument=signal.instrument,
                expected_risk_cash=expected_risk_cash,
                actual_risk_cash=actual,
                risk_ratio=ratio,
                status=status,
                action=action,
                entry_price=entry,
                sl=sl,
                volume=volume,
                pip_size=pip_size,
                pip_value=pip_value,
                source=source,
            )

        if status == "under_risk":
            if self._live_monitor:
                try:
                    await self._live_monitor._notify_telegram(
                        f"⚠️ Sous-risque détecté — {broker_id} {signal.instrument}\n"
                        f"position_id={position_id} cible={expected_risk_cash:.2f}$ "
                        f"réel≈{actual:.2f}$ ({ratio:.2f}x)\n"
                        "Trade conservé ; calibrage à corriger pour les prochains ordres."
                    )
                except Exception as e:
                    logger.warning(f"[Engine] risk integrity notification: {e}")
        elif status == "over_risk_warning":
            reason = (
                f"risk integrity warning {signal.instrument} position={position_id} "
                f"{ratio:.2f}x"
            )
            if getattr(self, "_dispatcher", None):
                self._dispatcher.block_broker_entries(broker_id, reason)
            await self._alert_execution_integrity(
                f"🚨 Sur-risque post-fill — {broker_id} {signal.instrument}\n"
                f"position_id={position_id} cible={expected_risk_cash:.2f}$ "
                f"réel≈{actual:.2f}$ ({ratio:.2f}x)\n"
                "Nouvelles entrées broker bloquées ; position surveillée."
            )
        elif status == "over_risk_critical":
            reason = (
                f"risk integrity critical {signal.instrument} position={position_id} "
                f"{ratio:.2f}x"
            )
            if getattr(self, "_dispatcher", None):
                self._dispatcher.block_broker_entries(broker_id, reason)
            close_result = await broker.close_position(str(position_id))
            await self._alert_execution_integrity(
                f"🚨 Sur-risque critique — {broker_id} {signal.instrument}\n"
                f"position_id={position_id} cible={expected_risk_cash:.2f}$ "
                f"réel≈{actual:.2f}$ ({ratio:.2f}x)\n"
                f"clôture immédiate demandée: success={close_result.success} "
                f"message={close_result.message}"
            )
            return not close_result.success
        return True

    async def _flag_extreme_fill_if_needed(
        self, broker_id: str, signal, position_id: str, entry: float
    ) -> float:
        """Alert/quarantine on impossible-looking fill; never drop monitoring."""
        slip = abs(entry - signal.close)
        risk_distance = abs(signal.close - signal.sl) if signal.sl else 1.0
        slip_in_r = slip / risk_distance if risk_distance > 0 else 0.0
        if slip_in_r > 5.0:
            reason = (
                f"fill mismatch {signal.instrument} position={position_id} "
                f"{slip_in_r:.1f}R"
            )
            if getattr(self, "_dispatcher", None):
                self._dispatcher.block_broker_entries(broker_id, reason)
            await self._alert_execution_integrity(
                f"🚨 FILL MISMATCH — {broker_id} {signal.instrument}\n"
                f"position_id={position_id} signal={signal.close:.5f} "
                f"fill={entry:.5f} écart={slip_in_r:.1f}R\n"
                "Position enregistrée et surveillée ; nouvelles entrées broker bloquées."
            )
        return slip_in_r

    async def _on_order_result(self, broker_id, signal, result) -> None:
        status = "✅" if result.success else "❌"
        if result.success:
            logger.info(
                f"[Engine] {status} {broker_id} | {signal.instrument} "
                f"{signal.side.value} order_id={result.order_id}"
            )
            # NB : on ne loggue plus `record_entry` ici. Les ordres STOP/LIMIT
            # peuvent être placés sans être fillés (Cabriole Donchian breakout
            # peut prendre 2h+). _register_position_in_monitor décide entre
            # `record_entry` (fill confirmé broker-side) et `record_pending_order`
            # (en attente de fill, suivi par _pending_fills).
            if result.order_id:
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

    def _on_slippage_reject(self, signal, trigger_price: float, slippage_atr: float) -> None:
        """Callback quand un signal est rejeté pour slippage excessif.

        Crée un counterfactual pour mesurer l'impact du guard :
        le trade aurait-il été profitable malgré le slippage ?
        """
        import json
        from datetime import datetime, timezone
        from pathlib import Path
        from arabesque.core.models import Counterfactual, DecisionType

        cf = Counterfactual(
            signal_id=signal.signal_id,
            decision_type=DecisionType.SIGNAL_REJECTED,
            instrument=signal.instrument,
            side=signal.side,
            hypothetical_entry=trigger_price,
            hypothetical_sl=signal.sl,
            hypothetical_tp=signal.tp_indicative,
            ts_decision=datetime.now(timezone.utc),
            price_at_decision=trigger_price,
            mfe_after=trigger_price,
            mae_after=trigger_price,
        )

        # Stocker dans le position_monitor pour tracking continu
        if self._position_monitor and hasattr(self._position_monitor, 'manager'):
            self._position_monitor.manager.counterfactuals.append(cf)
            logger.info(
                f"[Engine] 📊 Counterfactual créé: {signal.instrument} "
                f"{signal.side.value} slip={slippage_atr:.2f} ATR "
                f"entry={trigger_price:.5f} SL={signal.sl:.5f}"
            )

        # Log JSONL pour analyse post-hoc
        try:
            log_path = Path("logs/slippage_rejects.jsonl")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "instrument": signal.instrument,
                "side": signal.side.value,
                "signal_close": signal.close,
                "trigger_price": trigger_price,
                "slippage_atr": round(slippage_atr, 4),
                "atr": signal.atr,
                "sl": signal.sl,
                "tp": signal.tp_indicative,
                "regime": signal.regime,
                "strategy": signal.strategy_type,
                "cf_id": cf.cf_id,
            }
            with open(log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.debug(f"[Engine] slippage log error: {e}")

    async def _register_position_in_monitor(self, broker_id, signal, result):
        """Confirme le fill d'un ordre, ou le marque comme pending.

        - Si la position apparaît dans get_positions() après retry → fill confirmé,
          on loggue `record_entry` + on enregistre dans le position_monitor.
        - Sinon (STOP/LIMIT non encore fillé) → on loggue `record_pending_order`
          et on stocke dans `_pending_fills`. Le `_pending_fills_loop` détectera
          le fill ultérieur et fera record_entry à ce moment-là.

        Valide aussi que l'entry broker correspond au signal (détection mismatch).
        """
        try:
            broker = self._brokers.get(broker_id)
            if not broker:
                return

            entry = signal.close
            volume = result.fill_volume or result.volume_lots or 0.01
            found = False
            pos = None

            for attempt in range(3):
                await asyncio.sleep(2.0 * (attempt + 1))
                try:
                    positions = await broker.get_positions()
                except Exception as e:
                    # L'ordre a ete accepte par le broker mais son etat ne
                    # peut pas etre relu. Ne jamais perdre sa trace : apres
                    # les retries il sera persiste comme pending/uncertain
                    # et la boucle de poll reprendra la confirmation.
                    logger.warning(
                        f"[Engine] Confirmation fill différée "
                        f"{broker_id}:{result.order_id} "
                        f"(attempt {attempt + 1}/3): {e}"
                    )
                    continue
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
                # Pas de fill confirme : ordre STOP/LIMIT encore en attente,
                # ou ordre place dont la relecture broker a echoue. Dans les
                # deux cas, on conserve l'identifiant sans inventer d'entry ;
                # _poll_pending_fills le convertira en position trackee.
                if self._live_monitor:
                    self._live_monitor.record_pending_order(
                        signal=signal,
                        broker_id=broker_id,
                        order_id=str(result.order_id),
                        order_type="STOP_OR_LIMIT",
                        target_price=signal.close,
                        volume=volume,
                        risk_cash=getattr(result, "risk_cash", 0.0),
                    )
                key = f"{broker_id}:{result.order_id}"
                self._pending_fills[key] = {
                    "broker_id": broker_id,
                    "order_id": str(result.order_id),
                    "instrument": signal.instrument,
                    "side": signal.side.value,
                    "signal_close": signal.close,
                    "signal_sl": signal.sl,
                    "signal_tp": getattr(signal, "tp_indicative", 0.0),
                    "strategy_type": getattr(signal, "strategy_type", "unknown"),
                    "signal_id": getattr(signal, "signal_id", ""),
                    "risk_cash": getattr(result, "risk_cash", 0.0),
                    "volume_estimate": volume,
                    "ts_placed": time.time(),
                }
                self._save_pending_fills()
                logger.info(
                    f"[Engine] ⏳ Pending/unconfirmed fill: {signal.instrument} "
                    f"{signal.side.value} order_id={result.order_id} — "
                    f"sera confirmé par polling broker"
                )
                return

            # Fill confirmé broker-side : même anormal, une position réelle
            # doit être journalisée et monitorée ; l'alerte/quarantaine traite
            # les nouvelles entrées sans perdre celle qui existe déjà.
            slip = abs(entry - signal.close)
            slip_in_r = await self._flag_extreme_fill_if_needed(
                broker_id, signal, str(result.order_id), entry
            )
            effective_sl, effective_tp = await self._confirm_post_fill_protection(
                broker_id, broker, signal, str(result.order_id), pos
            )

            logger.info(
                f"[Engine] 📋 Fill confirmé: {signal.instrument} "
                f"{signal.side.value} {volume:.3f}L "
                f"entry={entry:.5f} (signal={signal.close:.5f} "
                f"slip={slip:.5f}) "
                f"SL={pos.stop_loss} TP={pos.take_profit}"
            )

            # Trade journal entry — la position existe vraiment
            if self._live_monitor:
                bid_e, ask_e = 0.0, 0.0
                try:
                    tick = await broker.get_quote(signal.instrument)
                    if tick:
                        bid_e, ask_e = float(tick.bid or 0), float(tick.ask or 0)
                except Exception:
                    pass
                self._live_monitor.record_entry(
                    signal=signal,
                    broker_id=broker_id,
                    position_id=str(result.order_id),
                    entry_price=entry,
                    volume=volume,
                    risk_cash=getattr(result, "risk_cash", 0.0),
                    broker_bid=bid_e,
                    broker_ask=ask_e,
                )

            should_monitor = await self._check_post_fill_risk_integrity(
                broker_id=broker_id,
                broker=broker,
                signal=signal,
                position_id=str(result.order_id),
                entry=entry,
                sl=effective_sl,
                volume=volume,
                expected_risk_cash=float(getattr(result, "risk_cash", 0.0) or 0.0),
            )
            if not should_monitor:
                return

            # Position monitor (BE/trailing)
            if not self._position_monitor:
                return

            digits = 5
            try:
                sinfo = await broker.get_symbol_info(signal.instrument)
                if sinfo:
                    digits = sinfo.digits
            except Exception:
                pass

            self._position_monitor.register_position(
                broker_id=broker_id,
                position_id=str(result.order_id),
                symbol=signal.instrument,
                side=signal.side,
                entry=entry,
                sl=effective_sl,
                tp=effective_tp,
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
            channels = select_notification_channels(
                notif_settings.get("channels") or [], urgent=False
            )

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

# Basket live validé walk-forward (2026-03-15) : XAUUSD H1 + crypto 4H + JPY crosses H1
_DEFAULT_INSTRUMENTS = [
    "BTCUSD","ETHUSD","SOLUSD","BNBUSD","LNKUSD","DOGEUSD","ADAUSD",
    "AVAXUSD","LTCUSD","XAUUSD",
    "AUDJPY","CHFJPY","GBPJPY",
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
      --source parquet --strategy trend --start 2025-10-01 --end 2026-01-01

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
    from arabesque.core.guards import PropConfig, ExecConfig, AccountState
    from arabesque.execution.dryrun import ParquetClock
    from arabesque.execution.orchestrator import Orchestrator
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

    from arabesque.strategies.extension.signal import TrendSignalGenerator, TrendSignalConfig
    if args.strategy in ("trend", "combined", "mean_reversion"):
        sig_gen = TrendSignalGenerator(TrendSignalConfig())
    else:
        raise ValueError(f"Stratégie inconnue : {args.strategy}")

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
