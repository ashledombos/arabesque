"""
Arabesque v2 — Backtest signal generator.

Génère des signaux BB excess à partir de données OHLCV.
Même code utilisé en live (source cTrader) et en backtest (source parquet).

Indicateurs calculés :
- BB(20, 2) sur 1H
- EMA 200 sur 1H (filtre trend)
- RSI 14 sur 1H
- CMF 20 sur 1H
- ATR 14 sur 1H
- Williams %R 14 sur 1H
- Régime HTF (4H) via EMA fast/slow + ADX

Anti-lookahead (backtest) : signal émis sur bougie CONFIRMÉE (index i),
  entrée simulée au OPEN de i+1.
live_mode=True : signal émis sur la DERNIÈRE bougie du cache (index n-1),
  entrée au close courant.
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from arabesque.models import Signal, Side
from arabesque.backtest.signal_labeler import label_mr_signal
from arabesque.indicators import (
    compute_rsi, compute_atr, compute_adx,
    compute_bollinger, compute_cmf, compute_williams_r,
    compute_ema, compute_htf_regime,
)


@dataclass
class SignalGenConfig:
    """Paramètres du générateur de signaux."""
    bb_period: int = 20
    bb_std: float = 2.0
    ema_fast: int = 50
    ema_slow: int = 200
    rsi_period: int = 14
    cmf_period: int = 20
    atr_period: int = 14
    wr_period: int = 14
    rsi_oversold: float = 35.0
    rsi_overbought: float = 65.0
    min_bb_width: float = 0.003
    min_rr: float = 0.5
    htf_ema_fast: int = 12
    htf_ema_slow: int = 26
    htf_adx_period: int = 14
    sl_method: str = "swing"
    sl_atr_mult: float = 1.5
    sl_swing_bars: int = 10
    min_sl_atr: float = 0.8


class BacktestSignalGenerator:
    """Génère des signaux BB excess à partir de données OHLC."""

    def __init__(self, config: SignalGenConfig | None = None, live_mode: bool = False):
        self.cfg = config or SignalGenConfig()
        self.live_mode = live_mode

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calcule tous les indicateurs. Utilise arabesque/indicators.py."""
        df = df.copy()
        bb = compute_bollinger(df, self.cfg.bb_period, self.cfg.bb_std)
        df["bb_mid"] = bb["mid"]
        df["bb_lower"] = bb["lower"]
        df["bb_upper"] = bb["upper"]
        df["bb_width"] = bb["width"]
        df["ema_fast"] = compute_ema(df["Close"], self.cfg.ema_fast)
        df["ema_slow"] = compute_ema(df["Close"], self.cfg.ema_slow)
        df["rsi"] = compute_rsi(df["Close"], self.cfg.rsi_period)
        df["cmf"] = compute_cmf(df, self.cfg.cmf_period)
        df["atr"] = compute_atr(df, self.cfg.atr_period)
        df["wr_14"] = compute_williams_r(df, self.cfg.wr_period)
        df = compute_htf_regime(
            df,
            ema_fast=self.cfg.htf_ema_fast,
            ema_slow=self.cfg.htf_ema_slow,
            adx_period=self.cfg.htf_adx_period,
        )
        df["swing_low"] = df["Low"].rolling(self.cfg.sl_swing_bars).min()
        df["swing_high"] = df["High"].rolling(self.cfg.sl_swing_bars).max()
        return df

    def generate_signals(
        self, df: pd.DataFrame, instrument: str = ""
    ) -> list[tuple[int, Signal]]:
        signals: list[tuple[int, Signal]] = []
        warmup = max(self.cfg.ema_slow, self.cfg.bb_period, self.cfg.atr_period) + 10
        end = len(df) if self.live_mode else len(df) - 1
        for i in range(warmup, end):
            row = df.iloc[i]
            if pd.isna(row.get("atr")) or row["atr"] <= 0:
                continue
            if pd.isna(row.get("bb_lower")):
                continue
            signal = self._check_bb_excess(row, df, i, instrument)
            if signal is not None:
                signals.append((i, signal))
        return signals

    def _check_bb_excess(
        self, row: pd.Series, df: pd.DataFrame, idx: int, instrument: str
    ) -> Signal | None:
        close = row["Close"]
        bb_lower = row["bb_lower"]
        bb_upper = row["bb_upper"]
        bb_mid = row["bb_mid"]
        atr = row["atr"]

        if row["bb_width"] < self.cfg.min_bb_width:
            return None

        # ── LONG : close < BB lower ──
        if close < bb_lower:
            if row["rsi"] > self.cfg.rsi_oversold:
                return None
            regime = row.get("regime", "bull_range")
            if regime == "bear_trend":
                return None
            sl = self._compute_sl(row, df, idx, Side.LONG)
            if sl <= 0 or sl >= close:
                sl = close - self.cfg.sl_atr_mult * atr
            tp = bb_mid
            risk_dist = abs(close - sl)
            reward_dist = abs(tp - close)
            if risk_dist <= 0:
                return None
            rr = reward_dist / risk_dist
            if rr < self.cfg.min_rr:
                return None
            sig = Signal(
                instrument=instrument,
                side=Side.LONG,
                timeframe="1h",
                close=close,
                open_=row["Open"],
                sl=sl,
                tp_indicative=tp,
                atr=atr,
                rsi=row["rsi"],
                cmf=row.get("cmf", 0),
                bb_lower=bb_lower,
                bb_mid=bb_mid,
                bb_upper=bb_upper,
                bb_width=row["bb_width"],
                wr_14=row.get("wr_14", -50),
                ema200_ltf=row.get("ema_slow", 0),
                htf_ema_fast=row.get("htf_ema_fast_val", 0),
                htf_ema_slow=row.get("htf_ema_slow_val", 0),
                htf_adx=row.get("htf_adx", 0),
                regime=regime,
                max_spread_atr=0.3,
                rr=round(rr, 2),
                strategy_type="mean_reversion",
                timestamp=df.index[idx],
            )
            return label_mr_signal(sig, df, idx)

        # ── SHORT : close > BB upper ──
        if close > bb_upper:
            if row["rsi"] < self.cfg.rsi_overbought:
                return None
            regime = row.get("regime", "bear_range")
            if regime == "bull_trend":
                return None
            sl = self._compute_sl(row, df, idx, Side.SHORT)
            if sl <= 0 or sl <= close:
                sl = close + self.cfg.sl_atr_mult * atr
            tp = bb_mid
            risk_dist = abs(sl - close)
            reward_dist = abs(close - tp)
            if risk_dist <= 0:
                return None
            rr = reward_dist / risk_dist
            if rr < self.cfg.min_rr:
                return None
            sig = Signal(
                instrument=instrument,
                side=Side.SHORT,
                timeframe="1h",
                close=close,
                open_=row["Open"],
                sl=sl,
                tp_indicative=tp,
                atr=atr,
                rsi=row["rsi"],
                cmf=row.get("cmf", 0),
                bb_lower=bb_lower,
                bb_mid=bb_mid,
                bb_upper=bb_upper,
                bb_width=row["bb_width"],
                wr_14=row.get("wr_14", -50),
                ema200_ltf=row.get("ema_slow", 0),
                htf_ema_fast=row.get("htf_ema_fast_val", 0),
                htf_ema_slow=row.get("htf_ema_slow_val", 0),
                htf_adx=row.get("htf_adx", 0),
                regime=regime,
                max_spread_atr=0.3,
                rr=round(rr, 2),
                strategy_type="mean_reversion",
                timestamp=df.index[idx],
            )
            return label_mr_signal(sig, df, idx)

        return None

    def _compute_sl(self, row, df, idx, side):
        atr = row["atr"]
        close = row["Close"]
        min_dist = self.cfg.min_sl_atr * atr
        if self.cfg.sl_method == "swing":
            if side == Side.LONG:
                sl = row.get("swing_low", 0)
                if sl > 0:
                    sl -= 0.2 * atr
                if sl <= 0 or (close - sl) < min_dist:
                    sl = close - min_dist
            else:
                sl = row.get("swing_high", 0)
                if sl > 0:
                    sl += 0.2 * atr
                if sl <= 0 or (sl - close) < min_dist:
                    sl = close + min_dist
            return sl
        if side == Side.LONG:
            return close - max(self.cfg.sl_atr_mult * atr, min_dist)
        return close + max(self.cfg.sl_atr_mult * atr, min_dist)
