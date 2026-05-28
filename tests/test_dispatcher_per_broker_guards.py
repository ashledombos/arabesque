"""Safety regressions for live dispatch across prop-firm accounts.

Before 2026-05-26 the dispatcher carried only FTMO state/config while placing
orders on GFT too, and ``worst_case_budget`` was not enabled in live mode.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from arabesque.broker.base import OrderResult, OrderType, PriceTick, SymbolInfo
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


class _MinXauBroker(_Broker):
    async def get_symbol_info(self, symbol: str):
        return SymbolInfo(
            symbol=symbol,
            broker_symbol=symbol + ".X",
            pip_size=0.01,
            lot_size=100.0,
            min_volume=0.01,
            max_volume=10.0,
            volume_step=0.01,
        )


class _LiveGftBroker(_Broker):
    def __init__(self, quote):
        self.quote = quote
        self.placed = []

    async def get_quote(self, symbol: str):
        return self.quote

    async def place_order(self, order):
        self.placed.append(order)
        return OrderResult(success=True, order_id="P1")


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


def test_min_volume_risk_overshoot_is_blocked_before_order(caplog):
    dispatcher = _dispatcher(
        PropConfig(
            risk_per_trade_pct=0.30,
            max_daily_dd_pct=2.5,
            max_total_dd_pct=8.0,
        )
    )
    dispatcher.brokers["gft_compte1"] = _MinXauBroker()
    dispatcher._rodage_strategies = {"glissade"}
    dispatcher._rodage_multiplier = 0.25
    dispatcher.update_account_state(
        AccountState(
            balance=142110.0,
            equity=142110.0,
            start_balance=150000.0,
            daily_start_balance=142110.0,
        ),
        broker_id="gft_compte1",
    )
    signal = _signal()
    signal.strategy_type = "glissade"
    signal.sl = 4400.0

    with caplog.at_level("WARNING"):
        result = asyncio.run(
            dispatcher._place_on_broker(
                "gft_compte1",
                dispatcher.brokers["gft_compte1"],
                _pending(signal),
                PriceTick("XAUUSD", 4500.0, 4500.1),
            )
        )

    assert result.success is False
    assert "Volume calculé = 0" in result.message
    assert "risk overshoot" in caplog.text


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


def test_live_gft_preflight_blocks_when_rest_quote_unavailable():
    dispatcher = _dispatcher(PropConfig())
    dispatcher.dry_run = False
    broker = _LiveGftBroker(None)
    dispatcher.brokers["gft_compte1"] = broker
    dispatcher.update_account_state(AccountState(), broker_id="gft_compte1")

    result = asyncio.run(
        dispatcher._place_on_broker(
            "gft_compte1", broker, _pending(_signal()),
            PriceTick("XAUUSD", 4500.0, 4500.1),
        )
    )

    assert result.success is False
    assert "quote REST indisponible" in result.message
    assert broker.placed == []


def test_live_gft_preflight_blocks_adverse_price_in_r(tmp_path, monkeypatch):
    reject_log = tmp_path / "broker_guard_rejects.jsonl"
    coherence_log = tmp_path / "gft_quote_coherence.jsonl"
    monkeypatch.setattr(
        "arabesque.execution.order_dispatcher.BROKER_REJECT_LOG_PATH",
        reject_log,
    )
    monkeypatch.setattr(
        "arabesque.execution.order_dispatcher.GFT_QUOTE_COHERENCE_LOG_PATH",
        coherence_log,
    )
    dispatcher = _dispatcher(PropConfig())
    dispatcher.dry_run = False
    # LONG entry target=4500, SL=4490: ask=4503 consumes 0.30R.
    broker = _LiveGftBroker(PriceTick("XAUUSD", 4502.9, 4503.0))
    dispatcher.brokers["gft_compte1"] = broker
    dispatcher.update_account_state(AccountState(), broker_id="gft_compte1")

    result = asyncio.run(
        dispatcher._place_on_broker(
            "gft_compte1", broker, _pending(_signal()),
            PriceTick("XAUUSD", 4500.0, 4500.1),
        )
    )

    assert result.success is False
    assert "dérive défavorable 0.30R" in result.message
    assert broker.placed == []
    row = json.loads(reject_log.read_text().splitlines()[0])
    assert row["reason"] == "gft_adverse_entry_slippage"
    assert row["adverse_r"] == pytest.approx(0.30)
    coherence = json.loads(coherence_log.read_text().splitlines()[0])
    assert coherence["event"] == "gft_quote_coherence_check"
    assert coherence["decision"] == "block"
    assert coherence["reason"] == "gft_adverse_entry_slippage"
    assert coherence["reference_trade_price"] == pytest.approx(4500.1)
    assert coherence["gft_trade_price"] == pytest.approx(4503.0)


def test_live_gft_preflight_logs_quote_coherence_when_allowed(tmp_path, monkeypatch):
    coherence_log = tmp_path / "gft_quote_coherence.jsonl"
    monkeypatch.setattr(
        "arabesque.execution.order_dispatcher.GFT_QUOTE_COHERENCE_LOG_PATH",
        coherence_log,
    )
    dispatcher = _dispatcher(PropConfig())
    dispatcher.dry_run = False
    broker = _LiveGftBroker(PriceTick("XAUUSD", 4499.9, 4500.05))
    dispatcher.brokers["gft_compte1"] = broker
    dispatcher.update_account_state(AccountState(), broker_id="gft_compte1")

    result = asyncio.run(
        dispatcher._place_on_broker(
            "gft_compte1", broker, _pending(_signal()),
            PriceTick("XAUUSD", 4500.0, 4500.1),
        )
    )

    assert result.success is True
    row = json.loads(coherence_log.read_text().splitlines()[0])
    assert row["decision"] == "allow"
    assert row["adverse_r_vs_reference"] == 0.0
    assert row["offset_price"] == pytest.approx(-0.05)


def test_execution_quarantine_blocks_new_order_without_touching_broker():
    dispatcher = _dispatcher(PropConfig())
    dispatcher.dry_run = False
    broker = _LiveGftBroker(PriceTick("XAUUSD", 4500.0, 4500.1))
    dispatcher.brokers["gft_compte1"] = broker
    dispatcher.block_broker_entries("gft_compte1", "protection absente")

    result = asyncio.run(
        dispatcher._place_on_broker(
            "gft_compte1", broker, _pending(_signal()),
            PriceTick("XAUUSD", 4500.0, 4500.1),
        )
    )

    assert result.success is False
    assert "Quarantaine exécution" in result.message
    assert broker.placed == []


def test_signal_is_blocked_when_primary_risk_state_has_been_invalidated():
    dispatcher = _dispatcher(PropConfig())
    dispatcher.update_account_state(
        AccountState(
            balance=100_000.0,
            equity=100_000.0,
            start_balance=100_000.0,
            daily_start_balance=100_000.0,
        ),
        broker_id="ftmo_challenge",
    )
    dispatcher.invalidate_account_state("ftmo_challenge")

    accepted = asyncio.run(dispatcher.receive_signal(_signal()))

    assert accepted is False
    assert "ftmo_challenge" not in dispatcher._account_states_by_broker


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
