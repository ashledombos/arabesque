"""Câblage stratégie → générateur dans le BarAggregator.

Régression pour l'ajout de Renversé (2026-07-04, mode ombre) : une stratégie
présente dans strategy_assignments mais absente du câblage tomberait en
fallback Extension SILENCIEUSEMENT — l'ombre mesurerait la mauvaise stratégie.
"""
from __future__ import annotations

import pytest

from arabesque.execution.bar_aggregator import BarAggregator, BarAggregatorConfig


@pytest.mark.parametrize("strategy, expected_cls", [
    ("extension", "ExtensionSignalGenerator"),
    ("glissade", "GlissadeRSIDivGenerator"),
    ("cabriole", "CabrioleSignalGenerator"),
    ("fouette", "FouetteSignalGenerator"),
    ("renverse", "RenverseSignalGenerator"),
])
def test_strategy_wiring_instantiates_expected_generator(strategy, expected_cls):
    agg = BarAggregator(BarAggregatorConfig(
        instruments=["XAUUSD"], signal_strategy=strategy,
    ), on_signal=lambda s: None)
    gen = agg._make_signal_generator()
    assert type(gen).__name__ == expected_cls


def test_unknown_strategy_falls_back_to_extension(caplog):
    agg = BarAggregator(BarAggregatorConfig(
        instruments=["XAUUSD"], signal_strategy="inexistante",
    ), on_signal=lambda s: None)
    with caplog.at_level("WARNING"):
        gen = agg._make_signal_generator()
    assert type(gen).__name__ == "ExtensionSignalGenerator"
    assert "inconnue" in caplog.text
