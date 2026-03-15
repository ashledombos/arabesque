#!/usr/bin/env python3
"""
Arabesque — Point d'entrée CLI unifié.

Usage :
    python -m arabesque run   --strategy extension --mode backtest
    python -m arabesque run   --strategy extension --mode dryrun
    python -m arabesque run   --strategy extension --mode live --account ftmo_swing_test
    python -m arabesque screen --strategy extension --list crypto
    python -m arabesque fetch  --from 2024-01-01 --to 2026-12-31
    python -m arabesque analyze [--days 7] [--all]
    python -m arabesque check  --account ftmo_swing_test

Modes :
    backtest  — Replay bar-by-bar sur parquet historique (IS+OOS)
    dryrun    — Replay parquet en temps quasi-réel, sans ordres réels
    live      — Moteur live cTrader, ordres réels sur le compte spécifié

Règle de sécurité (comptes protected) :
    Le mode live refuse de trader sur un compte avec protected: true
    dans config/accounts.yaml sans l'argument --force-live explicite.
"""

from __future__ import annotations

import argparse
import logging
import sys


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_run(args: argparse.Namespace) -> int:
    """Exécute une stratégie dans le mode spécifié."""
    _setup_logging(args.log_level)

    if args.mode == "live":
        return _run_live(args)
    elif args.mode == "dryrun":
        return _run_dryrun(args)
    elif args.mode == "backtest":
        return _run_backtest(args)
    else:
        print(f"Mode inconnu : {args.mode}", file=sys.stderr)
        return 1


def _run_live(args: argparse.Namespace) -> int:
    """Lance le moteur live."""
    import asyncio
    from pathlib import Path
    import yaml

    # Vérification compte protected
    accounts_path = Path("config/accounts.yaml")
    if accounts_path.exists():
        with open(accounts_path) as f:
            accounts_cfg = yaml.safe_load(f) or {}
        account_id = args.account
        account = accounts_cfg.get("accounts", {}).get(account_id, {})
        if account.get("protected", False) and not args.force_live:
            print(
                f"\n🔴 REFUS — Le compte '{account_id}' est marqué protected: true\n"
                f"   Label : {account.get('label', 'compte protégé')}\n"
                f"   Pour forcer (DANGER) : ajoutez --force-live\n",
                file=sys.stderr,
            )
            return 1

    from arabesque.execution.live import LiveEngine
    engine = LiveEngine.from_config(dry_run=getattr(args, "dry_run", False))

    async def _run():
        await engine.start()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    return 0


def _run_dryrun(args: argparse.Namespace) -> int:
    """Lance le replay parquet dry-run (via l'engine.py --source parquet)."""
    import asyncio
    from arabesque.execution.live import LiveEngine
    engine = LiveEngine.from_config(dry_run=True)

    async def _run():
        await engine.start()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    return 0

def _run_backtest(args: argparse.Namespace) -> int:
    """Lance un backtest IS+OOS."""
    from arabesque.execution.backtest import BacktestRunner, BacktestConfig
    from arabesque.data.store import load_ohlc

    strategy = getattr(args, "strategy", "extension")

    # Charger le bon signal generator + timeframe
    if strategy == "fouette":
        from arabesque.strategies.fouette.signal import FouetteSignalGenerator, FouetteConfig
        sig_gen = FouetteSignalGenerator(FouetteConfig())
        timeframe = "min1"  # Fouetté = ORB M1
    else:
        from arabesque.strategies.extension.signal import ExtensionSignalGenerator, ExtensionConfig
        sig_gen = ExtensionSignalGenerator(ExtensionConfig())
        timeframe = "1h"    # Extension = trend H1

    cfg = BacktestConfig(
        risk_per_trade_pct=float(getattr(args, "risk", 0.40)),
        verbose=getattr(args, "verbose", False),
    )
    runner = BacktestRunner(cfg, signal_generator=sig_gen)

    instruments = getattr(args, "instruments", None) or []
    period = getattr(args, "period", "730d")

    if not instruments:
        print("Usage : python -m arabesque run --mode backtest BTCUSD XAUUSD", file=sys.stderr)
        return 1

    for inst in instruments:
        df = load_ohlc(inst, period=period, interval=timeframe)
        if df is None or len(df) < 100:
            print(f"⚠  Données insuffisantes pour {inst}")
            continue
        df = sig_gen.prepare(df)
        result = runner.run(df, inst)
        print(result.report)

    return 0

