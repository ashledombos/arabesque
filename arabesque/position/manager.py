"""
Arabesque v2 — Position Manager.

REFONTE v3.1 (2026-02-21) — Post-diagnostic replay v3.0 :

v3.0 avait ajouté le ROI dégressif mais les tiers étaient trop longs
(48/120/240h) pour des trades de durée médiane 3h → quasi inutile (2.3%).

v3.1 corrige en se basant sur les données réelles du replay (786 trades) :

MODIFIÉ : ROI tiers courts (6/12/24/48/120h)
  Adapté à la distribution réelle. WR naturel à 12h = 72%.

MODIFIÉ : BE abaissé de +1.0R → +0.5R
  39% des SL-losers avaient MFE ≥ 0.5R — ce BE les protège.

MODIFIÉ : Giveback MFE abaissé de 1.0R → 0.5R
  Capture les trades qui érodent leurs profits.

CONSERVÉ de v3.0 : trailing 3 paliers (>= 1.5R), time-stop 336h, EXIT_ROI.

Aussi modifié dans d'autres fichiers :
  indicators.py : BB sur typical_price (H+L+C)/3 (aligné BB_RPB_TSL)
  signal_gen.py : RSI 35→30, min_bb_width 0.003→0.02, SL 1.5→2.0 ATR
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
class RoiTier:
    """Time-based profit-taking tier (inspired by BB_RPB_TSL minimal_roi).

    After `bars` candles, close the trade if current profit >= `min_profit_r`.
    This is the key mechanism behind BB_RPB_TSL's 90.8% win rate:
    as time passes, the required profit to close decreases, capturing
    small gains frequently instead of holding for large moves.
    """
    bars: int
    min_profit_r: float


# Sub-types avec TP fixe validé sur backtest multi-instruments (OOS réel)
# Critère de validation : N >= 100, Total R positif, TP hits > 30
# trend_strong : 449 trades, WR=65%, +51.7R, 70 TP hits — OK
TP_FIXED_SUBTYPES: dict[str, float] = {
    "trend_strong": 1.5,
}


@dataclass
class ManagerConfig:
    # ── ROI dégressif INTRABAR (v3.2 — le fix critique) ─────────────
    # v3.0 : tiers à 48/120/240 barres, check sur close → 2.3% des trades
    # v3.1 : tiers courts (6/12/24/48), check sur close → insuffisant
    #
    # v3.2 : CHANGEMENT FONDAMENTAL — le ROI abaisse dynamiquement le TP
    #         et est vérifié INTRABAR (high/low), pas sur close.
    #
    #         BB_RPB_TSL minimal_roi est vérifié intrabar dans Freqtrade.
    #         Sans ça, un trade qui touche +0.5R intrabar (high) mais
    #         clôture à -0.2R ne sera jamais capturé par le ROI.
    #
    #         156 trades dans le dernier replay avaient MFE > 0.15R
    #         mais sont sortis en SL. Ce fix les transforme en gagnants.
    #
    # Mécanisme : avant chaque check SL/TP, on recalcule pos.tp
    # comme min(tp_original, roi_level). Ensuite le SL/TP intrabar
    # standard fait le reste (y compris la règle conservatrice).
    roi_enabled: bool = True
    roi_table: list[RoiTier] = field(default_factory=lambda: [
        RoiTier(bars=0,   min_profit_r=3.0),   # Move exceptionnel immédiat
        RoiTier(bars=6,   min_profit_r=1.0),    # Bon profit en 6h (WR naturel 60%)
        RoiTier(bars=12,  min_profit_r=0.5),    # Profit modéré en 12h (WR naturel 72%)
        RoiTier(bars=24,  min_profit_r=0.3),    # Petit profit en 1 jour
        RoiTier(bars=48,  min_profit_r=0.15),   # Micro-profit en 2 jours
        RoiTier(bars=120, min_profit_r=0.05),   # Quasi-breakeven en 5 jours
    ])

    # ── Trailing (uniquement pour les "bonus trades" > 1.5R) ────────
    trailing_tiers: list[TrailingTier] = field(default_factory=lambda: [
        TrailingTier(mfe_threshold_r=3.0, trail_distance_r=1.5),
        TrailingTier(mfe_threshold_r=2.0, trail_distance_r=1.0),
        TrailingTier(mfe_threshold_r=1.5, trail_distance_r=0.7),
    ])

    # TP fixe par sub-type
    tp_r_by_subtype: dict[str, float] = field(
        default_factory=lambda: dict(TP_FIXED_SUBTYPES)
    )

    # ── Break-even (abaissé à +0.5R — données montrent que c'est critique) ──
    # v3.0 avait relevé à +1.0R "pour laisser respirer", mais les données
    # montrent que 39% des losers atteignent MFE ≥ 0.5R avant de revenir
    # au SL. Protéger ces trades est le levier #1 pour le WR.
    # Note : BE n'est pas un close — il déplace le SL à l'entry.
    # Le trade reste ouvert et peut encore atteindre TP/ROI.
    be_trigger_r: float = 0.5
    be_offset_r: float = 0.05

    # ── Giveback (seuils abaissés) ──────────────────────────────────
    # v3.0 : MFE ≥ 1.0R → trop haut, rate les profits qui s'érodent
    # v3.1 : MFE ≥ 0.5R → capture les trades qui ont fait +0.5R
    #        puis reculent sous +0.15R avec momentum faible
    giveback_enabled: bool = True
    giveback_mfe_min_r: float = 0.5
    giveback_current_max_r: float = 0.15
    giveback_rsi_threshold: float = 46.0
    giveback_cmf_threshold: float = 0.0

    # Deadfish
    deadfish_enabled: bool = True
    deadfish_max_bars: int = 24
    deadfish_mfe_min_r: float = 0.5
    deadfish_current_max_r: float = 0.0
    deadfish_bb_width_threshold: float = 0.005

    # ── Time-stop (backstop) ──────────────────────────────────────
    time_stop_enabled: bool = True
    time_stop_bars: int = 336
    time_stop_min_profit_r: float = 0.0


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

        # Appliquer TP fixe selon sub_type si configuré
        sub_type = getattr(signal, "sub_type", "") or ""
        tp_r = self.cfg.tp_r_by_subtype.get(sub_type)
        if tp_r is not None and pos.R > 0:
            if pos.side == Side.LONG:
                pos.tp = pos.entry + tp_r * pos.R
            else:
                pos.tp = pos.entry - tp_r * pos.R
            pos.signal_data["tp_fixed_r"] = tp_r

        # Sauvegarder le TP original APRÈS toutes les modifications
        # (sera utilisé pour distinguer EXIT_TP vs EXIT_ROI)
        pos.tp_original = pos.tp

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

        # ── 0. ROI abaisse dynamiquement le TP (AVANT SL/TP) ──
        #    Le ROI ne génère pas un exit séparé : il ABAISSE pos.tp
        #    puis le mécanisme SL/TP intrabar standard fait le reste.
        #    Cela permet de capturer les profits intrabar (high/low)
        #    et non pas seulement sur le close.
        roi_lowered = False
        if self.cfg.roi_enabled and pos.R > 0:
            roi_tp = self._get_roi_tp(pos)
            if roi_tp is not None:
                is_long = pos.side == Side.LONG
                # Pour LONG : TP plus bas = plus facile à atteindre
                # Pour SHORT : TP plus haut = plus facile à atteindre
                if is_long and (pos.tp == 0 or roi_tp < pos.tp):
                    pos.tp = roi_tp
                    roi_lowered = True
                elif not is_long and (pos.tp == 0 or roi_tp > pos.tp):
                    pos.tp = roi_tp
                    roi_lowered = True

        # ── 1. SL/TP intrabar (AVANT trailing, sinon on rate des SL) ──
        exit_decision = self._check_sl_tp_intrabar(pos, high, low)
        if exit_decision:
            # Si le TP a été abaissé par ROI et c'est un EXIT_TP → relabelliser
            if (roi_lowered
                    and exit_decision.decision_type == DecisionType.EXIT_TP):
                exit_decision.decision_type = DecisionType.EXIT_ROI
                exit_decision.reason = (
                    f"ROI intrabar: {pos.current_r:.2f}R "
                    f"(bars={pos.bars_open}, tp_roi={pos.tp:.5f})")
                pos.exit_reason = DecisionType.EXIT_ROI.value
            return [exit_decision]

        # ── 2. Break-even ──
        be_decision = self._update_breakeven(pos, close)
        if be_decision:
            decisions.append(be_decision)

        # ── 3. Trailing paliers (bonus trades uniquement) ──
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

        # ── 6. Time-stop (backstop final) ──
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
            if pos.trailing_active or pos.breakeven_set:
                dtype = DecisionType.EXIT_TRAILING
                reason = (f"Trailing SL hit (ambiguous bar) @ {pos.sl:.5f} "
                         f"(tier {pos.trailing_tier})")
            else:
                dtype = DecisionType.EXIT_SL
                reason = "SL hit (ambiguous bar: SL and TP both touched, conservative=SL)"
            return self._close_position(pos, pos.sl, dtype, reason)

        if sl_hit:
            if pos.trailing_active or pos.breakeven_set:
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

    # ── ROI dynamique — calcul du TP abaissé ───────────────────────

    def _get_roi_tp(self, pos: Position) -> float | None:
        """Calcule le niveau TP correspondant au tier ROI applicable.

        Retourne le prix TP (pas le R), ou None si aucun tier applicable.
        Le TP ROI est toujours plus facile à atteindre (plus proche de entry)
        que le TP original. L'appelant décide s'il abaisse pos.tp.
        """
        if not self.cfg.roi_table or pos.R == 0:
            return None

        # Trouver le tier applicable (le dernier dont bars <= pos.bars_open)
        applicable_tier = None
        for tier in sorted(self.cfg.roi_table, key=lambda t: t.bars):
            if pos.bars_open >= tier.bars:
                applicable_tier = tier

        if applicable_tier is None:
            return None

        # Calculer le prix TP correspondant
        if pos.side == Side.LONG:
            return pos.entry + applicable_tier.min_profit_r * pos.R
        else:
            return pos.entry - applicable_tier.min_profit_r * pos.R

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
        """Backstop final : ferme le trade après time_stop_bars barres.

        Avec le ROI dégressif, la plupart des trades profitables sont
        capturés bien avant. Le time_stop est un filet de sécurité pour
        les trades bloqués (profitable ou non).
        """
        if pos.bars_open < self.cfg.time_stop_bars:
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
