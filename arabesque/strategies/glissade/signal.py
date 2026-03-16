"""
arabesque/strategies/glissade/signal.py
=======================================
Glissade — Scalping intraday sur pullback VWAP + EMA.

Une glissade en danse classique est un pas glissé, court et fluide, qui relie
deux mouvements plus grands. Le prix "glisse" brièvement contre la tendance
intraday puis repart — un pullback vers le VWAP capté au bon moment.

Timeframe cible  : M1 (entrée) + M5 (contexte via resample)
Instruments      : XAUUSD, crypto liquides, indices
Session          : Heures de marché à haute liquidité (NY, London)

Logique
-------
1. Contexte (M5 resample) : EMA200 direction + ADX in range [15,30]
2. VWAP session : prix doit être du bon côté du VWAP (confirme le biais)
3. Pullback : 2+ bougies M1 contre-tendance, creux/sommet touchant EMA20(M1)
4. Trigger : bougie M1 de retournement clôturant au-delà du high/low précédent
5. Entrée : OPEN de bougie i+1 (anti-lookahead strict)

Gestion
-------
- SL : sl_atr_factor × ATR14(M1), capped à sl_atr_max × ATR14
- TP : rr_tp × SL distance (simple ratio, position manager gère BE/trailing)
- Time stop : géré par position_manager (pas dans signal.py)

Guards spécifiques
------------------
- ADX(M5) : adx_min ≤ ADX ≤ adx_max (pas de chop, pas de trend violent)
- Session window : entry_delay après ouverture, session_end_buffer avant fin
- Max trades/jour : max_trades_per_day
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

            sig = Signal(
                instrument=instrument,
                side=side,
                close=c,
                sl=sl,
                tp_indicative=tp,
                atr=atr_val,
                bb_width=1.0,  # Neutral — BB squeeze guard is Extension-specific
                strategy_type="glissade_vwap_pullback",
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
