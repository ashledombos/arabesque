"""Persistance immédiate du fichier ``logs/position_monitor_state.json``
après chaque ``register_position()`` / ``unregister_position()`` — pré-requis
pour le Hot Path #2 (`feed_watchdog` lit ce fichier pour décider du skip
weekend conditionné).

Sans cette persistance immédiate, le state file n'est mis à jour qu'au
cycle ``reconcile()`` (toutes les 2 min côté ``live.py``). Le watchdog
pourrait alors voir "0 position" pendant 2 min après l'ouverture d'une
position et skipper le weekend à tort, laissant la position sans
surveillance feed pendant ce laps.

Invariants verrouillés :
  1. Après ``register_position()``, ``STATE_FILE`` existe et contient
     1 entrée avec broker_id/position_id corrects.
  2. Après ``register_position()`` × 2, le fichier contient 2 entrées.
  3. Après ``unregister_position()`` qui laisse ≥ 1 position, le fichier
     existe encore et contient les positions restantes.
  4. Après ``unregister_position()`` de la dernière position, le fichier
     est supprimé (sémantique "vide = absent").
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from arabesque.core.models import Side
from arabesque.execution import position_monitor as pm_module
from arabesque.execution.position_monitor import (
    LivePositionMonitor,
    MonitorConfig,
)


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    """Moniteur isolé : STATE_FILE redirigé vers tmp_path pour ne pas
    polluer le state file de production."""
    state_path = tmp_path / "position_monitor_state.json"
    monkeypatch.setattr(pm_module, "STATE_FILE", state_path)
    cfg = MonitorConfig(
        broker_reconcile_enabled=False,  # pas de boucle async dans ces tests
    )
    mon = LivePositionMonitor(brokers={}, config=cfg)
    return mon, state_path


def _register(mon, *, broker_id="ftmo", position_id="P1", symbol="DASHUSD"):
    return mon.register_position(
        broker_id=broker_id, position_id=position_id, symbol=symbol,
        side=Side.LONG, entry=49.40, sl=46.70, tp=54.70,
        volume=1.0, digits=2,
    )


def test_state_file_created_on_register_position(monitor):
    """Invariant 1 : register → fichier créé avec 1 entrée."""
    mon, state_path = monitor
    assert not state_path.exists(), "préalable : pas de state au départ"
    _register(mon, position_id="P1", symbol="DASHUSD")
    assert state_path.exists(), "register_position doit créer le state file"
    data = json.loads(state_path.read_text())
    assert len(data) == 1
    assert "ftmo:P1" in data
    assert data["ftmo:P1"]["symbol"] == "DASHUSD"


def test_state_file_contains_multiple_positions(monitor):
    """Invariant 2 : N registers → N entrées."""
    mon, state_path = monitor
    _register(mon, position_id="P1", symbol="DASHUSD")
    _register(mon, position_id="P2", symbol="BTCUSD")
    data = json.loads(state_path.read_text())
    assert len(data) == 2
    assert "ftmo:P1" in data
    assert "ftmo:P2" in data


def test_state_file_persists_remaining_after_partial_unregister(monitor):
    """Invariant 3 : unregister 1 sur 2 → file contient encore l'autre."""
    mon, state_path = monitor
    _register(mon, position_id="P1", symbol="DASHUSD")
    _register(mon, position_id="P2", symbol="BTCUSD")
    mon.unregister_position("ftmo", "P1")
    assert state_path.exists(), "fichier doit subsister tant qu'il reste ≥ 1 position"
    data = json.loads(state_path.read_text())
    assert len(data) == 1
    assert "ftmo:P2" in data
    assert "ftmo:P1" not in data


def test_state_file_removed_when_last_position_unregistered(monitor):
    """Invariant 4 : unregister de la dernière → fichier supprimé."""
    mon, state_path = monitor
    _register(mon, position_id="P1", symbol="DASHUSD")
    assert state_path.exists()
    mon.unregister_position("ftmo", "P1")
    assert not state_path.exists(), (
        "vide = absent : le fichier doit disparaître quand 0 position"
    )


def test_unregister_unknown_position_noop(monitor):
    """Unregister d'une position non trackée ne doit ni planter ni créer
    le state file inutilement."""
    mon, state_path = monitor
    # Aucune position registered
    mon.unregister_position("ftmo", "NEVER_EXISTED")
    assert not state_path.exists()
