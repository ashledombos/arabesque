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
    python -m arabesque check     --account ftmo_swing_test
    python -m arabesque positions --account ftmo_swing_test

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
    from arabesque.data.store import load_ohlc, _categorize

    strategy = getattr(args, "strategy", "extension")

    # Charger le bon signal generator + timeframe
    if strategy == "fouette":
        from arabesque.strategies.fouette.signal import FouetteSignalGenerator, FouetteConfig
        from arabesque.core.guards import ExecConfig
        sig_gen = FouetteSignalGenerator(FouetteConfig())
        timeframe = "min1"  # Fouetté = ORB M1
        # Sur M1, ATR ~$0.80 sur XAUUSD vs ~$15 en H1.
        # Les seuils H1 (spread/slip < 0.10-0.15×ATR) rejettent 97% des signaux M1.
        # 0.5×ATR M1 ≈ $0.40 → filtre les moments illiquides sans rejeter le spread normal.
        exec_cfg = ExecConfig(max_spread_atr=0.5, max_slippage_atr=0.5)
    else:
        from arabesque.strategies.extension.signal import ExtensionSignalGenerator, ExtensionConfig
        sig_gen = ExtensionSignalGenerator(ExtensionConfig())
        timeframe = "1h"    # Extension = trend H1
        exec_cfg = None

    # Override timeframe si --interval est spécifié
    interval_override = getattr(args, "interval", None)
    if interval_override:
        timeframe = interval_override

    # Résolution des instruments : --universe ou liste explicite
    instruments = _resolve_instruments(args)
    period = getattr(args, "period", "730d")

    if not instruments:
        print("Usage : python -m arabesque run --mode backtest BTCUSD XAUUSD", file=sys.stderr)
        print("    ou : python -m arabesque run --mode backtest --universe crypto", file=sys.stderr)
        return 1

    cfg = BacktestConfig(
        risk_per_trade_pct=float(getattr(args, "risk", 0.40)),
        verbose=getattr(args, "verbose", False),
    )

    # Boucle multi-instruments avec collecte des résultats
    results: dict[str, object] = {}
    for inst in instruments:
        try:
            runner = BacktestRunner(cfg, signal_generator=sig_gen, exec_config=exec_cfg)
            df = load_ohlc(inst, period=period, interval=timeframe)
            if df is None or len(df) < 100:
                print(f"  Données insuffisantes pour {inst}", file=sys.stderr)
                continue
            df = sig_gen.prepare(df)
            result = runner.run(df, inst)
            results[inst] = result
            print(result.report)
        except Exception as e:
            print(f"  Erreur sur {inst}: {e}", file=sys.stderr)
            continue

    # Synthèse multi-instruments si > 1 instrument
    if len(results) > 1:
        _print_backtest_synthesis(results, _categorize)

    return 0


def _resolve_instruments(args: argparse.Namespace) -> list[str]:
    """Résout la liste d'instruments depuis --universe ou la liste explicite."""
    from pathlib import Path
    instruments = getattr(args, "instruments", None) or []

    universe = getattr(args, "universe", None)
    if universe:
        universes_path = Path("config/universes.yaml")
        if universes_path.exists():
            import yaml
            with open(universes_path) as f:
                universes = yaml.safe_load(f) or {}
            if universe in universes:
                instruments = universes[universe]
            else:
                available = ", ".join(universes.keys())
                print(f"Univers inconnu : {universe}. Disponibles : {available}", file=sys.stderr)
                return []
        else:
            print("config/universes.yaml introuvable", file=sys.stderr)
            return []

    return instruments


