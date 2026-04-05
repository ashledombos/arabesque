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
    compute_vwap(df, session_reset)     → Series
    compute_vwap_bands(df, session_reset, std_mult) → dict {vwap, upper, lower, dist}
    compute_rsi_divergence(df, rsi, lookback)  → Series (-1 bearish, 0 none, +1 bullish)
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

def compute_donchian(df: pd.DataFrame, period: int = 20, shift: int = 1):
    """Donchian channels (highest high / lowest low over N bars).

    Returns (upper, lower). shift=1 avoids lookahead (uses bars [i-period-shift+1, i-shift]).
    """
    upper = df["High"].rolling(period).max().shift(shift)
    lower = df["Low"].rolling(period).min().shift(shift)
    return upper, lower


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


# ─────────────────────────────────────────────────────────────────────────────
# VWAP — Volume-Weighted Average Price (session-based)
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# RSI Divergence — détecte les divergences prix/RSI sur pivots locaux
# ─────────────────────────────────────────────────────────────────────────────

def compute_rsi_divergence(
    df: pd.DataFrame,
    rsi: pd.Series | None = None,
    lookback: int = 20,
    rsi_period: int = 14,
    pivot_window: int = 5,
) -> pd.Series:
    """Détecte les divergences RSI classiques sur des pivots locaux.

    Divergence bullish (+1) : le prix fait un creux plus bas mais le RSI
    fait un creux plus haut → affaiblissement de la pression vendeuse.

    Divergence bearish (-1) : le prix fait un sommet plus haut mais le RSI
    fait un sommet plus bas → affaiblissement de la pression acheteuse.

    Utilisable comme :
    - Filtre d'entrée (confirmer un breakout avec divergence)
    - Signal de retournement (Glissade, sorties de range)
    - Shadow filter pour Extension

    Args:
        df: DataFrame avec colonnes High, Low, Close.
        rsi: Series RSI pré-calculée. Si None, calcule RSI(rsi_period).
        lookback: Fenêtre max pour chercher le pivot précédent.
        rsi_period: Période RSI si rsi=None.
        pivot_window: Demi-fenêtre pour détecter les pivots locaux
                      (pivot = extremum sur 2×pivot_window+1 barres).

    Returns:
        Series d'entiers : -1 (bearish div), 0 (aucune), +1 (bullish div).
    """
    if rsi is None:
        rsi = compute_rsi(df["Close"], rsi_period)

    n = len(df)
    result = pd.Series(0, index=df.index, dtype=int)

    low = df["Low"].values
    high = df["High"].values
    rsi_vals = rsi.values
    w = pivot_window

    # Pré-calculer les pivots low et high (fenêtre centrée)
    # Un pivot à la barre p est confirmé à la barre p+w (première barre
    # où toutes les barres de la fenêtre sont disponibles).
    pivot_lows = np.zeros(n, dtype=bool)
    pivot_highs = np.zeros(n, dtype=bool)

    for i in range(w, n - w):
        # Pivot low : low[i] est le min sur la fenêtre [i-w, i+w]
        if low[i] == np.min(low[max(0, i - w):i + w + 1]):
            pivot_lows[i] = True
        # Pivot high : high[i] est le max sur la fenêtre [i-w, i+w]
        if high[i] == np.max(high[max(0, i - w):i + w + 1]):
            pivot_highs[i] = True

    # Scanner les divergences — ANTI-LOOKAHEAD
    # Le pivot à la barre p n'est confirmé qu'à la barre p+w.
    # On scanne donc à la barre i (= moment présent) et on regarde
    # le pivot candidat à i-w (qui vient d'être confirmé).
    # La divergence est reportée à la barre i (première barre
    # où la divergence est observable sans lookahead).
    res = np.zeros(n, dtype=int)

    for i in range(2 * w, n):
        candidate = i - w  # Pivot qui vient d'être confirmé

        # Bullish divergence : pivot low confirmé à candidate
        if pivot_lows[candidate]:
            for j in range(candidate - w - 1, max(candidate - lookback, w) - 1, -1):
                if pivot_lows[j]:
                    # Prix : creux plus bas, RSI : creux plus haut
                    if low[candidate] < low[j] and rsi_vals[candidate] > rsi_vals[j]:
                        res[i] = 1
                    break

        # Bearish divergence : pivot high confirmé à candidate
        if pivot_highs[candidate]:
            for j in range(candidate - w - 1, max(candidate - lookback, w) - 1, -1):
                if pivot_highs[j]:
                    # Prix : sommet plus haut, RSI : sommet plus bas
                    if high[candidate] > high[j] and rsi_vals[candidate] < rsi_vals[j]:
                        res[i] = -1
                    break

    result[:] = res
    return result


