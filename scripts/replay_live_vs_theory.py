"""Replay live vs théorie : Δ_R = R_live − R_theorique trade par trade.

Pour chaque trade du journal live, retrouve la barre de signal sur parquet,
simule le trade pur (BE 0.3R / offset 0.20R / TP 2R / SL signal.sl) et
reporte l'écart Δ_R par rapport au R réalisé en live.

Permet de répondre **sans extrapolation** à : "ce setup, en théorie pure,
aurait donné quoi ?". Sépare l'exécution (slippage, spread, BE non armé)
du régime de marché et du bias backtest.

Limites connues :
- Bias H/L bidirectionnel ±0.05R (cf. project_backtest_bias.md).
- Si SL et TP touchés sur la même bougie, on prend SL en premier (conservateur).
- Pas de spread ni slippage simulés côté théorique : c'est le but.

Usage:
    python scripts/replay_live_vs_theory.py --since 2026-05-07T23:45
    python scripts/replay_live_vs_theory.py --last 30
    python scripts/replay_live_vs_theory.py --since J-30 --strategy extension
    python scripts/replay_live_vs_theory.py --sanity-check  # compare à /tmp/replay_trades.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "logs" / "trade_journal.jsonl"
OUT_LOG = ROOT / "logs" / "replay_live_vs_theory.jsonl"
SETTINGS = ROOT / "config" / "settings.yaml"
INSTRUMENTS = ROOT / "config" / "instruments.yaml"

INTERVAL_MAP = {"M1": "min1", "H1": "1h", "H4": "4h"}
STRAT_ALIAS = {"trend": "extension"}


def parse_since(s: str) -> datetime:
    if s.startswith("J-") or s.startswith("j-"):
        n = int(s[2:])
        return datetime.now(timezone.utc) - timedelta(days=n)
    if "T" in s:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def resolve_tf(strategy: str, instrument: str, settings: dict, instr_cfg: dict) -> str:
    """Détermine le timeframe attendu (H1/H4/M1) pour un (strategy, instrument)."""
    sa = settings.get("strategy_assignments", {}).get(strategy, {})
    if sa and instrument in (sa.get("instruments") or []):
        return sa.get("timeframe", "H1").upper()
    if strategy == "extension":
        meta = instr_cfg.get(instrument, {}) or {}
        return (meta.get("tf") or "H1").upper()
    return "H1"


def load_trades(since: datetime | None, until: datetime,
                strategy: str | None, broker: str | None) -> list[dict]:
    """Charge entries+exits du journal et matche par trade_id."""
    if not JOURNAL.exists():
        return []
    entries: dict[str, dict] = {}
    exits: dict[str, dict] = {}
    for line in JOURNAL.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev = e.get("event")
        if ev not in ("entry", "exit"):
            continue
        tid = e.get("trade_id")
        if not tid:
            continue
        strat = STRAT_ALIAS.get(e.get("strategy", ""), e.get("strategy", ""))
        e["_strat_norm"] = strat
        if strategy and strat != strategy:
            continue
        if broker and e.get("broker_id") != broker:
            continue
        ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        if since and ts < since:
            continue
        if ts > until:
            continue
        if ev == "entry":
            entries[tid] = e
        else:
            exits[tid] = e
    trades = []
    for tid, ent in entries.items():
        if tid in exits:
            trades.append({"entry": ent, "exit": exits[tid], "trade_id": tid})
    trades.sort(key=lambda t: t["entry"]["ts"])
    return trades


def simulate_pure(df: pd.DataFrame, entry_ts: pd.Timestamp, side: str,
                  entry_price: float, sl: float,
                  max_bars: int = 200) -> dict | None:
    """Simule un trade pur à partir d'entry_ts. BE 0.3R offset 0.20R, TP 2R."""
    if side == "LONG":
        risk = entry_price - sl
        if risk <= 0:
            return None
        tp = entry_price + 2.0 * risk
        be_sl = entry_price + 0.20 * risk
    else:
        risk = sl - entry_price
        if risk <= 0:
            return None
        tp = entry_price - 2.0 * risk
        be_sl = entry_price - 0.20 * risk

    df_after = df[df.index >= entry_ts]
    if df_after.empty:
        return None
    df_after = df_after.iloc[:max_bars]

    cur_sl = sl
    be_armed = False
    mfe_r = 0.0

    for i, (ts, row) in enumerate(df_after.iterrows()):
        h, l = float(row["High"]), float(row["Low"])
        if side == "LONG":
            mfe_bar = (h - entry_price) / risk
        else:
            mfe_bar = (entry_price - l) / risk
        if mfe_bar > mfe_r:
            mfe_r = mfe_bar
        if not be_armed and mfe_r >= 0.3:
            be_armed = True
            cur_sl = be_sl

        if side == "LONG":
            sl_hit = l <= cur_sl
            tp_hit = h >= tp
        else:
            sl_hit = h >= cur_sl
            tp_hit = l <= tp

        if sl_hit:
            r = (cur_sl - entry_price) / risk if side == "LONG" else (entry_price - cur_sl) / risk
            return {"r_theo": round(r, 3), "mfe_theo": round(mfe_r, 3),
                    "exit_reason_theo": "be_exit" if be_armed else "stop_loss",
                    "exit_ts_theo": ts.isoformat(), "n_bars": i + 1,
                    "be_armed_theo": be_armed}
        if tp_hit:
            return {"r_theo": 2.0, "mfe_theo": round(mfe_r, 3),
                    "exit_reason_theo": "take_profit",
                    "exit_ts_theo": ts.isoformat(), "n_bars": i + 1,
                    "be_armed_theo": be_armed}

    last_close = float(df_after.iloc[-1]["Close"])
    r = ((last_close - entry_price) / risk if side == "LONG"
         else (entry_price - last_close) / risk)
    return {"r_theo": round(r, 3), "mfe_theo": round(mfe_r, 3),
            "exit_reason_theo": "still_open",
            "exit_ts_theo": df_after.index[-1].isoformat(),
            "n_bars": len(df_after), "be_armed_theo": be_armed}


