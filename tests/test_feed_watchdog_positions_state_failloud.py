"""Task #40 patch #1 — fail-loud sur ``POSITIONS_STATE`` corrompu/illisible.

Contexte. Avant ce patch, ``_open_positions_count()`` retournait ``0`` pour
trois cas distincts :
  - fichier absent (sémantique légitime "0 position" écrite par
    ``LivePositionMonitor.save_state``) ;
  - JSON corrompu ;
  - JSON valide mais non-dict (ex: ``[]``, ``"foo"``).

Conséquence : en weekend avec un fichier corrompu, le watchdog basculait en
``weekend_guard`` (skip total). Or c'est exactement le scénario qu'on cherche
à éviter (cf incident DASHUSD 2026-05-20). Le fail-safe silencieux protège
*contre le spam d'alertes* mais *au prix de la surveillance feed* — l'inverse
du compromis voulu.

Patch : différencier sémantique "vide" (= absent, OK) vs "illisible"
(= corrompu, fail-loud). Nouvelle signature ``_open_positions_count() ->
tuple[int, bool]`` où le second élément vaut ``True`` ssi le fichier existait
mais était illisible / non-dict.

Invariants verrouillés :
  1. Fichier absent → ``(0, False)`` (sémantique vide = absent inchangée).
  2. Fichier JSON valide dict avec N entrées → ``(N, False)``.
  3. Fichier JSON corrompu (parse error) → ``(0, True)``.
  4. Fichier JSON valide mais non-dict (list, string, number) → ``(0, True)``.
  5. En weekend avec ``corrupted=True``, ``main()`` NE bascule PAS en
     ``weekend_guard`` skip total : il présume hot path (surveillance feed
     active) + notif Telegram URGENT signalant le state file corrompu.
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
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


# ---------------------------------------------------------------------------
# Invariant 1 — absent → (0, False)
# ---------------------------------------------------------------------------

def test_absent_returns_zero_not_corrupted(watchdog):
    wd, _ = watchdog
    count, corrupted = wd._open_positions_count()
    assert count == 0
    assert corrupted is False


# ---------------------------------------------------------------------------
# Invariant 2 — valide dict → (N, False)
# ---------------------------------------------------------------------------

def test_valid_dict_returns_count_not_corrupted(watchdog):
    wd, _ = watchdog
    wd.POSITIONS_STATE.parent.mkdir(parents=True, exist_ok=True)
    wd.POSITIONS_STATE.write_text(json.dumps({
        "ftmo:P1": {"symbol": "DASHUSD"},
        "ftmo:P2": {"symbol": "BTCUSD"},
        "gft:P3": {"symbol": "ETHUSD"},
    }))
    count, corrupted = wd._open_positions_count()
    assert count == 3
    assert corrupted is False


# ---------------------------------------------------------------------------
# Invariant 3 — JSON corrompu → (0, True)
# ---------------------------------------------------------------------------

def test_corrupted_json_returns_corrupted_flag(watchdog):
    wd, _ = watchdog
    wd.POSITIONS_STATE.parent.mkdir(parents=True, exist_ok=True)
    wd.POSITIONS_STATE.write_text("{ not valid json")
    count, corrupted = wd._open_positions_count()
    assert count == 0
    assert corrupted is True


# ---------------------------------------------------------------------------
# Invariant 4 — non-dict → (0, True)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    "[]",                # list
    '"string_payload"',  # string
    "42",                # number
    "null",              # null (json.loads → None)
])
def test_non_dict_returns_corrupted_flag(watchdog, payload):
    wd, _ = watchdog
    wd.POSITIONS_STATE.parent.mkdir(parents=True, exist_ok=True)
    wd.POSITIONS_STATE.write_text(payload)
    count, corrupted = wd._open_positions_count()
    assert count == 0
    assert corrupted is True, f"payload {payload!r} should be flagged corrupted"


# ---------------------------------------------------------------------------
# Invariant 5 — intégration : weekend + corrupted → ne skip PAS, présume hot path
# ---------------------------------------------------------------------------

def test_weekend_with_corrupted_state_does_not_skip(watchdog):
    """Weekend + state file corrompu → main() ne doit PAS écrire
    ``weekend_guard`` (= skip total). Doit basculer en surveillance active.

    Régression directe vs incident DASHUSD : un fail-safe silencieux qui
    retourne 0 en cas de corruption fait croire au watchdog qu'il n'y a
    aucune position → il skip le weekend → 0 surveillance.
    """
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    wd.POSITIONS_STATE.parent.mkdir(parents=True, exist_ok=True)
    wd.POSITIONS_STATE.write_text("{ corrupted")

    # Stubs : engine actif, feed OK (pour isoler la branche weekend)
    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 30), \
         patch.object(wd, "_send_alert", lambda *a, **kw: True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(sat)):
        wd.main()

    state = json.loads(wd.STATE.read_text())
    # Le status NE doit PAS être "weekend_guard" (= skip total).
    assert state.get("last_status") != "weekend_guard", (
        f"Weekend + state corrompu doit basculer en surveillance, vu "
        f"last_status={state.get('last_status')!r} (skip total = régression DASHUSD)"
    )


def test_weekend_with_corrupted_state_flags_status(watchdog):
    """Le status écrit doit refléter la corruption pour debug ultérieur."""
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    wd.POSITIONS_STATE.parent.mkdir(parents=True, exist_ok=True)
    wd.POSITIONS_STATE.write_text("[1,2,3]")  # non-dict

    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 30), \
         patch.object(wd, "_send_alert", lambda *a, **kw: True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(sat)):
        wd.main()

    state = json.loads(wd.STATE.read_text())
    assert state.get("positions_state_corrupted") is True, (
        f"State file doit tagger positions_state_corrupted=True, vu state={state!r}"
    )


def test_weekend_with_corrupted_state_alerts_user(watchdog):
    """Le watchdog doit notifier l'humain (1 fois, sous cooldown) que le
    state file est corrompu — sinon le fail-loud se fait sans qu'on le sache."""
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    wd.POSITIONS_STATE.parent.mkdir(parents=True, exist_ok=True)
    wd.POSITIONS_STATE.write_text("garbage")

    alerts = []

    def fake_send(body, title, urgent=False):
        alerts.append({"body": body, "title": title, "urgent": urgent})
        return True

    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 30), \
         patch.object(wd, "_send_alert", fake_send), \
         patch.object(wd.dt, "datetime", _FixedDatetime(sat)):
        wd.main()

    # Au moins une alerte mentionnant la corruption / state file
    assert any(
        ("corromp" in a["body"].lower() or "state" in a["body"].lower())
        and a["urgent"]
        for a in alerts
    ), f"Alerte corruption attendue (urgent), vu {alerts!r}"


# ---------------------------------------------------------------------------
# Invariant non-régression — weekend + 0 position (absent) reste skip total
# ---------------------------------------------------------------------------

def test_weekend_with_absent_state_still_skips_as_before(watchdog):
    """Régression check : un fichier absent (= 0 position légitime, sémantique
    de save_state) doit toujours skip le weekend. C'est le comportement
    historique préservé."""
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    # Pas de fichier = 0 position
    assert not wd.POSITIONS_STATE.exists()

    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 30), \
         patch.object(wd, "_send_alert", lambda *a, **kw: True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(sat)):
        wd.main()

    state = json.loads(wd.STATE.read_text())
    assert state.get("last_status") == "weekend_guard"
    assert "positions_state_corrupted" not in state
