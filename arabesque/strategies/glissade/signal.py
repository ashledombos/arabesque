"""
arabesque/strategies/glissade/signal.py
=======================================
Glissade — Scalping intraday sur pullback VWAP + EMA.

Une glissade en danse classique est un pas glissé, court et fluide, qui relie
deux mouvements plus grands. Le prix "glisse" brièvement contre la tendance
intraday puis repart — un pullback vers le VWAP capté au bon moment.

Timeframe cible  : M1 (entrée) + M5 (contexte)
Instruments      : Indices (US500, US100), XAUUSD, crypto liquides
Session          : Heures de marché à haute liquidité (NY, London)

Logique
-------
1. Contexte haussier/baissier via EMA200(M5) + position vs VWAP session
2. Pullback : 2+ bougies M1 contre-tendance, creux/sommet touchant EMA20(M1)
3. Trigger : bougie M1 de retournement clôturant au-delà du high/low précédent
4. Entrée : stop-limit au high/low du trigger + 1 tick (anti-faux départ)

Gestion
-------
- SL : 0.9×ATR14(M1) à 1.5×ATR14(M1) selon volatilité
- BE trigger 0.5R → offset 0.3R (à calibrer, plus serré que Extension car M1)
- TP1 : 0.8R (sortie partielle), TP2 : 1.2R ou retour sous/sur VWAP
- Time stop : 6 minutes sans progrès → sortie

Guards spécifiques
------------------
- Filtre ADX(M5) : 15 ≤ ADX ≤ 30 (pas de chop, pas de trend violent)
- Filtre ATR max : pas de trade si ATR14(M1) > seuil (à calibrer par instrument)
- Max 6 trades/jour, max 3 pertes consécutives → arrêt
- News window ±2 min sur annonces majeures
- Fenêtre horaire : pas de scalping à l'ouverture immédiate (+5 min) ni en fin
  de session

Indicateurs requis (arabesque.modules.indicators)
--------------------------------------------------
- VWAP session (reset quotidien) — À IMPLÉMENTER dans indicators.py
- EMA 20 (M1), EMA 200 (M5)
- ATR 14 (M1)
- ADX 14 (M5)

WR cible : 55-70% brut → 70-80% avec BE trigger (hypothèse à valider)
Profil boussole : compatible si BE trigger fonctionne aussi bien qu'en Extension

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
class GlissadeConfig:
    """Paramètres de la stratégie Glissade (scalping VWAP pullback).

    Tous les paramètres sont des defaults de départ à calibrer par
    walk-forward sur données M1. Aucun n'est validé.
    """

    # ── Session / fenêtre ─────────────────────────────────────────────────
    session: str = "ny"                     # "ny", "london", "overlap"
    entry_delay_minutes: int = 5            # Attendre N min après cash open
    session_end_buffer_minutes: int = 10    # Arrêter N min avant fin session

    # ── Contexte (M5) ────────────────────────────────────────────────────
    ema_slow_period: int = 200              # EMA lente sur M5
    adx_period: int = 14                    # ADX sur M5
    adx_min: float = 15.0                   # Pas de trade sous ce seuil
    adx_max: float = 30.0                   # Pas de trade au-dessus

    # ── Signal (M1) ──────────────────────────────────────────────────────
    ema_fast_period: int = 20               # EMA rapide sur M1 (pullback target)
    pullback_min_bars: int = 2              # Min bougies contre-tendance
    pullback_ema_tolerance_atr: float = 0.2 # Creux à ≤ 0.2×ATR de EMA20

    # ── SL / TP ──────────────────────────────────────────────────────────
    sl_atr_factor: float = 0.9              # SL = facteur × ATR14(M1)
    sl_atr_max: float = 1.5                 # Cap SL à max × ATR14(M1)
    atr_max_filter: float = 0.0             # 0 = désactivé, sinon max ATR absolu
    tp1_r: float = 0.8                      # TP1 = 0.8R (sortie partielle)
    tp1_pct: float = 0.50                   # % de position sorti à TP1
    tp2_r: float = 1.2                      # TP2 = 1.2R ou retour VWAP
    time_stop_minutes: int = 6              # Sortie si pas de progrès

    # ── Risk ─────────────────────────────────────────────────────────────
    max_trades_per_day: int = 6
    max_consecutive_losses: int = 3         # Arrêt après N pertes consécutives
    risk_per_trade_pct: float = 0.10        # 0.10% (scalping = fréquence haute)


# ─── Signal Generator ──────────────────────────────────────────────────────

class GlissadeSignalGenerator:
    """Placeholder — interface conforme au contrat signal.py.

    TODO: implémenter prepare() et generate_signals() quand :
    - VWAP session est disponible dans indicators.py
    - Données M1+M5 synchronisées sont testées
    - Premier backtest sur pool quick (EURUSD, XAUUSD, BTCUSD)
    """

    def __init__(self, config: Optional[GlissadeConfig] = None):
        self.cfg = config or GlissadeConfig()

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ajoute les colonnes indicateurs nécessaires.

        TODO:
        - VWAP session (reset quotidien)
        - EMA20(M1), EMA200(M5)
        - ATR14(M1), ADX14(M5)
        """
        raise NotImplementedError("Glissade: signal.py est un placeholder")

    def generate_signals(
        self, df: pd.DataFrame, instrument: str
    ) -> list[tuple[int, Signal]]:
        """Génère les signaux de scalping VWAP pullback.

        Convention anti-lookahead :
            Signal sur bougie i → fill au OPEN de bougie i+1.
        """
        raise NotImplementedError("Glissade: signal.py est un placeholder")
