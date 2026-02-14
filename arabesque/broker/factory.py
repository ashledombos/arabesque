"""
Arabesque v2 — Broker Factory.

Crée le bon adapter depuis la configuration.
"""

from __future__ import annotations

import logging
from arabesque.broker.adapters import BrokerAdapter, DryRunAdapter

logger = logging.getLogger("arabesque.broker")


def create_broker(config: dict) -> BrokerAdapter:
    """Crée un adapter broker selon le type spécifié dans la config.

    Config attendue :
        {"type": "ctrader", "host": "...", "client_id": "...", ...}
        {"type": "tradelocker", "email": "...", "password": "...", ...}
        {"type": "dry_run"}

    Returns:
        BrokerAdapter connecté (ou prêt à connecter)
    """
    broker_type = config.get("type", "dry_run").lower()

    if broker_type == "ctrader":
        from arabesque.broker.ctrader import CTraderAdapter, CTraderConfig
        ct_config = CTraderConfig(**{
            k: v for k, v in config.items()
            if k in CTraderConfig.__dataclass_fields__
        })
        return CTraderAdapter(ct_config)

    elif broker_type == "tradelocker":
        from arabesque.broker.tradelocker import TradeLockerAdapter, TradeLockerConfig
        tl_config = TradeLockerConfig(**{
            k: v for k, v in config.items()
            if k in TradeLockerConfig.__dataclass_fields__
        })
        return TradeLockerAdapter(tl_config)

    elif broker_type == "dry_run":
        return DryRunAdapter(config)

    else:
        logger.warning(f"Unknown broker type '{broker_type}', defaulting to dry_run")
        return DryRunAdapter(config)


def create_all_brokers(configs: list[dict]) -> dict[str, BrokerAdapter]:
    """Crée et connecte plusieurs brokers.

    Returns:
        Dict {name: BrokerAdapter}
    """
    brokers = {}
    for cfg in configs:
        name = cfg.get("name", cfg.get("type", "unknown"))
        try:
            broker = create_broker(cfg)
            if broker.connect():
                brokers[name] = broker
                logger.info(f"Broker '{name}' connected")
            else:
                logger.error(f"Broker '{name}' failed to connect")
        except Exception as e:
            logger.error(f"Broker '{name}' creation error: {e}")

    return brokers