def _print_backtest_synthesis(results: dict, categorize_fn) -> None:
    """Affiche une synthèse agrégée par catégorie + total."""
    print(f"\n{'='*70}")
    print("  SYNTHÈSE MULTI-INSTRUMENTS")
    print(f"{'='*70}")
    print(f"  {'Instrument':<12s} {'Cat':<12s} {'Trades':>7s} {'WR':>6s} "
          f"{'Exp(R)':>8s} {'PF':>6s} {'MaxDD':>7s} {'Disq':>5s}")
    print(f"  {'-'*63}")

    # Par catégorie
    by_cat: dict[str, list] = {}
    for inst, result in results.items():
        m = result.metrics
        cat = categorize_fn(inst)
        by_cat.setdefault(cat, []).append((inst, m))
        print(f"  {inst:<12s} {cat:<12s} {m.n_trades:>7d} {m.win_rate:>5.0%} "
              f"{m.expectancy_r:>+7.3f} {m.profit_factor:>5.2f} "
              f"{m.max_dd_pct:>6.1f}% {m.n_disqualifying_days:>5d}")

    # Sous-totaux par catégorie
    print(f"  {'-'*63}")
    total_trades = 0
    total_r = 0.0
    total_wins = 0
    for cat, items in sorted(by_cat.items()):
        cat_trades = sum(m.n_trades for _, m in items)
        cat_wins = sum(int(m.win_rate * m.n_trades) for _, m in items)
        cat_r = sum(m.expectancy_r * m.n_trades for _, m in items)
        cat_wr = cat_wins / cat_trades if cat_trades > 0 else 0
        cat_exp = cat_r / cat_trades if cat_trades > 0 else 0
        total_trades += cat_trades
        total_r += cat_r
        total_wins += cat_wins
        print(f"  {'[' + cat + ']':<24s} {cat_trades:>7d} {cat_wr:>5.0%} "
              f"{cat_exp:>+7.3f} {'':>6s} {'':>7s} {'':>5s}")

    # Total global
    if total_trades > 0:
        global_wr = total_wins / total_trades
        global_exp = total_r / total_trades
        print(f"  {'-'*63}")
        print(f"  {'TOTAL':<24s} {total_trades:>7d} {global_wr:>5.0%} "
              f"{global_exp:>+7.3f}")
    print(f"{'='*70}")

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


def cmd_positions(args: argparse.Namespace) -> int:
    """Affiche les positions ouvertes et ordres en attente d'un compte."""
    import asyncio
    from arabesque.config import load_full_config
    from arabesque.broker.factory import create_broker

    _setup_logging("WARNING")  # Silencieux sauf erreurs

    settings, secrets, instruments = load_full_config()
    account_id = args.account

    async def _run():
        broker = create_broker(account_id, settings, secrets, instruments)
        try:
            await broker.connect()

            positions = await broker.get_positions()
            print(f"Positions ouvertes ({len(positions)}) :")
            for pos in positions:
                pnl = f"{pos.profit:+.2f}" if pos.profit is not None else "?"
                print(f"  {pos.symbol:10} {pos.side.value:5} "
                      f"vol={pos.volume} entry={pos.entry_price} "
                      f"sl={pos.stop_loss} tp={pos.take_profit} pnl={pnl}")

            orders = await broker.get_pending_orders()
            print(f"\nOrdres en attente ({len(orders)}) :")
            for order in orders:
                print(f"  {order.symbol:10} {order.side.value:5} "
                      f"{order.order_type.value} @ {order.entry_price} "
                      f"vol={order.volume} sl={order.stop_loss} tp={order.take_profit}")

            info = await broker.get_account_info()
            if info:
                print(f"\nCompte :")
                print(f"  Balance : {info.balance:.2f} {info.currency}")
                print(f"  Equity  : {info.equity:.2f} {info.currency}")
                print(f"  P&L     : {info.equity - info.balance:+.2f}")
                print(f"  Margin  : {info.margin_used:.2f} / {info.margin_free:.2f}")

            await broker.disconnect()
        except Exception as e:
            print(f"Erreur : {e}", file=sys.stderr)
            return 1
        return 0

    return asyncio.run(_run())


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
    run_p.add_argument("--interval", default=None,
                       help="Override timeframe (ex: 4h, 15m). Par défaut : selon la stratégie")
    run_p.add_argument("--risk", type=float, default=0.40, help="Risque par trade (%)")
    run_p.add_argument("--verbose", "-v", action="store_true")
    run_p.add_argument("--universe", default=None,
                       help="Univers d'instruments (config/universes.yaml)")
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

    # ── positions ──
    pos_p = subparsers.add_parser("positions", help="Afficher positions et ordres en attente")
    pos_p.add_argument("--account", required=True, help="ID du compte (config/accounts.yaml)")
    pos_p.set_defaults(func=cmd_positions)

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
