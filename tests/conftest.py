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


@pytest.fixture(autouse=True)
def _isolate_dispatcher_log_paths(tmp_path, monkeypatch):
    """Même classe de bug que STATE_FILE : les chemins relatifs du dispatcher
    (``logs/*.jsonl``) pointent sur les logs de production quand pytest tourne
    depuis la racine du repo. Découvert 2026-07-08 — 178 entrées
    ``signal_id=gft-risk`` (fixture de test) accumulées dans
    ``logs/broker_guard_rejects.jsonl`` depuis le 27/05, polluant les métriques
    de la watchlist /suivi (``ftmo_minlot_overshoot`` compte ces rejets)."""
    from arabesque.execution import order_dispatcher as od_module
    monkeypatch.setattr(od_module, "SHADOW_LOG_PATH", tmp_path / "shadow_filters.jsonl")
    monkeypatch.setattr(
        od_module, "WEEKEND_GUARD_LOG_PATH", tmp_path / "weekend_crypto_guard.jsonl"
    )
    monkeypatch.setattr(
        od_module, "BROKER_REJECT_LOG_PATH", tmp_path / "broker_guard_rejects.jsonl"
    )
    monkeypatch.setattr(
        od_module,
        "GFT_QUOTE_COHERENCE_LOG_PATH",
        tmp_path / "gft_quote_coherence.jsonl",
    )
    yield
