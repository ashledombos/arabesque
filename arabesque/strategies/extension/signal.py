"""
Arabesque — Stratégie Extension (Trend-Following H1).

Fusionne l'ancien backtest/signal_gen_trend.py et backtest/signal_labeler.py
en un seul fichier. C'est l'implémentation unique utilisée par le backtest
ET le moteur live — aucune divergence possible.

Détecte les breakouts après un squeeze de Bollinger :
1. BB width tombe en dessous d'un seuil (squeeze)
2. BB width commence à s'étendre (expansion)
3. Le prix casse au-dessus/en-dessous des BB avec confirmation ADX

Même interface attendue par execution/backtest.py, execution/live.py,
execution/dryrun.py et execution/bar_aggregator.py :
  - prepare(df) → DataFrame enrichi
  - generate_signals(df, instrument) → list[(bar_index, Signal)]
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from arabesque.core.models import Signal, Side
from arabesque.modules.indicators import (
    compute_rsi, compute_atr, compute_adx,
    compute_bollinger, compute_cmf, compute_williams_r,
    compute_ema, compute_htf_regime,
)


# ── Seuils de labeling ────────────────────────────────────────────────────────
ADX_STRONG = 30.0


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class ExtensionConfig:
    """Paramètres du générateur de signaux Extension (trend-following)."""
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

    # Régime HTF
    htf_ema_fast: int = 12
    htf_ema_slow: int = 26
    htf_adx_period: int = 14

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
    tp_rr_mult: float = 2.0

    # R:R minimum
    min_rr: float = 1.0

    # Volume confirmation
    cmf_confirm: bool = True            # CMF > 0 pour LONG, < 0 pour SHORT


# ── Signal Generator ─────────────────────────────────────────────────────────

class ExtensionSignalGenerator:
    """
    Génère des signaux trend (BB squeeze → expansion → breakout).

    C'est LA source de vérité pour les signaux de la stratégie Extension.
    Utilisée à l'identique par :
      - execution/backtest.py  (replay historique bar-by-bar)
      - execution/dryrun.py    (replay parquet en temps quasi-réel)
      - execution/bar_aggregator.py  (live cTrader)

    Interface :
      prepare(df) → enrichit le DataFrame avec tous les indicateurs
      generate_signals(df, instrument) → list[(bar_index, Signal)]
    """

    def __init__(self, config: ExtensionConfig | None = None):
        self.cfg = config or ExtensionConfig()

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calcule les indicateurs sur le DataFrame.

        Colonnes standard :
            bb_mid, bb_lower, bb_upper, bb_width, ema_fast, ema_slow,
            rsi, cmf, atr, wr_14, swing_low, swing_high,
            regime, htf_ema_fast, htf_ema_slow, htf_adx, adx

        Colonnes spécifiques Extension :
            squeeze, recent_squeeze, bb_expanding, adx_rising, rsi_div
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

        # Swing levels basés sur pivots confirmés (pour trailing Dow Theory)
        from arabesque.modules.indicators import compute_swing_levels
        swings = compute_swing_levels(df, pivot_window=5)
        df["last_swing_low"] = swings["last_swing_low"]
        df["last_swing_high"] = swings["last_swing_high"]

        df = compute_htf_regime(
            df,
            ema_fast=self.cfg.htf_ema_fast,
            ema_slow=self.cfg.htf_ema_slow,
            adx_period=self.cfg.htf_adx_period,
        )

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

        # Divergence RSI (shadow filter — log only, pas bloquant)
        div_lookback = 5
        price_chg = df["Close"] - df["Close"].shift(div_lookback)
        rsi_chg = df["rsi"] - df["rsi"].shift(div_lookback)
        df["rsi_div"] = 0
        df.loc[(price_chg > 0) & (rsi_chg < 0), "rsi_div"] = -1
        df.loc[(price_chg < 0) & (rsi_chg > 0), "rsi_div"] = 1

        return df

    def generate_signals(
        self, df: pd.DataFrame, instrument: str = "UNKNOWN"
    ) -> list[tuple[int, Signal]]:
        """Génère des signaux trend à partir d'un DataFrame préparé.

        Returns:
            Liste de (bar_index, Signal) — signal émis sur bougie confirmée.
            L'exécution (fill) se fait au OPEN de la bougie suivante (anti-lookahead).
        """
        signals = []
        n = len(df)

        for i in range(max(self.cfg.squeeze_lookback, 200), n):
            row = df.iloc[i]

            if pd.isna(row.get("atr", np.nan)) or row["atr"] <= 0:
                continue
            if pd.isna(row.get("bb_width", np.nan)):
                continue

            sig = self._check_breakout(row, df, i, instrument)
            if sig is not None:
                signals.append((i, sig))

        return signals

    # ── Signal detection ──────────────────────────────────────────────────────

    def _check_breakout(
        self, row: pd.Series, df: pd.DataFrame, idx: int, instrument: str
    ) -> Signal | None:
        """Vérifie si la bougie courante génère un signal breakout."""
        close    = row["Close"]
        bb_lower = row["bb_lower"]
        bb_upper = row["bb_upper"]
        bb_mid   = row["bb_mid"]
        atr      = row["atr"]

        # Préconditions communes
        if not row.get("recent_squeeze", False):
            return None
        if not row.get("bb_expanding", False):
            return None

        adx = row.get("adx", 0)
        if adx < self.cfg.adx_trend_min:
            return None
        if adx < 30 and not row.get("adx_rising", False):
            return None

        regime = row.get("regime", "bull_range")

        # ── LONG breakout ──
        if close > bb_upper + self.cfg.breakout_margin:
            if regime == "bear_trend":
                return None
            ema_f, ema_s = row.get("ema_fast", 0), row.get("ema_slow", 0)
            if ema_f > 0 and ema_s > 0 and ema_f < ema_s:
                return None
            if self.cfg.cmf_confirm and row.get("cmf", 0) < 0:
                return None

            sl = (bb_lower - 0.2 * atr) if self.cfg.sl_method == "opposite_bb" \
                 else (close - self.cfg.sl_atr_mult * atr)
            if sl >= close:
                sl = close - self.cfg.sl_atr_mult * atr

            risk_dist = abs(close - sl)
            if risk_dist <= 0:
                return None
            tp  = close + self.cfg.tp_rr_mult * risk_dist
            rr  = self.cfg.tp_rr_mult
            if rr < self.cfg.min_rr:
                return None

            sig = self._build_signal(
                instrument, Side.LONG, close, row, df, idx,
                sl, tp, rr, atr, bb_lower, bb_mid, bb_upper, regime,
            )
            return self._label(sig, df, idx)

        # ── SHORT breakout ──
        if close < bb_lower - self.cfg.breakout_margin:
            if regime == "bull_trend":
                return None
            ema_f, ema_s = row.get("ema_fast", 0), row.get("ema_slow", 0)
            if ema_f > 0 and ema_s > 0 and ema_f > ema_s:
                return None
            if self.cfg.cmf_confirm and row.get("cmf", 0) > 0:
                return None

            sl = (bb_upper + 0.2 * atr) if self.cfg.sl_method == "opposite_bb" \
                 else (close + self.cfg.sl_atr_mult * atr)
            if sl <= close:
                sl = close + self.cfg.sl_atr_mult * atr

            risk_dist = abs(sl - close)
            if risk_dist <= 0:
                return None
            tp  = close - self.cfg.tp_rr_mult * risk_dist
            rr  = self.cfg.tp_rr_mult
            if rr < self.cfg.min_rr:
                return None

            sig = self._build_signal(
                instrument, Side.SHORT, close, row, df, idx,
                sl, tp, rr, atr, bb_lower, bb_mid, bb_upper, regime,
            )
            return self._label(sig, df, idx)

        return None

    def _build_signal(
        self, instrument, side, close, row, df, idx,
        sl, tp, rr, atr, bb_lower, bb_mid, bb_upper, regime,
    ) -> Signal:
        return Signal(
            instrument=instrument,
            side=side,
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
            rsi_div=int(row.get("rsi_div", 0)),
            ema200_ltf=row.get("ema_slow", 0),
            htf_ema_fast=row.get("htf_ema_fast", 0),
            htf_ema_slow=row.get("htf_ema_slow", 0),
            htf_adx=row.get("htf_adx", 0),
            regime=regime,
            max_spread_atr=0.3,
            rr=round(rr, 2),
            strategy_type="extension",
            timestamp=df.index[idx],
        )

    # ── Labeling (sub_type + label_factors) ───────────────────────────────────

    def _label(self, signal: Signal, df: pd.DataFrame, bar_idx: int) -> Signal:
        """Attache sub_type et label_factors au signal."""
        row = df.iloc[bar_idx]
        adx = row.get("adx", 0)
        is_strong = adx >= ADX_STRONG

        # Squeeze duration
        squeeze_duration = 0
        if "squeeze" in df.columns:
            for j in range(bar_idx - 1, max(0, bar_idx - 50), -1):
                if df.iloc[j].get("squeeze", False):
                    squeeze_duration += 1
                else:
                    break

        # Breakout strength
        atr = signal.atr
        if atr > 0:
            if signal.side == Side.LONG:
                breakout_strength = (signal.close - signal.bb_upper) / atr
            else:
                breakout_strength = (signal.bb_lower - signal.close) / atr
        else:
            breakout_strength = 0.0

        # CMF alignment
        cmf = signal.cmf
        cmf_aligned = (cmf > 0) if signal.side == Side.LONG else (cmf < 0)

        # Volume ratio
        volume = row.get("Volume", 0)
        volume_ratio = 0.0
        if volume > 0 and "Volume" in df.columns:
            start = max(0, bar_idx - 20)
            avg_vol = df["Volume"].iloc[start:bar_idx].mean()
            if avg_vol > 0:
                volume_ratio = volume / avg_vol

        signal.sub_type = "trend_strong" if is_strong else "trend_moderate"
        signal.label_factors = {
            "adx": round(adx, 1),
            "squeeze_duration": squeeze_duration,
            "breakout_strength": round(breakout_strength, 2),
            "cmf_aligned": cmf_aligned,
            "volume_ratio": round(volume_ratio, 2),
            "rr": signal.rr,
            "regime": signal.regime,
        }
        return signal


# ── Backward-compat alias (anciens noms utilisés dans bar_aggregator, engine) ──
# Permet à engine.py et bar_aggregator.py d'importer TrendSignalGenerator
# sans modification immédiate.
TrendSignalGenerator = ExtensionSignalGenerator
TrendSignalConfig = ExtensionConfig
