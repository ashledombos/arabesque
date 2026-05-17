"""Distribution rolling-30j de l'Exp sur baseline 20 mois.

Pour chaque stratégie active, rejoue le backtest sur la baseline complète
(Jul 2024 → maintenant), concatène tous les trades fermés, puis calcule
l'Exp sur fenêtre glissante de 30 jours en pas hebdomadaire.

Sort une distribution (mean, std, p10/p25/p50/p75/p90) pour chaque stratégie
et place le creux actuel (BT pleine fenêtre J-30) dans cette distribution.

Réponse à la question : « ce creux est-il attendu dans la baseline ? »
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arabesque.execution.backtest import BacktestRunner, BacktestConfig  # noqa: E402
from arabesque.data.store import load_ohlc  # noqa: E402
from scripts.compare_live_vs_backtest import _resolve_strategy, manager_config_for  # noqa: E402

BASELINE_START = "2024-07-01"
OUT_JSONL = ROOT / "logs" / "rolling_baseline_distribution.jsonl"


def _bt_trades_for_instrument(instrument: str, strategy: str,
                              start: str, end: str) -> list[tuple[dt.datetime, float]]:
    """Renvoie [(ts_exit, result_r), ...] pour un (strat, instrument) sur la fenêtre."""
    try:
        sig_gen, forced_tf, _exec_cfg = _resolve_strategy(strategy)
        if forced_tf:
            interval = forced_tf
        else:
            interval = "1h"
        start_dt = dt.datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        warmup_start = (start_dt - dt.timedelta(days=90)).strftime("%Y-%m-%d")
        df = load_ohlc(instrument, interval=interval, start=warmup_start, end=end)
        if df is None or len(df) < 50:
            return []
        sub_bar_df = None
        if interval not in ("min1", "1m", "M1"):
            try:
                df_m1 = load_ohlc(instrument, interval="min1", start=warmup_start, end=end)
                if df_m1 is not None and len(df_m1) > 0:
                    if "close" in df_m1.columns and "Close" not in df_m1.columns:
                        df_m1.columns = [c.capitalize() for c in df_m1.columns]
                    sub_bar_df = df_m1
            except Exception:
                pass
        df_prepared = sig_gen.prepare(df)
        mgr_cfg = manager_config_for(instrument, interval)
        runner = BacktestRunner(
            bt_config=BacktestConfig(risk_per_trade_pct=0.45, start_balance=100_000),
            manager_config=mgr_cfg,
            signal_generator=sig_gen,
        )
        result = runner.run(df_prepared, instrument=instrument,
                            sample_type="rolling_baseline", sub_bar_df=sub_bar_df)
        out = []
        for t in (result.closed_positions or []):
            ts = getattr(t, "ts_exit", None)
            r = getattr(t, "result_r", None)
            if ts is None or r is None:
                continue
            if not isinstance(ts, dt.datetime):
                continue
            if ts < start_dt:
                continue
            out.append((ts, float(r)))
        return out
    except Exception as e:
        print(f"  ! {strategy} {instrument}: {e}", file=sys.stderr)
        return []


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def _rank_below(sorted_vals: list[float], target: float) -> float:
    """Renvoie le pourcentage de fenêtres dont l'Exp est ≤ target."""
    if not sorted_vals:
        return float("nan")
    below = sum(1 for v in sorted_vals if v <= target)
    return below / len(sorted_vals) * 100


