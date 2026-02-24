#!/usr/bin/env python3
"""
Arabesque — Analyse complète d'un replay dry-run.

Usage:
    python scripts/analyze_replay_v2.py dry_run_20260222_183814.jsonl

    Options:
        --compare FILE    Compare avec un autre replay
        --spike N         Seuil MFE pour détecter les spikes (défaut: 10)
        --no-spike-filter Ne pas filtrer les spikes
        --grid            Afficher la grille de simulation BE/TP

Remplace analyze_replay.py (qui ne prenait qu'un fichier à la fois).
"""

import json
import sys
import argparse
import statistics
import math
from collections import Counter, defaultdict
from pathlib import Path


def load_trades(path):
    """Charge les trades depuis un JSONL."""
    trades = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            t = json.loads(line)
            if t.get("type") == "trade":
                trades.append(t)
    return trades


def basic_stats(trades, label=""):
    """Stats de base."""
    n = len(trades)
    if n == 0:
        print(f"  Aucun trade.")
        return

    wins = [t for t in trades if t["result_r"] > 0]
    losses = [t for t in trades if t["result_r"] <= 0]
    wr = len(wins) / n
    avg_win = statistics.mean([t["result_r"] for t in wins]) if wins else 0
    avg_loss = statistics.mean([t["result_r"] for t in losses]) if losses else 0
    total = sum(t["result_r"] for t in trades)
    exp = total / n

    print(f"\n{'=' * 72}")
    print(f"  {label or Path(sys.argv[1]).stem}  —  {n} trades")
    print(f"{'=' * 72}")
    print(f"  WR: {wr:.1%}    Exp: {exp:+.4f}R    Total: {total:+.1f}R")
    print(f"  Avg win: {avg_win:+.3f}R    Avg loss: {avg_loss:+.3f}R")
    print(f"  Profit Factor: {abs(sum(t['result_r'] for t in wins)) / max(abs(sum(t['result_r'] for t in losses)), 0.01):.2f}")


def exit_breakdown(trades):
    """Breakdown par type de sortie."""
    print(f"\n  EXIT BREAKDOWN")
    print(f"  {'Type':<20} {'N':>5} {'%':>6} {'WR':>6} {'AvgR':>8} {'TotalR':>8}")
    print(f"  {'-' * 58}")

    by_exit = defaultdict(list)
    for t in trades:
        by_exit[t["exit_reason"]].append(t)

    for et, ts in sorted(by_exit.items(), key=lambda x: -len(x[1])):
        n = len(ts)
        wins = sum(1 for t in ts if t["result_r"] > 0)
        avg = sum(t["result_r"] for t in ts) / n
        total = sum(t["result_r"] for t in ts)
        wr = wins / n
        print(f"  {et:<20} {n:>5} {n / len(trades) * 100:>5.1f}% {wr:>5.0%} {avg:>+7.3f} {total:>+7.1f}")


def strategy_breakdown(trades):
    """Breakdown par type de stratégie."""
    by_strat = defaultdict(list)
    for t in trades:
        by_strat[t.get("strategy_type", "unknown")].append(t)

    if len(by_strat) <= 1 and "unknown" in by_strat:
        return

    print(f"\n  STRATEGY BREAKDOWN")
    print(f"  {'Strategy':<20} {'N':>5} {'WR':>6} {'Exp':>8} {'TotalR':>8}")
    print(f"  {'-' * 50}")
    for strat, ts in sorted(by_strat.items()):
        n = len(ts)
        wr = sum(1 for t in ts if t["result_r"] > 0) / n
        total = sum(t["result_r"] for t in ts)
        exp = total / n
        print(f"  {strat:<20} {n:>5} {wr:>5.0%} {exp:>+7.3f} {total:>+7.1f}")


def side_breakdown(trades):
    """Breakdown LONG vs SHORT."""
    print(f"\n  LONG vs SHORT")
    print(f"  {'Side':<8} {'N':>5} {'WR':>6} {'Exp':>8} {'TotalR':>8}")
    print(f"  {'-' * 40}")
    for side in ["LONG", "SHORT"]:
        ts = [t for t in trades if t["side"] == side]
        if not ts:
            continue
        n = len(ts)
        wr = sum(1 for t in ts if t["result_r"] > 0) / n
        total = sum(t["result_r"] for t in ts)
        exp = total / n
        print(f"  {side:<8} {n:>5} {wr:>5.0%} {exp:>+7.3f} {total:>+7.1f}")


def duration_analysis(trades):
    """WR par durée de trade."""
    print(f"\n  WR PAR DURÉE")
    print(f"  {'Bucket':<12} {'N':>5} {'WR':>6} {'Exp':>8} {'TotalR':>8}")
    print(f"  {'-' * 44}")
    for lo, hi in [(0, 3), (3, 6), (6, 12), (12, 24), (24, 48), (48, 999)]:
        ts = [t for t in trades if lo <= t["bars_open"] < hi]
        if not ts:
            continue
        n = len(ts)
        wr = sum(1 for t in ts if t["result_r"] > 0) / n
        total = sum(t["result_r"] for t in ts)
        exp = total / n
        print(f"  [{lo:>3},{hi:>3})h {n:>5} {wr:>5.0%} {exp:>+7.3f} {total:>+7.1f}")


