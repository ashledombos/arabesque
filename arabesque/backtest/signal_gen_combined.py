"""
Arabesque v2 — Combined Signal Generator.

Fusionne les signaux mean-reversion et trend.
Les deux stratégies partagent les mêmes indicateurs de base (BB, RSI, CMF, ATR)
mais avec une logique d'entrée complémentaire :

- Mean-reversion : BB large → entry sur excès → TP = retour BB mid
- Trend : BB squeeze → expansion → breakout → TP trailing

Le runner n'a besoin de connaître que le CombinedSignalGenerator.
"""

from __future__ import annotations

import pandas as pd

from arabesque.models import Signal
from arabesque.backtest.signal_gen import BacktestSignalGenerator, SignalGenConfig
from arabesque.backtest.signal_gen_trend import TrendSignalGenerator, TrendSignalConfig


class CombinedSignalGenerator:
    """Fusionne mean-reversion + trend signals.

    Usage :
        gen = CombinedSignalGenerator()
        df = gen.prepare(df)
        signals = gen.generate_signals(df, "EURUSD")
        # signals contient les deux types, identifiés par signal.strategy_type
    """

    def __init__(
        self,
        mr_config: SignalGenConfig | None = None,
        trend_config: TrendSignalConfig | None = None,
        enable_mr: bool = True,
        enable_trend: bool = True,
    ):
        self.enable_mr = enable_mr
        self.enable_trend = enable_trend

        if enable_mr:
            self.mr_gen = BacktestSignalGenerator(mr_config)
        else:
            self.mr_gen = None

        if enable_trend:
            self.trend_gen = TrendSignalGenerator(trend_config)
        else:
            self.trend_gen = None

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Prépare le DataFrame avec tous les indicateurs.

        Utilise le trend generator s'il est actif (il calcule
        des colonnes supplémentaires : squeeze, recent_squeeze,
        bb_expanding, adx, adx_rising).
        Sinon, utilise le mean-reversion generator.
        """
        if self.trend_gen is not None:
            return self.trend_gen.prepare(df)
        elif self.mr_gen is not None:
            return self.mr_gen.prepare(df)
        return df

    def generate_signals(
        self, df: pd.DataFrame, instrument: str = "UNKNOWN"
    ) -> list[tuple[int, Signal]]:
        """Génère tous les signaux (MR + trend), triés par bar index.

        Normalise en format (bar_index, Signal) pour les deux générateurs.
        En cas de conflit (MR et trend sur la même bougie), on garde
        les deux — le runner appliquera le cooldown et max_positions.
        Le trend est prioritaire (listé en premier pour le même index)
        car il capte une dynamique de marché plus forte.
        """
        all_signals: list[tuple[int, Signal]] = []

        if self.mr_gen is not None and self.enable_mr:
            mr_signals = self.mr_gen.generate_signals(df, instrument)
            for item in mr_signals:
                if isinstance(item, tuple):
                    all_signals.append(item)
                else:
                    # MR returns Signal directly → find bar index via timestamp
                    sig = item
                    try:
                        idx = df.index.get_loc(sig.timestamp)
                        if isinstance(idx, int):
                            all_signals.append((idx, sig))
                    except (KeyError, AttributeError):
                        pass

        if self.trend_gen is not None and self.enable_trend:
            trend_signals = self.trend_gen.generate_signals(df, instrument)
            for item in trend_signals:
                if isinstance(item, tuple):
                    all_signals.append(item)
                else:
                    sig = item
                    try:
                        idx = df.index.get_loc(sig.timestamp)
                        if isinstance(idx, int):
                            all_signals.append((idx, sig))
                    except (KeyError, AttributeError):
                        pass

        # Trier par bar index, trend d'abord en cas d'égalité
        def sort_key(item):
            idx, sig = item
            priority = 0 if sig.strategy_type == "trend" else 1
            return (idx, priority)

        all_signals.sort(key=sort_key)
        return all_signals
