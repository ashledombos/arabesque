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
from arabesque.backtest.signal_labeler import label_trend_signal
from arabesque.indicators import (
    compute_rsi, compute_atr, compute_adx,
    compute_bollinger, compute_cmf, compute_williams_r,
    compute_ema, compute_htf_regime,
)


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

        Utilise arabesque/indicators.py pour les indicateurs communs.

        Colonnes standard (partagées avec mean-reversion) :
            bb_mid, bb_lower, bb_upper, bb_width, ema_fast, ema_slow,
            rsi, cmf, atr, wr_14, swing_low, swing_high,
            regime, htf_ema_fast, htf_ema_slow, htf_adx

        Colonnes spécifiques trend :
            adx, adx_rising, squeeze, recent_squeeze, bb_expanding
        """
        df = df.copy()

        bb = compute_bollinger(df, self.cfg.bb_period, self.cfg.bb_std)
        df["bb_mid"]   = bb["mid"]
        df["bb_lower"] = bb["lower"]
        df["bb_upper"] = bb["upper"]
        df["bb_width"] = bb["width"]

        df["ema_fast"] = compute_ema(df["Close"], self.cfg.ema_fast)
        df["ema_slow"] = compute_ema(df["Close"], self.cfg.ema_slow)
        df["rsi"]      = compute_rsi(df["Close"], self.cfg.rsi_period)
        df["cmf"]      = compute_cmf(df, self.cfg.cmf_period)
        df["atr"]      = compute_atr(df, self.cfg.atr_period)
        df["wr_14"]    = compute_williams_r(df, self.cfg.wr_period)
        df["adx"]      = compute_adx(df, self.cfg.atr_period)

        df["swing_low"]  = df["Low"].rolling(10, center=False).min()
        df["swing_high"] = df["High"].rolling(10, center=False).max()

        df = compute_htf_regime(
            df,
            ema_fast=self.cfg.htf_ema_fast,
            ema_slow=self.cfg.htf_ema_slow,
            adx_period=self.cfg.htf_adx_period,
        )

        # ── Indicateurs spécifiques trend ──────────────────────────────────

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

        # ADX rising : ADX en hausse N barres consécutives
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

            sig = Signal(
                instrument=instrument,
                side=Side.LONG,
                timeframe="1h",
                close=close,
                open_=row["Open"],
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
            return label_trend_signal(sig, df, idx)

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

            sig = Signal(
                instrument=instrument,
                side=Side.SHORT,
                timeframe="1h",
                close=close,
                open_=row["Open"],
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
            return label_trend_signal(sig, df, idx)

        return None

    # ── Indicator computation (mêmes formules que mean-reversion) ────
