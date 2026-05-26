"""Safety regressions for live dispatch across prop-firm accounts.

Before 2026-05-26 the dispatcher carried only FTMO state/config while placing
orders on GFT too, and ``worst_case_budget`` was not enabled in live mode.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from arabesque.broker.base import OrderType, PriceTick
from arabesque.core.guards import AccountState, PropConfig
from arabesque.core.models import Signal, Side
from arabesque.execution.live import LiveEngine
from arabesque.execution.order_dispatcher import OrderDispatcher, PendingSignal


class _Broker:
    config = {"type": "tradelocker"}

    def map_symbol(self, symbol: str) -> str:
        return symbol + ".X"

    async def get_symbol_info(self, symbol: str):
        return None


def _signal() -> Signal:
    return Signal(
        signal_id="gft-risk",
        instrument="XAUUSD",
        side=Side.LONG,
        timeframe="1h",
        close=4500.0,
        sl=4490.0,
        tp_indicative=4520.0,
        atr=20.0,
        bb_width=0.01,
        rr=2.0,
        strategy_type="extension",
    )


def _pending(signal: Signal) -> PendingSignal:
    return PendingSignal(
        signal=signal,
        entry_price=signal.close,
        order_type=OrderType.STOP,
        volume_lots=1.0,
        risk_cash=999.0,  # Must be replaced with the broker-specific sizing.
        expiry=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _dispatcher(gft_prop: PropConfig) -> OrderDispatcher:
    return OrderDispatcher(
        brokers={"ftmo_challenge": _Broker(), "gft_compte1": _Broker()},
        instruments_cfg={"XAUUSD": {"pip_size": 0.01, "pip_value_per_lot": 1.0}},
        prop_config=PropConfig(
            risk_per_trade_pct=0.45,
            max_daily_dd_pct=3.0,
            max_total_dd_pct=8.0,
        ),
        prop_configs_by_broker={"gft_compte1": gft_prop},
        dry_run=True,
    )


def test_live_dispatcher_enables_worst_case_budget():
    dispatcher = _dispatcher(PropConfig())

    assert dispatcher.guards.live_mode is True
    assert dispatcher._guards_by_broker["gft_compte1"].live_mode is True


def test_gft_order_is_sized_from_gft_state_not_primary_pending_budget():
    dispatcher = _dispatcher(
        PropConfig(
            risk_per_trade_pct=0.30,
            max_daily_dd_pct=2.5,
            max_total_dd_pct=8.0,
        )
    )
    state = AccountState(
        balance=142110.0,
        equity=142110.0,
        start_balance=150000.0,
        daily_start_balance=142110.0,
    )
    dispatcher.update_account_state(state, broker_id="gft_compte1")

    result = asyncio.run(
        dispatcher._place_on_broker(
            "gft_compte1",
            dispatcher.brokers["gft_compte1"],
            _pending(_signal()),
            PriceTick("XAUUSD", 4500.0, 4500.1),
        )
    )

    expected = dispatcher._guards_by_broker["gft_compte1"].compute_sizing(
        _signal(), state
    )["risk_cash"]
    assert result.success is True
    assert result.risk_cash == pytest.approx(expected)
    assert result.risk_cash != 999.0


def test_gft_daily_limit_blocks_order_even_when_primary_would_be_safe():
    dispatcher = _dispatcher(
        PropConfig(
            risk_per_trade_pct=0.30,
            max_daily_dd_pct=2.5,
            max_total_dd_pct=8.0,
        )
    )
    dispatcher.update_account_state(
        AccountState(
            balance=146000.0,
            equity=146000.0,
            start_balance=150000.0,
            daily_start_balance=150000.0,
        ),
        broker_id="gft_compte1",
    )

    result = asyncio.run(
        dispatcher._place_on_broker(
            "gft_compte1",
            dispatcher.brokers["gft_compte1"],
            _pending(_signal()),
            PriceTick("XAUUSD", 4500.0, 4500.1),
        )
    )

    assert result.success is False
    assert "DD daily" in result.message


def test_order_is_blocked_when_broker_risk_state_is_missing():
    dispatcher = _dispatcher(PropConfig())

    result = asyncio.run(
        dispatcher._place_on_broker(
            "gft_compte1",
            dispatcher.brokers["gft_compte1"],
            _pending(_signal()),
            PriceTick("XAUUSD", 4500.0, 4500.1),
        )
    )

    assert result.success is False
    assert "Etat risque indisponible" in result.message


def test_live_engine_builds_distinct_prop_limits_from_accounts_yaml():
    engine = LiveEngine(
        settings={
            "general": {"risk_percent": 0.45},
            "filters": {},
            "execution": {},
        },
        secrets={},
        instruments={},
        dry_run=False,
    )
    engine._brokers = {
        "ftmo_challenge": _Broker(),
        "gft_compte1": _Broker(),
    }

    dispatcher = engine._make_dispatcher()

    ftmo = dispatcher._guards_by_broker["ftmo_challenge"].prop
    gft = dispatcher._guards_by_broker["gft_compte1"].prop
    assert ftmo.risk_per_trade_pct == pytest.approx(0.45)
    assert ftmo.max_daily_dd_pct == pytest.approx(3.0)
    assert gft.risk_per_trade_pct == pytest.approx(0.30)
    assert gft.max_daily_dd_pct == pytest.approx(2.5)
