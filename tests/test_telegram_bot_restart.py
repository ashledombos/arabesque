"""Tests pour la commande /restart du bot Telegram.

Vérifie :
- Refus fail-closed si état positions inconnu (state file absent/illisible)
- Refus par défaut si positions ouvertes ; /restart force outrepasse avec
  message explicite et journalisation positions_by_broker
- Séquence stop → sleep RESTART_STOP_SLEEP_S → start (avec sleep asyncio)
- Cooldown sur toute tentative récente (ok, fail, timeout)
- Échec stop avorte la séquence (pas de start), ntfy urgent
- Échec start après stop OK → ntfy urgent, engine probablement arrêté
- Logs JSONL séparés restart_stop / restart_start
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
def tmp_state_file(tmp_path, monkeypatch):
    state = tmp_path / "position_monitor_state.json"
    monkeypatch.setattr(bot, "POSITION_MONITOR_STATE", state)
    return state


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
    ctx.args = []

    async def _auth(update, context):
        return True
    ctx.bot_data["auth"] = _auth
    return ctx


def _read_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]


def _write_state(state_file: Path, positions: list[dict]) -> None:
    data = {f"{p['broker_id']}:{p['position_id']}": p for p in positions}
    state_file.write_text(json.dumps(data))


def _flat_state(state_file: Path) -> None:
    state_file.write_text("{}")


def _mock_ntfy(monkeypatch):
    """Remplace _notify_ntfy_urgent par un AsyncMock et retourne le mock."""
    m = AsyncMock()
    monkeypatch.setattr(bot, "_notify_ntfy_urgent", m)
    return m


# --------------------------------------------------------------------------
# /restart — pré-checks
# --------------------------------------------------------------------------

def test_restart_blocked_when_state_file_missing(
    tmp_actions_log, tmp_state_file, fake_update, fake_context, monkeypatch,
):
    """État inconnu → refus fail-closed même si engine pourrait être flat."""
    ntfy = _mock_ntfy(monkeypatch)
    asyncio.run(bot.cmd_restart(fake_update, fake_context))

    assert "pending_restart" not in fake_context.bot_data or \
        12345 not in fake_context.bot_data.get("pending_restart", {})
    msg = fake_update.message.reply_text.call_args.args[0]
    assert "Etat positions inconnu" in msg
    assert "refusé" in msg
    log = _read_log(tmp_actions_log)
    assert log[-1]["status"] == "blocked_state_unknown"
    ntfy.assert_awaited_once()


def test_restart_blocked_when_positions_open_no_force(
    tmp_actions_log, tmp_state_file, fake_update, fake_context, monkeypatch,
):
    _write_state(tmp_state_file, [
        {"broker_id": "ftmo_challenge", "position_id": "53546867",
         "symbol": "AUDJPY", "side": "LONG"},
    ])
    ntfy = _mock_ntfy(monkeypatch)
    asyncio.run(bot.cmd_restart(fake_update, fake_context))

    assert 12345 not in fake_context.bot_data.get("pending_restart", {})
    msg = fake_update.message.reply_text.call_args.args[0]
    assert "1 position" in msg
    assert "refusé" in msg
    assert "AUDJPY" in msg
    assert "ftmo_challenge" in msg
    assert "/restart force" in msg
    log = _read_log(tmp_actions_log)
    assert log[-1]["status"] == "blocked_open_positions"
    assert log[-1]["positions_by_broker"]["ftmo_challenge"][0]["symbol"] == "AUDJPY"
    assert log[-1]["force"] is False
    ntfy.assert_awaited_once()


def test_restart_force_accepts_open_positions_with_warning(
    tmp_actions_log, tmp_state_file, fake_update, fake_context, monkeypatch,
):
    _write_state(tmp_state_file, [
        {"broker_id": "ftmo_challenge", "position_id": "100",
         "symbol": "AUDJPY", "side": "LONG"},
        {"broker_id": "gft_compte1", "position_id": "200",
         "symbol": "XAUUSD", "side": "SHORT"},
    ])
    _mock_ntfy(monkeypatch)
    fake_context.args = ["force"]
    asyncio.run(bot.cmd_restart(fake_update, fake_context))

    assert 12345 in fake_context.bot_data["pending_restart"]
    pending = fake_context.bot_data["pending_restart"][12345]
    assert pending["force"] is True
    assert pending["positions_at_request"]["ftmo_challenge"][0]["symbol"] == "AUDJPY"

    msg = fake_update.message.reply_text.call_args.args[0]
    assert "MODE FORCE" in msg
    assert "2 position" in msg
    assert "AUDJPY" in msg
    assert "XAUUSD" in msg
    assert "monitoring/BE/trailing" in msg

    log = _read_log(tmp_actions_log)
    assert log[-1]["status"] == "pending"
    assert log[-1]["force"] is True
    assert log[-1]["positions_count"] == 2
    assert "ftmo_challenge" in log[-1]["positions_by_broker"]
    assert "gft_compte1" in log[-1]["positions_by_broker"]


def test_restart_flat_no_force_passes(
    tmp_actions_log, tmp_state_file, fake_update, fake_context, monkeypatch,
):
    _flat_state(tmp_state_file)
    _mock_ntfy(monkeypatch)
    asyncio.run(bot.cmd_restart(fake_update, fake_context))

    assert 12345 in fake_context.bot_data["pending_restart"]
    pending = fake_context.bot_data["pending_restart"][12345]
    assert pending["force"] is False
    msg = fake_update.message.reply_text.call_args.args[0]
    assert "Aucune position ouverte" in msg
    assert f"sleep {bot.RESTART_STOP_SLEEP_S}s" in msg


# --------------------------------------------------------------------------
# /restart_confirm — pre-checks
# --------------------------------------------------------------------------

def test_confirm_without_pending(tmp_actions_log, fake_update, fake_context):
    asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))
    msg = fake_update.message.reply_text.call_args.args[0]
    assert "Aucune demande" in msg
    log = _read_log(tmp_actions_log)
    assert log[-1]["status"] == "no_pending"


def test_confirm_expired(tmp_actions_log, fake_update, fake_context):
    old = datetime.now(timezone.utc) - timedelta(
        seconds=bot.RESTART_CONFIRM_WINDOW_S + 5
    )
    fake_context.bot_data["pending_restart"] = {
        12345: {"ts": old, "force": False, "positions_at_request": {}},
    }
    asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))
    msg = fake_update.message.reply_text.call_args.args[0]
    assert "expirée" in msg
    log = _read_log(tmp_actions_log)
    assert log[-1]["status"] == "expired"
    assert 12345 not in fake_context.bot_data["pending_restart"]


def test_confirm_cooldown_blocks_any_recent_attempt(
    tmp_actions_log, fake_update, fake_context, monkeypatch,
):
    """Tout restart_exec/stop/start, succès comme échec, bloque dans la fenêtre."""
    _mock_ntfy(monkeypatch)
    recent = datetime.now(timezone.utc) - timedelta(seconds=120)
    # On simule une tentative échouée il y a 2 minutes (< 600s cooldown)
    bot._log_action(99, "restart_start", "fail", "rc=1")
    # Patch l'horodatage de la ligne pour qu'elle apparaisse à -120s.
    lines = tmp_actions_log.read_text().splitlines()
    rewritten = []
    for line in lines:
        entry = json.loads(line)
        entry["ts"] = recent.isoformat()
        rewritten.append(json.dumps(entry))
    tmp_actions_log.write_text("\n".join(rewritten) + "\n")

    fake_context.bot_data["pending_restart"] = {
        12345: {"ts": datetime.now(timezone.utc), "force": False,
                "positions_at_request": {}},
    }
    asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))
    msg = fake_update.message.reply_text.call_args.args[0]
    assert "Cooldown" in msg
    log = _read_log(tmp_actions_log)
    assert log[-1]["status"] == "cooldown"


def test_confirm_cooldown_ignored_for_restart_request(
    tmp_actions_log, fake_update, fake_context, monkeypatch,
):
    """Un /restart sans confirm (status=pending) ne déclenche pas le cooldown."""
    _mock_ntfy(monkeypatch)
    recent = datetime.now(timezone.utc) - timedelta(seconds=30)
    bot._log_action(99, "restart_request", "pending")
    lines = tmp_actions_log.read_text().splitlines()
    rewritten = []
    for line in lines:
        entry = json.loads(line)
        entry["ts"] = recent.isoformat()
        rewritten.append(json.dumps(entry))
    tmp_actions_log.write_text("\n".join(rewritten) + "\n")

    fake_context.bot_data["pending_restart"] = {
        12345: {"ts": datetime.now(timezone.utc), "force": False,
                "positions_at_request": {}},
    }

    # Mock systemctl pour ne pas appeler le vrai
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"", b""))

    async def fake_exec(*args, **kwargs):
        return proc

    with patch.object(bot.asyncio, "create_subprocess_exec", side_effect=fake_exec), \
         patch.object(bot.asyncio, "sleep", new=AsyncMock()), \
         patch.object(bot, "_engine_status",
                      return_value={"active": "active", "uptime_h": 0.0}):
        asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))

    # Doit NE PAS s'être arrêté en cooldown — séquence doit avoir tourné
    statuses = [e["status"] for e in _read_log(tmp_actions_log)]
    assert "cooldown" not in statuses
    assert "ok" in statuses


# --------------------------------------------------------------------------
# Séquence stop → sleep → start
# --------------------------------------------------------------------------

def _setup_pending(fake_context):
    fake_context.bot_data["pending_restart"] = {
        12345: {"ts": datetime.now(timezone.utc), "force": False,
                "positions_at_request": {}},
    }


def test_confirm_runs_stop_sleep_start_in_order(
    tmp_actions_log, fake_update, fake_context, monkeypatch,
):
    _mock_ntfy(monkeypatch)
    _setup_pending(fake_context)

    calls: list[tuple[str, ...]] = []

    def make_proc():
        p = MagicMock()
        p.returncode = 0
        p.communicate = AsyncMock(return_value=(b"", b""))
        return p

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return make_proc()

    sleep_mock = AsyncMock()
    with patch.object(bot.asyncio, "create_subprocess_exec", side_effect=fake_exec), \
         patch.object(bot.asyncio, "sleep", new=sleep_mock), \
         patch.object(bot, "_engine_status",
                      return_value={"active": "active", "uptime_h": 0.1}):
        asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))

    # systemctl appelé deux fois, stop puis start
    assert len(calls) == 2
    assert calls[0][:4] == ("systemctl", "--user", "stop", "arabesque-live")
    assert calls[1][:4] == ("systemctl", "--user", "start", "arabesque-live")
    # sleep async appelé avec RESTART_STOP_SLEEP_S (+ asyncio.sleep(5) post-start)
    sleep_args = [c.args[0] for c in sleep_mock.await_args_list]
    assert bot.RESTART_STOP_SLEEP_S in sleep_args

    log = _read_log(tmp_actions_log)
    actions = [e["action"] for e in log]
    statuses_by_action = {e["action"]: e["status"] for e in log
                          if e["action"] in {"restart_stop", "restart_start"}}
    assert "restart_stop" in actions
    assert "restart_start" in actions
    assert statuses_by_action["restart_stop"] == "ok"
    assert statuses_by_action["restart_start"] == "ok"


def test_confirm_stop_failure_aborts_no_start(
    tmp_actions_log, fake_update, fake_context, monkeypatch,
):
    ntfy = _mock_ntfy(monkeypatch)
    _setup_pending(fake_context)

    calls: list[tuple[str, ...]] = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        p = MagicMock()
        p.returncode = 1
        p.communicate = AsyncMock(return_value=(b"", b"Unit not loaded"))
        return p

    sleep_mock = AsyncMock()
    with patch.object(bot.asyncio, "create_subprocess_exec", side_effect=fake_exec), \
         patch.object(bot.asyncio, "sleep", new=sleep_mock):
        asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))

    # Un seul systemctl appelé : stop, jamais start
    assert len(calls) == 1
    assert calls[0][2] == "stop"
    sleep_args = [c.args[0] for c in sleep_mock.await_args_list]
    assert bot.RESTART_STOP_SLEEP_S not in sleep_args

    log = _read_log(tmp_actions_log)
    statuses_by_action = {(e["action"], e["status"]) for e in log}
    assert ("restart_stop", "fail") in statuses_by_action
    assert not any(a == "restart_start" for a, _ in statuses_by_action)
    ntfy.assert_awaited()


def test_confirm_start_failure_after_stop_ok_notifies_urgent(
    tmp_actions_log, fake_update, fake_context, monkeypatch,
):
    ntfy = _mock_ntfy(monkeypatch)
    _setup_pending(fake_context)

    call_count = {"n": 0}

    async def fake_exec(*args, **kwargs):
        call_count["n"] += 1
        p = MagicMock()
        # stop OK, start fail
        if call_count["n"] == 1:
            p.returncode = 0
            p.communicate = AsyncMock(return_value=(b"", b""))
        else:
            p.returncode = 5
            p.communicate = AsyncMock(return_value=(b"", b"Failed to start"))
        return p

    with patch.object(bot.asyncio, "create_subprocess_exec", side_effect=fake_exec), \
         patch.object(bot.asyncio, "sleep", new=AsyncMock()):
        asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))

    log = _read_log(tmp_actions_log)
    statuses_by_action = {(e["action"], e["status"]) for e in log}
    assert ("restart_stop", "ok") in statuses_by_action
    assert ("restart_start", "fail") in statuses_by_action
    ntfy.assert_awaited()
    # message Telegram final mentionne start fail
    msgs = [c.args[0] for c in fake_update.message.reply_text.call_args_list]
    assert any("Start échec" in m for m in msgs)


def test_confirm_stop_timeout_notifies_urgent(
    tmp_actions_log, fake_update, fake_context, monkeypatch,
):
    ntfy = _mock_ntfy(monkeypatch)
    _setup_pending(fake_context)

    async def fake_exec(*args, **kwargs):
        p = MagicMock()
        # communicate ne retourne jamais → asyncio.wait_for lèvera TimeoutError
        p.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        return p

    with patch.object(bot.asyncio, "create_subprocess_exec", side_effect=fake_exec), \
         patch.object(bot.asyncio, "sleep", new=AsyncMock()):
        asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))

    log = _read_log(tmp_actions_log)
    assert any(e["action"] == "restart_stop" and e["status"] == "timeout"
               for e in log)
    ntfy.assert_awaited()


def test_confirm_consumes_pending(
    tmp_actions_log, tmp_state_file, fake_update, fake_context, monkeypatch,
):
    _mock_ntfy(monkeypatch)
    _setup_pending(fake_context)

    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"", b""))

    async def fake_exec(*args, **kwargs):
        return proc

    with patch.object(bot.asyncio, "create_subprocess_exec", side_effect=fake_exec), \
         patch.object(bot.asyncio, "sleep", new=AsyncMock()), \
         patch.object(bot, "_engine_status",
                      return_value={"active": "active", "uptime_h": 0.0}):
        asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))

    assert 12345 not in fake_context.bot_data["pending_restart"]

    fake_update.message.reply_text.reset_mock()
    asyncio.run(bot.cmd_restart_confirm(fake_update, fake_context))
    msg = fake_update.message.reply_text.call_args.args[0]
    assert "Aucune demande" in msg


def test_audit_log_records_chat_id(
    tmp_actions_log, tmp_state_file, fake_update, fake_context, monkeypatch,
):
    _flat_state(tmp_state_file)
    _mock_ntfy(monkeypatch)
    asyncio.run(bot.cmd_restart(fake_update, fake_context))
    log = _read_log(tmp_actions_log)
    assert log[-1]["chat_id"] == 12345
    assert "ts" in log[-1]
