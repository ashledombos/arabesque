"""
arabesque/strategies/glissade/signal.py
=======================================
Glissade — Deux modes de signal :

**Mode RSI divergence H1 (validé, walk-forward PASS 3/3)** :
    RSI divergence comme signal principal sur H1.
    XAUUSD H1 : WR 87%, +5.7R OOS. BTCUSD H1 : WR 85%, +10.6R OOS.
    Utiliser GlissadeRSIDivGenerator.

**Mode VWAP pullback M1 (abandonné, structurellement négatif)** :
    Scalping intraday sur pullback VWAP + EMA.
    Conservé pour référence, ne pas déployer.
    Utiliser GlissadeSignalGenerator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from arabesque.core.models import Side, Signal
from arabesque.modules.indicators import (
    compute_adx,
    compute_atr,
    compute_ema,
    compute_rsi,
    compute_rsi_divergence,
    compute_vwap,
)

logger = logging.getLogger(__name__)


# ─── Configuration ──────────────────────────────────────────────────────────

@dataclass
class GlissadeConfig:
    """Paramètres de la stratégie Glissade (scalping VWAP pullback).

    Tous les paramètres sont des defaults de départ à calibrer par
    walk-forward sur données M1. Aucun n'est validé.
    """

    # ── Session / fenêtre ─────────────────────────────────────────────────
    session: str = "ny"                     # "ny", "london", "overlap"
    entry_delay_minutes: int = 5            # Attendre N min après session open
    session_end_buffer_minutes: int = 10    # Arrêter N min avant fin session

    # ── Contexte (M5 resample) ──────────────────────────────────────────
    ema_slow_period: int = 200              # EMA lente sur M5
    adx_period: int = 14                    # ADX sur M5
    adx_min: float = 15.0                   # Pas de trade sous ce seuil
    adx_max: float = 30.0                   # Pas de trade au-dessus

    # ── Signal (M1) ──────────────────────────────────────────────────────
    ema_fast_period: int = 20               # EMA rapide sur M1 (pullback target)
    pullback_min_bars: int = 2              # Min bougies contre-tendance
    pullback_ema_tolerance_atr: float = 0.3 # Creux à ≤ 0.3×ATR de EMA20

    # ── VWAP ─────────────────────────────────────────────────────────────
    vwap_session_reset: str = "daily"       # "daily", "london", "new_york"

    # ── SL / TP ──────────────────────────────────────────────────────────
    sl_atr_factor: float = 1.0              # SL = facteur × ATR14(M1)
    sl_atr_max: float = 1.5                 # Cap SL à max × ATR14(M1)
    rr_tp: float = 2.0                      # TP = rr_tp × SL distance

    # ── RSI divergence filter ──────────────────────────────────────────
    rsi_div_required: bool = False          # True = only enter on RSI divergence
    rsi_div_lookback: int = 20              # Fenêtre pour chercher le pivot précédent
    rsi_div_pivot_window: int = 5           # Demi-fenêtre pour les pivots locaux

    # ── Risk ─────────────────────────────────────────────────────────────
    max_trades_per_day: int = 6
    risk_per_trade_pct: float = 0.10        # 0.10% (scalping = fréquence haute)


# ─── Session windows ─────────────────────────────────────────────────────────

# (start_hour_utc, start_min, end_hour_utc, end_min)
_SESSION_WINDOWS = {
    "ny":      (13, 30, 20, 0),    # 9h30-16h00 ET (summer UTC)
    "london":  (8,  0,  16, 30),   # 8h00-16h30 UTC
    "overlap": (13, 30, 16, 30),   # NY/London overlap
}


# ─── Signal Generator ────────────────────────────────────────────────────────

class GlissadeSignalGenerator:
    """Scalping VWAP pullback — M1 entries with M5 context.

    Usage
    -----
    >>> sg = GlissadeSignalGenerator(GlissadeConfig())
    >>> df = sg.prepare(df_m1)
    >>> signals = sg.generate_signals(df, instrument="XAUUSD")
    """

    def __init__(self, config: Optional[GlissadeConfig] = None):
        self.cfg = config or GlissadeConfig()

    # ── prepare ──────────────────────────────────────────────────────────────

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ajoute les colonnes indicateurs nécessaires.

        Colonnes ajoutées :
            ema20       : EMA 20 sur M1 (pullback target)
            atr         : ATR 14 sur M1
            vwap        : VWAP session
            m5_ema200   : EMA 200 sur M5 (forward-filled vers M1)
            m5_adx      : ADX 14 sur M5 (forward-filled vers M1)
            in_session  : bool, True si la bougie est dans la fenêtre de session
        """
        df = df.copy()

        # Normaliser les noms (tolérer Open/open)
        _had_caps = "Close" in df.columns
        col_map = {c: c.lower() for c in df.columns}
        df.columns = [c.lower() for c in df.columns]

        # ── M1 indicators ──
        df["ema20"] = compute_ema(df["close"], span=self.cfg.ema_fast_period)
        df["atr"] = compute_atr(
            df.rename(columns={"open": "Open", "high": "High",
                               "low": "Low", "close": "Close",
                               "volume": "Volume"}),
            period=14,
        )
        # Remettre en lowercase après compute_atr
        df["vwap"] = compute_vwap(
            df.rename(columns={"open": "Open", "high": "High",
                               "low": "Low", "close": "Close",
                               "volume": "Volume"}),
            session_reset=self.cfg.vwap_session_reset,
        )

        # ── RSI + divergence ──
        df_caps = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })
        df["rsi"] = compute_rsi(df_caps["Close"], period=14)
        df["rsi_div"] = compute_rsi_divergence(
            df_caps, rsi=df["rsi"],
            lookback=self.cfg.rsi_div_lookback,
            pivot_window=self.cfg.rsi_div_pivot_window,
        )

        # ── M5 context via resample ──
        df_m5 = df.resample("5min").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna()

        if len(df_m5) >= self.cfg.ema_slow_period + 10:
            df_m5_caps = df_m5.rename(columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            })
            m5_ema200 = compute_ema(df_m5["close"], span=self.cfg.ema_slow_period)
            m5_adx = compute_adx(df_m5_caps, period=self.cfg.adx_period)

            # Forward-fill M5 → M1
            df["m5_ema200"] = m5_ema200.reindex(df.index, method="ffill")
            df["m5_adx"] = m5_adx.reindex(df.index, method="ffill")
        else:
            # Fallback si pas assez de données M5
            df["m5_ema200"] = df["close"]
            df["m5_adx"] = 20.0  # Neutre (dans la plage adx_min-adx_max)

        # ── Session window ──
        df["in_session"] = self._tag_session(df)

        # Restaurer les colonnes capitalisées pour le runner backtest
        if _had_caps:
            for lc, uc in [("open", "Open"), ("high", "High"), ("low", "Low"),
                           ("close", "Close"), ("volume", "Volume")]:
                if lc in df.columns:
                    df[uc] = df[lc]

        return df

    def _tag_session(self, df: pd.DataFrame) -> pd.Series:
        """Marque les barres dans la fenêtre de session active (vectorisé)."""
        window = _SESSION_WINDOWS.get(self.cfg.session, _SESSION_WINDOWS["ny"])
        start_h, start_m, end_h, end_m = window

        idx = pd.DatetimeIndex(df.index)
        bar_min = idx.hour * 60 + idx.minute
        session_start = start_h * 60 + start_m + self.cfg.entry_delay_minutes
        session_end = end_h * 60 + end_m - self.cfg.session_end_buffer_minutes

        return pd.Series(
            (bar_min >= session_start) & (bar_min < session_end),
            index=df.index,
        )

    # ── generate_signals ─────────────────────────────────────────────────────

    def generate_signals(
        self, df: pd.DataFrame, instrument: str
    ) -> list[tuple[int, Signal]]:
        """Génère les signaux de scalping VWAP pullback.

        Convention anti-lookahead :
            Signal sur bougie i → fill au OPEN de bougie i+1.

        Logique :
        1. Filtres contextuels : in_session, ADX in range
        2. Biais directionnel : close > m5_ema200 + close > vwap → bullish
        3. Détection pullback : N bougies contre-tendance, creux near EMA20
        4. Trigger : bougie de retournement (close au-delà du prev high/low)
        """
        signals: list[tuple[int, Signal]] = []
        cfg = self.cfg

        # Colonnes en lowercase
        close = df["close"] if "close" in df.columns else df["Close"]
        high = df["high"] if "high" in df.columns else df["High"]
        low = df["low"] if "low" in df.columns else df["Low"]
        ema20 = df["ema20"]
        atr = df["atr"]
        vwap = df["vwap"]
        m5_ema200 = df["m5_ema200"]
        m5_adx = df["m5_adx"]
        in_session = df["in_session"]

        # Tracking trades per day
        trades_today: dict[str, int] = {}  # date_str → count

        min_pb = cfg.pullback_min_bars

        for i in range(min_pb + 2, len(df)):
            # ── Guard: session window ──
            if not in_session.iloc[i]:
                continue

            # ── Guard: ADX in range ──
            adx_val = m5_adx.iloc[i]
            if pd.isna(adx_val) or adx_val < cfg.adx_min or adx_val > cfg.adx_max:
                continue

            # ── Guard: ATR valid ──
            atr_val = atr.iloc[i]
            if pd.isna(atr_val) or atr_val <= 0:
                continue

            # ── Guard: max trades per day ──
            day_key = str(df.index[i].date())
            if trades_today.get(day_key, 0) >= cfg.max_trades_per_day:
                continue

            # ── Determine bias ──
            c = close.iloc[i]
            ema200_val = m5_ema200.iloc[i]
            vwap_val = vwap.iloc[i]

            if pd.isna(ema200_val) or pd.isna(vwap_val):
                continue

            bullish_bias = c > ema200_val and c > vwap_val
            bearish_bias = c < ema200_val and c < vwap_val

            if not bullish_bias and not bearish_bias:
                continue  # Mixed signals → skip

            # ── Detect pullback ──
            # For bullish: N consecutive bars with lower lows (counter-trend)
            # For bearish: N consecutive bars with higher highs
            if bullish_bias:
                pullback_ok = self._detect_pullback_bull(
                    df, i, min_pb, ema20, atr_val
                )
            else:
                pullback_ok = self._detect_pullback_bear(
                    df, i, min_pb, ema20, atr_val
                )

            if not pullback_ok:
                continue

            # ── RSI divergence filter ──
            if cfg.rsi_div_required:
                rsi_div_val = df["rsi_div"].iloc[i] if "rsi_div" in df.columns else 0
                if bullish_bias and rsi_div_val != 1:
                    continue
                if bearish_bias and rsi_div_val != -1:
                    continue

            # ── Detect trigger (reversal bar) ──
            if bullish_bias:
                # Current bar closes above previous bar's high
                if c <= high.iloc[i - 1]:
                    continue
            else:
                # Current bar closes below previous bar's low
                if c >= low.iloc[i - 1]:
                    continue

            # ── Compute SL / TP ──
            sl_dist = min(cfg.sl_atr_factor * atr_val,
                          cfg.sl_atr_max * atr_val)

            if bullish_bias:
                sl = c - sl_dist
                tp = c + cfg.rr_tp * sl_dist
                side = Side.LONG
            else:
                sl = c + sl_dist
                tp = c - cfg.rr_tp * sl_dist
                side = Side.SHORT

            rsi_div_val = int(df["rsi_div"].iloc[i]) if "rsi_div" in df.columns else 0
            rsi_val = float(df["rsi"].iloc[i]) if "rsi" in df.columns else 50.0

            sig = Signal(
                instrument=instrument,
                side=side,
                close=c,
                sl=sl,
                tp_indicative=tp,
                atr=atr_val,
                rsi=rsi_val,
                rsi_div=rsi_div_val,
                bb_width=1.0,  # Neutral — BB squeeze guard is Extension-specific
                strategy_type="glissade",
                sub_type="vwap_pullback" + ("_rdiv" if rsi_div_val != 0 else ""),
                timeframe="1m",
            )

            signals.append((i, sig))
            trades_today[day_key] = trades_today.get(day_key, 0) + 1

        return signals

    # ── Pullback detection helpers ───────────────────────────────────────────

    def _detect_pullback_bull(
        self, df: pd.DataFrame, i: int, min_bars: int,
        ema20: pd.Series, atr_val: float,
    ) -> bool:
        """Détecte un pullback haussier : N barres avec des lows décroissants,
        et le creux touche l'EMA20 (± tolérance ATR)."""
        low = df["low"] if "low" in df.columns else df["Low"]
        tol = self.cfg.pullback_ema_tolerance_atr * atr_val

        # Check N bars before current: lows decreasing (counter-trend pullback)
        pullback_bars = 0
        for j in range(i - 1, max(i - min_bars - 3, 0), -1):
            if low.iloc[j] < low.iloc[j + 1]:
                pullback_bars += 1
            else:
                break

        if pullback_bars < min_bars:
            return False

        # Check that the lowest low in the pullback is near EMA20
        pullback_low = low.iloc[i - pullback_bars:i].min()
        ema_val = ema20.iloc[i - 1]  # EMA at previous bar
        if pd.isna(ema_val):
            return False

        return pullback_low <= ema_val + tol and pullback_low >= ema_val - tol

    def _detect_pullback_bear(
        self, df: pd.DataFrame, i: int, min_bars: int,
        ema20: pd.Series, atr_val: float,
    ) -> bool:
        """Détecte un pullback baissier : N barres avec des highs croissants,
        et le sommet touche l'EMA20 (± tolérance ATR)."""
        high = df["high"] if "high" in df.columns else df["High"]
        tol = self.cfg.pullback_ema_tolerance_atr * atr_val

        pullback_bars = 0
        for j in range(i - 1, max(i - min_bars - 3, 0), -1):
            if high.iloc[j] > high.iloc[j + 1]:
                pullback_bars += 1
            else:
                break

        if pullback_bars < min_bars:
            return False

        pullback_high = high.iloc[i - pullback_bars:i].max()
        ema_val = ema20.iloc[i - 1]
        if pd.isna(ema_val):
            return False

        return pullback_high >= ema_val - tol and pullback_high <= ema_val + tol


