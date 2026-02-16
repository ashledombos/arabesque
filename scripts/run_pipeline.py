#!/usr/bin/env python3
"""
Pipeline de screening multi-instrument.

Placement : scripts/run_pipeline.py

Usage :
    # Lancer sur tous les instruments par défaut
    python scripts/run_pipeline.py

    # Lancer sur une liste spécifique
    python scripts/run_pipeline.py EURUSD GBPUSD XAUUSD XAGUSD BTC

    # Mode conservateur (seuils stricts)
    python scripts/run_pipeline.py --mode strict

    # Mode large (plus de candidats)
    python scripts/run_pipeline.py --mode wide

    # Custom
    python scripts/run_pipeline.py --min-signals 30 --min-trades 20 --period 1095d
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arabesque.backtest.pipeline import Pipeline, PipelineConfig


# ── Listes d'instruments prédéfinies ──

FX_MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD"]

FX_CROSSES = [
    "EURGBP", "EURJPY", "GBPJPY", "EURCHF", "EURAUD", "GBPAUD",
    "AUDNZD", "AUDCAD", "AUDCHF", "AUDJPY",
    "NZDJPY", "NZDCAD", "NZDCHF",
    "CADJPY", "CADCHF", "CHFJPY",
]

METALS = ["XAUUSD", "XAGUSD"]

INDICES = ["US30", "US500", "USTEC", "DE40", "UK100", "JP225"]

ENERGY = ["USOIL", "UKOIL"]

CRYPTO = ["BTC"]

ALL_INSTRUMENTS = FX_MAJORS + FX_CROSSES + METALS + INDICES + ENERGY + CRYPTO


def main():
    parser = argparse.ArgumentParser(description="Pipeline de screening Arabesque")
    parser.add_argument("instruments", nargs="*", default=None,
                        help="Instruments à tester (défaut: tous)")
    parser.add_argument("--mode", choices=["default", "strict", "wide"],
                        default="default", help="Mode de filtrage")
    parser.add_argument("--period", default="730d", help="Période (défaut: 730d)")
    parser.add_argument("--strategy", default="combined", help="Stratégie")
    parser.add_argument("--sims", type=int, default=10_000, help="Simulations MC")
    parser.add_argument("--min-signals", type=int, default=None)
    parser.add_argument("--min-trades", type=int, default=None)
    parser.add_argument("--min-exp", type=float, default=None)
    parser.add_argument("--list", choices=["fx", "metals", "indices", "crypto", "all"],
                        default=None, help="Liste prédéfinie d'instruments")
    args = parser.parse_args()

    # ── Sélection des instruments ──
    if args.instruments:
        instruments = args.instruments
    elif args.list:
        lists = {
            "fx": FX_MAJORS + FX_CROSSES,
            "metals": METALS,
            "indices": INDICES,
            "crypto": CRYPTO,
            "all": ALL_INSTRUMENTS,
        }
        instruments = lists[args.list]
    else:
        instruments = ALL_INSTRUMENTS

    # ── Configuration selon le mode ──
    configs = {
        "default": PipelineConfig(
            min_signals=50,
            min_trades_is=30,
            min_expectancy_r=-0.10,
            min_profit_factor=0.8,
            max_dd_pct=10.0,
        ),
        "strict": PipelineConfig(
            min_signals=80,
            min_trades_is=50,
            min_expectancy_r=-0.05,
            min_profit_factor=0.9,
            max_dd_pct=6.0,
        ),
        "wide": PipelineConfig(
            min_signals=30,
            min_trades_is=20,
            min_expectancy_r=-0.15,
            min_profit_factor=0.7,
            max_dd_pct=12.0,
        ),
    }
    cfg = configs[args.mode]
    cfg.period = args.period
    cfg.strategy = args.strategy
    cfg.n_simulations = args.sims

    # Overrides manuels
    if args.min_signals is not None:
        cfg.min_signals = args.min_signals
    if args.min_trades is not None:
        cfg.min_trades_is = args.min_trades
    if args.min_exp is not None:
        cfg.min_expectancy_r = args.min_exp

    # ── Lancer le pipeline ──
    print(f"\nMode: {args.mode} | Instruments: {len(instruments)} | Période: {args.period}")
    print(f"Seuils: signals>={cfg.min_signals}, trades_IS>={cfg.min_trades_is}, "
          f"exp>={cfg.min_expectancy_r}, PF>={cfg.min_profit_factor}, DD<={cfg.max_dd_pct}%\n")

    pipeline = Pipeline(cfg)
    result = pipeline.run(instruments)

    # ── Export des résultats viables ──
    if result.stage3_passed:
        print(f"\n>>> PROCHAINE ÉTAPE : lancer l'analyse statistique sur les viables :")
        for inst in result.stage3_passed:
            print(f"    python scripts/run_stats.py {inst} --period {args.period}")


if __name__ == "__main__":
    main()
