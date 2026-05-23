"""Hot Path Mode étape 2 — skip weekend conditionné à 0 position (task #36).

Incident fondateur : 2026-05-20→22 DASHUSD #53110148. La position a traversé
le weekend (ouverte vendredi 22:00 UTC, fermée broker-side dimanche matin).
Le watchdog feed skippait inconditionnellement le weekend, donc le canal #1
(feed Protobuf) n'était plus surveillé — combiné au canal #3 aveugle, on
a perdu toute visibilité pendant ~16h.

Cette PR conditionne le skip weekend à ``open_positions_count == 0``. Dès
qu'une position traverse le weekend, le watchdog continue ses checks. À la
fermeture (au cycle suivant), il rebascule en skip weekend.

Note importante : en weekend avec position, l'auto-restart est maintenu mais
**espacé progressivement** (backoff 30/60/120/240 min — cf. fichier dédié
``test_feed_watchdog_weekend_backoff.py``). Justification : cTrader accepte
les sessions weekend mais leur comportement est erratique (feed quote fermé,
login/reconnect intermittents) — un restart peut malgré tout sortir d'une
boucle de reconnect patinante, mais le répéter à pleine cadence est
contre-productif. Au 5e restart dans 24h, anti-boucle URGENT distincte.

Invariants verrouillés :
  1. Weekend + 0 position → skip total (status `weekend_guard`, comportement
     historique préservé).
  2. Weekend + ≥ 1 position → status `weekend_guard_with_positions`, les
     checks BarAggregator/alertes continuent normalement.
  3. Weekend + position + feed_stale détecté au 1er passage (aucun restart
     weekend récent) → auto-restart fire au seuil standard 30 min, avec tag
     ``weekend=True`` dans l'historique. Backoff progressif assuré par les
     tests dédiés (``test_feed_watchdog_weekend_backoff.py``).
  4. Helper ``_open_positions_count`` retourne ``(0, False)`` si state file
     absent (sémantique légitime "vide").
  5. Helper ``_open_positions_count`` retourne ``(len(dict), False)`` si
     state file valide.
  6. Helper ``_open_positions_count`` retourne ``(0, True)`` si le state
     file est corrompu / illisible. Task #40 patch #1 — bascule fail-safe →
     fail-loud : le caller doit présumer hot path plutôt que skip weekend
     (sinon régression DASHUSD). Voir
     ``tests/test_feed_watchdog_positions_state_failloud.py`` pour les
     invariants caller-side.
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
    """Charge feed_watchdog avec STATE / RESTART_HISTORY / SECRETS / état
    positions redirigés vers tmp_path."""
    import scripts.feed_watchdog as wd
    importlib.reload(wd)
    monkeypatch.setattr(wd, "STATE", tmp_path / "feed_watchdog_state.json")
    monkeypatch.setattr(wd, "RESTART_HISTORY", tmp_path / "restart_history.jsonl")
    monkeypatch.setattr(wd, "SECRETS", tmp_path / "secrets.yaml")
    monkeypatch.setattr(wd, "POSITIONS_STATE", tmp_path / "position_monitor_state.json")
    monkeypatch.setattr(wd, "RESTART_STOP_SLEEP_S", 0)
    return wd, tmp_path


def _saturday_noon() -> dt.datetime:
    """Samedi 23 mai 2026, 12:00 UTC — bien en weekend."""
    return dt.datetime(2026, 5, 23, 12, 0, 0, tzinfo=dt.timezone.utc)


def _tuesday_noon() -> dt.datetime:
    return dt.datetime(2026, 5, 19, 12, 0, 0, tzinfo=dt.timezone.utc)


def _write_positions_state(path, positions: list[dict]):
    """Écrit un state file au format produit par ``LivePositionMonitor.save_state``."""
    if not positions:
        # Sémantique : 0 position = fichier absent
        if path.exists():
            path.unlink()
        return
    state = {
        f"{p['broker_id']}:{p['position_id']}": p for p in positions
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


# ---------------------------------------------------------------------------
# Helper _open_positions_count
# ---------------------------------------------------------------------------


def test_open_positions_count_returns_zero_when_state_file_absent(watchdog):
    wd, _ = watchdog
    assert wd._open_positions_count() == (0, False)


def test_open_positions_count_reads_valid_state_file(watchdog):
    wd, tmp_path = watchdog
    _write_positions_state(wd.POSITIONS_STATE, [
        {"broker_id": "ftmo", "position_id": "P1", "symbol": "DASHUSD"},
        {"broker_id": "ftmo", "position_id": "P2", "symbol": "BTCUSD"},
    ])
    assert wd._open_positions_count() == (2, False)


def test_open_positions_count_failloud_on_corrupted_file(watchdog):
    """Task #40 patch #1 — un JSON corrompu doit lever le flag
    ``corrupted=True``. Le caller (main) bascule alors en hot path présumé
    + notif URGENT plutôt que skip silencieusement (régression DASHUSD)."""
    wd, tmp_path = watchdog
    wd.POSITIONS_STATE.parent.mkdir(parents=True, exist_ok=True)
    wd.POSITIONS_STATE.write_text("{ not valid json")
    assert wd._open_positions_count() == (0, True)


# ---------------------------------------------------------------------------
# Invariant 1 — weekend + 0 position → skip total
# ---------------------------------------------------------------------------


def test_weekend_with_zero_positions_skips_as_before(watchdog):
    wd, tmp_path = watchdog
    # Pas de state file = 0 position
    sat = _saturday_noon()
    called = {"bar_check": 0, "alerts": 0}

    def _spy_bar(_now):
        called["bar_check"] += 1
        return 60 * 60  # stale, mais on ne doit jamais arriver ici

    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", _spy_bar), \
         patch.object(wd, "_send_alert",
                      lambda b, t, urgent=False: called.__setitem__(
                          "alerts", called["alerts"] + 1) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(sat)):
        wd.main()

    state = json.loads(wd.STATE.read_text())
    assert state["last_status"] == "weekend_guard"
    assert called["bar_check"] == 0, "weekend+0pos doit skipper avant le check barres"
    assert called["alerts"] == 0


# ---------------------------------------------------------------------------
# Invariant 2 — weekend + ≥1 position → surveillance active
# ---------------------------------------------------------------------------


def test_weekend_with_one_position_runs_normal_checks(watchdog):
    wd, tmp_path = watchdog
    _write_positions_state(wd.POSITIONS_STATE, [
        {"broker_id": "ftmo", "position_id": "P1", "symbol": "DASHUSD"},
    ])
    sat = _saturday_noon()
    called = {"bar_check": 0}

    def _spy_bar(_now):
        called["bar_check"] += 1
        return 5 * 60  # OK, age 5 min

    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", _spy_bar), \
         patch.object(wd, "_send_alert", lambda b, t, urgent=False: True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(sat)):
        wd.main()

    state = json.loads(wd.STATE.read_text())
    assert "weekend_guard_with_positions" in state["last_status"] or \
           state["last_status"].startswith("ok:"), \
           f"checks doivent tourner; status={state['last_status']}"
    assert called["bar_check"] == 1, "le check barre doit avoir été exécuté"


def test_weekend_with_position_logs_open_count_in_state(watchdog):
    wd, tmp_path = watchdog
    _write_positions_state(wd.POSITIONS_STATE, [
        {"broker_id": "ftmo", "position_id": "P1", "symbol": "DASHUSD"},
        {"broker_id": "gft", "position_id": "X1", "symbol": "EURUSD"},
    ])
    sat = _saturday_noon()

    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 5 * 60), \
         patch.object(wd, "_send_alert", lambda b, t, urgent=False: True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(sat)):
        wd.main()

    state = json.loads(wd.STATE.read_text())
    assert state.get("open_positions_count") == 2


# ---------------------------------------------------------------------------
# Invariant 3 — weekend + position + feed_stale → alerte mais PAS d'auto-restart
# ---------------------------------------------------------------------------


def test_weekend_with_position_and_stale_fires_first_restart_at_standard_threshold(watchdog):
    """Au 1er stale en weekend+position (aucun restart weekend récent),
    l'auto-restart fire au seuil standard 30 min, taggé ``weekend=True``.
    Le backoff progressif est testé séparément dans
    ``test_feed_watchdog_weekend_backoff.py``."""
    wd, tmp_path = watchdog
    _write_positions_state(wd.POSITIONS_STATE, [
        {"broker_id": "ftmo", "position_id": "P1", "symbol": "DASHUSD"},
    ])
    sat = _saturday_noon()
    # Tracker déjà posé il y a 40 min (au-delà du seuil 30 min) ; aucun restart weekend récent
    wd.STATE.parent.mkdir(parents=True, exist_ok=True)
    wd.STATE.write_text(json.dumps({
        "feed_stale_since_ts": (sat - dt.timedelta(minutes=40)).isoformat()
    }))

    restart_calls: list[dict] = []
    alerts: list[tuple] = []

    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 40 * 60), \
         patch.object(wd, "_attempt_auto_restart",
                      lambda now_, reason, weekend=False:
                          restart_calls.append({"reason": reason, "weekend": weekend})
                          or (True, "ok")), \
         patch.object(wd, "_send_alert",
                      lambda b, t, urgent=False: alerts.append((t, urgent, b)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(sat)):
        wd.main()

    assert len(restart_calls) == 1, (
        "1er restart weekend autorisé au seuil standard 30 min (backoff "
        "ne s'active qu'à partir du 2e — cf test dédié weekend_backoff)"
    )
    assert restart_calls[0]["weekend"] is True, (
        "Le restart weekend doit être taggé pour entrer dans le compteur backoff"
    )
    assert len(alerts) == 1, "Alerte URGENT auto-restart envoyée"
    _, urgent, body = alerts[0]
    assert urgent is True, "Auto-restart = notif URGENT"
    # L'alerte doit indiquer le contexte weekend (pour distinguer du flux weekday)
    assert "weekend" in body.lower() or "weekend" in alerts[0][0].lower()


# ---------------------------------------------------------------------------
# Invariant 4 — fermeture position en weekend → cycle suivant skip à nouveau
# ---------------------------------------------------------------------------


def test_position_closing_during_weekend_reverts_to_skip(watchdog):
    """Cycle 1 (position ouverte) : surveillance active.
    Cycle 2 (position fermée) : retour au skip weekend."""
    wd, tmp_path = watchdog
    sat = _saturday_noon()

    # Cycle 1 — 1 position ouverte
    _write_positions_state(wd.POSITIONS_STATE, [
        {"broker_id": "ftmo", "position_id": "P1", "symbol": "DASHUSD"},
    ])
    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 3 * 60), \
         patch.object(wd, "_send_alert", lambda b, t, urgent=False: True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(sat)):
        wd.main()
    state1 = json.loads(wd.STATE.read_text())
    assert "weekend_guard_with_positions" in state1["last_status"] or \
           state1["last_status"].startswith("ok:")

    # Cycle 2 — position fermée (state file supprimé par save_state)
    _write_positions_state(wd.POSITIONS_STATE, [])
    later = sat + dt.timedelta(minutes=15)
    # Patch _last_bar_age_seconds explicitement pour rendre le test hermétique
    # (évite l'appel subprocess `journalctl` réel si on n'atteint pas le skip).
    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 0), \
         patch.object(wd.dt, "datetime", _FixedDatetime(later)):
        wd.main()
    state2 = json.loads(wd.STATE.read_text())
    assert state2["last_status"] == "weekend_guard"
    # Le compteur open_positions_count doit être absent ou 0
    assert state2.get("open_positions_count", 0) == 0
