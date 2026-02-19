"""
Arabesque v2 — Backtest signal generator.

Remplace Pine pour le backtest. Calcule les MÊMES indicateurs :
- BB(20, 2) sur 1H (signal)
- EMA 200 sur 1H (filtre trend)
- RSI 14 sur 1H
- CMF 20 sur 1H
- ATR 14 sur 1H
- Williams %R 14 sur 1H (vrai W%R, pas percentrank * -1)
- BB width
- Régime HTF (4H) via EMA fast/slow + ADX

Signal BB excess LONG : close < BB lower && régime bullish && filtre supplémentaire
Signal BB excess SHORT : close > BB upper && régime bearish && filtre supplémentaire

Anti-lookahead (backtest) : signal émis sur bougie CONFIRMÉE (index i),
  entrée simulée au OPEN de i+1.
live_mode=True : signal émis sur la DERNIÈRE bougie du cache (index n-1),
  entrée au close courant (pas de décalage).
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from arabesque.models import Signal, Side
from arabesque.backtest.signal_labeler import label_mr_signal


@dataclass
class SignalGenConfig:
    """Paramètres du générateur de signaux."""
    # Bollinger Bands
    bb_period: int = 20
    bb_std: float = 2.0

    # EMAs
    ema_fast: int = 50
    ema_slow: int = 200

    # RSI
    rsi_period: int = 14

    # CMF
    cmf_period: int = 20

    # ATR
    atr_period: int = 14

    # Williams %R
    wr_period: int = 14

    # Filtres signal
    rsi_oversold: float = 35.0      # RSI < X pour LONG
    rsi_overbought: float = 65.0    # RSI > X pour SHORT
    min_bb_width: float = 0.003     # Filtre BB squeeze
    min_rr: float = 0.5             # Risk/Reward minimum

    # Régime HTF (simulé en 4H via resample)
    htf_ema_fast: int = 12
    htf_ema_slow: int = 26
    htf_adx_period: int = 14

    # SL placement
    sl_method: str = "swing"        # "swing" = recent swing low/high, "atr" = N*ATR
    sl_atr_mult: float = 1.5        # Multiplicateur ATR pour SL si method=atr
    sl_swing_bars: int = 10         # Lookback pour swing low/high
    min_sl_atr: float = 0.8        # SL minimum = 0.8 * ATR (évite les R minuscules)


class BacktestSignalGenerator:
    """Génère des signaux BB excess à partir de données OHLC.

    Interface identique à TrendSignalGenerator :
    generate_signals() retourne list[tuple[int, Signal]].

    live_mode=True : inclut la dernière bougie du cache (index n-1)
    et ne nécessite pas la bougie suivante pour simuler l'entrée.
    """

    def __init__(self, config: SignalGenConfig | None = None, live_mode: bool = False):
        self.cfg = config or SignalGenConfig()
        self.live_mode = live_mode

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calcule tous les indicateurs sur le DataFrame."""
        df = df.copy()

        # ── BB(20, 2) ──
        df["bb_mid"] = df["Close"].rolling(self.cfg.bb_period).mean()
        bb_std = df["Close"].rolling(self.cfg.bb_period).std()
        df["bb_lower"] = df["bb_mid"] - self.cfg.bb_std * bb_std
        df["bb_upper"] = df["bb_mid"] + self.cfg.bb_std * bb_std
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

        # ── EMAs ──
        df["ema_fast"] = df["Close"].ewm(span=self.cfg.ema_fast, adjust=False).mean()
        df["ema_slow"] = df["Close"].ewm(span=self.cfg.ema_slow, adjust=False).mean()

        # ── RSI ──
        df["rsi"] = self._rsi(df["Close"], self.cfg.rsi_period)

        # ── CMF ──
        df["cmf"] = self._cmf(df, self.cfg.cmf_period)

        # ── ATR ──
        df["atr"] = self._atr(df, self.cfg.atr_period)

        # ── Williams %R ──
        df["wr_14"] = self._williams_r(df, self.cfg.wr_period)

        # ── Régime HTF (resample 4H) ──
        df = self._compute_htf_regime(df)

        # ── SL levels ──
        df["swing_low"] = df["Low"].rolling(self.cfg.sl_swing_bars).min()
        df["swing_high"] = df["High"].rolling(self.cfg.sl_swing_bars).max()

        return df

    def generate_signals(
        self, df: pd.DataFrame, instrument: str = ""
    ) -> list[tuple[int, Signal]]:
        """Génère les signaux à partir du DataFrame préparé.

        Retourne list[tuple[bar_index, Signal]] — même interface que
        TrendSignalGenerator pour usage dans _generate_signals_from_cache.

        En backtest (live_mode=False) : boucle sur range(warmup, n-1),
          anti-lookahead strict, entrée simulée au OPEN de i+1.
        En live_mode=True : boucle sur range(warmup, n), inclut la
          dernière bougie, entrée au close courant.
        """
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
        """Vérifie si la bougie courante génère un signal BB excess."""
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
                tv_close=close,
                tv_open=row["Open"],
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
                tv_close=close,
                tv_open=row["Open"],
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

    def _compute_sl(
        self, row: pd.Series, df: pd.DataFrame, idx: int, side: Side
    ) -> float:
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

    # ── Indicateurs ──────────────────────────────────────────────────

    @staticmethod
    def _rsi(series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - 100 / (1 + rs)

    @staticmethod
    def _cmf(df: pd.DataFrame, period: int) -> pd.Series:
        hl_range = df["High"] - df["Low"]
        hl_range = hl_range.replace(0, np.nan)
        mf_mult = ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / hl_range
        mf_volume = mf_mult * df.get("Volume", pd.Series(1, index=df.index))
        return mf_volume.rolling(period).sum() / df.get("Volume", pd.Series(1, index=df.index)).rolling(period).sum()

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        high = df["High"]
        low = df["Low"]
        close = df["Close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    @staticmethod
    def _williams_r(df: pd.DataFrame, period: int) -> pd.Series:
        hh = df["High"].rolling(period).max()
        ll = df["Low"].rolling(period).min()
        hl_range = hh - ll
        return ((hh - df["Close"]) / hl_range.replace(0, np.nan)) * -100

    def _compute_htf_regime(self, df: pd.DataFrame) -> pd.DataFrame:
        df_4h = df.resample("4h").agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
        }).dropna()

        if len(df_4h) < self.cfg.htf_ema_slow + 10:
            df["regime"] = "bull_range"
            df["htf_ema_fast_val"] = df["Close"]
            df["htf_ema_slow_val"] = df["Close"]
            df["htf_adx"] = 0
            return df

        df_4h["ema_fast"] = df_4h["Close"].ewm(span=self.cfg.htf_ema_fast, adjust=False).mean()
        df_4h["ema_slow"] = df_4h["Close"].ewm(span=self.cfg.htf_ema_slow, adjust=False).mean()
        df_4h["adx"] = self._adx(df_4h, self.cfg.htf_adx_period)

        def _regime(r):
            bull = r["ema_fast"] > r["ema_slow"]
            strong = r["adx"] > 25 if not pd.isna(r["adx"]) else False
            if bull and strong:   return "bull_trend"
            elif not bull and strong: return "bear_trend"
            elif bull:            return "bull_range"
            else:                 return "bear_range"

        df_4h["regime"] = df_4h.apply(_regime, axis=1)

        htf_cols = df_4h[["regime", "ema_fast", "ema_slow", "adx"]].copy()
        htf_cols.columns = ["regime", "htf_ema_fast_val", "htf_ema_slow_val", "htf_adx"]
        htf_reindexed = htf_cols.reindex(df.index, method="ffill")

        df["regime"] = htf_reindexed["regime"].fillna("bull_range")
        df["htf_ema_fast_val"] = htf_reindexed["htf_ema_fast_val"].fillna(df["Close"])
        df["htf_ema_slow_val"] = htf_reindexed["htf_ema_slow_val"].fillna(df["Close"])
        df["htf_adx"] = htf_reindexed["htf_adx"].fillna(0)

        return df

    @staticmethod
    def _adx(df: pd.DataFrame, period: int) -> pd.Series:
        high = df["High"]
        low = df["Low"]
        close = df["Close"]

        plus_dm = high.diff().clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)

        mask = plus_dm <= minus_dm
        plus_dm[mask] = 0
        minus_dm[~mask] = 0

        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)

        atr = tr.ewm(span=period, adjust=False).mean()
        plus_di  = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
        minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)

        dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
        return dx.ewm(span=period, adjust=False).mean()
