"""An accepted order must remain tracked if broker confirmation is unavailable."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from arabesque.core.models import Side
from arabesque.execution.live import LiveEngine


def test_order_with_position_query_errors_is_preserved_as_pending():
    engine = LiveEngine.__new__(LiveEngine)
    broker = MagicMock()
    broker.get_positions = AsyncMock(side_effect=ConnectionError("HTTP 429"))
    engine._brokers = {"gft_compte1": broker}
    engine._live_monitor = MagicMock()
    engine._position_monitor = MagicMock()
    engine._pending_fills = {}
    engine._save_pending_fills = MagicMock()

    signal = SimpleNamespace(
        instrument="AUDJPY",
        side=Side.LONG,
        close=114.205,
        sl=114.016,
        tp_indicative=114.578,
        strategy_type="extension",
        signal_id="sig-unknown",
    )
    result = SimpleNamespace(
        order_id="POSITION-42",
        fill_volume=0.13,
        volume_lots=0.13,
        risk_cash=15.56,
    )

    with patch("arabesque.execution.live.asyncio.sleep", new=AsyncMock()):
        asyncio.run(
            engine._register_position_in_monitor("gft_compte1", signal, result)
        )

    assert broker.get_positions.await_count == 3
    engine._live_monitor.record_entry.assert_not_called()
    engine._live_monitor.record_pending_order.assert_called_once()
    assert "gft_compte1:POSITION-42" in engine._pending_fills
    engine._save_pending_fills.assert_called_once()
