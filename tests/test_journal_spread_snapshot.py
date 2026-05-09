"""Test que record_entry et record_exit persistent les snapshots spread broker
+ exit_price_source dans le journal JSONL.

Garantit qu'on pourra distinguer en analyse post-trade :
- coût d'exécution (spread ask-bid au moment du fill)
- vrai fill broker vs estimation (real_fill / estimated / reconciled)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from arabesque.execution.live_monitor import LiveMonitor


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    journal = tmp_path / "trade_journal.jsonl"
    monkeypatch.setattr(
        "arabesque.execution.live_monitor.TRADE_JOURNAL_PATH",
        journal,
    )
    m = LiveMonitor.__new__(LiveMonitor)
    m._open_trades = {}
    m._closed_trades = []
    m._max_closed_history = 1000
    m._perf = {}
    m._perf_by_inst = {}
    m._daily_pnl = {}

    from arabesque.execution.live_monitor import ProtectionLevel, MonitorConfig
    m._protection_level = ProtectionLevel.NORMAL
    m._cfg = MonitorConfig()

    m._notification_channels = []
    m._consecutive_loss_threshold = 5
    m._drift_threshold_pp = 15
    m._best_day_threshold_pct = 4.0
    m._journal_path = journal
    return m


def _make_signal(side="LONG", instrument="XAUUSD", sl=4400.0):
    sig = MagicMock()
    sig.signal_id = "abc123def456"
    sig.instrument = instrument
    sig.strategy_type = "extension"
    sig.side = MagicMock()
    sig.side.value = side
    sig.sl = sl
    sig.tp_indicative = 4500.0
    return sig


def _read_journal(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_record_entry_persists_broker_bid_ask(monitor, tmp_path):
    monitor.record_entry(
        signal=_make_signal(),
        broker_id="ftmo_challenge",
        position_id="42",
        entry_price=4452.10,
        volume=0.5,
        risk_cash=400.0,
        broker_bid=4452.05,
        broker_ask=4452.15,
    )

    rows = _read_journal(tmp_path / "trade_journal.jsonl")
    assert len(rows) == 1
    r = rows[0]
    assert r["event"] == "entry"
    assert r["broker_bid_at_entry"] == 4452.05
    assert r["broker_ask_at_entry"] == 4452.15
    assert r["spread_at_entry"] == pytest.approx(0.10, abs=1e-6)


def test_record_exit_persists_source_real_fill(monitor, tmp_path):
    # entry first
    monitor.record_entry(
        signal=_make_signal(),
        broker_id="ftmo_challenge",
        position_id="42",
        entry_price=4452.10,
        volume=0.5,
        risk_cash=400.0,
        broker_bid=4452.05,
        broker_ask=4452.15,
    )
    monitor.record_exit(
        broker_id="ftmo_challenge",
        position_id="42",
        exit_price=4400.50,
        exit_reason="stop_loss",
        mfe_r=0.4,
        be_set=False,
        trailing_tier=0,
        broker_bid=4400.40,
        broker_ask=4400.60,
        exit_price_source="real_fill",
    )

    rows = _read_journal(tmp_path / "trade_journal.jsonl")
    exits = [r for r in rows if r["event"] == "exit"]
    assert len(exits) == 1
    e = exits[0]
    assert e["exit_price_source"] == "real_fill"
    assert e["broker_bid_at_exit"] == 4400.40
    assert e["broker_ask_at_exit"] == 4400.60
    assert e["spread_at_exit"] == pytest.approx(0.20, abs=1e-6)


def test_record_exit_estimated_when_no_quote(monitor, tmp_path):
    monitor.record_entry(
        signal=_make_signal(),
        broker_id="gft_compte1",
        position_id="100",
        entry_price=4452.10,
        volume=0.5,
        risk_cash=400.0,
    )
    monitor.record_exit(
        broker_id="gft_compte1",
        position_id="100",
        exit_price=4400.50,
        exit_reason="stop_loss",
        mfe_r=0.0,
        be_set=False,
        trailing_tier=0,
        exit_price_source="estimated",
    )

    rows = _read_journal(tmp_path / "trade_journal.jsonl")
    exits = [r for r in rows if r["event"] == "exit"]
    e = exits[0]
    assert e["exit_price_source"] == "estimated"
    # Quand on n'a pas de quote, broker_bid/ask = 0 et spread = 0
    assert e["broker_bid_at_exit"] == 0.0
    assert e["broker_ask_at_exit"] == 0.0
    assert e["spread_at_exit"] == 0.0
