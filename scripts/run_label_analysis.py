#!/usr/bin/env python3
"""
Arabesque v2 — Pipeline Phase 1.3 : Analyse par sous-type de signal.

Placement : scripts/run_label_analysis.py

Lance les backtests, collecte les positions labelées, et produit la matrice
sub_type × catégorie d'instrument.

Usage :
    # Analyse sur tous les instruments avec Parquet
    python scripts/run_label_analysis.py

    # Seulement crypto
    python scripts/run_label_analysis.py --list crypto

    # Instruments spécifiques
    python scripts/run_label_analysis.py XAUUSD EURUSD BTCUSD XTZUSD

    # Avec plus de détail
    python scripts/run_label_analysis.py -v

    # Exporter en JSON
    python scripts/run_label_analysis.py --json results/labels_analysis.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

# Ajouter le parent pour les imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arabesque.backtest.runner import BacktestRunner, BacktestConfig, BacktestResult
from arabesque.backtest.data import (
    load_ohlc, split_in_out_sample, list_all_ftmo_instruments,
    get_last_source_info, yahoo_symbol,
)
from arabesque.backtest.signal_gen import BacktestSignalGenerator, SignalGenConfig
from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
from arabesque.position.manager import ManagerConfig
from arabesque.backtest.metrics_by_label import (
    analyze_by_subtype, analyze_factors, format_subtype_report,
    ventilate_pipeline_results,
)


def _categorize(instrument: str) -> str:
    """Catégorise un instrument FTMO (copie de data.py)."""
    inst = instrument.upper().replace(".CASH", "").replace(".C", "")

    if inst in ("XAUUSD", "XAGUSD", "XAUEUR", "XAGEUR", "XAUAUD", "XAGAUD",
                "XCUUSD", "XPTUSD", "XPDUSD"):
        return "metals"
    if inst in ("USOIL", "UKOIL", "NATGAS", "HEATOIL"):
        return "energy"
    if inst in ("COCOA", "COFFEE", "CORN", "COTTON", "SOYBEAN", "SUGAR", "WHEAT"):
        return "commodities"
    INDICES = {"US30", "US500", "US100", "US2000", "USTEC", "DE40", "GER40",
               "UK100", "JP225", "AU200", "AUS200", "EU50", "FRA40", "SPN35",
               "HK50", "N25", "DXY"}
    if inst in INDICES:
        return "indices"
    CRYPTO_BASES = {"BTC", "ETH", "LTC", "SOL", "BNB", "BCH", "XRP", "ADA",
                    "DOGE", "DOT", "UNI", "XLM", "VET", "VEC", "MANA", "MAN",
                    "SAND", "SAN", "XTZ", "AVAX", "AVA", "LINK", "LNK", "AAVE",
                    "AAV", "ALGO", "ALG", "NEAR", "NER", "IMX", "GRT", "GAL",
                    "FET", "ICP", "BAR", "NEO", "XMR", "DASH", "DAS", "ETC"}
    if inst.endswith("USD") and len(inst) >= 6:
        base = inst[:-3]
        if base in CRYPTO_BASES:
            return "crypto"
    if inst in CRYPTO_BASES:
        return "crypto"
    FX_CURRENCIES = {"EUR", "USD", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
                     "CNH", "CZK", "HKD", "HUF", "ILS", "MXN", "NOK", "PLN",
                     "SEK", "SGD", "ZAR", "TRY", "DKK"}
    if len(inst) == 6:
        base, quote = inst[:3], inst[3:]
        if base in FX_CURRENCIES and quote in FX_CURRENCIES:
            return "fx"
    return "other"


def run_single_backtest(
    instrument: str,
    period: str = "730d",
    split_pct: float = 0.70,
    verbose: bool = False,
) -> tuple[list, list, str] | None:
    """Lance un backtest et retourne les positions IS + OOS.

    Returns:
        (positions_is, positions_oos, data_source) or None on error
    """
    try:
        symbol = yahoo_symbol(instrument)
        df = load_ohlc(symbol, period=period, instrument=instrument)
        source_info = get_last_source_info()
        data_source = source_info.source if source_info else "unknown"

        if len(df) < 2000:
            if verbose:
                print(f"    x {instrument:12s} INSUFFICIENT_DATA ({len(df)} bars)")
            return None

        # Signal generator (combined = MR + trend)
        sig_gen = CombinedSignalGenerator()
        df_prepared = sig_gen.prepare(df)
        df_in, df_out = split_in_out_sample(df_prepared, split_pct)

        bt_cfg = BacktestConfig(verbose=False)
        mgr_cfg = ManagerConfig()

        # IS
        runner_in = BacktestRunner(bt_cfg, mgr_cfg, signal_generator=sig_gen)
        result_in = runner_in.run(df_in, instrument, "in_sample")

        # OOS
        runner_out = BacktestRunner(bt_cfg, mgr_cfg, signal_generator=sig_gen)
        result_out = runner_out.run(df_out, instrument, "out_of_sample")

        return (result_in.closed_positions, result_out.closed_positions, data_source)

    except Exception as e:
        if verbose:
            print(f"    x {instrument:12s} ERROR: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Arabesque Phase 1.3 — Label Analysis")
    parser.add_argument("instruments", nargs="*", help="Instruments à analyser")
    parser.add_argument("--list", choices=["fx", "crypto", "metals", "indices",
                                           "energy", "all"],
                        help="Catégorie d'instruments")
    parser.add_argument("--period", default="730d", help="Période de données")
    parser.add_argument("--split", type=float, default=0.70, help="Split IS/OOS")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--json", help="Exporter en JSON")
    parser.add_argument("--oos-only", action="store_true", default=True,
                        help="Analyser seulement les résultats OOS (défaut)")
    parser.add_argument("--is-too", action="store_true",
                        help="Inclure aussi les résultats IS")
    parser.add_argument("--min-trades", type=int, default=10,
                        help="Minimum de trades par cellule dans la matrice")

    args = parser.parse_args()

    # Résoudre la liste d'instruments
    if args.instruments:
        instruments = [i.upper() for i in args.instruments]
    elif args.list:
        all_inst = list_all_ftmo_instruments()
        if args.list == "all":
            instruments = all_inst
        else:
            instruments = [i for i in all_inst if _categorize(i) == args.list]
    else:
        # Auto: tous les instruments avec Parquet
        instruments = list_all_ftmo_instruments()

    if not instruments:
        print("Aucun instrument trouvé.")
        sys.exit(1)

    # Catégoriser
    inst_cats = {i: _categorize(i) for i in instruments}
    cat_counts = defaultdict(int)
    for cat in inst_cats.values():
        cat_counts[cat] += 1

    print(f"\n  PHASE 1.3 — ANALYSE PAR SOUS-TYPE DE SIGNAL")
    print(f"  {len(instruments)} instruments")
    for cat, n in sorted(cat_counts.items()):
        print(f"    {cat:12s}: {n}")
    print()

    # Lancer les backtests
    all_positions_oos: dict[str, list] = {}
    all_positions_is: dict[str, list] = {}
    t0 = time.time()
    n_ok = 0
    n_total = len(instruments)

    for i, inst in enumerate(instruments, 1):
        cat = inst_cats[inst]
        print(f"  [{i:2d}/{n_total}] {inst:12s} [{cat:8s}] ", end="", flush=True)

        result = run_single_backtest(
            inst, period=args.period, split_pct=args.split, verbose=args.verbose
        )

        if result is None:
            print("SKIP")
            continue

        pos_is, pos_oos, source = result
        tag = "P" if source == "parquet" else "Y"

        n_is = len(pos_is)
        n_oos = len(pos_oos)

        # Compter les trades labelés
        n_labeled = sum(1 for p in pos_oos if _has_label(p))

        print(f"[{tag}] IS:{n_is:3d}t  OOS:{n_oos:3d}t  labeled:{n_labeled:3d}", flush=True)

        all_positions_oos[inst] = pos_oos
        if args.is_too:
            all_positions_is[inst] = pos_is
        n_ok += 1

    elapsed = time.time() - t0
    print(f"\n  {n_ok}/{n_total} instruments traités en {elapsed:.0f}s")

    # ── Analyse globale ──

    # 1. Pool toutes les positions OOS
    all_oos = []
    for positions in all_positions_oos.values():
        all_oos.extend(positions)

    print(f"\n  Total positions OOS : {len(all_oos)}")
    n_labeled_total = sum(1 for p in all_oos if _has_label(p))
    print(f"  Positions labelées  : {n_labeled_total}")

    if n_labeled_total == 0:
        print("\n  ⚠ AUCUNE POSITION LABELÉE")
        print("  Vérifiez que signal_labeler.py est intégré dans signal_gen.py")
        print("  et que les Signal ont les champs sub_type / label_factors")
        # Fallback : tenter un labeling a posteriori basé sur signal_data
        print("\n  Tentative de labeling a posteriori via signal_data...")
        n_relabeled = _relabel_from_signal_data(all_oos)
        print(f"  Relabelés : {n_relabeled}")
        if n_relabeled == 0:
            print("  Impossible de labeler. Sortie.")
            sys.exit(1)

    # 2. Ventilation par sub_type (global)
    groups = analyze_by_subtype(all_oos, min_trades=args.min_trades)
    factors = analyze_factors(all_oos)
    print(format_subtype_report(groups, factors, "VENTILATION OOS GLOBALE"))

    # 3. Matrice sub_type × catégorie
    print(ventilate_pipeline_results(
        all_positions_oos, inst_cats, min_trades=args.min_trades
    ))

    # 4. Par catégorie, détail sub_type
    for cat in sorted(cat_counts.keys()):
        cat_oos = []
        for inst, positions in all_positions_oos.items():
            if inst_cats.get(inst) == cat:
                cat_oos.extend(positions)
        if cat_oos:
            cat_groups = analyze_by_subtype(cat_oos, min_trades=5)
            cat_factors = analyze_factors(cat_oos)
            if cat_groups:
                print(format_subtype_report(
                    cat_groups, cat_factors,
                    f"DÉTAIL {cat.upper()} ({len(cat_oos)} trades OOS)"
                ))

    # ── Export JSON ──
    if args.json:
        export = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "n_instruments": n_ok,
            "n_trades_oos": len(all_oos),
            "global": _metrics_to_dict(groups),
            "by_category": {},
        }
        for cat in sorted(cat_counts.keys()):
            cat_oos = []
            for inst, positions in all_positions_oos.items():
                if inst_cats.get(inst) == cat:
                    cat_oos.extend(positions)
            cat_groups = analyze_by_subtype(cat_oos, min_trades=5)
            export["by_category"][cat] = _metrics_to_dict(cat_groups)

        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w") as f:
            json.dump(export, f, indent=2)
        print(f"\n  JSON → {args.json}")


def _has_label(pos) -> bool:
    """Check if a position has a label."""
    sd = getattr(pos, "signal_data", {})
    if isinstance(sd, dict) and sd.get("sub_type"):
        return True
    return bool(getattr(pos, "_sub_type", ""))


def _relabel_from_signal_data(positions: list) -> int:
    """Tente de re-labeler a posteriori en utilisant signal_data.

    Si les positions ont rsi, bb_width etc. dans signal_data mais pas de sub_type,
    on peut recalculer le label.
    """
    n = 0
    for pos in positions:
        sd = getattr(pos, "signal_data", {})
        if not isinstance(sd, dict):
            continue
        if sd.get("sub_type"):
            continue  # Déjà labelé

        rsi = sd.get("rsi", 50)
        bb_width = sd.get("bb_width", 0)
        strategy_type = sd.get("strategy_type", "mean_reversion")

        if strategy_type == "trend":
            adx = sd.get("adx", 0) or sd.get("htf_adx", 0)
            sub_type = "trend_strong" if adx >= 30 else "trend_moderate"
        else:
            # MR labeling simplifié (pas de z-score sans le DF complet)
            side = getattr(pos, "side", None)
            if side and side.value == "LONG":
                is_deep = rsi < 25
            else:
                is_deep = rsi > 75

            # Fallback: bb_width > 0.03 = "wide" (heuristic)
            is_wide = bb_width > 0.03

            if is_deep and is_wide:
                sub_type = "mr_deep_wide"
            elif is_deep:
                sub_type = "mr_deep_narrow"
            elif is_wide:
                sub_type = "mr_shallow_wide"
            else:
                sub_type = "mr_shallow_narrow"

        sd["sub_type"] = sub_type
        n += 1

    return n


def _metrics_to_dict(groups: dict) -> dict:
    """Convert SubTypeMetrics to JSON-serializable dict."""
    return {
        sub: {
            "n_trades": g.n_trades,
            "win_rate": round(g.win_rate, 3),
            "expectancy": round(g.expectancy, 4),
            "profit_factor": round(g.profit_factor, 2),
            "total_r": round(g.total_r, 1),
        }
        for sub, g in groups.items()
    }


if __name__ == "__main__":
    main()
