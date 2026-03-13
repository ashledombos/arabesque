"""
Stratégie Extension — Trend-Following H1.

Nom de code : Extension (mouvement de danse classique)
Statut : ✅ Validé live (FTMO, Jul 2024 → Fév 2026)

Importer :
    from arabesque.strategies.extension.signal import ExtensionSignalGenerator, ExtensionConfig
"""
from arabesque.strategies.extension.signal import (
    ExtensionSignalGenerator,
    ExtensionConfig,
    TrendSignalGenerator,   # alias compat
    TrendSignalConfig,      # alias compat
)

__all__ = [
    "ExtensionSignalGenerator",
    "ExtensionConfig",
    "TrendSignalGenerator",
    "TrendSignalConfig",
]
