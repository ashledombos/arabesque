"""
Arabesque v2 — Trend Signal Generator.

Détecte les breakouts après un squeeze de Bollinger :
1. BB width tombe en dessous d'un seuil (squeeze)
2. BB width commence à s'étendre (expansion)
3. Le prix casse au-dessus/en-dessous des BB avec confirmation ADX

Complémentaire au mean-reversion :
- Mean-reversion : BB large → entry sur excès → TP = retour au milieu
- Trend : BB squeeze → expansion → entry sur breakout → TP trailing

Même interface que BacktestSignalGenerator pour interchangeabilité.
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from arabesque.models import Signal, Side


@dataclass
class TrendSignalConfig:
    """Paramètres du générateur de signaux trend."""
    # Bollinger Bands (mêmes que mean-reversion)
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

    # Régime HTF
    htf_ema_fast: int = 12
    htf_ema_slow: int = 26
    htf_adx_period: int = 14

    # ── Trend-specific ──

    # Squeeze detection
    squeeze_lookback: int = 100         # Fenêtre pour percentile BB width
    squeeze_pctile: float = 20.0        # BB width < percentile(20) = squeeze
    squeeze_memory: int = 10            # Signal valide si squeeze dans les N dernières barres

    # Expansion
    expansion_bars: int = 2             # BB width en hausse pendant N barres

    # ADX filter
    adx_trend_min: float = 20.0         # ADX minimum pour confirmer la tendance
    adx_rising_bars: int = 3            # ADX en hausse depuis N barres

    # Entry
    breakout_margin: float = 0.0        # Marge au-delà de la BB pour confirmer (0 = touche)

    # SL
    sl_method: str = "atr"              # "atr" ou "opposite_bb"
    sl_atr_mult: float = 1.5

    # TP (indicatif, le trailing gère la vraie sortie)
    tp_rr_mult: float = 2.0            # TP = entry + 2 * risk_distance

    # R:R minimum
    min_rr: float = 1.0                 # Trend a besoin de plus de R:R

    # Volume confirmation
    cmf_confirm: bool = True            # CMF > 0 pour LONG, < 0 pour SHORT


class TrendSignalGenerator:
    """Génère des signaux trend (BB squeeze → expansion → breakout).

    Même interface que BacktestSignalGenerator :
    - prepare(df) → enrichit le DataFrame
    - generate_signals(df, instrument) → list[(bar_index, Signal)]

    Les indicateurs sont les mêmes (BB, RSI, CMF, ATR, HTF regime)
    mais la logique de signal est inversée :
    - mean_reversion : entry QUAND close sort de la bande → retour au milieu
    - trend : entry QUAND close casse la bande APRÈS un squeeze → ride la tendance
    """

    def __init__(self, config: TrendSignalConfig | None = None):
        self.cfg = config or TrendSignalConfig()

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calcule les indicateurs sur le DataFrame.

        Même sortie que BacktestSignalGenerator.prepare() + colonnes trend :
        - squeeze: bool, True si BB width est en squeeze
        - recent_squeeze: bool, True si squeeze dans les N dernières barres
        - bb_expanding: bool, True si BB width en hausse depuis N barres
        - adx_rising: bool
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
        high = df["High"]
        low = df["Low"]
        prev_close = df["Close"].shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        df["atr"] = tr.rolling(self.cfg.atr_period).mean()

        # ── Williams %R ──
        df["wr_14"] = self._williams_r(df, self.cfg.wr_period)

        # ── Swing high/low ──
        df["swing_low"] = df["Low"].rolling(10, center=False).min()
        df["swing_high"] = df["High"].rolling(10, center=False).max()

        # ── ADX (sur 1H directement) ──
        df["adx"] = self._adx(df, self.cfg.atr_period)

        # ── HTF Regime (4H resample) ──
        df = self._compute_htf_regime(df)

        # ── Trend-specific indicators ──

        # Squeeze : BB width en-dessous du percentile seuil
        bb_pctile = df["bb_width"].rolling(
            self.cfg.squeeze_lookback, min_periods=20
        ).apply(lambda x: np.percentile(x, self.cfg.squeeze_pctile), raw=True)
        df["squeeze"] = df["bb_width"] <= bb_pctile

        # Recent squeeze : squeeze active dans les N dernières barres
        df["recent_squeeze"] = (
            df["squeeze"]
            .rolling(self.cfg.squeeze_memory, min_periods=1)
            .max()
            .astype(bool)
        )

        # BB expanding : bb_width en hausse N barres consécutives
        bb_diff = df["bb_width"].diff()
        df["bb_expanding"] = True
        for i in range(self.cfg.expansion_bars):
            df["bb_expanding"] = df["bb_expanding"] & (bb_diff.shift(i) > 0)

        # ADX rising
        adx_diff = df["adx"].diff()
        df["adx_rising"] = True
        for i in range(self.cfg.adx_rising_bars):
            df["adx_rising"] = df["adx_rising"] & (adx_diff.shift(i) > 0)

        return df

    def generate_signals(
        self, df: pd.DataFrame, instrument: str = "UNKNOWN"
    ) -> list[tuple[int, Signal]]:
        """Génère des signaux trend à partir d'un DataFrame préparé.

        Returns:
            Liste de (bar_index, Signal) — signal émis sur bougie confirmée.
        """
        signals = []
        n = len(df)

        for i in range(max(self.cfg.squeeze_lookback, 200), n):
            row = df.iloc[i]

            # Skip si données manquantes
            if pd.isna(row.get("atr", np.nan)) or row["atr"] <= 0:
                continue
            if pd.isna(row.get("bb_width", np.nan)):
                continue

            sig = self._check_trend_breakout(row, df, i, instrument)
            if sig is not None:
                signals.append((i, sig))

        return signals

    def _check_trend_breakout(
        self, row: pd.Series, df: pd.DataFrame, idx: int, instrument: str
    ) -> Signal | None:
        """Vérifie si la bougie courante génère un signal trend breakout."""
        close = row["Close"]
        bb_lower = row["bb_lower"]
        bb_upper = row["bb_upper"]
        bb_mid = row["bb_mid"]
        atr = row["atr"]

        # ── Préconditions ──

        # 1. Recent squeeze obligatoire
        if not row.get("recent_squeeze", False):
            return None

        # 2. BB en expansion
        if not row.get("bb_expanding", False):
            return None

        # 3. ADX confirme la tendance
        adx = row.get("adx", 0)
        if adx < self.cfg.adx_trend_min:
            return None

        # ADX en hausse (mais optionnel si ADX déjà fort)
        if adx < 30 and not row.get("adx_rising", False):
            return None

        regime = row.get("regime", "bull_range")

        # ── LONG breakout : close > BB upper ──
        if close > bb_upper + self.cfg.breakout_margin:
            # Regime filter : pas en bear_trend
            if regime == "bear_trend":
                return None

            # EMA confirmation : fast > slow (tendance haussière)
            ema_f = row.get("ema_fast", 0)
            ema_s = row.get("ema_slow", 0)
            if ema_f > 0 and ema_s > 0 and ema_f < ema_s:
                return None

            # CMF confirmation
            if self.cfg.cmf_confirm and row.get("cmf", 0) < 0:
                return None

            # SL
            if self.cfg.sl_method == "opposite_bb":
                sl = bb_lower - 0.2 * atr
            else:
                sl = close - self.cfg.sl_atr_mult * atr

            if sl >= close:
                sl = close - self.cfg.sl_atr_mult * atr

            # TP indicatif (trend : on veut que le trailing travaille)
            risk_dist = abs(close - sl)
            if risk_dist <= 0:
                return None
            tp = close + self.cfg.tp_rr_mult * risk_dist

            rr = self.cfg.tp_rr_mult  # R:R = tp_rr_mult par construction
            if rr < self.cfg.min_rr:
                return None

            return Signal(
                instrument=instrument,
                side=Side.LONG,
                timeframe="1h",
                tv_close=close,
                tv_open=row["Open"],
                sl=sl,
                tp_indicative=tp,
                atr=atr,
                rsi=row.get("rsi", 50),
                cmf=row.get("cmf", 0),
                bb_lower=bb_lower,
                bb_mid=bb_mid,
                bb_upper=bb_upper,
                bb_width=row.get("bb_width", 0),
                wr_14=row.get("wr_14", -50),
                ema200_ltf=row.get("ema_slow", 0),
                htf_ema_fast=row.get("htf_ema_fast_val", 0),
                htf_ema_slow=row.get("htf_ema_slow_val", 0),
                htf_adx=row.get("htf_adx", 0),
                regime=regime,
                max_spread_atr=0.3,
                rr=round(rr, 2),
                strategy_type="trend",
                timestamp=df.index[idx],
            )

        # ── SHORT breakout : close < BB lower ──
        if close < bb_lower - self.cfg.breakout_margin:
            if regime == "bull_trend":
                return None

            ema_f = row.get("ema_fast", 0)
            ema_s = row.get("ema_slow", 0)
            if ema_f > 0 and ema_s > 0 and ema_f > ema_s:
                return None

            if self.cfg.cmf_confirm and row.get("cmf", 0) > 0:
                return None

            if self.cfg.sl_method == "opposite_bb":
                sl = bb_upper + 0.2 * atr
            else:
                sl = close + self.cfg.sl_atr_mult * atr

            if sl <= close:
                sl = close + self.cfg.sl_atr_mult * atr

            risk_dist = abs(sl - close)
            if risk_dist <= 0:
                return None
            tp = close - self.cfg.tp_rr_mult * risk_dist

            rr = self.cfg.tp_rr_mult
            if rr < self.cfg.min_rr:
                return None

            return Signal(
                instrument=instrument,
                side=Side.SHORT,
                timeframe="1h",
                tv_close=close,
                tv_open=row["Open"],
                sl=sl,
                tp_indicative=tp,
                atr=atr,
                rsi=row.get("rsi", 50),
                cmf=row.get("cmf", 0),
                bb_lower=bb_lower,
                bb_mid=bb_mid,
                bb_upper=bb_upper,
                bb_width=row.get("bb_width", 0),
                wr_14=row.get("wr_14", -50),
                ema200_ltf=row.get("ema_slow", 0),
                htf_ema_fast=row.get("htf_ema_fast_val", 0),
                htf_ema_slow=row.get("htf_ema_slow_val", 0),
                htf_adx=row.get("htf_adx", 0),
                regime=regime,
                max_spread_atr=0.3,
                rr=round(rr, 2),
                strategy_type="trend",
                timestamp=df.index[idx],
            )

        return None

    # ── Indicator computation (mêmes formules que mean-reversion) ────

    def _rsi(self, series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - 100 / (1 + rs)

    def _cmf(self, df: pd.DataFrame, period: int) -> pd.Series:
        hl_range = df["High"] - df["Low"]
        hl_range = hl_range.replace(0, np.nan)
        mfm = ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / hl_range
        vol = df.get("Volume", pd.Series(1, index=df.index))
        mfv = mfm * vol
        return mfv.rolling(period).sum() / vol.rolling(period).sum()

    def _williams_r(self, df: pd.DataFrame, period: int) -> pd.Series:
        hh = df["High"].rolling(period).max()
        ll = df["Low"].rolling(period).min()
        denom = hh - ll
        denom = denom.replace(0, np.nan)
        return ((hh - df["Close"]) / denom) * -100

    def _adx(self, df: pd.DataFrame, period: int) -> pd.Series:
        """ADX sur 1H directement."""
        high = df["High"]
        low = df["Low"]
        close = df["Close"]

        plus_dm = high.diff()
        minus_dm = -low.diff()

        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr_smooth = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr_smooth
        minus_di = 100 * minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr_smooth

        di_sum = plus_di + minus_di
        di_sum = di_sum.replace(0, np.nan)
        dx = 100 * (plus_di - minus_di).abs() / di_sum
        adx = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        return adx

    def _compute_htf_regime(self, df: pd.DataFrame) -> pd.DataFrame:
        """Régime HTF via 4H resample (même logique que mean-reversion)."""
        # Créer un index temporel si pas présent
        if not isinstance(df.index, pd.DatetimeIndex):
            df["regime"] = "bull_range"
            df["htf_ema_fast_val"] = df["ema_fast"]
            df["htf_ema_slow_val"] = df["ema_slow"]
            df["htf_adx"] = df.get("adx", 0)
            return df

        try:
            htf = df.resample("4h").agg({
                "Open": "first", "High": "max", "Low": "min", "Close": "last",
                "Volume": "sum",
            }).dropna()

            if len(htf) < max(self.cfg.htf_ema_fast, self.cfg.htf_ema_slow) + 10:
                df["regime"] = "bull_range"
                df["htf_ema_fast_val"] = 0
                df["htf_ema_slow_val"] = 0
                df["htf_adx"] = 0
                return df

            htf_ema_f = htf["Close"].ewm(span=self.cfg.htf_ema_fast, adjust=False).mean()
            htf_ema_s = htf["Close"].ewm(span=self.cfg.htf_ema_slow, adjust=False).mean()
            htf_adx = self._adx(htf, self.cfg.htf_adx_period)

            def classify(row_idx):
                ema_f = htf_ema_f.iloc[row_idx]
                ema_s = htf_ema_s.iloc[row_idx]
                adx_val = htf_adx.iloc[row_idx] if row_idx < len(htf_adx) else 0
                bullish = ema_f > ema_s
                trending = adx_val > 25
                if bullish and trending:
                    return "bull_trend"
                elif bullish:
                    return "bull_range"
                elif trending:
                    return "bear_trend"
                else:
                    return "bear_range"

            htf_regime = pd.Series(
                [classify(i) for i in range(len(htf))],
                index=htf.index
            )
            htf_ema_f_series = htf_ema_f
            htf_ema_s_series = htf_ema_s

            df["regime"] = htf_regime.reindex(df.index, method="ffill")
            df["htf_ema_fast_val"] = htf_ema_f_series.reindex(df.index, method="ffill")
            df["htf_ema_slow_val"] = htf_ema_s_series.reindex(df.index, method="ffill")
            df["htf_adx"] = htf_adx.reindex(df.index, method="ffill")

        except Exception:
            df["regime"] = "bull_range"
            df["htf_ema_fast_val"] = 0
            df["htf_ema_slow_val"] = 0
            df["htf_adx"] = 0

        return df
