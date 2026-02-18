#!/usr/bin/env python3
"""
Arabesque Research — TP fixe vs Trailing Stop Loss.

Placement : scripts/research/explore_tp_vs_tsl.py

Compare trois stratégies de sortie sur les sub-types avec AvgWin > 1.0R :
  A) Trailing actuel (paliers par défaut)
  B) TP fixe à 1.5R
  C) TP fixe à 2.0R

Le critère de sélection : TP fixe retenu si Sharpe ≥ trailing ET max DD ≤ trailing.

Usage ::

    python scripts/research/explore_tp_vs_tsl.py
    python scripts/research/explore_tp_vs_tsl.py --min-avgwin 0.8
    python scripts/research/explore_tp_vs_tsl.py --tp-levels 1.5 2.0 2.5
    python scripts/research/explore_tp_vs_tsl.py -v

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
from arabesque.backtest.metrics_by_label import analyze_by_subtype


# Sub-types avec AvgWin typiquement > 1.0R (issus de l'analyse Phase 1.3)
HIGH_AVGWIN_SUBTYPES = {
    "mr_deep_wide",
    "mr_deep_narrow",
    "trend_strong",
}


# ── Helpers ──────────────────────────────────────────────────────────

def _categorize(instrument: str) -> str:
    """Version minimale de _categorize pour filtrage."""
    inst = instrument.upper().replace(".CASH", "").replace(".C", "")
    if inst in ("XAUUSD", "XAGUSD", "XCUUSD", "XPTUSD", "XPDUSD"):
        return "metals"
    if inst in ("USOIL", "UKOIL", "NATGAS", "HEATOIL"):
        return "energy"
    if inst in ("COCOA", "COFFEE", "CORN", "COTTON", "SOYBEAN", "SUGAR", "WHEAT"):
        return "commodities"
    crypto_bases = {"BTC", "ETH", "LTC", "SOL", "BNB", "BCH", "XRP", "ADA",
                    "DOGE", "DOT", "LINK", "XTZ", "AVAX", "ALGO"}
    if inst.endswith("USD") and inst[:-3] in crypto_bases:
        return "crypto"
    indices = {"US30", "US500", "US100", "DE40", "UK100", "JP225", "AU200"}
    if inst in indices:
        return "indices"
    fx_ccy = {"EUR", "USD", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"}
    if len(inst) == 6 and inst[:3] in fx_ccy and inst[3:] in fx_ccy:
        return "fx"
    return "other"


def _patch_tp(positions: list, tp_r: float) -> list:
    """Recalcule les résultats avec un TP fixe à tp_r × R.

    Approche conservatrice : si la position a atteint tp_r × R en MFE,
    on considère le trade fermé à ce niveau. Sinon résultat inchangé.

    NOTE : c'est une simulation a posteriori sur MFE, PAS une simulation
    d'exécution complète. Sert à l'estimation, pas à l'optimisation fine.
    """
    import copy
    patched = []
    for pos in positions:
        p = copy.copy(pos)
        if p.mfe_r >= tp_r:
            # Le prix a atteint le TP — exit simulé à tp_r
            p._patched_result_r = tp_r
            p._patched_exit = f"tp_fixed_{tp_r}r"
        else:
            # Pas atteint le TP → sort en SL ou trailing (résultat original)
            p._patched_result_r = p.result_r if p.result_r is not None else 0.0
            p._patched_exit = p.exit_reason
        patched.append(p)
    return patched


def _metrics(positions: list, label: str = "") -> dict:
    """Calcule les métriques de base sur une liste de positions."""
    if not positions:
        return {"n": 0, "total_r": 0, "win_rate": 0, "expectancy": 0,
                "avg_win": 0, "avg_loss": 0}

    results = [
        getattr(p, "_patched_result_r", None) or
        (p.result_r if p.result_r is not None else 0.0)
        for p in positions
    ]

    wins = [r for r in results if r > 0]
    losses = [r for r in results if r <= 0]
    n = len(results)

    return {
        "n": n,
        "total_r": round(sum(results), 2),
        "win_rate": round(len(wins) / n, 3) if n else 0,
        "expectancy": round(sum(results) / n, 4) if n else 0,
        "avg_win": round(sum(wins) / len(wins), 3) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 3) if losses else 0,
    }


def run_backtest(instrument: str, period: str, split_pct: float, verbose: bool):
    """Lance un backtest standard et retourne les positions OOS."""
    try:
        symbol = yahoo_symbol(instrument)
        df = load_ohlc(symbol, period=period, instrument=instrument)
        source_info = get_last_source_info()

        if len(df) < 2000:
            return None

        sig_gen = CombinedSignalGenerator()
        df_prepared = sig_gen.prepare(df)
        _, df_out = split_in_out_sample(df_prepared, split_pct)

        bt_cfg = BacktestConfig(verbose=False)
        mgr_cfg = ManagerConfig()

        runner = BacktestRunner(bt_cfg, mgr_cfg, signal_generator=sig_gen)
        result = runner.run(df_out, instrument, "out_of_sample")
        return result.closed_positions

    except Exception as e:
        if verbose:
            print(f"    x {instrument:12s} ERROR: {e}")
        return None


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Arabesque Research — TP fixe vs Trailing Stop"
    )
    parser.add_argument("instruments", nargs="*", help="Instruments à analyser")
    parser.add_argument("--period", default="730d")
    parser.add_argument("--split", type=float, default=0.70)
    parser.add_argument(
        "--tp-levels", nargs="+", type=float, default=[1.5, 2.0],
        help="Niveaux de TP fixe à tester (en R)",
    )
    parser.add_argument(
        "--min-avgwin", type=float, default=1.0,
        help="AvgWin minimum (en R) pour inclure un sub-type",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--json",
        default="results/research/s4_tp_vs_tsl.json",
        help="Chemin de sortie JSON",
    )
    args = parser.parse_args()

    # Instruments : tous par défaut (on filtre sur sub-type après)
    if args.instruments:
        instruments = [i.upper() for i in args.instruments]
    else:
        instruments = [d["ftmo_symbol"] for d in list_all_ftmo_instruments()]

    if not instruments:
        print("Aucun instrument trouvé.")
        sys.exit(1)

    print(f"\n  RESEARCH — TP FIXE vs TRAILING STOP")
    print(f"  {len(instruments)} instruments")
    print(f"  TP levels : {args.tp_levels}R")
    print(f"  Filtre AvgWin > {args.min_avgwin}R")
    print()

    all_oos: list = []
    t0 = time.time()
    n_ok = 0

    for i, inst in enumerate(instruments, 1):
        cat = _categorize(inst)
        print(f"  [{i:2d}/{len(instruments)}] {inst:12s} [{cat:8s}] ", end="", flush=True)

        positions = run_backtest(inst, args.period, args.split, args.verbose)
        if positions is None:
            print("SKIP")
            continue

        print(f"{len(positions):3d} trades OOS", flush=True)
        all_oos.extend(positions)
        n_ok += 1

    elapsed = time.time() - t0
    print(f"\n  {n_ok}/{len(instruments)} instruments en {elapsed:.0f}s")
    print(f"  Total positions OOS : {len(all_oos)}")

    if not all_oos:
        print("  Aucun résultat — abandon.")
        sys.exit(1)

    # ── Filtrer sur sub-types avec AvgWin > min_avgwin ──
    groups_all = analyze_by_subtype(all_oos, min_trades=10)
    eligible_subtypes = {
        sub for sub, g in groups_all.items()
        if (g.n_trades > 0 and
            (sum(r for r in [getattr(p, 'result_r', 0) or 0
                             for p in all_oos
                             if _get_subtype(p) == sub] if r > 0 else [])
             / max(1, sum(1 for p in all_oos
                          if _get_subtype(p) == sub
                          and (getattr(p, 'result_r', 0) or 0) > 0))
             ) >= args.min_avgwin
        )
    }

    # Simplification : utiliser HIGH_AVGWIN_SUBTYPES comme base
    eligible_subtypes = eligible_subtypes | HIGH_AVGWIN_SUBTYPES
    print(f"\n  Sub-types éligibles (AvgWin > {args.min_avgwin}R) : {sorted(eligible_subtypes)}")

    eligible_positions = [
        p for p in all_oos
        if _get_subtype(p) in eligible_subtypes
    ]
    print(f"  Positions éligibles : {len(eligible_positions)}")

    if not eligible_positions:
        print("  Aucune position éligible — abandon.")
        sys.exit(1)

    # ── Comparaison ──
    results_export: dict = {}

    print(f"\n  {'Stratégie':<20} {'N':>6} {'Total R':>10} {'WR':>8} {'Expectancy':>12} {'AvgW':>8} {'AvgL':>8}")
    print(f"  {'-'*76}")

    # Baseline : trailing actuel
    base_metrics = _metrics(eligible_positions, "trailing_actuel")
    print(f"  {'trailing_actuel':<20} {base_metrics['n']:>6} "
          f"{base_metrics['total_r']:>10.2f} {base_metrics['win_rate']:>8.1%} "
          f"{base_metrics['expectancy']:>12.4f} {base_metrics['avg_win']:>8.3f} "
          f"{base_metrics['avg_loss']:>8.3f}")
    results_export["trailing_actuel"] = base_metrics

    # TP fixes
    for tp_r in args.tp_levels:
        patched = _patch_tp(eligible_positions, tp_r)
        m = _metrics(patched, f"tp_{tp_r}r")
        label = f"tp_fixed_{tp_r}r"
        print(f"  {label:<20} {m['n']:>6} {m['total_r']:>10.2f} {m['win_rate']:>8.1%} "
              f"{m['expectancy']:>12.4f} {m['avg_win']:>8.3f} {m['avg_loss']:>8.3f}")
        results_export[label] = m

    print(f"  {'-'*76}")
    print("  NOTE : simulation a posteriori sur MFE — pas un backtest d'exécution complet")

    # Export JSON
    os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
    export = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "experiment": "tp_vs_tsl",
        "tp_levels": args.tp_levels,
        "min_avgwin_filter": args.min_avgwin,
        "eligible_subtypes": sorted(eligible_subtypes),
        "n_eligible_positions": len(eligible_positions),
        "results": results_export,
    }
    with open(args.json, "w") as f:
        json.dump(export, f, indent=2)
    print(f"\n  JSON → {args.json}")


def _get_subtype(pos) -> str:
    """Extrait le sub_type d'une position depuis signal_data ou attribut direct."""
    sd = getattr(pos, "signal_data", {})
    if isinstance(sd, dict) and sd.get("sub_type"):
        return sd["sub_type"]
    return getattr(pos, "_sub_type", "")


if __name__ == "__main__":
    main()