def mfe_analysis(trades):
    """Analyse MFE — le potentiel non capturé."""
    print(f"\n  MFE ANALYSIS")
    all_mfe = [t["mfe_r"] for t in trades]
    sl = [t for t in trades if t["exit_reason"] == "exit_sl"]
    sl_mfe = [t["mfe_r"] for t in sl] if sl else []

    print(f"  All trades: MFE median={statistics.median(all_mfe):.2f}R  mean={statistics.mean(all_mfe):.2f}R")
    if sl_mfe:
        print(f"  SL losers:  MFE median={statistics.median(sl_mfe):.2f}R  N={len(sl)}")
        for thresh in [0.1, 0.2, 0.3, 0.5, 1.0]:
            n = sum(1 for m in sl_mfe if m >= thresh)
            print(f"    MFE >= {thresh:.1f}R: {n} SL-losers ({n / len(sl) * 100:.0f}%)")


def instrument_table(trades):
    """P&L par instrument."""
    by_inst = defaultdict(list)
    for t in trades:
        by_inst[t["instrument"]].append(t)

    print(f"\n  P&L PAR INSTRUMENT")
    print(f"  {'Inst':<10} {'N':>4} {'WR':>6} {'Exp':>8} {'TotalR':>8} {'SL%':>5}")
    print(f"  {'-' * 46}")
    for inst in sorted(by_inst.keys(), key=lambda i: -sum(t["result_r"] for t in by_inst[i])):
        ts = by_inst[inst]
        n = len(ts)
        wr = sum(1 for t in ts if t["result_r"] > 0) / n
        total = sum(t["result_r"] for t in ts)
        exp = total / n
        sl_pct = sum(1 for t in ts if t["exit_reason"] == "exit_sl") / n
        print(f"  {inst:<10} {n:>4} {wr:>5.0%} {exp:>+7.3f} {total:>+7.1f} {sl_pct:>4.0%}")


def spike_detection(trades, threshold=10.0):
    """Détecte les spikes de données (MFE anormalement élevé)."""
    spikes = [t for t in trades if t["mfe_r"] > threshold]
    if not spikes:
        print(f"\n  SPIKE DETECTION: aucun spike (MFE > {threshold}R)")
        return []

    print(f"\n  ⚠️  SPIKE DETECTION: {len(spikes)} trades avec MFE > {threshold}R")
    for t in sorted(spikes, key=lambda x: -x["mfe_r"]):
        print(f"    {t['instrument']:<8} MFE={t['mfe_r']:.1f}R result={t['result_r']:+.1f}R "
              f"bars={t['bars_open']} {t['exit_reason']}")
    return spikes


def be_grid_simulation(trades):
    """Grille de simulation post-hoc des stratégies BE/TP."""
    print(f"\n  GRILLE DE SIMULATION BE/TP")
    print(f"  {'Config':<28} {'WR':>6} {'AvgW':>7} {'Exp':>8} {'TotalR':>8}")
    print(f"  {'-' * 62}")

    configs = [
        (None, 0.3, 0.15, "BE 0.3/0.15 ★"),
        (None, 0.3, 0.20, "BE 0.3/0.20"),
        (None, 0.5, 0.25, "BE 0.5/0.25 (ancien)"),
        (None, 1.0, 0.05, "BE 1.0/0.05 (v3.0)"),
        (1.0, 0.3, 0.15, "TP 1.0 + BE 0.3/0.15"),
        (1.5, 0.3, 0.15, "TP 1.5 + BE 0.3/0.15"),
        (None, None, None, "Pas de BE (ref)"),
    ]

    for tp_r, be_t, be_o, name in configs:
        results = []
        for t in trades:
            mfe = t["mfe_r"]
            actual = t["result_r"]

            if tp_r and mfe >= tp_r:
                results.append(tp_r)
            elif be_t and mfe >= be_t:
                if actual < be_o:
                    results.append(be_o)
                else:
                    results.append(actual)
            else:
                results.append(-1.0)

        n = len(results)
        wins = sum(1 for r in results if r > 0)
        wr = wins / n
        avg_w = statistics.mean([r for r in results if r > 0]) if wins else 0
        total = sum(results)
        exp = total / n
        print(f"  {name:<28} {wr:>5.1%} {avg_w:>+6.2f}R {exp:>+7.3f}R {total:>+7.1f}R")


