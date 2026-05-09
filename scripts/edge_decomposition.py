"""Décomposition causale du ΔExp live vs baseline.

Pour une fenêtre d'exits, attribue chaque trade à une catégorie de cause et
quantifie l'impact en R par catégorie. Permet de répondre à : "l'edge dérive,
pourquoi ?" sans extrapolation, en restant agrégé.

Catégories appliquées dans cet ordre (premier match) :

1. be_missed       : mfe_r ≥ 0.3R, be_set=False, result_r ≤ -0.5R
                     → cost imputable = result_r - 0.20  (BE aurait donné +0.20R)
2. sl_slipped      : exit_reason="stop_loss", result_r < -1.05
                     → cost imputable = result_r - (-1.0)  (au-delà du -1R nominal)
3. reconciled      : exit_reason commence par "reconciled_" OU exit_price_source="reconciled"
                     → flagué mais cost non quantifié individuellement
                     (engine aveugle = bug, pas un cost de marché)
4. mfe_zero_loser  : mfe_r == 0, result_r ≤ -0.5R, hors catégories ci-dessus
                     → flagué (tracker cassé, cost réel inconnaissable)
5. wide_spread     : spread_at_entry > 2× moyenne historique de l'instrument (post-#15)
                     → cost = (spread_actuel - moy) × 2 / abs(entry_price - sl) ≈ R
6. normal_loss     : autres result_r < 0
7. normal_win      : result_r ≥ 0

Sortie : tableau par catégorie (n, ΣR, %ΔExp imputé, cost moyen) + résiduel régime.

Usage :
    python scripts/edge_decomposition.py --since 2026-05-07T23:45
    python scripts/edge_decomposition.py --last 30
    python scripts/edge_decomposition.py --since J-7 --strategy extension --broker ftmo_challenge
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

JOURNAL = Path("logs/trade_journal.jsonl")
DECOMP_LOG = Path("logs/edge_decomposition.jsonl")

# Baselines par stratégie pour calculer ΔExp (Exp_baseline depuis HANDOFF)
BASELINE_EXP = {
    "extension": 0.083,  # baseline 20 mois multi-instruments
    "cabriole": 0.034,   # 20 mois 6 cryptos H4
    "glissade": 0.196,   # 20 mois XAUUSD+BTCUSD H1
    "fouette": 0.150,    # 20 mois XAUUSD London + BTCUSD NY
}
# Alias historiques
STRAT_ALIAS = {"trend": "extension"}


def parse_since(since: str) -> datetime:
    """Accepte ISO date, YYYY-MM-DD, ou J-N (relatif)."""
    if since.startswith("J-") or since.startswith("j-"):
        n = int(since[2:])
        return datetime.now(timezone.utc) - timedelta(days=n)
    if "T" in since:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def load_exits(since: datetime | None, last: int | None,
               strategy: str | None, broker: str | None) -> list[dict]:
    if not JOURNAL.exists():
        return []
    rows = []
    with JOURNAL.open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("event") != "exit":
                continue
            strat = STRAT_ALIAS.get(e.get("strategy", ""), e.get("strategy", ""))
            e["_strategy_norm"] = strat
            if strategy and strat != strategy:
                continue
            if broker and e.get("broker_id") != broker:
                continue
            ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
            e["_ts_dt"] = ts
            rows.append(e)
    rows.sort(key=lambda r: r["_ts_dt"])
    if since:
        rows = [r for r in rows if r["_ts_dt"] >= since]
    if last:
        rows = rows[-last:]
    return rows


def compute_spread_baseline(all_exits: list[dict]) -> dict[str, float]:
    """Spread moyen par instrument depuis le journal complet (post-#15)."""
    by_inst: dict[str, list[float]] = defaultdict(list)
    for e in all_exits:
        sp = e.get("spread_at_entry", 0)
        if sp and sp > 0:
            by_inst[e["instrument"]].append(sp)
    return {k: sum(v) / len(v) for k, v in by_inst.items() if len(v) >= 5}


def classify(e: dict, spread_baseline: dict[str, float]) -> tuple[str, float]:
    """Retourne (categorie, cost_imputable_en_R).

    cost_imputable = écart en R par rapport au comportement attendu de la
    catégorie. Ex: be_missed avec result_r=-1.0 → cost=-1.20 (on attendait +0.20).
    Pour normal_loss/normal_win, cost=0 (pas d'imputation, c'est le résultat
    naturel du setup).
    """
    result_r = float(e.get("result_r", 0))
    mfe_r = float(e.get("mfe_r", 0))
    be_set = bool(e.get("be_set", False))
    exit_reason = str(e.get("exit_reason", ""))
    exit_price_source = str(e.get("exit_price_source", ""))

    # 1. BE raté
    if mfe_r >= 0.3 and not be_set and result_r <= -0.5:
        return "be_missed", result_r - 0.20

    # 2. SL slippé (au-delà du -1R nominal)
    if exit_reason == "stop_loss" and result_r < -1.05:
        return "sl_slipped", result_r - (-1.0)

    # 3. Reconciled (exit reconstruit post-mortem = engine était aveugle)
    if exit_reason.startswith("reconciled_") or exit_price_source == "reconciled":
        return "reconciled", 0.0

    # 4. MFE zero loser (tracker cassé, cost non chiffrable)
    if mfe_r == 0 and result_r <= -0.5:
        return "mfe_zero_loser", 0.0

    # 5. Spread anormal (post-#15 seulement, baseline ≥ 5 trades sur instrument)
    inst = e.get("instrument", "")
    sp = e.get("spread_at_entry", 0)
    if sp and sp > 0 and inst in spread_baseline:
        moy = spread_baseline[inst]
        if sp > 2 * moy:
            entry = float(e.get("entry_price", 0))
            sl = float(e.get("sl", 0))
            risk_unit = abs(entry - sl)
            if risk_unit > 0:
                # Surplus spread × 2 (entry+exit) / risk_unit ≈ R perdu
                cost = -((sp - moy) * 2 / risk_unit)
                return "wide_spread", cost

    # 6/7. Normal
    if result_r < 0:
        return "normal_loss", 0.0
    return "normal_win", 0.0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", type=str, default=None,
                   help="Date début (YYYY-MM-DD, ISO, ou J-N relatif)")
    p.add_argument("--last", type=int, default=None,
                   help="N derniers exits seulement")
    p.add_argument("--strategy", type=str, default=None)
    p.add_argument("--broker", type=str, default=None)
    p.add_argument("--no-persist", action="store_true",
                   help="Ne pas écrire dans logs/edge_decomposition.jsonl")
    args = p.parse_args()

    since = parse_since(args.since) if args.since else None
    if not since and not args.last:
        # défaut : 30 derniers jours
        since = datetime.now(timezone.utc) - timedelta(days=30)

    # Pour le baseline spread on lit TOUT le journal (pas filtré)
    all_exits = load_exits(None, None, None, None)
    spread_baseline = compute_spread_baseline(all_exits)

    exits = load_exits(since, args.last, args.strategy, args.broker)
    if not exits:
        print("Aucun exit dans la fenêtre.")
        return 0

    # Classification + agrégat
    by_cat: dict[str, list[float]] = defaultdict(list)  # liste de result_r
    cost_by_cat: dict[str, list[float]] = defaultdict(list)  # liste de cost_imputable
    sample_by_cat: dict[str, list[dict]] = defaultdict(list)  # 3 exemples max

    for e in exits:
        cat, cost = classify(e, spread_baseline)
        by_cat[cat].append(float(e.get("result_r", 0)))
        cost_by_cat[cat].append(cost)
        if len(sample_by_cat[cat]) < 3:
            sample_by_cat[cat].append({
                "ts": e["ts"],
                "instrument": e.get("instrument"),
                "broker": e.get("broker_id"),
                "result_r": e.get("result_r"),
                "mfe_r": e.get("mfe_r"),
                "be_set": e.get("be_set"),
                "exit_reason": e.get("exit_reason"),
            })

    n_total = len(exits)
    exp_total = sum(float(e.get("result_r", 0)) for e in exits) / n_total

    # ΔExp vs baseline (par stratégie pondérée si multi-strat dans la fenêtre)
    by_strat = defaultdict(list)
    for e in exits:
        by_strat[e["_strategy_norm"]].append(float(e.get("result_r", 0)))
    weighted_baseline = sum(
        BASELINE_EXP.get(s, 0) * len(rs) for s, rs in by_strat.items()
    ) / n_total
    delta_exp = exp_total - weighted_baseline

    # Output console
    print()
    fenetre = (f"--last {args.last}" if args.last
               else f"depuis {since.strftime('%Y-%m-%d %H:%M UTC') if since else 'tout'}")
    filtres = ""
    if args.strategy:
        filtres += f" strategy={args.strategy}"
    if args.broker:
        filtres += f" broker={args.broker}"
    print(f"=== Décomposition causale ΔExp — {fenetre}{filtres} ===")
    print(f"  n={n_total}  Exp={exp_total:+.3f}R  baseline_pondérée={weighted_baseline:+.3f}R  ΔExp={delta_exp:+.3f}R")
    print()
    print(f"  {'Catégorie':<18s} {'n':>4s} {'%n':>5s} {'ΣR':>8s} {'meanR':>8s} {'cost_moy':>10s} {'%ΔExp':>8s}")
    print("  " + "-" * 70)

    cats_order = ["be_missed", "sl_slipped", "reconciled", "mfe_zero_loser",
                  "wide_spread", "normal_loss", "normal_win"]
    total_cost_attribue = 0.0
    for cat in cats_order:
        rs = by_cat.get(cat, [])
        costs = cost_by_cat.get(cat, [])
        n = len(rs)
        if n == 0:
            continue
        sumr = sum(rs)
        meanr = sumr / n
        cost_mean = sum(costs) / n if costs else 0
        sumcost = sum(costs)
        total_cost_attribue += sumcost
        # %ΔExp imputé : combien la cat explique du drift global
        if abs(delta_exp) > 1e-6:
            pct_drift = (sumcost / n_total) / delta_exp * 100
        else:
            pct_drift = 0
        print(f"  {cat:<18s} {n:>4d} {n/n_total*100:>4.0f}% {sumr:>+8.2f} {meanr:>+8.3f}R {cost_mean:>+9.3f}R {pct_drift:>+7.0f}%")

    # Résiduel régime = ΔExp − ce qui est imputé
    residual_per_trade = delta_exp - total_cost_attribue / n_total
    print("  " + "-" * 70)
    print(f"  {'résiduel régime':<18s} {'-':>4s} {'-':>5s} {'-':>8s} {residual_per_trade:>+8.3f}R {'(non imputé)':>10s}")

    # Borne supérieure cost caché reconciled+mfe_zero (heuristique HANDOFF :
    # 5/17 trades historiques reconstruits comme BE post-mortem → +1.20R chacun)
    n_recon = len(by_cat.get("reconciled", []))
    n_mfez = len(by_cat.get("mfe_zero_loser", []))
    if n_recon + n_mfez > 0:
        # Hypothèse heuristique 5/17 ≈ 29% des reconciled+mfe_zero auraient été BE
        n_likely_be = round(0.29 * (n_recon + n_mfez))
        cost_hidden_upper = n_likely_be * 1.20  # gain BE+0.20 vs SL -1
        pct_hidden = (cost_hidden_upper / n_total) / abs(delta_exp) * 100 if abs(delta_exp) > 1e-6 else 0
        print(f"  {'(reconciled+mfez':<18s} {'≈':>4s} {f'{n_likely_be}':>5s}  borne sup cost caché ≈ +{cost_hidden_upper:.1f}R total"
              f" → ≈{pct_hidden:.0f}% de |ΔExp|, à confirmer par reconstruction post-mortem)")
    print()

    # Exemples 1-3 par catégorie problématique
    pb_cats = [c for c in ["be_missed", "sl_slipped", "reconciled", "mfe_zero_loser", "wide_spread"]
               if by_cat.get(c)]
    if pb_cats:
        print("  Échantillons :")
        for cat in pb_cats:
            for s in sample_by_cat[cat][:2]:
                print(f"    [{cat}] {s['ts'][:16]} {s['instrument']} ({s['broker']}) "
                      f"r={s['result_r']:+.2f} mfe={s['mfe_r']:.2f} be={s['be_set']} reason={s['exit_reason']}")
        print()

    # Persistance
    if not args.no_persist:
        DECOMP_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "since": since.isoformat() if since else None,
            "last": args.last,
            "strategy_filter": args.strategy,
            "broker_filter": args.broker,
            "n_total": n_total,
            "exp_live": round(exp_total, 4),
            "baseline_weighted": round(weighted_baseline, 4),
            "delta_exp": round(delta_exp, 4),
            "by_category": {
                cat: {
                    "n": len(by_cat[cat]),
                    "sum_r": round(sum(by_cat[cat]), 3),
                    "mean_r": round(sum(by_cat[cat]) / len(by_cat[cat]), 4),
                    "cost_mean_r": round(sum(cost_by_cat[cat]) / len(cost_by_cat[cat]), 4)
                                   if cost_by_cat[cat] else 0,
                    "sum_cost_r": round(sum(cost_by_cat[cat]), 3),
                }
                for cat in cats_order if by_cat.get(cat)
            },
            "residual_per_trade": round(residual_per_trade, 4),
        }
        with DECOMP_LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")
        print(f"  Persisté dans {DECOMP_LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
