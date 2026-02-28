#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_connectivity.py — Vérifie la connectivité et le mapping des instruments

Tests effectués (NON destructifs, aucun ordre placé) :
  1. Connexion à tous les brokers configurés
  2. Vérification du mapping instruments.yaml vs symboles réels
  3. Détection des symboles mappés mais inexistants sur le broker
  4. Détection des symboles tradables mais non mappés
  5. Vérification des infos de compte (balance, equity)
  6. Test de récupération d'historique
  7. Rapport complet

Usage:
  python scripts/test_connectivity.py [--verbose]
"""

import argparse
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run_test(verbose: bool):
    from arabesque.config import load_full_config
    from arabesque.broker.factory import create_all_brokers

    print("=" * 70)
    print("  ARABESQUE — Test de connectivité et mapping instruments")
    print("=" * 70)
    print()

    # 1. Charger config
    print("[1] Chargement de la configuration...")
    settings, secrets, instruments = load_full_config()

    brokers_cfg = settings.get("brokers", {})
    enabled_brokers = {k: v for k, v in brokers_cfg.items() if v.get("enabled", True)}
    print(f"  Brokers configurés: {list(enabled_brokers.keys())}")

    followed = [sym for sym, data in instruments.items()
                if isinstance(data, dict) and data.get("follow", False)]
    print(f"  Instruments suivis: {len(followed)}")
    print()

    # 2. Créer et connecter les brokers
    print("[2] Connexion aux brokers...")
    brokers = create_all_brokers(settings, secrets, instruments)

    connected_brokers = {}
    for broker_id, broker in brokers.items():
        try:
            ok = await broker.connect()
            mapping_count = len(broker.config.get("instruments_mapping", {}))
            if ok:
                connected_brokers[broker_id] = broker
                print(f"  ✅ {broker_id}: connecté ({mapping_count} instruments mappés)")
            else:
                print(f"  ❌ {broker_id}: connexion échouée")
        except Exception as e:
            print(f"  ❌ {broker_id}: {e}")
    print()

    # 3. Pour chaque broker, vérifier les mappings
    all_issues = []

    for broker_id, broker in connected_brokers.items():
        print(f"[3] Vérification {broker_id}...")
        mapping = broker.config.get("instruments_mapping", {})
        broker_type = brokers_cfg.get(broker_id, {}).get("type", "?")

        # Récupérer les symboles réels du broker
        try:
            real_symbols = await broker.get_symbols()
            real_names = {s.symbol for s in real_symbols}
            print(f"  Symboles réels sur le broker: {len(real_names)}")
        except Exception as e:
            print(f"  ⚠️  Impossible de charger les symboles: {e}")
            real_names = set()
            real_symbols = []

        # Vérifier chaque mapping
        ok_count = 0
        missing_count = 0
        for unified_sym, broker_sym in mapping.items():
            if unified_sym not in followed:
                continue  # Pas suivi, on ignore

            # Vérifier si le symbole broker existe
            found = broker_sym in real_names
            if not found:
                # Chercher par ID numérique
                try:
                    sid = int(broker_sym)
                    found = any(str(s.broker_symbol) == str(sid) for s in real_symbols)
                except ValueError:
                    pass

            if found:
                ok_count += 1
                if verbose:
                    print(f"    ✅ {unified_sym} → {broker_sym}")
            else:
                missing_count += 1
                issue = f"{broker_id}: {unified_sym} → {broker_sym} NON TROUVÉ"
                all_issues.append(issue)
                print(f"    ❌ {unified_sym} → {broker_sym} NON TROUVÉ sur {broker_id}")

        print(f"  Mapping: {ok_count} OK, {missing_count} manquants "
              f"sur {ok_count + missing_count} suivis")

        # Symboles suivis mais non mappés pour ce broker
        unmapped = []
        for sym in followed:
            if sym not in mapping:
                unmapped.append(sym)
        if unmapped and verbose:
            print(f"  ℹ️  Non mappés pour {broker_id}: {', '.join(unmapped[:10])}"
                  + (f"... (+{len(unmapped)-10})" if len(unmapped) > 10 else ""))

        # 4. Info compte
        try:
            info = await broker.get_account_info()
            if info:
                print(f"  💰 Balance: {info.balance:.2f} {info.currency} | "
                      f"Equity: {info.equity:.2f} | "
                      f"Leverage: 1:{info.leverage}")
            else:
                print(f"  ⚠️  Pas d'info compte")
        except Exception as e:
            print(f"  ⚠️  Erreur info compte: {e}")

        # 5. Test historique (seulement pour cTrader)
        if broker_type == "ctrader":
            test_sym = "EURUSD" if "EURUSD" in mapping else next(iter(mapping), None)
            if test_sym:
                try:
                    bars = await broker.get_history(test_sym, "H1", 5)
                    if bars:
                        print(f"  📊 Historique {test_sym}: {len(bars)} barres H1 OK "
                              f"(dernière: {bars[-1].get('close', '?')})")
                    else:
                        print(f"  ⚠️  Historique {test_sym}: 0 barres retournées")
                except Exception as e:
                    print(f"  ❌ Historique {test_sym}: {e}")

        print()

    # Rapport final
    print("=" * 70)
    print("  RAPPORT FINAL")
    print("=" * 70)
    print(f"  Brokers connectés: {len(connected_brokers)}/{len(brokers)}")
    print(f"  Instruments suivis: {len(followed)}")

    if all_issues:
        print(f"\n  ⚠️  {len(all_issues)} problème(s) de mapping:")
        for issue in all_issues:
            print(f"    • {issue}")
    else:
        print(f"\n  ✅ Tous les mappings sont valides")

    # Déconnexion
    for broker_id, broker in connected_brokers.items():
        try:
            await broker.disconnect()
        except Exception:
            pass

    print()
    return len(all_issues) == 0


def main():
    parser = argparse.ArgumentParser(
        description="Test connectivité et mapping instruments"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Afficher le détail de chaque symbole"
    )
    args = parser.parse_args()

    success = asyncio.run(run_test(args.verbose))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
