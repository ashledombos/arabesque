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

RESTART_CONFIRM_WINDOW_S = 30


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


def _log_action(chat_id: int | None, action: str, status: str, detail: str = "") -> None:
    BOT_ACTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "action": action,
        "status": status,
        "detail": detail,
    }
    with BOT_ACTIONS_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


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
    context.bot_data.setdefault("pending_restart", {})[chat_id] = datetime.now(
        timezone.utc
    )
    _log_action(chat_id, "restart_request", "pending")
    await update.message.reply_text(
        f"⚠️ Confirme avec /restart_confirm dans les {RESTART_CONFIRM_WINDOW_S}s "
        f"pour redémarrer arabesque-live.\n"
        f"Annule en ignorant le message."
    )


async def cmd_restart_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not await context.bot_data["auth"](update, context):
        return
    chat_id = update.effective_chat.id if update.effective_chat else None
    pending = context.bot_data.setdefault("pending_restart", {})
    requested_at = pending.pop(chat_id, None)
    now = datetime.now(timezone.utc)
    if requested_at is None:
        _log_action(chat_id, "restart_confirm", "no_pending")
        await update.message.reply_text(
            "Aucune demande /restart en attente. Tape /restart d'abord."
        )
        return
    age_s = (now - requested_at).total_seconds()
    if age_s > RESTART_CONFIRM_WINDOW_S:
        _log_action(chat_id, "restart_confirm", "expired", f"age={age_s:.1f}s")
        await update.message.reply_text(
            f"Demande expirée ({age_s:.0f}s > {RESTART_CONFIRM_WINDOW_S}s). "
            f"Retape /restart."
        )
        return

    await update.message.reply_text("⏳ Restart en cours…")
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "--user", "restart", "arabesque-live",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        _log_action(chat_id, "restart_exec", "timeout")
        await update.message.reply_text("❌ systemctl timeout (30s).")
        return
    except Exception as e:
        _log_action(chat_id, "restart_exec", "exception", str(e))
        await update.message.reply_text(f"❌ Erreur : {e}")
        return

    if proc.returncode != 0:
        err = (stderr.decode(errors="ignore") or stdout.decode(errors="ignore"))[:500]
        _log_action(chat_id, "restart_exec", "fail", err)
        await update.message.reply_text(
            f"❌ Restart échec (rc={proc.returncode}) : {err}"
        )
        return

    await asyncio.sleep(5)
    eng = _engine_status()
    active = eng.get("active")
    up = eng.get("uptime_h")
    up_s = f"{up:.1f}h" if up is not None else "?"
    _log_action(
        chat_id, "restart_exec", "ok",
        f"active={active} uptime={up_s}",
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
