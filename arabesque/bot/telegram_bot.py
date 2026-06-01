"""Bot Telegram interactif pour piloter Arabesque depuis mobile.

Lecture (phase 1) + commande /restart de l'engine (phase 1.5).
Whitelist par chat_id depuis ``config/secrets.yaml``.

Usage::

    python -m arabesque.bot.telegram_bot
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

ROOT = Path(__file__).resolve().parent.parent.parent
SECRETS = ROOT / "config" / "secrets.yaml"
EDGE_AUDIT = ROOT / "logs" / "edge_audit_latest.md"
JOURNAL_DIR = ROOT / "logs" / "journal"
EQUITY_LOG = ROOT / "logs" / "equity_snapshots.jsonl"
MAINT_STATE = ROOT / "logs" / "maintenance_state.jsonl"
BOT_ACTIONS_LOG = ROOT / "logs" / "bot_actions.jsonl"
POSITION_MONITOR_STATE = ROOT / "logs" / "position_monitor_state.json"

RESTART_CONFIRM_WINDOW_S = 30
# Calque du cooldown feed_watchdog (2 restarts/h max). Toute tentative récente
# bloque, qu'elle ait reussi ou non : on ne veut pas qu'un echec systemctl
# puisse etre rejoué en boucle depuis Telegram.
RESTART_COOLDOWN_S = 600
# Delai entre stop et start pour laisser cTrader purger la session serveur-side
# (TTL ALREADY_LOGGED_IN observe plusieurs minutes lors des incidents 29-30/05).
RESTART_STOP_SLEEP_S = 60


def _md_to_plaintext(md: str) -> str:
    """Convertit un markdown léger en plain text lisible Telegram (sans parse_mode)."""
    out_lines = []
    for line in md.splitlines():
        s = line.rstrip()
        if s.startswith("# "):
            s = "═══ " + s[2:].upper() + " ═══"
        elif s.startswith("## "):
            s = "▸ " + s[3:]
        elif s.startswith("### "):
            s = "  • " + s[4:]
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        s = re.sub(r"(?<![\*_])\*([^*]+?)\*(?!\*)", r"\1", s)
        s = re.sub(r"`([^`]+)`", r"\1", s)
        out_lines.append(s)
    return "\n".join(out_lines)

TGRAM_RE = re.compile(r"tgram://([^/]+)/(\d+)")

logger = logging.getLogger("arabesque.bot")


def _parse_tgram_url(url: str) -> tuple[str, int] | None:
    m = TGRAM_RE.match(url)
    if not m:
        return None
    return m.group(1), int(m.group(2))


def _load_credentials() -> tuple[str, set[int]]:
    secrets = yaml.safe_load(SECRETS.read_text())
    channels = secrets.get("notifications", {}).get("channels", []) or []
    token = None
    allowed_chats: set[int] = set()
    for ch in channels:
        if not isinstance(ch, str):
            continue
        parsed = _parse_tgram_url(ch)
        if parsed:
            token, chat_id = parsed
            allowed_chats.add(chat_id)
    if not token:
        raise RuntimeError("No tgram:// channel found in config/secrets.yaml")
    extra = secrets.get("telegram_bot", {}).get("extra_chat_ids", []) or []
    for cid in extra:
        allowed_chats.add(int(cid))
    return token, allowed_chats


def _authorized(allowed: set[int]):
    async def check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id not in allowed:
            logger.warning("Unauthorized chat_id=%s", chat_id)
            if update.message:
                await update.message.reply_text("⛔ Non autorisé.")
            return False
        return True
    return check


def _engine_status() -> dict:
    try:
        active = subprocess.run(
            ["systemctl", "--user", "is-active", "arabesque-live.service"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        ts_raw = subprocess.run(
            ["systemctl", "--user", "show", "arabesque-live.service",
             "-p", "ActiveEnterTimestamp", "--value"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception as exc:
        return {"active": "unknown", "uptime": None, "error": str(exc)}
    uptime = None
    if ts_raw:
        try:
            ts = datetime.strptime(ts_raw, "%a %Y-%m-%d %H:%M:%S %Z")
            uptime = (datetime.now() - ts).total_seconds() / 3600
        except ValueError:
            pass
    return {"active": active, "uptime_h": uptime}


def _last_equity() -> dict:
    if not EQUITY_LOG.exists():
        return {}
    last_per_broker: dict = {}
    for line in EQUITY_LOG.read_text().splitlines()[-200:]:
        if not line.strip():
            continue
        try:
            import json
            o = json.loads(line)
        except Exception:
            continue
        b = o.get("broker_id") or o.get("broker") or "?"
        last_per_broker[b] = o
    return last_per_broker


def _log_action(
    chat_id: int | None,
    action: str,
    status: str,
    detail: str = "",
    extra: dict | None = None,
) -> None:
    BOT_ACTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "action": action,
        "status": status,
        "detail": detail,
    }
    if extra:
        entry.update(extra)
    with BOT_ACTIONS_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# Actions concerns par le cooldown : toute tentative recente bloque, qu'elle
# ait reussi, echoue, time-out ou leve une exception. Un /restart_request seul
# (avant confirmation) n'est pas compte — c'est la sequence physique qui peut
# perturber le live.
_RESTART_COOLDOWN_ACTIONS = {"restart_exec", "restart_stop", "restart_start"}


def _last_restart_attempt_age_s() -> float | None:
    """Retourne l'age (s) de la derniere tentative restart, tous statuts confondus.

    None si jamais tente ou journal absent. Lecture taillee (dernieres 500
    lignes) car le journal grossit avec /status, /positions, etc.
    """
    if not BOT_ACTIONS_LOG.exists():
        return None
    try:
        lines = BOT_ACTIONS_LOG.read_text().splitlines()[-500:]
    except OSError:
        return None
    now = datetime.now(timezone.utc)
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("action") not in _RESTART_COOLDOWN_ACTIONS:
            continue
        ts_raw = entry.get("ts")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        return (now - ts).total_seconds()
    return None


def _read_open_positions() -> tuple[int | None, dict[str, list[dict]]]:
    """Lit ``logs/position_monitor_state.json`` et retourne (count, par broker).

    - Fichier **absent** -> ``(0, {})``. ``position_monitor.save_state()`` purge
      le fichier sur etat flat (cf. ``arabesque/execution/position_monitor.py``
      L201-204), donc l'absence est l'indicateur normal de "rien d'ouvert".
    - Fichier **illisible / corrompu / JSON invalide** -> ``(None, {})``.
      L'appelant doit traiter ``None`` comme **inconnu** et fail-closed.
    - Fichier **valide** -> ``(count, by_broker)``.
    """
    if not POSITION_MONITOR_STATE.exists():
        return 0, {}
    try:
        data = json.loads(POSITION_MONITOR_STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return None, {}
    if not isinstance(data, dict):
        return None, {}
    by_broker: dict[str, list[dict]] = {}
    for pos in data.values():
        if not isinstance(pos, dict):
            continue
        broker_id = str(pos.get("broker_id", "?"))
        by_broker.setdefault(broker_id, []).append({
            "symbol": pos.get("symbol", "?"),
            "side": pos.get("side", "?"),
            "position_id": str(pos.get("position_id", "?")),
        })
    count = sum(len(v) for v in by_broker.values())
    return count, by_broker


def _format_positions_human(by_broker: dict[str, list[dict]]) -> str:
    if not by_broker:
        return "  (aucune)"
    lines = []
    for broker, items in sorted(by_broker.items()):
        for it in items:
            lines.append(
                f"  • {broker} : {it['symbol']} {it['side']} (id={it['position_id']})"
            )
    return "\n".join(lines)


async def _notify_ntfy_urgent(title: str, body: str) -> None:
    """Envoie une alerte urgente (Telegram + ntfy) via apprise.

    Reservee aux situations bloquantes ou aux echecs systemctl. Les messages
    Telegram nominaux du flux /restart restent envoyes via reply_text et n'ont
    pas besoin de passer par ce helper.
    """
    if not SECRETS.exists():
        return
    try:
        secrets = yaml.safe_load(SECRETS.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return
    from arabesque.notifications import select_notification_channels

    raw_channels = (secrets.get("notifications") or {}).get("channels") or []
    channels = select_notification_channels(raw_channels, urgent=True)
    if not channels:
        return
    try:
        import apprise
    except ImportError:
        return
    ap = apprise.Apprise()
    for ch in channels:
        if isinstance(ch, str):
            ap.add(ch)
    try:
        await ap.async_notify(
            body=body,
            title=f"[URGENT] {title}",
            body_format=apprise.NotifyFormat.TEXT,
        )
    except Exception as exc:  # ne JAMAIS faire planter le bot sur un echec notif
        logger.warning("ntfy urgent notification failed: %s", exc)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await context.bot_data["auth"](update, context):
        return
    msg = (
        "🤖 Arabesque bot\n\n"
        "/status — engine + DD + protection\n"
        "/positions — positions ouvertes par broker\n"
        "/edge — résumé audit edge live vs backtest\n"
        "/journal — derniers événements du mois (fallback dernier dispo)\n"
        "/suivi_state — dernier état /suivi (passages auto Claude)\n"
        "/restart — redémarre arabesque-live (double confirmation)\n"
        "/help — ce message"
    )
    await update.message.reply_text(msg)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await context.bot_data["auth"](update, context):
        return
    eng = _engine_status()
    eq = _last_equity()
    lines = ["📊 *Status*"]
    if eng.get("active") == "active":
        up = eng.get("uptime_h")
        up_s = f"{up:.1f}h" if up is not None else "?"
        lines.append(f"Engine : ✅ active ({up_s})")
    else:
        lines.append(f"Engine : ❌ {eng.get('active')}")
    for broker, snap in eq.items():
        prot = snap.get("protection_level", "?")
        eq_v = snap.get("equity")
        bal = snap.get("balance")
        lines.append(
            f"{broker} : eq={eq_v} bal={bal} protection={prot}"
        )
    if not eq:
        lines.append("(pas de snapshot equity récent)")
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines.append(f"\n_lu à {now}_")
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN,
    )


async def _broker_positions(account: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            ".venv/bin/python", "-m", "arabesque", "positions",
            "--account", account,
            cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        return f"{account}: timeout"
    text = stdout.decode(errors="replace")
    keep = []
    for line in text.splitlines():
        if any(k in line for k in
               ("Positions ouvertes", "Ordres en attente",
                "Balance", "Equity", "P&L", "Margin")):
            keep.append(line.strip())
    return f"*{account}*\n```\n" + "\n".join(keep) + "\n```"


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await context.bot_data["auth"](update, context):
        return
    await update.message.reply_text("⏳ Lecture brokers…")
    parts = []
    for acc in ("ftmo_challenge", "gft_compte1"):
        parts.append(await _broker_positions(acc))
    await update.message.reply_text(
        "\n\n".join(parts), parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_edge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await context.bot_data["auth"](update, context):
        return
    if not EDGE_AUDIT.exists():
        await update.message.reply_text("Pas d'audit edge.")
        return
    text = _md_to_plaintext(EDGE_AUDIT.read_text())
    if len(text) > 3800:
        text = text[:3800] + "\n…(tronqué)"
    await update.message.reply_text(text)


async def cmd_journal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await context.bot_data["auth"](update, context):
        return
    now = datetime.now(timezone.utc)
    candidates = sorted(JOURNAL_DIR.glob("*.md"))
    if not candidates:
        await update.message.reply_text("Pas de journal trouvé.")
        return
    target = JOURNAL_DIR / f"{now:%Y-%m}.md"
    if not target.exists():
        target = candidates[-1]
        prefix = f"_(mois courant absent, dernier dispo : {target.name})_\n\n"
    else:
        prefix = ""
    text = _md_to_plaintext(target.read_text())
    tail = text[-3500:]
    await update.message.reply_text(f"{prefix}{tail}")


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await context.bot_data["auth"](update, context):
        return
    chat_id = update.effective_chat.id if update.effective_chat else None
    args = [a.lower() for a in (context.args or [])]
    force = "force" in args

    count, by_broker = _read_open_positions()

    # Fail-closed sur etat inconnu : on ne sait pas si des trades sont
    # exposes, on refuse meme avec ``force`` (l'operateur doit verifier
    # manuellement, par exemple via /positions, avant d'insister).
    if count is None:
        _log_action(
            chat_id, "restart_request", "blocked_state_unknown",
            f"state_file={POSITION_MONITOR_STATE.name} absent_ou_illisible",
        )
        await update.message.reply_text(
            "⛔ Etat positions inconnu (position_monitor_state.json absent ou "
            "illisible). Restart refusé par sécurité — vérifie /positions et "
            "relance quand l'état est lisible."
        )
        await _notify_ntfy_urgent(
            "Arabesque /restart bloqué",
            "Etat positions inconnu, /restart refusé. Intervention humaine.",
        )
        return

    if count > 0 and not force:
        positions_text = _format_positions_human(by_broker)
        _log_action(
            chat_id, "restart_request", "blocked_open_positions",
            f"count={count}",
            extra={"positions_by_broker": by_broker, "force": False},
        )
        await update.message.reply_text(
            f"⛔ {count} position(s) ouverte(s) — restart refusé par défaut.\n"
            f"{positions_text}\n\n"
            f"Pour outrepasser : /restart force (puis /restart_confirm).\n"
            f"Un restart en position interrompt monitoring/BE/trailing."
        )
        await _notify_ntfy_urgent(
            "Arabesque /restart bloqué (positions ouvertes)",
            f"{count} position(s) ouverte(s). Operateur doit confirmer "
            f"avec /restart force s'il veut outrepasser.",
        )
        return

    context.bot_data.setdefault("pending_restart", {})[chat_id] = {
        "ts": datetime.now(timezone.utc),
        "force": force,
        "positions_at_request": by_broker,
    }
    _log_action(
        chat_id, "restart_request", "pending",
        f"force={force} count={count}",
        extra={"force": force, "positions_count": count,
               "positions_by_broker": by_broker},
    )
    if force and count > 0:
        positions_text = _format_positions_human(by_broker)
        await update.message.reply_text(
            f"⚠️ MODE FORCE — {count} position(s) ouverte(s) :\n"
            f"{positions_text}\n\n"
            f"Le restart va interrompre monitoring/BE/trailing en cours. "
            f"Le stop déclenche save_state, le start restaure les positions, "
            f"mais tout MFE/trailing en vol depuis le dernier checkpoint sera "
            f"perdu (≤ 2 min).\n\n"
            f"Confirme avec /restart_confirm dans les "
            f"{RESTART_CONFIRM_WINDOW_S}s. Annule en ignorant."
        )
    else:
        await update.message.reply_text(
            f"⚠️ Aucune position ouverte. Confirme avec /restart_confirm "
            f"dans les {RESTART_CONFIRM_WINDOW_S}s.\n"
            f"Séquence : stop → sleep {RESTART_STOP_SLEEP_S}s → start "
            f"(libère la session cTrader).\n"
            f"Annule en ignorant le message."
        )


async def _run_systemctl(
    action: str, timeout_s: float = 30.0,
) -> tuple[int | None, str, str]:
    """Lance ``systemctl --user <action> arabesque-live``.

    Retourne ``(returncode, stdout, stderr)``. ``returncode is None`` signale
    un timeout — l'appelant le distingue d'un echec rc != 0.
    """
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "--user", action, "arabesque-live",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        return None, "", "timeout"
    return proc.returncode, stdout.decode(errors="ignore"), stderr.decode(errors="ignore")


async def cmd_restart_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not await context.bot_data["auth"](update, context):
        return
    chat_id = update.effective_chat.id if update.effective_chat else None
    pending = context.bot_data.setdefault("pending_restart", {})
    request = pending.pop(chat_id, None)
    now = datetime.now(timezone.utc)
    if request is None:
        _log_action(chat_id, "restart_confirm", "no_pending")
        await update.message.reply_text(
            "Aucune demande /restart en attente. Tape /restart d'abord."
        )
        return

    requested_at = request["ts"] if isinstance(request, dict) else request
    force = bool(request.get("force")) if isinstance(request, dict) else False
    positions_by_broker = (
        request.get("positions_at_request") if isinstance(request, dict) else {}
    )

    age_s = (now - requested_at).total_seconds()
    if age_s > RESTART_CONFIRM_WINDOW_S:
        _log_action(chat_id, "restart_confirm", "expired", f"age={age_s:.1f}s")
        await update.message.reply_text(
            f"Demande expirée ({age_s:.0f}s > {RESTART_CONFIRM_WINDOW_S}s). "
            f"Retape /restart."
        )
        return

    cooldown_age = _last_restart_attempt_age_s()
    if cooldown_age is not None and cooldown_age < RESTART_COOLDOWN_S:
        remaining = int(RESTART_COOLDOWN_S - cooldown_age)
        _log_action(
            chat_id, "restart_confirm", "cooldown",
            f"last_attempt_age={cooldown_age:.0f}s remaining={remaining}s",
        )
        await update.message.reply_text(
            f"⛔ Cooldown actif. Dernière tentative il y a "
            f"{int(cooldown_age)}s ; réessaie dans {remaining}s "
            f"(seuil {RESTART_COOLDOWN_S}s)."
        )
        return

    log_extra = {
        "force": force,
        "positions_by_broker": positions_by_broker or {},
    }

    # --- STOP -------------------------------------------------------------
    await update.message.reply_text("⏸️ Stop en cours…")
    try:
        rc, stdout, stderr = await _run_systemctl("stop")
    except Exception as exc:
        _log_action(chat_id, "restart_stop", "exception", str(exc),
                    extra=log_extra)
        await update.message.reply_text(f"❌ Stop : exception {exc}")
        await _notify_ntfy_urgent(
            "Arabesque /restart : exception au stop",
            f"chat_id={chat_id} force={force} err={exc}",
        )
        return
    if rc is None:
        _log_action(chat_id, "restart_stop", "timeout", extra=log_extra)
        await update.message.reply_text("❌ Stop : systemctl timeout (30s).")
        await _notify_ntfy_urgent(
            "Arabesque /restart : timeout au stop",
            f"chat_id={chat_id} force={force} — état engine inconnu.",
        )
        return
    if rc != 0:
        err = (stderr or stdout)[:500]
        _log_action(chat_id, "restart_stop", "fail",
                    f"rc={rc} err={err}", extra=log_extra)
        await update.message.reply_text(
            f"❌ Stop échec (rc={rc}) : {err}\nSéquence interrompue."
        )
        await _notify_ntfy_urgent(
            "Arabesque /restart : echec stop",
            f"chat_id={chat_id} force={force} rc={rc} err={err}",
        )
        return
    _log_action(chat_id, "restart_stop", "ok", extra=log_extra)

    # --- SLEEP (async, ne bloque pas l'event loop du bot) ----------------
    await update.message.reply_text(
        f"⏳ Stop OK. Attente {RESTART_STOP_SLEEP_S}s avant start "
        f"(purge session cTrader)…"
    )
    await asyncio.sleep(RESTART_STOP_SLEEP_S)

    # --- START -----------------------------------------------------------
    await update.message.reply_text("▶️ Start en cours…")
    try:
        rc, stdout, stderr = await _run_systemctl("start")
    except Exception as exc:
        _log_action(chat_id, "restart_start", "exception", str(exc),
                    extra=log_extra)
        await update.message.reply_text(f"❌ Start : exception {exc}")
        await _notify_ntfy_urgent(
            "Arabesque /restart : exception au start",
            f"chat_id={chat_id} engine probablement arrete. err={exc}",
        )
        return
    if rc is None:
        _log_action(chat_id, "restart_start", "timeout", extra=log_extra)
        await update.message.reply_text(
            "❌ Start : systemctl timeout (30s) — engine peut-être arrêté."
        )
        await _notify_ntfy_urgent(
            "Arabesque /restart : timeout au start",
            f"chat_id={chat_id} engine probablement arrete.",
        )
        return
    if rc != 0:
        err = (stderr or stdout)[:500]
        _log_action(chat_id, "restart_start", "fail",
                    f"rc={rc} err={err}", extra=log_extra)
        await update.message.reply_text(
            f"❌ Start échec (rc={rc}) : {err}\nEngine probablement arrêté."
        )
        await _notify_ntfy_urgent(
            "Arabesque /restart : echec start",
            f"chat_id={chat_id} rc={rc} err={err}",
        )
        return

    await asyncio.sleep(5)
    eng = _engine_status()
    active = eng.get("active")
    up = eng.get("uptime_h")
    up_s = f"{up:.1f}h" if up is not None else "?"
    _log_action(
        chat_id, "restart_start", "ok",
        f"active={active} uptime={up_s}", extra=log_extra,
    )
    await update.message.reply_text(
        f"✅ Restart OK. Engine : {active} ({up_s})"
    )


async def cmd_suivi_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await context.bot_data["auth"](update, context):
        return
    if not MAINT_STATE.exists():
        await update.message.reply_text("Pas de maintenance_state.")
        return
    import json
    last_line = None
    for line in MAINT_STATE.read_text().splitlines():
        if line.strip():
            last_line = line
    if not last_line:
        await update.message.reply_text("État vide.")
        return
    try:
        s = json.loads(last_line)
    except json.JSONDecodeError:
        await update.message.reply_text("État illisible.")
        return
    lines = [
        "📋 Dernier /suivi",
        f"📅 {s.get('ts', '?')}",
        f"⏱ Délai depuis précédent : {s.get('delay_h_since_last', '?')}h",
        f"Engine : {'✅ OK' if s.get('engine_ok') else '❌'} "
        f"(uptime {s.get('engine_uptime_h')}h)",
    ]
    prot = s.get("protection", {})
    dd = s.get("dd_pct", {})
    for b in prot:
        lines.append(f"  {b}: {prot[b]}, DD={dd.get(b)}%")
    triggers = s.get("watchlist_triggered", []) or []
    if triggers:
        lines.append(f"⚠️ Triggers : {len(triggers)}")
        for t in triggers:
            lines.append(f"  • {t}")
    else:
        lines.append("Watchlist : 0 trigger")
    if s.get("bilan_ran"):
        lines.append(f"📊 Bilan exécuté : {s['bilan_ran']}")
    next_h = s.get("next_expected_in_hours")
    if next_h:
        lines.append(f"⏰ Prochain prévu : +{next_h}h")
    if note := s.get("note"):
        lines.append(f"\n📝 {note}")
    await update.message.reply_text("\n".join(lines))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    token, allowed = _load_credentials()
    logger.info("Bot starting — %d allowed chat_id(s)", len(allowed))
    app = Application.builder().token(token).build()
    app.bot_data["auth"] = _authorized(allowed)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("edge", cmd_edge))
    app.add_handler(CommandHandler("journal", cmd_journal))
    app.add_handler(CommandHandler("suivi_state", cmd_suivi_state))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("restart_confirm", cmd_restart_confirm))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