def cmd_screen(args: argparse.Namespace) -> int:
    """Lance le pipeline de screening multi-instruments."""
    _setup_logging(args.log_level)
    from arabesque.analysis.pipeline import Pipeline, PipelineConfig
    cfg = PipelineConfig(
        strategy=getattr(args, "strategy", "trend"),
        verbose=getattr(args, "verbose", False),
        period=getattr(args, "period", "730d"),
    )
    Pipeline(cfg).run()
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """Télécharge/met à jour les données Parquet."""
    _setup_logging(args.log_level)
    from arabesque.data.fetch import main as fetch_main
    fetch_main()
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """Analyse les logs JSONL d'un run live/dry-run."""
    _setup_logging(args.log_level)
    # Déléguer au script analyze.py existant
    import scripts.analyze as analyze_mod
    analyze_mod.main(args)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Teste la connectivité broker."""
    _setup_logging(args.log_level)
    print(f"Test de connectivité pour le compte : {getattr(args, 'account', 'non spécifié')}")
    # TODO: implémenter test_connectivity
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arabesque",
        description="Arabesque — Système de trading algorithmique",
    )
    parser.add_argument("--log-level", default="INFO", help="Niveau de log")

    subparsers = parser.add_subparsers(dest="command", help="Commande")

    # ── run ──
    run_p = subparsers.add_parser("run", help="Exécuter une stratégie")
    run_p.add_argument("--strategy", default="extension", help="Stratégie (ex: extension)")
    run_p.add_argument("--mode", choices=["backtest", "dryrun", "live"], required=True)
    run_p.add_argument("--preset", default="default", help="Preset de paramètres")
    run_p.add_argument("--account", default=None, help="ID du compte (mode live)")
    run_p.add_argument("--force-live", action="store_true",
                       help="Forcer le live sur un compte protected (DANGER)")
    run_p.add_argument("--from", dest="start", default=None, help="Date de début (YYYY-MM-DD)")
    run_p.add_argument("--to", dest="end", default=None, help="Date de fin (YYYY-MM-DD)")
    run_p.add_argument("--period", default="730d", help="Période backtest (ex: 730d)")
    run_p.add_argument("--risk", type=float, default=0.40, help="Risque par trade (%)")
    run_p.add_argument("--verbose", "-v", action="store_true")
    run_p.add_argument("instruments", nargs="*", help="Instruments (mode backtest)")
    run_p.set_defaults(func=cmd_run)

    # ── screen ──
    screen_p = subparsers.add_parser("screen", help="Screening multi-instruments")
    screen_p.add_argument("--strategy", default="extension")
    screen_p.add_argument("--list", choices=["crypto", "fx", "metals", "all"], default="all")
    screen_p.add_argument("--period", default="730d")
    screen_p.add_argument("--verbose", "-v", action="store_true")
    screen_p.set_defaults(func=cmd_screen)

    # ── fetch ──
    fetch_p = subparsers.add_parser("fetch", help="Télécharger/mettre à jour les données")
    fetch_p.add_argument("--from", dest="start", default="2024-01-01")
    fetch_p.add_argument("--to", dest="end", default=None)
    fetch_p.add_argument("--instrument", default=None, help="Instrument spécifique")
    fetch_p.set_defaults(func=cmd_fetch)

    # ── analyze ──
    analyze_p = subparsers.add_parser("analyze", help="Analyser les logs live")
    analyze_p.add_argument("--days", type=int, default=None)
    analyze_p.add_argument("--all", action="store_true")
    analyze_p.add_argument("--csv", default=None)
    analyze_p.set_defaults(func=cmd_analyze)

    # ── check ──
    check_p = subparsers.add_parser("check", help="Tester la connectivité broker")
    check_p.add_argument("--account", required=True)
    check_p.set_defaults(func=cmd_check)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
