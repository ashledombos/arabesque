"""Safety coverage for risk-state refresh in the live engine.

Accepted broker pending orders are already exposures: they can fill without a
new signal decision. Broker/journal read failures must never be interpreted as
zero risk while orders remain enabled.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from arabesque.execution.live import LiveEngine


class _Dispatcher:
    def __init__(self):
        self.states = {}
        self.invalidated = []

    def update_account_state(self, state, broker_id=""):
        self.states[broker_id] = state

    def invalidate_account_state(self, broker_id):
        self.states.pop(broker_id, None)
        self.invalidated.append(broker_id)


class _Monitor:
    def __init__(self, open_trades=None):
        self._open_trades = open_trades or []

    def get_open_trades(self):
        return self._open_trades

    async def check_protection(self, **kwargs):
        return None

    def record_equity_snapshot(self, **kwargs):
        return None


def _engine(broker, monitor=None) -> LiveEngine:
    engine = LiveEngine.__new__(LiveEngine)
    engine._brokers = {"gft_compte1": broker}
    engine._dispatcher = _Dispatcher()
    engine._position_monitor = None
    engine._live_monitor = monitor
    engine._pending_fills = {}
    engine._broker_initial_balance = {"gft_compte1": 150_000.0}
    engine._broker_daily_start_balance = {"gft_compte1": 142_000.0}
    engine._broker_daily_start_date = {"gft_compte1": "2026-05-26"}
    engine._initial_balance = None
    engine._daily_start_balance = None
    engine._daily_start_date = None
    return engine


class _Broker:
    def __init__(self, positions=None, pending=None, error=None):
        self.positions = positions or []
        self.pending = pending or []
        self.error = error

    async def get_positions(self):
        if self.error:
            raise self.error
        return self.positions

    async def get_pending_orders(self):
        if self.error:
            raise self.error
        return self.pending

    async def get_account_info(self):
        return SimpleNamespace(
            balance=142_000.0,
            equity=142_000.0,
            margin_free=142_000.0,
            currency="USD",
        )


def test_refresh_reserves_tracked_and_pending_risk_and_daily_slot(tmp_path, monkeypatch):
    journal = tmp_path / "trade_journal.jsonl"
    journal.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-05-26T08:00:00+00:00",
                        "event": "pending_order",
                        "broker_id": "gft_compte1",
                        "trade_id": "pending-1",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-05-26T08:02:00+00:00",
                        "event": "entry",
                        "broker_id": "gft_compte1",
                        "trade_id": "pending-1",
                    }
                ),
            ]
        )
        + "\n"
    )
    monkeypatch.setattr("arabesque.execution.live.TRADE_JOURNAL_PATH", journal)
    broker_position = SimpleNamespace(symbol="AUDJPY", position_id="pos-1")
    engine = _engine(
        _Broker([broker_position]),
        _Monitor(
            [
                {
                    "broker_id": "gft_compte1",
                    "position_id": "pos-1",
                    "risk_cash": 31.25,
                }
            ]
        ),
    )
    engine._pending_fills = {
        "gft_compte1:pending-1": {
            "broker_id": "gft_compte1",
            "order_id": "pending-1",
            "instrument": "XAUUSD",
            "risk_cash": 17.50,
        }
    }
    broker_pending = SimpleNamespace(order_id="pending-1")
    engine._brokers["gft_compte1"].pending = [broker_pending]

    asyncio.run(engine._refresh_account_state())

    state = engine._dispatcher.states["gft_compte1"]
    assert state.open_positions == 2
    assert set(state.open_instruments) == {"AUDJPY", "XAUUSD"}
    assert state.open_risk_cash == pytest.approx(48.75)
    assert state.daily_trades == 1


def test_refresh_uses_conservative_risk_for_untracked_broker_position(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "arabesque.execution.live.TRADE_JOURNAL_PATH",
        tmp_path / "missing.jsonl",
    )
    engine = _engine(
        _Broker([SimpleNamespace(symbol="XAUUSD", position_id="orphan")]),
        _Monitor(),
    )

    asyncio.run(engine._refresh_account_state())

    assert engine._dispatcher.states["gft_compte1"].open_risk_cash == pytest.approx(400.0)


def test_stale_tracked_position_does_not_mask_unknown_broker_risk(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "arabesque.execution.live.TRADE_JOURNAL_PATH",
        tmp_path / "missing.jsonl",
    )
    engine = _engine(
        _Broker([SimpleNamespace(symbol="XAUUSD", position_id="new-orphan")]),
        _Monitor(
            [
                {
                    "broker_id": "gft_compte1",
                    "position_id": "stale-old",
                    "risk_cash": 4.82,
                }
            ]
        ),
    )

    asyncio.run(engine._refresh_account_state())

    assert engine._dispatcher.states["gft_compte1"].open_risk_cash == pytest.approx(400.0)


def test_positions_failure_invalidates_state_instead_of_reporting_zero_risk(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "arabesque.execution.live.TRADE_JOURNAL_PATH",
        tmp_path / "missing.jsonl",
    )
    engine = _engine(_Broker(error=ConnectionError("429 unavailable")))

    asyncio.run(engine._refresh_account_state())

    assert engine._dispatcher.invalidated == ["gft_compte1"]
    assert "gft_compte1" not in engine._dispatcher.states


def test_unknown_broker_pending_invalidates_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "arabesque.execution.live.TRADE_JOURNAL_PATH",
        tmp_path / "missing.jsonl",
    )
    engine = _engine(
        _Broker(pending=[SimpleNamespace(order_id="not-in-local-state")])
    )

    asyncio.run(engine._refresh_account_state())

    assert engine._dispatcher.invalidated == ["gft_compte1"]
    assert "gft_compte1" not in engine._dispatcher.states


def test_daily_journal_read_failure_invalidates_state(tmp_path, monkeypatch):
    monkeypatch.setattr("arabesque.execution.live.TRADE_JOURNAL_PATH", tmp_path)
    engine = _engine(_Broker())

    asyncio.run(engine._refresh_account_state())

    assert engine._dispatcher.invalidated == ["gft_compte1"]
    assert "gft_compte1" not in engine._dispatcher.states
