"""Audit COUVERTURE & BIAIS de sélection (live vs population backtest).

Décompose l'écart live↔backtest que `audit_edge_live_vs_backtest` ne sépare pas :
le live, borné par le cap + filtres, prend un sous-échantillon des signaux. Ce
script énumère TOUS les signaux théoriques (population backtest) extension+glissade
sur les targets live, calcule r_theo (simulate_pure), puis matche avec les entries
live → PRIS vs RATÉ, et compare leur R moyen.

Verdicts :
  - low_coverage_variance : pris ≈ ratés en qualité → sous-échantillonnage/variance
    (le cap aide ; l'edge de la population reste la référence).
  - mild_tilt            : ratés légèrement meilleurs (écart 0.15-0.30R) → à surveiller.
  - selection_bias       : ratés NETTEMENT meilleurs (écart > 0.30R sur n_pris≥20)
    → le live rate systématiquement les bons signaux (filtre live-only à corriger).
  - low_n                : n_pris < 15, inconclusif.

READ-ONLY. Aucune modif live/config. Source d'autorité : docs/VALIDATION_CONTRACT.md.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd
import yaml

import replay_signals_vs_live as rsl
from replay_live_vs_theory import simulate_pure, floor_to_tf
from arabesque.data.store import load_ohlc, get_last_source_info

STRATS = {"extension", "glissade"}
IV = {"M1": "min1", "H1": "1h", "H4": "4h"}
TOL = pd.Timedelta(hours=3)
BIAS_THR, TILT_THR = 0.30, 0.15


def gen_signals(strat, tf, instr, since, until):
    iv = IV.get(tf.upper(), tf.lower())
    fs = (since - pd.Timedelta(days=130)).strftime("%Y-%m-%d")
    fe = (until + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        df = load_ohlc(instr, interval=iv, start=fs, end=fe)
    except Exception:
        return []
    src = get_last_source_info()
    if src is None or not src.source.startswith("parquet") or df is None or df.empty or len(df) < 250:
        return []
    sg = rsl._instantiate(strat)
    dfp = sg.prepare(df)
    try:
        signals = sg.generate_signals(dfp, instr)
    except Exception:
        return []
    tf_delta = df.index[1] - df.index[0]
    raw = []
    for i, sig in signals:
        if i + 1 >= len(df.index):
            continue
        ets = df.index[i] + tf_delta
        if ets < since or ets > until:
            continue
        raw.append((ets, sig))
    kept = set(rsl._dedup_sessions([e for e, _ in raw], tf))
    out = []
    for ets, sig in raw:
        if ets not in kept:
            continue
        bar_ts = floor_to_tf(ets, tf)
        after = dfp[dfp.index >= bar_ts]
        if after.empty:
            continue
        sim = simulate_pure(dfp, ets, sig.side.name, float(after.iloc[0]["Open"]), float(sig.sl), tf)
        if sim is None:
            continue
        out.append({"strat": strat, "instr": instr,
                    "entry_ts": pd.Timestamp(sim["entry_ts_theo"]), "r": float(sim["r_theo"])})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-05-16T08:44")
    ap.add_argument("--no-persist", action="store_true")
    args = ap.parse_args()
    since = pd.Timestamp(args.since, tz="UTC")
    until = pd.Timestamp.now(tz="UTC")

    settings = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
    instruments_cfg = yaml.safe_load((ROOT / "config/instruments.yaml").read_text())
    targets = [t for t in rsl._build_targets(settings, instruments_cfg) if t[0] in STRATS]

    theo = []
    for strat, tf, instr in targets:
        theo.extend(gen_signals(strat, tf, instr, since, until))

    live_entries = defaultdict(list)
    for line in open(ROOT / "logs/trade_journal.jsonl"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("event") != "entry" or d.get("strategy") not in STRATS:
            continue
        try:
            t = pd.Timestamp(d.get("ts"))
        except Exception:
            continue
        if since - pd.Timedelta(hours=6) <= t <= until:
            live_entries[(d.get("strategy"), d.get("instrument"))].append(t)

    def taken(sig):
        cands = live_entries.get((sig["strat"], sig["instr"]), [])
        return any(abs((t - sig["entry_ts"]).total_seconds()) <= TOL.total_seconds() for t in cands)

    pris = [s for s in theo if taken(s)]
    rates = [s for s in theo if not taken(s)]

    def mean_r(rows):
        return sum(x["r"] for x in rows) / len(rows) if rows else 0.0

    n_theo, n_pris, n_rate = len(theo), len(pris), len(rates)
    cov = 100 * n_pris / n_theo if n_theo else 0
    mr_pris, mr_rate, mr_pop = mean_r(pris), mean_r(rates), mean_r(theo)
    ecart = mr_rate - mr_pris

    if n_pris < 15:
        verdict = "low_n"
    elif ecart > BIAS_THR and n_pris >= 20:
        verdict = "selection_bias"
    elif ecart > TILT_THR:
        verdict = "mild_tilt"
    else:
        verdict = "low_coverage_variance"

    lines = [
        f"# Audit couverture & biais de sélection",
        f"- Généré : {until.isoformat()}",
        f"- Fenêtre : {since.date()} → {until.date()} (extension+glissade)",
        f"- Population théorique : n={n_theo}  ΣR_theo={sum(x['r'] for x in theo):+.2f}  meanR_theo={mr_pop:+.3f}",
        f"- PRIS en live : n={n_pris} ({cov:.0f}% couverture)  meanR_theo={mr_pris:+.3f}",
        f"- RATÉS : n={n_rate}  meanR_theo={mr_rate:+.3f}",
        f"- Écart (raté − pris) : {ecart:+.3f}R",
        f"- **Verdict : {verdict}**",
    ]
    report = "\n".join(lines)
    print(report)

    if not args.no_persist:
        (ROOT / "logs" / "selection_coverage_latest.md").write_text(report + "\n")
        rec = {"ts": until.isoformat(), "since": str(since.date()), "n_theo": n_theo,
               "n_pris": n_pris, "coverage_pct": round(cov, 1), "meanR_pop": round(mr_pop, 4),
               "meanR_pris": round(mr_pris, 4), "meanR_rate": round(mr_rate, 4),
               "ecart": round(ecart, 4), "verdict": verdict}
        with open(ROOT / "logs" / "selection_coverage.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")

    return verdict


if __name__ == "__main__":
    main()
