"""WeekendFilter — wrapper de signal generator qui émule le weekend crypto guard.

Le guard live (``order_dispatcher._is_weekend_crypto_blocked``) bloque les
nouvelles positions crypto sur cTrader après vendredi 15h UTC. Pour que les
backtests reflètent la config live, on filtre les signaux dont l'entry_time
(clôture de la bougie signal) tombe dans la fenêtre bloquée :
vendredi >= cutoff_hour UTC, samedi, dimanche.

Limite connue : les trades ouverts vendredi AVANT le cutoff et qui *traversent*
le weekend sont conservés (comme en live), mais le backtest ne simule pas les
gaps de prix à la réouverture — source d'optimisme résiduelle.
"""
from __future__ import annotations

import pandas as pd


class WeekendFilter:
    """Wrappe un signal generator en filtrant les signaux bloqués le weekend.

    Convention : un signal à l'index ``i`` du DataFrame correspond à une
    entrée à la clôture de la bougie ``i`` = ``df.index[i] + timeframe``.
    On déduit le timeframe depuis l'index du DataFrame.
    """

    def __init__(self, inner, cutoff_hour: int = 15):
        self._inner = inner
        self._cutoff = cutoff_hour

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        return self._inner.prepare(df)

    def generate_signals(self, df: pd.DataFrame, instrument: str = "UNKNOWN"):
        all_sigs = self._inner.generate_signals(df, instrument)
        if len(df.index) < 2:
            return all_sigs
        tf_delta = df.index[1] - df.index[0]

        filtered = []
        for i, s in all_sigs:
            entry_time = df.index[i] + tf_delta
            wd = entry_time.weekday()
            hour = entry_time.hour
            if wd == 4 and hour >= self._cutoff:
                continue
            if wd in (5, 6):
                continue
            filtered.append((i, s))
        return filtered
