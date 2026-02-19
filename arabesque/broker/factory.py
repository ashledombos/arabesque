#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Broker factory: instancie le bon connecteur selon le type déclaré
dans config/settings.yaml (section brokers).
"""

from typing import Dict, Optional
from .base import BaseBroker


def create_broker(broker_id: str, config: dict) -> BaseBroker:
    """
    Crée un broker depuis sa config.

    config doit contenir au moins:
      type: ctrader | tradelocker
      enabled: true/false
      + les champs spécifiques au type

    Les credentials (email, password, access_token, etc.) doivent
    avoir été mergés depuis secrets.yaml avant appel.
    """
    broker_type = config.get("type", "").lower()

    if broker_type == "ctrader":
        from .ctrader import CTraderBroker
        return CTraderBroker(broker_id, config)

    elif broker_type == "tradelocker":
        from .tradelocker import TradeLockerBroker
        return TradeLockerBroker(broker_id, config)

    else:
        raise ValueError(
            f"Unknown broker type '{broker_type}' for broker '{broker_id}'. "
            f"Supported: ctrader, tradelocker"
        )


def create_all_brokers(settings: dict, secrets: dict) -> Dict[str, BaseBroker]:
    """
    Instancie tous les brokers activés depuis settings + secrets.

    settings: contenu de config/settings.yaml
    secrets:  contenu de config/secrets.yaml

    Retourne un dict broker_id -> instance.
    """
    brokers_cfg = settings.get("brokers", {})
    brokers_secrets = secrets  # secrets.yaml est au même niveau que les broker_ids

    result: Dict[str, BaseBroker] = {}

    for broker_id, broker_cfg in brokers_cfg.items():
        if not broker_cfg.get("enabled", True):
            continue

        # Merger les secrets dans la config du broker
        merged = dict(broker_cfg)
        if broker_id in brokers_secrets:
            merged.update(brokers_secrets[broker_id])

        # Ajouter le mapping instruments si disponible
        # (instruments[symbol][broker_id] -> nom broker)
        instruments_cfg = settings.get("instruments", {})
        instruments_mapping = {}
        for symbol, inst_data in instruments_cfg.items():
            if isinstance(inst_data, dict) and broker_id in inst_data:
                instruments_mapping[symbol] = inst_data[broker_id]
        if instruments_mapping:
            merged["instruments_mapping"] = instruments_mapping

        try:
            broker = create_broker(broker_id, merged)
            result[broker_id] = broker
            print(f"[factory] ✅ Broker créé: {broker_id} ({broker_cfg.get('type')})")
        except Exception as e:
            print(f"[factory] ❌ Échec création broker {broker_id}: {e}")

    return result
