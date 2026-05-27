"""Regression tests for cross-broker health telemetry.

Incident 2026-05-26: effective risk was DANGER for FTMO and GFT, while the
health event reported NORMAL and compared GFT equity with FTMO equity.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from arabesque.execution.live import LiveEngine
from arabesque.execution.live_monitor import (
    LiveMonitor,
    MonitorConfig,
    ProtectionLevel,
    StrategyPerf,
)


def _monitor(tmp_path, monkeypatch) -> LiveMonitor:
    monkeypatch.setattr(
        "arabesque.execution.live_monitor.TRADE_JOURNAL_PATH",
        tmp_path / "trade_journal.jsonl",
    )
    monkeypatch.setattr(
        "arabesque.execution.live_monitor.EQUITY_SNAPSHOT_PATH",
        tmp_path / "equity_snapshots.jsonl",
    )
    return LiveMonitor(MonitorConfig(equity_snapshot_interval_s=0))


def test_health_report_uses_effective_level_and_groups_equity_by_broker(
    tmp_path, monkeypatch
):
    monitor = _monitor(tmp_path, monkeypatch)
    monitor._protection_per_broker = {
        "ftmo_challenge": ProtectionLevel.DANGER,
        "gft_compte1": ProtectionLevel.DANGER,
    }
    monitor._equity_history = [
        {"ts": "t1", "broker_id": "ftmo_challenge", "equity": 93298.21},
        {"ts": "t2", "broker_id": "gft_compte1", "equity": 142089.38},
        {"ts": "t3", "broker_id": "gft_compte1", "equity": 142105.25},
    ]

    report = monitor.emit_health_report()

    assert report["protection_level"] == "danger"
    assert report["protection_by_broker"] == {
        "ftmo_challenge": "danger",
        "gft_compte1": "danger",
    }
    assert monitor.protection_level == ProtectionLevel.DANGER
    assert "equity_latest" not in report
    assert "equity_24h_ago" not in report
    assert report["equity_by_broker"]["ftmo_challenge"] == {
        "latest": 93298.21,
        "latest_ts": "t1",
        "oldest_observed": 93298.21,
        "oldest_observed_ts": "t1",
        "snapshots": 1,
    }
    assert report["equity_by_broker"]["gft_compte1"]["oldest_observed"] == 142089.38
    assert report["equity_by_broker"]["gft_compte1"]["latest"] == 142105.25
    assert monitor.get_stats()["protection_level"] == "danger"

    event = json.loads((tmp_path / "trade_journal.jsonl").read_text().strip())
    assert event["protection_level"] == "danger"
    assert event["equity_by_broker"] == report["equity_by_broker"]


def test_restored_open_trade_is_reported_and_can_be_closed_without_duplicate_entry(
    tmp_path, monkeypatch
):
    monitor = _monitor(tmp_path, monkeypatch)
    entry = {
        "event": "entry",
        "ts": "2026-05-27T14:00:27+00:00",
        "trade_id": "signal-1",
        "instrument": "XAUUSD",
        "strategy": "extension",
        "side": "SHORT",
        "entry_price": 4426.55,
        "sl": 4458.85,
        "tp": 4364.54,
        "volume": 0.02,
        "risk_cash": 72.75,
        "broker_id": "gft_compte1",
        "position_id": "POSITION-42",
    }

    assert monitor.restore_open_trade(entry) is True
    assert monitor.emit_health_report()["open_trades"] == 1
    assert monitor.record_exit(
        broker_id="gft_compte1",
        position_id="POSITION-42",
        exit_price=4458.85,
        exit_reason="stop_loss",
    ) is not None

    records = [
        json.loads(line)
        for line in (tmp_path / "trade_journal.jsonl").read_text().splitlines()
    ]
    assert [r["event"] for r in records] == ["health_report", "exit"]


def test_refresh_records_snapshot_after_new_protection_level():
    """A snapshot must contain the level computed from the same account read."""
    engine = LiveEngine.__new__(LiveEngine)
    engine._brokers = {}
    engine._dispatcher = SimpleNamespace(
        update_account_state=lambda state, broker_id="": None,
        invalidate_account_state=lambda broker_id: None,
    )
    engine._accounts_config = {}
    engine._position_monitor = None
    engine._broker_initial_balance = {"gft_compte1": 150_000.0}
    engine._broker_daily_start_balance = {"gft_compte1": 150_000.0}
    engine._broker_daily_start_date = {"gft_compte1": "2026-05-26"}

    class _Broker:
        async def get_positions(self):
            return []

        async def get_pending_orders(self):
            return []

        async def get_account_info(self):
            return SimpleNamespace(
                balance=142_000.0,
                equity=142_000.0,
                margin_free=142_000.0,
                currency="USD",
            )

    calls: list[str] = []

    class _Monitor:
        async def check_protection(self, **kwargs):
            calls.append("protection")

        def record_equity_snapshot(self, **kwargs):
            calls.append("snapshot")

        def get_open_trades(self):
            return []

    engine._brokers = {"gft_compte1": _Broker()}
    engine._live_monitor = _Monitor()
    engine._pending_fills = {}

    asyncio.run(engine._refresh_account_state())

    assert calls == ["protection", "snapshot"]


def test_disabled_strategy_losing_streak_does_not_pin_live_guard(tmp_path, monkeypatch):
    monitor = _monitor(tmp_path, monkeypatch)
    monitor._cfg.consecutive_loss_strategies = ("extension", "glissade", "fouette")
    monitor._perf = {
        "cabriole": StrategyPerf(strategy="cabriole", consecutive_losses=8),
        "glissade": StrategyPerf(strategy="glissade", consecutive_losses=5),
        "extension": StrategyPerf(strategy="extension", consecutive_losses=0),
    }

    level = monitor._evaluate_protection_level(
        daily_dd_pct=0.0,
        total_dd_pct=-6.70,
        equity=93298.21,
        free_margin=93298.21,
    )

    assert level == ProtectionLevel.CAUTION


def test_broker_guard_does_not_double_count_mirrored_losses(tmp_path, monkeypatch):
    monitor = _monitor(tmp_path, monkeypatch)
    monitor._cfg.consecutive_loss_strategies = ("extension",)
    monitor._perf = {
        "extension": StrategyPerf(strategy="extension", consecutive_losses=6),
    }
    monitor._perf_by_broker_strategy = {
        ("ftmo_challenge", "extension"): StrategyPerf(
            strategy="extension", consecutive_losses=3
        ),
        ("gft_compte1", "extension"): StrategyPerf(
            strategy="extension", consecutive_losses=3
        ),
    }

    ftmo_level = monitor._evaluate_protection_level(
        daily_dd_pct=0.0,
        total_dd_pct=-6.70,
        equity=93298.21,
        free_margin=93298.21,
        broker_id="ftmo_challenge",
    )
    global_level = monitor._evaluate_protection_level(
        daily_dd_pct=0.0,
        total_dd_pct=-6.70,
        equity=93298.21,
        free_margin=93298.21,
    )

    assert ftmo_level == ProtectionLevel.NORMAL
    assert global_level == ProtectionLevel.CAUTION


def test_broker_guard_triggers_only_from_that_broker_streak(tmp_path, monkeypatch):
    monitor = _monitor(tmp_path, monkeypatch)
    monitor._cfg.consecutive_loss_strategies = ("extension",)
    monitor._perf_by_broker_strategy = {
        ("ftmo_challenge", "extension"): StrategyPerf(
            strategy="extension", consecutive_losses=5
        ),
        ("gft_compte1", "extension"): StrategyPerf(
            strategy="extension", consecutive_losses=2
        ),
    }

    assert monitor._evaluate_protection_level(
        0.0, -6.70, 93298.21, 93298.21, broker_id="ftmo_challenge"
    ) == ProtectionLevel.CAUTION
    assert monitor._evaluate_protection_level(
        0.0, -5.26, 142105.25, 142105.25, broker_id="gft_compte1"
    ) == ProtectionLevel.NORMAL


def test_journal_rebuilds_broker_streaks_without_mirror_double_count(
    tmp_path, monkeypatch
):
    journal = tmp_path / "trade_journal.jsonl"
    rows = [
        {"event": "exit", "trade_id": "a", "broker_id": "ftmo_challenge",
         "strategy": "extension", "result_r": -1.0},
        {"event": "exit", "trade_id": "a", "broker_id": "gft_compte1",
         "strategy": "extension", "result_r": -1.0},
        {"event": "exit", "trade_id": "b", "broker_id": "ftmo_challenge",
         "strategy": "extension", "result_r": -1.0},
        {"event": "exit", "trade_id": "b", "broker_id": "gft_compte1",
         "strategy": "extension", "result_r": 1.0},
    ]
    journal.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    monkeypatch.setattr(
        "arabesque.execution.live_monitor.TRADE_JOURNAL_PATH", journal
    )
    monkeypatch.setattr(
        "arabesque.execution.live_monitor.EQUITY_SNAPSHOT_PATH",
        tmp_path / "equity_snapshots.jsonl",
    )

    monitor = LiveMonitor(MonitorConfig(equity_snapshot_interval_s=0))

    ftmo = monitor._perf_by_broker_strategy[("ftmo_challenge", "extension")]
    gft = monitor._perf_by_broker_strategy[("gft_compte1", "extension")]
    assert ftmo.n_trades == 2
    assert ftmo.consecutive_losses == 2
    assert gft.n_trades == 2
    assert gft.consecutive_losses == 0
    # Existing global reporting remains signal-level and is not changed here.
    assert monitor._perf["extension"].n_trades == 2


def test_engine_configures_consecutive_loss_scope_from_active_strategies(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "arabesque.execution.live_monitor.TRADE_JOURNAL_PATH",
        tmp_path / "trade_journal.jsonl",
    )
    monkeypatch.setattr(
        "arabesque.execution.live_monitor.EQUITY_SNAPSHOT_PATH",
        tmp_path / "equity_snapshots.jsonl",
    )
    engine = LiveEngine.__new__(LiveEngine)
    engine.secrets = {}
    engine._brokers = {}
    engine.settings = {
        "strategy": {"type": "extension"},
        "strategy_assignments": {
            "glissade": {"timeframe": "H1"},
            "fouette": {"timeframe": "M1"},
            # No cabriole entry: it is disabled and excluded from the guard.
        },
    }

    monitor = engine._make_live_monitor()

    assert monitor._cfg.consecutive_loss_strategies == (
        "extension",
        "fouette",
        "glissade",
    )
