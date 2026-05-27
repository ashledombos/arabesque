"""Rappel /suivi — Telegram si le prochain passage est dépassé, et
Telegram+ntfy uniquement si le watchdog feed détecte une panne persistante.

Lance toutes les heures via le timer systemd user
`arabesque-suivi-reminder.timer`. Survit au reboot grâce à `Persistent=true`.

Logique :
- **Escalade feed (priorité)** — lit `logs/feed_watchdog_state.json` ; si
  `loop_guard` / `autorestart_failed` / `feed_stale_since_ts ≥ 30 min` →
  notif URGENT distincte avec cooldown 1h (vs 3h reminder normal). Skip si
  `weekend_guard` (marchés fermés = stale attendu). Incident fondateur :
  2026-05-21T22:59 → 2026-05-22T18:55 UTC, 19h54 de feed mort où l'utilisateur
  a porté seul la surveillance car aucun /suivi assistant n'a été déclenché.
- **Reminder normal** — lit la dernière ligne de `logs/maintenance_state.jsonl`,
  calcule `due = last_ts + next_expected_in_hours`, ping si dépassé.
  Cooldown 3h anti-spam.

Si escalade envoyée, le reminder normal est skippé (l'urgence prime).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arabesque.notifications import select_notification_channels

STATE = ROOT / "logs" / "maintenance_state.jsonl"
WATCHDOG_STATE = ROOT / "logs" / "feed_watchdog_state.json"
SECRETS = ROOT / "config" / "secrets.yaml"

REMIND_COOLDOWN_H = 3.0
ESCALATE_COOLDOWN_H = 1.0
ESCALATE_PERSIST_MIN = 30


def _load_apprise():
    try:
        import apprise

        return apprise
    except ImportError:
        pass

    # Le unit déployé doit utiliser .venv/bin/python, mais certains services
    # existants peuvent encore appeler /usr/bin/env python3. Dans ce cas, on
    # charge explicitement la dépendance depuis le venv du dépôt.
    for site_packages in sorted((ROOT / ".venv").glob("lib*/python*/site-packages")):
        sys.path.insert(0, str(site_packages))

    import apprise

    return apprise


def _last_state_line() -> dict | None:
    """Retourne la dernière ligne hors events ``reminder_sent`` / ``escalation_sent``."""
    if not STATE.exists():
        return None
    lines = [l for l in STATE.read_text().splitlines() if l.strip()]
    skip_events = {"reminder_sent", "escalation_sent"}
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") in skip_events:
            continue
        return obj
    return None


def _last_event_ts(event_name: str) -> dt.datetime | None:
    if not STATE.exists():
        return None
    for line in reversed(STATE.read_text().splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == event_name:
            try:
                return dt.datetime.fromisoformat(obj["ts"])
            except Exception:
                return None
    return None


def _watchdog_escalation_reason(now: dt.datetime) -> str | None:
    """Lit l'état watchdog feed et détermine s'il faut escalader.

    Retourne la raison textuelle si escalade nécessaire, sinon ``None``.
    Skipper en weekend_guard (marchés fermés, stale attendu).
    """
    if not WATCHDOG_STATE.exists():
        return None
    try:
        ws = json.loads(WATCHDOG_STATE.read_text())
    except Exception:
        return None

    status = ws.get("last_status") or ""
    if "weekend_guard" in status:
        return None
    if "loop_guard" in status:
        return f"anti-boucle watchdog déclenchée — {status}"
    if "autorestart_failed" in status:
        return f"auto-restart échoué — {status}"

    stale_since_ts = ws.get("feed_stale_since_ts")
    if stale_since_ts:
        try:
            stale_since = dt.datetime.fromisoformat(stale_since_ts)
        except Exception:
            return None
        persist_min = (now - stale_since).total_seconds() / 60
        if persist_min >= ESCALATE_PERSIST_MIN:
            return f"feed_stale persistant {int(persist_min)}min sans restart réussi"

    return None


def _send_escalation(apprise, now: dt.datetime, reason: str) -> bool:
    secrets = yaml.safe_load(SECRETS.read_text()) if SECRETS.exists() else {}
    channels = select_notification_channels(
        (secrets or {}).get("notifications", {}).get("channels", []) or [],
        urgent=True,
    )
    if not channels:
        return False
    ap = apprise.Apprise()
    for ch in channels:
        if isinstance(ch, str):
            ap.add(ch)
    body = (
        f"🚨 URGENT — feed Arabesque\n"
        f"{reason}\n"
        f"Lance /suivi pour diagnostiquer.\n"
        f"État watchdog : logs/feed_watchdog_state.json"
    )
    sent = asyncio.run(ap.async_notify(
        body=body, title="[URGENT] Arabesque feed escalade"
    ))
    with STATE.open("a") as f:
        f.write(json.dumps({
            "ts": now.isoformat(),
            "event": "escalation_sent",
            "reason": reason,
            "channels": len(channels),
            "ok": bool(sent),
        }) + "\n")
    return bool(sent)


def main() -> int:
    apprise = _load_apprise()
    now = dt.datetime.now(dt.timezone.utc)

    # 1) Escalade prioritaire sur feed_stale persistant
    reason = _watchdog_escalation_reason(now)
    if reason:
        last_esc = _last_event_ts("escalation_sent")
        if last_esc is None or (now - last_esc).total_seconds() >= ESCALATE_COOLDOWN_H * 3600:
            _send_escalation(apprise, now, reason)
        return 0  # l'urgence prime, pas de double notif reminder

    # 2) Reminder normal sur retard /suivi
    state = _last_state_line()
    if not state:
        return 0
    last_ts = dt.datetime.fromisoformat(state["ts"])
    next_h = state.get("next_expected_in_hours") or 24
    due = last_ts + dt.timedelta(hours=next_h)
    overdue_h = (now - due).total_seconds() / 3600.0
    if overdue_h < 0:
        return 0

    last_remind = _last_event_ts("reminder_sent")
    if last_remind and (now - last_remind).total_seconds() < REMIND_COOLDOWN_H * 3600:
        return 0

    secrets = yaml.safe_load(SECRETS.read_text())
    channels = select_notification_channels(
        (secrets or {}).get("notifications", {}).get("channels", []) or [],
        urgent=False,
    )
    if not channels:
        return 0
    ap = apprise.Apprise()
    for ch in channels:
        if isinstance(ch, str):
            ap.add(ch)

    elapsed_h = (now - last_ts).total_seconds() / 3600.0
    body = (
        f"⏰ Rappel /suivi\n"
        f"Dernier passage : {last_ts:%Y-%m-%d %H:%M} UTC (il y a {elapsed_h:.1f}h)\n"
        f"Échéance : {due:%Y-%m-%d %H:%M} UTC\n"
        f"En retard de {overdue_h:.1f}h.\n"
        f"Lance /suivi quand tu peux."
    )
    sent = asyncio.run(ap.async_notify(body=body, title="Arabesque /suivi"))

    with STATE.open("a") as f:
        f.write(
            json.dumps(
                {
                    "ts": now.isoformat(),
                    "event": "reminder_sent",
                    "overdue_h": round(overdue_h, 2),
                    "channels": len(channels),
                    "ok": bool(sent),
                }
            )
            + "\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
