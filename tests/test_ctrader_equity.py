from __future__ import annotations

import pytest

from arabesque.broker.base import OrderSide, Position, PriceTick, SymbolInfo
from arabesque.broker.ctrader import CTraderBroker


def _broker_stub() -> CTraderBroker:
    broker = CTraderBroker.__new__(CTraderBroker)
    broker._position_symbol_ids = {}
    broker._price_ticks = {}
    broker._symbols = {}
    broker._symbol_id_to_unified = {}
    broker._instruments_config = {}
    return broker


def test_compute_equity_uses_yaml_pip_value_for_jpy_cross():
    """AUDJPY floating PnL is JPY-denominated, not USD-denominated.

    Regression for 2026-05-29: the old raw formula treated roughly -1,680 JPY
    as -1,680 USD and put FTMO in false DANGER. The calibrated instrument pip
    value keeps equity aligned with the same risk model used for sizing.
    """
    broker = _broker_stub()
    broker._position_symbol_ids = {"P1": 101}
    broker._symbol_id_to_unified = {101: "AUDJPY"}
    broker._symbols = {
        101: SymbolInfo(
            symbol="AUDJPY",
            broker_symbol="AUDJPY",
            pip_size=0.01,
            lot_size=100000,
        )
    }
    broker._instruments_config = {
        "AUDJPY": {"pip_size": 0.01, "pip_value_per_lot": 6.33}
    }
    broker._price_ticks = {
        101: PriceTick(symbol="AUDJPY", bid=114.445, ask=114.455, timestamp=1.0)
    }
    broker._positions = [
        Position(
            position_id="P1",
            symbol="AUDJPY",
            side=OrderSide.BUY,
            volume=0.21,
            entry_price=114.525,
            swap=0.0,
            commission=0.0,
        )
    ]

    equity = broker._compute_equity(93_185.45)

    # (114.445 - 114.525) / 0.01 * 6.33 * 0.21 = -10.6344 USD.
    assert equity == pytest.approx(93_174.8156, abs=1e-4)


def test_compute_equity_fallback_converts_quote_currency_when_yaml_missing():
    broker = _broker_stub()
    broker._position_symbol_ids = {"P1": 101}
    broker._symbol_id_to_unified = {101: "AUDJPY", 202: "USDJPY"}
    broker._symbols = {
        101: SymbolInfo(symbol="AUDJPY", broker_symbol="AUDJPY", lot_size=100000),
        202: SymbolInfo(symbol="USDJPY", broker_symbol="USDJPY", lot_size=100000),
    }
    broker._price_ticks = {
        101: PriceTick(symbol="AUDJPY", bid=114.445, ask=114.455, timestamp=1.0),
        202: PriceTick(symbol="USDJPY", bid=156.90, ask=156.92, timestamp=1.0),
    }
    broker._positions = [
        Position(
            position_id="P1",
            symbol="AUDJPY",
            side=OrderSide.BUY,
            volume=0.21,
            entry_price=114.525,
        )
    ]

    equity = broker._compute_equity(93_185.45)

    raw_jpy = (114.445 - 114.525) * 0.21 * 100000
    assert equity == pytest.approx(93_185.45 + raw_jpy / 156.91, abs=1e-4)
