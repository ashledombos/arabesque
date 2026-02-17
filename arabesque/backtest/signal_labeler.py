"""
Arabesque v2 — Signal Labeler (Phase 1.3).

Placement : arabesque/backtest/signal_labeler.py

Classifie les signaux en sous-types et attache des facteurs de qualité.
Appelé APRÈS la création du signal, avant le return.

Sub-types Mean-Reversion :
  - mr_deep_wide     : RSI extrême + BB large → meilleur setup théorique
  - mr_deep_narrow   : RSI extrême + BB étroite → signal fort, R/R limité
  - mr_shallow_wide  : RSI modéré + BB large → room OK, signal faible
  - mr_shallow_narrow: RSI modéré + BB étroite → pire setup

Sub-types Trend :
  - trend_strong     : ADX > 30, breakout net
  - trend_moderate   : ADX 20-30, breakout naissant

Label factors (continus, pour analyse granulaire) :
  - rsi_depth        : abs(RSI - 50), plus c'est haut plus l'extrême est profond
  - bb_penetration   : distance price/band en fraction de BB width
  - bb_width_z       : bb_width vs sa propre moyenne glissante (z-score)
  - cmf_aligned      : True si CMF confirme la direction du signal
  - volume_ratio     : volume / avg_volume_20 (seulement si volume > 0)
  - rr_ratio         : R:R du signal
  - regime           : le régime de marché au moment du signal
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arabesque.models import Signal, Side

import pandas as pd


# ── Seuils de classification ────────────────────────────────────────

# RSI thresholds pour deep vs shallow
RSI_DEEP_LONG = 25.0       # RSI < 25 = deep oversold
RSI_DEEP_SHORT = 75.0      # RSI > 75 = deep overbought

# BB width threshold pour wide vs narrow
# Utilise un z-score : >0 = au-dessus de la moyenne = "wide"
BB_WIDTH_Z_THRESHOLD = 0.0

# ADX thresholds pour trend
ADX_STRONG = 30.0


# ── Fonctions de labeling ────────────────────────────────────────────

def label_mr_signal(
    signal: "Signal",
    df: pd.DataFrame,
    bar_idx: int,
    bb_width_lookback: int = 100,
) -> "Signal":
    """Label un signal mean-reversion.

    Ajoute sub_type et label_factors au signal.
    Doit être appelé APRÈS la création du signal, avant le return.

    Args:
        signal: Le signal créé
        df: DataFrame avec indicateurs
        bar_idx: Index de la barre du signal
        bb_width_lookback: Fenêtre pour le z-score BB width
    """
    row = df.iloc[bar_idx]
    rsi = signal.rsi
    bb_width = signal.bb_width
    close = signal.tv_close

    # ── RSI depth ──
    if signal.side.value == "LONG":
        is_deep = rsi < RSI_DEEP_LONG
        rsi_depth = max(0, 50 - rsi)  # 50 = neutral
    else:
        is_deep = rsi > RSI_DEEP_SHORT
        rsi_depth = max(0, rsi - 50)

    # ── BB width z-score ──
    if "bb_width" in df.columns:
        start = max(0, bar_idx - bb_width_lookback)
        bb_hist = df["bb_width"].iloc[start:bar_idx + 1]
        if len(bb_hist) > 10:
            bb_mean = bb_hist.mean()
            bb_std = bb_hist.std()
            bb_width_z = (bb_width - bb_mean) / bb_std if bb_std > 0 else 0.0
        else:
            bb_width_z = 0.0
    else:
        bb_width_z = 0.0

    is_wide = bb_width_z > BB_WIDTH_Z_THRESHOLD

    # ── BB penetration ──
    bb_range = signal.bb_upper - signal.bb_lower
    if bb_range > 0:
        if signal.side.value == "LONG":
            bb_penetration = (signal.bb_lower - close) / bb_range
        else:
            bb_penetration = (close - signal.bb_upper) / bb_range
    else:
        bb_penetration = 0.0

    # ── CMF alignment ──
    cmf = signal.cmf
    if signal.side.value == "LONG":
        cmf_aligned = cmf > 0  # Money flowing IN while price oversold
    else:
        cmf_aligned = cmf < 0  # Money flowing OUT while price overbought

    # ── Volume ratio ──
    volume = row.get("Volume", 0)
    volume_ratio = 0.0
    if volume > 0 and "Volume" in df.columns:
        start = max(0, bar_idx - 20)
        avg_vol = df["Volume"].iloc[start:bar_idx].mean()
        if avg_vol > 0:
            volume_ratio = volume / avg_vol

    # ── Sub-type ──
    if is_deep and is_wide:
        sub_type = "mr_deep_wide"
    elif is_deep and not is_wide:
        sub_type = "mr_deep_narrow"
    elif not is_deep and is_wide:
        sub_type = "mr_shallow_wide"
    else:
        sub_type = "mr_shallow_narrow"

    # ── Assign ──
    signal.sub_type = sub_type
    signal.label_factors = {
        "rsi_depth": round(rsi_depth, 1),
        "bb_width_z": round(bb_width_z, 2),
        "bb_penetration": round(bb_penetration, 4),
        "cmf_aligned": cmf_aligned,
        "volume_ratio": round(volume_ratio, 2),
        "rr": signal.rr,
        "regime": signal.regime,
    }

    return signal


def label_trend_signal(
    signal: "Signal",
    df: pd.DataFrame,
    bar_idx: int,
) -> "Signal":
    """Label un signal trend.

    Args:
        signal: Le signal créé
        df: DataFrame avec indicateurs
        bar_idx: Index de la barre du signal
    """
    row = df.iloc[bar_idx]
    adx = row.get("adx", 0)

    # ── ADX strength ──
    is_strong = adx >= ADX_STRONG

    # ── Squeeze duration (combien de barres en squeeze avant le breakout) ──
    squeeze_duration = 0
    if "squeeze" in df.columns:
        for j in range(bar_idx - 1, max(0, bar_idx - 50), -1):
            if df.iloc[j].get("squeeze", False):
                squeeze_duration += 1
            else:
                break

    # ── Breakout strength (distance au-delà de la bande en fraction d'ATR) ──
    close = signal.tv_close
    atr = signal.atr
    if atr > 0:
        if signal.side.value == "LONG":
            breakout_strength = (close - signal.bb_upper) / atr
        else:
            breakout_strength = (signal.bb_lower - close) / atr
    else:
        breakout_strength = 0.0

    # ── CMF alignment ──
    cmf = signal.cmf
    if signal.side.value == "LONG":
        cmf_aligned = cmf > 0
    else:
        cmf_aligned = cmf < 0

    # ── Volume ratio ──
    volume = row.get("Volume", 0)
    volume_ratio = 0.0
    if volume > 0 and "Volume" in df.columns:
        start = max(0, bar_idx - 20)
        avg_vol = df["Volume"].iloc[start:bar_idx].mean()
        if avg_vol > 0:
            volume_ratio = volume / avg_vol

    # ── Sub-type ──
    sub_type = "trend_strong" if is_strong else "trend_moderate"

    signal.sub_type = sub_type
    signal.label_factors = {
        "adx": round(adx, 1),
        "squeeze_duration": squeeze_duration,
        "breakout_strength": round(breakout_strength, 2),
        "cmf_aligned": cmf_aligned,
        "volume_ratio": round(volume_ratio, 2),
        "rr": signal.rr,
        "regime": signal.regime,
    }

    return signal
