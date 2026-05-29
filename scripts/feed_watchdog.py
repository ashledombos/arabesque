"""Watchdog externe du PriceFeed Arabesque.

Détecte un engine `active` mais figé (aucune barre fermée depuis > N minutes).
Les observations ordinaires partent sur Telegram ; ntfy est reserve aux
escalades qui exigent une intervention humaine rapide.

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
import os
import re
import subprocess
import sys
from pathlib import Path

import apprise
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arabesque.notifications import select_notification_channels

SECRETS = ROOT / "config" / "secrets.yaml"
STATE = ROOT / "logs" / "feed_watchdog_state.json"
RESTART_HISTORY = ROOT / "logs" / "watchdog_restart_history.jsonl"
POSITIONS_STATE = ROOT / "logs" / "position_monitor_state.json"
UPTIME_EVENTS = ROOT / "logs" / "uptime_events.jsonl"

STALE_THRESHOLD_MIN = 15
COOLDOWN_MIN = 30
PRICEFEED_SUMMARY_MAX_AGE_S = 600

# Étages 3+4 — auto-restart + anti-boucle
RESTART_PERSISTENCE_MIN = 30          # feed_stale doit persister > 30 min
RESTART_MAX_PER_HOUR = 2              # 3e tentative dans l'heure → escalade
RESTART_STOP_SLEEP_S = 60             # stop+sleep pour libérer session cTrader
AUTO_RESTART_REQUIRES_FLAT = True     # jamais de restart auto avec position ouverte

# Hot Path #2 bis — backoff progressif des restart auto en weekend avec
# position ouverte. cTrader accepte les sessions weekend mais leur comportement
# est erratique (feed quote fermé, login/reconnect intermittents) — on garde
# le filet de sécurité du restart mais on espace progressivement les tentatives.
# Compteur N = restarts weekend dans les 24 dernières heures.
#   N=0 → 1er restart au seuil standard (30 min)
#   N=1 → 2e si persistance ≥ 60 min
#   N=2 → 3e si persistance ≥ 120 min
#   N=3 → 4e si persistance ≥ 240 min (cap)
#   N≥4 → bloqué, anti-boucle URGENT distincte (intervention humaine)
WEEKEND_BACKOFF_THRESHOLDS_MIN = [30, 60, 120, 240]
WEEKEND_RESTART_MAX_24H = 4
WEEKEND_BACKOFF_WINDOW_S = 24 * 3600

# Weekend guard étendu : forex close 21:00 UTC vendredi (1h avant crypto)
# pour éviter auto-restart à vide pendant le bord de fenêtre.
WEEKEND_GUARD_FRI_HOUR = 21           # vendredi 21:00 UTC → debut weekend
WEEKEND_GUARD_SUN_HOUR = 22           # dimanche 22:00 UTC → fin weekend

# Task #40 patch #3 — seuil mtime POSITIONS_STATE. En fonctionnement normal,
# le fichier est touché par LivePositionMonitor à chaque register/unregister
# + checkpoint (cadence ~60-120s via BE polling / broker_reconcile). Au-delà
# de 10 min sans réécriture alors que le fichier existe → monitor probablement
# mort silencieusement.
POSITIONS_STATE_STALE_S = 600

BAR_PATTERN = re.compile(
    r"^(\w{3})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2}).*BarAggregator.*Résumé"
)
PRICEFEED_SUMMARY_PATTERN = re.compile(
    r"^(\w{3})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2}).*"
    r"PriceFeed.*?(\d+)/(\d+) actifs, "
    r"(\d+) dormants, (\d+) stale majeurs, (\d+) jamais reçus"
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


def _open_positions_count() -> tuple[int, bool]:
    """Lit le state file partagé du ``LivePositionMonitor``
    (cf. ``arabesque/execution/position_monitor.py``).

    Retourne ``(count, corrupted)`` :
      - Fichier absent → ``(0, False)`` (sémantique "vide = absent" écrite par
        ``LivePositionMonitor.save_state`` quand ``_positions`` se vide).
      - Fichier JSON dict valide → ``(len(dict), False)``.
      - Fichier corrompu (parse error) ou non-dict → ``(0, True)``.

    Task #40 patch #1 — bascule fail-safe → fail-loud. Le caller doit
    présumer hot path (surveillance feed active) quand ``corrupted=True``
    plutôt que skip silencieusement le weekend. Régression directe vs
    incident DASHUSD 2026-05-20 : un fail-safe qui retourne 0 en cas de
    corruption fait skip le weekend pile quand il faut rester actif.
    """
    if not POSITIONS_STATE.exists():
        return 0, False
    try:
        data = json.loads(POSITIONS_STATE.read_text())
    except Exception:
        return 0, True
    if not isinstance(data, dict):
        return 0, True
    return len(data), False


def _positions_state_age_seconds(now: dt.datetime) -> int | None:
    """Task #40 patch #3 — âge en secondes du fichier ``POSITIONS_STATE``.

    Retourne ``None`` si le fichier est absent (= sémantique légitime "0
    position" écrite par ``LivePositionMonitor.save_state``). Retourne
    ``int >= 0`` si le fichier existe.

    Utilisé par ``main()`` pour détecter un ``LivePositionMonitor`` mort
    silencieusement : fichier figé depuis > ``POSITIONS_STATE_STALE_S``
    alors que des positions sont trackées dedans (cf invariant 5 de
    ``tests/test_feed_watchdog_positions_state_mtime.py``).
    """
    if not POSITIONS_STATE.exists():
        return None
    try:
        mtime = POSITIONS_STATE.stat().st_mtime
    except OSError:
        return None
    age = (now.timestamp() - mtime)
    return max(0, int(age))


def _engine_active() -> bool:
    """Task #40 patch #2 — ``timeout=5`` sur ``systemctl is-active``. Si
    systemd freeze (dbus bloqué, OOM), retourne ``False`` (fail-safe :
    traité comme engine_inactive, branche sans danger)."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "arabesque-live.service"],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        print(
            "[feed_watchdog] WARNING: systemctl is-active timeout (5s) — "
            "presuming engine_inactive",
            file=sys.stderr,
        )
        return False
    return r.stdout.strip() == "active"