def _instruments_for(strategy: str, settings: dict, instruments_cfg: dict) -> list[str]:
    sa = settings.get("strategy_assignments", {}) or {}
    if strategy in sa:
        return list((sa[strategy] or {}).get("instruments") or [])
    if strategy == "extension":
        return [k for k, v in (instruments_cfg or {}).items()
                if isinstance(v, dict) and v.get("follow") is True]
    return []


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", required=True,
                   choices=["extension", "cabriole", "glissade", "fouette"])
    p.add_argument("--baseline-start", default=BASELINE_START)
    p.add_argument("--end", default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"))
    p.add_argument("--window-days", type=int, default=30,
                   help="Taille de la fenêtre rolling (défaut 30j)")
    p.add_argument("--step-days", type=int, default=7,
                   help="Pas entre deux fenêtres (défaut 7j hebdomadaire)")
    p.add_argument("--target-exp", type=float, default=None,
                   help="Exp à comparer à la distribution (ex: -0.093 pour extension actuel)")
    p.add_argument("--min-trades", type=int, default=5,
                   help="Fenêtres avec moins de N trades ignorées (défaut 5)")
    args = p.parse_args()

    settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())
    instruments_cfg = yaml.safe_load((ROOT / "config" / "instruments.yaml").read_text()) or {}
    instruments = _instruments_for(args.strategy, settings, instruments_cfg)
    if not instruments:
        print(f"❌ Pas d'instruments pour {args.strategy}", file=sys.stderr)
        return 1

    print(f"Rolling baseline — {args.strategy}  ({args.baseline_start} → {args.end})")
    print(f"  Fenêtre={args.window_days}j  pas={args.step_days}j  "
          f"min_trades={args.min_trades}")
    print(f"  Instruments: {len(instruments)}")

    all_trades: list[tuple[dt.datetime, float]] = []
    for i, inst in enumerate(instruments, 1):
        print(f"  [{i:2d}/{len(instruments)}] BT {inst}... ", end="", flush=True)
        trades = _bt_trades_for_instrument(inst, args.strategy,
                                            args.baseline_start, args.end)
        print(f"{len(trades)} trades")
        all_trades.extend(trades)
    all_trades.sort(key=lambda x: x[0])
    print(f"  Total {len(all_trades)} trades baseline")

    # Fenêtres rolling : on commence à baseline_start + window_days
    bs = dt.datetime.strptime(args.baseline_start, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    en = dt.datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    win = dt.timedelta(days=args.window_days)
    step = dt.timedelta(days=args.step_days)
    obs = bs + win
    rolling: list[dict] = []
    while obs <= en:
        wstart = obs - win
        trades_in = [r for (ts, r) in all_trades if wstart <= ts <= obs]
        if len(trades_in) >= args.min_trades:
            exp = sum(trades_in) / len(trades_in)
            rolling.append({
                "obs_ts": obs.strftime("%Y-%m-%d"),
                "n_trades": len(trades_in),
                "exp": exp,
            })
        obs += step

    if not rolling:
        print("❌ Aucune fenêtre avec assez de trades")
        return 1

    exps = sorted(w["exp"] for w in rolling)
    mean = statistics.fmean(exps)
    stdev = statistics.stdev(exps) if len(exps) > 1 else 0.0
    pcts = {p: _percentile(exps, p) for p in (0.1, 0.25, 0.5, 0.75, 0.9)}
    print()
    print(f"  Distribution Exp rolling-{args.window_days}j (pas {args.step_days}j) — {len(exps)} fenêtres")
    print(f"    mean   {mean:+.3f}R")
    print(f"    stdev   {stdev:.3f}R")
    print(f"    p10    {pcts[0.1]:+.3f}R  ← creux historiques")
    print(f"    p25    {pcts[0.25]:+.3f}R")
    print(f"    p50    {pcts[0.5]:+.3f}R  (médiane)")
    print(f"    p75    {pcts[0.75]:+.3f}R")
    print(f"    p90    {pcts[0.9]:+.3f}R  ← bons régimes")

    if args.target_exp is not None:
        rk = _rank_below(exps, args.target_exp)
        verdict = (
            "✅ creux normal (dans la fourchette historique p10-p25)"
            if rk >= 10
            else "🟡 creux profond (≤ p10 historique)"
            if rk >= 5
            else "🔶 creux extrême (< p5 historique)"
        )
        print()
        print(f"  Cible Exp = {args.target_exp:+.3f}R")
        print(f"    {rk:.0f}% des fenêtres historiques sont ≤ à cette valeur")
        print(f"    → {verdict}")

    # Persiste pour traçabilité
    entry = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "strategy": args.strategy,
        "baseline_start": args.baseline_start,
        "end": args.end,
        "window_days": args.window_days,
        "step_days": args.step_days,
        "n_windows": len(exps),
        "n_trades_total": len(all_trades),
        "mean_exp": round(mean, 4),
        "stdev_exp": round(stdev, 4),
        "p10": round(pcts[0.1], 4),
        "p25": round(pcts[0.25], 4),
        "p50": round(pcts[0.5], 4),
        "p75": round(pcts[0.75], 4),
        "p90": round(pcts[0.9], 4),
        "target_exp": args.target_exp,
        "target_rank_pct": round(_rank_below(exps, args.target_exp), 2)
                            if args.target_exp is not None else None,
    }
    with open(OUT_JSONL, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"\n  Persisté dans {OUT_JSONL.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
