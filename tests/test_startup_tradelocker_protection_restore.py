"""Startup recovery retains monitoring when TradeLocker hides SL/TP on Position."""
from __future__ import annotations

import asyncio
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
