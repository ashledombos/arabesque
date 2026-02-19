"""
Arabesque v2 — Modèles de données.

Fusion v0.1 (types propres, audit trail) + v1 (pragmatisme pipeline).
Chaque objet est conçu pour l'audit : append-only, immutable une fois résolu.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ── Enums ───────────────────────────────────────────────────────────────────────

class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Regime(str, Enum):
    BULL_RANGE = "bull_range"
    BULL_TREND = "bull_trend"
    BEAR_RANGE = "bear_range"
    BEAR_TREND = "bear_trend"
    SQUEEZE = "squeeze"


class DecisionType(str, Enum):
    # Entrée
    SIGNAL_RECEIVED = "signal_received"
    SIGNAL_ACCEPTED = "signal_accepted"
    SIGNAL_REJECTED = "signal_rejected"
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    ORDER_REJECTED = "order_rejected"
    ORDER_TIMEOUT = "order_timeout"
    # Gestion
    SL_MOVED = "sl_moved"
    SL_BREAKEVEN = "sl_breakeven"
    TRAILING_ACTIVATED = "trailing_activated"
    TRAILING_TIGHTENED = "trailing_tightened"
    # Sortie
    EXIT_SL = "exit_sl"
    EXIT_TP = "exit_tp"
    EXIT_TRAILING = "exit_trailing"
    EXIT_GIVEBACK = "exit_giveback"
    EXIT_DEADFISH = "exit_deadfish"
    EXIT_TIME_STOP = "exit_time_stop"
    EXIT_MANUAL = "exit_manual"
    EXIT_PROP_GUARD = "exit_prop_guard"
    # Counterfactual
    COUNTERFACTUAL_UPDATE = "counterfactual_update"


class RejectReason(str, Enum):
    SPREAD_TOO_WIDE = "spread_too_wide"
    SLIPPAGE_TOO_HIGH = "slippage_too_high"
    DAILY_DD_LIMIT = "daily_dd_limit"
    MAX_DD_LIMIT = "max_dd_limit"
    MAX_POSITIONS = "max_positions"
    OPEN_RISK_LIMIT = "open_risk_limit"
    MAX_DAILY_TRADES = "max_daily_trades"
    DUPLICATE_INSTRUMENT = "duplicate_instrument"
    MARGIN_INSUFFICIENT = "margin_insufficient"
    MIN_RR_NOT_MET = "min_rr_not_met"
    ORDER_EXPIRED = "order_expired"
    BB_SQUEEZE = "bb_squeeze"


# ── Signal ───────────────────────────────────────────────────────────────────

@dataclass
class Signal:
    """Signal émis par le générateur interne (cTrader → OHLCV → indicateurs).

    NB : les champs tv_close / tv_open sont des alias maintenus pour
    la compatibilité avec l'ancien code backtest. Utiliser `close` / `open_`
    dans tout nouveau code.
    """
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    instrument: str = ""
    side: Side = Side.LONG
    timeframe: str = "1h"

    # Prix au moment du signal (bougie confirmée)
    close: float = 0.0        # close de la bougie signal
    open_: float = 0.0        # open de la bougie signal ("open" est un builtin Python)

    # Niveaux calculés
    sl: float = 0.0
    tp_indicative: float = 0.0
    atr: float = 0.0
    atr_htf: float = 0.0

    # Contexte technique (pour audit + gestion contextuelle)
    rsi: float = 50.0
    cmf: float = 0.0
    bb_lower: float = 0.0
    bb_mid: float = 0.0
    bb_upper: float = 0.0
    bb_width: float = 0.0
    wr_14: float = 0.0
    ema200_ltf: float = 0.0
    htf_ema_fast: float = 0.0
    htf_ema_slow: float = 0.0
    htf_adx: float = 0.0
    regime: str = "bull_range"
    max_spread_atr: float = 0.3

    # R:R
    rr: float = 0.0

    # Type de stratégie
    strategy_type: str = "mean_reversion"

    # Sub-type et facteurs de qualité
    sub_type: str = ""
    label_factors: dict = field(default_factory=dict)

    # ── Alias de compatibilité (ancienne interface TradingView) ──────
    # Ces propriétés permettent au code backtest existant de continuer
    # à fonctionner sans modification. À supprimer dans une future version.

    @property
    def tv_close(self) -> float:
        return self.close

    @tv_close.setter
    def tv_close(self, v: float):
        self.close = v

    @property
    def tv_open(self) -> float:
        return self.open_

    @tv_open.setter
    def tv_open(self, v: float):
        self.open_ = v

    @classmethod
    def from_bar(cls, instrument: str, row, df=None, timeframe: str = "1h") -> "Signal":
        """
        Construit un Signal minimal depuis une ligne de DataFrame OHLCV.
        Utilisé principalement pour les tests et le replay parquet.
        Les signaux complets sont générés par signal_gen via generate_signals().
        """
        return cls(
            instrument=instrument,
            timeframe=timeframe,
            close=float(row.get("Close", 0)),
            open_=float(row.get("Open", 0)),
        )


# ── Decision (Event) ────────────────────────────────────────────────────────────────

@dataclass
class Decision:
    """'Événement atomique dans l'audit trail."""
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    decision_type: DecisionType = DecisionType.SIGNAL_RECEIVED
    signal_id: str = ""
    position_id: str = ""
    instrument: str = ""

    reason: str = ""
    reject_reason: RejectReason | None = None

    price_at_decision: float = 0.0
    spread_at_decision: float = 0.0

    value_before: float = 0.0
    value_after: float = 0.0

    metadata: dict = field(default_factory=dict)


# ── Position ──────────────────────────────────────────────────────────────────────

@dataclass
class Position:
    """Position avec suivi MFE/MAE et gestion contextuelle."""
    position_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    signal_id: str = ""
    instrument: str = ""
    side: Side = Side.LONG

    # Timing
    ts_signal: datetime | None = None
    ts_entry: datetime | None = None
    ts_exit: datetime | None = None

    # Prix — FILL RÉEL, pas le signal
    entry: float = 0.0
    sl: float = 0.0
    sl_initial: float = 0.0
    tp: float = 0.0
    exit_price: float = 0.0
    exit_reason: str = ""

    # Risk
    risk_cash: float = 0.0
    volume: float = 0.0
    atr_at_entry: float = 0.0

    # État
    is_open: bool = True
    bars_open: int = 0
    current_price: float = 0.0

    # MFE/MAE
    max_favorable_price: float = 0.0
    max_adverse_price: float = 0.0

    # Trailing state
    trailing_active: bool = False
    trailing_tier: int = 0
    breakeven_set: bool = False

    # Contexte courant
    current_rsi: float = 50.0
    current_cmf: float = 0.0
    current_bb_width: float = 0.01
    current_ema200: float = 0.0

    # Signal d'origine (pour audit)
    signal_data: dict = field(default_factory=dict)

    @property
    def R(self) -> float:
        return abs(self.entry - self.sl_initial) if self.sl_initial != 0 else 0

    @property
    def current_r(self) -> float:
        if self.R == 0:
            return 0.0
        if self.side == Side.LONG:
            return (self.current_price - self.entry) / self.R
        return (self.entry - self.current_price) / self.R

    @property
    def mfe_r(self) -> float:
        if self.R == 0:
            return 0.0
        if self.side == Side.LONG:
            return (self.max_favorable_price - self.entry) / self.R
        return (self.entry - self.max_favorable_price) / self.R

    @property
    def mae_r(self) -> float:
        if self.R == 0:
            return 0.0
        if self.side == Side.LONG:
            return (self.max_adverse_price - self.entry) / self.R
        return (self.entry - self.max_adverse_price) / self.R

    @property
    def result_r(self) -> float | None:
        if self.is_open:
            return None
        if self.R == 0:
            return 0.0
        if self.side == Side.LONG:
            return (self.exit_price - self.entry) / self.R
        return (self.entry - self.exit_price) / self.R

    def update_price(self, high: float, low: float, close: float):
        self.current_price = close
        if self.side == Side.LONG:
            self.max_favorable_price = max(self.max_favorable_price, high)
            if self.max_adverse_price == 0:
                self.max_adverse_price = low
            else:
                self.max_adverse_price = min(self.max_adverse_price, low)
        else:
            if self.max_favorable_price == 0:
                self.max_favorable_price = low
            else:
                self.max_favorable_price = min(self.max_favorable_price, low)
            self.max_adverse_price = max(self.max_adverse_price, high)

    def recalculate_from_fill(self, fill_price: float, signal: "Signal"):
        """Recalcule SL/TP à partir du fill réel."""
        self.entry = fill_price
        if signal.sl != 0:
            original_sl_dist = abs(signal.close - signal.sl)
            if self.side == Side.LONG:
                self.sl = fill_price - original_sl_dist
            else:
                self.sl = fill_price + original_sl_dist
            self.sl_initial = self.sl
        self.tp = signal.tp_indicative
        self.max_favorable_price = fill_price
        self.max_adverse_price = fill_price
        self.current_price = fill_price

    def summary(self) -> str:
        r = self.result_r if not self.is_open else self.current_r
        state = "OPEN" if self.is_open else f"CLOSED({self.exit_reason})"
        return (f"[{self.position_id}] {self.instrument} {self.side.value} "
                f"{state} {r:+.2f}R MFE={self.mfe_r:.2f}R MAE={self.mae_r:.2f}R "
                f"bars={self.bars_open}")


# ── Counterfactual ──────────────────────────────────────────────────────────────────

@dataclass
class Counterfactual:
    """Suivi de ce qui SERAIT arrivé si une décision avait été différente."""
    cf_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    signal_id: str = ""
    position_id: str = ""
    decision_type: DecisionType = DecisionType.SIGNAL_REJECTED
    instrument: str = ""
    side: Side = Side.LONG

    hypothetical_entry: float = 0.0
    hypothetical_sl: float = 0.0
    hypothetical_tp: float = 0.0

    ts_decision: datetime | None = None
    ts_last_update: datetime | None = None
    price_at_decision: float = 0.0
    mfe_after: float = 0.0
    mae_after: float = 0.0

    resolved: bool = False
    would_have_hit_sl: bool = False
    would_have_hit_tp: bool = False
    hypothetical_result_r: float = 0.0
    verdict: str = ""

    max_bars_to_track: int = 50
    bars_tracked: int = 0

    def update(self, high: float, low: float, close: float):
        if self.resolved:
            return
        self.bars_tracked += 1
        self.ts_last_update = datetime.now(timezone.utc)
        r_dist = abs(self.hypothetical_entry - self.hypothetical_sl)
        if r_dist == 0:
            return
        is_long = self.side == Side.LONG
        if is_long:
            self.mfe_after = max(self.mfe_after, high)
            self.mae_after = min(self.mae_after, low) if self.mae_after > 0 else low
        else:
            self.mfe_after = min(self.mfe_after, low) if self.mfe_after > 0 else low
            self.mae_after = max(self.mae_after, high)
        sl_hit = (low <= self.hypothetical_sl) if is_long else (high >= self.hypothetical_sl)
        tp_hit = (high >= self.hypothetical_tp) if is_long and self.hypothetical_tp > 0 else \
                 (low <= self.hypothetical_tp) if not is_long and self.hypothetical_tp > 0 else False
        if sl_hit and tp_hit:
            self._resolve(-1.0, "sl_hit (ambiguous)", "good_reject"
                          if self.decision_type == DecisionType.SIGNAL_REJECTED else "good_exit")
        elif sl_hit:
            self._resolve(-1.0, "sl_hit", "good_reject"
                          if self.decision_type == DecisionType.SIGNAL_REJECTED else "good_exit")
        elif tp_hit:
            if is_long:
                result_r = (self.hypothetical_tp - self.hypothetical_entry) / r_dist
            else:
                result_r = (self.hypothetical_entry - self.hypothetical_tp) / r_dist
            self._resolve(result_r, "tp_hit", "missed_gain"
                          if self.decision_type == DecisionType.SIGNAL_REJECTED else "premature_exit")
        elif self.bars_tracked >= self.max_bars_to_track:
            if is_long:
                result_r = (close - self.hypothetical_entry) / r_dist
            else:
                result_r = (self.hypothetical_entry - close) / r_dist
            self._resolve(result_r, "timeout",
                          "timeout_positive" if result_r > 0 else "timeout_negative")

    def _resolve(self, result_r: float, outcome: str, verdict: str):
        self.resolved = True
        self.hypothetical_result_r = round(result_r, 3)
        self.would_have_hit_sl = "sl" in outcome
        self.would_have_hit_tp = "tp" in outcome
        self.verdict = verdict
