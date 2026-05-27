from __future__ import annotations

import json

from scripts.audit_sizing_distortion import (
    SymbolConstraints,
    analyze_entry,
    load_phase4_entries,
    summarize,
)


def _entry(**overrides):
    row = {
        "event": "entry",
        "ts": "2026-05-19T10:00:00+00:00",
        "trade_id": "tid",
        "broker_id": "gft_compte1",
        "strategy": "glissade",
        "instrument": "XAUUSD",
        "entry_price": 100.0,
        "sl": 99.0,
        "risk_cash": 80.0,
        "volume": 0.01,
        "spread_at_entry": 0.12,
        "protection_level": "caution",
    }
    row.update(overrides)
    return row


def test_analyze_flags_min_volume_under_risk_and_spread():
    row = analyze_entry(
        _entry(),
        SymbolConstraints(lot_size=100.0, min_volume=0.01, volume_step=0.01, pip_size=0.01),
        {"pip_size": 0.01, "pip_value_per_lot": 1.0},
    )

    assert row.actual_risk_cash == 1.0
    assert row.actual_to_requested == 0.013
    assert row.at_min_volume is True
    assert "at_min_volume" in row.flags
    assert "actual_risk_lt_50pct_target" in row.flags
    assert "spread_entry_gt_0.10R" in row.flags
    assert "risk_cash_lt_10" in row.flags


def test_cross_currency_does_not_invent_actual_cash_conversion():
    row = analyze_entry(
        _entry(instrument="CHFJPY", risk_cash=31.0, spread_at_entry=0.01),
        SymbolConstraints(lot_size=100000.0, min_volume=0.01, volume_step=0.01, pip_size=0.01),
        {"pip_size": 0.01, "pip_value_per_lot": 6.33},
    )

    assert row.actual_risk_cash == 6.33
    assert row.actual_to_requested == 0.204
    assert "actual_risk_lt_50pct_target" in row.flags


def test_load_filters_active_strategies_and_pairs_exit_by_broker(tmp_path):
    path = tmp_path / "journal.jsonl"
    rows = [
        _entry(trade_id="shared", broker_id="ftmo_challenge", strategy="extension"),
        _entry(trade_id="shared", broker_id="gft_compte1", strategy="extension"),
        {"event": "exit", "ts": "2026-05-19T11:00:00+00:00", "trade_id": "shared",
         "broker_id": "gft_compte1", "strategy": "extension", "result_r": -1.0},
        _entry(trade_id="off", strategy="cabriole"),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    entries = load_phase4_entries(path)

    assert len(entries) == 2
    assert entries[0].get("_exit") is None
    assert entries[1]["_exit"]["result_r"] == -1.0


def test_summary_counts_flagged_entries():
    rows = [
        analyze_entry(
            _entry(),
            SymbolConstraints(100.0, 0.01, 0.01, 0.01),
            {"pip_size": 0.01, "pip_value_per_lot": 1.0},
        ),
        analyze_entry(
            _entry(trade_id="ok", volume=1.0, risk_cash=100.0, spread_at_entry=0.01),
            SymbolConstraints(100.0, 0.01, 0.01, 0.01),
            {"pip_size": 0.01, "pip_value_per_lot": 1.0},
        ),
    ]

    summary = summarize(rows)

    assert summary["n_entries"] == 2
    assert summary["n_flagged"] == 1
    assert summary["flags"]["at_min_volume"] == 1
