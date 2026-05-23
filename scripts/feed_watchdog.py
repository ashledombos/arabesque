"""Watchdog externe du PriceFeed Arabesque.

Détecte un engine `active` mais figé (aucune barre fermée depuis > N minutes)
et envoie une alerte Telegram+ntfy avec cooldown anti-spam.

Étages résilience (cf docs/INCIDENT_DASHUSD_RESILIENCE_BROKER_2026-05-21.md
sections 3+7) :
  - Étage 1 (1ère alerte) : notif normale, recommandation `systemctl restart`
    manuel. Cooldown 30 min entre 2 notifs.
  - Étage 3 (auto-restart) : si `feed_stale` persiste depuis > 30 min,
    `systemctl --user restart arabesque-live.service` automatiquement.
    Évite les 19h54 de feed mort où l'utilisateur porte seul la surveillance
    (incident 2026-05-22).
  - Étage 4 (anti-boucle) : max 2 restarts dans la dernière heure. Au 3e,
    on stoppe l'auto-restart et on envoie une **alerte critique distincte**
    (priority=urgent côté ntfy). Empêche les boucles de redémarrage masquant
    un bug structurel.

Étage 2 (heartbeat broker dédié) : non implémenté — le watchdog externe
couvre fonctionnellement le besoin (observation indépendante du canal trading
toutes les 5 min via journalctl). Cf section 3 du dossier d'incident.

Pas d'auto-restart sur `no_bar_data_in_window` (fenêtre vide ≠ feed mort) ni
en weekend guard (vendredi 21:00 UTC → dimanche 22:00 UTC, forex+crypto close).
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
RESTART_HISTORY = ROOT / "logs" / "watchdog_restart_history.jsonl"

STALE_THRESHOLD_MIN = 15
COOLDOWN_MIN = 30

# Étages 3+4 — auto-restart + anti-boucle
RESTART_PERSISTENCE_MIN = 30          # feed_stale doit persister > 30 min
RESTART_MAX_PER_HOUR = 2              # 3e tentative dans l'heure → escalade
RESTART_STOP_SLEEP_S = 60             # stop+sleep pour libérer session cTrader

# Weekend guard étendu : forex close 21:00 UTC vendredi (1h avant crypto)
# pour éviter auto-restart à vide pendant le bord de fenêtre.
WEEKEND_GUARD_FRI_HOUR = 21           # vendredi 21:00 UTC → debut weekend
WEEKEND_GUARD_SUN_HOUR = 22           # dimanche 22:00 UTC → fin weekend

BAR_PATTERN = re.compile(
    r"^(\w{3})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2}).*BarAggregator.*Résumé"
)
MONTH_MAP = {
    "jan": 1, "feb": 2, "fév": 2, "mar": 3, "apr": 4, "avr": 4,
    "may": 5, "mai": 5, "jun": 6, "jui": 6, "jul": 7, "aug": 8,
    "aoû": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12, "déc": 12,
}


def _is_weekend_utc(now: dt.datetime) -> bool:
    """Fenêtre vendredi 21:00 UTC → dimanche 22:00 UTC.

    Couvre la fenêtre où forex + métaux + crypto cTrader sont tous fermés.
    Étendue d'1h côté début (vs 22h initial) pour éviter les faux positifs
    pendant la transition forex close → crypto close vendredi soir.
    """
    wd = now.weekday()  # lundi=0, dimanche=6
    h = now.hour
    if wd == 4 and h >= WEEKEND_GUARD_FRI_HOUR:
        return True
    if wd == 5:
        return True
    if wd == 6 and h < WEEKEND_GUARD_SUN_HOUR:
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


def _send_alert(body: str, title: str, urgent: bool = False) -> bool:
    """Envoie une notif Telegram+ntfy.

    Si ``urgent=True``, marque le titre ``[URGENT]`` (Telegram + ntfy support
    via tag/priorité côté apprise — la mise en évidence visuelle suffit pour
    distinguer les escalades du flux normal).
    """
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
    if urgent:
        title = f"[URGENT] {title}"
    return asyncio.run(ap.async_notify(
        body=body, title=title, body_format=apprise.NotifyFormat.TEXT
    ))


def _append_restart_history(now: dt.datetime, outcome: str, reason: str) -> None:
    """Append-only log des restarts auto. Lu par ``_recent_restart_count``."""
    RESTART_HISTORY.parent.mkdir(exist_ok=True)
    entry = {"ts": now.isoformat(), "outcome": outcome, "reason": reason}
    with open(RESTART_HISTORY, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _recent_restart_count(now: dt.datetime, window_s: int = 3600) -> int:
    """Compte les restarts réussis (``outcome=ok``) dans les ``window_s`` dernières secondes."""
    if not RESTART_HISTORY.exists():
        return 0
    cutoff = now - dt.timedelta(seconds=window_s)
    count = 0
    try:
        for line in RESTART_HISTORY.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("outcome") != "ok":
                continue
            try:
                ts = dt.datetime.fromisoformat(entry["ts"])
            except Exception:
                continue
            if ts >= cutoff:
                count += 1
    except Exception:
        return 0
    return count


def _attempt_auto_restart(now: dt.datetime, reason: str) -> tuple[bool, str]:
    """Tente ``systemctl --user stop`` + ``sleep`` + ``start``.

    Retourne ``(success, message)``. ``stop+sleep+start`` plutôt que ``restart``
    pour laisser la session cTrader se fermer côté serveur (sinon
    ALREADY_LOGGED_IN au redémarrage).
    """
    try:
        stop = subprocess.run(
            ["systemctl", "--user", "stop", "arabesque-live.service"],
            capture_output=True, text=True, timeout=30,
        )
        if stop.returncode != 0:
            return False, f"stop failed: {stop.stderr.strip() or stop.stdout.strip()}"
    except subprocess.TimeoutExpired:
        return False, "stop timeout (30s)"

    # Laisser la session Protobuf cTrader se libérer côté serveur
    import time as _time
    _time.sleep(RESTART_STOP_SLEEP_S)

    try:
        start = subprocess.run(
            ["systemctl", "--user", "start", "arabesque-live.service"],
            capture_output=True, text=True, timeout=60,
        )
        if start.returncode != 0:
            return False, f"start failed: {start.stderr.strip() or start.stdout.strip()}"
    except subprocess.TimeoutExpired:
        return False, "start timeout (60s)"

    _append_restart_history(now, "ok", reason)
    return True, "stop+sleep60s+start ok"


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    state = _read_state()
    state["last_check_ts"] = now.isoformat()

    if not _engine_active():
        state["last_status"] = "engine_inactive"
        # Reset feed_stale tracker — on est pas dans le cas d'un feed mort
        state.pop("feed_stale_since_ts", None)
        _write_state(state)
        return 0

    if _is_weekend_utc(now):
        state["last_status"] = "weekend_guard"
        state.pop("feed_stale_since_ts", None)
        _write_state(state)
        return 0

    age_s = _last_bar_age_seconds(now)
    state["last_bar_age_seconds"] = age_s

    is_feed_stale = age_s is not None and age_s > STALE_THRESHOLD_MIN * 60
    no_bar_data = age_s is None

    if is_feed_stale:
        # Premier passage stale → on track l'instant de détection
        if "feed_stale_since_ts" not in state:
            state["feed_stale_since_ts"] = now.isoformat()
        try:
            stale_since = dt.datetime.fromisoformat(state["feed_stale_since_ts"])
        except Exception:
            stale_since = now
        persistence_s = (now - stale_since).total_seconds()
        state["last_status"] = f"feed_stale:{age_s}s persist={int(persistence_s)}s"

        # Étage 3+4 — auto-restart si persistance > 30 min, anti-boucle
        if persistence_s >= RESTART_PERSISTENCE_MIN * 60:
            recent = _recent_restart_count(now, window_s=3600)
            if recent >= RESTART_MAX_PER_HOUR:
                # Étage 4 — anti-boucle déclenché
                body = (
                    f"FEED STALE PERSISTANT depuis {int(persistence_s // 60)}min, "
                    f"dernière barre il y a {age_s // 60}min.\n"
                    f"Anti-boucle watchdog DECLENCHEE : "
                    f"{recent} restart auto déjà tentés dans la dernière heure.\n"
                    f"Auto-restart STOPPE — intervention humaine requise.\n"
                    f"Reco: investiguer journalctl --user -u arabesque-live "
                    f"(token / oauth / network) puis restart manuel."
                )
                _append_restart_history(now, "skipped_loop_guard", state["last_status"])
                if _can_alert(state, now):
                    _send_alert(body, "Feed Arabesque — anti-boucle restart", urgent=True)
                    state["last_alert_ts"] = now.isoformat()
                state["last_status"] += f"+loop_guard(recent={recent})"
                _write_state(state)
                return 0

            # Étage 3 — auto-restart
            ok, msg = _attempt_auto_restart(now, reason=state["last_status"])
            if ok:
                body = (
                    f"FEED STALE > {RESTART_PERSISTENCE_MIN}min "
                    f"(persistance {int(persistence_s // 60)}min, "
                    f"dernière barre il y a {age_s // 60}min).\n"
                    f"Auto-restart engine effectue (stop + sleep "
                    f"{RESTART_STOP_SLEEP_S}s + start).\n"
                    f"{recent+1}e restart dans l'heure courante (max {RESTART_MAX_PER_HOUR})."
                )
                _send_alert(body, "Feed Arabesque — auto-restart", urgent=True)
                state["last_alert_ts"] = now.isoformat()
                state.pop("feed_stale_since_ts", None)  # reset tracker post-restart
                state["last_status"] += "+autorestart_ok"
            else:
                body = (
                    f"FEED STALE > {RESTART_PERSISTENCE_MIN}min mais "
                    f"auto-restart ECHOUE : {msg}.\n"
                    f"Intervention humaine requise (systemctl --user status)."
                )
                _append_restart_history(now, "failed", msg)
                _send_alert(body, "Feed Arabesque — auto-restart ECHEC", urgent=True)
                state["last_alert_ts"] = now.isoformat()
                state["last_status"] += f"+autorestart_failed:{msg}"
            _write_state(state)
            return 0

        # Persistance < 30 min → notif Étage 1 normale (sans escalade)
        body = (
            f"Derniere barre fermee il y a {age_s // 60}min{age_s % 60:02d}s.\n"
            f"Persistance feed_stale: {int(persistence_s // 60)}min "
            f"(seuil auto-restart {RESTART_PERSISTENCE_MIN}min).\n"
            f"Engine systemctl=active mais fige. Pas en weekend guard.\n"
            f"Reco: surveille — auto-restart watchdog se declenchera si "
            f"persistance > {RESTART_PERSISTENCE_MIN}min "
            f"(max {RESTART_MAX_PER_HOUR} restarts/heure)."
        )
        title = "Feed Arabesque mort"

    elif no_bar_data:
        # Pas d'auto-restart sur no_bar_data (fenetre vide != feed stale)
        state["last_status"] = "no_bar_data_in_window"
        state.pop("feed_stale_since_ts", None)
        body = (
            f"Aucune barre fermee trouvee dans les 30 dernieres minutes.\n"
            f"Engine systemctl=active mais BarAggregator inactif.\n"
            f"Verifier: journalctl --user -u arabesque-live | tail -30\n"
            f"Reco: investigation manuelle (pas d'auto-restart sur ce cas)."
        )
        title = "Feed Arabesque — pas de barres"

    else:
        # OK
        state["last_status"] = f"ok:age={age_s}s"
        state.pop("feed_stale_since_ts", None)
        _write_state(state)
        return 0

    if not _can_alert(state, now):
        state["last_status"] += "+cooldown"
        _write_state(state)
        return 0

    ok = _send_alert(body, title, urgent=False)
    state["last_alert_ts"] = now.isoformat()
    state["last_alert_ok"] = bool(ok)
    _write_state(state)
    print(f"watchdog: {state['last_status']} → notif ok={ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
