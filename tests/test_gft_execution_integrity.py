"""GFT fills must be protected and kept under monitoring even on anomalies."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arabesque.broker.base import OrderResult, SymbolInfo
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
    engine.instruments = {}
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


def test_tradelocker_display_rounding_does_not_quarantine_protected_fill():
    engine = _engine()
    broker = MagicMock()
    broker.config = {"type": "tradelocker"}
    broker.get_position_protection = AsyncMock(return_value=(313.14, 276.79))
    broker.get_symbol_info = AsyncMock(return_value=SymbolInfo(
        symbol="BCHUSD",
        broker_symbol="BCHUSD",
        pip_size=0.01,
        tick_size=0.00001,
        digits=5,
        lot_size=1,
    ))
    broker.amend_position_sltp = AsyncMock()
    signal = SimpleNamespace(
        instrument="BCHUSD",
        side=Side.SHORT,
        close=300.0,
        sl=313.140714,
        tp_indicative=276.79357,
        strategy_type="extension",
        signal_id="sig-bch",
    )
    pos = SimpleNamespace(stop_loss=None, take_profit=None)

    sl, tp = asyncio.run(
        engine._confirm_post_fill_protection(
            "gft_compte1", broker, signal, "P-BCH", pos
        )
    )

    assert (sl, tp) == pytest.approx((313.14, 276.79))
    broker.amend_position_sltp.assert_not_awaited()
    assert engine._live_monitor.record_protection_check.call_args.kwargs["confirmed"]
    engine._dispatcher.block_broker_entries.assert_not_called()


def test_extreme_overrisk_filled_position_is_journaled_then_closed():
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
    broker.get_symbol_info = AsyncMock(return_value=SymbolInfo(
        symbol="XAUUSD",
        broker_symbol="XAUUSD",
        pip_size=0.01,
        tick_size=0.01,
        lot_size=100,
        digits=2,
    ))
    broker.close_position = AsyncMock(return_value=OrderResult(success=True))
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

    assert engine._dispatcher.block_broker_entries.call_count == 2
    engine._live_monitor.record_entry.assert_called_once()
    broker.close_position.assert_awaited_once()
    engine._position_monitor.register_position.assert_not_called()


def test_under_risk_is_journaled_and_kept_open():
    engine = _engine()
    broker = MagicMock()
    broker.get_symbol_info = AsyncMock(return_value=SymbolInfo(
        symbol="XRPUSD",
        broker_symbol="XRPUSD",
        pip_size=0.00001,
        tick_size=0.00001,
        lot_size=10000,
        digits=5,
    ))
    broker.close_position = AsyncMock()

    should_monitor = asyncio.run(
        engine._check_post_fill_risk_integrity(
            broker_id="ftmo_challenge",
            broker=broker,
            signal=SimpleNamespace(instrument="XRPUSD"),
            position_id="P-XRP",
            entry=1.2775,
            sl=1.3062892857,
            volume=0.01,
            expected_risk_cash=25.75,
        )
    )

    assert should_monitor is True
    kwargs = engine._live_monitor.record_risk_integrity_check.call_args.kwargs
    assert kwargs["status"] == "under_risk"
    assert kwargs["risk_ratio"] == pytest.approx(0.1118, rel=0.02)
    engine._live_monitor._notify_telegram.assert_awaited_once()
    engine._live_monitor._notify_ntfy.assert_not_awaited()
    broker.close_position.assert_not_awaited()


def test_over_risk_critical_closes_and_skips_monitoring():
    engine = _engine()
    broker = MagicMock()
    broker.get_symbol_info = AsyncMock(return_value=SymbolInfo(
        symbol="XAUUSD",
        broker_symbol="XAUUSD",
        pip_size=0.01,
        tick_size=0.01,
        lot_size=100,
        digits=2,
    ))
    broker.close_position = AsyncMock(return_value=OrderResult(
        success=True, message="closed"
    ))

    should_monitor = asyncio.run(
        engine._check_post_fill_risk_integrity(
            broker_id="gft_compte1",
            broker=broker,
            signal=SimpleNamespace(instrument="XAUUSD"),
            position_id="P-RISK",
            entry=4500.0,
            sl=4510.0,
            volume=0.20,
            expected_risk_cash=100.0,
        )
    )

    assert should_monitor is False
    kwargs = engine._live_monitor.record_risk_integrity_check.call_args.kwargs
    assert kwargs["status"] == "over_risk_critical"
    assert kwargs["risk_ratio"] == pytest.approx(2.0)
    broker.close_position.assert_awaited_once_with("P-RISK")
    engine._dispatcher.block_broker_entries.assert_called_once()
    assert engine._live_monitor._notify_ntfy.await_count == 1