def prop_firm_score(trades):
    """Score de compatibilité prop firm."""
    n = len(trades)
    wr = sum(1 for t in trades if t["result_r"] > 0) / n
    total = sum(t["result_r"] for t in trades)
    exp = total / n

    # Simulate equity curve for DD
    cum = 0
    peak = 0
    max_dd_r = 0
    for t in sorted(trades, key=lambda x: x.get("ts_exit", "")):
        cum += t["result_r"]
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd_r:
            max_dd_r = dd

    # Auto-detect risk % from trade data
    # Use the modal (most common) risk_cash as the nominal risk
    risk_cash_values = [t.get("risk_cash", 0) for t in trades if t.get("risk_cash")]
    if risk_cash_values:
        # Most trades have nominal risk (before DD reduction)
        # The mode approximates the unreduced risk = start_balance × risk_pct
        nominal_risk = max(set(risk_cash_values), key=risk_cash_values.count)
        # start_balance is not in JSONL, estimate from nominal_risk
        # Common values: 400→0.40%, 500→0.50%
        risk_pct = round(nominal_risk / 1000, 2)  # assumes 100k start
        if risk_pct < 0.1 or risk_pct > 2.0:
            risk_pct = 0.5  # fallback
    else:
        risk_pct = 0.5  # fallback if no risk_cash field

    dd_pct = max_dd_r * risk_pct
    total_pct = total * risk_pct

    print(f"\n  PROP FIRM READINESS (risk = {risk_pct}% par trade, auto-détecté)")
    print(f"  {'-' * 50}")

    # Criteria
    checks = []

    # 1. Expectancy positive (IC95)
    rs = [t["result_r"] for t in trades]
    se = statistics.stdev(rs) / math.sqrt(n) if n > 1 else 999
    ic95_low = exp - 1.96 * se
    ok1 = ic95_low > 0
    checks.append(ok1)
    print(f"  {'✅' if ok1 else '❌'} Expectancy IC95 bas: {ic95_low:+.4f}R {'> 0' if ok1 else '< 0'}")

    # 2. WR >= 55%
    ok2 = wr >= 0.55
    checks.append(ok2)
    print(f"  {'✅' if ok2 else '❌'} Win Rate: {wr:.1%} {'>= 55%' if ok2 else '< 55%'}")

    # 3. Max DD < 10%
    ok3 = dd_pct < 10
    checks.append(ok3)
    print(f"  {'✅' if ok3 else '❌'} Max DD: {dd_pct:.1f}% {'< 10%' if ok3 else '>= 10%'}")

    # 4. Total return > 10% (challenge target)
    ok4 = total_pct > 10
    checks.append(ok4)
    print(f"  {'✅' if ok4 else '❌'} Total return: {total_pct:+.1f}% {'>= 10%' if ok4 else '< 10%'}")

    # 5. Days to 10% estimate
    # Auto-detect period length from trade timestamps
    ts_entries = [t.get("ts_entry", "") for t in trades if t.get("ts_entry")]
    if len(ts_entries) >= 2:
        from datetime import datetime
        try:
            first = datetime.fromisoformat(ts_entries[0].replace("Z", "+00:00"))
            last = datetime.fromisoformat(ts_entries[-1].replace("Z", "+00:00"))
            period_days = max(1, (last - first).days)
        except Exception:
            period_days = 90  # fallback
    else:
        period_days = 90  # fallback: assume 3 months
    trades_per_day = n / period_days
    daily_exp_pct = exp * risk_pct * trades_per_day
    days_10 = 10 / daily_exp_pct if daily_exp_pct > 0 else float("inf")
    ok5 = days_10 < 45
    checks.append(ok5)
    print(f"  {'✅' if ok5 else '❌'} Est. jours pour +10%: {days_10:.0f}j {'< 45j' if ok5 else '>= 45j'}")

    score = sum(checks)
    print(f"\n  SCORE: {score}/5")
    return score


def main():
    parser = argparse.ArgumentParser(description="Analyse complète d'un replay Arabesque")
    parser.add_argument("jsonl", help="Fichier JSONL du replay")
    parser.add_argument("--compare", help="Fichier JSONL de comparaison", default=None)
    parser.add_argument("--spike", type=float, default=10.0, help="Seuil MFE spike")
    parser.add_argument("--no-spike-filter", action="store_true")
    parser.add_argument("--grid", action="store_true", help="Afficher grille simulation BE/TP")
    args = parser.parse_args()

    trades = load_trades(args.jsonl)

    if not args.no_spike_filter:
        spikes = spike_detection(trades, args.spike)
        if spikes:
            print(f"  (ces trades faussent les stats — filtrage recommandé)")

    basic_stats(trades)
    exit_breakdown(trades)
    strategy_breakdown(trades)
    side_breakdown(trades)
    duration_analysis(trades)
    mfe_analysis(trades)
    instrument_table(trades)
    prop_firm_score(trades)

    if args.grid:
        be_grid_simulation(trades)

    if args.compare:
        print(f"\n{'#' * 72}")
        print(f"  COMPARAISON")
        print(f"{'#' * 72}")
        trades_cmp = load_trades(args.compare)
        basic_stats(trades_cmp, f"Comparaison: {args.compare}")
        exit_breakdown(trades_cmp)


if __name__ == "__main__":
    main()
