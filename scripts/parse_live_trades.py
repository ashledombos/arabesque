#!/usr/bin/env python3
"""
parse_live_trades.py — Extrait les trades du live.log en format structuré.

Usage:
    python scripts/parse_live_trades.py live.log
    python scripts/parse_live_trades.py live.log --jsonl   # export jsonl
    python scripts/parse_live_trades.py live.log --compare dry_run.jsonl
"""

import re
import json
import sys
import argparse
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional


@dataclass
class LiveTrade:
    """Trade extrait du live.log."""
    instrument: str
    side: str
    ts_signal: str
    signal_close: float
    signal_sl: float
    signal_tp: float
    signal_rr: float

    # Fill
    fill_volume: float = 0.0
    fill_entry: float = 0.0
    fill_slip: float = 0.0
    fill_ok: bool = False

    # Sizing
    risk_cash: float = 0.0
    lot_size: float = 0.0
    pip_val: float = 0.0
    pip_conversion: str = ""

    # BE/Trailing
    be_triggered: bool = False
    be_set: bool = False
    be_skip_count: int = 0

    # Exit
    exit_type: str = ""  # "sl_broker", "tp_broker", "timeout", "unknown"
    ts_exit: str = ""
    duration_hours: float = 0.0
    mfe_r: float = 0.0

    # Issues
    issues: list = None

    def __post_init__(self):
        if self.issues is None:
            self.issues = []


def parse_live_log(filepath: str) -> list[LiveTrade]:
    """Parse un fichier live.log et retourne les trades structurés."""
    with open(filepath) as f:
        lines = f.readlines()

    log_text = "".join(lines)
    trades: dict[str, LiveTrade] = {}  # keyed by instrument+ts_signal

    # 1. Signaux
    for m in re.finditer(
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Signal (\w+) (LONG|SHORT) '
        r'close=([\d.]+) sl=([\d.]+) tp=([\d.]+) rr=([\d.]+)', log_text
    ):
        ts, sym, side, close, sl, tp, rr = m.groups()
        key = f"{sym}_{ts}"
        trades[key] = LiveTrade(
            instrument=sym, side=side, ts_signal=ts,
            signal_close=float(close), signal_sl=float(sl),
            signal_tp=float(tp), signal_rr=float(rr),
        )

    # 2. Sizing info
    for m in re.finditer(
        r'sizing \w+ (\w+): risk=(\d+)\$ dist=([\d.]+) lot_size=([\d.]+) '
        r'pip_val=([\d.]+)\(([^)]+)\)', log_text
    ):
        sym, risk, dist, lot_size, pip_val, conversion = m.groups()
        # Match to closest signal for this instrument
        for key, t in trades.items():
            if t.instrument == sym and t.risk_cash == 0:
                t.risk_cash = float(risk)
                t.lot_size = float(lot_size)
                t.pip_val = float(pip_val)
                t.pip_conversion = conversion
                break

    # 3. Fills
    for m in re.finditer(
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Fill confirmé: (\w+) '
        r'(LONG|SHORT) ([\d.]+)L entry=([\d.]+) '
        r'\(signal=([\d.]+) slip=([\d.]+)\)', log_text
    ):
        ts, sym, side, vol, entry, signal, slip = m.groups()
        # Detect mismatch
        slip_f = float(slip)
        mismatch = slip_f > 5.0  # > 5 pips = probable mismatch

        for key, t in trades.items():
            if t.instrument == sym and not t.fill_ok and abs(t.signal_close - float(signal)) < 0.01:
                t.fill_volume = float(vol)
                t.fill_entry = float(entry)
                t.fill_slip = slip_f
                t.fill_ok = True
                if mismatch:
                    t.issues.append(f"FILL_MISMATCH: slip={slip} (entry={entry} vs signal={signal})")
                break

    # 4. Timeouts
    for m in re.finditer(
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*échec ordre (\w+) — (.+)', log_text
    ):
        ts, sym, reason = m.groups()
        for key, t in trades.items():
            if t.instrument == sym and not t.fill_ok and t.exit_type == "":
                t.exit_type = "timeout"
                t.ts_exit = ts
                t.issues.append(f"ORDER_TIMEOUT: {reason}")
                break

    # 5. BE triggers et skips
    for m in re.finditer(
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*BE trigger: (\w+) MFE=([\d.]+)R', log_text
    ):
        ts, sym, mfe = m.groups()
        for key, t in trades.items():
            if t.instrument == sym and t.fill_ok:
                t.be_triggered = True
                t.mfe_r = max(t.mfe_r, float(mfe))
                break

    for m in re.finditer(
        r'BE/Trail skipped: (\w+) new_sl=([\d.]+) > bid=([\d.]+)', log_text
    ):
        sym, new_sl, bid = m.groups()
        for key, t in trades.items():
            if t.instrument == sym and t.fill_ok:
                t.be_skip_count += 1
                break

    # 6. BE successes
    for m in re.finditer(
        r'SL amended: (\w+).*→ ([\d.]+)', log_text
    ):
        sym, new_sl = m.groups()
        for key, t in trades.items():
            if t.instrument == sym and t.fill_ok:
                t.be_set = True
                break

    # 7. Position removals (SL hit on broker)
    for m in re.finditer(
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Position (\w+) \d+ non trouvée.*'
        r'après (\d+)s.*MFE=([\d.]+)R BE=(.)', log_text
    ):
        ts, sym, secs, mfe, be = m.groups()
        for key, t in trades.items():
            if t.instrument == sym and t.fill_ok and t.exit_type == "":
                t.exit_type = "sl_broker"
                t.ts_exit = ts
                t.duration_hours = int(secs) / 3600
                t.mfe_r = float(mfe)
                if be == "✗" and t.be_triggered:
                    t.issues.append(
                        f"BE_NEVER_SET: triggered {t.be_skip_count}x but always skipped (price fell back)"
                    )
                break

    return list(trades.values())


