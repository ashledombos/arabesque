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

Anti-lookahead : le signal est émis sur la bougie CONFIRMÉE (index i),
l'entrée est simulée au OPEN de la bougie suivante (i+1).
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
    sl_swing_bars: int = 10          # Lookback pour swing low/high
    min_sl_atr: float = 0.8         # SL minimum = 0.8 * ATR (évite les R minuscules)


class BacktestSignalGenerator:
    """Génère des signaux BB excess à partir de données OHLC."""

    def __init__(self, config: SignalGenConfig | None = None):
        self.cfg = config or SignalGenConfig()

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calcule tous les indicateurs sur le DataFrame.

        Retourne le DataFrame enrichi avec les colonnes indicateurs.
        ATTENTION : ne modifie pas le df original.
        """
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

        # ── Williams %R (VRAI, pas percentrank * -1) ──
        df["wr_14"] = self._williams_r(df, self.cfg.wr_period)

        # ── Régime HTF (resample 4H) ──
        df = self._compute_htf_regime(df)

        # ── SL levels ──
        df["swing_low"] = df["Low"].rolling(self.cfg.sl_swing_bars).min()
        df["swing_high"] = df["High"].rolling(self.cfg.sl_swing_bars).max()

        return df

    def generate_signals(self, df: pd.DataFrame, instrument: str = "") -> list[Signal]:
        """Génère les signaux à partir du DataFrame préparé.

        ANTI-LOOKAHEAD : le signal à l'index i utilise les données de i et avant.
        L'entrée se fait au OPEN de i+1 (simulée par le runner).
        """
        signals = []

        # Démarrer après warmup de tous les indicateurs
        warmup = max(self.cfg.ema_slow, self.cfg.bb_period, self.cfg.atr_period) + 10

        for i in range(warmup, len(df) - 1):  # -1 car on a besoin de la bougie suivante
            row = df.iloc[i]

            # Skip si indicateurs manquants
            if pd.isna(row.get("atr")) or row["atr"] <= 0:
                continue
            if pd.isna(row.get("bb_lower")):
                continue

            signal = self._check_bb_excess(row, df, i, instrument)
            if signal is not None:
                signals.append(signal)

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

        # ── BB width filter (pas de signal en squeeze) ──
        if row["bb_width"] < self.cfg.min_bb_width:
            return None

        # ── LONG : close < BB lower ──
        if close < bb_lower:
            # Filtre RSI : oversold
            if row["rsi"] > self.cfg.rsi_oversold:
                return None

            # Filtre régime : pas en bear trend fort
            regime = row.get("regime", "bull_range")
            if regime == "bear_trend":
                return None

            # SL
            sl = self._compute_sl(row, df, idx, Side.LONG)
            if sl <= 0 or sl >= close:
                # SL invalide, fallback ATR
                sl = close - self.cfg.sl_atr_mult * atr

            # TP = BB mid (retour à la moyenne)
            tp = bb_mid

            # R:R check
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
                timestamp=df.index[idx],
            )
            return label_trend_signal(sig, df, idx)

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
                timestamp=df.index[idx],
            )
            return label_trend_signal(sig, df, idx)

        return None

    def _compute_sl(
        self, row: pd.Series, df: pd.DataFrame, idx: int, side: Side
    ) -> float:
        """Calcule le SL basé sur swing low/high ou ATR.

        Enforce un R minimum de min_sl_atr * ATR pour éviter les SL absurdes.
        """
        atr = row["atr"]
        close = row["Close"]
        min_dist = self.cfg.min_sl_atr * atr

        if self.cfg.sl_method == "swing":
            if side == Side.LONG:
                sl = row.get("swing_low", 0)
                if sl > 0:
                    sl -= 0.2 * atr
                # Enforce minimum distance
                if sl <= 0 or (close - sl) < min_dist:
                    sl = close - min_dist
            else:
                sl = row.get("swing_high", 0)
                if sl > 0:
                    sl += 0.2 * atr
                if sl <= 0 or (sl - close) < min_dist:
                    sl = close + min_dist
            return sl

        # Fallback ATR
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
        """Chaikin Money Flow."""
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
        """Vrai Williams %R : (HH - close) / (HH - LL) * -100."""
        hh = df["High"].rolling(period).max()
        ll = df["Low"].rolling(period).min()
        hl_range = hh - ll
        return ((hh - df["Close"]) / hl_range.replace(0, np.nan)) * -100

    def _compute_htf_regime(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calcule le régime HTF (4H) en resampleant les données 1H.

        Régime :
        - bull_trend : EMA fast > EMA slow AND ADX > 25
        - bear_trend : EMA fast < EMA slow AND ADX > 25
        - bull_range : EMA fast > EMA slow AND ADX <= 25
        - bear_range : EMA fast < EMA slow AND ADX <= 25
        - squeeze : BB width < threshold (déjà géré par le guard)
        """
        # Resample à 4H
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

        # EMAs 4H
        df_4h["ema_fast"] = df_4h["Close"].ewm(span=self.cfg.htf_ema_fast, adjust=False).mean()
        df_4h["ema_slow"] = df_4h["Close"].ewm(span=self.cfg.htf_ema_slow, adjust=False).mean()

        # ADX 4H
        df_4h["adx"] = self._adx(df_4h, self.cfg.htf_adx_period)

        # Régime
        def _regime(r):
            bull = r["ema_fast"] > r["ema_slow"]
            strong = r["adx"] > 25 if not pd.isna(r["adx"]) else False
            if bull and strong:
                return "bull_trend"
            elif not bull and strong:
                return "bear_trend"
            elif bull:
                return "bull_range"
            else:
                return "bear_range"

        df_4h["regime"] = df_4h.apply(_regime, axis=1)

        # Forward-fill vers 1H
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
        """Average Directional Index."""
        high = df["High"]
        low = df["Low"]
        close = df["Close"]

        plus_dm = high.diff().clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)

        # Si +DM <= -DM, +DM = 0 et vice versa
        mask = plus_dm <= minus_dm
        plus_dm[mask] = 0
        minus_dm[~mask] = 0

        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)

        atr = tr.ewm(span=period, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
        minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)

        dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
        adx = dx.ewm(span=period, adjust=False).mean()
        return adx
