"""
Arabesque — Stratégie Cabriole (Donchian Breakout 4H).

Adapté du projet Envolées (DonchianBreakoutStrategy).
Détecte les breakouts au-delà des canaux de Donchian :
1. EMA200 pour la direction du trend
2. Close casse au-dessus/en-dessous du canal Donchian(N) + buffer ATR
3. Filtre volatilité : ATR relatif < percentile glissant
4. SL fixe en ATR, TP en R:R

Même interface que les autres signal generators :
  - prepare(df) → DataFrame enrichi
  - generate_signals(df, instrument) → list[(bar_index, Signal)]
"""

from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from arabesque.core.models import Signal, Side
from arabesque.modules.indicators import (
    compute_ema, compute_atr, compute_donchian,
)


@dataclass
class CabrioleConfig:
    """Paramètres du générateur de signaux Cabriole (Donchian breakout)."""
    donchian_n: int = 20
    ema_period: int = 200
    atr_period: int = 14
    sl_atr: float = 1.5
    rr_tp: float = 2.0
    buffer_atr: float = 0.10
    vol_quantile: float = 0.90
    vol_window: int = 500


class CabrioleSignalGenerator:
    """Donchian breakout avec EMA200 trend filter et volatilité filter."""

    def __init__(self, cfg: CabrioleConfig | None = None):
        self.cfg = cfg or CabrioleConfig()

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        c = self.cfg

        df["ema200"] = compute_ema(df["Close"], span=c.ema_period)
        df["atr"] = compute_atr(df, period=c.atr_period)
        df["d_high"], df["d_low"] = compute_donchian(df, period=c.donchian_n, shift=1)

        atr_rel = df["atr"] / df["Close"]
        df["vol_ok"] = atr_rel <= atr_rel.rolling(
            c.vol_window, min_periods=min(50, c.vol_window)
        ).quantile(c.vol_quantile)

        return df

    def generate_signals(
        self, df: pd.DataFrame, instrument: str = "UNKNOWN"
    ) -> list[tuple[int, Signal]]:
        signals: list[tuple[int, Signal]] = []
        c = self.cfg
        start = max(c.ema_period, 50)

        for i in range(start, len(df) - 1):
            row = df.iloc[i]
            close = row["Close"]
            ema = row["ema200"]
            atr = row["atr"]
            d_high = row["d_high"]
            d_low = row["d_low"]

            if pd.isna(ema) or pd.isna(atr) or pd.isna(d_high) or atr <= 0:
                continue

            if not row["vol_ok"]:
                continue

            buffer = c.buffer_atr * atr
            sl_dist = c.sl_atr * atr

            # LONG: close breaks above Donchian high + buffer, trend up
            if close > ema and close > d_high + buffer:
                sl = close - sl_dist
                tp = close + c.rr_tp * sl_dist
                sig = Signal(
                    instrument=instrument,
                    side=Side.LONG,
                    close=close,
                    sl=sl,
                    tp_indicative=tp,
                    atr=atr,
                    rsi=50.0,
                    rsi_div=0,
                    bb_width=1.0,
                    strategy_type="cabriole",
                    timeframe="4h",
                    timestamp=df.index[i],
                )
                signals.append((i, sig))

            # SHORT: close breaks below Donchian low - buffer, trend down
            elif close < ema and close < d_low - buffer:
                sl = close + sl_dist
                tp = close - c.rr_tp * sl_dist
                sig = Signal(
                    instrument=instrument,
                    side=Side.SHORT,
                    close=close,
                    sl=sl,
                    tp_indicative=tp,
                    atr=atr,
                    rsi=50.0,
                    rsi_div=0,
                    bb_width=1.0,
                    strategy_type="cabriole",
                    timeframe="4h",
                    timestamp=df.index[i],
                )
                signals.append((i, sig))

        return signals
