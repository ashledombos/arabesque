"""
Arabesque v2 — Position Manager.

CORRECTIONS CRITIQUES vs v1 :
1. update() accepte high/low/close (pas juste close)
2. SL/TP intrabar : si les deux touchés dans la même bougie → PIRE CAS
3. SL ne descend JAMAIS (LONG) / ne monte JAMAIS (SHORT)
4. Trailing calculé sur MFE (high/low), pas sur close
5. Même code appelé par le live ET le backtest (pas de divergence)

Paliers trailing en R (inspiré BB_RPB_TSL) :
  BB_RPB_TSL :  +3% → trail 1.5%,  +6% → 2%,  +10% → 3%,  +20% → 5%
  Arabesque  :  +0.5R → trail 0.3R, +1.0R → 0.5R, +1.5R → 0.8R, +2.0R → 1.2R
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from arabesque.models import (
    Position, Signal, Decision, Counterfactual,
    DecisionType, RejectReason, Side,
)


# ── Configuration ────────────────────────────────────────────────────

@dataclass
class TrailingTier:
    mfe_threshold_r: float
    trail_distance_r: float


@dataclass
class ManagerConfig:
    # Trailing paliers (du plus haut au plus bas)
    trailing_tiers: list[TrailingTier] = field(default_factory=lambda: [
        TrailingTier(mfe_threshold_r=3.0, trail_distance_r=1.5),
        TrailingTier(mfe_threshold_r=2.0, trail_distance_r=1.2),
        TrailingTier(mfe_threshold_r=1.5, trail_distance_r=0.8),
        TrailingTier(mfe_threshold_r=1.0, trail_distance_r=0.5),
        TrailingTier(mfe_threshold_r=0.5, trail_distance_r=0.3),
    ])

    # Break-even
    be_trigger_r: float = 0.5
    be_offset_r: float = 0.05     # buffer au-dessus de entry (couvre spread)

    # Giveback
    giveback_enabled: bool = True
    giveback_mfe_min_r: float = 1.0
    giveback_current_max_r: float = 0.2
    giveback_rsi_threshold: float = 46.0
    giveback_cmf_threshold: float = 0.0

    # Deadfish
    deadfish_enabled: bool = True
    deadfish_max_bars: int = 24
    deadfish_mfe_min_r: float = 0.5
    deadfish_current_max_r: float = 0.0
    deadfish_bb_width_threshold: float = 0.005

    # Time-stop
    time_stop_enabled: bool = True
    time_stop_bars: int = 48
    time_stop_min_profit_r: float = 0.3


# ── Position Manager ────────────────────────────────────────────────

class PositionManager:
    """Gère le cycle de vie des positions.

    Utilisé par :
    - Le webhook live (une bougie à la fois)
    - Le backtest runner (itération sur OHLC historique)
    Même code, zéro divergence.
    """

    def __init__(self, config: ManagerConfig | None = None):
        self.cfg = config or ManagerConfig()
        self.positions: list[Position] = []
        self.closed_positions: list[Position] = []
        self.counterfactuals: list[Counterfactual] = []
        # Trier du plus haut au plus bas pour le trailing
        self.cfg.trailing_tiers.sort(key=lambda t: t.mfe_threshold_r, reverse=True)

    @property
    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.is_open]

    def open_position(self, signal: Signal, fill_price: float,
                      risk_cash: float, volume: float) -> Position:
        """Crée et enregistre une position à partir d'un fill broker."""
        pos = Position(
            signal_id=signal.signal_id,
            instrument=signal.instrument,
            side=signal.side,
            ts_signal=signal.timestamp,
            ts_entry=datetime.now(timezone.utc),
            atr_at_entry=signal.atr,
            risk_cash=risk_cash,
            volume=volume,
            signal_data={
                "rsi": signal.rsi, "cmf": signal.cmf,
                "bb_width": signal.bb_width, "regime": signal.regime,
                "strategy_type": signal.strategy_type,
                "sub_type": getattr(signal, "sub_type", ""),
                "label_factors": getattr(signal, "label_factors", {}),
                "rr": signal.rr,
                "htf_adx": signal.htf_adx,
            },
        )
        # Recalculer SL/TP depuis le fill réel
        pos.recalculate_from_fill(fill_price, signal)
        self.positions.append(pos)
        return pos

    def update_position(
        self,
        pos: Position,
        high: float,
        low: float,
        close: float,
        indicators: dict | None = None,
    ) -> list[Decision]:
        """Met à jour une position avec les données OHLC d'une bougie.

        CRITIQUE : utilise high/low pour SL/TP intrabar, pas juste close.

        Args:
            pos: La position à mettre à jour
            high: High de la bougie
            low: Low de la bougie
            close: Close de la bougie
            indicators: {"rsi": x, "cmf": x, "bb_width": x, "ema200": x}

        Returns:
            Liste des décisions générées (SL move, exit, etc.)
        """
        if not pos.is_open:
            return []

        pos.bars_open += 1
        decisions: list[Decision] = []

        # Mettre à jour indicateurs contextuels
        if indicators:
            pos.current_rsi = indicators.get("rsi", pos.current_rsi)
            pos.current_cmf = indicators.get("cmf", pos.current_cmf)
            pos.current_bb_width = indicators.get("bb_width", pos.current_bb_width)
            pos.current_ema200 = indicators.get("ema200", pos.current_ema200)

        # Mettre à jour MFE/MAE avec high/low
        pos.update_price(high, low, close)

        # ── 1. SL/TP intrabar (AVANT trailing, sinon on rate des SL) ──
        exit_decision = self._check_sl_tp_intrabar(pos, high, low)
        if exit_decision:
            return [exit_decision]

        # ── 2. Break-even ──
        be_decision = self._update_breakeven(pos, close)
        if be_decision:
            decisions.append(be_decision)

        # ── 3. Trailing paliers ──
        trail_decision = self._update_trailing(pos, close)
        if trail_decision:
            decisions.append(trail_decision)

        # ── 4. Giveback ──
        if self.cfg.giveback_enabled:
            gb = self._check_giveback(pos, close)
            if gb:
                return decisions + [gb]

        # ── 5. Deadfish ──
        if self.cfg.deadfish_enabled:
            df = self._check_deadfish(pos, close)
            if df:
                return decisions + [df]

        # ── 6. Time-stop ──
        if self.cfg.time_stop_enabled:
            ts = self._check_time_stop(pos, close)
            if ts:
                return decisions + [ts]

        return decisions

    # ── SL/TP intrabar (CRITIQUE) ────────────────────────────────────

    def _check_sl_tp_intrabar(self, pos, high: float, low: float):
        """Vérifie SL/TP sur high/low avec règle conservatrice.
    
        Si SL ET TP touchés dans la même bougie → PRENDRE LE SL (pire cas).
        C'est la règle conservatrice standard pour éviter l'optimisme OHLC.
    
        NOUVEAU : Discrimine EXIT_TRAILING vs EXIT_SL selon que le SL
        a été remonté (trailing/breakeven actif) ou non.
        """
        from arabesque.models import Side, DecisionType
    
        is_long = pos.side == Side.LONG
    
        sl_hit = (low <= pos.sl) if is_long else (high >= pos.sl) if pos.sl > 0 else False
        tp_hit = (high >= pos.tp) if is_long and pos.tp > 0 else \
                 (low <= pos.tp) if not is_long and pos.tp > 0 else False
    
        if sl_hit and tp_hit:
            # Ambiguïté OHLC → pire cas = SL
            # Mais discriminer trailing vs SL original
            if pos.trailing_active or pos.breakeven_set:
                dtype = DecisionType.EXIT_TRAILING
                reason = (f"Trailing SL hit (ambiguous bar) @ {pos.sl:.5f} "
                         f"(tier {pos.trailing_tier})")
            else:
                dtype = DecisionType.EXIT_SL
                reason = "SL hit (ambiguous bar: SL and TP both touched, conservative=SL)"
            return self._close_position(pos, pos.sl, dtype, reason)
    
        if sl_hit:
            # Discriminer : SL original (loss) vs trailing SL (win)
            if pos.trailing_active or pos.breakeven_set:
                # Le SL a été remonté → c'est une sortie trailing
                # result_r sera positif si SL > entry (LONG) ou SL < entry (SHORT)
                dtype = DecisionType.EXIT_TRAILING
                reason = (f"Trailing SL hit @ {pos.sl:.5f} "
                         f"(tier {pos.trailing_tier}, MFE={pos.mfe_r:.2f}R)")
            else:
                dtype = DecisionType.EXIT_SL
                reason = f"SL hit @ {pos.sl:.5f}"
            return self._close_position(pos, pos.sl, dtype, reason)
    
        if tp_hit:
            return self._close_position(
                pos, pos.tp, DecisionType.EXIT_TP,
                f"TP hit @ {pos.tp:.5f}")
    
        return None

    # ── Break-even ───────────────────────────────────────────────────

    def _update_breakeven(self, pos: Position, current_price: float) -> Decision | None:
        if pos.breakeven_set:
            return None
        if pos.mfe_r < self.cfg.be_trigger_r:
            return None

        old_sl = pos.sl
        if pos.side == Side.LONG:
            be_level = pos.entry + self.cfg.be_offset_r * pos.R
            if be_level <= pos.sl:
                return None
            pos.sl = be_level
        else:
            be_level = pos.entry - self.cfg.be_offset_r * pos.R
            if be_level >= pos.sl:
                return None
            pos.sl = be_level

        pos.breakeven_set = True

        return Decision(
            decision_type=DecisionType.SL_BREAKEVEN,
            position_id=pos.position_id,
            signal_id=pos.signal_id,
            instrument=pos.instrument,
            reason=f"BE @ MFE={pos.mfe_r:.2f}R",
            price_at_decision=current_price,
            value_before=old_sl,
            value_after=pos.sl,
        )

    # ── Trailing paliers ─────────────────────────────────────────────

    def _update_trailing(self, pos: Position, current_price: float) -> Decision | None:
        best_tier = None
        for tier in self.cfg.trailing_tiers:
            if pos.mfe_r >= tier.mfe_threshold_r:
                best_tier = tier
                break

        if best_tier is None:
            return None

        tier_idx = self.cfg.trailing_tiers.index(best_tier) + 1

        # Calculer nouveau SL
        if pos.side == Side.LONG:
            new_sl = pos.max_favorable_price - best_tier.trail_distance_r * pos.R
            if new_sl <= pos.sl:
                return None
            old_sl = pos.sl
            pos.sl = new_sl
        else:
            new_sl = pos.max_favorable_price + best_tier.trail_distance_r * pos.R
            if new_sl >= pos.sl:
                return None
            old_sl = pos.sl
            pos.sl = new_sl

        old_tier = pos.trailing_tier
        pos.trailing_tier = tier_idx
        pos.trailing_active = True

        dtype = DecisionType.TRAILING_ACTIVATED if old_tier == 0 else DecisionType.TRAILING_TIGHTENED
        return Decision(
            decision_type=dtype,
            position_id=pos.position_id,
            signal_id=pos.signal_id,
            instrument=pos.instrument,
            reason=f"Trailing tier {tier_idx}: MFE={pos.mfe_r:.2f}R, dist={best_tier.trail_distance_r}R",
            price_at_decision=current_price,
            value_before=old_sl,
            value_after=new_sl,
            metadata={"tier": tier_idx, "mfe_r": round(pos.mfe_r, 3)},
        )

    # ── Giveback ─────────────────────────────────────────────────────

    def _check_giveback(self, pos: Position, current_price: float) -> Decision | None:
        if pos.mfe_r < self.cfg.giveback_mfe_min_r:
            return None
        if pos.current_r > self.cfg.giveback_current_max_r:
            return None

        # Condition momentum (BB_RPB_TSL : RSI < 46 + CMF < 0)
        momentum_weak = (pos.current_rsi < self.cfg.giveback_rsi_threshold and
                         pos.current_cmf < self.cfg.giveback_cmf_threshold)
        if not momentum_weak:
            return None

        return self._close_position(
            pos, current_price, DecisionType.EXIT_GIVEBACK,
            f"Giveback: MFE={pos.mfe_r:.2f}R, cur={pos.current_r:.2f}R, "
            f"RSI={pos.current_rsi:.0f}, CMF={pos.current_cmf:.3f}")

    # ── Deadfish ─────────────────────────────────────────────────────

    def _check_deadfish(self, pos: Position, current_price: float) -> Decision | None:
        if pos.bars_open < self.cfg.deadfish_max_bars:
            return None
        if pos.mfe_r >= self.cfg.deadfish_mfe_min_r:
            return None
        if pos.current_r > self.cfg.deadfish_current_max_r:
            return None

        tight_bb = pos.current_bb_width < self.cfg.deadfish_bb_width_threshold
        if not tight_bb:
            return None

        return self._close_position(
            pos, current_price, DecisionType.EXIT_DEADFISH,
            f"Deadfish: {pos.bars_open} bars, MFE={pos.mfe_r:.2f}R, "
            f"bb_w={pos.current_bb_width:.4f}")

    # ── Time-stop ────────────────────────────────────────────────────

    def _check_time_stop(self, pos: Position, current_price: float) -> Decision | None:
        if pos.bars_open < self.cfg.time_stop_bars:
            return None
        if pos.current_r >= self.cfg.time_stop_min_profit_r:
            return None

        return self._close_position(
            pos, current_price, DecisionType.EXIT_TIME_STOP,
            f"Time-stop: {pos.bars_open} bars, profit={pos.current_r:.2f}R")

    # ── Close position ───────────────────────────────────────────────

    def _close_position(
        self, pos: Position, exit_price: float,
        decision_type: DecisionType, reason: str,
    ) -> Decision:
        pos.exit_price = exit_price
        pos.exit_reason = decision_type.value
        pos.ts_exit = datetime.now(timezone.utc)
        pos.is_open = False
        self.closed_positions.append(pos)

        decision = Decision(
            decision_type=decision_type,
            position_id=pos.position_id,
            signal_id=pos.signal_id,
            instrument=pos.instrument,
            reason=reason,
            price_at_decision=exit_price,
            metadata={
                "result_r": pos.result_r,
                "mfe_r": round(pos.mfe_r, 3),
                "mae_r": round(pos.mae_r, 3),
                "bars_open": pos.bars_open,
                "trailing_tier": pos.trailing_tier,
            },
        )

        # Counterfactual pour sorties anticipées
        if decision_type in (DecisionType.EXIT_GIVEBACK, DecisionType.EXIT_DEADFISH):
            cf = Counterfactual(
                signal_id=pos.signal_id,
                position_id=pos.position_id,
                decision_type=decision_type,
                instrument=pos.instrument,
                side=pos.side,
                hypothetical_entry=pos.entry,
                hypothetical_sl=pos.sl_initial,
                hypothetical_tp=0,
                ts_decision=datetime.now(timezone.utc),
                price_at_decision=exit_price,
                mfe_after=exit_price,
                mae_after=exit_price,
            )
            self.counterfactuals.append(cf)

        return decision

    # ── Counterfactual updates ───────────────────────────────────────

    def update_counterfactuals(self, instrument: str, high: float, low: float, close: float):
        for cf in self.counterfactuals:
            if cf.instrument == instrument and not cf.resolved:
                cf.update(high, low, close)
