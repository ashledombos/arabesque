"""
Arabesque — Live Runner.

Point d'entrée pour lancer le paper trading ou le live trading.

Utilisation :
    # Paper trading (dry_run)
    python -m arabesque.live.runner --mode dry_run

    # Live trading (avec cTrader réel)
    python -m arabesque.live.runner --mode live
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
    parser = argparse.ArgumentParser(
        description="Arabesque Live Trading Runner"
    )
    parser.add_argument(
        "--mode",
        choices=["dry_run", "live"],
        default="dry_run",
        help="dry_run = paper trading, live = ordres réels",
    )
    parser.add_argument(
        "--instruments",
        nargs="+",
        default=None,
        help="Liste d'instruments (défaut: 19 viables du dernier run)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Chemin vers arabesque_config.yaml",
    )
    args = parser.parse_args()

    # ── Config ──────────────────────────────────────────────────────────────
    from arabesque.config import ArabesqueConfig
    from arabesque.broker.ctrader import CTraderAdapter, CTraderConfig
    from arabesque.broker.adapters import DryRunAdapter
    from arabesque.webhook.orchestrator import Orchestrator
    from arabesque.live.bar_poller import BarPoller, BarPollerConfig, DEFAULT_INSTRUMENTS

    if args.config:
        cfg = ArabesqueConfig.from_yaml(args.config)
    else:
        # Fallback : lire depuis les variables d'environnement
        cfg = ArabesqueConfig(
            mode=args.mode,
            start_balance=float(os.environ.get("ARABESQUE_BALANCE", "10000")),
            max_daily_dd_pct=float(os.environ.get("ARABESQUE_MAX_DAILY_DD", "5.0")),
            max_total_dd_pct=float(os.environ.get("ARABESQUE_MAX_TOTAL_DD", "10.0")),
            max_positions=int(os.environ.get("ARABESQUE_MAX_POSITIONS", "3")),
            risk_per_trade_pct=float(os.environ.get("ARABESQUE_RISK_PCT", "1.0")),
            telegram_token=os.environ.get("TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            ntfy_topic=os.environ.get("NTFY_TOPIC", ""),
        )

    # ── Broker ──────────────────────────────────────────────────────────────
    if args.mode == "dry_run":
        broker = DryRunAdapter()
        brokers = {"dry_run": broker}
        logger.info("Mode DRY_RUN — aucun ordre réel ne sera passé")
    else:
        # Credentials depuis variables d'environnement
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

    # ── Orchestrator ────────────────────────────────────────────────────────
    orchestrator = Orchestrator(config=cfg, brokers=brokers)

    # ── Instruments ─────────────────────────────────────────────────────────
    instruments = args.instruments or DEFAULT_INSTRUMENTS
    logger.info(f"Instruments: {instruments}")

    # ── Bar Poller ──────────────────────────────────────────────────────────
    poller_cfg = BarPollerConfig(
        instruments=instruments,
        dry_run=(args.mode == "dry_run"),
        use_polling_fallback=True,
        poll_interval_sec=60,
    )
    poller = BarPoller(
        ctrader_adapter=broker if args.mode == "live" else None,
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