def print_report(trades: list[LiveTrade]):
    """Affiche le rapport des trades."""
    print("=" * 90)
    print("RAPPORT TRADES LIVE")
    print("=" * 90)

    filled = [t for t in trades if t.fill_ok]
    timed_out = [t for t in trades if t.exit_type == "timeout"]
    closed = [t for t in trades if t.exit_type in ("sl_broker", "tp_broker")]
    still_open = [t for t in filled if t.exit_type == ""]

    print(f"\nSignaux: {len(trades)} | Fills: {len(filled)} | Timeouts: {len(timed_out)} | "
          f"Fermés: {len(closed)} | Encore ouverts: {len(still_open)}")

    print("\n--- TRADES DÉTAILLÉS ---\n")
    for t in trades:
        status = "✅" if t.fill_ok else "❌ TIMEOUT" if t.exit_type == "timeout" else "⚠️"
        print(f"{t.ts_signal} {t.instrument:8s} {t.side:5s} {status}")

        if t.fill_ok:
            print(f"  Fill: {t.fill_volume:.3f}L @ {t.fill_entry} (slip={t.fill_slip})")
            print(f"  Risk: ${t.risk_cash:.0f} | pip_val={t.pip_val}({t.pip_conversion}) lot_size={t.lot_size}")
            print(f"  SL={t.signal_sl:.5f} TP={t.signal_tp:.5f} RR={t.signal_rr}")

        if t.be_triggered:
            be_status = f"SET ✅" if t.be_set else f"SKIPPED ❌ ({t.be_skip_count}x)"
            print(f"  BE: triggered (MFE={t.mfe_r:.2f}R) → {be_status}")

        if t.exit_type:
            print(f"  Exit: {t.exit_type} @ {t.ts_exit} ({t.duration_hours:.1f}h)")

        if t.issues:
            for issue in t.issues:
                print(f"  ⚠️  {issue}")
        print()

    # Summary
    print("--- PROBLÈMES IDENTIFIÉS ---\n")
    all_issues = []
    for t in trades:
        for issue in t.issues:
            all_issues.append(f"{t.instrument}: {issue}")
    if all_issues:
        for issue in all_issues:
            print(f"  🔴 {issue}")
    else:
        print("  Aucun problème détecté")


