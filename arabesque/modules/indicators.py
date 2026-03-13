"""
Arabesque — Indicateurs techniques partagés.

Toutes les fonctions sont pures (DataFrame in → Series out), sans side-effects.
Utilisées par BacktestSignalGenerator, TrendSignalGenerator, CombinedSignalGenerator.

Conventions :
- Colonnes OHLCV : « High », « Low », « Close », « Volume » (avec majuscule — format pandas standard)
- Toutes les EWM utilisent le lissage de Wilder : alpha=1/period (PAS span=period)
  Wilder's smoothing : alpha=1/N  ≠  EWMA standard : alpha=2/(N+1)
  L'ADX et le RSI de Wilder utilisent alpha=1/N — c'est la formule de référence.
- ATR : rolling mean (standard, non-Wilder)

Fonctions disponibles :
    compute_rsi(close, period)          → Series
    compute_atr(df, period)             → Series
    compute_adx(df, period)             → Series
    compute_bollinger(df, period, std)  → (mid, lower, upper, width) DataFrames en dict
    compute_cmf(df, period)             → Series
    compute_williams_r(df, period)      → Series
    compute_ema(series, span)           → Series
    compute_htf_regime(df, ema_fast, ema_slow, adx_period, adx_strong) → df enrichi
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# RSI — Wilder's smoothing (alpha=1/period)
# ─────────────────────────────────────────────────────────────────────────────

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI avec lissage de Wilder (alpha=1/period, min_periods=period).

    Version canonique : utilise where() pour la séparation gains/pertes
    (évite l'ambiguïté de clip() avec NaN), et min_periods pour les
    premières valeurs insuffisantes.
    """
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


# ─────────────────────────────────────────────────────────────────────────────
# ATR — True Range, rolling mean (Wilder simple)
# ─────────────────────────────────────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range sur rolling mean.

    True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
    """
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


# ─────────────────────────────────────────────────────────────────────────────
# ADX — Wilder's smoothing (alpha=1/period)
# ─────────────────────────────────────────────────────────────────────────────

def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX avec lissage de Wilder (alpha=1/period).

    Implémentation correcte de l'ADX de Wilder :
      1. DM+ / DM- : conditionnellement nuls si l'autre direction est plus forte
      2. Lissage EWM avec alpha=1/period (PAS span=period)
      3. DX → ADX via un second lissage Wilder

    Note : ewm(alpha=1/14) ≠ ewm(span=14).
      alpha=1/14 ≈ 0.0714  →  décroissance plus lente (plus de mémoire)
      span=14 → alpha=2/15 ≈ 0.133  →  décroissance plus rapide
    La formule de Wilder utilise alpha=1/N.
    """
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()

    # DM+ non nul seulement si le move haussier est plus fort
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    # DM- non nul seulement si le move baissier est plus fort
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    alpha = 1.0 / period
    atr_smooth = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_smooth
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_smooth

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    return adx


# ─────────────────────────────────────────────────────────────────────────────
# Bollinger Bands
# ─────────────────────────────────────────────────────────────────────────────

def compute_bollinger(
    df: pd.DataFrame,
    period: int = 20,
    std_mult: float = 2.0,
) -> dict[str, pd.Series]:
    """Bandes de Bollinger.

    v3.1: source = typical_price (H+L+C)/3, aligné sur BB_RPB_TSL.
    Avant: source = Close seul (produisait des signaux de moindre qualité).
    BB_RPB_TSL utilise qtpylib.typical_price() qui est (H+L+C)/3.

    Retourne un dict : { "mid", "lower", "upper", "width" }
    bb_width = (upper - lower) / mid  (mesure de la volatilité normalisée)
    """
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    mid = tp.rolling(period, min_periods=period).mean()
    std = tp.rolling(period, min_periods=period).std()
    lower = mid - std_mult * std
    upper = mid + std_mult * std
    width = (upper - lower) / mid.replace(0, np.nan)
    return {"mid": mid, "lower": lower, "upper": upper, "width": width}


# ─────────────────────────────────────────────────────────────────────────────
# CMF — Chaikin Money Flow
# ─────────────────────────────────────────────────────────────────────────────

