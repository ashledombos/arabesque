"""
Arabesque — Live Runner.

Point d'entrée pour lancer le paper trading ou le live trading.

Usage :
    # Paper trading depuis les parquets locaux (pas besoin de cTrader)
    python -m arabesque.live.runner --mode dry_run --source parquet

    # Paper trading connecté à cTrader démo (barres réelles)
    python -m arabesque.live.runner --mode dry_run --source ctrader

    # Live trading cTrader (ordres réels)
    python -m arabesque.live.runner --mode live

Variables d'environnement pour le mode live/ctrader :
    CTRADER_HOST           demo.ctraderapi.com | live.ctraderapi.com
    CTRADER_PORT           5035
    CTRADER_CLIENT_ID
    CTRADER_CLIENT_SECRET
    CTRADER_ACCESS_TOKEN
    CTRADER_ACCOUNT_ID

Variables Arabesque :
    ARABESQUE_BALANCE          solde de départ (défaut: 10000)
    ARABESQUE_MAX_DAILY_DD     limite DD journalier % (défaut: 5.0)
    ARABESQUE_MAX_TOTAL_DD     limite DD total % (défaut: 10.0)
    ARABESQUE_MAX_POSITIONS    filet absolu positions simultanées (défaut: 10)
    ARABESQUE_MAX_OPEN_RISK_PCT % start_balance max en risque ouvert (défaut: 2.0)
    ARABESQUE_RISK_PCT         risque par trade % (défaut: 1.0)
    TELEGRAM_TOKEN             (optionnel)
    TELEGRAM_CHAT_ID           (optionnel)
    NTFY_TOPIC                 (optionnel)
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
    args = parser.parse_args()

    # ── Imports ──────────────────────────────────────────────────
    from arabesque.config import ArabesqueConfig
    from arabesque.broker.adapters import DryRunAdapter
    from arabesque.webhook.orchestrator import Orchestrator
    from arabesque.live.bar_poller import BarPoller, BarPollerConfig, DEFAULT_INSTRUMENTS
    from arabesque.live.parquet_clock import ParquetClock

    # ── Config ───────────────────────────────────────────────────
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
            telegram_token=os.environ.get("TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            ntfy_topic=os.environ.get("NTFY_TOPIC", ""),
        )

    instruments = args.instruments or DEFAULT_INSTRUMENTS

    # ── Broker ───────────────────────────────────────────────────
    if args.mode == "dry_run":
        broker = DryRunAdapter()
        brokers = {"dry_run": broker}
        logger.info("Mode DRY_RUN — aucun ordre réel")
    else:
        # Mode live : cTrader obligatoire
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

    # ── Orchestrator ───────────────────────────────────────────────
    orchestrator = Orchestrator(config=cfg, brokers=brokers)

    # ── Source de barres ───────────────────────────────────────────
    logger.info(f"Source: {args.source} | Instruments: {instruments}")

    try:
        if args.source == "parquet":
            # Replay depuis les parquets locaux — pas besoin de cTrader
            clock = ParquetClock(
                instruments=instruments,
                start=args.start,
                end=args.end,
                replay_speed=args.speed,
            )
            logger.info("Starting ParquetClock replay... (Ctrl+C to stop)")
            clock.run(orchestrator, blocking=True)

        else:
            # Stream live cTrader
            if args.mode == "dry_run":
                # dry_run + ctrader source : connexion démo cTrader
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
                stream_adapter = broker  # déjà le CTraderAdapter live

            poller_cfg = BarPollerConfig(
                instruments=instruments,
                dry_run=(args.mode == "dry_run"),
                use_polling_fallback=True,
                poll_interval_sec=60,
            )
            poller = BarPoller(
                ctrader_adapter=stream_adapter,
                orchestrator=orchestrator,
                config=poller_cfg,
            )
            logger.info("Starting BarPoller... (Ctrl+C to stop)")
            poller.start(blocking=True)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
