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

# Pas de bar en minutes pour floor → bar boundary
TF_MINUTES = {"M1": 1, "H1": 60, "H4": 240}


def floor_to_tf(ts: pd.Timestamp, tf: str) -> pd.Timestamp:
    """Floor un timestamp au début de la bougie de TF donné (UTC)."""
    minutes = TF_MINUTES.get(tf.upper(), 60)
    epoch_min = int(ts.timestamp() // 60)
    floored_min = (epoch_min // minutes) * minutes
    return pd.Timestamp(floored_min * 60, unit="s", tz="UTC")


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
    """Détermine le timeframe attendu (H1/H4/M1) pour un (strategy, instrument).

    IMPORTANT : le live lit `timeframe` (cf. arabesque/execution/live.py),
    `tf` reste accepté comme alias legacy. Sans cette unification, Extension
    crypto (timeframe: H4) était évaluée en H1 — Δ_R faussés.
    """
    sa = settings.get("strategy_assignments", {}).get(strategy, {})
    if sa and instrument in (sa.get("instruments") or []):
        return sa.get("timeframe", "H1").upper()
    if strategy == "extension":
        meta = instr_cfg.get(instrument, {}) or {}
        return (meta.get("timeframe") or meta.get("tf") or "H1").upper()
    return "H1"


def load_trades(since: datetime | None, until: datetime,
                strategy: str | None, broker: str | None) -> list[dict]:
    """Charge entries+exits du journal et matche par (trade_id, broker_id).

    Un même signal pris sur FTMO ET GFT produit deux trades distincts à
    mesurer : avant le fix 2026-05-19, la clé `trade_id` seule causait
    l'écrasement du 1er exit par le 2e (35 paires sous-comptées dans le
    journal, n divisé par ~2 → meanΔR biaisé sur la moitié des données).
    """
    if not JOURNAL.exists():
        return []
    entries: dict[tuple[str, str], dict] = {}
    exits: dict[tuple[str, str], dict] = {}
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
        bid = e.get("broker_id") or "?"
        if broker and bid != broker:
            continue
        ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        if since and ts < since:
            continue
        if ts > until:
            continue
        key = (tid, bid)
        if ev == "entry":
            entries[key] = e
        else:
            exits[key] = e
    trades = []
    for key, ent in entries.items():
        if key in exits:
            trades.append({
                "entry": ent,
                "exit": exits[key],
                "trade_id": key[0],
                "broker_id": key[1],
            })
    trades.sort(key=lambda t: t["entry"]["ts"])
    return trades


def simulate_pure(df: pd.DataFrame, entry_ts: pd.Timestamp, side: str,
                  entry_price: float, sl: float, tf: str,
                  max_bars: int = 200) -> dict | None:
    """Simule un trade pur sur la bougie qui contient entry_ts.

    Aligné sur la convention du BT principal (position_manager._check_sl_tp_intrabar
    + _update_breakeven) :
    1. **Check SL/TP d'abord** avec le SL courant (avant BE update de cette barre).
    2. Si SL ET TP touchés sur la même barre → **SL** (convention pessimiste).
    3. PUIS update MFE et BE pour la barre suivante.

    Sans cette discipline, on assumerait que H vient avant L (LONG) — biais
    optimiste interdit par la boussole projet.

    BE 0.3R offset 0.20R, TP 2R. Le simulateur entre à l'open de la bougie qui
    contient entry_ts (anti-lookahead : c'est la bougie post-signal, déjà
    projetée par l'engine). Le risk_distance utilisé reste celui de la consigne
    live (entry_price → sl), pour mesurer purement la déviation d'exécution
    post-entry et exposer le slippage d'entrée séparément.
    """
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

    bar_ts = floor_to_tf(entry_ts, tf)
    df_after = df[df.index >= bar_ts]
    if df_after.empty:
        return None
    df_after = df_after.iloc[:max_bars]

    entry_ts_theo = df_after.index[0]
    entry_price_theo = float(df_after.iloc[0]["Open"])

    cur_sl = sl
    be_armed = False
    be_armed_ts = None
    be_armed_price = None
    mfe_r = 0.0

    for i, (ts, row) in enumerate(df_after.iterrows()):
        h, lo = float(row["High"]), float(row["Low"])

        # 1. MFE tracking (pas d'effet sur le SL de cette barre)
        if side == "LONG":
            mfe_bar = (h - entry_price) / risk
        else:
            mfe_bar = (entry_price - lo) / risk
        if mfe_bar > mfe_r:
            mfe_r = mfe_bar

        # 2. Check SL/TP avec cur_sl AVANT update BE de cette barre
        if side == "LONG":
            sl_hit = lo <= cur_sl
            tp_hit = h >= tp
        else:
            sl_hit = h >= cur_sl
            tp_hit = lo <= tp

        # 3. Ambiguïté SL+TP touchés sur la même barre → SL (pessimiste, BT convention)
        if sl_hit and tp_hit:
            r = (cur_sl - entry_price) / risk if side == "LONG" else (entry_price - cur_sl) / risk
            return {
                "r_theo": round(r, 3),
                "mfe_theo": round(mfe_r, 3),
                "exit_reason_theo": "be_exit" if be_armed else "stop_loss",
                "exit_ts_theo": ts.isoformat(),
                "exit_price_theo": round(cur_sl, 5),
                "n_bars": i + 1,
                "be_armed_theo": be_armed,
                "be_armed_ts_theo": be_armed_ts.isoformat() if be_armed_ts is not None else None,
                "be_armed_price_theo": round(be_armed_price, 5) if be_armed_price is not None else None,
                "entry_ts_theo": entry_ts_theo.isoformat(),
                "entry_price_theo": round(entry_price_theo, 5),
                "ambiguous_bar": True,
            }
        if sl_hit:
            r = (cur_sl - entry_price) / risk if side == "LONG" else (entry_price - cur_sl) / risk
            return {
                "r_theo": round(r, 3),
                "mfe_theo": round(mfe_r, 3),
                "exit_reason_theo": "be_exit" if be_armed else "stop_loss",
                "exit_ts_theo": ts.isoformat(),
                "exit_price_theo": round(cur_sl, 5),
                "n_bars": i + 1,
                "be_armed_theo": be_armed,
                "be_armed_ts_theo": be_armed_ts.isoformat() if be_armed_ts is not None else None,
                "be_armed_price_theo": round(be_armed_price, 5) if be_armed_price is not None else None,
                "entry_ts_theo": entry_ts_theo.isoformat(),
                "entry_price_theo": round(entry_price_theo, 5),
                "ambiguous_bar": False,
            }
        if tp_hit:
            return {
                "r_theo": 2.0,
                "mfe_theo": round(mfe_r, 3),
                "exit_reason_theo": "take_profit",
                "exit_ts_theo": ts.isoformat(),
                "exit_price_theo": round(tp, 5),
                "n_bars": i + 1,
                "be_armed_theo": be_armed,
                "be_armed_ts_theo": be_armed_ts.isoformat() if be_armed_ts is not None else None,
                "be_armed_price_theo": round(be_armed_price, 5) if be_armed_price is not None else None,
                "entry_ts_theo": entry_ts_theo.isoformat(),
                "entry_price_theo": round(entry_price_theo, 5),
                "ambiguous_bar": False,
            }

        # 4. BE armement pour la barre SUIVANTE si MFE ≥ 0.3R
        if not be_armed and mfe_r >= 0.3:
            be_armed = True
            cur_sl = be_sl
            be_armed_ts = ts
            be_armed_price = h if side == "LONG" else lo

    last_close = float(df_after.iloc[-1]["Close"])
    r = ((last_close - entry_price) / risk if side == "LONG"
         else (entry_price - last_close) / risk)
    return {
        "r_theo": round(r, 3),
        "mfe_theo": round(mfe_r, 3),
        "exit_reason_theo": "still_open",
        "exit_ts_theo": df_after.index[-1].isoformat(),
        "exit_price_theo": round(last_close, 5),
        "n_bars": len(df_after),
        "be_armed_theo": be_armed,
        "be_armed_ts_theo": be_armed_ts.isoformat() if be_armed_ts is not None else None,
        "be_armed_price_theo": round(be_armed_price, 5) if be_armed_price is not None else None,
        "entry_ts_theo": entry_ts_theo.isoformat(),
        "entry_price_theo": round(entry_price_theo, 5),
        "ambiguous_bar": False,
    }


_DF_CACHE: dict[tuple[str, str], pd.DataFrame] = {}

# Strict-data mode : refuse les fallbacks Yahoo (source ≠ parquet) sauf
# --allow-yahoo. Yahoo peut diverger de la source de validation locale.
_ALLOW_YAHOO: bool = False
_SKIPPED_NO_PARQUET: set[tuple[str, str]] = set()  # (instrument, tf)


def get_df(instrument: str, tf: str, fetch_start: str, fetch_end: str) -> pd.DataFrame | None:
    key = (instrument, tf)
    if key in _DF_CACHE:
        return _DF_CACHE[key]
    from arabesque.data.store import load_ohlc, get_last_source_info
    iv = INTERVAL_MAP.get(tf.upper(), tf.lower())
    try:
        df = load_ohlc(instrument, interval=iv, start=fetch_start, end=fetch_end)
    except Exception:
        _SKIPPED_NO_PARQUET.add((instrument, tf))
        return None
    src = get_last_source_info()
    if not _ALLOW_YAHOO and (src is None or not src.source.startswith("parquet")):
        _SKIPPED_NO_PARQUET.add((instrument, tf))
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
    p.add_argument("--allow-yahoo", action="store_true",
                   help="Autoriser le fallback Yahoo si parquet manquant. "
                        "Par défaut (strict-data), un instrument sans parquet est skippé.")
    args = p.parse_args()

    global _ALLOW_YAHOO
    _ALLOW_YAHOO = bool(args.allow_yahoo)

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

        # Sanity guard : si le prix parquet diffère du prix live d'un facteur ≥ 5,
        # on est face à un mismatch de price_scale (bug data ; ex: paires JPY
        # stockées en 1e5 au lieu de 1e3). Skipper et tracer.
        first_bar_open = float(df[df.index >= floor_to_tf(entry_ts, tf)].iloc[0]["Open"]) if not df[df.index >= floor_to_tf(entry_ts, tf)].empty else 0
        if first_bar_open > 0 and entry_price > 0:
            ratio = entry_price / first_bar_open
            if ratio > 5 or ratio < 0.2:
                print(f"⚠️  scale mismatch {inst} (live={entry_price} parquet={first_bar_open}) → trade ignoré")
                continue

        sim = simulate_pure(df, entry_ts, side, entry_price, sl, tf, max_bars=args.max_bars)
        if sim is None:
            continue
        delta_r = round(r_live - sim["r_theo"], 3)

        # Slippage entrée : (theo − live) × side_sign / risk. Négatif = défavorable au trader.
        risk_distance = abs(entry_price - sl)
        side_sign = 1.0 if side == "LONG" else -1.0
        slip_entry_R = round(
            (sim["entry_price_theo"] - entry_price) * side_sign / risk_distance, 4
        ) if risk_distance > 0 else 0.0

        # Délais (minutes) live vs théorie
        entry_ts_theo_dt = pd.Timestamp(sim["entry_ts_theo"])
        if entry_ts_theo_dt.tzinfo is None:
            entry_ts_theo_dt = entry_ts_theo_dt.tz_localize("UTC")
        delay_entry_min = round((entry_ts - entry_ts_theo_dt).total_seconds() / 60.0, 1)

        exit_ts_live_dt = pd.Timestamp(ext["ts"])
        if exit_ts_live_dt.tzinfo is None:
            exit_ts_live_dt = exit_ts_live_dt.tz_localize("UTC")
        exit_ts_theo_dt = pd.Timestamp(sim["exit_ts_theo"])
        if exit_ts_theo_dt.tzinfo is None:
            exit_ts_theo_dt = exit_ts_theo_dt.tz_localize("UTC")
        delay_exit_min = round((exit_ts_live_dt - exit_ts_theo_dt).total_seconds() / 60.0, 1)

        row = {
            "trade_id": t["trade_id"],
            "strategy": strat,
            "instrument": inst,
            "side": side,
            # Clé d'unicité (cf. load_trades) — un trade_id peut apparaître
            # 2× quand FTMO et GFT prennent le même signal.
            "broker_id": t.get("broker_id") or ent.get("broker_id"),
            "broker": ent.get("broker_id"),
            "tf": tf,
            # --- Live ---
            "entry_ts_live": ent["ts"],
            "entry_price_live": entry_price,
            "sl": sl,
            "exit_ts_live": ext["ts"],
            "exit_price_live": float(ext.get("exit_price", 0)) or None,
            "r_live": r_live,
            "mfe_live": mfe_live,
            "be_set_live": bool(ext.get("be_set", False)),
            "exit_reason_live": ext.get("exit_reason"),
            # --- Théorie ---
            "entry_ts_theo": sim["entry_ts_theo"],
            "entry_price_theo": sim["entry_price_theo"],
            "be_armed_ts_theo": sim["be_armed_ts_theo"],
            "be_armed_price_theo": sim["be_armed_price_theo"],
            "exit_ts_theo": sim["exit_ts_theo"],
            "exit_price_theo": sim["exit_price_theo"],
            "r_theo": sim["r_theo"],
            "mfe_theo": sim["mfe_theo"],
            "be_armed_theo": sim["be_armed_theo"],
            "exit_reason_theo": sim["exit_reason_theo"],
            "ambiguous_bar_theo": sim.get("ambiguous_bar", False),
            # --- Comparaison ---
            "delta_r": delta_r,
            "slip_entry_R": slip_entry_R,
            "delay_entry_min": delay_entry_min,
            "delay_exit_min": delay_exit_min,
            # --- Spread/quote broker (depuis trade_journal, 0 si non instrumenté) ---
            "spread_at_entry": ent.get("spread_at_entry"),
            "spread_at_exit": ext.get("spread_at_exit"),
            "broker_bid_at_entry": ent.get("broker_bid_at_entry"),
            "broker_ask_at_entry": ent.get("broker_ask_at_entry"),
            "broker_bid_at_exit": ext.get("broker_bid_at_exit"),
            "broker_ask_at_exit": ext.get("broker_ask_at_exit"),
            "exit_price_source": ext.get("exit_price_source"),
            # alias rétro-compat (anciens consumers du JSONL)
            "entry_ts": ent["ts"],
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
            print("  Aperçu des 5 plus gros écarts (lecture : modèle naïf vs stratégie complète) :")
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

    if _SKIPPED_NO_PARQUET:
        print()
        print(f"⚠️  Strict-data : {len(_SKIPPED_NO_PARQUET)} (instrument, tf) skippé(s) — pas de parquet local")
        for instr, tf in sorted(_SKIPPED_NO_PARQUET)[:15]:
            print(f"    {tf:3s}  {instr}")
        if len(_SKIPPED_NO_PARQUET) > 15:
            print(f"    ... et {len(_SKIPPED_NO_PARQUET) - 15} autres")
        print("    → rejouer avec --allow-yahoo, ou ingérer le parquet manquant.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
