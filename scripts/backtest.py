#!/usr/bin/env python3
"""
Arabesque v2 — CLI Backtest.

Usage :
    # Single instrument, mean-reversion
    python scripts/backtest.py EURUSD

    # Multiple instruments
    python scripts/backtest.py EURUSD GBPUSD XAUUSD --period 730d

    # Trend strategy
    python scripts/backtest.py EURUSD --strategy trend

    # Combined (mean-reversion + trend)
    python scripts/backtest.py EURUSD --strategy combined

    # All FX majors
    python scripts/backtest.py --preset fx_majors

    # All crypto
    python scripts/backtest.py --preset crypto

    # All FTMO instruments by category
    python scripts/backtest.py --preset all
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arabesque.backtest.runner import (
    BacktestConfig, BacktestRunner, BacktestResult,
    run_backtest, run_multi_instrument,
)
from arabesque.backtest.signal_gen import SignalGenConfig
from arabesque.backtest.data import print_data_status

# ── Presets ──────────────────────────────────────────────────────────

PRESETS = {
    "fx_majors": [
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    ],
    "fx_crosses": [
        "EURGBP", "EURJPY", "EURCHF", "EURCAD", "EURAUD", "EURNZD",
        "GBPJPY", "GBPCHF", "GBPCAD", "GBPAUD", "GBPNZD",
        "AUDJPY", "NZDJPY", "CADJPY", "CHFJPY",
        "AUDCAD", "AUDCHF", "AUDNZD", "CADCHF", "NZDCAD", "NZDCHF",
    ],
    "fx_exotics": [
        "USDCNH", "USDMXN", "USDNOK", "USDPLN", "USDSEK", "USDZAR",
        "EURCZK", "EURHUF", "EURNOK", "EURPLN",
    ],
    "crypto_top": [
        "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK",
    ],
    "crypto_all": [
        "BTC", "ETH", "LTC", "SOL", "BNB", "BCH", "XRP", "DOGE", "ADA",
        "DOT", "XMR", "DASH", "NEO", "UNI", "XLM", "AAVE", "MANA", "IMX",
        "GRT", "ETC", "ALGO", "NEAR", "LINK", "AVAX", "XTZ", "FET", "ICP",
        "SAND", "GAL", "VET",
    ],
    "metals": ["XAUUSD", "XAGUSD", "XPDUSD", "XPTUSD", "COPPER"],
    "energy": ["USOIL", "UKOIL", "NATGAS"],
    "indices": [
        "SP500", "NAS100", "US30", "US2000",
        "GER40", "UK100", "FRA40", "EU50",
        "JPN225", "HK50", "AUS200",
    ],
    "commodities": ["COCOA", "COFFEE", "CORN", "COTTON", "SOYBEAN", "WHEAT", "SUGAR"],
    "stocks_us": [
        "AAPL", "AMZN", "GOOG", "MSFT", "NFLX", "NVDA", "META", "TSLA",
        "BAC", "V", "WMT", "PFE", "T", "ZM", "BABA",
    ],
    "stocks_eu": ["RACE", "MC", "AF", "ALV", "BAYN", "DBK", "VOW3", "IBE"],
}

# Presets composites
PRESETS["fx_all"] = PRESETS["fx_majors"] + PRESETS["fx_crosses"] + PRESETS["fx_exotics"]
PRESETS["stocks_all"] = PRESETS["stocks_us"] + PRESETS["stocks_eu"]
PRESETS["all"] = (
    PRESETS["fx_majors"] + PRESETS["crypto_top"] + PRESETS["metals"]
    + PRESETS["energy"] + PRESETS["indices"][:5] + PRESETS["stocks_us"][:5]
)


def main():
    parser = argparse.ArgumentParser(
        description="Arabesque v2 — Backtest Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Presets disponibles :
  fx_majors     7 paires FX majeures
  fx_crosses    21 paires FX crosses
  fx_exotics    10 paires FX exotiques
  fx_all        Toutes les paires FX (38)
  crypto_top    9 cryptos principales
  crypto_all    30 cryptos FTMO
  metals        5 métaux précieux
  energy        3 énergies
  indices       11 indices mondiaux
  commodities   7 matières premières agricoles
  stocks_us     15 actions US
  stocks_eu     8 actions EU
  stocks_all    23 actions US+EU
  all           Mix de chaque catégorie (27 instruments)

Exemples :
  python scripts/backtest.py EURUSD XAUUSD BTC
  python scripts/backtest.py --preset fx_majors --strategy combined
  python scripts/backtest.py --preset crypto_top --period 365d
""",
    )

    parser.add_argument("instruments", nargs="*", help="Instruments à backtester")
    parser.add_argument("--preset", type=str, help="Preset d'instruments")
    parser.add_argument("--strategy", type=str, default="mean_reversion",
                        choices=["mean_reversion", "trend", "combined"],
                        help="Stratégie (default: mean_reversion)")
    parser.add_argument("--period", type=str, default="730d",
                        help="Période Yahoo Finance (default: 730d)")
    parser.add_argument("--start", type=str, default=None,
                        help="Date de début (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None,
                        help="Date de fin (YYYY-MM-DD)")
    parser.add_argument("--balance", type=float, default=100_000,
                        help="Balance initiale (default: 100000)")
    parser.add_argument("--risk", type=float, default=0.5,
                        help="Risque par trade en %% (default: 0.5)")
    parser.add_argument("--split", type=float, default=0.70,
                        help="Split in/out sample (default: 0.70)")
    parser.add_argument("--quiet", action="store_true",
                        help="Désactiver l'affichage détaillé")
    parser.add_argument("--data-root", type=str, default=None,
                        help="Chemin vers le data root barres_au_sol "
                             "(default: ~/dev/barres_au_sol/data ou "
                             "env BARRES_AU_SOL_DATA_ROOT)")
    parser.add_argument("--data-status", action="store_true",
                        help="Afficher la disponibilité des données Parquet "
                             "puis quitter")
    parser.add_argument("--no-parquet", action="store_true",
                        help="Forcer l'utilisation de Yahoo Finance "
                             "(ignorer les données Parquet)")

    args = parser.parse_args()

    # Résoudre les instruments
    instruments = []
    if args.preset:
        preset = args.preset.lower()
        if preset not in PRESETS:
            print(f"Preset inconnu : {preset}")
            print(f"Disponibles : {', '.join(sorted(PRESETS.keys()))}")
            sys.exit(1)
        instruments = PRESETS[preset]
        print(f"Preset '{preset}' : {len(instruments)} instruments")
    elif args.instruments:
        instruments = [i.upper() for i in args.instruments]
    else:
        parser.print_help()
        sys.exit(0)

    # Data status
    if args.data_status:
        print_data_status(instruments, data_root=args.data_root)
        sys.exit(0)

    # Config
    bt_config = BacktestConfig(
        start_balance=args.balance,
        risk_per_trade_pct=args.risk,
        verbose=not args.quiet,
    )

    # Run
    if len(instruments) == 1:
        run_backtest(
            instruments[0],
            period=args.period,
            start=args.start,
            end=args.end,
            bt_config=bt_config,
            split_pct=args.split,
            verbose=not args.quiet,
            strategy=args.strategy,
            data_root=args.data_root,
        )
    else:
        run_multi_instrument(
            instruments,
            period=args.period,
            start=args.start,
            end=args.end,
            bt_config=bt_config,
            split_pct=args.split,
            verbose=not args.quiet,
            strategy=args.strategy,
            data_root=args.data_root,       #
        )


if __name__ == "__main__":
    main()
