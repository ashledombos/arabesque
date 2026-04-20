#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_broker_divergences.py

Analyse logs/multi_broker_snapshots.jsonl : pour chaque (symbol, ts) apparié
entre brokers, calcule :
  - écart mid-to-mid (bps et en prix)
  - écart de spread (différence de spread ask-bid)
  - spread moyen par broker
Utile pour confirmer/infirmer la thèse "fills GFT pires hors heures liquides".

Usage :
    python scripts/review_broker_divergences.py
    python scripts/review_broker_divergences.py --symbol BTCUSD
    python scripts/review_broker_divergences.py --since 2026-04-19
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median

LOG_PATH = Path("logs/multi_broker_snapshots.jsonl")


def load(path: Path, symbol_filter=None, since=None):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if symbol_filter and r.get("symbol") != symbol_filter:
            continue
        if since and r.get("ts", "") < since:
            continue
        rows.append(r)
    return rows


def group_pairs(rows):
    """Regroupe par (symbol, ts). Ne garde que les timestamps avec >=2 brokers."""
    by_key = defaultdict(dict)  # (symbol, ts) → {broker: row}
    for r in rows:
        by_key[(r["symbol"], r["ts"])][r["broker"]] = r
    return {k: v for k, v in by_key.items() if len(v) >= 2}


def analyse(paired):
    """Stats par symbole."""
    stats = defaultdict(lambda: {
        "n": 0,
        "mid_diffs_bps": [],
        "spread_diffs": [],
        "spread_by_broker": defaultdict(list),
    })
    for (symbol, _ts), brokers in paired.items():
        s = stats[symbol]
        s["n"] += 1
        # spread moyen par broker
        for bid, row in brokers.items():
            s["spread_by_broker"][bid].append(row["spread"])
        # écart mid broker_a vs broker_b (toutes paires)
        ids = sorted(brokers.keys())
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                ma, mb = brokers[a]["mid"], brokers[b]["mid"]
                if ma > 0:
                    s["mid_diffs_bps"].append(
                        10_000 * (mb - ma) / ma  # bps, signé b-a
                    )
                s["spread_diffs"].append(
                    brokers[b]["spread"] - brokers[a]["spread"]
                )
    return stats


def print_report(stats):
    if not stats:
        print("Aucune paire broker/symbole à analyser (besoin >=2 brokers pour le même symbol+ts).")
        return
    for symbol, s in sorted(stats.items()):
        print(f"\n=== {symbol} — {s['n']} snapshots appariés ===")
        if s["mid_diffs_bps"]:
            vals = s["mid_diffs_bps"]
            print(
                f"  mid diff (bps, broker_b - broker_a, trié alpha): "
                f"médiane={median(vals):+.2f}  moyenne={mean(vals):+.2f}  "
                f"min={min(vals):+.2f}  max={max(vals):+.2f}"
            )
        if s["spread_diffs"]:
            vals = s["spread_diffs"]
            print(
                f"  spread diff (prix):          "
                f"médiane={median(vals):+.6f}  moyenne={mean(vals):+.6f}"
            )
        print("  spread moyen par broker:")
        for bid, spreads in sorted(s["spread_by_broker"].items()):
            if spreads:
                print(
                    f"    {bid:<20} médiane={median(spreads):.6f}  "
                    f"moyenne={mean(spreads):.6f}  n={len(spreads)}"
                )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=str(LOG_PATH))
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--since", default=None, help="ISO date, ex: 2026-04-19")
    args = ap.parse_args()

    rows = load(Path(args.path), symbol_filter=args.symbol, since=args.since)
    if not rows:
        print(f"Aucune donnée dans {args.path}")
        return
    print(f"{len(rows)} lignes chargées depuis {args.path}")
    paired = group_pairs(rows)
    print(f"{len(paired)} timestamps avec >=2 brokers")
    stats = analyse(paired)
    print_report(stats)


if __name__ == "__main__":
    main()
