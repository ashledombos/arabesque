"""Pending TradeLocker fills must be registered under their position ID.

Incident 2026-05-26: XAUUSD was placed as order ``...137324`` and filled as
position ``...746742``.  Polling compared those identifiers directly, never
registered the fill and the monitor later auto-closed it as an orphan.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from arabesque.execution.live import LiveEngine


def test_pending_fill_resolves_order_id_to_position_id():
    engine = LiveEngine.__new__(LiveEngine)
    engine._pending_fills = {
        "gft_compte1:ORDER-1": {
            "broker_id": "gft_compte1",
            "order_id": "ORDER-1",
            "instrument": "XAUUSD",
            "side": "SHORT",
            "signal_close": 4532.38,
            "signal_sl": 4550.31,
            "signal_tp": 4496.51,
            "strategy_type": "extension",
            "signal_id": "sig-1",
            "risk_cash": 15.56,
            "ts_placed": 1.0,
        }
    }

    position = SimpleNamespace(
        position_id="POSITION-9",
        entry_price=4532.40,
        volume=0.01,
        stop_loss=4550.31,
        take_profit=4496.51,
    )
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[position])
    broker.resolve_position_id_from_order_id = AsyncMock(return_value="POSITION-9")
    broker.get_quote = AsyncMock(return_value=None)
    broker.get_symbol_info = AsyncMock(return_value=None)
    engine._brokers = {"gft_compte1": broker}

    engine._live_monitor = MagicMock()
    engine._position_monitor = MagicMock()
    engine._save_pending_fills = MagicMock()

    asyncio.run(engine._poll_pending_fills())

    broker.resolve_position_id_from_order_id.assert_awaited_once_with("ORDER-1")
    assert not engine._pending_fills
    entry_kwargs = engine._live_monitor.record_entry.call_args.kwargs
    assert entry_kwargs["position_id"] == "POSITION-9"
    register_kwargs = engine._position_monitor.register_position.call_args.kwargs
    assert register_kwargs["position_id"] == "POSITION-9"
    engine._save_pending_fills.assert_called_once()


def test_old_working_pending_is_not_expired_while_broker_still_reports_it(monkeypatch):
    engine = LiveEngine.__new__(LiveEngine)
    engine._pending_fills = {
        "gft_compte1:ORDER-OLD": {
            "broker_id": "gft_compte1",
            "order_id": "ORDER-OLD",
            "instrument": "XAUUSD",
            "side": "LONG",
            "signal_close": 4500.0,
            "signal_sl": 4490.0,
            "strategy_type": "extension",
            "risk_cash": 20.0,
            "ts_placed": 1.0,
        }
    }
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])
    broker.resolve_position_id_from_order_id = AsyncMock(return_value=None)
    broker.get_pending_orders = AsyncMock(
        return_value=[SimpleNamespace(order_id="ORDER-OLD")]
    )
    engine._brokers = {"gft_compte1": broker}
    engine._live_monitor = MagicMock()
    engine._position_monitor = None
    engine._save_pending_fills = MagicMock()
    monkeypatch.setattr("arabesque.execution.live.time.time", lambda: 100_000.0)

    asyncio.run(engine._poll_pending_fills())

    assert "gft_compte1:ORDER-OLD" in engine._pending_fills
    engine._live_monitor.record_pending_expired.assert_not_called()
    engine._save_pending_fills.assert_not_called()
