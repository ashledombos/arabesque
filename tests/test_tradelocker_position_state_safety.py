"""Safety regressions for TradeLocker position-state reads.

Incident 2026-05-26: an HTTP 429 from ``get_all_positions()`` was converted
to ``[]``.  The live monitor therefore declared AUDJPY closed while it was
still open broker-side.  Its corroboration then selected the opening fill
instead of the closing execution from TradeLocker's linked-order history.
"""
from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from arabesque.broker.tradelocker import TradeLockerBroker
from arabesque.broker.base import OrderType
from arabesque.core.models import Side
from arabesque.execution.position_monitor import LivePositionMonitor


class _Api:
    def __init__(
        self, *, positions=None, orders=None, positions_error=None,
        orders_error=None, resolved_position_id=None,
    ):
        self.positions = positions
        self.orders = orders
        self.positions_error = positions_error
        self.orders_error = orders_error
        self.resolved_position_id = resolved_position_id

    def get_all_positions(self):
        if self.positions_error:
            raise self.positions_error
        return self.positions

    def get_all_orders(self, history=True):
        if self.orders_error:
            raise self.orders_error
        return self.orders

    def get_position_id_from_order_id(self, order_id):
        return self.resolved_position_id


def _broker(api: _Api) -> TradeLockerBroker:
    broker = TradeLockerBroker.__new__(TradeLockerBroker)
    broker._api = api
    broker._instruments_reverse_map = {}
    broker.broker_id = "gft_compte1"
    return broker


def test_get_positions_error_is_unknown_not_empty():
    broker = _broker(_Api(positions_error=RuntimeError("HTTP 429 Too Many Requests")))

    with pytest.raises(ConnectionError, match="get_positions failed"):
        asyncio.run(broker.get_positions())


def test_get_pending_orders_error_is_unknown_not_empty():
    broker = _broker(_Api(orders_error=RuntimeError("HTTP 429 Too Many Requests")))

    with pytest.raises(ConnectionError, match="get_pending_orders failed"):
        asyncio.run(broker.get_pending_orders())


def test_reconcile_preserves_tracked_position_when_tradelocker_query_fails():
    broker = _broker(_Api(positions_error=RuntimeError("HTTP 429 Too Many Requests")))
    closed = []
    monitor = LivePositionMonitor(
        brokers={"gft_compte1": broker},
        on_position_closed=lambda **kwargs: closed.append(kwargs),
    )
    monitor.register_position(
        broker_id="gft_compte1",
        position_id="42",
        symbol="AUDJPY",
        side=Side.LONG,
        entry=114.205,
        sl=114.016,
        tp=114.578,
        volume=0.13,
    )
    monitor._positions["gft_compte1:42"].registered_at = 0.0

    asyncio.run(monitor.reconcile())

    assert "gft_compte1:42" in monitor._positions
    assert closed == []


def test_pending_order_can_resolve_distinct_position_id():
    broker = _broker(_Api(resolved_position_id=987))

    resolved = asyncio.run(broker.resolve_position_id_from_order_id("123"))

    assert resolved == "987"


def test_open_position_protection_is_read_from_attached_orders():
    orders = pd.DataFrame([
        {
            "positionId": 42, "side": "sell", "type": "stop",
            "status": "Working", "stopPrice": 114.016, "price": 0.0,
        },
        {
            "positionId": 42, "side": "sell", "type": "limit",
            "status": "Working", "price": 114.578,
        },
    ])
    broker = _broker(_Api(orders=orders))

    protection = asyncio.run(broker.get_position_protection("42"))

    assert protection == pytest.approx((114.016, 114.578))


