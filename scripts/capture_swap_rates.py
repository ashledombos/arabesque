#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capture des taux de swap par instrument (chantier coûts, 2026-07-04).

Contexte : le swap est le trou de données n°1 du rapport coûts P1 — bloquant
pour 2 dossiers Phase C (session-métaux : coût de détention overnight ;
carry : c'est l'edge lui-même).

- **cTrader/FTMO** : ProtoOASymbolByIdReq expose les champs swap* du
  ProtoOASymbol. Le connecteur les ignore (_process_symbol_details ne garde
  que digits/volumes) → on intercepte le payload par monkeypatch read-only,
  sans toucher au connecteur.
- **TradeLocker/GFT** : AUCUNE route API n'expose les taux (vérifié 07-04 :
  ni /trade/instruments/{id}, ni config). Seule mesure possible = accrual
  observé sur positions tenues la nuit (déjà journalisé à l'exit via
  swap_cash depuis 06-07). Ce script ne couvre donc que FTMO ; GFT se
  remplira empiriquement.

Sortie : logs/swap_rates.jsonl (append, 1 ligne/symbole/run) + table stdout.
Les valeurs proto sont stockées BRUTES (unités cTrader à interpréter :
pips/rollover a priori) + tous les champs swap*/rollover* rencontrés.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BROKER_ID = "ftmo_challenge"
TARGETS = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDJPY",
           "CHFJPY", "GBPJPY", "EURGBP", "BTCUSD", "ETHUSD"]
OUT = ROOT / "logs" / "swap_rates.jsonl"


async def main() -> int:
    from arabesque.config import load_full_config
    from arabesque.broker.factory import create_broker

    settings, secrets, instruments = load_full_config()
    cfg = settings["brokers"][BROKER_ID].copy()
    cfg.update(secrets.get(BROKER_ID, {}))
    if instruments:
        cfg["instruments"] = instruments
    broker = create_broker(BROKER_ID, cfg)

    captured: dict[int, dict] = {}
    orig = broker._process_symbol_details

    def spy(payload):
        for s in payload.symbol:
            fields = {}
            for fd, val in s.ListFields():
                n = fd.name
                if "swap" in n.lower() or "rollover" in n.lower():
                    fields[n] = val
            captured[s.symbolId] = fields
        return orig(payload)

    broker._process_symbol_details = spy

    try:
        if not await broker.connect():
            print("connexion échouée (weekend cTrader erratique ? re-tenter en semaine)")
            return 1
        await broker.get_symbols()
        name_by_id = {}
        sym_ids = []
        for sid, sinfo in broker._symbols.items():
            if sinfo.symbol in TARGETS:
                sym_ids.append(sid)
                name_by_id[sid] = sinfo.symbol
        await broker.fetch_symbol_details(sym_ids)

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = []
        for sid in sym_ids:
            f = captured.get(sid, {})
            sym = name_by_id[sid]
            sinfo = broker._symbols.get(sid)
            pip = getattr(sinfo, "pip_size", None)
            tick = await broker.get_quote(sym)
            mid = (tick.bid + tick.ask) / 2 if tick and tick.bid > 0 else None
            row = {"ts": now, "broker": BROKER_ID, "symbol": sym,
                   "pip_size": pip, "mid": mid, "raw_swap_fields": f}
            # Conversion bps/nuit. swapCalculationType : 0/absent = PIPS par
            # période de 24h ; 1 = POURCENTAGE ANNUEL (crypto).
            if f:
                if f.get("swapCalculationType") == 1:
                    row["swap_long_bps_night"] = round(f["swapLong"] * 100 / 365, 3)
                    row["swap_short_bps_night"] = round(f["swapShort"] * 100 / 365, 3)
                elif pip and mid:
                    row["swap_long_bps_night"] = round(f["swapLong"] * pip / mid * 1e4, 3)
                    row["swap_short_bps_night"] = round(f["swapShort"] * pip / mid * 1e4, 3)
            rows.append(row)
        rows.sort(key=lambda r: r["symbol"])
        with OUT.open("a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        print(f"\n{'symbole':8s} {'pip':>7s} {'mid':>10s} {'long bps/n':>11s} {'short bps/n':>12s}")
        for r in rows:
            mid_s = f"{r['mid']:.3f}" if r.get("mid") else "—"
            print(f"{r['symbol']:8s} {str(r.get('pip_size')):>7s} {mid_s:>10s} "
                  f"{str(r.get('swap_long_bps_night', '—')):>11s} "
                  f"{str(r.get('swap_short_bps_night', '—')):>12s}")
        print(f"\n{len(rows)} lignes → {OUT}")
    finally:
        try:
            await broker.disconnect()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
