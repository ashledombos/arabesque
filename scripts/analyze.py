#!/usr/bin/env python3
"""
Arabesque v2 — CLI Audit Analyzer.

Analyse les logs JSONL du paper trading / live.

Usage :
    # Rapport de performance
    python scripts/analyze.py

    # Derniers 7 jours
    python scripts/analyze.py --days 7

    # Calibration des guards
    python scripts/analyze.py --guards

    # Timeline
    python scripts/analyze.py --timeline

    # Résumé quotidien
    python scripts/analyze.py --daily

    # Export CSV
    python scripts/analyze.py --csv trades.csv

    # Tout
    python scripts/analyze.py --all
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arabesque.analysis.analyzer import AuditAnalyzer


def main():
    parser = argparse.ArgumentParser(
        description="Arabesque v2 — Audit Log Analyzer",
    )

    parser.add_argument("--dir", type=str, default="logs/audit",
                        help="Répertoire des logs audit (default: logs/audit)")
    parser.add_argument("--days", type=int, default=0,
                        help="Limiter aux N derniers jours (0 = tout)")
    parser.add_argument("--balance", type=float, default=100_000,
                        help="Balance initiale (default: 100000)")
    parser.add_argument("--risk", type=float, default=0.5,
                        help="Risque par trade en %% (default: 0.5)")

    # Reports
    parser.add_argument("--performance", action="store_true", default=True,
                        help="Rapport de performance (default)")
    parser.add_argument("--guards", action="store_true",
                        help="Calibration des guards")
    parser.add_argument("--timeline", action="store_true",
                        help="Timeline des événements")
    parser.add_argument("--daily", action="store_true",
                        help="Résumé quotidien")
    parser.add_argument("--csv", type=str, default=None,
                        help="Export CSV des trades")
    parser.add_argument("--all", action="store_true",
                        help="Tous les rapports")

    args = parser.parse_args()

    # Charger les logs
    analyzer = AuditAnalyzer(args.dir)
    analyzer.load(days_back=args.days)

    n_decisions = len(analyzer.decisions)
    n_cf = len(analyzer.counterfactuals)
    print(f"\nChargé : {n_decisions} decisions, {n_cf} counterfactuels")

    if n_decisions == 0:
        print("Aucun log trouvé. Le paper trading a-t-il été lancé ?")
        print(f"  Répertoire cherché : {args.dir}")
        sys.exit(0)

    # Rapports
    if args.all or args.performance:
        print(analyzer.performance_report(args.balance, args.risk))

    if args.all or args.guards:
        print(analyzer.guard_calibration_report())

    if args.all or args.daily:
        print(analyzer.daily_summary())

    if args.all or args.timeline:
        print(analyzer.timeline())

    if args.csv:
        result = analyzer.export_trades_csv(args.csv)
        print(result)


if __name__ == "__main__":
    main()
