"""GFT fills must be protected and kept under monitoring even on anomalies."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arabesque.broker.base import OrderResult
from arabesque.core.models import Side
from arabesque.execution.live import LiveEngine


def _signal():
    return SimpleNamespace(
        instrument="XAUUSD",
        side=Side.SHORT,
        close=4500.0,
        sl=4510.0,
        tp_indicative=4480.0,
        strategy_type="extension",
        signal_id="sig-protect",
    )


def _engine():
    engine = LiveEngine.__new__(LiveEngine)
    engine._dispatcher = MagicMock()
    engine._live_monitor = MagicMock()
    engine._live_monitor._notify_telegram = AsyncMock()
    engine._live_monitor._notify_ntfy = AsyncMock()
    return engine


def test_tradelocker_linked_protection_confirms_fill_without_amend():
    engine = _engine()
    broker = MagicMock()
    broker.config = {"type": "tradelocker"}
    broker.get_position_protection = AsyncMock(return_value=(4510.0, 4480.0))
    broker.get_symbol_info = AsyncMock(return_value=None)
    broker.amend_position_sltp = AsyncMock()
    pos = SimpleNamespace(stop_loss=None, take_profit=None)

    sl, tp = asyncio.run(
        engine._confirm_post_fill_protection(
            "gft_compte1", broker, _signal(), "P1", pos
        )
    )

    assert (sl, tp) == pytest.approx((4510.0, 4480.0))
    broker.amend_position_sltp.assert_not_awaited()
    kwargs = engine._live_monitor.record_protection_check.call_args.kwargs
    assert kwargs["confirmed"] is True
    assert kwargs["action"] == "verified"
    engine._dispatcher.block_broker_entries.assert_not_called()


def test_unconfirmed_gft_protection_quarantines_new_entries_but_returns_tracking_levels():
    engine = _engine()
    broker = MagicMock()
    broker.config = {"type": "tradelocker"}
    broker.get_position_protection = AsyncMock(return_value=None)
    broker.get_symbol_info = AsyncMock(return_value=None)
    broker.amend_position_sltp = AsyncMock(
        return_value=OrderResult(success=False, message="failed")
    )
    pos = SimpleNamespace(stop_loss=None, take_profit=None)

    sl, tp = asyncio.run(
        engine._confirm_post_fill_protection(
            "gft_compte1", broker, _signal(), "P1", pos
        )
    )

    assert (sl, tp) == pytest.approx((4510.0, 4480.0))
    engine._dispatcher.block_broker_entries.assert_called_once()
    assert engine._live_monitor._notify_telegram.await_count == 1
    assert engine._live_monitor._notify_ntfy.await_count == 1
    kwargs = engine._live_monitor.record_protection_check.call_args.kwargs
    assert kwargs["confirmed"] is False


def test_extreme_filled_position_is_registered_instead_of_dropped():
    engine = _engine()
    broker = MagicMock()
    broker.config = {"type": "ctrader"}
    broker.get_positions = AsyncMock(return_value=[
        SimpleNamespace(
            position_id="P-BAD",
            entry_price=4560.0,  # 6R away from signal close.
            volume=0.01,
            stop_loss=4510.0,
            take_profit=4480.0,
        )
    ])
    broker.get_quote = AsyncMock(return_value=None)
    broker.get_symbol_info = AsyncMock(return_value=None)
    engine._brokers = {"ftmo_challenge": broker}
    engine._position_monitor = MagicMock()
    engine._pending_fills = {}
    engine._save_pending_fills = MagicMock()
    result = SimpleNamespace(
        order_id="P-BAD", fill_volume=0.01, volume_lots=0.01, risk_cash=10.0
    )

    with patch("arabesque.execution.live.asyncio.sleep", new=AsyncMock()):
        asyncio.run(
            engine._register_position_in_monitor(
                "ftmo_challenge", _signal(), result
            )
        )

    engine._dispatcher.block_broker_entries.assert_called_once()
    engine._live_monitor.record_entry.assert_called_once()
    engine._position_monitor.register_position.assert_called_once()
