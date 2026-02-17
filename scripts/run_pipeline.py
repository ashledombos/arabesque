#!/usr/bin/env python3
"""
Pipeline de screening multi-instrument v2.

Placement : scripts/run_pipeline.py

Usage :
    # Auto-détection : tous les instruments avec Parquet disponible
    python scripts/run_pipeline.py

    # Par catégorie
    python scripts/run_pipeline.py --list fx
    python scripts/run_pipeline.py --list metals
    python scripts/run_pipeline.py --list all

    # Liste spécifique
    python scripts/run_pipeline.py EURUSD GBPUSD XAUUSD

    # Modes de filtrage
    python scripts/run_pipeline.py --mode strict
    python scripts/run_pipeline.py --mode wide

    # Verbose (détails des éliminations stage 1)
    python scripts/run_pipeline.py -v

    # Chemin Parquet custom
    python scripts/run_pipeline.py --data-root /path/to/barres_au_sol/data

    # Sans export JSON auto
    python scripts/run_pipeline.py --no-json
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arabesque.backtest.pipeline import Pipeline, PipelineConfig


def main():
    parser = argparse.ArgumentParser(description="Arabesque Pipeline v2")
    parser.add_argument("instruments", nargs="*", default=None,
                        help="Instruments (défaut: auto depuis Parquet)")
    parser.add_argument("--list", choices=[
        "fx", "fx_majors", "fx_crosses", "fx_exotics",
        "metals", "indices", "energy", "commodities", "crypto",
        "all",
    ], default=None, help="Liste prédéfinie")
    parser.add_argument("--mode", choices=["default", "strict", "wide"],
                        default="default")
    parser.add_argument("--period", default="730d")
    parser.add_argument("--strategy", default="combined")
    parser.add_argument("--data-root", default=None,
                        help="Chemin vers barres_au_sol/data")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--no-json", action="store_true",
                        help="Désactiver l'export JSONL automatique")
    parser.add_argument("--output-dir", default="results")
    args = parser.parse_args()

    # ── Configuration selon le mode ──
    configs = {
        "default": PipelineConfig(
            min_signals=50, min_trades_is=30,
            min_expectancy_r=-0.10, min_profit_factor=0.8, max_dd_pct=10.0,
        ),
        "strict": PipelineConfig(
            min_signals=80, min_trades_is=50,
            min_expectancy_r=-0.05, min_profit_factor=0.9, max_dd_pct=6.0,
        ),
        "wide": PipelineConfig(
            min_signals=30, min_trades_is=20,
            min_expectancy_r=-0.15, min_profit_factor=0.7, max_dd_pct=12.0,
        ),
    }
    cfg = configs[args.mode]
    cfg.period = args.period
    cfg.strategy = args.strategy
    cfg.data_root = args.data_root
    cfg.verbose = args.verbose
    cfg.auto_json = not args.no_json
    cfg.output_dir = args.output_dir

    # ── Résolution instruments ──
    instruments = None  # None = auto-détection Parquet

    if args.instruments:
        instruments = [i.upper() for i in args.instruments]
    elif args.list:
        instruments = _get_list(args.list)

    # ── Run ──
    pipeline = Pipeline(cfg)
    result = pipeline.run(instruments)

    # ── Suggestions ──
    if result.stage3_passed:
        print("  Prochaine etape :")
        for inst in result.stage3_passed:
            print(f"    python scripts/run_stats.py {inst} --period {args.period}")


def _get_list(name: str) -> list[str]:
    """Retourne une liste d'instruments par catégorie."""
    # Essayer de charger depuis instruments.csv
    try:
        from arabesque.backtest.data import list_all_ftmo_instruments
        all_inst = list_all_ftmo_instruments()
        if all_inst:
            cat_map = {
                "fx": lambda i: i["category"] == "fx",
                "fx_majors": lambda i: i["ftmo_symbol"] in {
                    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"},
                "fx_crosses": lambda i: i["category"] == "fx" and i["ftmo_symbol"] not in {
                    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"},
                "fx_exotics": lambda i: i["category"] == "fx" and any(
                    c in i["ftmo_symbol"] for c in ["MXN", "NOK", "PLN", "SEK", "ZAR", "CZK", "HUF", "CNH", "HKD", "SGD", "ILS"]),
                "metals": lambda i: i["category"] == "metals",
                "indices": lambda i: i["category"] == "indices",
                "energy": lambda i: i["category"] == "energy",
                "commodities": lambda i: i["category"] == "commodities",
                "crypto": lambda i: i["category"] == "crypto",
                "all": lambda i: True,
            }
            filt = cat_map.get(name, lambda i: True)
            return [i["ftmo_symbol"] for i in all_inst if filt(i)]
    except Exception:
        pass

    # Fallback hardcodé
    LISTS = {
        "fx_majors": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"],
        "fx_crosses": [
            "EURGBP", "EURJPY", "GBPJPY", "EURCHF", "EURAUD", "GBPAUD",
            "AUDNZD", "AUDCAD", "AUDCHF", "AUDJPY", "NZDJPY", "NZDCAD",
            "NZDCHF", "CADJPY", "CADCHF", "CHFJPY",
        ],
        "metals": ["XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD", "XCUUSD"],
        "indices": ["US30", "US500", "US100", "DE40", "UK100", "JP225", "EU50", "FRA40", "AU200"],
        "energy": ["USOIL", "UKOIL", "NATGAS"],
        "commodities": ["COCOA", "COFFEE", "CORN", "COTTON", "SOYBEAN", "SUGAR", "WHEAT"],
        "crypto": ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK"],
    }
    LISTS["fx"] = LISTS["fx_majors"] + LISTS["fx_crosses"]
    LISTS["fx_exotics"] = ["USDMXN", "USDNOK", "USDPLN", "USDSEK", "USDZAR", "USDCNH"]
    LISTS["all"] = []
    for k in ["fx_majors", "fx_crosses", "metals", "indices", "energy"]:
        LISTS["all"].extend(LISTS[k])

    return LISTS.get(name, LISTS["all"])


if __name__ == "__main__":
    main()
