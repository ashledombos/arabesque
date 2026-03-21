"""
arabesque/strategies/reverence/signal.py
========================================
Révérence — Range Contraction (NR4/NR7 + Inside Bar) → Expansion Breakout

Détecte les phases de compression (bougies de plus en plus petites) suivies
d'un breakout confirmé par un candle pattern (engulfing/marubozu).

Séquence de setup :
    1. Contraction : NR4/NR7 (range = min des N dernières) et/ou inside bar
    2. Breakout : close au-delà du high/low de la bougie NR ou mère
    3. Confirmation : bougie engulfing ou marubozu (corps ≥ 70% du range)
    4. Filtre directionnel : EMA200 (optionnel)

Différence avec Extension : Extension détecte la compression via BB width sur
20 barres (macro), Révérence via bougies individuelles (micro). Timings différents.

Convention anti-lookahead :
    Signal bougie i → fill au OPEN de bougie i+1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from arabesque.core.models import Side, Signal
from arabesque.modules.indicators import compute_atr, compute_ema

logger = logging.getLogger(__name__)


# ─── Configuration ──────────────────────────────────────────────────────────

@dataclass
class ReverenceConfig:
    """Paramètres de la stratégie Révérence.

    La détection de contraction combine NR (narrow range) et inside bars.
    Le breakout est confirmé par un candle pattern (engulfing ou corps plein).
    """

    # ── Contraction detection ─────────────────────────────────────────
    nr_period: int = 7             # NR7 par défaut (range = min des 7 dernières)
    require_inside_bar: bool = False  # True = exiger aussi inside bar
    min_inside_bars: int = 1       # Nombre minimum d'inside bars consécutives

    # ── Breakout confirmation ─────────────────────────────────────────
    min_body_ratio: float = 0.60   # Corps ≥ ratio × range pour confirmation
    require_engulfing: bool = False  # Engulfing trop restrictif sans gain de WR

    # ── Trend filter ──────────────────────────────────────────────────
    ema_period: int = 200          # EMA trend filter (0 = désactivé)
    require_ema_filter: bool = True

    # ── SL / TP ───────────────────────────────────────────────────────
    sl_buffer_atr: float = 0.1     # SL = bord opposé de la NR bar ± buffer × ATR
    rr_tp: float = 2.0             # TP = RR × SL distance
    max_sl_atr: float = 3.0        # Rejeter si SL distance > max × ATR

    # ── Ablation flags ────────────────────────────────────────────────
    require_nr: bool = True        # False = skip NR check (inside bar only)
    require_candle_confirm: bool = True  # False = breakout sans confirmation


# ─── Signal Generator ───────────────────────────────────────────────────────

class ReverenceSignalGenerator:
    """Range contraction → expansion breakout.

    Usage
    -----
    >>> sg = ReverenceSignalGenerator(ReverenceConfig())
    >>> df = sg.prepare(df_h1)
    >>> signals = sg.generate_signals(df, instrument="XAUUSD")
    """

    def __init__(self, cfg: ReverenceConfig | None = None):
        self.cfg = cfg or ReverenceConfig()

    # ── prepare ──────────────────────────────────────────────────────────

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ajoute les colonnes indicateurs nécessaires.

        Colonnes ajoutées :
            atr        : ATR 14 périodes
            candle_range : High - Low
            nr         : bool, True si range est le min des nr_period dernières
            inside_bar : bool, True si High ≤ prev High et Low ≥ prev Low
            inside_count : nombre d'inside bars consécutives
            ema200     : EMA200 pour filtre directionnel
        """
        df = df.copy()
        c = self.cfg

        # ATR
        df["atr"] = compute_atr(df, period=14)

        # Candle range
        df["candle_range"] = df["High"] - df["Low"]

        # NR detection (Narrow Range)
        # NR7 = range de la bougie courante est le min des 7 dernières
        # Utilise <= pour inclure l'égalité (doji = NR)
        if c.require_nr:
            rolling_min = df["candle_range"].rolling(
                c.nr_period, min_periods=c.nr_period
            ).min()
            df["nr"] = df["candle_range"] <= rolling_min
        else:
            df["nr"] = True  # Ablation: toujours True

        # Inside bar detection
        df["inside_bar"] = (
            (df["High"] <= df["High"].shift(1)) &
            (df["Low"] >= df["Low"].shift(1))
        )

        # Count consecutive inside bars
        df["inside_count"] = 0
        count = 0
        inside_counts = []
        for ib in df["inside_bar"]:
            if ib:
                count += 1
            else:
                count = 0
            inside_counts.append(count)
        df["inside_count"] = inside_counts

        # EMA200 trend filter
        if c.require_ema_filter and c.ema_period > 0:
            df["ema200"] = compute_ema(df["Close"], span=c.ema_period)
        else:
            df["ema200"] = np.nan

        return df

    # ── generate_signals ─────────────────────────────────────────────────

    def generate_signals(
        self, df: pd.DataFrame, instrument: str = "UNKNOWN"
    ) -> list[tuple[int, Signal]]:
        """Génère les signaux Révérence.

        Logique :
        1. Repérer les barres NR (et/ou inside bars) = compression
        2. À la barre suivante, vérifier si c'est un breakout + confirmation
        3. Émettre le signal si toutes les conditions sont remplies
        """
        signals: list[tuple[int, Signal]] = []
        c = self.cfg

        warmup = max(c.nr_period, c.ema_period if c.require_ema_filter else 0, 14) + 5

        High = df["High"]
        Low = df["Low"]
        Open = df["Open"]
        Close = df["Close"]
        atr = df["atr"]
        nr = df["nr"]
        inside_bar = df["inside_bar"]
        inside_count = df["inside_count"]
        ema200 = df.get("ema200", pd.Series(np.nan, index=df.index))

        for i in range(warmup, len(df) - 1):
            atr_val = atr.iloc[i]
            if pd.isna(atr_val) or atr_val <= 0:
                continue

            # ── 1. La BARRE PRÉCÉDENTE doit être en contraction ──────
            prev = i - 1
            if prev < 1:
                continue

            is_contraction = False

            # NR check on previous bar
            if c.require_nr and not nr.iloc[prev]:
                continue

            # Inside bar check on previous bar
            if c.require_inside_bar:
                if inside_count.iloc[prev] < c.min_inside_bars:
                    continue

            # At least one contraction signal
            is_contraction = nr.iloc[prev] or inside_count.iloc[prev] >= 1

            if not is_contraction:
                continue

            # Reference range = the NR/inside bar (previous bar)
            ref_high = High.iloc[prev]
            ref_low = Low.iloc[prev]
            # If multiple inside bars, use the mother bar (first non-inside)
            if inside_count.iloc[prev] >= 1:
                mother_idx = prev - inside_count.iloc[prev]
                if mother_idx >= 0:
                    ref_high = max(ref_high, High.iloc[mother_idx])
                    ref_low = min(ref_low, Low.iloc[mother_idx])

            # ── 2. Barre courante = breakout ? ──────────────────────
            curr_close = Close.iloc[i]
            curr_open = Open.iloc[i]
            curr_high = High.iloc[i]
            curr_low = Low.iloc[i]
            curr_range = curr_high - curr_low

            if curr_range <= 0:
                continue

            # Bullish breakout: close above ref_high
            bullish = curr_close > ref_high
            # Bearish breakout: close below ref_low
            bearish = curr_close < ref_low

            if not bullish and not bearish:
                continue

            direction = 1 if bullish else -1

            # ── 3. Candle confirmation ──────────────────────────────
            if c.require_candle_confirm:
                body = abs(curr_close - curr_open)
                body_ratio = body / curr_range if curr_range > 0 else 0

                # Body must be substantial (marubozu-like)
                if body_ratio < c.min_body_ratio:
                    continue

                # Direction of body must match breakout
                if direction == 1 and curr_close <= curr_open:
                    continue  # Not a bullish candle
                if direction == -1 and curr_close >= curr_open:
                    continue  # Not a bearish candle

                # Engulfing: body engulfs the NR bar's range
                if c.require_engulfing:
                    if direction == 1:
                        engulfs = curr_close > ref_high and curr_open <= ref_low + atr_val * 0.5
                    else:
                        engulfs = curr_close < ref_low and curr_open >= ref_high - atr_val * 0.5
                    if not engulfs:
                        continue

            # ── 4. EMA filter ───────────────────────────────────────
            if c.require_ema_filter:
                ema_val = ema200.iloc[i]
                if not pd.isna(ema_val):
                    if direction == 1 and curr_close < ema_val:
                        continue
                    if direction == -1 and curr_close > ema_val:
                        continue

            # ── 5. Compute SL/TP ────────────────────────────────────
            buffer = c.sl_buffer_atr * atr_val

            if direction == 1:
                sl = ref_low - buffer
                risk = curr_close - sl
                if risk <= 0 or risk > c.max_sl_atr * atr_val:
                    continue
                tp = curr_close + c.rr_tp * risk
                side = Side.LONG
            else:
                sl = ref_high + buffer
                risk = sl - curr_close
                if risk <= 0 or risk > c.max_sl_atr * atr_val:
                    continue
                tp = curr_close - c.rr_tp * risk
                side = Side.SHORT

            # ── 6. Build sub_type for ablation tracking ─────────────
            components = []
            if c.require_nr:
                components.append(f"nr{c.nr_period}")
            if c.require_inside_bar:
                components.append("ib")
            if c.require_candle_confirm:
                components.append("cc")
            if c.require_engulfing:
                components.append("eng")
            if c.require_ema_filter:
                components.append("ema")
            sub_type = "reverence_" + "_".join(components) if components else "reverence_raw"

            sig = Signal(
                instrument=instrument,
                side=side,
                close=curr_close,
                sl=sl,
                tp_indicative=tp,
                atr=atr_val,
                bb_width=float(df["candle_range"].iloc[i]) if "candle_range" in df.columns else 1.0,
                strategy_type="reverence",
                sub_type=sub_type,
                timeframe="1h",
                timestamp=df.index[i],
            )
            signals.append((i, sig))

        return signals
