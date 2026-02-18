#!/usr/bin/env python3
"""
Arabesque Research — FX sur timeframe 4H.

Placement : scripts/research/explore_fx_4h.py

Même pipeline que run_label_analysis.py mais :
- Instruments FX uniquement
- Données rééchantillonnées en 4H (resample depuis les 1H)
- Résultats sauvegardés dans results/research/s3_fx_4h.json

Usage ::

    python scripts/research/explore_fx_4h.py
    python scripts/research/explore_fx_4h.py --period 1095d
    python scripts/research/explore_fx_4h.py EURUSD GBPUSD USDJPY
    python scripts/research/explore_fx_4h.py -v --json results/research/custom.json

NOTE : Ce script est expérimental. Ne pas importer dans le runner de prod.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from arabesque.backtest.runner import BacktestRunner, BacktestConfig
from arabesque.backtest.data import (
    load_ohlc, split_in_out_sample, list_all_ftmo_instruments,
    get_last_source_info, yahoo_symbol,
)
from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
from arabesque.position.manager import ManagerConfig
from arabesque.backtest.metrics_by_label import (
    analyze_by_subtype, analyze_factors, format_subtype_report,
    ventilate_pipeline_results,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _categorize(instrument: str) -> str:
    """Catégorise un instrument (copie locale pour éviter import circulaire)."""
    inst = instrument.upper().replace(".CASH", "").replace(".C", "")
    FX_CURRENCIES = {
        "EUR", "USD", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
        "CNH", "CZK", "HKD", "HUF", "ILS", "MXN", "NOK", "PLN",
        "SEK", "SGD", "ZAR", "TRY", "DKK",
    }
    if len(inst) == 6:
        base, quote = inst[:3], inst[3:]
        if base in FX_CURRENCIES and quote in FX_CURRENCIES:
            return "fx"
    return "other"


def _resample_4h(df):
    """Rééchantillonne un DataFrame 1H en 4H (OHLCV)."""
    import pandas as pd
    ohlc_dict = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }
    # S'assurer que l'index est un DatetimeIndex
    df = df.copy()
    if not hasattr(df.index, 'freq'):
        df.index = pd.to_datetime(df.index)
    return df.resample("4h").agg(ohlc_dict).dropna()


def run_fx_4h_backtest(
    instrument: str,
    period: str = "730d",
    split_pct: float = 0.70,
    verbose: bool = False,
):
    """Lance un backtest FX sur données 4H.

    Returns:
        (positions_is, positions_oos, data_source) or None on error
    """
    try:
        symbol = yahoo_symbol(instrument)
        df_1h = load_ohlc(symbol, period=period, instrument=instrument)
        source_info = get_last_source_info()
        data_source = source_info.source if source_info else "unknown"

        if len(df_1h) < 2000:
            if verbose:
                print(f"    x {instrument:12s} INSUFFICIENT_DATA ({len(df_1h)} bars 1H)")
            return None

        # Rééchantillonner en 4H
        df_4h = _resample_4h(df_1h)

        if len(df_4h) < 500:
            if verbose:
                print(f"    x {instrument:12s} INSUFFICIENT_DATA ({len(df_4h)} bars 4H)")
            return None

        sig_gen = CombinedSignalGenerator()
        df_prepared = sig_gen.prepare(df_4h)
        df_in, df_out = split_in_out_sample(df_prepared, split_pct)

        bt_cfg = BacktestConfig(verbose=False)
        mgr_cfg = ManagerConfig()

        runner_in = BacktestRunner(bt_cfg, mgr_cfg, signal_generator=sig_gen)
        result_in = runner_in.run(df_in, instrument, "in_sample")

        runner_out = BacktestRunner(bt_cfg, mgr_cfg, signal_generator=sig_gen)
        result_out = runner_out.run(df_out, instrument, "out_of_sample")

        return (result_in.closed_positions, result_out.closed_positions, data_source)

    except Exception as e:
        if verbose:
            print(f"    x {instrument:12s} ERROR: {e}")
        return None


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Arabesque Research — FX 4H backtest"
    )
    parser.add_argument("instruments", nargs="*", help="Instruments FX à analyser")
    parser.add_argument("--period", default="730d", help="Période de données (ex: 730d)")
    parser.add_argument("--split", type=float, default=0.70, help="Split IS/OOS")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--json",
        default="results/research/s3_fx_4h.json",
        help="Chemin de sortie JSON",
    )
    parser.add_argument(
        "--min-trades", type=int, default=10,
        help="Minimum de trades par cellule",
    )
    args = parser.parse_args()

    # Résoudre la liste d'instruments FX
    if args.instruments:
        instruments = [i.upper() for i in args.instruments]
    else:
        all_inst = [d["ftmo_symbol"] for d in list_all_ftmo_instruments()]
        instruments = [i for i in all_inst if _categorize(i) == "fx"]

    if not instruments:
        print("Aucun instrument FX trouvé.")
        sys.exit(1)

    print(f"\n  RESEARCH — FX 4H BACKTEST")
    print(f"  {len(instruments)} instruments FX")
    print(f"  Timeframe : 1H → rééchantillonné 4H")
    print()

    all_positions_oos: dict[str, list] = {}
    all_positions_is: dict[str, list] = {}
    t0 = time.time()
    n_ok = 0
    n_total = len(instruments)

    for i, inst in enumerate(instruments, 1):
        print(f"  [{i:2d}/{n_total}] {inst:12s} [fx      ] ", end="", flush=True)

        result = run_fx_4h_backtest(
            inst,
            period=args.period,
            split_pct=args.split,
            verbose=args.verbose,
        )

        if result is None:
            print("SKIP")
            continue

        pos_is, pos_oos, source = result
        tag = "P" if source == "parquet" else "Y"
        print(
            f"[{tag}] IS:{len(pos_is):3d}t  OOS:{len(pos_oos):3d}t",
            flush=True,
        )

        all_positions_oos[inst] = pos_oos
        all_positions_is[inst] = pos_is
        n_ok += 1

    elapsed = time.time() - t0
    print(f"\n  {n_ok}/{n_total} instruments traités en {elapsed:.0f}s")

    if not all_positions_oos:
        print("  Aucun résultat — abandon.")
        sys.exit(1)

    # Analyse
    all_oos = [p for positions in all_positions_oos.values() for p in positions]
    print(f"\n  Total positions OOS : {len(all_oos)}")

    inst_cats = {i: "fx" for i in instruments}
    groups = analyze_by_subtype(all_oos, min_trades=args.min_trades)
    factors = analyze_factors(all_oos)
    print(format_subtype_report(groups, factors, "FX 4H — VENTILATION OOS"))
    print(ventilate_pipeline_results(all_positions_oos, inst_cats, min_trades=args.min_trades))

    # ── Comparaison 1H vs 4H (résumé) ──
    print("\n  NOTE : pour comparer 1H vs 4H, relancer run_label_analysis.py --list fx")
    print("  et comparer les fichiers results/research/ vs results/stable/")

    # Export JSON
    os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
    export = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "experiment": "fx_4h",
        "timeframe": "4H (resampled from 1H)",
        "n_instruments": n_ok,
        "n_trades_oos": len(all_oos),
        "by_subtype": {
            sub: {
                "n_trades": g.n_trades,
                "win_rate": round(g.win_rate, 3),
                "expectancy": round(g.expectancy, 4),
                "profit_factor": round(g.profit_factor, 2),
                "total_r": round(g.total_r, 1),
            }
            for sub, g in groups.items()
        },
    }
    with open(args.json, "w") as f:
        json.dump(export, f, indent=2)
    print(f"\n  JSON → {args.json}")


if __name__ == "__main__":
    main()
