"""
arabesque/strategies/renverse/signal.py
=======================================
Renversé — Liquidity Sweep + Structure Shift + FVG Retrace

Stratégie inspirée des concepts ICT/SMC, implémentée comme un système
entièrement mécanique et déterministe, sans règle subjective.

Séquence de setup :
    1. Compression : BB width sous un percentile roulant (contexte)
    2. Liquidity sweep : prix casse un swing high/low puis rejette (mèche)
    3. Structure shift (CHOCH) : cassure du swing opposé confirme le retournement
    4. FVG retrace : entrée sur pullback dans un Fair Value Gap

Chaque composant est activable/désactivable pour tests d'ablation.
HTF bias : EMA200 sur H4 (resampleé depuis H1).

Convention anti-lookahead :
    Signal bougie i → fill au OPEN de bougie i+1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from arabesque.core.models import Side, Signal
from arabesque.modules.indicators import (
    compute_atr,
    compute_bollinger,
    compute_ema,
)

logger = logging.getLogger(__name__)


# ─── Configuration ──────────────────────────────────────────────────────────

@dataclass
class RenverseConfig:
    """Paramètres de la stratégie Renversé.

    Tous les paramètres sont explicites et documentés.
    Les flags d'ablation permettent de tester chaque composant isolément.
    """

    # ── Compression (contexte) ──────────────────────────────────────────
    bb_period: int = 20
    bb_std: float = 2.0
    squeeze_percentile: float = 25.0    # BB width < ce percentile = squeeze
    squeeze_lookback: int = 100         # fenêtre roulante pour le percentile

    # ── Swing detection ─────────────────────────────────────────────────
    swing_period: int = 20              # lookback pour swing high/low

    # ── Liquidity sweep ─────────────────────────────────────────────────
    min_wick_ratio: float = 0.5         # mèche de sweep >= ratio × candle range

    # ── Structure shift (CHOCH) ─────────────────────────────────────────
    choch_max_bars: int = 15            # max barres après sweep pour trouver CHOCH

    # ── FVG (Fair Value Gap) ────────────────────────────────────────────
    min_fvg_atr: float = 0.2           # taille FVG >= ratio × ATR
    fvg_max_bars: int = 15             # max barres après CHOCH pour trouver FVG
    retrace_max_bars: int = 20         # max barres pour attendre le retrace

    # ── HTF bias ────────────────────────────────────────────────────────
    htf_ema_period: int = 200
    htf_resample: str = "4h"

    # ── SL / TP ─────────────────────────────────────────────────────────
    sl_buffer_atr: float = 0.1         # SL = sweep extreme ± buffer × ATR
    rr_tp: float = 2.0                 # TP = RR × SL distance
    max_sl_atr: float = 3.0            # rejeter si SL distance > max × ATR

    # ── Ablation flags ──────────────────────────────────────────────────
    # Ablation tests (20 mois, XAUUSD+BTCUSD) ont montré :
    #   - compression trop restrictif (tue la fréquence sans améliorer WR)
    #   - CHOCH trop restrictif (réduit trades de 61→18 sans gain de WR)
    #   - FVG retrace ajoute un filtre utile
    #   - HTF bias filtre pertinent
    # Defaults = meilleur compromis fréquence/edge
    require_compression: bool = False
    require_choch: bool = False
    require_fvg_retrace: bool = True
    require_htf_bias: bool = True


# ─── Internal state ─────────────────────────────────────────────────────────

@dataclass
class _PendingSetup:
    """Suivi interne d'un setup en cours de formation."""
    direction: int            # +1 = bullish (long), -1 = bearish (short)
    sweep_bar: int            # index de la barre de sweep
    sweep_extreme: float      # Low[sweep] pour bull, High[sweep] pour bear
    choch_target: float       # niveau que le CHOCH doit casser
    state: str = "choch"      # "choch" → "fvg" → "retrace" → "done"
    choch_bar: int = -1
    fvg_low: float = 0.0
    fvg_high: float = 0.0
    fvg_bar: int = -1
    labels: list = field(default_factory=list)


# ─── Signal Generator ───────────────────────────────────────────────────────

