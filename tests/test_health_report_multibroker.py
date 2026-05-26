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


def test_refresh_records_snapshot_after_new_protection_level():
    """A snapshot must contain the level computed from the same account read."""
    engine = LiveEngine.__new__(LiveEngine)
    engine._brokers = {}
    engine._dispatcher = SimpleNamespace(update_account_state=lambda state: None)
    engine._accounts_config = {}
    engine._position_monitor = None
    engine._broker_initial_balance = {"gft_compte1": 150_000.0}
    engine._broker_daily_start_balance = {"gft_compte1": 150_000.0}
    engine._broker_daily_start_date = {"gft_compte1": "2026-05-26"}

    class _Broker:
        async def get_positions(self):
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

    engine._brokers = {"gft_compte1": _Broker()}
    engine._live_monitor = _Monitor()

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
