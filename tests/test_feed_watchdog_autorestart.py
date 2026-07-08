"""Watchdog feed Arabesque — étages 3+4 résilience (auto-restart + anti-boucle).

Incident fondateur : 2026-05-21T22:59 → 2026-05-22T18:55 UTC (19h54 de feed
FTMO mort, refresh_token in-memory désynchro disque). Le watchdog v1 alertait
toutes les 30 min mais ne faisait rien — l'utilisateur portait seul la charge
de surveillance pendant 19h54. Cf docs/INCIDENT_DASHUSD_RESILIENCE_BROKER_2026-05-21.md.

Étages livrés (task #31 + task #33) :
  - Étage 3 : auto-restart systemctl --user stop+sleep+start si feed_stale
    persiste > 30 min.
  - Étage 4 : anti-boucle — max 2 restarts/heure ; le 3e bascule en alerte
    critique distincte (URGENT) sans relancer.

Invariants verrouillés :
  1. `feed_stale_since_ts` enregistré au 1er passage stale, persisté entre
     invocations watchdog.
  2. Persistance < 30 min → pas de restart (notif standard).
  3. Persistance ≥ 30 min + 0-1 restart récent → auto-restart + notif urgent.
  4. Persistance ≥ 30 min + ≥ 2 restarts récents → escalade anti-boucle
     (pas de 3e restart, notif distincte).
  5. `engine_inactive` reset le tracker feed_stale (pas du fait du feed).
  6. `weekend_guard` reset le tracker (marché fermé, pas un feed mort).
  7. `no_bar_data_in_window` (fenêtre vide) ≠ feed_stale → pas de restart.
  8. Restart historique persistant dans `logs/watchdog_restart_history.jsonl`
     (append-only, lisible par les invocations suivantes).
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
from unittest.mock import patch

import pytest


@pytest.fixture
def watchdog(tmp_path, monkeypatch):
    """Charge feed_watchdog avec STATE et RESTART_HISTORY redirigés vers tmp_path.

    Évite la pollution du fichier réel `logs/feed_watchdog_state.json` qui
    sert au timer systemd en production.
    """
    import scripts.feed_watchdog as wd
    importlib.reload(wd)
    monkeypatch.setattr(wd, "STATE", tmp_path / "feed_watchdog_state.json")
    monkeypatch.setattr(wd, "RESTART_HISTORY", tmp_path / "restart_history.jsonl")
    monkeypatch.setattr(wd, "SECRETS", tmp_path / "secrets.yaml")
    monkeypatch.setattr(wd, "UPTIME_EVENTS", tmp_path / "uptime_events.jsonl")
    # Isole du state file de production (Hot Path #2) — sinon les positions
    # ouvertes en dev/live font fuiter la branche `weekend_with_positions`.
    monkeypatch.setattr(wd, "POSITIONS_STATE", tmp_path / "position_monitor_state.json")
    # Pas de sleep réel pendant les tests (RESTART_STOP_SLEEP_S=60s par défaut)
    monkeypatch.setattr(wd, "RESTART_STOP_SLEEP_S", 0)
    return wd


def _no_weekend_now() -> dt.datetime:
    """Mardi midi UTC — clairement hors weekend guard."""
    return dt.datetime(2026, 5, 19, 12, 0, 0, tzinfo=dt.timezone.utc)


def _engine_active_true(wd):
    return lambda: True


def _make_state_fixture(wd, state: dict) -> None:
    wd.STATE.parent.mkdir(parents=True, exist_ok=True)
    wd.STATE.write_text(json.dumps(state))


# ---------------------------------------------------------------------------
# 1. Tracker feed_stale_since_ts : posé au 1er passage stale
# ---------------------------------------------------------------------------

def test_feed_stale_tracker_initialized_first_pass(watchdog):
    wd = watchdog
    now = _no_weekend_now()

    sent_alerts = []

    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 20 * 60), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False: sent_alerts.append((title, urgent)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    state = json.loads(wd.STATE.read_text())
    assert "feed_stale_since_ts" in state, "1er passage stale doit poser le tracker"
    assert state["feed_stale_since_ts"] == now.isoformat()
    # Pas de restart (persistance = 0 min < 30 min)
    assert not wd.RESTART_HISTORY.exists() or wd.RESTART_HISTORY.read_text() == ""
    assert len(sent_alerts) == 1
    assert sent_alerts[0][1] is False, "1er passage = notif normale, pas urgent"


# ---------------------------------------------------------------------------
# 2. Persistance < 30 min → pas d'auto-restart
# ---------------------------------------------------------------------------

def test_no_restart_when_persistence_below_threshold(watchdog):
    wd = watchdog
    # 1er passage il y a 20 min, on est encore sous le seuil 30
    twenty_min_ago = _no_weekend_now() - dt.timedelta(minutes=20)
    _make_state_fixture(wd, {
        "feed_stale_since_ts": twenty_min_ago.isoformat(),
        # Pas de last_alert_ts → cooldown OK pour le test
    })

    restart_calls = []
    sent_alerts = []
    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 30 * 60), \
         patch.object(wd, "_attempt_auto_restart",
                      lambda now, reason: restart_calls.append(reason) or (True, "")), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False: sent_alerts.append((title, urgent)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(_no_weekend_now())):
        wd.main()

    assert restart_calls == [], "Persistance 20 min < 30 min → pas de restart"
    assert len(sent_alerts) == 1
    assert sent_alerts[0][1] is False, "Persistance sous seuil = notif normale"


# ---------------------------------------------------------------------------
# 3. Persistance ≥ 30 min → auto-restart (étage 3)
# ---------------------------------------------------------------------------

def test_autorestart_triggers_after_30min_persistence(watchdog):
    wd = watchdog
    now = _no_weekend_now()
    stale_since = now - dt.timedelta(minutes=35)
    _make_state_fixture(wd, {"feed_stale_since_ts": stale_since.isoformat()})

    restart_calls = []
    sent_alerts = []
    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 35 * 60), \
         patch.object(wd, "_attempt_auto_restart",
                      lambda now_, reason: restart_calls.append(reason) or (True, "ok")), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False: sent_alerts.append((title, urgent, body)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    assert len(restart_calls) == 1, "Persistance > 30 min doit déclencher 1 restart"
    assert len(sent_alerts) == 1
    title, urgent, body = sent_alerts[0]
    assert urgent is True, "Auto-restart = notif URGENT"
    assert "auto-restart" in title.lower()
    assert "Auto-restart engine" in body
    # Tracker doit être reset post-restart pour ne pas re-trigger immédiatement
    state = json.loads(wd.STATE.read_text())
    assert "feed_stale_since_ts" not in state, "Tracker reset après restart réussi"


def test_autorestart_blocked_when_position_open(watchdog):
    wd = watchdog
    now = _no_weekend_now()
    stale_since = now - dt.timedelta(minutes=35)
    _make_state_fixture(wd, {"feed_stale_since_ts": stale_since.isoformat()})
    wd.POSITIONS_STATE.write_text(json.dumps({
        "ftmo:P1": {"broker_id": "ftmo", "position_id": "P1", "symbol": "AUDJPY"}
    }))

    restart_calls = []
    sent_alerts = []
    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 35 * 60), \
         patch.object(wd, "_attempt_auto_restart",
                      lambda now_, reason: restart_calls.append(reason) or (True, "ok")), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False: sent_alerts.append((title, urgent, body)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    assert restart_calls == []
    assert len(sent_alerts) == 1
    title, urgent, body = sent_alerts[0]
    assert urgent is True
    assert "position ouverte" in title.lower()
    assert "Auto-restart bloque" in body
    state = json.loads(wd.STATE.read_text())
    assert state["open_positions_count"] == 1
    assert "manual_required_open_positions" in state["last_status"]


def test_trading_channel_dead_autorepairs_even_with_open_position(watchdog):
    wd = watchdog
    now = _no_weekend_now()
    wd.POSITIONS_STATE.write_text(json.dumps({
        "ftmo:BTC": {"broker_id": "ftmo_challenge", "position_id": "BTC", "symbol": "BTCUSD"}
    }))
    issue = {
        "kind": "reconcile_timeouts",
        "broker_id": "ftmo_challenge",
        "consecutive_timeouts": 18,
        "line": "reconcile broker ftmo_challenge : 18 timeouts consécutifs",
    }

    restart_calls = []
    sent_alerts = []
    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 60), \
         patch.object(wd, "_last_trading_channel_issue", lambda _now: issue), \
         patch.object(wd, "_attempt_auto_restart",
                      lambda now_, reason, weekend=False:
                      restart_calls.append((reason, weekend)) or (True, "ok")), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False:
                      sent_alerts.append((title, urgent, body)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    assert len(restart_calls) == 1
    assert "trading_channel_dead:reconcile_timeouts" in restart_calls[0][0]
    assert restart_calls[0][1] is False
    assert len(sent_alerts) == 1
    title, urgent, body = sent_alerts[0]
    assert urgent is True
    assert "auto-repair canal trading" in title
    assert "positions ouvertes trackees: 1" in body
    state = json.loads(wd.STATE.read_text())
    assert state["last_status"].endswith("+autorepair_ok")
    assert state["trading_channel_issue"]["kind"] == "reconcile_timeouts"


def test_trading_channel_loop_guard_blocks_third_repair(watchdog):
    wd = watchdog
    now = _no_weekend_now()
    wd.RESTART_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    wd.RESTART_HISTORY.write_text("\n".join(json.dumps(e) for e in [
        {"ts": (now - dt.timedelta(minutes=40)).isoformat(), "outcome": "ok", "reason": "old1"},
        {"ts": (now - dt.timedelta(minutes=10)).isoformat(), "outcome": "ok", "reason": "old2"},
    ]) + "\n")
    issue = {
        "kind": "amend_abandoned",
        "line": "SL amend ABANDONED after 3 attempts: BTCUSD [Amend timeout]",
    }

    restart_calls = []
    sent_alerts = []
    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 60), \
         patch.object(wd, "_last_trading_channel_issue", lambda _now: issue), \
         patch.object(wd, "_attempt_auto_restart",
                      lambda now_, reason, weekend=False:
                      restart_calls.append(reason) or (True, "ok")), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False:
                      sent_alerts.append((title, urgent, body)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    assert restart_calls == []
    assert len(sent_alerts) == 1
    title, urgent, body = sent_alerts[0]
    assert urgent is True
    assert "anti-boucle canal trading" in title
    assert "auto-repair bloquee" in body
    history = [json.loads(ln) for ln in wd.RESTART_HISTORY.read_text().splitlines()]
    assert any(e["outcome"] == "skipped_trading_channel_loop_guard" for e in history)


# ---------------------------------------------------------------------------
# 4. Anti-boucle (étage 4) : 3e restart bloqué + notif distincte
# ---------------------------------------------------------------------------

def test_loop_guard_blocks_third_restart_within_hour(watchdog):
    wd = watchdog
    now = _no_weekend_now()
    # 2 restarts récents dans l'historique
    wd.RESTART_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    history = [
        {"ts": (now - dt.timedelta(minutes=50)).isoformat(), "outcome": "ok", "reason": "test1"},
        {"ts": (now - dt.timedelta(minutes=20)).isoformat(), "outcome": "ok", "reason": "test2"},
    ]
    wd.RESTART_HISTORY.write_text("\n".join(json.dumps(e) for e in history) + "\n")

    stale_since = now - dt.timedelta(minutes=35)
    _make_state_fixture(wd, {"feed_stale_since_ts": stale_since.isoformat()})

    restart_calls = []
    sent_alerts = []
    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 35 * 60), \
         patch.object(wd, "_attempt_auto_restart",
                      lambda now_, reason: restart_calls.append(reason) or (True, "ok")), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False: sent_alerts.append((title, urgent, body)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    assert restart_calls == [], (
        "Anti-boucle : 3e restart dans l'heure ne doit PAS être tenté"
    )
    assert len(sent_alerts) == 1
    title, urgent, body = sent_alerts[0]
    assert urgent is True, "Anti-boucle = notif URGENT distincte"
    assert "anti-boucle" in title.lower() or "anti-boucle" in body.lower()
    assert "DECLENCHEE" in body

    # L'event skipped doit être loggué dans l'historique pour audit
    history_lines = [json.loads(ln) for ln in wd.RESTART_HISTORY.read_text().splitlines() if ln.strip()]
    skipped = [e for e in history_lines if e["outcome"] == "skipped_loop_guard"]
    assert len(skipped) == 1, "skipped_loop_guard doit être loggué"


# ---------------------------------------------------------------------------
# 5. Restarts hors fenêtre 1h ne comptent pas
# ---------------------------------------------------------------------------

def test_old_restarts_outside_window_do_not_block(watchdog):
    wd = watchdog
    now = _no_weekend_now()
    wd.RESTART_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    history = [
        # 2 restarts mais il y a > 1h → hors fenêtre
        {"ts": (now - dt.timedelta(hours=2)).isoformat(), "outcome": "ok", "reason": "old1"},
        {"ts": (now - dt.timedelta(hours=3)).isoformat(), "outcome": "ok", "reason": "old2"},
    ]
    wd.RESTART_HISTORY.write_text("\n".join(json.dumps(e) for e in history) + "\n")

    stale_since = now - dt.timedelta(minutes=35)
    _make_state_fixture(wd, {"feed_stale_since_ts": stale_since.isoformat()})

    restart_calls = []
    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 35 * 60), \
         patch.object(wd, "_attempt_auto_restart",
                      lambda now_, reason: restart_calls.append(reason) or (True, "ok")), \
         patch.object(wd, "_send_alert", lambda body, title, urgent=False: True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    assert len(restart_calls) == 1, (
        "Restarts > 1h ne comptent pas dans la fenêtre anti-boucle"
    )


# ---------------------------------------------------------------------------
# 6. Engine inactive → reset tracker
# ---------------------------------------------------------------------------

def test_engine_inactive_resets_feed_stale_tracker(watchdog):
    wd = watchdog
    now = _no_weekend_now()
    _make_state_fixture(wd, {
        "feed_stale_since_ts": (now - dt.timedelta(minutes=10)).isoformat()
    })

    with patch.object(wd, "_engine_active", lambda: False), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    state = json.loads(wd.STATE.read_text())
    assert state["last_status"] == "engine_inactive"
    assert "feed_stale_since_ts" not in state, (
        "engine_inactive ≠ feed mort, tracker doit être reset"
    )


# ---------------------------------------------------------------------------
# 7. Weekend guard → reset tracker + skip
# ---------------------------------------------------------------------------

def test_weekend_guard_resets_tracker_and_skips(watchdog):
    wd = watchdog
    # Samedi midi UTC (weekend)
    sat = dt.datetime(2026, 5, 23, 12, 0, 0, tzinfo=dt.timezone.utc)
    _make_state_fixture(wd, {
        "feed_stale_since_ts": (sat - dt.timedelta(minutes=20)).isoformat()
    })

    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd.dt, "datetime", _FixedDatetime(sat)):
        wd.main()

    state = json.loads(wd.STATE.read_text())
    assert state["last_status"] == "weekend_guard"
    assert "feed_stale_since_ts" not in state


def test_weekend_guard_starts_at_friday_21h_utc(watchdog):
    """Vendredi 21:00 UTC = début weekend (forex close)."""
    wd = watchdog
    # Vendredi 20:59 UTC → encore ouvert
    fri_2059 = dt.datetime(2026, 5, 22, 20, 59, 0, tzinfo=dt.timezone.utc)
    assert wd._is_weekend_utc(fri_2059) is False, "Vendredi 20:59 UTC = encore ouvert"
    # Vendredi 21:00 UTC → fermé
    fri_2100 = dt.datetime(2026, 5, 22, 21, 0, 0, tzinfo=dt.timezone.utc)
    assert wd._is_weekend_utc(fri_2100) is True, "Vendredi 21:00 UTC = weekend démarré"


# ---------------------------------------------------------------------------
# 8. no_bar_data_in_window ≠ feed_stale → pas de restart
# ---------------------------------------------------------------------------

def test_no_bar_data_does_not_trigger_restart(watchdog):
    wd = watchdog
    now = _no_weekend_now()
    # Pas d'état préexistant
    restart_calls = []
    sent_alerts = []

    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: None), \
         patch.object(wd, "_attempt_auto_restart",
                      lambda now_, reason: restart_calls.append(reason) or (True, "")), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False: sent_alerts.append((title, urgent)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    assert restart_calls == [], "no_bar_data_in_window ne doit PAS déclencher restart"
    state = json.loads(wd.STATE.read_text())
    assert state["last_status"] == "no_bar_data_in_window"
    # La notif est envoyée mais NON urgente
    assert len(sent_alerts) == 1 and sent_alerts[0][1] is False


# ---------------------------------------------------------------------------
# 9. Persistance multi-cycles : tracker conservé entre invocations
# ---------------------------------------------------------------------------

def test_persistence_accumulates_across_invocations(watchdog):
    wd = watchdog
    now = _no_weekend_now()

    # Cycle 1 : t=0, age=20min → tracker posé
    sent = []
    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 20 * 60), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False: sent.append(urgent) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()
    state1 = json.loads(wd.STATE.read_text())
    assert "feed_stale_since_ts" in state1

    # Cycle 2 : t=+35min, même état → persistance > 30 min → restart
    later = now + dt.timedelta(minutes=35)
    restart_calls = []
    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 55 * 60), \
         patch.object(wd, "_attempt_auto_restart",
                      lambda now_, reason: restart_calls.append(reason) or (True, "ok")), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False: sent.append(urgent) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(later)):
        wd.main()

    assert len(restart_calls) == 1, (
        "Cycle 2 : tracker conservé du cycle 1 → persistance 35 min → restart"
    )


# ---------------------------------------------------------------------------
# 10. Restart échoue → notif distincte, pas de boucle
# ---------------------------------------------------------------------------

def test_restart_failure_logs_and_notifies(watchdog):
    wd = watchdog
    now = _no_weekend_now()
    _make_state_fixture(wd, {
        "feed_stale_since_ts": (now - dt.timedelta(minutes=35)).isoformat()
    })

    sent = []
    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 35 * 60), \
         patch.object(wd, "_attempt_auto_restart",
                      lambda now_, reason: (False, "stop failed: permission denied")), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False: sent.append((title, urgent, body)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    assert len(sent) == 1
    title, urgent, body = sent[0]
    assert urgent is True
    assert "ECHEC" in title or "ECHEC" in body or "ECHOUE" in body

    # L'historique doit avoir une entrée "failed"
    history_lines = [json.loads(ln) for ln in wd.RESTART_HISTORY.read_text().splitlines() if ln.strip()]
    failed = [e for e in history_lines if e["outcome"] == "failed"]
    assert len(failed) == 1


# ---------------------------------------------------------------------------
# 11. Flux partiel : BarAggregator OK mais PriceFeed incomplet → notif simple
# ---------------------------------------------------------------------------

def test_pricefeed_partial_first_pass_is_gated_but_measured(watchdog):
    """Un flux partiel transitoire (1er passage) ne notifie PAS (préférence
    user 2026-07-03 — anti faux positifs) mais la mesure uptime continue."""
    wd = watchdog
    now = _no_weekend_now()
    sent = []
    restart_calls = []
    partial = {
        "ts": now.isoformat(),
        "age_seconds": 60,
        "active": 30,
        "total": 31,
        "dormant": 0,
        "stale_major": 1,
        "no_tick": 0,
    }

    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 60), \
         patch.object(wd, "_last_pricefeed_summary", lambda _now: partial), \
         patch.object(wd, "_attempt_auto_restart",
                      lambda now_, reason: restart_calls.append(reason) or (True, "ok")), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False: sent.append((title, urgent, body)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    assert restart_calls == []
    assert sent == []
    state = json.loads(wd.STATE.read_text())
    assert state["last_status"].startswith("pricefeed_partial:30/31")
    assert "persistence_gate" in state["last_status"]
    assert state["pricefeed_partial_since_ts"] == now.isoformat()
    uptime = json.loads(wd.UPTIME_EVENTS.read_text().splitlines()[0])
    assert uptime["event"] == "uptime_sample"
    assert uptime["cause"] == "partial_feed"
    assert uptime["pricefeed"]["active"] == 30


def test_pricefeed_partial_minor_notifies_after_long_persistence(watchdog):
    """Cas mineur (1 stale, 0 jamais reçu, ≥90% actifs) : notif seulement
    après PARTIAL_MINOR_NOTIFY_MIN de persistance."""
    wd = watchdog
    now = _no_weekend_now()
    sent = []
    partial = {
        "ts": now.isoformat(),
        "age_seconds": 60,
        "active": 30,
        "total": 31,
        "dormant": 0,
        "stale_major": 1,
        "no_tick": 0,
    }
    since = now - dt.timedelta(minutes=wd.PARTIAL_MINOR_NOTIFY_MIN + 5)
    wd.STATE.write_text(json.dumps(
        {"pricefeed_partial_since_ts": since.isoformat()}))

    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 60), \
         patch.object(wd, "_last_pricefeed_summary", lambda _now: partial), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False: sent.append((title, urgent, body)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    assert len(sent) == 1
    title, urgent, body = sent[0]
    assert urgent is False
    assert "persistant" in title.lower()
    assert "30/31 actifs" in body
    assert "Rien a faire" in body


def test_pricefeed_partial_major_uses_short_persistence(watchdog):
    """Cas majeur (no_tick > 0) : seuil court PARTIAL_NOTIFY_MIN suffit."""
    wd = watchdog
    now = _no_weekend_now()
    sent = []
    partial = {
        "ts": now.isoformat(),
        "age_seconds": 60,
        "active": 25,
        "total": 31,
        "dormant": 0,
        "stale_major": 3,
        "no_tick": 3,
    }
    since = now - dt.timedelta(minutes=wd.PARTIAL_NOTIFY_MIN + 5)
    wd.STATE.write_text(json.dumps(
        {"pricefeed_partial_since_ts": since.isoformat()}))

    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 60), \
         patch.object(wd, "_last_pricefeed_summary", lambda _now: partial), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False: sent.append((title, urgent, body)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    assert len(sent) == 1


def test_pricefeed_partial_since_cleared_on_ok(watchdog):
    """Retour à la normale → la clé de persistance est purgée (pas de
    fausse persistance au prochain épisode)."""
    wd = watchdog
    now = _no_weekend_now()
    wd.STATE.write_text(json.dumps(
        {"pricefeed_partial_since_ts": now.isoformat()}))
    healthy = {
        "ts": now.isoformat(),
        "age_seconds": 60,
        "active": 31,
        "total": 31,
        "dormant": 0,
        "stale_major": 0,
        "no_tick": 0,
    }

    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 60), \
         patch.object(wd, "_last_pricefeed_summary", lambda _now: healthy), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    state = json.loads(wd.STATE.read_text())
    assert state["last_status"].startswith("ok:")
    assert "pricefeed_partial_since_ts" not in state


def test_trading_channel_issue_parser_ignores_errors_before_ready(watchdog):
    wd = watchdog
    now = dt.datetime(
        2026, 6, 3, 3, 40, 0,
        tzinfo=dt.timezone(dt.timedelta(hours=2)),
    )
    log = "\n".join([
        "2026-06-03T03:20:00+02:00 host python[1]: 2026-06-03 03:20:00 [ERROR] "
        "arabesque.live.position_monitor: [Monitor] 🚨 reconcile broker "
        "ftmo_challenge : 18 timeouts consécutifs — canal trading probablement mort",
        "2026-06-03T03:28:38+02:00 host python[2]: 2026-06-03 03:28:38 [INFO] "
        "arabesque.live.engine: [Engine] ✅ Moteur prêt — ticks → barres",
    ])

    class Result:
        returncode = 0
        stdout = log

    with patch.object(wd.subprocess, "run", lambda *a, **kw: Result()):
        assert wd._last_trading_channel_issue(now) is None


def test_trading_channel_issue_parser_detects_errors_after_ready(watchdog):
    wd = watchdog
    now = dt.datetime(
        2026, 6, 3, 3, 40, 0,
        tzinfo=dt.timezone(dt.timedelta(hours=2)),
    )
    log = "\n".join([
        "2026-06-03T03:28:38+02:00 host python[2]: 2026-06-03 03:28:38 [INFO] "
        "arabesque.live.engine: [Engine] ✅ Moteur prêt — ticks → barres",
        "2026-06-03T03:33:16+02:00 host python[2]: 2026-06-03 03:33:16 [ERROR] "
        "arabesque.live.position_monitor: [Monitor] 🚨 reconcile broker "
        "ftmo_challenge : 4 timeouts consécutifs — canal trading probablement mort",
    ])

    class Result:
        returncode = 0
        stdout = log

    with patch.object(wd.subprocess, "run", lambda *a, **kw: Result()):
        issue = wd._last_trading_channel_issue(now)

    assert issue is not None
    assert issue["kind"] == "reconcile_timeouts"
    assert issue["broker_id"] == "ftmo_challenge"
    assert issue["consecutive_timeouts"] == 4


def _risk_invalid_line(ts: str) -> str:
    # Signature exacte de l'incident 2026-06-08 (canal trading zombie après
    # force-reconnect du feed) : feed vivant mais trading bloqué fail-closed.
    return (
        f"2026-06-03T{ts}+02:00 host python[2]: 2026-06-03 {ts} [WARNING] "
        "arabesque.live.engine: [Engine] ftmo_challenge: positions "
        "indisponibles - etat risque invalide (cTrader not connected while "
        "reading pending orders)"
    )


def test_trading_channel_not_connected_detected_after_threshold(watchdog):
    wd = watchdog
    now = dt.datetime(
        2026, 6, 3, 3, 40, 0,
        tzinfo=dt.timezone(dt.timedelta(hours=2)),
    )
    log = "\n".join([
        "2026-06-03T03:28:38+02:00 host python[2]: 2026-06-03 03:28:38 [INFO] "
        "arabesque.live.engine: [Engine] ✅ Moteur prêt — ticks → barres",
        _risk_invalid_line("03:30:00"),
        _risk_invalid_line("03:32:00"),
        _risk_invalid_line("03:34:00"),
    ])

    class Result:
        returncode = 0
        stdout = log

    with patch.object(wd.subprocess, "run", lambda *a, **kw: Result()):
        issue = wd._last_trading_channel_issue(now)

    assert issue is not None
    assert issue["kind"] == "trading_channel_not_connected"
    assert issue["risk_invalid_count"] == 3


def test_trading_channel_not_connected_below_threshold_is_none(watchdog):
    wd = watchdog
    now = dt.datetime(
        2026, 6, 3, 3, 40, 0,
        tzinfo=dt.timezone(dt.timedelta(hours=2)),
    )
    log = "\n".join([
        "2026-06-03T03:28:38+02:00 host python[2]: 2026-06-03 03:28:38 [INFO] "
        "arabesque.live.engine: [Engine] ✅ Moteur prêt — ticks → barres",
        _risk_invalid_line("03:30:00"),
        _risk_invalid_line("03:32:00"),
    ])

    class Result:
        returncode = 0
        stdout = log

    with patch.object(wd.subprocess, "run", lambda *a, **kw: Result()):
        assert wd._last_trading_channel_issue(now) is None


def test_pricefeed_partial_weekend_summary_is_measured_without_alert(watchdog):
    wd = watchdog
    now = _no_weekend_now()
    sent = []
    partial = {
        "ts": now.isoformat(),
        "age_seconds": 60,
        "active": 27,
        "total": 31,
        "dormant": 3,
        "stale_major": 1,
        "no_tick": 0,
        "weekend": True,
    }

    with patch.object(wd, "_engine_active", _engine_active_true(wd)), \
         patch.object(wd, "_last_bar_age_seconds", lambda _now: 60), \
         patch.object(wd, "_last_pricefeed_summary", lambda _now: partial), \
         patch.object(wd, "_send_alert",
                      lambda body, title, urgent=False: sent.append((title, urgent, body)) or True), \
         patch.object(wd.dt, "datetime", _FixedDatetime(now)):
        wd.main()

    assert sent == []
    state = json.loads(wd.STATE.read_text())
    assert state["last_status"].startswith(
        "pricefeed_partial_weekend_suppressed:27/31"
    )
    uptime = json.loads(wd.UPTIME_EVENTS.read_text().splitlines()[0])
    assert uptime["cause"] == "weekend"
    assert uptime["pricefeed"]["weekend"] is True


def test_pricefeed_summary_parser_extracts_latest(watchdog):
    wd = watchdog
    now = dt.datetime(2026, 5, 19, 10, 10, 0, tzinfo=dt.timezone.utc)

    class Result:
        returncode = 0
        stdout = "\n".join([
            "2026-05-19T12:01:00+02:00 host app[1]: [PriceFeed] 📊 31/31 actifs, 0 dormants, 0 stale majeurs, 0 jamais reçus — 10 ticks total",
            "2026-05-19T12:05:00+02:00 host app[1]: [PriceFeed] 📊 30/31 actifs, 0 dormants, 1 stale majeurs, 0 jamais reçus — 20 ticks total",
        ])

    with patch.object(wd.subprocess, "run", lambda *a, **kw: Result()):
        summary = wd._last_pricefeed_summary(now)

    assert summary is not None
    assert summary["active"] == 30
    assert summary["total"] == 31
    assert summary["stale_major"] == 1


def test_pricefeed_summary_parser_marks_weekend(watchdog):
    wd = watchdog
    now = dt.datetime(2026, 5, 31, 16, 10, 0, tzinfo=dt.timezone.utc)

    class Result:
        returncode = 0
        stdout = (
            "2026-05-31T18:05:00+02:00 host app[1]: [PriceFeed] 📊 27/31 actifs, "
            "3 dormants, 1 stale majeurs, 0 jamais reçus — 20 ticks total 🌙 WEEKEND"
        )

    with patch.object(wd.subprocess, "run", lambda *a, **kw: Result()):
        summary = wd._last_pricefeed_summary(now)

    assert summary is not None
    assert summary["weekend"] is True


def test_pricefeed_summary_parser_ignores_stale_summary(watchdog):
    wd = watchdog
    now = dt.datetime(2026, 5, 19, 10, 20, 1, tzinfo=dt.timezone.utc)

    class Result:
        returncode = 0
        stdout = (
            "2026-05-19T12:05:00+02:00 host app[1]: [PriceFeed] 📊 30/31 actifs, "
            "0 dormants, 1 stale majeurs, 0 jamais reçus — 20 ticks total"
        )

    with patch.object(wd.subprocess, "run", lambda *a, **kw: Result()):
        summary = wd._last_pricefeed_summary(now)

    assert summary is None


# ---------------------------------------------------------------------------
# Helper : FixedDatetime monkeypatch pour wd.dt.datetime
# ---------------------------------------------------------------------------

_REAL_DATETIME = dt.datetime  # capture avant tout patch — évite récursion


class _FixedDatetime:
    """Mock minimal de ``dt.datetime`` qui fixe ``now(tz)`` à une valeur donnée
    tout en laissant le reste passer au vrai ``dt.datetime``.

    NB : ``__getattr__`` doit déférer au **vrai** ``datetime.datetime`` (capturé
    dans ``_REAL_DATETIME``). Si on déférait à ``dt.datetime`` après patch, on
    boucle sur soi-même → ``RecursionError`` silencieusement avalée par les
    ``try/except Exception`` dans ``feed_watchdog.main()``.
    """
    def __init__(self, fixed_now: dt.datetime):
        self._fixed = fixed_now

    def now(self, tz=None):
        if tz is None:
            return self._fixed.replace(tzinfo=None)
        return self._fixed.astimezone(tz)

    def __getattr__(self, name):
        return getattr(_REAL_DATETIME, name)
