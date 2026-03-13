"""Compat shim — use arabesque.strategies.extension.signal instead."""
from arabesque.strategies.extension.signal import (
    ExtensionSignalGenerator as TrendSignalGenerator,
    ExtensionConfig as TrendSignalConfig,
    TrendSignalGenerator, TrendSignalConfig,  # alias already defined in signal.py
)
