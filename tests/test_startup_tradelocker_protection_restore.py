"""Startup recovery retains monitoring when TradeLocker hides SL/TP on Position."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from arabesque.broker.base import OrderSide
from arabesque.execution.live import LiveEngine


def test_existing_position_uses_attached_protection_on_restart():
    engine = LiveEngine.__new__(LiveEngine)
    position = SimpleNamespace(
        position_id="POSITION-42",
        symbol="AUDJPY",
        side=OrderSide.BUY,
        entry_price=114.205,
        stop_loss=None,
        take_profit=None,
        volume=0.13,
    )
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[position])
    broker.get_symbol_info = AsyncMock(return_value=None)
    broker.get_position_protection = AsyncMock(return_value=(114.016, 114.578))
    engine._brokers = {"gft_compte1": broker}
    engine._position_monitor = MagicMock()

    asyncio.run(engine._reconcile_existing_positions())

    broker.get_position_protection.assert_awaited_once_with("POSITION-42")
    kwargs = engine._position_monitor.register_position.call_args.kwargs
    assert kwargs["sl"] == 114.016
    assert kwargs["tp"] == 114.578


def test_reconcile_does_not_claim_zero_positions_when_broker_is_unavailable(caplog):
    engine = LiveEngine.__new__(LiveEngine)
    broker = MagicMock()
    broker.get_positions = AsyncMock(side_effect=ConnectionError("HTTP 429"))
    engine._brokers = {"gft_compte1": broker}
    engine._position_monitor = MagicMock()

    with caplog.at_level(logging.WARNING):
        asyncio.run(engine._reconcile_existing_positions())

    assert "Réconciliation incomplète" in caplog.text
    assert "aucune position ouverte" not in caplog.text


def test_startup_notification_marks_positions_unknown_on_broker_failure():
    engine = LiveEngine.__new__(LiveEngine)
    broker = MagicMock()
    broker.get_account_info = AsyncMock(
        return_value=SimpleNamespace(balance=142_000.0, equity=142_000.0)
    )
    broker.get_positions = AsyncMock(side_effect=ConnectionError("HTTP 429"))
    monitor = MagicMock()
    monitor.notify_startup = AsyncMock()
    monitor._protection_per_broker = {}
    engine._brokers = {"gft_compte1": broker}
    engine._live_monitor = monitor
    engine._broker_initial_balance = {"gft_compte1": 150_000.0}
    engine._broker_daily_start_balance = {"gft_compte1": 142_000.0}

    asyncio.run(engine._notify_startup_state())

    states = monitor.notify_startup.await_args.args[0]
    assert states["gft_compte1"]["positions_known"] is False
