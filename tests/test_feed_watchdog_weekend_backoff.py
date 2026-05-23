"""Hot Path Mode étape 2 bis — backoff progressif des restart auto en weekend
avec positions ouvertes (refinement task #36).

Le design initial désactivait entièrement l'auto-restart en weekend+position.
Refinement 2026-05-23 (correction user) : on garde le filet de sécurité du
restart mais on espace progressivement les tentatives. cTrader accepte les
sessions weekend mais leur comportement est erratique (feed quote fermé,
login/reconnect intermittents) — un restart peut malgré tout sortir d'une
boucle de reconnect patinante, mais le répéter à pleine cadence est
contre-productif.

Schéma de backoff (compteur N dans une fenêtre 24h glissante) :
  N=0 → 1er restart au seuil standard (persistance ≥ 30min)
  N=1 → 2e restart si persistance ≥ 60min
  N=2 → 3e restart si persistance ≥ 120min
  N=3 → 4e restart si persistance ≥ 240min (= 4h, cap)
  N≥4 → bloqué, escalade anti-boucle URGENT (intervention humaine)

Implémentation : ``WEEKEND_BACKOFF_THRESHOLDS_MIN = [30, 60, 120, 240]``
indexé par N. Les entrées RESTART_HISTORY déclenchées en weekend portent
``weekend=True`` ; ``_recent_weekend_restart_count(now, 24h)`` filtre dessus.

Invariants verrouillés :
  1. N=0 → seuil standard (30 min), comportement identique au weekday.
  2. Seuil weekend strict : persistance < seuil → pas de restart.
  3. Seuil weekend franchi → restart fire + tag ``weekend=True`` dans history.
  4. N=4 dans 24h → pas de restart, anti-boucle URGENT distincte.
  5. Restarts weekday (sans ``weekend=True``) ne comptent PAS dans le backoff
     weekend → première tentative weekend reste à 30 min même si plusieurs
     restarts weekday récents.
  6. Restarts weekend hors fenêtre 24h ne comptent pas.
  7. Restarts failed (``outcome=failed``) comptent dans le backoff weekend
     (même cause = espacement, pas de re-tentative immédiate).
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


def _seed_position(wd, tmp_path):
    wd.POSITIONS_STATE.parent.mkdir(parents=True, exist_ok=True)
    wd.POSITIONS_STATE.write_text(json.dumps({
        "ftmo:P1": {"broker_id": "ftmo", "position_id": "P1", "symbol": "DASHUSD"}
    }))


def _seed_stale_state(wd, sat: dt.datetime, persistence_min: int):
    wd.STATE.parent.mkdir(parents=True, exist_ok=True)
    wd.STATE.write_text(json.dumps({
        "feed_stale_since_ts": (sat - dt.timedelta(minutes=persistence_min)).isoformat()
    }))


def _seed_history(wd, entries: list[dict]):
    wd.RESTART_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    if entries:
        wd.RESTART_HISTORY.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n"
        )


def _weekend_entry(ts: dt.datetime, outcome: str = "ok", reason: str = "test") -> dict:
    return {"ts": ts.isoformat(), "outcome": outcome, "reason": reason, "weekend": True}


def _weekday_entry(ts: dt.datetime, outcome: str = "ok", reason: str = "test") -> dict:
    return {"ts": ts.isoformat(), "outcome": outcome, "reason": reason}


def _run_with_patches(wd, now: dt.datetime, *, age_min: int,
                     restart_calls: list, sent_alerts: list,
                     restart_outcome: tuple[bool, str] = (True, "ok")):
    with patch.object(wd, "_engine_active", lambda: True), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: age_min * 60), \
         patch.object(wd, "_attempt_auto_restart",
                      lambda now_, reason, weekend=False:
                          restart_calls.append({"reason": reason, "weekend": weekend})
                          or restart_outcome), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False:
                          sent_alerts.append({"title": title, "urgent": urgent, "body": body})
                          or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()


# ---------------------------------------------------------------------------
# Constantes attendues — verrouille le contrat du module
# ---------------------------------------------------------------------------


def test_weekend_backoff_thresholds_constant_shape(watchdog):
    wd, _ = watchdog
    assert wd.WEEKEND_BACKOFF_THRESHOLDS_MIN == [30, 60, 120, 240], (
        "Courbe backoff weekend : 30 → 60 → 120 → 240 min"
    )
    assert wd.WEEKEND_RESTART_MAX_24H == 4, (
        "Cap anti-boucle weekend : 4 restarts max dans 24h"
    )
    assert wd.WEEKEND_BACKOFF_WINDOW_S == 24 * 3600


# ---------------------------------------------------------------------------
# Invariant 1 — N=0 → seuil standard 30 min
# ---------------------------------------------------------------------------


def test_first_weekend_attempt_fires_at_standard_30min_threshold(watchdog):
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    _seed_position(wd, tmp_path)
    _seed_stale_state(wd, sat, persistence_min=35)

    restart_calls, sent = [], []
    _run_with_patches(wd, sat, age_min=35, restart_calls=restart_calls, sent_alerts=sent)

    assert len(restart_calls) == 1, "N=0 : seuil standard 30min → restart fire à 35min"
    assert restart_calls[0]["weekend"] is True, "Tag weekend=True passé à _attempt_auto_restart"


def test_first_weekend_attempt_blocked_under_30min_persistence(watchdog):
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    _seed_position(wd, tmp_path)
    _seed_stale_state(wd, sat, persistence_min=20)

    restart_calls, sent = [], []
    _run_with_patches(wd, sat, age_min=20, restart_calls=restart_calls, sent_alerts=sent)

    assert restart_calls == [], "Persistance 20min < seuil 30min → pas de restart"


# ---------------------------------------------------------------------------
# Invariant 2+3 — backoff strict : N=1 exige 60min, N=2 → 120min, N=3 → 240min
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_recent,persistence_min,should_fire", [
    (1, 45, False),    # N=1 → seuil 60min, persistance 45 → bloqué
    (1, 65, True),     # N=1 → seuil 60min, persistance 65 → fire
    (2, 90, False),    # N=2 → seuil 120min, persistance 90 → bloqué
    (2, 130, True),    # N=2 → seuil 120min, persistance 130 → fire
    (3, 200, False),   # N=3 → seuil 240min, persistance 200 → bloqué
    (3, 250, True),    # N=3 → seuil 240min, persistance 250 → fire
])
def test_weekend_backoff_thresholds_enforced(watchdog, n_recent, persistence_min, should_fire):
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    _seed_position(wd, tmp_path)
    _seed_stale_state(wd, sat, persistence_min=persistence_min)

    history = [
        _weekend_entry(sat - dt.timedelta(hours=i + 1))
        for i in range(n_recent)
    ]
    _seed_history(wd, history)

    restart_calls, sent = [], []
    _run_with_patches(wd, sat, age_min=persistence_min,
                      restart_calls=restart_calls, sent_alerts=sent)

    if should_fire:
        assert len(restart_calls) == 1, (
            f"N={n_recent} seuil={wd.WEEKEND_BACKOFF_THRESHOLDS_MIN[n_recent]}min, "
            f"persistance={persistence_min}min → restart attendu"
        )
        assert restart_calls[0]["weekend"] is True
    else:
        assert restart_calls == [], (
            f"N={n_recent} seuil={wd.WEEKEND_BACKOFF_THRESHOLDS_MIN[n_recent]}min, "
            f"persistance={persistence_min}min → restart bloqué attendu"
        )


# ---------------------------------------------------------------------------
# Invariant 4 — N=4 (cap) → bloqué + anti-boucle URGENT distincte
# ---------------------------------------------------------------------------


def test_weekend_cap_blocks_fifth_restart_with_urgent_alert(watchdog):
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    _seed_position(wd, tmp_path)
    _seed_stale_state(wd, sat, persistence_min=300)

    # 4 restarts weekend dans 24h (cap atteint)
    history = [
        _weekend_entry(sat - dt.timedelta(hours=h))
        for h in (1, 4, 8, 16)
    ]
    _seed_history(wd, history)

    restart_calls, sent = [], []
    _run_with_patches(wd, sat, age_min=300, restart_calls=restart_calls, sent_alerts=sent)

    assert restart_calls == [], "N=4 dans 24h → restart bloqué (anti-boucle weekend)"
    assert len(sent) == 1, "Anti-boucle weekend → exactement 1 alerte URGENT"
    assert sent[0]["urgent"] is True
    body = sent[0]["body"].lower()
    title = sent[0]["title"].lower()
    assert "anti-boucle" in body or "anti-boucle" in title, (
        f"Alerte anti-boucle distincte attendue (body={body[:120]} title={title})"
    )
    assert "weekend" in body or "weekend" in title

    # L'évènement doit être loggué pour audit
    lines = [json.loads(l) for l in wd.RESTART_HISTORY.read_text().splitlines() if l.strip()]
    skipped = [
        e for e in lines
        if e.get("outcome") == "skipped_weekend_backoff" and e.get("weekend") is True
    ]
    assert len(skipped) == 1, "skipped_weekend_backoff loggué"


# ---------------------------------------------------------------------------
# Invariant 5 — restarts weekday ne comptent pas dans le backoff weekend
# ---------------------------------------------------------------------------


def test_weekday_restarts_do_not_count_in_weekend_backoff(watchdog):
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    _seed_position(wd, tmp_path)
    _seed_stale_state(wd, sat, persistence_min=35)

    # 2 restarts weekday récents (pas de tag weekend) → N_weekend = 0
    history = [
        _weekday_entry(sat - dt.timedelta(minutes=m))
        for m in (30, 90)
    ]
    _seed_history(wd, history)

    restart_calls, sent = [], []
    _run_with_patches(wd, sat, age_min=35, restart_calls=restart_calls, sent_alerts=sent)

    assert len(restart_calls) == 1, (
        "Restarts weekday ne comptent pas en weekend → 1er attempt au seuil 30min"
    )


# ---------------------------------------------------------------------------
# Invariant 6 — restarts weekend > 24h hors fenêtre
# ---------------------------------------------------------------------------


def test_old_weekend_restarts_outside_24h_window_do_not_count(watchdog):
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    _seed_position(wd, tmp_path)
    _seed_stale_state(wd, sat, persistence_min=35)

    # 3 restarts weekend mais tous > 24h → N = 0
    history = [
        _weekend_entry(sat - dt.timedelta(hours=h))
        for h in (25, 30, 48)
    ]
    _seed_history(wd, history)

    restart_calls, sent = [], []
    _run_with_patches(wd, sat, age_min=35, restart_calls=restart_calls, sent_alerts=sent)

    assert len(restart_calls) == 1, "Restarts > 24h hors fenêtre, N_weekend = 0"


# ---------------------------------------------------------------------------
# Invariant 7 — restarts failed comptent dans le backoff (même cause)
# ---------------------------------------------------------------------------


def test_failed_weekend_restarts_count_in_backoff(watchdog):
    wd, tmp_path = watchdog
    sat = _saturday_noon()
    _seed_position(wd, tmp_path)
    _seed_stale_state(wd, sat, persistence_min=45)

    # 1 restart failed récent → N_weekend = 1 → seuil 60min, persistance 45min → bloqué
    history = [
        _weekend_entry(sat - dt.timedelta(hours=1), outcome="failed"),
    ]
    _seed_history(wd, history)

    restart_calls, sent = [], []
    _run_with_patches(wd, sat, age_min=45, restart_calls=restart_calls, sent_alerts=sent)

    assert restart_calls == [], (
        "Failed weekend restart compte dans backoff : N=1 → seuil 60min → 45min bloqué"
    )


# ---------------------------------------------------------------------------
# Helper direct : _recent_weekend_restart_count
# ---------------------------------------------------------------------------


def test_weekend_restart_tag_propagated_end_to_end(watchdog):
    """End-to-end : un `_attempt_auto_restart(weekend=True)` réel doit poser
    le tag dans RESTART_HISTORY (vérifie qu'on n'a pas oublié de propager
    le kwarg jusqu'à `_append_restart_history`)."""
    wd, _ = watchdog
    sat = _saturday_noon()

    def _fake_subprocess(args, **kwargs):
        # Simule stop/start réussis
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    with patch.object(wd.subprocess, "run", _fake_subprocess):
        ok, msg = wd._attempt_auto_restart(sat, reason="test_e2e", weekend=True)

    assert ok is True, f"Restart simulé doit réussir, reçu msg={msg}"
    lines = [json.loads(l) for l in wd.RESTART_HISTORY.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    assert lines[0]["outcome"] == "ok"
    assert lines[0]["weekend"] is True, (
        "Le tag weekend=True doit être propagé jusqu'à _append_restart_history"
    )


def test_weekend_restart_does_not_pollute_weekday_counter(watchdog):
    """Un restart taggé weekend ne doit PAS compter dans `_recent_restart_count`
    (compteur weekday 1h) — sinon un weekend chargé bloquerait à tort le 1er
    restart weekday légitime juste après (transition dimanche 22 UTC → lundi)."""
    wd, _ = watchdog
    sat = _saturday_noon()

    # 3 restarts weekend récents
    history = [
        _weekend_entry(sat - dt.timedelta(minutes=m))
        for m in (10, 20, 30)
    ]
    _seed_history(wd, history)

    # Compteur weekday doit voir 0 (filtre weekend=True)
    n_weekday = wd._recent_restart_count(sat, window_s=3600)
    assert n_weekday == 0, (
        f"Le compteur weekday doit ignorer les restarts weekend, reçu {n_weekday}"
    )

    # Compteur weekend doit voir 3
    n_weekend = wd._recent_weekend_restart_count(sat)
    assert n_weekend == 3


def test_recent_weekend_restart_count_filters_correctly(watchdog):
    wd, tmp_path = watchdog
    sat = _saturday_noon()

    history = [
        _weekend_entry(sat - dt.timedelta(hours=1)),     # in window, weekend
        _weekend_entry(sat - dt.timedelta(hours=10)),    # in window, weekend
        _weekend_entry(sat - dt.timedelta(hours=30)),    # OUT (>24h)
        _weekday_entry(sat - dt.timedelta(hours=2)),     # in window, NO weekend tag
        _weekend_entry(sat - dt.timedelta(hours=3), outcome="failed"),  # in, failed counts
        _weekend_entry(sat - dt.timedelta(hours=4), outcome="skipped_loop_guard"),  # exclu
    ]
    _seed_history(wd, history)

    n = wd._recent_weekend_restart_count(sat)
    assert n == 3, (
        f"Attendu 3 (2 ok + 1 failed, dans 24h, weekend=True), reçu {n}"
    )