# ─── RSI Divergence H1 mode (validated) ─────────────────────────────────────

@dataclass
class GlissadeRSIDivConfig:
    """Paramètres du mode RSI divergence H1.

    Validés par walk-forward (3/3 PASS) :
    - XAUUSD H1 pw3 RR2 +BE : 31 OOS trades, WR 87%, +5.7R
    - BTCUSD H1 pw3 RR2 +BE : 54 OOS trades, WR 85%, +10.6R
    """
    rr_tp: float = 2.0
    pivot_window: int = 3
    lookback: int = 20
    sl_lookback: int = 10
    ema_period: int = 200


class GlissadeRSIDivGenerator:
    """RSI divergence comme signal principal — mode validé H1.

    Setup :
    1. Contexte : close au-dessus/en-dessous de EMA200 (trend)
    2. Signal : RSI divergence bullish/bearish dans le sens du trend
    3. SL : recent swing low/high ± 0.1×ATR
    4. TP : RR × SL distance
    """

    def __init__(self, cfg: GlissadeRSIDivConfig | None = None):
        self.cfg = cfg or GlissadeRSIDivConfig()

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        c = self.cfg
        df["rsi"] = compute_rsi(df["Close"], period=14)
        df["ema200"] = compute_ema(df["Close"], span=c.ema_period)
        df["atr"] = compute_atr(df, period=14)
        df["rsi_div"] = compute_rsi_divergence(
            df, rsi=df["rsi"], lookback=c.lookback, pivot_window=c.pivot_window
        )
        df["swing_low"] = df["Low"].rolling(c.sl_lookback, center=False).min()
        df["swing_high"] = df["High"].rolling(c.sl_lookback, center=False).max()
        return df

    def generate_signals(
        self, df: pd.DataFrame, instrument: str = "UNKNOWN"
    ) -> list[tuple[int, Signal]]:
        signals: list[tuple[int, Signal]] = []
        c = self.cfg

        for i in range(max(c.ema_period, 50), len(df) - 1):
            row = df.iloc[i]
            rsi_div = int(row["rsi_div"])
            if rsi_div == 0:
                continue

            close = row["Close"]
            ema200 = row["ema200"]
            atr = row["atr"]
            if pd.isna(ema200) or pd.isna(atr) or atr <= 0:
                continue

            if rsi_div == 1 and close > ema200:
                sl = row["swing_low"] - 0.1 * atr
                risk = close - sl
                if risk <= 0 or risk > 3 * atr:
                    continue
                tp = close + c.rr_tp * risk
                sig = Signal(
                    instrument=instrument,
                    side=Side.LONG,
                    close=close,
                    sl=sl,
                    tp_indicative=tp,
                    atr=atr,
                    rsi=row["rsi"],
                    rsi_div=1,
                    bb_width=1.0,
                    strategy_type="glissade",
                    timeframe="1h",
                    timestamp=df.index[i],
                )
                signals.append((i, sig))

            elif rsi_div == -1 and close < ema200:
                sl = row["swing_high"] + 0.1 * atr
                risk = sl - close
                if risk <= 0 or risk > 3 * atr:
                    continue
                tp = close - c.rr_tp * risk
                sig = Signal(
                    instrument=instrument,
                    side=Side.SHORT,
                    close=close,
                    sl=sl,
                    tp_indicative=tp,
                    atr=atr,
                    rsi=row["rsi"],
                    rsi_div=-1,
                    bb_width=1.0,
                    strategy_type="glissade",
                    timeframe="1h",
                    timestamp=df.index[i],
                )
                signals.append((i, sig))

        return signals
