"""Watchdog externe du PriceFeed Arabesque.

Détecte un engine `active` mais figé (aucune barre fermée depuis > N minutes)
et envoie une alerte Telegram+ntfy avec cooldown anti-spam.

Pas d'auto-restart (v1 = détection + alerte seulement). L'opérateur décide.

Le watchdog est volontairement EXTERNE au process Python de l'engine pour
détecter les cas où le bug est dans le PriceFeed lui-même (cf. price_feed.py
bug 2026-05-14 : `_alert_sent` figé à True quand `_connect_and_subscribe`
raise systématiquement → 1 seule alerte sur 5h30 de panne silencieuse).

Invoqué par le timer systemd user `arabesque-feed-watchdog.timer` toutes les
5 minutes. Persistent=true → rattrape les passages manqués au reboot.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

import apprise
import yaml

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "config" / "secrets.yaml"
STATE = ROOT / "logs" / "feed_watchdog_state.json"

STALE_THRESHOLD_MIN = 15
COOLDOWN_MIN = 30
WEEKEND_GUARD_START_HOUR = 22  # vendredi 22:00 UTC
WEEKEND_GUARD_END_HOUR = 22    # dimanche 22:00 UTC

BAR_PATTERN = re.compile(
    r"^(\w{3})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2}).*BarAggregator.*Résumé"
)
MONTH_MAP = {
    "jan": 1, "feb": 2, "fév": 2, "mar": 3, "apr": 4, "avr": 4,
    "may": 5, "mai": 5, "jun": 6, "jui": 6, "jul": 7, "aug": 8,
    "aoû": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12, "déc": 12,
}


def _is_weekend_utc(now: dt.datetime) -> bool:
    """Crypto CFD cTrader fermé vendredi 22h UTC → dimanche 22h UTC."""
    wd = now.weekday()  # lundi=0, dimanche=6
    h = now.hour
    if wd == 4 and h >= WEEKEND_GUARD_START_HOUR:
        return True
    if wd == 5:
        return True
    if wd == 6 and h < WEEKEND_GUARD_END_HOUR:
        return True
    return False


def _engine_active() -> bool:
    r = subprocess.run(
        ["systemctl", "--user", "is-active", "arabesque-live.service"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() == "active"


def _last_bar_age_seconds(now: dt.datetime) -> int | None:
    """Retourne l'âge en secondes de la dernière `BarAggregator Résumé`, ou None."""
    r = subprocess.run(
        ["journalctl", "--user", "-u", "arabesque-live.service",
         "--since", "30 minutes ago", "--no-pager"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    last_ts: dt.datetime | None = None
    for line in r.stdout.splitlines():
        m = BAR_PATTERN.match(line)
        if not m:
            continue
        month_str, day_str, hh, mm, ss = m.groups()
        month = MONTH_MAP.get(month_str.lower()[:3])
        if not month:
            continue
        try:
            ts_local = dt.datetime(
                now.year, month, int(day_str),
                int(hh), int(mm), int(ss),
                tzinfo=dt.timezone(dt.timedelta(hours=now.astimezone().utcoffset().total_seconds() / 3600))
            )
        except Exception:
            continue
        ts_utc = ts_local.astimezone(dt.timezone.utc)
        if last_ts is None or ts_utc > last_ts:
            last_ts = ts_utc
    if last_ts is None:
        return None
    return int((now - last_ts).total_seconds())


def _read_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def _can_alert(state: dict, now: dt.datetime) -> bool:
    last_alert = state.get("last_alert_ts")
    if not last_alert:
        return True
    try:
        last_dt = dt.datetime.fromisoformat(last_alert)
    except Exception:
        return True
    return (now - last_dt).total_seconds() >= COOLDOWN_MIN * 60


def _send_alert(body: str, title: str) -> bool:
    if not SECRETS.exists():
        return False
    secrets = yaml.safe_load(SECRETS.read_text()) or {}
    channels = (secrets.get("notifications") or {}).get("channels") or []
    if not channels:
        return False
    ap = apprise.Apprise()
    for ch in channels:
        if isinstance(ch, str):
            ap.add(ch)
    return asyncio.run(ap.async_notify(
        body=body, title=title, body_format=apprise.NotifyFormat.TEXT
    ))


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    state = _read_state()
    state["last_check_ts"] = now.isoformat()

    if not _engine_active():
        state["last_status"] = "engine_inactive"
        _write_state(state)
        return 0

    if _is_weekend_utc(now):
        state["last_status"] = "weekend_guard"
        _write_state(state)
        return 0

    age_s = _last_bar_age_seconds(now)
    state["last_bar_age_seconds"] = age_s

    if age_s is None:
        state["last_status"] = "no_bar_data_in_window"
        body = (
            f"Aucune barre fermée trouvée dans les 30 dernières minutes de journalctl.\n"
            f"Engine systemctl=active mais BarAggregator inactif.\n"
            f"Vérifier: journalctl --user -u arabesque-live.service | tail -30\n"
            f"Reco: systemctl --user restart arabesque-live.service (après stop+sleep 60s)"
        )
    elif age_s > STALE_THRESHOLD_MIN * 60:
        state["last_status"] = f"feed_stale:{age_s}s"
        body = (
            f"Dernière barre fermée il y a {age_s // 60}min{age_s % 60:02d}s.\n"
            f"Seuil watchdog: {STALE_THRESHOLD_MIN}min.\n"
            f"Engine systemctl=active mais figé. Pas en weekend guard.\n"
            f"Reco: systemctl --user restart arabesque-live.service (après stop+sleep 60s pour libérer la session cTrader)"
        )
    else:
        state["last_status"] = f"ok:age={age_s}s"
        _write_state(state)
        return 0

    if not _can_alert(state, now):
        state["last_status"] += "+cooldown"
        _write_state(state)
        return 0

    title = "🚨 Feed Arabesque mort"
    ok = _send_alert(body, title)
    state["last_alert_ts"] = now.isoformat()
    state["last_alert_ok"] = bool(ok)
    _write_state(state)
    print(f"watchdog: {state['last_status']} → notif ok={ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