def compute_cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Chaikin Money Flow sur `period` barres.

    CMF = Σ(MFV) / Σ(Volume)
    MFV = ((Close - Low) - (High - Close)) / (High - Low) × Volume
    """
    hl_range = (df["High"] - df["Low"]).replace(0, np.nan)
    mfm = ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / hl_range
    vol = df.get("Volume", pd.Series(1.0, index=df.index))
    mfv = mfm * vol
    vol_sum = vol.rolling(period, min_periods=1).sum().replace(0, np.nan)
    return mfv.rolling(period, min_periods=1).sum() / vol_sum


# ─────────────────────────────────────────────────────────────────────────────
# Williams %R
# ─────────────────────────────────────────────────────────────────────────────

def compute_williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Williams %R.

    WR = (HH - Close) / (HH - LL) × -100
    Plage : -100 (oversold) à 0 (overbought)
    """
    hh = df["High"].rolling(period, min_periods=period).max()
    ll = df["Low"].rolling(period, min_periods=period).min()
    denom = (hh - ll).replace(0, np.nan)
    return ((hh - df["Close"]) / denom) * -100.0


# ─────────────────────────────────────────────────────────────────────────────
# EMA simple
# ─────────────────────────────────────────────────────────────────────────────

def compute_ema(series: pd.Series, span: int, adjust: bool = False) -> pd.Series:
    """EMA standard (EWMA, alpha=2/(span+1)).

    NOTE : utilise span (pas alpha) — c'est l'EMA usuelle des graphes,
    différent du lissage de Wilder. Pour les EMAs directionnelles (EMA200,
    fast/slow HTF), le span standard est correct.
    """
    return series.ewm(span=span, adjust=adjust).mean()


# ─────────────────────────────────────────────────────────────────────────────
# Régime HTF (4H) — resample + EMA + ADX
# ─────────────────────────────────────────────────────────────────────────────

def compute_htf_regime(
    df: pd.DataFrame,
    ema_fast: int = 50,
    ema_slow: int = 200,
    adx_period: int = 14,
    adx_strong: float = 25.0,
) -> pd.DataFrame:
    """Calcule le régime directionnel sur timeframe 4H et l'injecte dans df (1H).

    Colonnes ajoutées au df d'entrée :
        regime          : "bull_trend" | "bear_trend" | "bull_range" | "bear_range"
        htf_ema_fast    : EMA fast sur 4H (forward-filled sur 1H)
        htf_ema_slow    : EMA slow sur 4H (forward-filled sur 1H)
        htf_adx         : ADX sur 4H (forward-filled sur 1H)

    Anti-lookahead : le resample 4H utilise les données disponibles à chaque
    bougie — forward-fill des valeurs 4H vers 1H.

    Fallback : si pas assez de données 4H (< ema_slow + 10 barres),
    toutes les colonnes sont mises à des valeurs neutres.
    """
    df_4h = df.resample("4h").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last"
    }).dropna()

    if len(df_4h) < ema_slow + 10:
        df["regime"] = "bull_range"
        df["htf_ema_fast"] = df["Close"]
        df["htf_ema_slow"] = df["Close"]
        df["htf_adx"] = 0.0
        return df

    df_4h["ema_fast"] = compute_ema(df_4h["Close"], ema_fast)
    df_4h["ema_slow"] = compute_ema(df_4h["Close"], ema_slow)
    df_4h["adx"] = compute_adx(df_4h, adx_period)

    def _classify(row) -> str:
        bull = row["ema_fast"] > row["ema_slow"]
        strong = (row["adx"] > adx_strong) if not pd.isna(row["adx"]) else False
        if bull and strong:
            return "bull_trend"
        elif not bull and strong:
            return "bear_trend"
        elif bull:
            return "bull_range"
        else:
            return "bear_range"

    df_4h["regime"] = df_4h.apply(_classify, axis=1)

    # Sélectionner les colonnes HTF et forward-fill vers 1H
    htf = df_4h[["regime", "ema_fast", "ema_slow", "adx"]].copy()
    htf.columns = ["regime", "htf_ema_fast", "htf_ema_slow", "htf_adx"]
    htf_ri = htf.reindex(df.index, method="ffill")

    df["regime"] = htf_ri["regime"].fillna("bull_range")
    df["htf_ema_fast"] = htf_ri["htf_ema_fast"].fillna(df["Close"])
    df["htf_ema_slow"] = htf_ri["htf_ema_slow"].fillna(df["Close"])
    df["htf_adx"] = htf_ri["htf_adx"].fillna(0.0)

    return df