def compare_with_backtest(live_trades: list[LiveTrade], bt_file: str):
    """Compare les trades live avec un fichier dry_run jsonl."""
    bt_trades = []
    bt_summary = None
    with open(bt_file) as f:
        for line in f:
            d = json.loads(line.strip())
            if d["type"] == "trade":
                bt_trades.append(d)
            elif d["type"] == "summary":
                bt_summary = d

    print("\n" + "=" * 90)
    print("COMPARAISON LIVE vs BACKTEST")
    print("=" * 90)

    if bt_summary:
        print(f"\nBacktest: {bt_summary['period_start']} → {bt_summary['period_end']}")
        print(f"  {bt_summary['n_trades']} trades | WR={bt_summary['win_rate']}% | "
              f"Total R={bt_summary['total_r']:.2f}")

    # Match by instrument
    live_instruments = {t.instrument for t in live_trades if t.fill_ok}
    bt_instruments = {t["instrument"] for t in bt_trades}

    print(f"\nInstruments live: {sorted(live_instruments)}")
    print(f"Instruments backtest: {sorted(bt_instruments)}")
    common = live_instruments & bt_instruments
    print(f"En commun: {sorted(common)}")

    if common:
        print("\n--- COMPARAISON PAR INSTRUMENT ---\n")
        for sym in sorted(common):
            live_sym = [t for t in live_trades if t.instrument == sym and t.fill_ok]
            bt_sym = [t for t in bt_trades if t["instrument"] == sym]
            print(f"{sym}:")
            print(f"  Live:    {len(live_sym)} trade(s)")
            for t in live_sym:
                print(f"    {t.side} entry={t.fill_entry} sl={t.signal_sl:.5f} "
                      f"vol={t.fill_volume}L MFE={t.mfe_r:.2f}R exit={t.exit_type}")
            print(f"  Backtest: {len(bt_sym)} trade(s)")
            for t in bt_sym:
                print(f"    {t['side']} entry={t['entry']} sl={t['sl']:.5f} "
                      f"result={t['result_r']:+.2f}R exit={t['exit_reason']}")
            print()


def export_jsonl(trades: list[LiveTrade], outfile: str):
    """Export les trades en format jsonl."""
    with open(outfile, "w") as f:
        for t in trades:
            if t.fill_ok:
                d = {
                    "type": "live_trade",
                    "instrument": t.instrument,
                    "side": t.side,
                    "ts_signal": t.ts_signal,
                    "entry": t.fill_entry,
                    "sl": t.signal_sl,
                    "tp": t.signal_tp,
                    "volume": t.fill_volume,
                    "risk_cash": t.risk_cash,
                    "pip_val": t.pip_val,
                    "pip_conversion": t.pip_conversion,
                    "lot_size": t.lot_size,
                    "slip": t.fill_slip,
                    "be_triggered": t.be_triggered,
                    "be_set": t.be_set,
                    "be_skip_count": t.be_skip_count,
                    "mfe_r": t.mfe_r,
                    "exit_type": t.exit_type,
                    "ts_exit": t.ts_exit,
                    "duration_hours": t.duration_hours,
                    "issues": t.issues,
                }
                f.write(json.dumps(d) + "\n")
    print(f"\nExporté: {outfile}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse live.log trades")
    parser.add_argument("logfile", help="Path to live.log")
    parser.add_argument("--jsonl", action="store_true", help="Export to jsonl")
    parser.add_argument("--compare", help="Path to dry_run.jsonl for comparison")
    args = parser.parse_args()

    trades = parse_live_log(args.logfile)
    print_report(trades)

    if args.compare:
        compare_with_backtest(trades, args.compare)

    if args.jsonl:
        out = args.logfile.replace(".log", "_trades.jsonl")
        export_jsonl(trades, out)
