#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_order_flow.py — Test du cycle de vie complet d'un ordre sur cTrader

Séquence :
  1. Connexion au broker
  2. Place un ordre MARKET (micro volume)
  3. Attend 3s, vérifie la position ouverte
  4. Modifie le SL (amend)
  5. Attend 3s
  6. Ferme la position
  7. Rapport final

⚠️  Ce script PLACE DE VRAIS ORDRES sur le compte configuré.
    Il utilise le volume minimum (0.01 lots) et ferme immédiatement,
    mais il y aura un impact (spread + éventuels frais) sur le compte.

Usage:
  python scripts/test_order_flow.py [--symbol BTCUSD] [--broker ftmo_swing_test] [--yes]
"""

import argparse
import asyncio
import sys
import os

# Ajouter le répertoire racine au path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run_test(broker_id: str, symbol: str, auto_confirm: bool):
    from arabesque.config import load_full_config
    from arabesque.broker.factory import create_all_brokers
    from arabesque.broker.base import OrderRequest, OrderSide, OrderType

    print("=" * 60)
    print("  ARABESQUE — Test cycle de vie d'un ordre")
    print("=" * 60)
    print(f"  Broker:  {broker_id}")
    print(f"  Symbol:  {symbol}")
    print(f"  Volume:  0.01 lots (minimum)")
    print(f"  Action:  MARKET BUY → amend SL → close")
    print()
    print("  ⚠️  Ce test va placer un VRAI ordre sur votre compte.")
    print("  Le coût estimé est le spread × volume minimum,")
    print("  soit quelques centimes à quelques dollars selon l'instrument.")
    print("=" * 60)

    if not auto_confirm:
        answer = input("\n  Continuer ? [y/N] > ").strip().lower()
        if answer not in ("y", "yes", "o", "oui"):
            print("  ❌ Annulé.")
            return False

    # Avertissement si le live engine tourne déjà
    print("\n  ⚠️  Note: si le live engine tourne en parallèle (python -m arabesque.live.engine),")
    print("  les deux connexions cTrader au même compte peuvent interférer.")
    print("  Recommandation: arrêter le live engine avant ce test.")
    print()

    # 1. Charger config et connecter
    print("[1/7] Chargement de la configuration...")
    settings, secrets, instruments = load_full_config()

    print("[2/7] Connexion au broker...")
    brokers = create_all_brokers(settings, secrets, instruments)

    if broker_id not in brokers:
        print(f"  ❌ Broker '{broker_id}' non trouvé. Disponibles: {list(brokers.keys())}")
        return False

    broker = brokers[broker_id]
    connected = await broker.connect()
    if not connected:
        print("  ❌ Connexion échouée.")
        return False
    print(f"  ✅ Connecté à {broker_id}")

    # Vérifier le symbole
    mapping = broker.config.get("instruments_mapping", {})
    broker_sym = mapping.get(symbol)
    if not broker_sym:
        print(f"  ❌ {symbol} non mappé pour {broker_id}.")
        print(f"     Symboles disponibles: {list(mapping.keys())[:10]}...")
        return False

    # Récupérer les infos du symbole
    sym_info = await broker.get_symbol_info(broker_sym)
    if not sym_info:
        print(f"  ❌ Impossible de résoudre {broker_sym} en ID cTrader.")
        return False
    print(f"  ✅ Symbole résolu: {symbol} → {broker_sym} (ID: {sym_info.broker_symbol})")

    # Récupérer le prix actuel
    await asyncio.sleep(1)
    account = await broker.get_account_info()
    print(f"  💰 Balance: {account.balance:.2f} {account.currency}")

    # 3. Placer un ordre MARKET BUY
    print(f"\n[3/7] Placement: MARKET BUY {symbol} 0.01 lots...")
    order = OrderRequest(
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        volume=0.01,
        broker_symbol=broker_sym,
        label="test_flow",
        comment="arabesque_test",
    )
    result = await broker.place_order(order)

    if not result.success:
        print(f"  ❌ Échec placement: {result.message}")
        await broker.disconnect()
        return False

    position_id = result.order_id
    print(f"  ✅ Ordre placé — ID retourné: {position_id}")
    print(f"     Message: {result.message}")

    # 4. Vérifier la position — récupérer le vrai positionId
    print(f"\n[4/7] Attente 3s puis vérification de la position...")
    await asyncio.sleep(3)

    positions = await broker.get_positions()
    print(f"  Positions ouvertes: {len(positions)}")

    # Trouver la position par ID ou par symbole+label récent
    matching = None
    for p in positions:
        if str(p.position_id) == str(position_id):
            matching = p
            break
    
    if not matching:
        # Fallback: chercher par symbole parmi les plus récentes
        sym_positions = [p for p in positions if p.symbol == symbol or p.symbol == broker_sym]
        if sym_positions:
            matching = sym_positions[-1]  # la plus récente
            print(f"  ⚠️  Position {position_id} non trouvée par ID exact, "
                  f"utilisation de la dernière {symbol}: positionId={matching.position_id}")

    if matching:
        use_id = str(matching.position_id)
        print(f"  ✅ Position trouvée: {matching.symbol} {matching.side} "
              f"vol={matching.volume} entry={matching.entry_price:.5f} "
              f"SL={matching.stop_loss or 'none'} TP={matching.take_profit or 'none'} "
              f"positionId={use_id}")
    else:
        use_id = str(position_id)
        print(f"  ⚠️  Aucune position trouvée dans la liste ({len(positions)} positions)")
        print(f"     Tentative avec l'ID retourné: {use_id}")

    # 5. Modifier le SL
    print(f"\n[5/7] Attente 3s puis modification du SL...")
    await asyncio.sleep(3)

    # Calculer un SL raisonnable (50 pips en dessous du prix)
    entry = matching.entry_price if matching else 0
    if not entry:
        tick = broker.get_last_tick(symbol) if hasattr(broker, 'get_last_tick') else None
        if tick:
            entry = tick.bid

    if entry > 0:
        # SL à environ 0.5% en dessous du prix (raisonnable pour un test)
        sl_offset = round(entry * 0.005, sym_info.digits)
        new_sl = round(entry - sl_offset, sym_info.digits)
        print(f"  Nouveau SL: {new_sl} (entry={entry:.5f}, -0.5% = -{sl_offset})")

        amend_result = await broker.amend_position_sltp(
            use_id, stop_loss=new_sl
        )
        if amend_result.success:
            print(f"  ✅ SL modifié — {amend_result.message}")
        else:
            print(f"  ❌ Échec modification SL: {amend_result.message}")
    else:
        print(f"  ⚠️  Impossible de calculer le SL (pas de prix d'entrée)")

    # 6. Fermer la position
    print(f"\n[6/7] Attente 3s puis fermeture de la position (ID={use_id})...")
    await asyncio.sleep(3)

    close_result = await broker.close_position(use_id)
    if close_result.success:
        print(f"  ✅ Position fermée — {close_result.message}")
    else:
        print(f"  ❌ Échec fermeture: {close_result.message}")
        print(f"     Tentative alternative: ordre opposé MARKET SELL...")
        # Fallback: placer un ordre inverse
        close_order = OrderRequest(
            symbol=symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            volume=0.01,
            broker_symbol=broker_sym,
            label="test_close",
            comment="arabesque_test_close",
        )
        close_result2 = await broker.place_order(close_order)
        if close_result2.success:
            print(f"  ✅ Fermée via ordre inverse — {close_result2.message}")
        else:
            print(f"  ❌ ATTENTION: position non fermée! ID={use_id}")
            print(f"     Fermez manuellement dans la plateforme.")

    # 7. Rapport
    print(f"\n[7/7] Vérification finale...")
    await asyncio.sleep(2)
    account_after = await broker.get_account_info()
    if account_after:
        delta = account_after.balance - account.balance
        print(f"  💰 Balance: {account_after.balance:.2f} {account_after.currency} "
              f"(delta: {delta:+.2f})")

    await broker.disconnect()

    print("\n" + "=" * 60)
    print("  TEST TERMINÉ")
    print("=" * 60)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Test cycle de vie ordre sur cTrader"
    )
    parser.add_argument(
        "--symbol", default="BTCUSD",
        help="Symbole à trader (défaut: BTCUSD)"
    )
    parser.add_argument(
        "--broker", default="ftmo_swing_test",
        help="Broker ID (défaut: ftmo_swing_test)"
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompt"
    )
    args = parser.parse_args()

    success = asyncio.run(run_test(args.broker, args.symbol, args.yes))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
