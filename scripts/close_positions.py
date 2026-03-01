#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
close_positions.py — Ferme les positions ouvertes sur un broker

Usage:
  python scripts/close_positions.py                        # Liste les positions
  python scripts/close_positions.py --close-all            # Ferme tout
  python scripts/close_positions.py --close-id 12345       # Ferme une position
  python scripts/close_positions.py --broker gft_compte2   # Autre broker
"""

import argparse
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run(broker_id: str, close_all: bool, close_id: str):
    from arabesque.config import load_full_config
    from arabesque.broker.factory import create_all_brokers

    settings, secrets, instruments = load_full_config()
    brokers = create_all_brokers(settings, secrets, instruments)

    if broker_id not in brokers:
        print(f"❌ Broker '{broker_id}' non trouvé. Disponibles: {list(brokers.keys())}")
        return False

    broker = brokers[broker_id]
    connected = await broker.connect()
    if not connected:
        print(f"❌ Connexion échouée à {broker_id}")
        return False

    # Lister les positions
    positions = await broker.get_positions()
    if not positions:
        print(f"✅ Aucune position ouverte sur {broker_id}")
        await broker.disconnect()
        return True

    print(f"\n📋 {len(positions)} position(s) ouverte(s) sur {broker_id}:\n")
    for p in positions:
        pnl = getattr(p, 'raw_data', {})
        print(f"  ID: {p.position_id} | {p.symbol} {p.side} "
              f"vol={p.volume:.2f} entry={p.entry_price:.5f} "
              f"SL={p.stop_loss or '—'} TP={p.take_profit or '—'}")

    if close_id:
        # Fermer une position spécifique
        print(f"\n🔄 Fermeture de la position {close_id}...")
        result = await broker.close_position(close_id)
        if result.success:
            print(f"  ✅ Fermée — {result.message}")
        else:
            print(f"  ❌ Échec: {result.message}")

    elif close_all:
        confirm = input(f"\n  Fermer les {len(positions)} positions ? [y/N] > ").strip().lower()
        if confirm not in ("y", "yes", "o", "oui"):
            print("  Annulé.")
            await broker.disconnect()
            return True

        for p in positions:
            print(f"\n🔄 Fermeture {p.position_id} ({p.symbol} {p.side})...")
            result = await broker.close_position(str(p.position_id))
            if result.success:
                print(f"  ✅ Fermée — {result.message}")
            else:
                print(f"  ❌ Échec: {result.message}")
            await asyncio.sleep(1)  # Pause entre les ordres
    else:
        print(f"\n  Utilisez --close-all pour tout fermer, "
              f"ou --close-id <ID> pour une position spécifique")

    await broker.disconnect()
    return True


def main():
    parser = argparse.ArgumentParser(description="Gestion des positions ouvertes")
    parser.add_argument("--broker", default="ftmo_swing_test",
                        help="Broker ID (défaut: ftmo_swing_test)")
    parser.add_argument("--close-all", action="store_true",
                        help="Fermer toutes les positions")
    parser.add_argument("--close-id", default=None,
                        help="Fermer une position spécifique par ID")
    args = parser.parse_args()

    asyncio.run(run(args.broker, args.close_all, args.close_id))


if __name__ == "__main__":
    main()
