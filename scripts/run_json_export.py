#!/usr/bin/env python3
"""
Export des résultats de backtest en JSONL.

Placement : scripts/run_json_export.py

Usage :
    # Un seul instrument → stdout
    python scripts/run_json_export.py XAUUSD

    # Un seul instrument → fichier
    python scripts/run_json_export.py XAUUSD -o results/xauusd.jsonl

    # Multi-instrument avec synthèse
    python scripts/run_json_export.py XAUUSD XAGUSD EURUSD -o results/multi.jsonl

    # Exploiter le JSONL avec jq
    cat results/multi.jsonl | jq 'select(.type == "instrument_result" and .viable == true)'
    cat results/multi.jsonl | jq 'select(.type == "synthesis") | .viable_list'

    # Alimenter un LLM
    cat results/multi.jsonl | llm "Quels instruments sont viables pour FTMO ?"
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arabesque.backtest.runner import run_backtest, BacktestConfig
from arabesque.backtest.json_output import (
    JsonOutput, result_to_jsonl, synthesis_to_jsonl,
)


def main():
    parser = argparse.ArgumentParser(description="Export JSONL des backtests")
    parser.add_argument("instruments", nargs="+", help="Instruments à tester")
    parser.add_argument("-o", "--output", default=None, help="Fichier de sortie (défaut: stdout)")
    parser.add_argument("--period", default="730d", help="Période (défaut: 730d)")
    parser.add_argument("--risk", type=float, default=0.5, help="Risque %% (défaut: 0.5)")
    parser.add_argument("--balance", type=float, default=100_000, help="Capital (défaut: 100000)")
    parser.add_argument("--split", type=float, default=0.70, help="Split IS/OOS (défaut: 0.70)")
    parser.add_argument("--strategy", default="combined", help="Stratégie (défaut: combined)")
    args = parser.parse_args()

    bt_config = BacktestConfig(
        risk_per_trade_pct=args.risk,
        start_balance=args.balance,
    )

    # ── Ouvrir la sortie ──
    outf = open(args.output, "w") if args.output else None
    jout = JsonOutput(outf)

    # ── Metadata ──
    jout.emit_metadata(
        instruments=args.instruments,
        strategy=args.strategy,
        period=args.period,
        balance=args.balance,
        risk_pct=args.risk,
    )

    # ── Backtests ──
    all_results = {}
    for inst in args.instruments:
        # Logs vers stderr pour ne pas polluer le JSONL sur stdout
        print(f"Backtest {inst}...", file=sys.stderr)
        try:
            result_in, result_out = run_backtest(
                inst,
                period=args.period,
                bt_config=bt_config,
                split_pct=args.split,
                verbose=False,
                strategy=args.strategy,
            )
            all_results[inst] = (result_in, result_out)

            # Émettre le résultat par instrument
            jout.emit(result_to_jsonl(inst, result_in, result_out))

            m = result_out.metrics
            print(f"  {inst}: {m.n_trades} trades, exp={m.expectancy_r:+.3f}R, "
                  f"PF={m.profit_factor:.2f}, {'VIABLE' if m.expectancy_r > 0 and m.n_disqualifying_days == 0 else 'NON'}",
                  file=sys.stderr)

        except Exception as e:
            print(f"  {inst}: ERREUR — {e}", file=sys.stderr)

    # ── Synthèse multi-instrument ──
    if len(all_results) > 1:
        jout.emit(synthesis_to_jsonl(all_results))

    jout.close()

    # ── Résumé ──
    if args.output:
        print(f"\nExporté vers {args.output} ({len(all_results)} instruments)", file=sys.stderr)
        print(f"Exploiter avec :", file=sys.stderr)
        print(f"  cat {args.output} | jq .", file=sys.stderr)
        print(f"  cat {args.output} | jq 'select(.viable == true)'", file=sys.stderr)


if __name__ == "__main__":
    main()
