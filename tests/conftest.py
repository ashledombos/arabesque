"""Isolation globale pour la suite de tests.

Incident fondateur 2026-05-23 — pytest a écrit un état phantom XAUUSD
#52759859 dans ``logs/position_monitor_state.json`` (chemin de production)
parce que ``tests/test_replay_incident_2026_05_14.py`` appelle
``mon.register_position(...)`` sans monkeypatcher
``arabesque.execution.position_monitor.STATE_FILE`` (chemin relatif
``logs/position_monitor_state.json`` → CWD = racine repo).

Conséquence : ``feed_watchdog`` (Hot Path #2) lisait 1 position fictive,
restait en mode "hot path", et avec le check mtime du patch #3 retiré
le 23/05 22:14 UTC aurait re-spammé URGENT en weekend dormant. Cf.
``feedback_mtime_threshold_requires_periodic_write.md``.

Cette fixture autouse redirige ``STATE_FILE`` vers ``tmp_path`` pour
*toute* fonction de test. Les fixtures locales qui font déjà un
monkeypatch.setattr sur ``STATE_FILE`` continuent de fonctionner —
l'ordre LIFO de pytest applique la fixture locale après l'autouse.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_position_monitor_state_file(tmp_path, monkeypatch):
    from arabesque.execution import position_monitor as pm_module
    monkeypatch.setattr(
        pm_module, "STATE_FILE", tmp_path / "position_monitor_state.json"
    )
    yield
