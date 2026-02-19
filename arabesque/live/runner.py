"""
Arabesque — Live Runner.

Point d'entrée pour lancer le paper trading ou le live trading.

Usage :
    # Paper trading depuis les parquets locaux
    python -m arabesque.live.runner --mode dry_run --source parquet
    python -m arabesque.live.runner --mode dry_run --source parquet --strategy mean_reversion
    python -m arabesque.live.runner --mode dry_run --source parquet --strategy trend

    # Paper trading connecté à cTrader démo
    python -m arabesque.live.runner --mode dry_run --source ctrader

    # Live trading cTrader
    python -m arabesque.live.runner --mode live

Variables d'environnement :
    CTRADER_HOST           demo.ctraderapi.com | live.ctraderapi.com
    CTRADER_PORT           5035
    CTRADER_CLIENT_ID
    CTRADER_CLIENT_SECRET
    CTRADER_ACCESS_TOKEN
    CTRADER_ACCOUNT_ID
    ARABESQUE_BALANCE          (défaut: 10000)
    ARABESQUE_MAX_DAILY_DD     (défaut: 5.0)
    ARABESQUE_MAX_TOTAL_DD     (défaut: 10.0)
    ARABESQUE_MAX_POSITIONS    (défaut: 10)
    ARABESQUE_MAX_OPEN_RISK_PCT (défaut: 2.0)
    ARABESQUE_RISK_PCT         (défaut: 1.0)
    ARABESQUE_MAX_DAILY_TRADES (défaut: 999 en dry_run, 5 en live)
    TELEGRAM_TOKEN / TELEGRAM_CHAT_ID / NTFY_TOPIC  (optionnel)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)
logger = logging.getLogger("arabesque.live.runner")


def main():
    parser = argparse.ArgumentParser(description="Arabesque Live Trading Runner")
    parser.add_argument(
        "--mode", choices=["dry_run", "live"], default="dry_run",
        help="dry_run=paper trading sans ordres réels, live=ordres réels",
    )
    parser.add_argument(
        "--source", choices=["parquet", "ctrader"], default="parquet",
        help="Source de barres : parquet=replay local, ctrader=stream live",
    )
    parser.add_argument(
        "--instruments", nargs="+", default=None,
        help="Instruments (défaut: 19 viables du dernier pipeline run)",
    )
    parser.add_argument(
        "--start", default=None,
        help="Date de début pour le replay parquet (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end", default=None,
        help="Date de fin pour le replay parquet (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--speed", type=float, default=0.0,
        help="Secondes entre 2 bougies en mode parquet (0=max speed)",
    )
    parser.add_argument(
        "--config", default=None,
        help="Chemin vers arabesque_config.yaml",
    )
    parser.add_argument(
        "--strategy", choices=["trend", "mean_reversion", "combined"], default="trend",
        help="Stratégie de signal : trend | mean_reversion | combined (défaut: trend)",
    )
    args = parser.parse_args()

    # ── Imports ────────────────────────────────────────────
    from arabesque.config import ArabesqueConfig
    from arabesque.broker.adapters import DryRunAdapter
    from arabesque.webhook.orchestrator import Orchestrator
    from arabesque.live.bar_poller import BarPoller, BarPollerConfig, DEFAULT_INSTRUMENTS
    from arabesque.live.parquet_clock import ParquetClock

    # ── Config ─────────────────────────────────────────────
    # max_daily_trades : 999 en dry_run (mean_reversion génère beaucoup), 5 en live
    if args.mode == "dry_run":
        default_max_daily = "999"
    else:
        default_max_daily = "5"

    if args.config:
        cfg = ArabesqueConfig.from_yaml(args.config)
    else:
        cfg = ArabesqueConfig(
            mode=args.mode,
            start_balance=float(os.environ.get("ARABESQUE_BALANCE", "10000")),
            max_daily_dd_pct=float(os.environ.get("ARABESQUE_MAX_DAILY_DD", "5.0")),
            max_total_dd_pct=float(os.environ.get("ARABESQUE_MAX_TOTAL_DD", "10.0")),
            max_positions=int(os.environ.get("ARABESQUE_MAX_POSITIONS", "10")),
            max_open_risk_pct=float(os.environ.get("ARABESQUE_MAX_OPEN_RISK_PCT", "2.0")),
            risk_per_trade_pct=float(os.environ.get("ARABESQUE_RISK_PCT", "1.0")),
            max_daily_trades=int(os.environ.get("ARABESQUE_MAX_DAILY_TRADES", default_max_daily)),
            telegram_token=os.environ.get("TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            ntfy_topic=os.environ.get("NTFY_TOPIC", ""),
        )

    instruments = args.instruments or DEFAULT_INSTRUMENTS

    # ── Signal generator ─────────────────────────────────────
    # CRITIQUE : live_mode=False pour replay parquet (anti-lookahead)
    #           live_mode=True SEULEMENT pour stream ctrader réel
    if args.strategy == "mean_reversion":
        from arabesque.backtest.signal_gen import BacktestSignalGenerator, SignalGenConfig
        # live_mode dépend de la source : False pour parquet (backtest-like), True pour ctrader
        live_mode = (args.source == "ctrader")
        signal_generator = BacktestSignalGenerator(SignalGenConfig(), live_mode=live_mode)
    elif args.strategy == "combined":
        from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
        signal_generator = CombinedSignalGenerator()
    else:  # trend (default)
        from arabesque.backtest.signal_gen_trend import TrendSignalGenerator, TrendSignalConfig
        signal_generator = TrendSignalGenerator(TrendSignalConfig())

    logger.info(f"Strategy: {args.strategy.upper()}")

    # ── Broker ─────────────────────────────────────────────
    if args.mode == "dry_run":
        broker = DryRunAdapter()
        brokers = {"dry_run": broker}
        logger.info("Mode DRY_RUN — aucun ordre réel")
    else:
        from arabesque.broker.ctrader import CTraderAdapter, CTraderConfig
        ctrader_cfg = CTraderConfig(
            host=os.environ.get("CTRADER_HOST", "live.ctraderapi.com"),
            port=int(os.environ.get("CTRADER_PORT", "5035")),
            client_id=os.environ.get("CTRADER_CLIENT_ID", ""),
            client_secret=os.environ.get("CTRADER_CLIENT_SECRET", ""),
            access_token=os.environ.get("CTRADER_ACCESS_TOKEN", ""),
            account_id=int(os.environ.get("CTRADER_ACCOUNT_ID", "0")),
        )
        if not ctrader_cfg.client_id or not ctrader_cfg.access_token:
            logger.error(
                "Variables manquantes: CTRADER_CLIENT_ID, "
                "CTRADER_ACCESS_TOKEN, CTRADER_ACCOUNT_ID"
            )
            sys.exit(1)
        broker = CTraderAdapter(ctrader_cfg)
        brokers = {"ctrader": broker}
        logger.info(f"Mode LIVE — compte {ctrader_cfg.account_id}")

    # ── Orchestrator ──────────────────────────────────────────
    orchestrator = Orchestrator(config=cfg, brokers=brokers)

    # ── Source de barres ─────────────────────────────────────
    logger.info(f"Source: {args.source} | Instruments: {instruments}")

    if args.source == "parquet":
        clock = ParquetClock(
            instruments=instruments,
            start=args.start,
            end=args.end,
            replay_speed=args.speed,
            signal_generator=signal_generator,
        )
        logger.info("Starting ParquetClock replay... (Ctrl+C to stop)")
        # ParquetClock.run() gère déjà KeyboardInterrupt en interne et appelle _print_summary()
        clock.run(orchestrator, blocking=True)

    else:
        # BarPoller (ctrader stream)
        if args.mode == "dry_run":
            from arabesque.broker.ctrader import CTraderAdapter, CTraderConfig
            ctrader_cfg = CTraderConfig(
                host=os.environ.get("CTRADER_HOST", "demo.ctraderapi.com"),
                port=int(os.environ.get("CTRADER_PORT", "5035")),
                client_id=os.environ.get("CTRADER_CLIENT_ID", ""),
                client_secret=os.environ.get("CTRADER_CLIENT_SECRET", ""),
                access_token=os.environ.get("CTRADER_ACCESS_TOKEN", ""),
                account_id=int(os.environ.get("CTRADER_ACCOUNT_ID", "0")),
            )
            stream_adapter = CTraderAdapter(ctrader_cfg)
        else:
            stream_adapter = broker

        poller_cfg = BarPollerConfig(
            instruments=instruments,
            dry_run=(args.mode == "dry_run"),
            use_polling_fallback=True,
            poll_interval_sec=60,
            signal_generator=signal_generator,
        )
        poller = BarPoller(
            ctrader_adapter=stream_adapter,
            orchestrator=orchestrator,
            config=poller_cfg,
        )
        logger.info("Starting BarPoller... (Ctrl+C to stop)")
        try:
            poller.start(blocking=True)
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            poller.stop()


if __name__ == "__main__":
    main()
