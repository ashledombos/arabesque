"""Task #40 patch #3 — détection ``POSITIONS_STATE`` figé (monitor mort).

Cas couvert. Si ``LivePositionMonitor`` crashe sans cleanup (SIGSEGV, OOM
kill, panique kernel) **avec une position ouverte**, le fichier
``logs/position_monitor_state.json`` reste à ``{position_id: {...}}`` figé.
Le watchdog le lit → count > 0 → mode hot. Bon, c'est safe côté décision.

Mais une seconde lecture est utile : **le fichier existe et n'a pas été
touché depuis longtemps**. En fonctionnement normal, ``save_state`` est
appelé à chaque register/unregister + checkpoint périodique (cf
``arabesque/execution/position_monitor.py``). Si le mtime > 10 min alors
que le fichier existe → monitor probablement mort silencieusement.

Patch : nouveau helper ``_positions_state_age_seconds(now)`` qui retourne
l'âge du fichier en secondes (None si absent). En weekend avec count > 0
et age > 600s, le watchdog flag ``positions_state_stale=True`` + notif
URGENT (1×, sous cooldown). Pas de changement de comportement décisionnel
— le mode hot est déjà actif (count > 0).

Invariants verrouillés :
  1. ``_positions_state_age_seconds`` retourne ``None`` si fichier absent.
  2. ``_positions_state_age_seconds`` retourne un ``int >= 0`` si présent.
  3. Fichier fraîchement écrit → age petit (< quelques secondes).
  4. Fichier dont le mtime est artificiellement vieux → age cohérent.
  5. En weekend + count > 0 + state stale → ``state["positions_state_stale"]
     = True`` ET notif URGENT envoyée (sous cooldown).
  6. En weekend + count > 0 + state frais → pas de flag stale, pas de notif
     supplémentaire.
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import time
from unittest.mock import patch

import pytest


_REAL_DATETIME = dt.datetime


class _FixedDatetime:
    def __init__(self, fixed_now: dt.datetime):
        self._fixed = fixed_now

    def now(self, tz=None):
        if tz is None:
            return self._fixed.replace(tzinfo=None)
        return self._fixed.astimezone(tz)

    def __getattr__(self, name):
        return getattr(_REAL_DATETIME, name)


@pytest.fixture
def watchdog(tmp_path, monkeypatch):
    import scripts.feed_watchdog as wd
    importlib.reload(wd)
    monkeypatch.setattr(wd, "STATE", tmp_path / "feed_watchdog_state.json")
    monkeypatch.setattr(wd, "RESTART_HISTORY", tmp_path / "restart_history.jsonl")
    monkeypatch.setattr(wd, "SECRETS", tmp_path / "secrets.yaml")
    monkeypatch.setattr(wd, "POSITIONS_STATE", tmp_path / "position_monitor_state.json")
    monkeypatch.setattr(wd, "RESTART_STOP_SLEEP_S", 0)
    return wd, tmp_path


def _saturday_noon() -> dt.datetime:
    return dt.datetime(2026, 5, 23, 12, 0, 0, tzinfo=dt.timezone.utc)


def _write_positions_state(path, positions: list[dict]):
    if not positions:
        if path.exists():
            path.unlink()
        return
    state = {
        f"{p['broker_id']}:{p['position_id']}": p for p in positions
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


# ---------------------------------------------------------------------------
# Invariant 1 — absent → None
# ---------------------------------------------------------------------------

def test_age_returns_none_when_absent(watchdog):
    wd, _ = watchdog
    now = _saturday_noon()
    assert wd._positions_state_age_seconds(now) is None


# ---------------------------------------------------------------------------
# Invariant 2 + 3 — fichier frais → age petit
# ---------------------------------------------------------------------------

def test_age_returns_small_int_when_fresh(watchdog):
    wd, _ = watchdog
    _write_positions_state(wd.POSITIONS_STATE, [
        {"broker_id": "ftmo", "position_id": "P1", "symbol": "DASHUSD"},
    ])
    # Utiliser un now réel pour comparer au mtime réel
    now = dt.datetime.now(dt.timezone.utc)
    age = wd._positions_state_age_seconds(now)
    assert age is not None
    assert age >= 0
    assert age < 5, f"Fichier juste cree devrait avoir age < 5s, vu {age}s"


# ---------------------------------------------------------------------------
# Invariant 4 — mtime artificiellement vieux → age cohérent
# ---------------------------------------------------------------------------

def test_age_reflects_old_mtime(watchdog):
    wd, _ = watchdog
    _write_positions_state(wd.POSITIONS_STATE, [
        {"broker_id": "ftmo", "position_id": "P1", "symbol": "DASHUSD"},
    ])
    # Reculer mtime de 30 min
    old_mtime = time.time() - 1800
    os.utime(wd.POSITIONS_STATE, (old_mtime, old_mtime))

    now = dt.datetime.now(dt.timezone.utc)
    age = wd._positions_state_age_seconds(now)
    assert age is not None
    assert 1750 < age < 1850, f"Age ~1800s attendu, vu {age}s"


# ---------------------------------------------------------------------------
# Invariant 5 — weekend + count > 0 + stale → flag + alerte
# ---------------------------------------------------------------------------

def test_weekend_stale_state_flags_and_alerts(watchdog):
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    _write_positions_state(wd.POSITIONS_STATE, [
        {"broker_id": "ftmo", "position_id": "P1", "symbol": "DASHUSD"},
    ])
    # Forcer mtime à -20 min (> 10 min seuil)
    old_mtime = sat.timestamp() - 1200
    os.utime(wd.POSITIONS_STATE, (old_mtime, old_mtime))

    alerts = []

    def fake_send(body, title, urgent=False):
        alerts.append({"body": body, "title": title, "urgent": urgent})
        return True

    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 30), \
         patch.object(wd, "_send_alert", fake_send), \
         patch.object(wd.dt, "datetime", _FixedDatetime(sat)):
        wd.main()

    state = json.loads(wd.STATE.read_text())
    assert state.get("positions_state_stale") is True, (
        f"state.positions_state_stale doit etre True, vu state={state!r}"
    )
    assert any(
        ("stale" in a["body"].lower() or "monitor" in a["body"].lower()
         or "fige" in a["body"].lower() or "fig" in a["body"].lower())
        for a in alerts
    ), f"Alerte mentionnant le monitor figé/stale attendue, vu {alerts!r}"


# ---------------------------------------------------------------------------
# Invariant 6 — weekend + count > 0 + frais → pas de flag stale
# ---------------------------------------------------------------------------

def test_weekend_fresh_state_no_stale_flag(watchdog):
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    _write_positions_state(wd.POSITIONS_STATE, [
        {"broker_id": "ftmo", "position_id": "P1", "symbol": "DASHUSD"},
    ])
    # Forcer mtime à -1 min (< 10 min seuil)
    fresh_mtime = sat.timestamp() - 60
    os.utime(wd.POSITIONS_STATE, (fresh_mtime, fresh_mtime))

    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 30), \
         patch.object(wd, "_send_alert", lambda *a, **kw: True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(sat)):
        wd.main()

    state = json.loads(wd.STATE.read_text())
    assert state.get("positions_state_stale") is not True, (
        f"State frais ne doit pas declencher positions_state_stale, "
        f"vu state={state!r}"
    )


# ---------------------------------------------------------------------------
# Invariant non-régression — absence (= 0 position légitime) ne flag PAS
# ---------------------------------------------------------------------------

def test_weekend_absent_state_no_stale_flag(watchdog):
    """Le fichier absent est la sémantique légitime "0 position" (cf
    LivePositionMonitor.save_state supprime le fichier quand _positions vide).
    Cela ne doit JAMAIS déclencher positions_state_stale."""
    wd, _ = watchdog
    sat = _saturday_noon()
    # Pas de fichier

    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 30), \
         patch.object(wd, "_send_alert", lambda *a, **kw: True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(sat)):
        wd.main()

    state = json.loads(wd.STATE.read_text())
    assert state.get("positions_state_stale") is not True
    # En plus : doit être en weekend_guard (skip total) comme avant
    assert state.get("last_status") == "weekend_guard"
