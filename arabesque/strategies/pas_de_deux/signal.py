"""
arabesque/strategies/pas_de_deux/signal.py
==========================================
Pas de Deux — Pairs trading par cointégration et z-score.

Un pas de deux en ballet est une danse à deux partenaires en miroir, chacun
répondant aux mouvements de l'autre. Deux instruments cointégrés "dansent"
autour d'un équilibre — quand ils s'écartent, on parie sur leur retour.

Timeframe cible  : M15 (signal) + recalibrage quotidien
Instruments      : Paires cointégrées (indices entre eux, forex crosses, etc.)
Session          : Heures de marché où les deux jambes sont liquides

Logique
-------
1. Calibration : fenêtre glissante (20 sessions × 26 barres M15 ≈ 520 obs)
   Engle-Granger : log(PA) = α + β·log(PB) + ε, test ADF sur résidu
2. Z-score du résidu : z = (ε - mean(ε, L)) / std(ε, L), L = 260
3. Entrée short spread si z ≥ +2.0 : short A, long B (tailles selon β)
   Entrée long spread si z ≤ -2.0 : long A, short B
4. Condition : corrélation roulante 10 sessions ≥ 0.80

Gestion
-------
- TP : z revient à 0.0 (±0.25), sortie partielle 50% à |z| = 1.0
- Stop stat : |z| ≥ 3.5
- Stop temps : z ne revient pas sous |z| ≤ 1.0 après 2 sessions
- Stop rupture : ADF p-value > 0.10 au recalibrage → sortie fin de barre

Guards spécifiques
------------------
- Max 1 paire simultanée
- Cooldown 1 session après stop (éviter de trader une rupture)
- News window ±2 min sur annonces impactant les deux jambes
- Leg risk : si fill d'une jambe échoue > 1 tick, annulation

Infrastructure requise (pas encore dans Arabesque)
---------------------------------------------------
- Support multi-jambes dans BacktestRunner (actuellement mono-instrument)
- Exécution synchronisée de 2 ordres (broker)
- Calcul de hedge ratio dynamique
- Test ADF (statsmodels ou implémentation interne)
- Corrélation roulante dans indicators.py

WR cible : 50-65%
Profil boussole : courbe régulière SI relation stable, mais risque de rupture
  → nécessite stress tests approfondis sur breakdown de cointégration

Complexité : élevée — infra significative à construire avant le premier backtest.
Priorité : long terme / exploratoire.

Statut : PLACEHOLDER — rien n'est implémenté ci-dessous.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from arabesque.core.models import Side, Signal

logger = logging.getLogger(__name__)


# ─── Configuration ──────────────────────────────────────────────────────────

@dataclass
class PasDeDeuxConfig:
    """Paramètres de la stratégie Pas de Deux (pairs cointégration).

    Tous les paramètres sont des defaults théoriques. Aucun n'est validé.
    """

    # ── Paire ─────────────────────────────────────────────────────────────
    instrument_a: str = ""                  # Jambe A (ex: "US500")
    instrument_b: str = ""                  # Jambe B (ex: "US100")

    # ── Calibration ──────────────────────────────────────────────────────
    calibration_sessions: int = 20          # Fenêtre de calibration (sessions)
    calibration_bars_per_session: int = 26  # Barres M15 par session
    zscore_lookback: int = 260              # Lookback pour mean/std du z-score
    recalibrate_frequency: str = "daily"    # "daily" | "weekly"

    # ── Seuils d'entrée / sortie ─────────────────────────────────────────
    z_entry: float = 2.0                    # |z| ≥ 2.0 pour entrer
    z_exit: float = 0.25                    # Sortie quand |z| ≤ 0.25
    z_partial: float = 1.0                  # Sortie partielle à |z| = 1.0
    z_stop: float = 3.5                     # Stop stat si |z| ≥ 3.5
    partial_exit_pct: float = 0.50          # 50% sorti à z_partial

    # ── Conditions de validité ───────────────────────────────────────────
    adf_pvalue_max: float = 0.05            # Cointégration requise (p ≤ 0.05)
    adf_pvalue_exit: float = 0.10           # Sortie si p > 0.10 au recalibrage
    correlation_min: float = 0.80           # Corrélation roulante min
    correlation_lookback_sessions: int = 10

    # ── Stops ────────────────────────────────────────────────────────────
    time_stop_sessions: int = 2             # Sortie si pas de réversion en N sessions
    leg_risk_max_ticks: int = 1             # Annulation si fill > N ticks d'écart

    # ── Risk ─────────────────────────────────────────────────────────────
    risk_per_trade_pct: float = 0.15        # 0.15% par trade
    max_simultaneous_pairs: int = 1
    cooldown_sessions: int = 1              # Cooldown après stop


# ─── Signal Generator ──────────────────────────────────────────────────────

class PasDeDeuxSignalGenerator:
    """Placeholder — interface adaptée au contrat signal.py.

    IMPORTANT : le contrat signal.py standard est mono-instrument.
    Pas de Deux nécessite une extension pour les paires :
    - prepare() prend 2 DataFrames (ou un DataFrame fusionné)
    - generate_signals() retourne des signaux avec 2 jambes

    TODO avant implémentation :
    1. Définir l'interface multi-jambes (extension de Signal ou nouveau type)
    2. Adapter BacktestRunner pour le multi-instrument
    3. Implémenter le calcul Engle-Granger + ADF
    4. Construire le z-score rolling
    5. Tester sur paires synthétiques d'abord
    """

    def __init__(self, config: Optional[PasDeDeuxConfig] = None):
        self.cfg = config or PasDeDeuxConfig()

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Prépare les données pour le pairs trading.

        TODO: interface à redéfinir (mono-instrument → bi-instrument).
        """
        raise NotImplementedError(
            "Pas de Deux: signal.py est un placeholder. "
            "Infrastructure multi-jambes requise avant implémentation."
        )

    def generate_signals(
        self, df: pd.DataFrame, instrument: str
    ) -> list[tuple[int, Signal]]:
        """Génère les signaux de pairs trading.

        Convention anti-lookahead :
            Signal sur bougie i → fill au OPEN de bougie i+1.
        """
        raise NotImplementedError(
            "Pas de Deux: signal.py est un placeholder. "
            "Infrastructure multi-jambes requise avant implémentation."
        )