def test_pending_attached_stop_preserves_stop_price_and_position_link():
    orders = pd.DataFrame([
        {
            "id": 11, "positionId": 42, "tradableInstrumentId": 7,
            "side": "buy", "type": "stop", "status": "Working",
            "stopPrice": 4458.85, "price": 0.0, "qty": 0.02,
        },
    ])
    broker = _broker(_Api(orders=orders))
    broker._instruments_reverse_map = {7: "XAUUSD"}

    pending = asyncio.run(broker.get_pending_orders())

    assert len(pending) == 1
    assert pending[0].order_type is OrderType.STOP
    assert pending[0].entry_price == pytest.approx(4458.85)
    assert pending[0].raw_data["position_id"] == "42"


def test_get_closed_detail_requires_a_closing_execution():
    orders = pd.DataFrame([{
        "id": 10,
        "positionId": 42,
        "side": "buy",
        "status": "Filled",
        "filledQty": 0.13,
        "avgPrice": 114.205,
        "price": 114.203,
        "createdDate": 1,
    }])
    broker = _broker(_Api(orders=orders))

    assert asyncio.run(broker.get_closed_position_detail("42")) is None


def test_get_closed_detail_selects_newest_opposite_filled_order():
    # Mirrors the row ordering observed for AUDJPY on 2026-05-26:
    # TradeLocker returns the closing execution before the opening fill.
    orders = pd.DataFrame([
        {
            "id": 13, "positionId": 42, "side": "sell", "type": "market",
            "status": "Filled", "filledQty": 0.13, "avgPrice": 114.167,
            "price": 114.167, "createdDate": 4,
        },
        {
            "id": 12, "positionId": 42, "side": "sell", "type": "limit",
            "status": "Cancelled", "filledQty": 0.0, "avgPrice": 0.0,
            "price": 114.578, "createdDate": 3,
        },
        {
            "id": 11, "positionId": 42, "side": "sell", "type": "stop",
            "status": "Cancelled", "filledQty": 0.0, "avgPrice": 0.0,
            "price": 114.016, "createdDate": 2,
        },
        {
            "id": 10, "positionId": 42, "side": "buy", "type": "stop",
            "status": "Filled", "filledQty": 0.13, "avgPrice": 114.205,
            "price": 114.203, "createdDate": 1,
        },
    ])
    broker = _broker(_Api(orders=orders))

    detail = asyncio.run(broker.get_closed_position_detail("42"))

    assert detail is not None
    assert detail["exit_price"] == pytest.approx(114.167)


def test_get_closed_detail_reports_real_commission_when_present():
    """La commission renvoyée par TradeLocker doit être journalisée telle quelle."""
    orders = pd.DataFrame([
        {"id": 10, "positionId": 42, "side": "buy", "status": "Filled",
         "filledQty": 0.1, "avgPrice": 100.0, "price": 100.0, "createdDate": 1},
        {"id": 11, "positionId": 42, "side": "sell", "status": "Filled",
         "filledQty": 0.1, "avgPrice": 105.0, "price": 105.0, "createdDate": 2,
         "grossPl": 20.0, "commission": -1.5, "swap": -0.3},
    ])
    detail = asyncio.run(_broker(_Api(orders=orders)).get_closed_position_detail("42"))
    assert detail is not None
    assert detail["commission"] == -1.5
    assert detail["swap"] == -0.3
    assert detail["gross_profit"] == 20.0


def test_get_closed_detail_commission_is_none_when_field_absent():
    """Champ commission absent de la réponse → None (pas 0), pour distinguer
    un vrai 0 d'un trou de données (régression coûts GFT, audit 2026-06-23)."""
    orders = pd.DataFrame([
        {"id": 10, "positionId": 42, "side": "buy", "status": "Filled",
         "filledQty": 0.1, "avgPrice": 100.0, "price": 100.0, "createdDate": 1},
        {"id": 11, "positionId": 42, "side": "sell", "status": "Filled",
         "filledQty": 0.1, "avgPrice": 105.0, "price": 105.0, "createdDate": 2},
    ])
    detail = asyncio.run(_broker(_Api(orders=orders)).get_closed_position_detail("42"))
    assert detail is not None
    assert detail["commission"] is None   # pas 0.0
    assert detail["gross_profit"] is None
    assert detail["swap"] is None
