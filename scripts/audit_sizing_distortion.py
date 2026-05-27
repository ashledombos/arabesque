#!/usr/bin/env python3
"""Audit micro-sizing distortion for the Phase 4 bis live sample.

This script is diagnostic only. It reads entry/exit events from the journal
and, with ``--with-broker-specs``, connects to configured brokers only to read
symbol constraints (lot size, minimum volume and volume step). It never places
or amends an order.

The relevant comparison is executed risk versus the already-modified
``risk_cash`` sent by the dispatcher. A small cash amount is not, by itself,
an R-statistics failure; it becomes problematic when broker granularity or
friction consumes a material part of R.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "logs" / "trade_journal.jsonl"
INSTRUMENTS = ROOT / "config" / "instruments.yaml"
DEFAULT_SINCE = "2026-05-16T08:44:00+00:00"
ACTIVE_STRATEGIES = {"extension", "glissade"}


@dataclass
class SymbolConstraints:
    lot_size: float
    min_volume: float
    volume_step: float
    pip_size: float


@dataclass
class SizingRow:
    ts: str
    broker_id: str
    instrument: str
    strategy: str
    trade_id: str
    protection_level: str
    requested_risk_cash: float
    volume_lots: float
    min_volume: float | None
    volume_step: float | None
    at_min_volume: bool | None
    actual_risk_cash: float | None
    actual_to_requested: float | None
    spread_entry_r: float | None
    spread_exit_r: float | None
    result_r: float | None
    flags: list[str]


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_phase4_entries(
    path: Path = JOURNAL,
    *,
    since: str = DEFAULT_SINCE,
    strategies: set[str] = ACTIVE_STRATEGIES,
) -> list[dict[str, Any]]:
    """Load active-strategy entries and attach matching exits by broker."""
    start = _parse_ts(since)
    entries: list[dict[str, Any]] = []
    exits: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.exists():
        return entries
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = row.get("ts")
        if not ts or _parse_ts(ts) < start:
            continue
        if row.get("strategy") not in strategies:
            continue
        key = (str(row.get("trade_id", "")), str(row.get("broker_id", "")))
        if row.get("event") == "entry":
            entries.append(row)
        elif row.get("event") == "exit":
            exits[key] = row
    for entry in entries:
        key = (str(entry.get("trade_id", "")), str(entry.get("broker_id", "")))
        entry["_exit"] = exits.get(key)
    return entries


def analyze_entry(
    entry: dict[str, Any],
    constraints: SymbolConstraints | None,
    instrument_cfg: dict[str, Any] | None = None,
) -> SizingRow:
    """Compute measurable distortion for one journal entry.

    The reconstruction intentionally mirrors ``OrderDispatcher``: for USD
    quoted symbols and crosses it uses the calibrated YAML pip value, rescaled
    when the broker reports a different pip size. Broker ``contractSize`` is
    not used as economic truth for TradeLocker crypto/metals.
    """
    requested = float(entry.get("risk_cash") or 0.0)
    volume = float(entry.get("volume") or 0.0)
    entry_price = float(entry.get("entry_price") or 0.0)
    sl = float(entry.get("sl") or 0.0)
    risk_distance = abs(entry_price - sl)
    spread_entry = float(entry.get("spread_at_entry") or 0.0)
    exit_row = entry.get("_exit") or {}
    spread_exit = float(exit_row.get("spread_at_exit") or 0.0)
    instrument = str(entry.get("instrument", ""))
    instrument_cfg = instrument_cfg or {}

    spread_entry_r = spread_entry / risk_distance if risk_distance > 0 and spread_entry else None
    spread_exit_r = spread_exit / risk_distance if risk_distance > 0 and spread_exit else None

    at_min: bool | None = None
    actual: float | None = None
    ratio: float | None = None
    if constraints:
        at_min = volume <= constraints.min_volume + max(
            constraints.volume_step, 1e-9
        ) / 2
        yaml_pip_size = float(instrument_cfg.get("pip_size") or 0.0)
        yaml_pip_value = float(instrument_cfg.get("pip_value_per_lot") or 0.0)
        base_ccy = instrument[:3] if len(instrument) >= 6 else ""
        quote_ccy = instrument[-3:] if len(instrument) >= 6 else ""
        supported = quote_ccy == "USD" or (base_ccy != "USD" and quote_ccy != "USD")
        if (
            supported and risk_distance > 0 and yaml_pip_size > 0
            and yaml_pip_value > 0 and constraints.pip_size > 0
        ):
            pip_ratio = constraints.pip_size / yaml_pip_size
            pip_value = yaml_pip_value * pip_ratio
            pips = risk_distance / constraints.pip_size
            actual = pips * pip_value * volume
            if requested > 0:
                ratio = actual / requested

    flags: list[str] = []
    if at_min:
        flags.append("at_min_volume")
    if ratio is not None and ratio < 0.50:
        flags.append("actual_risk_lt_50pct_target")
    if ratio is not None and ratio > 1.25:
        flags.append("actual_risk_gt_125pct_target")
    if spread_entry_r is not None and spread_entry_r > 0.10:
        flags.append("spread_entry_gt_0.10R")
    measured_cash = actual if actual is not None else requested
    if measured_cash > 0 and measured_cash < 10:
        flags.append("risk_cash_lt_10")

    return SizingRow(
        ts=str(entry.get("ts", "")),
        broker_id=str(entry.get("broker_id", "")),
        instrument=instrument,
        strategy=str(entry.get("strategy", "")),
        trade_id=str(entry.get("trade_id", "")),
        protection_level=str(entry.get("protection_level", "unknown")),
        requested_risk_cash=round(requested, 2),
        volume_lots=volume,
        min_volume=constraints.min_volume if constraints else None,
        volume_step=constraints.volume_step if constraints else None,
        at_min_volume=at_min,
        actual_risk_cash=round(actual, 2) if actual is not None else None,
        actual_to_requested=round(ratio, 3) if ratio is not None else None,
        spread_entry_r=round(spread_entry_r, 4) if spread_entry_r is not None else None,
        spread_exit_r=round(spread_exit_r, 4) if spread_exit_r is not None else None,
        result_r=float(exit_row["result_r"]) if "result_r" in exit_row else None,
        flags=flags,
    )


async def fetch_constraints(entries: list[dict[str, Any]]) -> dict[tuple[str, str], SymbolConstraints]:
    """Read symbol specifications from brokers; no execution action is used."""
    sys.path.insert(0, str(ROOT))
    from arabesque.broker.factory import create_all_brokers
    from arabesque.config import load_full_config

    settings, secrets, instruments = load_full_config()
    brokers = create_all_brokers(settings, secrets, instruments)
    targets = {(str(e.get("broker_id")), str(e.get("instrument"))) for e in entries}
    constraints: dict[tuple[str, str], SymbolConstraints] = {}
    for broker_id, broker in brokers.items():
        requested_symbols = sorted(sym for bid, sym in targets if bid == broker_id)
        if not requested_symbols:
            continue
        connected = False
        try:
            connected = bool(await broker.connect())
            if not connected:
                print(f"WARN {broker_id}: connexion impossible, specs indisponibles", file=sys.stderr)
                continue
            for symbol in requested_symbols:
                info = await broker.get_symbol_info(symbol)
                if info:
                    constraints[(broker_id, symbol)] = SymbolConstraints(
                        lot_size=float(info.lot_size),
                        min_volume=float(info.min_volume),
                        volume_step=float(info.volume_step),
                        pip_size=float(info.pip_size),
                    )
        finally:
            if connected:
                await broker.disconnect()
    return constraints


def summarize(rows: list[SizingRow]) -> dict[str, Any]:
    flags = Counter(flag for row in rows for flag in row.flags)
    distorted = [row for row in rows if row.flags]
    evaluable = [row for row in rows if row.actual_to_requested is not None]
    return {
        "n_entries": len(rows),
        "n_with_specs_and_usd_risk": len(evaluable),
        "n_flagged": len(distorted),
        "flag_rate": round(len(distorted) / len(rows), 3) if rows else 0.0,
        "flags": dict(flags),
    }


def render_text(rows: list[SizingRow], summary: dict[str, Any]) -> str:
    lines = [
        "Audit sizing Phase 4 bis - Extension + Glissade",
        f"Entries: {summary['n_entries']} | evaluable risk cash: "
        f"{summary['n_with_specs_and_usd_risk']} | flags: {summary['n_flagged']} "
        f"({summary['flag_rate']:.0%})",
    ]
    if summary["flags"]:
        lines.append("Flags: " + ", ".join(
            f"{key}={value}" for key, value in sorted(summary["flags"].items())
        ))
    lines.append("")
    for row in rows:
        actual = "n/a" if row.actual_risk_cash is None else f"${row.actual_risk_cash:.2f}"
        ratio = "n/a" if row.actual_to_requested is None else f"{row.actual_to_requested:.2f}x"
        spread = "n/a" if row.spread_entry_r is None else f"{row.spread_entry_r:.1%}R"
        markers = ",".join(row.flags) if row.flags else "ok"
        lines.append(
            f"{row.ts[:10]} {row.broker_id:15} {row.strategy:9} "
            f"{row.instrument:8} target=${row.requested_risk_cash:.2f} "
            f"actual={actual} ratio={ratio} vol={row.volume_lots:g} "
            f"spread={spread} [{markers}]"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit distortion sizing Phase 4 bis")
    parser.add_argument("--since", default=DEFAULT_SINCE)
    parser.add_argument(
        "--with-broker-specs", action="store_true",
        help="Connexion lecture seule aux brokers pour min/step/lot_size.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    entries = load_phase4_entries(since=args.since)
    instrument_cfg = yaml.safe_load(INSTRUMENTS.read_text()) or {}
    constraints = asyncio.run(fetch_constraints(entries)) if args.with_broker_specs else {}
    rows = [
        analyze_entry(
            entry,
            constraints.get((entry.get("broker_id"), entry.get("instrument"))),
            instrument_cfg.get(entry.get("instrument"), {}),
        )
        for entry in entries
    ]
    report = {"since": args.since, "summary": summarize(rows), "rows": [asdict(row) for row in rows]}
    rendered = json.dumps(report, indent=2) if args.json else render_text(rows, report["summary"])
    if args.output:
        args.output.write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
