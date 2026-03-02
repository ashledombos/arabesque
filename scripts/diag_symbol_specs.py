#!/usr/bin/env python3
"""
Diagnostic: affiche les valeurs brutes du proto pour vérifier
minVolume/maxVolume/stepVolume/lotSize/digits/pipPosition.

Usage:
    python scripts/diag_symbol_specs.py [broker_id] [symbol1,symbol2,...]
    python scripts/diag_symbol_specs.py ftmo_swing_test BNBUSD,BTCUSD,USDJPY,EURUSD
"""

import asyncio
import sys
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arabesque.broker.ctrader import CTraderBroker


# Symboles à diagnostiquer
TARGET_SYMBOLS = ["BNBUSD", "BTCUSD", "USDJPY", "EURUSD", "FETUSD", "GALUSD", "SOLUSD", "ETHUSD"]


async def main():
    # Parse args
    broker_id = sys.argv[1] if len(sys.argv) > 1 else "ftmo_swing_test"
    if len(sys.argv) > 2:
        TARGET_SYMBOLS[:] = sys.argv[2].split(",")

    # Load config
    settings = yaml.safe_load(open(ROOT / "config" / "settings.yaml"))
    secrets = yaml.safe_load(open(ROOT / "config" / "secrets.yaml"))

    broker_cfg = dict(settings["brokers"][broker_id])
    if broker_id in secrets:
        broker_cfg.update(secrets[broker_id])

    broker = CTraderBroker(broker_id, broker_cfg)

    print(f"[diag] Connexion {broker_id}...")
    ok = await broker.connect()
    if not ok:
        print("❌ Connexion échouée")
        return

    print(f"[diag] Chargement symboles...")
    symbols = await broker.get_symbols()
    print(f"[diag] {len(symbols)} symboles chargés")

    # Trouver les symbolIds pour nos cibles
    target_ids = []
    id_to_name = {}
    for sid, sinfo in broker._symbols.items():
        if sinfo.symbol in TARGET_SYMBOLS:
            target_ids.append(sid)
            id_to_name[sid] = sinfo.symbol

    print(f"[diag] {len(target_ids)} symboles cibles trouvés: "
          f"{[id_to_name[i] for i in target_ids]}")
    print()

    if not target_ids:
        print("❌ Aucun symbole cible trouvé")
        await broker.disconnect()
        return

    # Fetch symbol details et intercepter les valeurs brutes
    from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASymbolByIdReq

    loop = asyncio.get_event_loop()
    future = loop.create_future()

    # Stocker la réponse brute
    raw_payload = None
    original_handler = broker._process_symbol_details

    def capture_handler(payload):
        nonlocal raw_payload
        raw_payload = payload
        original_handler(payload)

    broker._process_symbol_details = capture_handler
    broker._pending_requests["symbol_details"] = future

    req = ProtoOASymbolByIdReq()
    req.ctidTraderAccountId = broker.account_id
    for sid in target_ids:
        req.symbolId.append(sid)

    broker._send_via_reactor(req)
    await asyncio.wait_for(future, timeout=30)

    # Afficher les valeurs brutes
    if raw_payload:
        print("=" * 80)
        print("VALEURS BRUTES DU PROTO (ProtoOASymbol)")
        print("=" * 80)

        for s in raw_payload.symbol:
            sym_name = id_to_name.get(s.symbolId, f"ID:{s.symbolId}")
            if s.symbolId not in [sid for sid in target_ids]:
                continue

            print(f"\n--- {sym_name} (symbolId={s.symbolId}) ---")

            # Lire tous les champs pertinents
            fields = [
                "digits", "pipPosition", "minVolume", "maxVolume",
                "stepVolume", "lotSize", "enableShortSelling",
                "slDistance", "tpDistance",
            ]
            for field in fields:
                has = s.HasField(field) if hasattr(s, 'HasField') else True
                val = getattr(s, field, "N/A")
                print(f"  {field:20s} = {val:>15}  (present={has})")

            # Calculer ce que notre code en fait
            digits = getattr(s, "digits", 5)
            pip_pos = getattr(s, "pipPosition", max(0, digits - 1))
            min_vol_raw = getattr(s, "minVolume", 100)
            max_vol_raw = getattr(s, "maxVolume", 10000000)
            step_raw = getattr(s, "stepVolume", 100)
            lot_size = getattr(s, "lotSize", 100000)

            print(f"\n  → Notre code calcule:")
            print(f"    digits       = {digits}")
            print(f"    pipPosition  = {pip_pos}")
            print(f"    min_volume   = {min_vol_raw}/100 = {min_vol_raw/100:.4f} lots")
            print(f"    max_volume   = {max_vol_raw}/100 = {max_vol_raw/100:.4f} lots")
            print(f"    step_volume  = {step_raw}/100 = {step_raw/100:.4f} lots")
            print(f"    lot_size     = {lot_size}")

            # Comparer avec SymbolInfo stocké
            sinfo = broker._symbols.get(s.symbolId)
            if sinfo:
                print(f"\n  → SymbolInfo stocké:")
                print(f"    min_volume  = {sinfo.min_volume}")
                print(f"    max_volume  = {sinfo.max_volume}")
                print(f"    volume_step = {sinfo.volume_step}")
                print(f"    lot_size    = {sinfo.lot_size}")
                print(f"    pip_size    = {sinfo.pip_size}")
                print(f"    digits      = {sinfo.digits}")

    print()
    print("=" * 80)

    # Aussi afficher les SymbolInfo AVANT fetch_symbol_details (les defaults)
    print("\nTest: valeurs par défaut si proto champs absents:")
    print(f"  getattr(s, 'minVolume', 100) / 100 = {100/100} lots ← FAUX si absent!")
    print(f"  getattr(s, 'minVolume', 1) / 100   = {1/100} lots ← correct si absent")

    await broker.disconnect()
    print("\n[diag] Terminé.")


if __name__ == "__main__":
    asyncio.run(main())