def _last_bar_age_seconds(now: dt.datetime) -> int | None:
    """Retourne l'âge en secondes de la dernière `BarAggregator Résumé`, ou None.

    Task #40 patch #2 — ``timeout=10`` sur ``journalctl``. Sur un journal
    chargé, ``--since "30 minutes ago"`` peut prendre 1-3s ; 10s laisse une
    marge. En cas de freeze (journal corrompu, mmap lent), retourne ``None``
    (fail-safe : traité comme ``no_bar_data_in_window``, notif normale sans
    auto-restart).
    """
    try:
        r = subprocess.run(
            ["journalctl", "--user", "-u", "arabesque-live.service",
             "--since", "30 minutes ago", "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        print(
            "[feed_watchdog] WARNING: journalctl timeout (10s) — "
            "presuming no_bar_data_in_window",
            file=sys.stderr,
        )
        return None
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


def _parse_journal_ts(now: dt.datetime, match: re.Match) -> dt.datetime | None:
    month_str, day_str, hh, mm, ss = match.groups()[:5]
    month = MONTH_MAP.get(month_str.lower()[:3])
    if not month:
        return None
    try:
        offset = now.astimezone().utcoffset()
        hours = offset.total_seconds() / 3600 if offset else 0
        ts_local = dt.datetime(
            now.year, month, int(day_str),
            int(hh), int(mm), int(ss),
            tzinfo=dt.timezone(dt.timedelta(hours=hours))
        )
    except Exception:
        return None
    return ts_local.astimezone(dt.timezone.utc)


def _last_pricefeed_summary(now: dt.datetime) -> dict | None:
    """Read the latest internal PriceFeed symbol-health summary.

    BarAggregator liveness can stay green while one symbol is dead. This is a
    cheap secondary integrity check: it never places orders and never restarts
    by itself, but it makes partial-feed degradation visible outside the engine.

    PriceFeed logs healthy summaries at DEBUG level, while degraded summaries
    are INFO. systemd usually only exposes the INFO degraded line; once the
    feed recovers, that last degraded line can remain the newest parseable
    summary for several watchdog cycles. Ignore stale summaries so an old
    pre-restart partial-feed line does not keep alerting after the engine is
    healthy again.
    """
    try:
        r = subprocess.run(
            ["journalctl", "--user", "-u", "arabesque-live.service",
             "--since", "30 minutes ago", "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    latest: dict | None = None
    for line in r.stdout.splitlines():
        m = PRICEFEED_SUMMARY_PATTERN.match(line)
        if not m:
            continue
        ts_utc = _parse_journal_ts(now, m)
        if ts_utc is None:
            continue
        active, total, dormant, stale_major, no_tick = map(int, m.groups()[5:])
        entry = {
            "ts": ts_utc.isoformat(),
            "age_seconds": int((now - ts_utc).total_seconds()),
            "active": active,
            "total": total,
            "dormant": dormant,
            "stale_major": stale_major,
            "no_tick": no_tick,
        }
        if latest is None or ts_utc > dt.datetime.fromisoformat(latest["ts"]):
            latest = entry
    if latest and latest["age_seconds"] > PRICEFEED_SUMMARY_MAX_AGE_S:
        return None
    return latest


def _read_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    """Task #40 patch #4 — pattern atomique via ``os.replace``.

    Sans ce pattern, un SIGKILL/OOM/disque plein au milieu de ``write_text``
    laisse un JSON tronqué. ``_read_state`` retombe alors silencieusement sur
    ``{}`` → perte de ``last_alert_ts`` (spam au prochain feed_stale),
    ``feed_stale_since_ts`` (auto-restart Étage 3 reporté de 30 min), etc.
    ``os.replace`` est atomique sur POSIX quand src/dst sont sur le même fs.
    """
    STATE.parent.mkdir(exist_ok=True)
    tmp = STATE.with_suffix(STATE.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE)
    try:
        if state.get("last_check_ts") and state.get("last_status"):
            now = dt.datetime.fromisoformat(state["last_check_ts"])
            _append_uptime_event(
                now,
                state["last_status"],
                state,
                cause=_infer_uptime_cause(state["last_status"]),
            )
    except Exception:
        pass


def _infer_uptime_cause(status: str) -> str:
    if status == "engine_inactive":
        return "engine_inactive"
    if status == "weekend_guard" or status.startswith("weekend_guard"):
        return "weekend"
    if status.startswith("feed_stale"):
        return "feed_stale"
    if status.startswith("pricefeed_partial"):
        return "partial_feed"
    if status == "no_bar_data_in_window":
        return "bar_aggregator_silent"
    if "loop_guard" in status:
        return "watchdog_restart_loop_guard"
    if "autorestart_failed" in status:
        return "watchdog_restart_failed"
    if status.startswith("ok:"):
        return "ok"
    return "unknown"


def _append_uptime_event(now: dt.datetime, status: str, state: dict,
                         *, cause: str = "unknown") -> None:
    """Append one watchdog availability sample.

    This file is intentionally append-only. Alerts tell us something happened;
    uptime events let us measure how often, for how long, and with which likely
    cause before running replay attribution on degraded windows.
    """
    try:
        event = {
            "event": "uptime_sample",
            "ts": now.isoformat(),
            "status": status,
            "cause": cause,
            "engine_active": status != "engine_inactive",
            "last_bar_age_seconds": state.get("last_bar_age_seconds"),
            "open_positions_count": state.get("open_positions_count", 0),
            "pricefeed": state.get("last_pricefeed_summary"),
        }
        if "feed_stale_since_ts" in state:
            event["feed_stale_since_ts"] = state["feed_stale_since_ts"]
        UPTIME_EVENTS.parent.mkdir(exist_ok=True)
        with UPTIME_EVENTS.open("a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception as e:
        print(f"[feed_watchdog] WARNING: uptime event write failed: {e}",
              file=sys.stderr)


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
    """Envoie une notification selon le niveau d'intervention.

    ``urgent=False`` = Telegram uniquement. ``urgent=True`` = Telegram et
    ntfy, avec un titre distinct pour rendre l'action attendue visible.
    """
    if not SECRETS.exists():
        return False
    secrets = yaml.safe_load(SECRETS.read_text()) or {}
    channels = select_notification_channels(
        (secrets.get("notifications") or {}).get("channels") or [],
        urgent=urgent,
    )
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


def _append_restart_history(now: dt.datetime, outcome: str, reason: str,
                            *, weekend: bool = False) -> None:
    """Append-only log des restarts auto. Lu par ``_recent_restart_count``
    et ``_recent_weekend_restart_count``. ``weekend=True`` tag les entrées
    déclenchées en weekend pour le compteur backoff dédié (Hot Path #2 bis).
    """
    RESTART_HISTORY.parent.mkdir(exist_ok=True)
    entry = {"ts": now.isoformat(), "outcome": outcome, "reason": reason}
    if weekend:
        entry["weekend"] = True
    with open(RESTART_HISTORY, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _recent_restart_count(now: dt.datetime, window_s: int = 3600) -> int:
    """Compte les restarts WEEKDAY réussis (``outcome=ok``) dans la fenêtre.

    **Filtre `weekend=True`** : les restarts weekend ont leur propre compteur
    backoff (``_recent_weekend_restart_count``) avec sa propre fenêtre 24h ;
    les mélanger ici bloquerait à tort un 1er restart weekday légitime juste
    après un weekend chargé (transition dimanche 22:00 UTC → lundi).
    """
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
            if entry.get("weekend"):
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


def _recent_weekend_restart_count(now: dt.datetime,
                                  window_s: int = WEEKEND_BACKOFF_WINDOW_S) -> int:
    """Compte les restarts weekend (``weekend=True`` dans l'entrée) dans la
    fenêtre. Inclut ``outcome=ok`` ET ``outcome=failed`` (même cause = on
    espace), exclut ``skipped_loop_guard``/``skipped_weekend_backoff``."""
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
            if not entry.get("weekend"):
                continue
            if entry.get("outcome") not in ("ok", "failed"):
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


def _attempt_auto_restart(now: dt.datetime, reason: str,
                          *, weekend: bool = False) -> tuple[bool, str]:
    """Tente ``systemctl --user stop`` + ``sleep`` + ``start``.

    Retourne ``(success, message)``. ``stop+sleep+start`` plutôt que ``restart``
    pour laisser la session cTrader se fermer côté serveur (sinon
    ALREADY_LOGGED_IN au redémarrage).

    ``weekend=True`` marque l'entrée d'historique pour le compteur backoff
    dédié (cf ``_recent_weekend_restart_count``).
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

    _append_restart_history(now, "ok", reason, weekend=weekend)
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

    open_count, positions_state_corrupted = _open_positions_count()
    if positions_state_corrupted:
        open_count = max(open_count, 1)
        state["positions_state_corrupted"] = True
    else:
        state.pop("positions_state_corrupted", None)
    state["open_positions_count"] = open_count

    weekend_with_positions = False
    if _is_weekend_utc(now):
        if positions_state_corrupted:
            # Task #40 patch #1 — fail-loud. State file corrompu/illisible :
            # on ne peut pas dire si une position est ouverte. Présumer hot
            # path (surveiller le feed) plutôt que skip silencieusement (=
            # régression DASHUSD 2026-05-20). Notif URGENT à l'humain (sous
            # cooldown _can_alert pour éviter le spam).
            weekend_with_positions = True
            if _can_alert(state, now):
                _send_alert(
                    f"POSITIONS_STATE ({POSITIONS_STATE.name}) corrompu ou "
                    f"illisible (non-dict). Watchdog presume hot path : "
                    f"surveillance feed active meme en weekend. "
                    f"Verifier le fichier (cat logs/position_monitor_state.json) "
                    f"et le LivePositionMonitor (journalctl --user -u "
                    f"arabesque-live | grep position_monitor).",
                    "Feed Arabesque — state file corrompu",
                    urgent=True,
                )
                state["last_alert_ts"] = now.isoformat()
        elif open_count == 0:
            # Comportement historique : marché fermé, rien ouvert → skip total
            state["last_status"] = "weekend_guard"
            state.pop("feed_stale_since_ts", None)
            state["open_positions_count"] = 0
            state.pop("positions_state_corrupted", None)
            _write_state(state)
            return 0
        else:
            # Hot Path #2 : ≥ 1 position traverse le weekend → surveillance active.
            # Auto-restart desactive (cf gating plus bas) : cTrader accepte les
            # sessions weekend mais leur comportement est erratique (feed quote
            # ferme, login/reconnect intermittents). Un restart auto risque de
            # patiner sans rien resoudre. On emet seulement l'alerte feed_stale
            # et on laisse l'humain decider.
            weekend_with_positions = True
            state["open_positions_count"] = open_count
            state.pop("positions_state_corrupted", None)
            # Hotfix 2026-05-23 22:15 UTC — patch #3 (mtime check) retiré :
            # ``LivePositionMonitor.save_state`` n'est appelé que sur
            # register/unregister/reconcile-checkpoint, pas périodiquement. En
            # weekend avec position dormante, le fichier date forcément de
            # l'ouverture → faux positif systématique → spam URGENT toutes
            # les 30 min (8 alertes 21:11→00:05 UTC sur la nuit du 2026-05-23).
            # Le check n'a pas de signal valide tant qu'on n'a pas une cadence
            # garantie de save_state. À ré-instrumenter une fois le monitor
            # patché pour checkpoint périodique indépendant de l'activité.
            state.pop("positions_state_stale", None)
            state.pop("positions_state_age_s", None)
    else:
        state.pop("positions_state_stale", None)
        state.pop("positions_state_age_s", None)

    age_s = _last_bar_age_seconds(now)
    state["last_bar_age_seconds"] = age_s
    pf_summary = _last_pricefeed_summary(now)
    if pf_summary:
        state["last_pricefeed_summary"] = pf_summary
    else:
        state.pop("last_pricefeed_summary", None)

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

        if AUTO_RESTART_REQUIRES_FLAT and open_count > 0:
            state["last_status"] += (
                f"+manual_required_open_positions(open={open_count})"
            )
            body = (
                f"FEED STALE avec {open_count} position(s) ouverte(s).\n"
                f"Derniere barre fermee il y a {age_s // 60}min"
                f"{age_s % 60:02d}s ; persistance "
                f"{int(persistence_s // 60)}min.\n"
                f"Auto-restart bloque par garde flat-only. "
                f"Intervention humaine requise : verifier la protection "
                f"broker-side et redemarrer manuellement si necessaire."
            )
            if _can_alert(state, now):
                ok = _send_alert(
                    body,
                    "Feed Arabesque mort — position ouverte",
                    urgent=True,
                )
                state["last_alert_ts"] = now.isoformat()
                state["last_alert_ok"] = bool(ok)
            else:
                state["last_status"] += "+cooldown"
            _write_state(state)
            return 0

        # === Branche weekend avec position : backoff progressif (Hot Path #2 bis) ===
        if weekend_with_positions:
            n_weekend = _recent_weekend_restart_count(now)

            if n_weekend >= WEEKEND_RESTART_MAX_24H:
                # Cap atteint → anti-boucle weekend, escalade URGENT distincte
                body = (
                    f"FEED STALE PERSISTANT en weekend depuis "
                    f"{int(persistence_s // 60)}min.\n"
                    f"Anti-boucle WEEKEND DECLENCHEE : {n_weekend} restart auto "
                    f"deja tentes dans les dernieres 24h.\n"
                    f"Auto-restart STOPPE — intervention humaine requise.\n"
                    f"Reco: investiguer journalctl --user -u arabesque-live "
                    f"et restart manuel si necessaire."
                )
                _append_restart_history(
                    now, "skipped_weekend_backoff", state["last_status"],
                    weekend=True,
                )
                if _can_alert(state, now):
                    _send_alert(
                        body, "Feed Arabesque — anti-boucle weekend",
                        urgent=True,
                    )
                    state["last_alert_ts"] = now.isoformat()
                state["last_status"] += f"+weekend_cap(n={n_weekend})"
                _write_state(state)
                return 0

            threshold_min = WEEKEND_BACKOFF_THRESHOLDS_MIN[n_weekend]
            if persistence_s >= threshold_min * 60:
                ok, msg = _attempt_auto_restart(
                    now, reason=state["last_status"], weekend=True,
                )
                if ok:
                    next_n = n_weekend + 1
                    next_str = (
                        f"{WEEKEND_BACKOFF_THRESHOLDS_MIN[next_n]}min"
                        if next_n < WEEKEND_RESTART_MAX_24H
                        else "anti-boucle (cap atteint)"
                    )
                    body = (
                        f"FEED STALE en weekend avec {open_count} position(s).\n"
                        f"Persistance {int(persistence_s // 60)}min "
                        f"(seuil weekend={threshold_min}min, N={n_weekend}).\n"
                        f"Auto-restart engine effectue (stop + sleep "
                        f"{RESTART_STOP_SLEEP_S}s + start).\n"
                        f"Backoff weekend: {next_n}/{WEEKEND_RESTART_MAX_24H} "
                        f"dans 24h. Prochain seuil: {next_str}."
                    )
                    _send_alert(
                        body, "Feed Arabesque — auto-restart weekend",
                        urgent=True,
                    )
                    state["last_alert_ts"] = now.isoformat()
                    state.pop("feed_stale_since_ts", None)
                    state["last_status"] += "+autorestart_ok(weekend)"
                else:
                    body = (
                        f"FEED STALE en weekend avec {open_count} position(s) "
                        f"mais auto-restart ECHOUE : {msg}.\n"
                        f"Backoff: {n_weekend+1}/{WEEKEND_RESTART_MAX_24H} "
                        f"tentatives dans 24h."
                    )
                    _append_restart_history(now, "failed", msg, weekend=True)
                    _send_alert(
                        body, "Feed Arabesque — auto-restart ECHEC (weekend)",
                        urgent=True,
                    )
                    state["last_alert_ts"] = now.isoformat()
                    state["last_status"] += f"+autorestart_failed(weekend):{msg}"
                _write_state(state)
                return 0

            # Persistance < seuil weekend backoff → notif Étage 1, attente
            body = (
                f"FEED STALE en weekend avec {open_count} position(s) ouverte(s).\n"
                f"Derniere barre fermee il y a {age_s // 60}min"
                f"{age_s % 60:02d}s.\n"
                f"Persistance {int(persistence_s // 60)}min "
                f"(seuil weekend backoff N={n_weekend}: {threshold_min}min).\n"
                f"Auto-restart se declenchera au seuil. Backoff: "
                f"{n_weekend}/{WEEKEND_RESTART_MAX_24H} restarts deja tentes "
                f"dans les 24h."
            )
            title = "Feed Arabesque — weekend (en attente seuil backoff)"
            # Tombe dans la branche commune _can_alert / _send_alert ci-dessous
            if not _can_alert(state, now):
                state["last_status"] += "+cooldown"
                _write_state(state)
                return 0
            ok = _send_alert(body, title, urgent=False)
            state["last_alert_ts"] = now.isoformat()
            state["last_alert_ok"] = bool(ok)
            _write_state(state)
            return 0

        # === Branche weekday standard — étage 3+4 inchangé ===
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

        # Persistance < 30 min en weekday → notif Étage 1 normale
        body = (
            f"Derniere barre fermee il y a {age_s // 60}min"
            f"{age_s % 60:02d}s.\n"
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
        if pf_summary:
            total = int(pf_summary.get("total") or 0)
            active = int(pf_summary.get("active") or 0)
            stale_major = int(pf_summary.get("stale_major") or 0)
            no_tick = int(pf_summary.get("no_tick") or 0)
            if total > 0 and (active < total or stale_major > 0 or no_tick > 0):
                state["last_status"] = (
                    f"pricefeed_partial:{active}/{total} "
                    f"stale_major={stale_major} no_tick={no_tick}"
                )
                body = (
                    f"BarAggregator vivant (derniere barre age={age_s}s), "
                    f"mais PriceFeed partiel: {active}/{total} actifs, "
                    f"{stale_major} stale majeurs, {no_tick} jamais recus.\n"
                    f"Aucune action automatique. Surveiller si cela persiste "
                    f"ou touche un instrument tradable actif."
                )
                title = "Feed Arabesque — flux partiel"
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

        # OK
        if weekend_with_positions:
            state["last_status"] = (
                f"weekend_guard_with_positions:ok age={age_s}s "
                f"open={open_count}"
            )
        else:
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
