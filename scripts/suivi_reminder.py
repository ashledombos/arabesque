"""Rappel /suivi — envoie une notif (Telegram + ntfy) si le prochain passage
prévu est dépassé.

Lance toutes les heures via le timer systemd user
`arabesque-suivi-reminder.timer`. Survit au reboot grâce à `Persistent=true`.

Logique :
- lit la dernière ligne de logs/maintenance_state.jsonl
- calcule `due = last_ts + next_expected_in_hours`
- si now > due, envoie un rappel via apprise (channels dans secrets.yaml)
- garde une trace `reminder_sent` pour éviter le spam (1 rappel toutes les 3h max)
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

import apprise
import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "logs" / "maintenance_state.jsonl"
SECRETS = ROOT / "config" / "secrets.yaml"

REMIND_COOLDOWN_H = 3.0


def _last_state_line() -> dict | None:
    if not STATE.exists():
        return None
    lines = [l for l in STATE.read_text().splitlines() if l.strip()]
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "reminder_sent":
            continue
        return obj
    return None


def _last_reminder_ts() -> dt.datetime | None:
    if not STATE.exists():
        return None
    for line in reversed(STATE.read_text().splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "reminder_sent":
            return dt.datetime.fromisoformat(obj["ts"])
    return None


def main() -> int:
    state = _last_state_line()
    if not state:
        return 0
    last_ts = dt.datetime.fromisoformat(state["ts"])
    next_h = state.get("next_expected_in_hours") or 24
    due = last_ts + dt.timedelta(hours=next_h)
    now = dt.datetime.now(dt.timezone.utc)
    overdue_h = (now - due).total_seconds() / 3600.0
    if overdue_h < 0:
        return 0

    last_remind = _last_reminder_ts()
    if last_remind and (now - last_remind).total_seconds() < REMIND_COOLDOWN_H * 3600:
        return 0

    secrets = yaml.safe_load(SECRETS.read_text())
    channels = (secrets or {}).get("notifications", {}).get("channels", []) or []
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
