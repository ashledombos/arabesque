"""
Stratégie Glissade — RSI divergence H1 (validé) + VWAP pullback M1 (abandonné).

Nom de code : Glissade (mouvement de danse classique)
Statut : ✅ Walk-forward PASS 3/3 (mode RSI divergence H1)

Importer :
    from arabesque.strategies.glissade.signal import GlissadeRSIDivGenerator, GlissadeRSIDivConfig
"""
from arabesque.strategies.glissade.signal import (
    GlissadeRSIDivGenerator,
    GlissadeRSIDivConfig,
    GlissadeSignalGenerator,
    GlissadeConfig,
)

__all__ = [
    "GlissadeRSIDivGenerator",
    "GlissadeRSIDivConfig",
    "GlissadeSignalGenerator",
    "GlissadeConfig",
]
