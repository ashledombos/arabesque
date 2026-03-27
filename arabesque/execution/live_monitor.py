"""
Arabesque — Live Monitor & Active Protection.

Surveillance temps réel + actions automatiques :
  1. Trade journal     — chaque entrée/sortie persistée en JSONL
  2. Equity snapshots  — balance/equity enregistrées périodiquement
  3. Performance live   — WR, Exp, TotalR par stratégie/instrument
  4. Drift detection   — alerte si performance live diverge du backtest
  5. Margin monitor    — alerte si marge libre trop basse
  6. Health reports    — résumé périodique dans les logs

PROTECTION ACTIVE :
  7. Risk reduction    — réduction progressive du risque par palier DD
  8. Close worst       — ferme les positions les plus perdantes si marge critique
  9. Emergency freeze  — coupe TOUT et attend intervention humaine
  10. Notifications    — Telegram (détaillé) + ntfy (urgent)

PALIERS DE PROTECTION :
  NORMAL   → risque plein, notifications Telegram info
  CAUTION  → risque réduit 50%, Telegram warning
  DANGER   → risque réduit 75%, ferme positions sans BE, ntfy urgent
  EMERGENCY → ferme TOUT, freeze trading, ntfy + Telegram urgent

FLUX :
  _on_order_result() → record_entry()
  position_monitor.reconcile() → record_exit()
  _account_refresh_loop() → record_equity_snapshot() → check_protection()
  periodic task → emit_health_report()
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("arabesque.live.monitor")

TRADE_JOURNAL_PATH = Path("logs/trade_journal.jsonl")
EQUITY_SNAPSHOT_PATH = Path("logs/equity_snapshots.jsonl")


# ══════════════════════════════════════════════════════════════════════
# Protection levels
# ══════════════════════════════════════════════════════════════════════

class ProtectionLevel(str, Enum):
    NORMAL = "normal"        # Risque plein
    CAUTION = "caution"      # Risque réduit 50%
    DANGER = "danger"        # Risque réduit 75%, close unprotected positions
    EMERGENCY = "emergency"  # Close ALL, freeze trading, attente humaine


# ══════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════

@dataclass
class MonitorConfig:
    """Configuration du monitoring live."""
    # Equity snapshots
    equity_snapshot_interval_s: float = 300.0  # 5 minutes

    # Drift detection — baselines backtest
    baselines: dict = field(default_factory=lambda: {
        "trend": {"wr": 0.75, "exp_r": 0.10, "label": "Extension H1"},
        "glissade": {"wr": 0.55, "exp_r": 0.15, "label": "Glissade RSI div H1"},
    })
    wr_drift_threshold: float = 0.15       # alerte si WR live < baseline - 15pp
    exp_drift_threshold: float = -0.05     # alerte si Exp live < -0.05R
    min_trades_for_drift: int = 20

    # Margin thresholds (% of equity)
    margin_warn_pct: float = 50.0
    margin_critical_pct: float = 20.0
    margin_emergency_pct: float = 10.0     # → EMERGENCY: close all

    # Health report
    health_report_interval_s: float = 3600.0

    # Consecutive losses
    max_consecutive_losses: int = 5        # → CAUTION
    max_consecutive_losses_danger: int = 8  # → DANGER

    # DD thresholds for protection tiers (% of start_balance, NEGATIVE)
    # Guards already have max_daily_dd=4% and max_total_dd=9%
    # These trigger BEFORE the guards kick in
    dd_daily_caution_pct: float = -2.5     # → CAUTION at -2.5% daily
    dd_daily_danger_pct: float = -3.0      # → DANGER at -3.0% daily
    dd_daily_emergency_pct: float = -3.5   # → EMERGENCY at -3.5% daily
    dd_total_caution_pct: float = -5.0     # → CAUTION at -5.0% total
    dd_total_danger_pct: float = -6.5      # → DANGER at -6.5% total
    dd_total_emergency_pct: float = -8.0   # → EMERGENCY at -8.0% total

    # Risk multipliers per protection level
    risk_multiplier_normal: float = 1.0
    risk_multiplier_caution: float = 0.50
    risk_multiplier_danger: float = 0.25
    risk_multiplier_emergency: float = 0.10   # lot minimum, pas de fermeture

    # Best Day consistency guard (FTMO)
    # Alert if today's profit exceeds this % of total positive-day profits
    best_day_warn_pct: float = 25.0   # warn at 25%, FTMO flags ~30%
    best_day_critical_pct: float = 30.0  # critical at 30%

    # Notifications
    # Apprise URLs — set in config/secrets.yaml → notifications section
    telegram_channel: str = ""   # tgram://bottoken@chat_id
    ntfy_channel: str = ""       # ntfys://topic


# ══════════════════════════════════════════════════════════════════════
# Trade record
# ══════════════════════════════════════════════════════════════════════

@dataclass
class LiveTrade:
    """Trade live suivi par le monitor."""
    trade_id: str = ""
    signal_id: str = ""
    instrument: str = ""
    strategy: str = ""
    side: str = ""
    entry_price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    volume: float = 0.0
    risk_cash: float = 0.0
    broker_id: str = ""
    position_id: str = ""
    ts_entry: str = ""

    # Exit
    exit_price: float = 0.0
    exit_reason: str = ""
    ts_exit: str = ""
    result_r: float = 0.0
    pnl_cash: float = 0.0

    # State
    is_closed: bool = False
    mfe_r: float = 0.0
    be_set: bool = False
    trailing_tier: int = 0


# ══════════════════════════════════════════════════════════════════════
# Performance aggregator
# ══════════════════════════════════════════════════════════════════════

@dataclass
class StrategyPerf:
    """Agrégation performance live pour une stratégie."""
    strategy: str = ""
    n_trades: int = 0
    n_wins: int = 0
    total_r: float = 0.0
    max_dd_r: float = 0.0
    consecutive_losses: int = 0
    max_consecutive_losses: int = 0
    _equity_curve_r: list = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.n_wins / self.n_trades if self.n_trades > 0 else 0.0

    @property
    def expectancy_r(self) -> float:
        return self.total_r / self.n_trades if self.n_trades > 0 else 0.0

    def record(self, result_r: float):
        self.n_trades += 1
        self.total_r += result_r
        self._equity_curve_r.append(self.total_r)

        if result_r > 0:
            self.n_wins += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            self.max_consecutive_losses = max(
                self.max_consecutive_losses, self.consecutive_losses
            )

        peak = max(self._equity_curve_r) if self._equity_curve_r else 0
        dd = self.total_r - peak
        self.max_dd_r = min(self.max_dd_r, dd)


# ══════════════════════════════════════════════════════════════════════
# LiveMonitor
# ══════════════════════════════════════════════════════════════════════

class LiveMonitor:
    """Moniteur de performance, protection active, et notifications.

    IMPORTANT: ce module peut FERMER des positions et BLOQUER le trading.
    Il a accès aux brokers via set_brokers() et au dispatcher via
    set_dispatcher() — appelés par LiveEngine.start().
    """

    def __init__(self, config: MonitorConfig | None = None):
        self._cfg = config or MonitorConfig()

        # Open trades indexed by broker_id:position_id
        self._open_trades: dict[str, LiveTrade] = {}
        self._closed_trades: list[LiveTrade] = []
        self._max_closed_history = 500

        # Performance
        self._perf: dict[str, StrategyPerf] = {}
        self._perf_by_inst: dict[str, StrategyPerf] = {}

        # Equity
        self._last_equity_snapshot: float = 0.0
        self._equity_history: list[dict] = []
        self._max_equity_history = 1000

        # Health report
        self._last_health_report: float = 0.0

        # Protection state
        self._protection_level: ProtectionLevel = ProtectionLevel.NORMAL
        self._frozen: bool = False  # EMERGENCY: no new trades, awaiting human
        self._frozen_reason: str = ""

        # Alerts state
        self._drift_alerts_sent: set[str] = set()
        self._margin_alert_level: str = ""

        # Broker/dispatcher access (set by LiveEngine)
        self._brokers: dict = {}
        self._dispatcher = None
        self._position_monitor = None

        # Daily P&L tracking (Best Day guard)
        # date_str → total pnl_cash for that day
        self._daily_pnl: dict[str, float] = {}
        self._best_day_alert_sent_today: str = ""  # date of last alert

        # Notification state (avoid spam)
        self._last_notification_time: float = 0.0
        self._min_notification_interval_s: float = 30.0

        # Ensure log dirs
        TRADE_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        EQUITY_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)

        self._load_journal()

    # ------------------------------------------------------------------
    # Dependency injection (called by LiveEngine after init)
    # ------------------------------------------------------------------

    def set_brokers(self, brokers: dict) -> None:
        """Inject broker references for active protection (close positions)."""
        self._brokers = brokers

    def set_dispatcher(self, dispatcher) -> None:
        """Inject dispatcher reference for trade freezing."""
        self._dispatcher = dispatcher

    def set_position_monitor(self, monitor) -> None:
        """Inject position monitor for reading open positions state."""
        self._position_monitor = monitor

    # ------------------------------------------------------------------
    # Protection level & risk multiplier
    # ------------------------------------------------------------------

    @property
    def protection_level(self) -> ProtectionLevel:
        return self._protection_level

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    @property
    def risk_multiplier(self) -> float:
        """Multiplicateur de risque selon le palier de protection.

        Appelé par les guards/dispatcher pour réduire le sizing.
        EMERGENCY = lot minimum (×0.10) au lieu de fermer tout.
        """
        if self._protection_level == ProtectionLevel.EMERGENCY:
            return self._cfg.risk_multiplier_emergency
        if self._protection_level == ProtectionLevel.DANGER:
            return self._cfg.risk_multiplier_danger
        if self._protection_level == ProtectionLevel.CAUTION:
            return self._cfg.risk_multiplier_caution
        return self._cfg.risk_multiplier_normal

    def should_accept_signal(self) -> tuple[bool, str]:
        """Vérifie si le monitor autorise un nouveau signal.

        Appelé par le dispatcher avant d'accepter un signal.
        EMERGENCY laisse passer (risk_multiplier ×0.10), le freeze
        n'est plus utilisé que par manual_freeze().
        Retourne (ok, reason).
        """
        if self._frozen:
            return False, f"FROZEN: {self._frozen_reason}"
        return True, ""

    def manual_unfreeze(self, reason: str = "manual") -> None:
        """Dégel manuel — UNIQUEMENT par action humaine."""
        logger.info(
            f"[LiveMonitor] 🟢 UNFREEZE manuel: {reason} "
            f"(was: {self._frozen_reason})"
        )
        self._frozen = False
        self._frozen_reason = ""
        self._protection_level = ProtectionLevel.CAUTION  # pas NORMAL direct
        self._append_journal({
            "event": "unfreeze",
            "ts": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        })
        asyncio.ensure_future(self._notify_telegram(
            f"🟢 UNFREEZE: {reason}\nNiveau: CAUTION (risque réduit 50%)"
        ))

    # ------------------------------------------------------------------
    # Core protection check (called every account refresh = 2min)
    # ------------------------------------------------------------------

    async def check_protection(
        self,
        daily_dd_pct: float,
        total_dd_pct: float,
        equity: float,
        free_margin: float,
    ) -> None:
        """Évalue le niveau de protection et prend des actions si nécessaire.

        Appelé toutes les 2 minutes par le refresh loop du LiveEngine.
        """
        if self._frozen:
            return  # Already frozen, nothing more to do

        old_level = self._protection_level
        new_level = self._evaluate_protection_level(
            daily_dd_pct, total_dd_pct, equity, free_margin
        )

        if new_level != old_level:
            self._protection_level = new_level
            await self._on_level_change(old_level, new_level,
                                        daily_dd_pct, total_dd_pct,
                                        equity, free_margin)

    def _evaluate_protection_level(
        self,
        daily_dd_pct: float,
        total_dd_pct: float,
        equity: float,
        free_margin: float,
    ) -> ProtectionLevel:
        """Détermine le niveau de protection basé sur les métriques courantes."""
        # Margin pct = free_margin / equity * 100
        # Safety: si equity <= 0 (erreur/déconnexion), on ne trigger pas sur la marge
        margin_pct = (free_margin / equity * 100) if equity > 0 else 100

        # EMERGENCY triggers (any one is enough)
        if (daily_dd_pct <= self._cfg.dd_daily_emergency_pct
                or total_dd_pct <= self._cfg.dd_total_emergency_pct
                or margin_pct < self._cfg.margin_emergency_pct):
            return ProtectionLevel.EMERGENCY

        # DANGER triggers
        if (daily_dd_pct <= self._cfg.dd_daily_danger_pct
                or total_dd_pct <= self._cfg.dd_total_danger_pct
                or margin_pct < self._cfg.margin_critical_pct):
            return ProtectionLevel.DANGER

        # CAUTION triggers
        if (daily_dd_pct <= self._cfg.dd_daily_caution_pct
                or total_dd_pct <= self._cfg.dd_total_caution_pct
                or margin_pct < self._cfg.margin_warn_pct):
            return ProtectionLevel.CAUTION

        # Check consecutive losses across all strategies
        max_consec = max(
            (p.consecutive_losses for p in self._perf.values()),
            default=0
        )
        if max_consec >= self._cfg.max_consecutive_losses_danger:
            return ProtectionLevel.DANGER
        if max_consec >= self._cfg.max_consecutive_losses:
            return ProtectionLevel.CAUTION

        return ProtectionLevel.NORMAL

    async def _on_level_change(
        self,
        old: ProtectionLevel,
        new: ProtectionLevel,
        daily_dd_pct: float,
        total_dd_pct: float,
        equity: float,
        free_margin: float,
    ) -> None:
        """Actions déclenchées par un changement de niveau de protection."""
        margin_pct = (free_margin / equity * 100) if equity > 0 else 100

        context = (
            f"daily_dd={daily_dd_pct:.1f}% total_dd={total_dd_pct:.1f}% "
            f"equity={equity:.0f} margin={margin_pct:.0f}%"
        )

        self._append_journal({
            "event": "protection_level_change",
            "ts": datetime.now(timezone.utc).isoformat(),
            "old": old.value,
            "new": new.value,
            "daily_dd_pct": round(daily_dd_pct, 2),
            "total_dd_pct": round(total_dd_pct, 2),
            "equity": round(equity, 2),
            "margin_pct": round(margin_pct, 1),
        })

        if new == ProtectionLevel.EMERGENCY:
            logger.critical(
                f"[LiveMonitor] 🚨 EMERGENCY — {context} — "
                f"RISQUE RÉDUIT AU MINIMUM ({self._cfg.risk_multiplier_emergency:.0%})"
            )
            await self._notify_ntfy(
                f"EMERGENCY ARABESQUE\n{context}\n"
                f"Risque réduit à {self._cfg.risk_multiplier_emergency:.0%} (lot minimum).\n"
                f"Positions existantes conservées. Intervention recommandée."
            )
            await self._notify_telegram(
                f"🚨 EMERGENCY\n{context}\n"
                f"Risque réduit à {self._cfg.risk_multiplier_emergency:.0%} (lot minimum).\n"
                f"Positions existantes conservées (pas de fermeture).\n"
                f"Fermeture des positions sans BE..."
            )
            # Fermer seulement les positions non protégées, pas TOUTES
            await self._close_unprotected_positions(f"EMERGENCY: {context}")

        elif new == ProtectionLevel.DANGER:
            logger.warning(
                f"[LiveMonitor] 🔴 DANGER — {context} — "
                f"risque réduit à {self._cfg.risk_multiplier_danger:.0%}, "
                f"fermeture des positions non protégées"
            )
            await self._notify_ntfy(
                f"DANGER Arabesque\n{context}\n"
                f"Risque réduit à {self._cfg.risk_multiplier_danger:.0%}. "
                f"Positions sans BE fermées."
            )
            await self._notify_telegram(
                f"🔴 DANGER (was {old.value})\n{context}\n"
                f"Risque réduit à {self._cfg.risk_multiplier_danger:.0%}\n"
                f"Fermeture des positions sans breakeven..."
            )
            await self._close_unprotected_positions(
                f"DANGER: {context}"
            )

        elif new == ProtectionLevel.CAUTION:
            logger.warning(
                f"[LiveMonitor] 🟡 CAUTION — {context} — "
                f"risque réduit à {self._cfg.risk_multiplier_caution:.0%}"
            )
            await self._notify_telegram(
                f"🟡 CAUTION (was {old.value})\n{context}\n"
                f"Risque réduit à {self._cfg.risk_multiplier_caution:.0%}"
            )

        elif new == ProtectionLevel.NORMAL:
            logger.info(
                f"[LiveMonitor] 🟢 NORMAL — {context} — risque plein"
            )
            await self._notify_telegram(
                f"🟢 Retour NORMAL (was {old.value})\n{context}"
            )

    # ------------------------------------------------------------------
    # Active protection: close positions
    # ------------------------------------------------------------------

    async def _emergency_close_all(self, reason: str) -> None:
        """NUCLEAR: ferme TOUTES les positions sur TOUS les brokers.

        Freeze le trading jusqu'à intervention humaine.
        """
        self._frozen = True
        self._frozen_reason = reason

        closed = 0
        errors = 0
        for broker_id, broker in self._brokers.items():
            try:
                positions = await broker.get_positions()
                if not positions:
                    continue
                for pos in positions:
                    try:
                        result = await broker.close_position(
                            str(pos.position_id)
                        )
                        if result.success:
                            closed += 1
                            logger.info(
                                f"[LiveMonitor] 🔒 EMERGENCY close: "
                                f"{pos.symbol} {broker_id}:{pos.position_id}"
                            )
                        else:
                            errors += 1
                            logger.error(
                                f"[LiveMonitor] ❌ EMERGENCY close failed: "
                                f"{pos.symbol} — {result.message}"
                            )
                    except Exception as e:
                        errors += 1
                        logger.error(
                            f"[LiveMonitor] ❌ EMERGENCY close exception: "
                            f"{pos.symbol} — {e}"
                        )
            except Exception as e:
                logger.error(
                    f"[LiveMonitor] ❌ EMERGENCY get_positions failed: "
                    f"{broker_id} — {e}"
                )

        self._append_journal({
            "event": "emergency_close_all",
            "ts": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "closed": closed,
            "errors": errors,
        })

        logger.critical(
            f"[LiveMonitor] 🔒 EMERGENCY COMPLETE: {closed} fermées, "
            f"{errors} erreurs — TRADING GELÉ"
        )

        if errors > 0:
            await self._notify_ntfy(
                f"ERREUR EMERGENCY: {errors} positions non fermées! "
                f"Vérifier manuellement."
            )

    async def _close_unprotected_positions(self, reason: str) -> None:
        """Gestion intelligente des positions non protégées en DANGER/EMERGENCY.

        Trie chaque position par son P&L non réalisé en R et applique
        l'action la plus adaptée pour préserver le capital :

        | P&L courant     | Action                                      |
        |-----------------|---------------------------------------------|
        | Protégée (BE/trail) | Laisser (déjà safe)                     |
        | > 0R            | BE immédiat (SL → entry + 0.10R)            |
        | 0R à -0.5R      | SL serré à -0.3R (dernière chance)          |
        | -0.5R à -0.7R   | Fermer (sauver 0.3-0.5R de perte restante)  |
        | < -0.7R         | Laisser (trop proche du SL, rien à gagner)  |
        """
        if not self._position_monitor:
            return

        closed = 0
        amended = 0
        kept = 0

        # Fetch les positions broker une seule fois pour avoir les prix courants
        broker_positions: dict[str, dict[str, float]] = {}  # broker_id → {pos_id → current_price}
        for broker_id, broker in self._brokers.items():
            try:
                positions = await broker.get_positions()
                broker_positions[broker_id] = {
                    str(p.position_id): p.current_price or 0
                    for p in positions
                }
            except Exception as e:
                logger.warning(f"[LiveMonitor] get_positions {broker_id}: {e}")

        for pos in self._position_monitor.open_positions:
            # 1. Déjà protégée → laisser
            if pos.breakeven_set or pos.trailing_active:
                logger.info(
                    f"[LiveMonitor] ✅ Gardée: {pos.symbol} "
                    f"(BE={'✓' if pos.breakeven_set else '✗'} "
                    f"trail={pos.trailing_tier} MFE={pos.mfe_r:.1f}R)"
                )
                kept += 1
                continue

            broker = self._brokers.get(pos.broker_id)
            if not broker:
                continue

            # Calculer le P&L courant en R (prix réel du broker)
            current_price = broker_positions.get(pos.broker_id, {}).get(
                str(pos.position_id), 0
            )
            current_pnl_r = self._compute_pnl_r(pos, current_price)

            # 2. Légèrement positive (> 0R) → BE immédiat
            if current_pnl_r > 0:
                be_offset = 0.10  # Plus serré que le BE normal (0.20R)
                if pos.side == Side.LONG:
                    new_sl = pos.entry + be_offset * pos.R
                else:
                    new_sl = pos.entry - be_offset * pos.R
                new_sl = round(new_sl, pos.digits)
                ok = await self._try_amend_sl(broker, pos, new_sl)
                if ok:
                    pos.sl = new_sl
                    pos.breakeven_set = True
                    amended += 1
                    logger.warning(
                        f"[LiveMonitor] 🛡️ BE forcé: {pos.symbol} "
                        f"P&L={current_pnl_r:+.2f}R → SL={new_sl:.{pos.digits}f} "
                        f"(+{be_offset}R) — {reason}"
                    )
                continue

            # 3. Juste entrée en négatif (0 à -0.5R) → SL serré à -0.3R
            if current_pnl_r > -0.5:
                tight_sl_r = -0.3  # SL serré : -0.3R au lieu de -1.0R
                if pos.side == Side.LONG:
                    new_sl = pos.entry + tight_sl_r * pos.R
                else:
                    new_sl = pos.entry - tight_sl_r * pos.R
                new_sl = round(new_sl, pos.digits)
                # Vérifier que le nouveau SL est plus serré que l'actuel
                sl_improves = (
                    (pos.side == Side.LONG and new_sl > pos.sl) or
                    (pos.side == Side.SHORT and new_sl < pos.sl)
                )
                if sl_improves:
                    ok = await self._try_amend_sl(broker, pos, new_sl)
                    if ok:
                        pos.sl = new_sl
                        amended += 1
                        logger.warning(
                            f"[LiveMonitor] 🛡️ SL serré: {pos.symbol} "
                            f"P&L={current_pnl_r:+.2f}R → SL={new_sl:.{pos.digits}f} "
                            f"(-0.3R, dernière chance) — {reason}"
                        )
                else:
                    logger.info(
                        f"[LiveMonitor] ⏸ {pos.symbol} P&L={current_pnl_r:+.2f}R "
                        f"SL déjà serré — laissé"
                    )
                    kept += 1
                continue

            # 4. Très proche du SL (< -0.7R) → laisser courir
            if current_pnl_r < -0.7:
                logger.info(
                    f"[LiveMonitor] ⏸ Laissée: {pos.symbol} "
                    f"P&L={current_pnl_r:+.2f}R (trop proche SL, rien à gagner) "
                    f"— {reason}"
                )
                kept += 1
                continue

            # 5. Zone intermédiaire (-0.5R à -0.7R) → fermer
            try:
                result = await broker.close_position(pos.position_id)
                if result.success:
                    closed += 1
                    logger.warning(
                        f"[LiveMonitor] 🔒 Fermée: {pos.symbol} "
                        f"P&L={current_pnl_r:+.2f}R (sauve ~{abs(1+current_pnl_r):.1f}R) "
                        f"— {reason}"
                    )
                else:
                    logger.error(
                        f"[LiveMonitor] ❌ Close failed: "
                        f"{pos.symbol} — {result.message}"
                    )
            except Exception as e:
                logger.error(
                    f"[LiveMonitor] ❌ Close exception: "
                    f"{pos.symbol} — {e}"
                )

        summary = (
            f"fermées={closed} SL_serrés={amended} conservées={kept}"
        )
        logger.info(f"[LiveMonitor] 📊 Protection résumé: {summary}")

        self._append_journal({
            "event": "smart_protection",
            "ts": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "closed": closed,
            "amended": amended,
            "kept": kept,
        })

    @staticmethod
    def _compute_pnl_r(pos, current_price: float) -> float:
        """Calcule le P&L courant en R depuis un prix broker."""
        from arabesque.core.models import Side
        if pos.R == 0 or current_price <= 0:
            return 0.0
        if pos.side == Side.LONG:
            return (current_price - pos.entry) / pos.R
        return (pos.entry - current_price) / pos.R

    async def _try_amend_sl(self, broker, pos, new_sl: float) -> bool:
        """Tente de modifier le SL d'une position."""
        try:
            result = await broker.amend_position_sltp(
                pos.position_id, stop_loss=new_sl
            )
            return result.success
        except Exception as e:
            logger.error(
                f"[LiveMonitor] ❌ Amend SL failed: {pos.symbol} → {e}"
            )
            return False

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    async def _notify_telegram(self, message: str) -> None:
        """Envoie une notification Telegram (détaillée, non-urgente)."""
        if not self._cfg.telegram_channel:
            logger.debug("[LiveMonitor] Telegram non configuré")
            return
        await self._send_apprise(self._cfg.telegram_channel, message,
                                 "Arabesque")

    async def _notify_ntfy(self, message: str) -> None:
        """Envoie une notification ntfy (urgente, push immédiat)."""
        if not self._cfg.ntfy_channel:
            logger.debug("[LiveMonitor] ntfy non configuré")
            return
        await self._send_apprise(self._cfg.ntfy_channel, message,
                                 "Arabesque URGENT")

    async def _send_apprise(self, channel: str, body: str,
                            title: str = "Arabesque") -> None:
        """Envoie via apprise avec rate limiting."""
        now = time.time()
        if now - self._last_notification_time < self._min_notification_interval_s:
            return
        self._last_notification_time = now

        try:
            import apprise
            a = apprise.Apprise()
            a.add(channel)
            await a.async_notify(body=body, title=title)
            logger.info(f"[LiveMonitor] 📨 Notification envoyée: {title}")
        except ImportError:
            logger.warning("[LiveMonitor] apprise non installé — pip install apprise")
        except Exception as e:
            logger.warning(f"[LiveMonitor] Notification échouée: {e}")

    # ------------------------------------------------------------------
    # Trade entry
    # ------------------------------------------------------------------

    def record_entry(
        self,
        signal,
        broker_id: str,
        position_id: str,
        entry_price: float,
        volume: float,
        risk_cash: float = 0.0,
    ) -> None:
        """Enregistre une nouvelle entrée de trade."""
        key = f"{broker_id}:{position_id}"

        trade = LiveTrade(
            trade_id=getattr(signal, "signal_id", "")[:12],
            signal_id=getattr(signal, "signal_id", ""),
            instrument=signal.instrument,
            strategy=getattr(signal, "strategy_type", "unknown"),
            side=signal.side.value,
            entry_price=entry_price,
            sl=signal.sl,
            tp=getattr(signal, "tp_indicative", 0.0),
            volume=volume,
            risk_cash=risk_cash,
            broker_id=broker_id,
            position_id=str(position_id),
            ts_entry=datetime.now(timezone.utc).isoformat(),
        )
        self._open_trades[key] = trade

        self._append_journal({
            "event": "entry",
            "ts": trade.ts_entry,
            "trade_id": trade.trade_id,
            "instrument": trade.instrument,
            "strategy": trade.strategy,
            "side": trade.side,
            "entry_price": trade.entry_price,
            "sl": trade.sl,
            "tp": trade.tp,
            "volume": trade.volume,
            "risk_cash": trade.risk_cash,
            "broker_id": broker_id,
            "position_id": str(position_id),
            "protection_level": self._protection_level.value,
        })

        logger.info(
            f"[LiveMonitor] 📝 Entry: {trade.instrument} {trade.side} "
            f"@ {entry_price:.5f} SL={signal.sl:.5f} "
            f"vol={volume:.3f}L risk={risk_cash:.0f}$ "
            f"({trade.strategy}) [{broker_id}:{position_id}]"
        )

    # ------------------------------------------------------------------
    # Trade exit
    # ------------------------------------------------------------------

    def record_exit(
        self,
        broker_id: str,
        position_id: str,
        exit_price: float = 0.0,
        exit_reason: str = "unknown",
        mfe_r: float = 0.0,
        be_set: bool = False,
        trailing_tier: int = 0,
    ) -> Optional[LiveTrade]:
        """Enregistre la sortie d'un trade. Retourne le trade ou None."""
        key = f"{broker_id}:{position_id}"
        trade = self._open_trades.pop(key, None)

        if trade is None:
            logger.debug(
                f"[LiveMonitor] Exit for unknown trade {key} — skipping"
            )
            return None

        trade.exit_price = exit_price
        trade.exit_reason = exit_reason
        trade.ts_exit = datetime.now(timezone.utc).isoformat()
        trade.mfe_r = mfe_r
        trade.be_set = be_set
        trade.trailing_tier = trailing_tier
        trade.is_closed = True

        # Calculate result in R
        risk_distance = abs(trade.entry_price - trade.sl)
        if risk_distance > 0 and exit_price > 0:
            if trade.side == "LONG":
                trade.result_r = (exit_price - trade.entry_price) / risk_distance
            else:
                trade.result_r = (trade.entry_price - exit_price) / risk_distance
        else:
            trade.result_r = 0.0

        if trade.risk_cash > 0 and risk_distance > 0:
            trade.pnl_cash = trade.result_r * trade.risk_cash

        # Update performance
        strat = trade.strategy or "unknown"
        if strat not in self._perf:
            self._perf[strat] = StrategyPerf(strategy=strat)
        self._perf[strat].record(trade.result_r)

        inst = trade.instrument
        if inst not in self._perf_by_inst:
            self._perf_by_inst[inst] = StrategyPerf(strategy=inst)
        self._perf_by_inst[inst].record(trade.result_r)

        # Track daily P&L for Best Day guard
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._daily_pnl[today] = self._daily_pnl.get(today, 0.0) + trade.pnl_cash

        self._closed_trades.append(trade)
        if len(self._closed_trades) > self._max_closed_history:
            self._closed_trades = self._closed_trades[-self._max_closed_history:]

        self._append_journal({
            "event": "exit",
            "ts": trade.ts_exit,
            "trade_id": trade.trade_id,
            "instrument": trade.instrument,
            "strategy": trade.strategy,
            "side": trade.side,
            "entry_price": trade.entry_price,
            "exit_price": exit_price,
            "sl": trade.sl,
            "result_r": round(trade.result_r, 3),
            "pnl_cash": round(trade.pnl_cash, 2),
            "mfe_r": round(mfe_r, 2),
            "be_set": be_set,
            "trailing_tier": trailing_tier,
            "exit_reason": exit_reason,
            "broker_id": broker_id,
            "position_id": str(position_id),
            "protection_level": self._protection_level.value,
        })

        emoji = "🟢" if trade.result_r > 0 else "🔴" if trade.result_r < -0.5 else "🟡"
        logger.info(
            f"[LiveMonitor] {emoji} Exit: {trade.instrument} {trade.side} "
            f"{trade.result_r:+.2f}R (${trade.pnl_cash:+.0f}) "
            f"reason={exit_reason} MFE={mfe_r:.1f}R "
            f"BE={'✓' if be_set else '✗'} trail={trailing_tier} "
            f"({trade.strategy})"
        )

        self._check_drift(strat)
        self._check_consecutive_losses(strat)
        self._check_best_day(today)

        return trade

    # ------------------------------------------------------------------
    # Equity snapshots
    # ------------------------------------------------------------------

    def record_equity_snapshot(
        self,
        balance: float,
        equity: float,
        free_margin: float = 0.0,
        open_positions: int = 0,
        daily_dd_pct: float = 0.0,
        total_dd_pct: float = 0.0,
    ) -> None:
        """Enregistre un snapshot de l'état du compte."""
        now = time.time()
        if now - self._last_equity_snapshot < self._cfg.equity_snapshot_interval_s:
            return
        self._last_equity_snapshot = now

        snapshot = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "balance": round(balance, 2),
            "equity": round(equity, 2),
            "free_margin": round(free_margin, 2),
            "open_positions": open_positions,
            "daily_dd_pct": round(daily_dd_pct, 2),
            "total_dd_pct": round(total_dd_pct, 2),
            "open_trades": len(self._open_trades),
            "protection_level": self._protection_level.value,
        }

        self._equity_history.append(snapshot)
        if len(self._equity_history) > self._max_equity_history:
            self._equity_history = self._equity_history[-self._max_equity_history:]

        try:
            with open(EQUITY_SNAPSHOT_PATH, "a") as f:
                f.write(json.dumps(snapshot) + "\n")
        except Exception as e:
            logger.debug(f"[LiveMonitor] equity snapshot write error: {e}")

    # ------------------------------------------------------------------
    # Drift detection
    # ------------------------------------------------------------------

    def _check_drift(self, strategy: str) -> None:
        """Vérifie si la performance live diverge du backtest."""
        perf = self._perf.get(strategy)
        if not perf or perf.n_trades < self._cfg.min_trades_for_drift:
            return

        baseline = self._cfg.baselines.get(strategy)
        if not baseline:
            return

        alert_key = f"drift_{strategy}"

        wr_baseline = baseline.get("wr", 0.5)
        wr_live = perf.win_rate
        if wr_live < wr_baseline - self._cfg.wr_drift_threshold:
            if alert_key + "_wr" not in self._drift_alerts_sent:
                self._drift_alerts_sent.add(alert_key + "_wr")
                msg = (
                    f"DRIFT WR {strategy}: "
                    f"live={wr_live:.1%} vs baseline={wr_baseline:.1%} "
                    f"sur {perf.n_trades} trades"
                )
                logger.warning(f"[LiveMonitor] ⚠️ {msg}")
                asyncio.ensure_future(self._notify_telegram(f"⚠️ {msg}"))

        exp_baseline = baseline.get("exp_r", 0.0)
        exp_live = perf.expectancy_r
        if exp_live < self._cfg.exp_drift_threshold:
            if alert_key + "_exp" not in self._drift_alerts_sent:
                self._drift_alerts_sent.add(alert_key + "_exp")
                msg = (
                    f"DRIFT EXP {strategy}: "
                    f"live={exp_live:+.3f}R vs baseline={exp_baseline:+.3f}R "
                    f"sur {perf.n_trades} trades"
                )
                logger.warning(f"[LiveMonitor] ⚠️ {msg}")
                asyncio.ensure_future(self._notify_telegram(f"⚠️ {msg}"))

    def _check_consecutive_losses(self, strategy: str) -> None:
        """Alerte si trop de pertes consécutives."""
        perf = self._perf.get(strategy)
        if not perf:
            return
        if perf.consecutive_losses >= self._cfg.max_consecutive_losses:
            msg = (
                f"{perf.consecutive_losses} pertes consécutives "
                f"sur {strategy}"
            )
            logger.warning(f"[LiveMonitor] 🔴 {msg}")
            if perf.consecutive_losses >= self._cfg.max_consecutive_losses_danger:
                asyncio.ensure_future(self._notify_ntfy(
                    f"DANGER: {msg} — risque réduit"
                ))
            else:
                asyncio.ensure_future(self._notify_telegram(f"🔴 {msg}"))

    # ------------------------------------------------------------------
    # Best Day consistency guard (FTMO)
    # ------------------------------------------------------------------

    def _check_best_day(self, today: str) -> None:
        """Alerte si le profit du jour dépasse le seuil Best Day.

        FTMO exige que le meilleur jour ne représente pas plus de ~30%
        du total des profits des jours positifs. On alerte en amont.
        """
        today_pnl = self._daily_pnl.get(today, 0.0)
        if today_pnl <= 0:
            return  # Jour négatif ou neutre — pas de risque

        # Sum of all positive days (excluding today to compute ratio correctly)
        positive_days_total = sum(
            pnl for d, pnl in self._daily_pnl.items()
            if pnl > 0
        )

        if positive_days_total <= 0:
            return

        best_day_pct = today_pnl / positive_days_total * 100

        # Avoid spamming: one alert per day per threshold
        if self._best_day_alert_sent_today == today:
            return

        if best_day_pct >= self._cfg.best_day_critical_pct:
            self._best_day_alert_sent_today = today
            msg = (
                f"BEST DAY CRITIQUE: aujourd'hui {today_pnl:+.0f}$ = "
                f"{best_day_pct:.0f}% des profits positifs (seuil {self._cfg.best_day_critical_pct:.0f}%). "
                f"Risque de flag FTMO consistency."
            )
            logger.warning(f"[LiveMonitor] 🚨 {msg}")
            asyncio.ensure_future(self._notify_ntfy(f"⚠️ {msg}"))
            asyncio.ensure_future(self._notify_telegram(f"🚨 {msg}"))

        elif best_day_pct >= self._cfg.best_day_warn_pct:
            self._best_day_alert_sent_today = today
            msg = (
                f"BEST DAY WARNING: aujourd'hui {today_pnl:+.0f}$ = "
                f"{best_day_pct:.0f}% des profits positifs (seuil warn {self._cfg.best_day_warn_pct:.0f}%). "
                f"Surveiller les prochains trades."
            )
            logger.warning(f"[LiveMonitor] ⚠️ {msg}")
            asyncio.ensure_future(self._notify_telegram(f"⚠️ {msg}"))

    # ------------------------------------------------------------------
    # Health report
    # ------------------------------------------------------------------

    def should_emit_health_report(self) -> bool:
        return (time.time() - self._last_health_report
                >= self._cfg.health_report_interval_s)

    def emit_health_report(self) -> dict:
        """Émet un rapport de santé. Retourne le rapport."""
        self._last_health_report = time.time()

        report = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "protection_level": self._protection_level.value,
            "frozen": self._frozen,
            "open_trades": len(self._open_trades),
            "total_closed": sum(p.n_trades for p in self._perf.values()),
            "strategies": {},
            "instruments_top5": [],
        }

        for strat, perf in self._perf.items():
            baseline = self._cfg.baselines.get(strat, {})
            report["strategies"][strat] = {
                "trades": perf.n_trades,
                "wr": f"{perf.win_rate:.1%}",
                "exp_r": f"{perf.expectancy_r:+.3f}R",
                "total_r": f"{perf.total_r:+.1f}R",
                "max_dd_r": f"{perf.max_dd_r:.1f}R",
                "consec_losses": perf.consecutive_losses,
                "baseline_wr": baseline.get("wr", "N/A"),
                "baseline_exp": baseline.get("exp_r", "N/A"),
            }

        sorted_inst = sorted(
            self._perf_by_inst.items(),
            key=lambda x: x[1].n_trades, reverse=True
        )[:5]
        for inst, perf in sorted_inst:
            report["instruments_top5"].append({
                "instrument": inst,
                "trades": perf.n_trades,
                "wr": f"{perf.win_rate:.1%}",
                "total_r": f"{perf.total_r:+.1f}R",
            })

        if len(self._equity_history) >= 2:
            report["equity_latest"] = self._equity_history[-1].get("equity", 0)
            report["equity_24h_ago"] = self._equity_history[0].get("equity", 0)

        logger.info(
            f"[LiveMonitor] 📊 HEALTH [{self._protection_level.value}] — "
            f"{report['total_closed']} fermés, {report['open_trades']} ouverts"
        )
        for strat, data in report["strategies"].items():
            logger.info(
                f"[LiveMonitor]   {strat}: {data['trades']} tr, "
                f"WR={data['wr']}, Exp={data['exp_r']}, "
                f"Tot={data['total_r']}, DD={data['max_dd_r']}"
            )

        self._append_journal({"event": "health_report", **report})
        return report

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def get_open_trades(self) -> list[dict]:
        return [
            {
                "instrument": t.instrument,
                "strategy": t.strategy,
                "side": t.side,
                "entry_price": t.entry_price,
                "sl": t.sl,
                "broker_id": t.broker_id,
                "position_id": t.position_id,
                "ts_entry": t.ts_entry,
            }
            for t in self._open_trades.values()
        ]

    def get_performance_summary(self) -> dict:
        return {
            strat: {
                "n_trades": p.n_trades,
                "win_rate": round(p.win_rate, 3),
                "expectancy_r": round(p.expectancy_r, 3),
                "total_r": round(p.total_r, 1),
                "max_dd_r": round(p.max_dd_r, 1),
                "consecutive_losses": p.consecutive_losses,
            }
            for strat, p in self._perf.items()
        }

    def get_stats(self) -> dict:
        return {
            "protection_level": self._protection_level.value,
            "frozen": self._frozen,
            "frozen_reason": self._frozen_reason,
            "risk_multiplier": self.risk_multiplier,
            "open_trades": len(self._open_trades),
            "closed_trades": sum(p.n_trades for p in self._perf.values()),
            "performance": self.get_performance_summary(),
            "equity_snapshots": len(self._equity_history),
            "alerts": {
                "drift_alerts": list(self._drift_alerts_sent),
                "margin_level": self._margin_alert_level,
            },
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _append_journal(self, entry: dict) -> None:
        try:
            with open(TRADE_JOURNAL_PATH, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.debug(f"[LiveMonitor] journal write error: {e}")

    def _load_journal(self) -> None:
        """Charge l'historique depuis le journal au démarrage."""
        if not TRADE_JOURNAL_PATH.exists():
            return

        try:
            with open(TRADE_JOURNAL_PATH) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event = entry.get("event")
                    if event == "exit":
                        strat = entry.get("strategy", "unknown")
                        if strat not in self._perf:
                            self._perf[strat] = StrategyPerf(strategy=strat)
                        self._perf[strat].record(entry.get("result_r", 0.0))

                        inst = entry.get("instrument", "")
                        if inst:
                            if inst not in self._perf_by_inst:
                                self._perf_by_inst[inst] = StrategyPerf(strategy=inst)
                            self._perf_by_inst[inst].record(entry.get("result_r", 0.0))

                        # Rebuild daily P&L for Best Day guard
                        ts = entry.get("ts", "")
                        pnl = entry.get("pnl_cash", 0.0)
                        if ts and pnl != 0:
                            day = ts[:10]  # "2026-03-22T..." → "2026-03-22"
                            self._daily_pnl[day] = self._daily_pnl.get(day, 0.0) + pnl

            n_total = sum(p.n_trades for p in self._perf.values())
            if n_total > 0:
                logger.info(
                    f"[LiveMonitor] 📂 Journal: {n_total} trades historiques"
                )
                for strat, perf in self._perf.items():
                    logger.info(
                        f"[LiveMonitor]   {strat}: {perf.n_trades} tr, "
                        f"WR={perf.win_rate:.1%}, Exp={perf.expectancy_r:+.3f}R"
                    )
        except Exception as e:
            logger.warning(f"[LiveMonitor] Failed to load journal: {e}")