_DF_CACHE: dict[tuple[str, str], pd.DataFrame] = {}


def get_df(instrument: str, tf: str, fetch_start: str, fetch_end: str) -> pd.DataFrame | None:
    key = (instrument, tf)
    if key in _DF_CACHE:
        return _DF_CACHE[key]
    from arabesque.data.store import load_ohlc
    iv = INTERVAL_MAP.get(tf.upper(), tf.lower())
    try:
        df = load_ohlc(instrument, interval=iv, start=fetch_start, end=fetch_end)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    _DF_CACHE[key] = df
    return df


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", type=str, default=None)
    p.add_argument("--last", type=int, default=None)
    p.add_argument("--strategy", type=str, default=None)
    p.add_argument("--broker", type=str, default=None)
    p.add_argument("--no-persist", action="store_true")
    p.add_argument("--sanity-check", action="store_true",
                   help="Compare à /tmp/replay_trades.json (replay one-shot 2026-05-07)")
    p.add_argument("--max-bars", type=int, default=200,
                   help="Nb max de bougies simulées par trade (200 = ~8j H1, ~1.5mois H4)")
    args = p.parse_args()

    settings = yaml.safe_load(SETTINGS.read_text())
    instr_cfg = yaml.safe_load(INSTRUMENTS.read_text()) or {}

    until = datetime.now(timezone.utc)
    if args.sanity_check:
        # Lit /tmp/replay_trades.json pour bornes + comparaison directe
        ref_path = Path("/tmp/replay_trades.json")
        if not ref_path.exists():
            print("⚠️  /tmp/replay_trades.json absent — sanity check impossible.")
            return 1
        ref = json.loads(ref_path.read_text())
        ref_by_tid: dict[str, dict] = {}
        ts_min = None
        for r in ref:
            ts = datetime.fromisoformat(r["entry_ts"].replace("Z", "+00:00"))
            if ts_min is None or ts < ts_min:
                ts_min = ts
            # ref n'a pas de trade_id, on matche par (instr, side, entry_ts proche)
            ref_by_tid[(r["instr"], r["side"], r["entry_ts"][:16])] = r
        since = ts_min - timedelta(hours=1)
        print(f"📋 Sanity check vs /tmp/replay_trades.json — {len(ref)} trades depuis {since.isoformat()}")
    else:
        since = parse_since(args.since) if args.since else None
        if not since and not args.last:
            since = datetime.now(timezone.utc) - timedelta(days=30)

    trades = load_trades(since, until, args.strategy, args.broker)
    if args.last:
        trades = trades[-args.last:]
    if not trades:
        print("Aucun trade dans la fenêtre.")
        return 0

    by_strat = defaultdict(list)
    rows = []
    for t in trades:
        ent = t["entry"]
        ext = t["exit"]
        strat = ent["_strat_norm"]
        inst = ent["instrument"]
        side = ent["side"]
        entry_price = float(ent["entry_price"])
        sl = float(ent["sl"])
        r_live = float(ext.get("result_r", 0))
        mfe_live = float(ext.get("mfe_r", 0))
        entry_ts = pd.Timestamp(ent["ts"]).tz_convert("UTC") if pd.Timestamp(ent["ts"]).tzinfo else pd.Timestamp(ent["ts"], tz="UTC")
        tf = resolve_tf(strat, inst, settings, instr_cfg)

        # Charger parquet : marge -120j pour indicateurs (au cas où on en ajoute), +30j post-exit
        fetch_start = (entry_ts - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
        fetch_end = (pd.Timestamp(ext["ts"]) + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        df = get_df(inst, tf, fetch_start, fetch_end)
        if df is None:
            continue

        sim = simulate_pure(df, entry_ts, side, entry_price, sl, max_bars=args.max_bars)
        if sim is None:
            continue
        delta_r = round(r_live - sim["r_theo"], 3)
        row = {
            "trade_id": t["trade_id"],
            "strategy": strat,
            "instrument": inst,
            "side": side,
            "broker": ent.get("broker_id"),
            "entry_ts": ent["ts"],
            "tf": tf,
            "r_live": r_live,
            "r_theo": sim["r_theo"],
            "delta_r": delta_r,
            "mfe_live": mfe_live,
            "mfe_theo": sim["mfe_theo"],
            "be_set_live": bool(ext.get("be_set", False)),
            "be_armed_theo": sim["be_armed_theo"],
            "exit_reason_live": ext.get("exit_reason"),
            "exit_reason_theo": sim["exit_reason_theo"],
        }
        rows.append(row)
        by_strat[strat].append(row)

    if not rows:
        print("Aucun trade simulable (parquet manquant ou borne hors plage).")
        return 0

    # Sortie console
    n = len(rows)
    sum_live = sum(r["r_live"] for r in rows)
    sum_theo = sum(r["r_theo"] for r in rows)
    sum_delta = sum(r["delta_r"] for r in rows)
    print()
    fenetre = (f"--last {args.last}" if args.last
               else f"depuis {since.strftime('%Y-%m-%d %H:%M UTC') if since else 'tout'}")
    print(f"=== Replay live vs théorie — {fenetre} ===")
    print(f"  n={n}  ΣR_live={sum_live:+.2f}  ΣR_theo={sum_theo:+.2f}  ΣΔR={sum_delta:+.2f}  meanΔR={sum_delta/n:+.3f}R")
    print()
    print(f"  {'Stratégie':<12s} {'n':>4s} {'ΣR_live':>9s} {'ΣR_theo':>9s} {'ΣΔR':>8s} {'meanΔR':>9s}")
    print("  " + "-" * 60)
    for strat, srows in sorted(by_strat.items()):
        ns = len(srows)
        sl_l = sum(r["r_live"] for r in srows)
        sl_t = sum(r["r_theo"] for r in srows)
        sd = sum(r["delta_r"] for r in srows)
        print(f"  {strat:<12s} {ns:>4d} {sl_l:>+9.2f} {sl_t:>+9.2f} {sd:>+8.2f} {sd/ns:>+8.3f}R")
    print()

    # Top 5 plus gros écarts (live moins bon que théorie)
    rows_sorted = sorted(rows, key=lambda r: r["delta_r"])
    print("  Top 5 trades où live < théorie (exécution la plus pénalisante) :")
    for r in rows_sorted[:5]:
        print(f"    {r['entry_ts'][:16]} {r['instrument']} {r['side']:<5s} ({r['broker']}) "
              f"R_live={r['r_live']:+.2f} R_theo={r['r_theo']:+.2f} Δ={r['delta_r']:+.3f} "
              f"BE_live={r['be_set_live']} BE_theo={r['be_armed_theo']}")
    print()

    # Sanity check
    if args.sanity_check:
        print("=== Sanity check : delta_r de chaque trade vs /tmp/replay_trades.json ===")
        ref = json.loads(Path("/tmp/replay_trades.json").read_text())
        ref_lookup = {(r["instr"], r["side"], r["entry_ts"][:16]): r for r in ref}
        match = 0
        diverg = []
        for r in rows:
            key = (r["instrument"], r["side"], r["entry_ts"][:16])
            if key in ref_lookup:
                ref_r = ref_lookup[key]
                ref_delta = ref_r["delta_r"]
                gap = abs(r["delta_r"] - ref_delta)
                match += 1
                if gap > 0.30:
                    diverg.append((r, ref_r, gap))
        print(f"  matched: {match}/{len(rows)} (live) ↔ {match}/{len(ref)} (ref)")
        print("  Note : le ref /tmp/replay_trades.json utilise bt_r_naive (sans BE ni TP cap).")
        print("  Mon script applique la stratégie complète (BE 0.3R, TP 2R). Divergences numériques attendues.")
        print("  Sanity OK = matching complet trade↔ref. Les Δ_R doivent par construction différer.")
        if diverg:
            print(f"  Aperçu des 5 plus gros écarts (lecture : modèle naïf vs stratégie complète) :")
            for r, ref_r, gap in diverg[:5]:
                print(f"    {r['instrument']:<8s} {r['entry_ts'][:16]} "
                      f"new={r['delta_r']:+.2f} ref={ref_r['delta_r']:+.2f} gap={gap:.2f}")
        print()

    # Persistance
    if not args.no_persist:
        OUT_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "since": since.isoformat() if since else None,
            "last": args.last,
            "strategy_filter": args.strategy,
            "broker_filter": args.broker,
            "n_total": n,
            "sum_r_live": round(sum_live, 3),
            "sum_r_theo": round(sum_theo, 3),
            "sum_delta_r": round(sum_delta, 3),
            "mean_delta_r": round(sum_delta / n, 4),
            "by_strategy": {
                strat: {
                    "n": len(srows),
                    "sum_r_live": round(sum(r["r_live"] for r in srows), 3),
                    "sum_r_theo": round(sum(r["r_theo"] for r in srows), 3),
                    "mean_delta_r": round(sum(r["delta_r"] for r in srows) / len(srows), 4),
                }
                for strat, srows in by_strat.items()
            },
        }
        with OUT_LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")
        # JSONL trade-par-trade séparé pour réutilisation
        per_trade_log = OUT_LOG.with_name("replay_live_vs_theory_trades.jsonl")
        with per_trade_log.open("a") as f:
            for r in rows:
                r2 = dict(r)
                r2["audit_ts"] = record["ts"]
                f.write(json.dumps(r2) + "\n")
        print(f"  Persisté résumé : {OUT_LOG}")
        print(f"  Persisté trades : {per_trade_log}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