def compute_swing_levels(
    df: pd.DataFrame,
    pivot_window: int = 5,
) -> pd.DataFrame:
    """Calcule le dernier swing low/high confirmé à chaque barre.

    Un pivot low à la barre i est confirmé à la barre i + pivot_window.
    last_swing_low = prix du dernier pivot low confirmé (forward-filled).
    last_swing_high = prix du dernier pivot high confirmé (forward-filled).

    Anti-lookahead strict : le swing n'est visible qu'après confirmation.

    Returns:
        DataFrame avec colonnes 'last_swing_low', 'last_swing_high'.
    """
    n = len(df)
    low = df["Low"].values
    high = df["High"].values
    w = pivot_window

    swing_low = np.full(n, np.nan)
    swing_high = np.full(n, np.nan)

    for i in range(w, n - w):
        if low[i] == np.min(low[max(0, i - w):i + w + 1]):
            # Confirmed at i + w
            confirm_bar = i + w
            if confirm_bar < n:
                swing_low[confirm_bar] = low[i]
        if high[i] == np.max(high[max(0, i - w):i + w + 1]):
            confirm_bar = i + w
            if confirm_bar < n:
                swing_high[confirm_bar] = high[i]

    # Forward-fill: carry last confirmed swing
    result = pd.DataFrame(index=df.index)
    result["last_swing_low"] = pd.Series(swing_low, index=df.index).ffill()
    result["last_swing_high"] = pd.Series(swing_high, index=df.index).ffill()
    return result


def compute_vwap(
    df: pd.DataFrame,
    session_reset: str = "daily",
) -> pd.Series:
    """VWAP avec reset par session.

    Le VWAP est recalculé à chaque début de session :
    - "daily" : reset à 00:00 UTC (crypto 24x7)
    - "london" : reset à 08:00 UTC
    - "new_york" : reset à 13:00 UTC

    Formule : VWAP = Σ(TP × Volume) / Σ(Volume)
    où TP = (High + Low + Close) / 3

    Args:
        df: DataFrame avec colonnes High, Low, Close, Volume et DatetimeIndex UTC.
        session_reset: "daily", "london", ou "new_york".

    Returns:
        Series du VWAP, même index que df.
    """
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df.get("Volume", pd.Series(1.0, index=df.index))
    tpv = tp * vol

    # Identifier les débuts de session
    reset_hours = {"daily": 0, "london": 8, "new_york": 13}
    reset_hour = reset_hours.get(session_reset, 0)

    hours = df.index.hour
    dates = df.index.date

    if reset_hour == 0:
        # Daily reset : chaque nouveau jour
        session_id = pd.Series(dates, index=df.index)
    else:
        # Session reset : change quand on passe l'heure de reset
        shifted = pd.Series(
            [d if h >= reset_hour else (pd.Timestamp(d) - pd.Timedelta(days=1)).date()
             for d, h in zip(dates, hours)],
            index=df.index,
        )
        session_id = shifted

    # Calcul VWAP par session via cumsum groupé
    cum_tpv = tpv.groupby(session_id).cumsum()
    cum_vol = vol.groupby(session_id).cumsum().replace(0, np.nan)

    return cum_tpv / cum_vol


def compute_vwap_bands(
    df: pd.DataFrame,
    session_reset: str = "daily",
    std_mult: float = 1.0,
) -> dict[str, pd.Series]:
    """VWAP avec bandes de déviation standard.

    Retourne : { "vwap", "vwap_upper", "vwap_lower", "vwap_dist" }
    vwap_dist = (Close - VWAP) / VWAP  (distance normalisée, utile pour z-score)
    """
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df.get("Volume", pd.Series(1.0, index=df.index))

    reset_hours = {"daily": 0, "london": 8, "new_york": 13}
    reset_hour = reset_hours.get(session_reset, 0)
    hours = df.index.hour
    dates = df.index.date

    if reset_hour == 0:
        session_id = pd.Series(dates, index=df.index)
    else:
        session_id = pd.Series(
            [d if h >= reset_hour else (pd.Timestamp(d) - pd.Timedelta(days=1)).date()
             for d, h in zip(dates, hours)],
            index=df.index,
        )

    tpv = tp * vol
    cum_tpv = tpv.groupby(session_id).cumsum()
    cum_vol = vol.groupby(session_id).cumsum().replace(0, np.nan)
    vwap = cum_tpv / cum_vol

    # Déviation standard rolling dans la session
    tp_sq_v = (tp ** 2) * vol
    cum_tp_sq_v = tp_sq_v.groupby(session_id).cumsum()
    variance = (cum_tp_sq_v / cum_vol) - vwap ** 2
    std = np.sqrt(variance.clip(lower=0))

    return {
        "vwap": vwap,
        "vwap_upper": vwap + std_mult * std,
        "vwap_lower": vwap - std_mult * std,
        "vwap_dist": (df["Close"] - vwap) / vwap.replace(0, np.nan),
    }
