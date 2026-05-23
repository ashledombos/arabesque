"""Task #40 patch #3 — helper ``_positions_state_age_seconds`` (mtime).

Historique. Le patch #3 initial (commit f65fbd4, 2026-05-23 18:15 UTC) flagait
``positions_state_stale=True`` + notif URGENT quand le mtime de
``logs/position_monitor_state.json`` dépassait 10 min en weekend avec position
ouverte. Hypothèse fausse : ``LivePositionMonitor.save_state`` n'est appelé
que sur register/unregister/reconcile-checkpoint, **pas périodiquement**. En
weekend avec position dormante, le fichier date forcément de l'ouverture →
faux positif systématique → spam URGENT toutes les 30 min (8 alertes
21:11→00:05 UTC sur la nuit du 2026-05-23). Hotfix 22:15 UTC : check mtime
retiré, à ré-instrumenter une fois le monitor patché pour checkpoint
périodique indépendant de l'activité.

Tests gardés : invariants 1-4 du helper (neutre, utilisable plus tard) +
tests de non-régression vérifiant que le flag ``positions_state_stale``
n'est **plus jamais** posé tant que le check n'est pas ré-instrumenté.

Invariants verrouillés :
  1. ``_positions_state_age_seconds`` retourne ``None`` si fichier absent.
  2. ``_positions_state_age_seconds`` retourne un ``int >= 0`` si présent.
  3. Fichier fraîchement écrit → age petit (< quelques secondes).
  4. Fichier dont le mtime est artificiellement vieux → age cohérent.
  5. **Non-régression hotfix** : weekend + count > 0 + state ancien (>10min)
     → **pas** de flag ``positions_state_stale``, **pas** de notif URGENT
     supplémentaire (le mode hot est déjà actif via count > 0).
  6. Weekend + count > 0 + state frais → pas de flag stale, comportement
     inchangé.
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
# Invariant 5 — non-régression hotfix : weekend + count > 0 + state ancien
# ne doit PAS poser le flag stale ni envoyer de notif "monitor fige"
# ---------------------------------------------------------------------------

def test_weekend_old_state_does_not_flag_after_hotfix(watchdog):
    """Hotfix 2026-05-23 22:15 UTC : le check mtime du patch #3 a été retiré
    car ``LivePositionMonitor.save_state`` n'est pas appelé périodiquement
    (uniquement register/unregister/reconcile-checkpoint). En weekend avec
    position dormante, mtime > 10 min est l'état normal → spam URGENT
    structurel (incident nuit 2026-05-23, 8 notifs en 3h)."""
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    _write_positions_state(wd.POSITIONS_STATE, [
        {"broker_id": "ftmo", "position_id": "P1", "symbol": "XAUUSD"},
    ])
    # Forcer mtime à -20 min (> 10 min ancien seuil)
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
    assert state.get("positions_state_stale") is not True, (
        f"Hotfix : le flag positions_state_stale ne doit PLUS etre pose "
        f"(spam URGENT structurel). Vu state={state!r}"
    )
    # Aucune notif "monitor fige" / "stale" ne doit être émise
    suspect = [
        a for a in alerts
        if "fige" in a["title"].lower() or "monitor" in a["title"].lower()
        or "stale" in a["body"].lower()
    ]
    assert suspect == [], (
        f"Hotfix : aucune notif 'monitor fige' / 'stale' ne doit etre emise "
        f"en weekend avec state mtime ancien. Vu {suspect!r}"
    )


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
