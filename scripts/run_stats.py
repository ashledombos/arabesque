#!/usr/bin/env python3
"""
Lancer l'analyse statistique après un backtest.

Placement : scripts/run_stats.py

Usage :
    # Après un backtest classique
    python scripts/run_stats.py XAUUSD

    # Avec paramètres custom
    python scripts/run_stats.py XAUUSD --risk 0.5 --sims 20000 --period 730d
"""

import argparse
import sys
import os

# Ajouter le répertoire parent au path si besoin
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arabesque.backtest.runner import run_backtest, BacktestConfig
from arabesque.backtest.stats import full_statistical_analysis


def main():
    parser = argparse.ArgumentParser(description="Analyse statistique post-backtest")
    parser.add_argument("instrument", help="Instrument à analyser (ex: XAUUSD)")
    parser.add_argument("--period", default="730d", help="Période de données (défaut: 730d)")
    parser.add_argument("--risk", type=float, default=0.5, help="Risque par trade en %% (défaut: 0.5)")
    parser.add_argument("--balance", type=float, default=100_000, help="Capital initial (défaut: 100000)")
    parser.add_argument("--sims", type=int, default=10_000, help="Simulations Monte Carlo (défaut: 10000)")
    parser.add_argument("--split", type=float, default=0.70, help="Split IS/OOS (défaut: 0.70)")
    parser.add_argument("--strategy", default="combined", help="Stratégie (défaut: combined)")
    parser.add_argument("--quiet", action="store_true", help="Pas de logs de backtest")
    args = parser.parse_args()

    bt_config = BacktestConfig(
        risk_per_trade_pct=args.risk,
        start_balance=args.balance,
    )

    # ── Lancer le backtest ──
    print(f"Backtest {args.instrument}...")
    result_in, result_out = run_backtest(
        args.instrument,
        period=args.period,
        bt_config=bt_config,
        split_pct=args.split,
        verbose=not args.quiet,
        strategy=args.strategy,
    )

    # ── Extraire les résultats R ──
    results_r_is = [p.result_r for p in result_in.closed_positions if p.result_r is not None]
    results_r_oos = [p.result_r for p in result_out.closed_positions if p.result_r is not None]

    # ── Analyse statistique IN-SAMPLE ──
    if results_r_is:
        print(f"\n{'#'*60}")
        print(f"  STATISTIQUES IN-SAMPLE ({len(results_r_is)} trades)")
        print(f"{'#'*60}")
        report_is = full_statistical_analysis(
            results_r_is,
            risk_per_trade_pct=args.risk,
            start_balance=args.balance,
            n_simulations=args.sims,
        )
        print(report_is)

    # ── Analyse statistique OUT-OF-SAMPLE ──
    if results_r_oos:
        print(f"\n{'#'*60}")
        print(f"  STATISTIQUES OUT-OF-SAMPLE ({len(results_r_oos)} trades)")
        print(f"{'#'*60}")
        report_oos = full_statistical_analysis(
            results_r_oos,
            risk_per_trade_pct=args.risk,
            start_balance=args.balance,
            n_simulations=args.sims,
        )
        print(report_oos)

    # ── Résumé décisionnel ──
    print(f"\n{'#'*60}")
    print(f"  DÉCISION")
    print(f"{'#'*60}")
    print(f"  IS  : {len(results_r_is)} trades, exp={sum(results_r_is)/len(results_r_is):+.4f}R")
    print(f"  OOS : {len(results_r_oos)} trades, exp={sum(results_r_oos)/len(results_r_oos):+.4f}R")

    # Dégradation IS → OOS
    exp_is = sum(results_r_is) / len(results_r_is)
    exp_oos = sum(results_r_oos) / len(results_r_oos)
    degradation = (exp_is - exp_oos) / abs(exp_is) * 100 if exp_is != 0 else 0
    print(f"  Dégradation IS→OOS : {degradation:.0f}%")

    if degradation > 70:
        print(f"  ⚠️  Dégradation > 70% — probable overfitting IS")
    elif degradation > 40:
        print(f"  ⚠️  Dégradation modérée — surveiller en forward-test")
    else:
        print(f"  ✓  Dégradation faible — bon signe de robustesse")


if __name__ == "__main__":
    main()
