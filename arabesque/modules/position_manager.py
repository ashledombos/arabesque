"""
Arabesque v2 — Position Manager.

v3.3 (2026-02-23) — Trend-only + BE optimisé.

PARCOURS COMPLET (4 replays, 2 périodes):
  v3.0 combined crypto  Oct→Jan: +73.9R  (seul MR rentable, mais fragile)
  v3.3 combined crypto  Oct→Jan: +33.5R  (BE 0.5/0.25)
  v3.3 combined crypto  Avr→Jul: -14.1R  (MR perd sur 2e période)
  v3.3 combined divers  Oct→Jan: -13.9R  (MR perd sur forex/commo)
  v3.3 TREND diversifié Avr→Jul: +21.2R  ← SEUL gagnant sur 2 périodes

DÉCISIONS DÉFINITIVES:
  1. TREND-ONLY (MR abandonné — perd sur toutes les catégories)
  2. BE 0.3R trigger / 0.20R offset (pas 0.15 — trop serré,
     323/339 trailing exits étaient des BE à +0.15R exact)
  3. Univers diversifié forex + commodités + crypto
     (forex le plus robuste, crypto questionnable)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from arabesque.core.models import (
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
    # ── ROI — QUASI-DÉSACTIVÉ (leçon v3.1/v3.2) ──────────────────
    # v3.1/v3.2 ont prouvé que le ROI court-terme (6/12/24h) DÉTRUIT
    # l'expectancy quand combiné avec un SL réel prop firm.
    #
    # BB_RPB_TSL peut se permettre des ROI courts car SL = -99%.
    # Avec SL = -1R : Exp = WR × avg_win - (1-WR) × 1.0
    #   WR=60% → avg_win minimum = 0.667R
    #   ROI court → avg_win = 0.55R → Exp NÉGATIVE
    #
    # On garde UNIQUEMENT un backstop très long pour les trades zombies.
    # Le mécanisme ROI intrabar (abaisse TP dynamiquement) reste codé
    # mais ne se déclenche quasi jamais avec ces tiers.
    roi_enabled: bool = True
    roi_table: list[RoiTier] = field(default_factory=lambda: [
        RoiTier(bars=0,   min_profit_r=3.0),   # Move exceptionnel (rare)
        RoiTier(bars=240, min_profit_r=0.15),   # Backstop 10 jours
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

    # ── Break-even (le levier principal du WR) ─────────────────────
    # MODÈLE ANALYTIQUE (testé sur 4 replays, 2 périodes):
    #   Trigger 0.3R: ~75% des trades l'atteignent → WR ~75%
    #   Offset 0.15R: trop serré — 323/339 trailing exits sont des BE
    #     exits à exactement +0.15R avec MFE moyen 0.68R (170R perdu)
    #   Offset 0.20R: chaque BE exit rapporte +0.05R de plus
    #     = +16R net sur 570 trades. Le risque additionnel (trades qui
    #     auraient survécu à 0.15R mais pas 0.20R) est minime (0.05R).
    be_trigger_r: float = 0.3
    be_offset_r: float = 0.20

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

    # ── EMA 200 exit (BB_RPB_TSL: sortie agressive sous EMA 200) ──
    # Quand le prix est du mauvais côté de l'EMA 200 (LONG sous,
    # SHORT au-dessus), les conditions de sortie sont resserrées :
    # giveback sans exiger momentum faible (RSI/CMF bypassed).
    ema200_exit_enabled: bool = False

    # ── RSI extreme exit (BB_RPB_TSL: sortie sur surextension) ──
    # Si RSI > seuil et le trade est en profit, prendre le profit
    # immédiatement. Capture les spikes sans attendre le trailing.
    rsi_extreme_exit_enabled: bool = False
    rsi_extreme_threshold: float = 80.0    # RSI > 80 pour LONG
    rsi_extreme_min_profit_r: float = 0.3  # Profit minimum pour sortir

    # ── Regime invalidation (M3) ──────────────────────────────────────
    # Sortie quand le régime HTF change en défaveur du trade :
    # LONG + regime → bear_trend, ou SHORT + regime → bull_trend.
    # Conditions : trade en profit (> min_profit_r) → exit immédiate.
    # Trade en perte → giveback bypasse le check momentum.
    regime_invalidation_enabled: bool = False
    regime_invalidation_min_profit_r: float = 0.0  # Sortir même à 0R

    # ── Trailing continu (M1 — alternative aux paliers) ──────────────
    # Au lieu de paliers discrets (1.5R/2R/3R), le trailing monte
    # continuellement : trail_distance = max(floor, MFE × ratio).
    # Active dès que MFE atteint le trigger.
    trailing_continuous: bool = False
    trailing_continuous_ratio: float = 0.5    # SL = MFE × (1 - ratio)
    trailing_continuous_floor_r: float = 0.3  # Distance min du SL
    trailing_continuous_trigger_r: float = 0.3  # MFE min pour activer

    # ── Trailing Dow Theory (H6) ─────────────────────────────────────
    # Trail SL vers le dernier swing low confirmé (LONG) ou swing high
    # (SHORT). Le SL ne monte que si le nouveau swing est plus haut.
    # Nécessite 'last_swing_low'/'last_swing_high' dans indicators.
    trailing_dow: bool = False
    trailing_dow_trigger_r: float = 0.3  # MFE min pour commencer
    trailing_dow_offset_r: float = 0.1   # Marge sous le swing


# ── Position Manager ────────────────────────────────────────────────

class PositionManager:
    """Gère le cycle de vie des positions.

    Utilisé par :
    - Le webhook live (une bougie à la fois)
    - Le backtest runner (itération sur OHLC historique)
    Même code, zéro divergence.

    NOTE: BE et trailing valident que le SL est faisable au prix
    de clôture (close) avant de le poser. Aligné sur le live
    qui vérifie SL <= bid (LONG) / SL >= ask (SHORT) avant
    d'envoyer l'amend au broker.
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
                # Champs pour évaluation post-hoc des shadow filters
                "wr_14": getattr(signal, "wr_14", 0),
                "rsi_div": getattr(signal, "rsi_div", 0),
                "ema200_ltf": getattr(signal, "ema200_ltf", 0),
                "htf_adx_raw": getattr(signal, "htf_adx", 0),
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
            regime = indicators.get("regime", "")
            if regime:
                pos.current_regime = regime

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
        trail_decision = self._update_trailing(pos, close, indicators)
        if trail_decision:
            decisions.append(trail_decision)

        # ── 4. RSI extreme exit (surextension) ──
        if self.cfg.rsi_extreme_exit_enabled:
            rsi_exit = self._check_rsi_extreme(pos, close)
            if rsi_exit:
                return decisions + [rsi_exit]

        # ── 4b. Regime invalidation ──
        if self.cfg.regime_invalidation_enabled:
            regime_exit = self._check_regime_invalidation(pos, close)
            if regime_exit:
                return decisions + [regime_exit]

        # ── 5. Giveback ──
        if self.cfg.giveback_enabled:
            gb = self._check_giveback(pos, close)
            if gb:
                return decisions + [gb]

        # ── 7. Deadfish ──
        if self.cfg.deadfish_enabled:
            df = self._check_deadfish(pos, close)
            if df:
                return decisions + [df]

        # ── 8. Time-stop (backstop final) ──
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
        from arabesque.core.models import Side, DecisionType

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
            # Validation faisabilité : SL doit être <= prix courant (LONG)
            # Aligné sur le live (cTrader exige SL <= bid pour BUY)
            if be_level > current_price:
                return None
            pos.sl = be_level
        else:
            be_level = pos.entry - self.cfg.be_offset_r * pos.R
            if be_level >= pos.sl:
                return None
            # Validation faisabilité : SL doit être >= prix courant (SHORT)
            if be_level < current_price:
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

    def _update_trailing(self, pos: Position, current_price: float,
                         indicators: dict | None = None) -> Decision | None:
        if self.cfg.trailing_dow:
            return self._update_trailing_dow(pos, current_price, indicators or {})
        if self.cfg.trailing_continuous:
            return self._update_trailing_continuous(pos, current_price)

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
            # Validation faisabilité : SL ne peut pas être > prix courant
            if new_sl > current_price:
                return None
            old_sl = pos.sl
            pos.sl = new_sl
        else:
            new_sl = pos.max_favorable_price + best_tier.trail_distance_r * pos.R
            if new_sl >= pos.sl:
                return None
            # Validation faisabilité : SL ne peut pas être < prix courant
            if new_sl < current_price:
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

    def _update_trailing_continuous(self, pos: Position, current_price: float) -> Decision | None:
        """Trailing continu : SL suit le MFE avec un ratio fixe."""
        if pos.mfe_r < self.cfg.trailing_continuous_trigger_r:
            return None

        # trail_distance = max(floor, MFE × ratio)
        trail_dist_r = max(
            self.cfg.trailing_continuous_floor_r,
            pos.mfe_r * self.cfg.trailing_continuous_ratio,
        )

        if pos.side == Side.LONG:
            new_sl = pos.max_favorable_price - trail_dist_r * pos.R
            if new_sl <= pos.sl:
                return None
            if new_sl > current_price:
                return None
            old_sl = pos.sl
            pos.sl = new_sl
        else:
            new_sl = pos.max_favorable_price + trail_dist_r * pos.R
            if new_sl >= pos.sl:
                return None
            if new_sl < current_price:
                return None
            old_sl = pos.sl
            pos.sl = new_sl

        was_active = pos.trailing_active
        pos.trailing_active = True

        dtype = DecisionType.TRAILING_ACTIVATED if not was_active else DecisionType.TRAILING_TIGHTENED
        return Decision(
            decision_type=dtype,
            position_id=pos.position_id,
            signal_id=pos.signal_id,
            instrument=pos.instrument,
            reason=f"Trailing continu: MFE={pos.mfe_r:.2f}R, dist={trail_dist_r:.2f}R",
            price_at_decision=current_price,
            value_before=old_sl,
            value_after=new_sl,
            metadata={"mode": "continuous", "mfe_r": round(pos.mfe_r, 3), "trail_dist_r": round(trail_dist_r, 3)},
        )

    def _update_trailing_dow(self, pos: Position, current_price: float,
                             indicators: dict) -> Decision | None:
        """Trailing Dow Theory : SL suit le dernier swing confirmé."""
        if pos.mfe_r < self.cfg.trailing_dow_trigger_r:
            return None

        is_long = pos.side == Side.LONG
        if is_long:
            swing = indicators.get("last_swing_low", 0)
            if not swing or swing <= 0:
                return None
            # SL = swing low - offset (marge de sécurité)
            new_sl = swing - self.cfg.trailing_dow_offset_r * pos.R
            if new_sl <= pos.sl:
                return None
            if new_sl > current_price:
                return None
            old_sl = pos.sl
            pos.sl = new_sl
        else:
            swing = indicators.get("last_swing_high", 0)
            if not swing or swing <= 0:
                return None
            new_sl = swing + self.cfg.trailing_dow_offset_r * pos.R
            if new_sl >= pos.sl:
                return None
            if new_sl < current_price:
                return None
            old_sl = pos.sl
            pos.sl = new_sl

        was_active = pos.trailing_active
        pos.trailing_active = True

        dtype = DecisionType.TRAILING_ACTIVATED if not was_active else DecisionType.TRAILING_TIGHTENED
        return Decision(
            decision_type=dtype,
            position_id=pos.position_id,
            signal_id=pos.signal_id,
            instrument=pos.instrument,
            reason=f"Trailing Dow: swing={swing:.5f}, MFE={pos.mfe_r:.2f}R",
            price_at_decision=current_price,
            value_before=old_sl,
            value_after=pos.sl,
            metadata={"mode": "dow", "swing": swing, "mfe_r": round(pos.mfe_r, 3)},
        )

    # ── Regime invalidation ────────────────────────────────────────────

    def _check_regime_invalidation(self, pos: Position, current_price: float) -> Decision | None:
        """Sortie quand le régime HTF change en défaveur du trade.

        LONG + bear_trend → le marché a retourné, sortir.
        SHORT + bull_trend → le marché a retourné, sortir.
        Condition : current_r >= min_profit_r (par défaut 0.0 = sortir même à breakeven).
        """
        if not pos.current_regime:
            return None

        is_long = pos.side == Side.LONG
        invalidated = (is_long and pos.current_regime == "bear_trend") or \
                      (not is_long and pos.current_regime == "bull_trend")

        if not invalidated:
            return None

        if pos.current_r < self.cfg.regime_invalidation_min_profit_r:
            return None

        return self._close_position(
            pos, current_price, DecisionType.EXIT_GIVEBACK,
            f"Regime invalidation: {pos.current_regime}, "
            f"profit={pos.current_r:.2f}R, side={pos.side.value}")

    # ── RSI extreme exit ──────────────────────────────────────────────

    def _check_rsi_extreme(self, pos: Position, current_price: float) -> Decision | None:
        """Sortie sur surextension RSI avec profit latent.

        BB_RPB_TSL sort quand RSI > 80 + profit décent. L'idée est de
        capturer les spikes rapides sans attendre le trailing.
        Pour LONG : RSI > seuil = suracheté, prendre le profit.
        Pour SHORT : RSI < (100 - seuil) = survendu, prendre le profit.
        """
        if pos.current_r < self.cfg.rsi_extreme_min_profit_r:
            return None

        is_long = pos.side == Side.LONG
        if is_long:
            if pos.current_rsi < self.cfg.rsi_extreme_threshold:
                return None
        else:
            # SHORT : RSI survendu = sous (100 - threshold)
            if pos.current_rsi > (100 - self.cfg.rsi_extreme_threshold):
                return None

        return self._close_position(
            pos, current_price, DecisionType.EXIT_GIVEBACK,
            f"RSI extreme: RSI={pos.current_rsi:.0f}, profit={pos.current_r:.2f}R")

    # ── Giveback ─────────────────────────────────────────────────────

    def _check_giveback(self, pos: Position, current_price: float) -> Decision | None:
        if pos.mfe_r < self.cfg.giveback_mfe_min_r:
            return None
        if pos.current_r > self.cfg.giveback_current_max_r:
            return None

        # EMA 200 override: si prix du mauvais côté, bypass momentum check
        ema200_override = False
        if self.cfg.ema200_exit_enabled and pos.current_ema200 > 0:
            is_long = pos.side == Side.LONG
            wrong_side = (current_price < pos.current_ema200) if is_long \
                    else (current_price > pos.current_ema200)
            if wrong_side:
                ema200_override = True

        if not ema200_override:
            # Condition momentum (BB_RPB_TSL : RSI < 46 + CMF < 0)
            momentum_weak = (pos.current_rsi < self.cfg.giveback_rsi_threshold and
                             pos.current_cmf < self.cfg.giveback_cmf_threshold)
            if not momentum_weak:
                return None

        reason = (f"Giveback: MFE={pos.mfe_r:.2f}R, cur={pos.current_r:.2f}R, "
                  f"RSI={pos.current_rsi:.0f}, CMF={pos.current_cmf:.3f}")
        if ema200_override:
            reason += f", EMA200 override (price {'<' if pos.side == Side.LONG else '>'} EMA200)"

        return self._close_position(
            pos, current_price, DecisionType.EXIT_GIVEBACK, reason)

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
