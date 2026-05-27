"""Escalade feed_stale dans suivi_reminder (task #33).

Incident fondateur : 2026-05-21T22:59 → 2026-05-22T18:55 UTC. Le watchdog
systemd `arabesque-feed-watchdog.service` a alerté ntfy+Telegram dès 07:22
UTC le 22/05, mais aucun /suivi assistant n'a été déclenché entre le crash
22:59 UTC le 21/05 et le /suivi user-initié à 18:51 UTC le 22/05 = 19h54
de fenêtre où l'utilisateur a porté seul la charge de surveillance.

Le `suivi_reminder` (timer hourly Persistent=true) lit désormais aussi
`logs/feed_watchdog_state.json` et escalade en **notif URGENT distincte**
(cooldown 1h vs 3h reminder normal) sur trois critères :
  1. `last_status` contient `loop_guard` (anti-boucle watchdog déclenchée)
  2. `last_status` contient `autorestart_failed` (auto-restart échoué)
  3. `feed_stale_since_ts` persistant ≥ 30 min sans restart réussi

Skip si `weekend_guard` actif (marchés fermés → stale attendu, pas un bug).

Invariants verrouillés :
  - Escalade prime sur le reminder normal (return early, pas de double notif).
  - Cooldown d'escalade indépendant du cooldown reminder (event séparé
    `escalation_sent` dans `logs/maintenance_state.jsonl`).
  - Pas de state watchdog → pas d'escalade (no-op, mais reminder normal
    peut tourner selon overdue).
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def reminder(tmp_path, monkeypatch):
    """Charge suivi_reminder avec STATE/WATCHDOG_STATE/SECRETS redirigés."""
    import scripts.suivi_reminder as sr
    importlib.reload(sr)
    monkeypatch.setattr(sr, "STATE", tmp_path / "maintenance_state.jsonl")
    monkeypatch.setattr(sr, "WATCHDOG_STATE", tmp_path / "feed_watchdog_state.json")
    monkeypatch.setattr(sr, "SECRETS", tmp_path / "secrets.yaml")
    (tmp_path / "secrets.yaml").write_text(
        "notifications:\n  channels:\n    - tgram://bot/chat\n    - ntfy://test\n"
    )
    return sr


def _write_watchdog(sr, state: dict) -> None:
    sr.WATCHDOG_STATE.parent.mkdir(parents=True, exist_ok=True)
    sr.WATCHDOG_STATE.write_text(json.dumps(state))


def _write_main_state(sr, *lines: dict) -> None:
    sr.STATE.parent.mkdir(parents=True, exist_ok=True)
    sr.STATE.write_text("\n".join(json.dumps(l) for l in lines) + "\n")


def _mock_apprise():
    """Retourne (module_factory, captured_sent_list)."""
    sent = []

    class FakeApprise:
        def __init__(self):
            self._urls = []
        def add(self, url):
            self._urls.append(url)
        async def async_notify(self, body, title, **kwargs):
            sent.append({"body": body, "title": title, "urls": list(self._urls)})
            return True

    mod = MagicMock()
    mod.Apprise = FakeApprise
    return mod, sent


# ---------------------------------------------------------------------------
# 1. Pas de state watchdog → pas d'escalade
# ---------------------------------------------------------------------------

def test_no_escalate_when_no_watchdog_state(reminder):
    sr = reminder
    mod, sent = _mock_apprise()
    with patch.object(sr, "_load_apprise", lambda: mod):
        sr.main()
    assert sent == []


# ---------------------------------------------------------------------------
# 2. last_status contient loop_guard → escalade urgent
# ---------------------------------------------------------------------------

def test_escalate_on_loop_guard(reminder):
    sr = reminder
    _write_watchdog(sr, {
        "last_status": "feed_stale:1800s persist=2400s+loop_guard(recent=2)"
    })
    mod, sent = _mock_apprise()
    with patch.object(sr, "_load_apprise", lambda: mod):
        sr.main()
    assert len(sent) == 1
    assert "URGENT" in sent[0]["title"]
    assert "anti-boucle" in sent[0]["body"].lower()
    assert sent[0]["urls"] == ["tgram://bot/chat", "ntfy://test"]


# ---------------------------------------------------------------------------
# 3. last_status contient autorestart_failed → escalade urgent
# ---------------------------------------------------------------------------

def test_escalate_on_autorestart_failed(reminder):
    sr = reminder
    _write_watchdog(sr, {
        "last_status": "feed_stale:1800s+autorestart_failed:start failed"
    })
    mod, sent = _mock_apprise()
    with patch.object(sr, "_load_apprise", lambda: mod):
        sr.main()
    assert len(sent) == 1
    assert "URGENT" in sent[0]["title"]
    assert "auto-restart" in sent[0]["body"].lower()


# ---------------------------------------------------------------------------
# 4. feed_stale_since_ts persistant ≥ 30 min → escalade
# ---------------------------------------------------------------------------

def test_escalate_on_feed_stale_over_30min(reminder):
    sr = reminder
    now = dt.datetime.now(dt.timezone.utc)
    stale_since = (now - dt.timedelta(minutes=35)).isoformat()
    _write_watchdog(sr, {
        "last_status": "feed_stale:2100s persist=2100s",
        "feed_stale_since_ts": stale_since,
    })
    mod, sent = _mock_apprise()
    with patch.object(sr, "_load_apprise", lambda: mod):
        sr.main()
    assert len(sent) == 1
    assert "URGENT" in sent[0]["title"]
    assert "persistant" in sent[0]["body"].lower()


# ---------------------------------------------------------------------------
# 5. feed_stale < 30 min → pas encore escalade
# ---------------------------------------------------------------------------

def test_no_escalate_under_30min_threshold(reminder):
    sr = reminder
    now = dt.datetime.now(dt.timezone.utc)
    stale_since = (now - dt.timedelta(minutes=20)).isoformat()
    _write_watchdog(sr, {
        "last_status": "feed_stale:1200s persist=1200s",
        "feed_stale_since_ts": stale_since,
    })
    mod, sent = _mock_apprise()
    with patch.object(sr, "_load_apprise", lambda: mod):
        sr.main()
    assert sent == []


# ---------------------------------------------------------------------------
# 6. weekend_guard → pas d'escalade (marchés fermés = stale attendu)
# ---------------------------------------------------------------------------

def test_no_escalate_in_weekend_guard(reminder):
    sr = reminder
    _write_watchdog(sr, {"last_status": "weekend_guard"})
    mod, sent = _mock_apprise()
    with patch.object(sr, "_load_apprise", lambda: mod):
        sr.main()
    assert sent == []


# ---------------------------------------------------------------------------
# 7. Cooldown 1h : 2e escalade dans l'heure bloquée
# ---------------------------------------------------------------------------

def test_escalation_cooldown_blocks_second_within_hour(reminder):
    sr = reminder
    _write_watchdog(sr, {
        "last_status": "feed_stale:1800s+loop_guard(recent=2)"
    })
    now = dt.datetime.now(dt.timezone.utc)
    last_esc = (now - dt.timedelta(minutes=30)).isoformat()
    _write_main_state(sr, {
        "ts": last_esc, "event": "escalation_sent",
        "reason": "test", "ok": True,
    })
    mod, sent = _mock_apprise()
    with patch.object(sr, "_load_apprise", lambda: mod):
        sr.main()
    assert sent == []


# ---------------------------------------------------------------------------
# 8. Cooldown 1h écoulé → nouvelle escalade autorisée
# ---------------------------------------------------------------------------

def test_escalation_after_cooldown_expired(reminder):
    sr = reminder
    _write_watchdog(sr, {
        "last_status": "feed_stale:1800s+loop_guard(recent=2)"
    })
    now = dt.datetime.now(dt.timezone.utc)
    last_esc = (now - dt.timedelta(hours=2)).isoformat()
    _write_main_state(sr, {
        "ts": last_esc, "event": "escalation_sent",
        "reason": "test", "ok": True,
    })
    mod, sent = _mock_apprise()
    with patch.object(sr, "_load_apprise", lambda: mod):
        sr.main()
    assert len(sent) == 1
    assert "URGENT" in sent[0]["title"]


# ---------------------------------------------------------------------------
# 9. Reminder normal continue à fonctionner sans escalade
# ---------------------------------------------------------------------------

def test_normal_reminder_unaffected_when_no_escalation(reminder):
    sr = reminder
    now = dt.datetime.now(dt.timezone.utc)
    last_suivi = (now - dt.timedelta(hours=29)).isoformat()
    _write_main_state(sr, {
        "ts": last_suivi, "event": "suivi_complete",
        "next_expected_in_hours": 24,
    })
    mod, sent = _mock_apprise()
    with patch.object(sr, "_load_apprise", lambda: mod):
        sr.main()
    assert len(sent) == 1
    assert "Rappel" in sent[0]["body"]
    assert "URGENT" not in sent[0]["title"]
    assert sent[0]["urls"] == ["tgram://bot/chat"]


# ---------------------------------------------------------------------------
# 10. Escalade prime sur reminder normal (pas de double notif)
# ---------------------------------------------------------------------------

def test_escalation_skips_normal_reminder(reminder):
    sr = reminder
    _write_watchdog(sr, {
        "last_status": "feed_stale:1800s+loop_guard(recent=2)"
    })
    now = dt.datetime.now(dt.timezone.utc)
    last_suivi = (now - dt.timedelta(hours=29)).isoformat()
    _write_main_state(sr, {
        "ts": last_suivi, "event": "suivi_complete",
        "next_expected_in_hours": 24,
    })
    mod, sent = _mock_apprise()
    with patch.object(sr, "_load_apprise", lambda: mod):
        sr.main()
    # Une seule notif : l'escalade (l'urgence prime).
    assert len(sent) == 1
    assert "URGENT" in sent[0]["title"]


# ---------------------------------------------------------------------------
# 11. Event escalation_sent persisté dans maintenance_state.jsonl
# ---------------------------------------------------------------------------

def test_escalation_logged_to_state(reminder):
    sr = reminder
    _write_watchdog(sr, {
        "last_status": "feed_stale:1800s+loop_guard(recent=2)"
    })
    mod, sent = _mock_apprise()
    with patch.object(sr, "_load_apprise", lambda: mod):
        sr.main()
    lines = [
        json.loads(l) for l in sr.STATE.read_text().splitlines() if l.strip()
    ]
    events = [l for l in lines if l.get("event") == "escalation_sent"]
    assert len(events) == 1
    assert events[0]["reason"]
    assert events[0]["ok"] is True
