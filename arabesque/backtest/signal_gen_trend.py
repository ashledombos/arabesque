"""Compat shim — use arabesque.strategies.extension.signal instead."""
from arabesque.strategies.extension.signal import (
    TrendSignalGenerator,
    TrendSignalConfig,
)

__all__ = ["TrendSignalGenerator", "TrendSignalConfig"]
