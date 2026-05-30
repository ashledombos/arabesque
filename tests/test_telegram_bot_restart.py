"""Tests pour la commande /restart du bot Telegram.

Vérifie la double-confirmation, la fenêtre de 30s, l'audit log,
et la robustesse face aux erreurs systemctl.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arabesque.bot import telegram_bot as bot


@pytest.fixture
def tmp_actions_log(tmp_path, monkeypatch):
    log = tmp_path / "bot_actions.jsonl"
    monkeypatch.setattr(bot, "BOT_ACTIONS_LOG", log)
    return log


@pytest.fixture
def fake_update():
    upd = MagicMock()
    upd.effective_chat.id = 12345
    upd.message.reply_text = AsyncMock()
    return upd


@pytest.fixture
def fake_context():
    ctx = MagicMock()
    ctx.bot_data = {}

    async def _auth(update, context):
        return True
    ctx.bot_data["auth"] = _auth
    return ctx


def _read_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]


def test_restart_sets_pending(tmp_actions_log, fake_update, fake_context):
    asyncio.run(bot.cmd_restart(fake_update, fake_context))
    pending = fake_context.bot_data["pending_restart"]
    assert 12345 in pending
    assert isinstance(pending[12345], datetime)
    fake_update.message.reply_text.assert_called_once()
    assert "restart_confirm" in fake_update.message.reply_text.call_args.args[0]
    log = _read_log(tmp_actions_log)
    assert log[-1]["action"] == "restart_request"
    assert log[-1]["status"] == "pending"


def test_restart_confirm_without_pending(tmp_actions_log, fake_update, fake_context):
    asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))
    msg = fake_update.message.reply_text.call_args.args[0]
    assert "Aucune demande" in msg
    log = _read_log(tmp_actions_log)
    assert log[-1]["status"] == "no_pending"


def test_restart_confirm_expired(tmp_actions_log, fake_update, fake_context):
    old = datetime.now(timezone.utc) - timedelta(seconds=bot.RESTART_CONFIRM_WINDOW_S + 5)
    fake_context.bot_data["pending_restart"] = {12345: old}
    asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))
    msg = fake_update.message.reply_text.call_args.args[0]
    assert "expirée" in msg
    log = _read_log(tmp_actions_log)
    assert log[-1]["status"] == "expired"
    # pending must be cleaned up
    assert 12345 not in fake_context.bot_data["pending_restart"]


def test_restart_confirm_ok(tmp_actions_log, fake_update, fake_context):
    fake_context.bot_data["pending_restart"] = {12345: datetime.now(timezone.utc)}

    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"", b""))

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    with patch.object(
        bot.asyncio, "create_subprocess_exec",
        side_effect=fake_create_subprocess_exec,
    ), patch.object(bot, "_engine_status", return_value={"active": "active", "uptime_h": 0.1}):
        asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))

    calls = [c.args[0] for c in fake_update.message.reply_text.call_args_list]
    assert any("en cours" in c for c in calls)
    assert any("Restart OK" in c for c in calls)
    log = _read_log(tmp_actions_log)
    assert log[-1]["action"] == "restart_exec"
    assert log[-1]["status"] == "ok"


def test_restart_confirm_systemctl_fails(tmp_actions_log, fake_update, fake_context):
    fake_context.bot_data["pending_restart"] = {12345: datetime.now(timezone.utc)}

    proc = MagicMock()
    proc.returncode = 1
    proc.communicate = AsyncMock(return_value=(b"", b"Unit not found"))

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    with patch.object(
        bot.asyncio, "create_subprocess_exec",
        side_effect=fake_create_subprocess_exec,
    ):
        asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))

    calls = [c.args[0] for c in fake_update.message.reply_text.call_args_list]
    assert any("échec" in c for c in calls)
    log = _read_log(tmp_actions_log)
    assert log[-1]["status"] == "fail"
    assert "Unit not found" in log[-1]["detail"]


def test_restart_confirm_consumes_pending(tmp_actions_log, fake_update, fake_context):
    """Une confirmation doit invalider la demande (un seul restart par /restart)."""
    fake_context.bot_data["pending_restart"] = {12345: datetime.now(timezone.utc)}

    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"", b""))

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    with patch.object(
        bot.asyncio, "create_subprocess_exec",
        side_effect=fake_create_subprocess_exec,
    ), patch.object(bot, "_engine_status", return_value={"active": "active", "uptime_h": 0.0}):
        asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))

    assert 12345 not in fake_context.bot_data["pending_restart"]

    # Une 2e confirmation immédiate doit être refusée
    fake_update.message.reply_text.reset_mock()
    asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))
    msg = fake_update.message.reply_text.call_args.args[0]
    assert "Aucune demande" in msg


def test_audit_log_records_chat_id(tmp_actions_log, fake_update, fake_context):
    asyncio.run(bot.cmd_restart(fake_update, fake_context))
    log = _read_log(tmp_actions_log)
    assert log[-1]["chat_id"] == 12345
    assert "ts" in log[-1]
