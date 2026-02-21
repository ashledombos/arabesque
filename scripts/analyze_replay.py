#!/usr/bin/env python3
"""
scripts/analyze_replay.py
Analyse statistique complète d'un fichier JSONL de replay dry-run.

Usage :
    python scripts/analyze_replay.py dry_run_20260221.jsonl
    python scripts/analyze_replay.py dry_run_20260221.jsonl --spike-threshold 20
    python scripts/analyze_replay.py dry_run_20260221.jsonl --no-spike-filter
"""

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path


# ── Chargement ──────────────────────────────────────────────────────

def load_trades(path: str) -> tuple[list[dict], dict]:
    trades = []
    summary = {}
    with open(path) as f:
        for line in f:
            d = json.loads(line.strip())
            if d.get("type") == "trade":
                trades.append(d)
            elif d.get("type") == "summary":
                summary = d
    return trades, summary


def detect_spike_trades(trades: list[dict], threshold_r: float = 20.0) -> list[dict]:
    """Retourne les trades avec result_r > threshold_r (outliers suspects)."""
    return [t for t in trades if abs(t["result_r"]) > threshold_r]


# ── Stats de base ───────────────────────────────────────────────────

def basic_stats(results_r: list[float]) -> dict:
    n = len(results_r)
    if n == 0:
        return {"n": 0}
    wins = sum(1 for r in results_r if r > 0)
    losses = sum(1 for r in results_r if r < 0)
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "wr": wins / n,
        "exp": sum(results_r) / n,
        "total_r": sum(results_r),
        "avg_win": sum(r for r in results_r if r > 0) / wins if wins else 0,
        "avg_loss": sum(r for r in results_r if r < 0) / losses if losses else 0,
        "max_r": max(results_r),
        "min_r": min(results_r),
    }


# ── Wilson CI ───────────────────────────────────────────────────────

def wilson_ci(wins: int, n: int, z: float = 1.960) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = wins / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (center - spread) / denom, (center + spread) / denom


# ── Bootstrap expectancy ─────────────────────────────────────────────

def bootstrap_exp(results_r: list[float], n_sims: int = 5000, seed: int = 42) -> dict:
    if not results_r:
        return {"ci95_low": 0, "ci95_high": 0, "p_pos": 0}
    random.seed(seed)
    n = len(results_r)
    boot = sorted([sum(random.choices(results_r, k=n)) / n for _ in range(n_sims)])
    idx025, idx975 = int(0.025 * n_sims), int(0.975 * n_sims)
    return {
        "ci95_low": boot[idx025],
        "ci95_high": boot[idx975],
        "ci80_low": boot[int(0.10 * n_sims)],
        "ci80_high": boot[int(0.90 * n_sims)],
        "p_pos": sum(1 for x in boot if x > 0) / n_sims,
        "significant": boot[idx025] > 0,
    }


# ── Analyse de concentration (trade trimming) ────────────────────────

def concentration_analysis(results_r: list[float]) -> dict:
    """
    Mesure la dépendance aux outliers positifs.
    Prop firm = edge doit tenir sans les meilleures trades.
    """
    if not results_r:
        return {}
    sorted_r = sorted(results_r, reverse=True)
    total = sum(results_r)
    n = len(results_r)
    base_exp = total / n
    result = {"base_exp": base_exp, "by_trim": {}}
    for k in [1, 3, 5, 10]:
        if k >= n:
            continue
        trimmed = sorted_r[k:]
        trimmed_exp = sum(trimmed) / len(trimmed)
        result["by_trim"][k] = {
            "exp": trimmed_exp,
            "total_r": sum(trimmed),
            "pct_lost": (total - sum(trimmed)) / abs(total) * 100 if total != 0 else 0,
            "still_positive": trimmed_exp > 0,
        }
    # Top-1 trade contribution
    result["top1_pct"] = (sorted_r[0] / total * 100) if total > 0 else 0
    result["top3_pct"] = (sum(sorted_r[:3]) / total * 100) if total > 0 else 0
    result["top5_pct"] = (sum(sorted_r[:5]) / total * 100) if total > 0 else 0
    return result


