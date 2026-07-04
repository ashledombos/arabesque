#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sondeur passif de spread métaux (GFT/TradeLocker, read-only).

Contexte (étude session-métaux 2026-07-04, docs/audit/session_metaux_2026-07-04.md) :
la dérive overnight de l'or se concentre sur ~22h-24h UTC, fenêtre où AUCUN spread
n'a jamais été mesuré (multi_broker_snapshots n'échantillonne que positions ouvertes,
04h-20h UTC). Ce script lève la réserve n°1 du dossier.

- 1 appel REST `get_quotes` par symbole (TradeLocker : REST pur, sans risque de
  session type ALREADY_LOGGED_IN cTrader — cf. tradelocker.py get_fresh_quote).
- GFT uniquement : venue cible métaux. On ne sonde PAS cTrader/FTMO (un process
  concurrent peut invalider le token de l'engine, incident 2026-05-20).
- Append logs/metals_night_spread.jsonl. Échec réseau = skip silencieux (données
  éparses acceptables, ne jamais spammer).

Lancé par arabesque-metals-spread.timer (systemd user, toutes les 30 min).
Analyse prévue ~2026-07-11 : médiane spread bps par heure UTC, focus 21h-03h.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BROKER_ID = "gft_compte1"
SYMBOLS = ["XAUUSD", "XAGUSD"]
OUT = ROOT / "logs" / "metals_night_spread.jsonl"


async def main() -> int:
    from arabesque.config import load_full_config
    from arabesque.broker.factory import create_broker

    settings, secrets, instruments = load_full_config()
    broker_cfg = settings.get("brokers", {}).get(BROKER_ID, {}).copy()
    broker_cfg.update(secrets.get(BROKER_ID, {}))
    if instruments:
        broker_cfg["instruments"] = instruments
    broker = create_broker(BROKER_ID, broker_cfg)
    try:
        if not await broker.connect():
            return 0  # skip silencieux
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lines = []
        for sym in SYMBOLS:
            tick = await broker.get_quote(sym)
            if tick is None or tick.bid <= 0 or tick.ask <= 0:
                continue
            mid = (tick.bid + tick.ask) / 2
            lines.append(json.dumps({
                "ts": now,
                "broker": BROKER_ID,
                "symbol": sym,
                "bid": tick.bid,
                "ask": tick.ask,
                "spread_bps": round((tick.ask - tick.bid) / mid * 1e4, 3),
            }))
        if lines:
            with OUT.open("a") as f:
                f.write("\n".join(lines) + "\n")
    except Exception:
        return 0  # jamais bruyant — mesure best-effort
    finally:
        try:
            await broker.disconnect()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
