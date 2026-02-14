"""
Arabesque — Signal Screener (Passe 1).

Le SEUL usage légitime du backtesting : mesurer la qualité brute
d'un signal SANS simuler d'exécution.

Pour chaque signal généré, on mesure :
- MFE (Maximum Favorable Excursion) : combien le prix est allé en notre faveur
- MAE (Maximum Adverse Excursion) : combien le prix est allé contre nous
- Temps de réversion : combien de barres pour revenir à BB mid / +0.5R / +1R
- Taux de réversion : % de signaux qui atteignent le target

PAS de simulation de fill, PAS de SL/TP, PAS de trailing.
C'est un scanner de distribution, pas un backtester.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
import pandas as pd


@dataclass
class SignalOutcome:
    """Résultat brut d'un signal (sans exécution simulée)."""
    timestamp: pd.Timestamp
    instrument: str
    side: str              # "LONG" ou "SHORT"
    signal_type: str
    entry_price: float     # close de la bougie signal
    atr: float

    # MFE/MAE sur N barres après le signal
    mfe_1r: float = 0.0   # MFE en multiples de risk (1R = 1 ATR ici)
    mae_1r: float = 0.0   # MAE en multiples de risk
    mfe_bars: int = 0      # barres pour atteindre MFE
    mae_bars: int = 0      # barres pour atteindre MAE

    # Targets
    reached_05r: bool = False
    reached_1r: bool = False
    reached_15r: bool = False
    reached_2r: bool = False
    bars_to_05r: int = -1
    bars_to_1r: int = -1

    # Contexte
    context: dict = field(default_factory=dict)