# ── Rolling window consistency ────────────────────────────────────────

def rolling_consistency(trades: list[dict], window: int = 50) -> dict:
    """
    Divise la séquence de trades en fenêtres glissantes.
    Prop firm : edge doit être positif sur ~70%+ des fenêtres.
    """
    if len(trades) < window:
        return {"insufficient_data": True}
    results_r = [t["result_r"] for t in trades]
    windows_pos = 0
    windows_neg = 0
    exps = []
    for i in range(len(results_r) - window + 1):
        chunk = results_r[i : i + window]
        exp = sum(chunk) / window
        exps.append(exp)
        if exp > 0:
            windows_pos += 1
        else:
            windows_neg += 1
    total_windows = windows_pos + windows_neg
    return {
        "window": window,
        "total_windows": total_windows,
        "pct_positive": windows_pos / total_windows if total_windows else 0,
        "min_exp": min(exps),
        "max_exp": max(exps),
        "median_exp": sorted(exps)[len(exps) // 2],
    }


# ── Monte Carlo Drawdown ──────────────────────────────────────────────

def mc_drawdown(results_r: list[float], risk_pct: float = 0.5, balance: float = 100_000,
                n_sims: int = 5000, daily_limit: float = 4.0, total_limit: float = 9.0,
                seed: int = 42) -> dict:
    if not results_r:
        return {}
    random.seed(seed)
    risk_cash = balance * risk_pct / 100
    n = len(results_r)
    max_dds, daily_breaches, total_breaches = [], 0, 0
    for _ in range(n_sims):
        seq = random.choices(results_r, k=n)
        eq, peak, daily_pnl, worst_daily, sim_dd = balance, balance, 0, 0, 0
        for i, r in enumerate(seq):
            eq += r * risk_cash
            peak = max(peak, eq)
            sim_dd = max(sim_dd, (peak - eq) / peak * 100)
            daily_pnl += r * risk_cash
            if (i + 1) % 8 == 0 or i == n - 1:
                worst_daily = max(worst_daily, abs(min(0, daily_pnl)) / balance * 100)
                daily_pnl = 0
        max_dds.append(sim_dd)
        if worst_daily >= daily_limit:
            daily_breaches += 1
        if sim_dd >= total_limit:
            total_breaches += 1
    max_dds.sort()
    return {
        "median_dd": max_dds[n_sims // 2],
        "p95_dd": max_dds[int(0.95 * n_sims)],
        "p_breach_daily": daily_breaches / n_sims,
        "p_breach_total": total_breaches / n_sims,
        "ftmo_compatible": total_breaches / n_sims < 0.10,
        "daily_limit": daily_limit,
        "total_limit": total_limit,
    }


# ── Rapport ──────────────────────────────────────────────────────────

def print_section(title: str):
    print(f"\n{'─'*64}")
    print(f"  {title}")
    print(f"{'─'*64}")


def verdict_symbol(ok: bool) -> str:
    return "✅" if ok else "❌"


def run_analysis(args):
    trades, summary = load_trades(args.jsonl)
    if not trades:
        print("Aucun trade trouvé dans le fichier.")
        sys.exit(1)

    print(f"\n{'='*64}")
    print(f"  ANALYSE REPLAY — {Path(args.jsonl).name}")
    print(f"{'='*64}")
    print(f"  Trades chargés : {len(trades)}")

    # ── Détection spikes ──
    spikes = detect_spike_trades(trades, threshold_r=args.spike_threshold)
    if spikes:
        print(f"\n⚠️  OUTLIERS DÉTECTÉS (|R| > {args.spike_threshold}) :")
        for s in sorted(spikes, key=lambda x: abs(x["result_r"]), reverse=True):
            print(f"     {s['instrument']:12} R={s['result_r']:+8.2f}  "
                  f"entry={s['entry']}  sl={s['sl']:.4f}  bars={s['bars_open']}")
        print(f"  → Ces trades représentent potentiellement des spikes de données.")
        print(f"  → Diagnostiquer avec : python -c \"import pandas as pd; "
              f"df=pd.read_parquet('path/UNIUSD*.parquet'); print(df[df['high']>20])\"")

    # ── Préparer les trois ensembles de trades ──
    all_r = [t["result_r"] for t in trades]
    spike_ids = {(s["instrument"], s["ts_entry"]) for s in spikes}
    clean_trades = [t for t in trades
                    if (t["instrument"], t["ts_entry"]) not in spike_ids]
    clean_r = [t["result_r"] for t in clean_trades]

    # ── 1. Vue globale ──
    print_section("1. VUE GLOBALE")
    for label, results in [("Brut (avec outliers)", all_r), ("Net (sans outliers)", clean_r)]:
        s = basic_stats(results)
        b = bootstrap_exp(results)
        sig = verdict_symbol(b["significant"])
        print(f"\n  [{label}] n={s['n']}")
        print(f"    Win rate   : {s['wr']:.1%}  "
              f"Wilson IC95=[{wilson_ci(s['wins'], s['n'])[0]:.1%}, "
              f"{wilson_ci(s['wins'], s['n'])[1]:.1%}]")
        print(f"    Expectancy : {s['exp']:+.4f}R  "
              f"(IC95=[{b['ci95_low']:+.4f}, {b['ci95_high']:+.4f}])")
        print(f"    Total R    : {s['total_r']:+.1f}R")
        print(f"    Significatif (IC95 > 0) : {sig}  P(exp>0)={b['p_pos']:.1%}")

    # ── 2. Concentration des trades ──
    print_section("2. ROBUSTESSE — CONCENTRATION DES RÉSULTATS")
    c = concentration_analysis(clean_r)
    if c:
        print(f"\n  Top-1  trade = {c['top1_pct']:.1f}% du P&L total")
        print(f"  Top-3  trades = {c['top3_pct']:.1f}% du P&L total")
        print(f"  Top-5  trades = {c['top5_pct']:.1f}% du P&L total")
        print(f"\n  Impact sur l'expectancy si on retire les N meilleures trades :")
        for k, v in c["by_trim"].items():
            ok = verdict_symbol(v["still_positive"])
            print(f"  {ok} Sans top-{k:>2} : exp={v['exp']:+.4f}R  "
                  f"(-{v['pct_lost']:.0f}% du P&L)")
    print(f"\n  ⚠️  Pour une prop firm, l'edge doit rester positif sans le top-5.")

    # ── 3. Consistency temporelle ──
    print_section("3. CONSISTANCE TEMPORELLE (fenêtres glissantes)")
    for w in [50, 100]:
        rc = rolling_consistency(clean_trades, window=w)
        if rc.get("insufficient_data"):
            print(f"\n  Fenêtre {w} : pas assez de trades.")
            continue
        ok = verdict_symbol(rc["pct_positive"] >= 0.65)
        print(f"\n  {ok} Fenêtre {w} trades : {rc['pct_positive']:.0%} de fenêtres positives "
              f"(seuil prop firm : ≥65%)")
        print(f"     Exp min={rc['min_exp']:+.3f}R  max={rc['max_exp']:+.3f}R  "
              f"median={rc['median_exp']:+.3f}R")

    # ── 4. Par instrument ──
    print_section("4. PAR INSTRUMENT (sans outliers)")
    by_inst = defaultdict(list)
    for t in clean_trades:
        by_inst[t["instrument"]].append(t["result_r"])

    print(f"\n  {'Inst':<12} {'N':>4}  {'Exp':>7}  {'IC95 low':>9}  {'IC95 hi':>9}  {'P(+)':>6}  {'Sig'}")
    print(f"  {'─'*67}")
    for inst, rs in sorted(by_inst.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True):
        if len(rs) < 5:
            continue
        s = basic_stats(rs)
        b = bootstrap_exp(rs, n_sims=3000)
        sig = verdict_symbol(b["significant"])
        print(f"  {inst:<12} {s['n']:>4}  {s['exp']:>+7.3f}R  "
              f"{b['ci95_low']:>+9.4f}  {b['ci95_high']:>+9.4f}  "
              f"{b['p_pos']:>6.1%}  {sig}")

    # ── 5. Monte Carlo Drawdown ──
    print_section("5. MONTE CARLO DRAWDOWN (prop firm compatibility)")
    dd = mc_drawdown(clean_r, risk_pct=0.5, balance=100_000)
    if dd:
        ok_daily = verdict_symbol(dd["p_breach_daily"] < 0.05)
        ok_total = verdict_symbol(dd["ftmo_compatible"])
        print(f"\n  DD médian (sim)  : {dd['median_dd']:.1f}%")
        print(f"  DD P95 (sim)     : {dd['p95_dd']:.1f}%")
        print(f"  {ok_daily} P(breach daily {dd['daily_limit']}%)  : {dd['p_breach_daily']:.1%}")
        print(f"  {ok_total} P(breach total {dd['total_limit']}%)  : {dd['p_breach_total']:.1%}")
        print(f"  FTMO compatible  : {'OUI' if dd['ftmo_compatible'] else 'NON'}")

    # ── 6. Exit reasons ──
    print_section("6. RÉPARTITION DES SORTIES")
    by_exit = defaultdict(list)
    for t in clean_trades:
        by_exit[t["exit_reason"]].append(t["result_r"])
    for exit_type, rs in sorted(by_exit.items()):
        n = len(rs)
        exp = sum(rs) / n
        wins = sum(1 for r in rs if r > 0)
        print(f"  {exit_type:<20} {n:>4} trades  exp={exp:+.3f}R  WR={wins/n:.0%}")

    # ── 7. Verdict final ──
    print_section("7. VERDICT PROP FIRM")
    b_clean = bootstrap_exp(clean_r)
    c_clean = concentration_analysis(clean_r)
    rc50 = rolling_consistency(clean_trades, window=50)
    dd_clean = mc_drawdown(clean_r, risk_pct=0.5)

    checks = {
        "Edge significatif (IC95 > 0)": b_clean["significant"],
        "Tient sans top-3 trades": c_clean.get("by_trim", {}).get(3, {}).get("still_positive", False),
        "Consistance ≥65% fenêtres": not rc50.get("insufficient_data") and rc50.get("pct_positive", 0) >= 0.65,
        "FTMO DD compatible": dd_clean.get("ftmo_compatible", False),
    }
    print()
    for check, ok in checks.items():
        print(f"  {verdict_symbol(ok)} {check}")

    passed = sum(1 for ok in checks.values() if ok)
    total = len(checks)
    print(f"\n  Score : {passed}/{total} critères validés")
    if passed == total:
        print(f"  → ✅ PRÊT pour forward-test prop firm")
    elif passed >= 3:
        print(f"  → ⚠️  PARTIEL — corriger les points faibles avant live")
    else:
        print(f"  → ❌ PAS PRÊT — edge insuffisant ou trop concentré")

    print()


def main():
    parser = argparse.ArgumentParser(description="Analyse statistique d'un replay JSONL Arabesque")
    parser.add_argument("jsonl", help="Fichier JSONL de replay (dry_run_*.jsonl)")
    parser.add_argument("--spike-threshold", type=float, default=20.0,
                        help="Seuil |R| pour détecter les outliers suspects (défaut: 20)")
    parser.add_argument("--no-spike-filter", action="store_true",
                        help="Inclure les outliers dans l'analyse principale")
    parser.add_argument("--sims", type=int, default=5000, help="Simulations bootstrap/MC (défaut: 5000)")
    args = parser.parse_args()
    run_analysis(args)


if __name__ == "__main__":
    main()
