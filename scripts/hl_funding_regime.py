#!/usr/bin/env python3
"""Moniteur de régime funding Hyperliquid — réveil du carry PARK.

Contexte (2026-07-09) : le cash-and-carry HL est en PARK (protocole
docs/audit/cash_and_carry_hl_protocole_2026-07-09.md) — rendement de base
insuffisant une fois la marge de survie payée. Condition de réouverture n°1 :
régime de funding euphorique. Ce script mesure le funding annualisé des
14 derniers jours sur les instruments à plus forte prime structurelle et
rend un verdict :

- ``dormant``  : rien à faire (cas normal)
- ``wake``     : ≥ WAKE_MIN_INSTRUMENTS instruments > WAKE_THRESHOLD (20 %/an)
- ``wake_confirmed`` : wake ET un wake précédent ≥ 7 j plus tôt dans
  l'historique (= soutenu 2 semaines) → rouvrir le protocole carry
  (nouveau protocole conditionnel pré-enregistré, décision opérateur).

Usage : python scripts/hl_funding_regime.py   (read-only, ~10 s réseau)
Append : logs/hl_funding_regime.jsonl (une ligne par run)
Consommé par la watchlist /suivi (ligne ``hl_funding_regime``).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

INSTRUMENTS = ["AAVE", "LINK", "UNI", "LTC", "NEAR", "DOGE", "BTC"]
LOOKBACK_DAYS = 14
WAKE_THRESHOLD = 0.20        # 20 %/an annualisé sur 14 j
WAKE_MIN_INSTRUMENTS = 3
SUSTAIN_MIN_DAYS = 7         # 2e wake ≥ 7 j après un précédent = soutenu
HOURS_YEAR = 24 * 365
STATE = Path("logs/hl_funding_regime.jsonl")


def fetch_recent_funding(base: str) -> float | None:
    """Funding annualisé moyen des 14 derniers jours (None si indisponible)."""
    import ccxt

    ex = ccxt.hyperliquid({
        "enableRateLimit": True,
        "timeout": 30000,
        "options": {"fetchMarkets": {"types": ["swap"]}},
    })
    since = int((datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).timestamp() * 1000)
    try:
        batch = ex.fetch_funding_rate_history(f"{base}/USDC:USDC", since=since, limit=500)
    except Exception:
        return None
    rates = [float(r["fundingRate"]) for r in batch if r.get("fundingRate") is not None]
    if len(rates) < 24 * 7:  # moins d'une semaine de données = pas fiable
        return None
    return sum(rates) / len(rates) * HOURS_YEAR


def previous_wake_ts() -> datetime | None:
    if not STATE.exists():
        return None
    last = None
    for line in STATE.read_text().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("verdict", "").startswith("wake"):
            last = datetime.fromisoformat(entry["ts"])
    return last


def main() -> None:
    now = datetime.now(timezone.utc)
    readings = {}
    for base in INSTRUMENTS:
        ann = fetch_recent_funding(base)
        readings[base] = None if ann is None else round(ann, 4)

    valid = {k: v for k, v in readings.items() if v is not None}
    hot = {k: v for k, v in valid.items() if v > WAKE_THRESHOLD}

    verdict = "dormant"
    if len(hot) >= WAKE_MIN_INSTRUMENTS:
        verdict = "wake"
        prev = previous_wake_ts()
        if prev is not None and (now - prev) >= timedelta(days=SUSTAIN_MIN_DAYS):
            verdict = "wake_confirmed"

    for base in INSTRUMENTS:
        v = readings[base]
        flag = " 🔥" if base in hot else ""
        print(f"  {base:5s} funding 14j annualisé : {'n/a' if v is None else f'{v:+.1%}'}{flag}")
    print(f"Verdict : {verdict} ({len(hot)}/{len(valid)} instruments > {WAKE_THRESHOLD:.0%}/an)")
    if verdict == "wake_confirmed":
        print("→ Régime euphorique SOUTENU : proposer la réouverture du protocole "
              "cash-and-carry (nouveau protocole conditionnel, décision opérateur).")

    STATE.parent.mkdir(exist_ok=True)
    with STATE.open("a") as f:
        f.write(json.dumps({
            "ts": now.isoformat(),
            "readings": readings,
            "hot": sorted(hot),
            "verdict": verdict,
        }) + "\n")


if __name__ == "__main__":
    main()