class RenverseSignalGenerator:
    """Liquidity sweep reversal — entrées H1 avec biais H4.

    Usage
    -----
    >>> sg = RenverseSignalGenerator(RenverseConfig())
    >>> df = sg.prepare(df_h1)
    >>> signals = sg.generate_signals(df, instrument="XAUUSD")
    """

    def __init__(self, cfg: RenverseConfig | None = None):
        self.cfg = cfg or RenverseConfig()

    # ── prepare ──────────────────────────────────────────────────────────

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ajoute les colonnes indicateurs nécessaires.

        Colonnes ajoutées :
            bb_width   : largeur BB normalisée
            squeeze    : bool, True si BB width < percentile roulant
            atr        : ATR 14 périodes
            swing_high : plus haut des swing_period dernières barres (shift 1)
            swing_low  : plus bas des swing_period dernières barres (shift 1)
            htf_ema    : EMA200 resampleée depuis H4 (forward-filled)
        """
        df = df.copy()
        c = self.cfg

        # BB compression
        bb = compute_bollinger(df, period=c.bb_period, std_mult=c.bb_std)
        df["bb_width"] = bb["width"]
        df["squeeze"] = df["bb_width"] < df["bb_width"].rolling(
            c.squeeze_lookback, min_periods=c.squeeze_lookback
        ).quantile(c.squeeze_percentile / 100)

        # ATR
        df["atr"] = compute_atr(df, period=14)

        # Swing high/low (rolling, shift 1 pour anti-lookahead :
        # à la barre i, swing_high = max(High) des barres [i-period, i-1])
        df["swing_high"] = df["High"].rolling(
            c.swing_period, min_periods=c.swing_period
        ).max().shift(1)
        df["swing_low"] = df["Low"].rolling(
            c.swing_period, min_periods=c.swing_period
        ).min().shift(1)

        # H4 EMA200 bias (resample H1 → H4)
        if c.require_htf_bias:
            try:
                df_htf = df.resample(c.htf_resample).agg({
                    "Open": "first", "High": "max", "Low": "min",
                    "Close": "last", "Volume": "sum",
                }).dropna()
                if len(df_htf) >= c.htf_ema_period + 10:
                    htf_ema = compute_ema(df_htf["Close"], span=c.htf_ema_period)
                    df["htf_ema"] = htf_ema.reindex(df.index, method="ffill")
                else:
                    df["htf_ema"] = df["Close"]
            except Exception:
                df["htf_ema"] = df["Close"]
        else:
            df["htf_ema"] = np.nan

        return df

    # ── generate_signals ─────────────────────────────────────────────────

    def generate_signals(
        self, df: pd.DataFrame, instrument: str = "UNKNOWN"
    ) -> list[tuple[int, Signal]]:
        """Génère les signaux Renversé.

        Scan forward avec suivi d'état : chaque sweep crée un setup
        qui progresse à travers les états choch → fvg → retrace → signal.
        """
        signals: list[tuple[int, Signal]] = []
        c = self.cfg
        setups: list[_PendingSetup] = []

        warmup = max(c.bb_period, c.swing_period, c.squeeze_lookback) + 10
        max_lifetime = c.choch_max_bars + c.fvg_max_bars + c.retrace_max_bars

        High = df["High"]
        Low = df["Low"]
        Open = df["Open"]
        Close = df["Close"]
        atr = df["atr"]
        squeeze = df.get("squeeze", pd.Series(True, index=df.index))
        swing_high = df["swing_high"]
        swing_low = df["swing_low"]
        htf_ema = df.get("htf_ema", pd.Series(np.nan, index=df.index))

        for i in range(warmup, len(df) - 1):
            # Expirer les setups trop anciens
            setups = [s for s in setups if i - s.sweep_bar <= max_lifetime]

            atr_val = atr.iloc[i]
            if pd.isna(atr_val) or atr_val <= 0:
                continue

            sh = swing_high.iloc[i]
            sl = swing_low.iloc[i]
            if pd.isna(sh) or pd.isna(sl):
                continue

            # ── 1. Détecter de nouveaux sweeps ──────────────────────────
            sweep = self._detect_sweep(
                High.iloc[i], Low.iloc[i], Close.iloc[i], Open.iloc[i],
                sh, sl, i,
            )
            if sweep:
                # Vérifier le contexte compression
                if c.require_compression and not squeeze.iloc[i]:
                    sweep = None

                # Vérifier le biais HTF
                if sweep and c.require_htf_bias:
                    htf_val = htf_ema.iloc[i]
                    if not pd.isna(htf_val):
                        if sweep.direction == 1 and Close.iloc[i] < htf_val:
                            sweep = None
                        elif sweep.direction == -1 and Close.iloc[i] > htf_val:
                            sweep = None

            if sweep:
                setups.append(sweep)

            # ── 2. Faire progresser les setups existants ────────────────
            completed = []
            for setup in setups:
                if setup.state == "done":
                    completed.append(setup)
                    continue

                advanced = self._advance_setup(
                    df, i, setup, atr_val, instrument, signals
                )
                if setup.state == "done":
                    completed.append(setup)

            for s in completed:
                if s in setups:
                    setups.remove(s)

        return signals

    # ── State machine ────────────────────────────────────────────────────

    def _advance_setup(
        self, df: pd.DataFrame, i: int, setup: _PendingSetup,
        atr_val: float, instrument: str,
        signals: list[tuple[int, Signal]],
    ) -> None:
        """Fait avancer un setup d'un pas dans la machine à états."""
        c = self.cfg
        close = df["Close"].iloc[i]

        if setup.state == "choch":
            if self._check_choch(close, setup):
                setup.state = "fvg"
                setup.choch_bar = i
                setup.labels.append(
                    f"renverse_{'bull' if setup.direction == 1 else 'bear'}_choch"
                )
            elif not c.require_choch:
                # Ablation : skip le CHOCH, aller directement au FVG
                setup.state = "fvg"
                setup.choch_bar = i
            elif i - setup.sweep_bar > c.choch_max_bars:
                setup.state = "done"

        elif setup.state == "fvg":
            ref_bar = setup.choch_bar if setup.choch_bar >= 0 else setup.sweep_bar
            fvg = self._detect_fvg(df, i, setup.direction, atr_val)
            if fvg:
                setup.fvg_low, setup.fvg_high = fvg
                setup.fvg_bar = i
                setup.labels.append(
                    f"renverse_{'bull' if setup.direction == 1 else 'bear'}_fvg"
                )
                if c.require_fvg_retrace:
                    setup.state = "retrace"
                else:
                    # Ablation : entrée directe sur FVG sans attendre le retrace
                    sig = self._make_signal(df, i, setup, instrument, atr_val)
                    if sig:
                        signals.append((i, sig))
                    setup.state = "done"
            elif i - ref_bar > c.fvg_max_bars:
                if not c.require_fvg_retrace:
                    # Ablation : entrée sans FVG du tout
                    sig = self._make_signal(df, i, setup, instrument, atr_val)
                    if sig:
                        signals.append((i, sig))
                setup.state = "done"

        elif setup.state == "retrace":
            if self._check_retrace(df["High"].iloc[i], df["Low"].iloc[i], setup):
                setup.labels.append(
                    f"renverse_{'bull' if setup.direction == 1 else 'bear'}_fvg_retrace"
                )
                sig = self._make_signal(df, i, setup, instrument, atr_val)
                if sig:
                    signals.append((i, sig))
                setup.state = "done"
            elif i - setup.fvg_bar > c.retrace_max_bars:
                setup.state = "done"

    # ── Brick detection helpers ──────────────────────────────────────────

    def _detect_sweep(
        self, high: float, low: float, close: float, open_: float,
        swing_high: float, swing_low: float, bar_idx: int,
    ) -> _PendingSetup | None:
        """Détecte un sweep de liquidité.

        Sweep haussier (bull) : Low casse sous swing_low, mais Close reste au-dessus.
            → La mèche basse a balayé les stops, le prix rejette et remonte.
        Sweep baissier (bear) : High casse au-dessus swing_high, mais Close reste en-dessous.
            → La mèche haute a balayé les stops, le prix rejette et redescend.
        """
        candle_range = high - low
        if candle_range <= 0:
            return None

        min_wick = self.cfg.min_wick_ratio * candle_range

        # Sweep haussier : Low < swing_low, Close > swing_low, longue mèche basse
        if low < swing_low and close > swing_low:
            lower_wick = min(open_, close) - low
            if lower_wick >= min_wick:
                return _PendingSetup(
                    direction=1,
                    sweep_bar=bar_idx,
                    sweep_extreme=low,
                    choch_target=swing_high,
                    labels=["renverse_bull_sweep"],
                )

        # Sweep baissier : High > swing_high, Close < swing_high, longue mèche haute
        if high > swing_high and close < swing_high:
            upper_wick = high - max(open_, close)
            if upper_wick >= min_wick:
                return _PendingSetup(
                    direction=-1,
                    sweep_bar=bar_idx,
                    sweep_extreme=high,
                    choch_target=swing_low,
                    labels=["renverse_bear_sweep"],
                )

        return None

    def _check_choch(self, close: float, setup: _PendingSetup) -> bool:
        """Vérifie un Change of Character (cassure de structure).

        Après un sweep bullish (balayage sous les lows) :
            CHOCH = close casse au-dessus du swing high → confirme retournement haussier.
        Après un sweep bearish (balayage au-dessus des highs) :
            CHOCH = close casse en-dessous du swing low → confirme retournement baissier.
        """
        if setup.direction == 1:
            return close > setup.choch_target
        else:
            return close < setup.choch_target

    def _detect_fvg(
        self, df: pd.DataFrame, i: int, direction: int, atr: float,
    ) -> tuple[float, float] | None:
        """Détecte un Fair Value Gap sur la barre i (pattern 3 barres).

        Bullish FVG : High[i-2] < Low[i]  → gap haussier entre ces niveaux
        Bearish FVG : Low[i-2] > High[i]  → gap baissier entre ces niveaux

        Retourne (fvg_low, fvg_high) ou None.
        """
        if i < 2:
            return None

        high_2 = df["High"].iloc[i - 2]
        low_0 = df["Low"].iloc[i]
        low_2 = df["Low"].iloc[i - 2]
        high_0 = df["High"].iloc[i]

        min_size = self.cfg.min_fvg_atr * atr

        if direction == 1:
            # Bullish FVG : gap entre High[i-2] et Low[i]
            gap = low_0 - high_2
            if gap >= min_size:
                return (high_2, low_0)
        else:
            # Bearish FVG : gap entre High[i] et Low[i-2]
            gap = low_2 - high_0
            if gap >= min_size:
                return (high_0, low_2)

        return None

    def _check_retrace(
        self, high: float, low: float, setup: _PendingSetup,
    ) -> bool:
        """Vérifie si la barre courante retrace dans la zone FVG.

        Bullish : le prix redescend et touche le haut du FVG (achat sur pullback).
        Bearish : le prix remonte et touche le bas du FVG (vente sur pullback).
        """
        if setup.direction == 1:
            return low <= setup.fvg_high
        else:
            return high >= setup.fvg_low

    # ── Signal creation ──────────────────────────────────────────────────

    def _make_signal(
        self, df: pd.DataFrame, i: int, setup: _PendingSetup,
        instrument: str, atr: float,
    ) -> Signal | None:
        """Crée le Signal final à partir d'un setup complété."""
        close = df["Close"].iloc[i]
        buffer = self.cfg.sl_buffer_atr * atr

        if setup.direction == 1:
            sl = setup.sweep_extreme - buffer
            risk = close - sl
            if risk <= 0 or risk > self.cfg.max_sl_atr * atr:
                return None
            tp = close + self.cfg.rr_tp * risk
            side = Side.LONG
        else:
            sl = setup.sweep_extreme + buffer
            risk = sl - close
            if risk <= 0 or risk > self.cfg.max_sl_atr * atr:
                return None
            tp = close - self.cfg.rr_tp * risk
            side = Side.SHORT

        # Label sub_type pour l'ablation
        components = []
        if self.cfg.require_compression:
            components.append("sq")
        components.append("sw")
        if self.cfg.require_choch:
            components.append("ch")
        if self.cfg.require_fvg_retrace:
            components.append("fvg")
        if self.cfg.require_htf_bias:
            components.append("htf")
        sub_type = "renverse_" + "_".join(components)

        return Signal(
            instrument=instrument,
            side=side,
            close=close,
            sl=sl,
            tp_indicative=tp,
            atr=atr,
            bb_width=float(df["bb_width"].iloc[i]) if "bb_width" in df.columns else 1.0,
            strategy_type="renverse",
            sub_type=sub_type,
            timeframe="1h",
            timestamp=df.index[i],
        )