class SignalScreener:
    """Scanner de qualité de signal sans simulation d'exécution."""

    def __init__(
        self,
        forward_bars: int = 50,
        risk_unit: str = "atr",    # "atr" = 1R = 1 ATR
    ):
        self.forward_bars = forward_bars
        self.risk_unit = risk_unit

    def screen_bb_excess(
        self,
        df: pd.DataFrame,
        bb_period: int = 20,
        bb_std: float = 2.0,
        ema_period: int = 200,
        atr_period: int = 14,
    ) -> list[SignalOutcome]:
        """Scanne les signaux BB excess (close < BB lower) et mesure MFE/MAE.
        
        df doit avoir des colonnes : Open, High, Low, Close, Volume.
        """
        # Calcul des indicateurs
        df = df.copy()
        df["ema"] = df["Close"].ewm(span=ema_period, adjust=False).mean()
        df["bb_mid"] = df["Close"].rolling(bb_period).mean()
        df["bb_std"] = df["Close"].rolling(bb_period).std()
        df["bb_lower"] = df["bb_mid"] - bb_std * df["bb_std"]
        df["bb_upper"] = df["bb_mid"] + bb_std * df["bb_std"]
        df["atr"] = self._atr(df, atr_period)

        outcomes = []

        for i in range(ema_period, len(df) - self.forward_bars):
            row = df.iloc[i]

            # Signal : close < BB lower ET close > EMA200 (filtre trend)
            if row["Close"] >= row["bb_lower"]:
                continue
            if row["Close"] <= row["ema"]:
                continue
            if pd.isna(row["atr"]) or row["atr"] <= 0:
                continue

            entry = row["Close"]
            atr = row["atr"]
            bb_mid = row["bb_mid"]

            # Mesurer MFE/MAE sur les N barres suivantes
            future = df.iloc[i + 1 : i + 1 + self.forward_bars]
            outcome = self._measure_outcome(
                entry, atr, future, "LONG",
                target_price=bb_mid,
            )
            outcome.timestamp = df.index[i]
            outcome.instrument = ""  # à remplir par l'appelant
            outcome.signal_type = "bb_excess"
            outcome.context = {
                "bb_lower": row["bb_lower"],
                "bb_mid": bb_mid,
                "ema": row["ema"],
                "close_to_bb": (entry - row["bb_lower"]) / atr,
            }

            outcomes.append(outcome)

        return outcomes

    def _measure_outcome(
        self,
        entry: float,
        atr: float,
        future_bars: pd.DataFrame,
        side: str,
        target_price: float = 0.0,
    ) -> SignalOutcome:
        """Mesure MFE/MAE sur les barres futures."""
        outcome = SignalOutcome(
            timestamp=pd.Timestamp.now(),
            instrument="",
            side=side,
            signal_type="",
            entry_price=entry,
            atr=atr,
        )

        max_high = entry
        min_low = entry
        mfe_bar = 0
        mae_bar = 0

        for j, (ts, bar) in enumerate(future_bars.iterrows()):
            h = bar["High"]
            l = bar["Low"]

            if side == "LONG":
                if h > max_high:
                    max_high = h
                    mfe_bar = j + 1
                if l < min_low:
                    min_low = l
                    mae_bar = j + 1

                # Check targets
                excursion_r = (h - entry) / atr
                if excursion_r >= 0.5 and not outcome.reached_05r:
                    outcome.reached_05r = True
                    outcome.bars_to_05r = j + 1
                if excursion_r >= 1.0 and not outcome.reached_1r:
                    outcome.reached_1r = True
                    outcome.bars_to_1r = j + 1
                if excursion_r >= 1.5 and not outcome.reached_15r:
                    outcome.reached_15r = True
                if excursion_r >= 2.0 and not outcome.reached_2r:
                    outcome.reached_2r = True

        # Calculer MFE/MAE en R (= ATR)
        if side == "LONG":
            outcome.mfe_1r = (max_high - entry) / atr
            outcome.mae_1r = (min_low - entry) / atr  # négatif
        else:
            outcome.mfe_1r = (entry - min_low) / atr
            outcome.mae_1r = (entry - max_high) / atr

        outcome.mfe_bars = mfe_bar
        outcome.mae_bars = mae_bar

        return outcome

    def report(self, outcomes: list[SignalOutcome], instrument: str = "") -> str:
        """Génère un rapport de screening."""
        if not outcomes:
            return f"Aucun signal trouvé{' pour ' + instrument if instrument else ''}."

        n = len(outcomes)
        mfes = [o.mfe_1r for o in outcomes]
        maes = [o.mae_1r for o in outcomes]

        reach_05 = sum(1 for o in outcomes if o.reached_05r) / n
        reach_1 = sum(1 for o in outcomes if o.reached_1r) / n
        reach_15 = sum(1 for o in outcomes if o.reached_15r) / n
        reach_2 = sum(1 for o in outcomes if o.reached_2r) / n

        bars_to_05 = [o.bars_to_05r for o in outcomes if o.bars_to_05r > 0]
        bars_to_1 = [o.bars_to_1r for o in outcomes if o.bars_to_1r > 0]

        lines = [
            f"SIGNAL SCREENER — {instrument or 'All'}",
            f"{'=' * 50}",
            f"  Signaux trouvés : {n}",
            f"",
            f"  MFE (max favorable) :",
            f"    Médiane : {np.median(mfes):.2f} ATR",
            f"    Moyenne : {np.mean(mfes):.2f} ATR",
            f"    P25/P75 : {np.percentile(mfes, 25):.2f} / {np.percentile(mfes, 75):.2f} ATR",
            f"",
            f"  MAE (max adverse) :",
            f"    Médiane : {np.median(maes):.2f} ATR",
            f"    Moyenne : {np.mean(maes):.2f} ATR",
            f"    P25/P75 : {np.percentile(maes, 25):.2f} / {np.percentile(maes, 75):.2f} ATR",
            f"",
            f"  Taux d'atteinte des targets :",
            f"    +0.5 ATR : {reach_05:.0%}  (médiane {np.median(bars_to_05):.0f} barres)" if bars_to_05 else f"    +0.5 ATR : {reach_05:.0%}",
            f"    +1.0 ATR : {reach_1:.0%}  (médiane {np.median(bars_to_1):.0f} barres)" if bars_to_1 else f"    +1.0 ATR : {reach_1:.0%}",
            f"    +1.5 ATR : {reach_15:.0%}",
            f"    +2.0 ATR : {reach_2:.0%}",
            f"",
            f"  Ratio MFE/MAE médian : {abs(np.median(mfes) / np.median(maes)):.2f}" if np.median(maes) != 0 else "",
        ]

        # Distribution MFE
        lines.append(f"")
        lines.append(f"  Distribution MFE (histogramme) :")
        bins = [0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, float('inf')]
        for i in range(len(bins) - 1):
            count = sum(1 for m in mfes if bins[i] <= m < bins[i+1])
            bar = "█" * int(count / n * 40) if n > 0 else ""
            label = f"{bins[i]:.1f}-{bins[i+1]:.1f}" if bins[i+1] != float('inf') else f"{bins[i]:.1f}+"
            lines.append(f"    {label:>8} ATR : {bar} {count} ({count/n:.0%})")

        return "\n".join(lines)

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["High"]
        low = df["Low"]
        close = df["Close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(period).mean()
