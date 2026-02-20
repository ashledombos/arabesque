#!/usr/bin/env python3
"""
Arabesque — update_and_compare.py

Workflow automatisé :
  1. Relit le dernier run backtest depuis logs/backtest_runs.jsonl
  2. Relance le backtest sur les mêmes instruments / période
  3. Compare les métriques run N vs run N-1
  4. Exporte un rapport delta dans logs/compare_<date>.txt

Usage :
    python scripts/update_and_compare.py
    python scripts/update_and_compare.py --instruments XRPUSD SOLUSD BTCUSD
    python scripts/update_and_compare.py --strategy combined --start 2025-01-01
    python scripts/update_and_compare.py --export-trades --out logs/trades_latest.jsonl

Les fichiers Parquet doivent avoir été mis à jour via barres_au_sol avant de lancer ce script.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Ajouter la racine du projet au PYTHONPATH si nécessaire
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arabesque.backtest.runner import BacktestConfig, run_backtest, run_multi_instrument

BACKTEST_RUNS_LOG = Path("logs/backtest_runs.jsonl")
COMPARE_DIR = Path("logs/comparisons")
TRADES_DIR = Path("logs/trades")

# ── Métriques à comparer ───────────────────────────────────────────
METRICS_TO_COMPARE = [
    ("n_trades",        "Trades",         "",    False),
    ("win_rate",        "Win Rate",        "%",   False),
    ("expectancy_r",    "Expectancy (R)",  "R",   True),   # True = highlight si régression
    ("profit_factor",  "Profit Factor",   "",    True),
    ("max_dd_pct",      "Max DD %",        "%",   True),
    ("n_disq_days",     "Disqual Days",    "",    False),
    ("n_signals",       "Signals",         "",    False),
    ("n_rejected",      "Rejected",        "",    False),
]


# ── Lecture du JSONL ───────────────────────────────────────────────

def load_runs(path: Path = BACKTEST_RUNS_LOG) -> list[dict]:
    """Charge tous les runs depuis le JSONL."""
    if not path.exists():
        return []
    runs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return runs


def get_last_runs_by_instrument(
    runs: list[dict],
    instruments: list[str] | None = None,
    sample: str = "out_of_sample",
) -> dict[str, dict]:
    """
    Pour chaque instrument, retourne le dernier run OOS enregistré.
    Si instruments=None, prend tous les instruments connus.
    """
    filtered = [r for r in runs if r.get("sample") == sample]
    if instruments:
        filtered = [r for r in filtered if r.get("instrument") in instruments]

    latest: dict[str, dict] = {}
    for run in filtered:
        inst = run["instrument"]
        if inst not in latest or run["ts"] > latest[inst]["ts"]:
            latest[inst] = run
    return latest


# ── Comparaison ────────────────────────────────────────────────────

def compare_runs(prev: dict, curr: dict) -> list[tuple[str, str, str, str, str]]:
    """
    Compare deux runs et retourne une liste de lignes (nom, prev, curr, delta, flag).
    flag = '⚠️' si régression sur une métrique critique, '✅' si amélioration, '' sinon.
    """
    rows = []
    for key, label, unit, critical in METRICS_TO_COMPARE:
        v_prev = prev.get(key)
        v_curr = curr.get(key)
        if v_prev is None or v_curr is None:
            rows.append((label, str(v_prev), str(v_curr), "", ""))
            continue

        if isinstance(v_prev, float) or isinstance(v_curr, float):
            delta = float(v_curr) - float(v_prev)
            s_prev = f"{v_prev:.3f}{unit}"
            s_curr = f"{v_curr:.3f}{unit}"
            s_delta = f"{delta:+.3f}{unit}"
        else:
            delta = int(v_curr) - int(v_prev)
            s_prev = f"{v_prev}{unit}"
            s_curr = f"{v_curr}{unit}"
            s_delta = f"{delta:+d}{unit}"

        flag = ""
        if critical:
            # Pour max_dd_pct, une hausse est une régression
            if key == "max_dd_pct":
                flag = "⚠️" if delta > 0.5 else ("✅" if delta < -0.5 else "")
            else:
                flag = "⚠️" if delta < -0.05 else ("✅" if delta > 0.05 else "")

        rows.append((label, s_prev, s_curr, s_delta, flag))
    return rows


def format_comparison_report(
    prev_runs: dict[str, dict],
    curr_results: dict[str, tuple],
    ts: str,
) -> str:
    """
    Formate le rapport de comparaison N-1 → N.
    curr_results : dict instrument → (BacktestResult_in, BacktestResult_out)
    """
    lines = []
    lines.append("=" * 70)
    lines.append(f"  ARABESQUE — COMPARAISON RUN N-1 → N")
    lines.append(f"  Généré le : {ts}")
    lines.append("=" * 70)

    regressions = []
    improvements = []

    for inst, (res_in, res_out) in curr_results.items():
        curr = {
            "n_trades":       res_out.metrics.n_trades,
            "win_rate":       res_out.metrics.win_rate,
            "expectancy_r":   res_out.metrics.expectancy_r,
            "profit_factor":  res_out.metrics.profit_factor,
            "max_dd_pct":     res_out.metrics.max_dd_pct,
            "n_disq_days":    res_out.metrics.n_disqualifying_days,
            "n_signals":      res_out.metrics.n_signals_generated,
            "n_rejected":     res_out.metrics.n_signals_rejected,
        }
        prev = prev_runs.get(inst)

        lines.append(f"\n{'─'*70}")
        lines.append(f"  {inst}")
        lines.append(f"{'─'*70}")

        if prev is None:
            lines.append("  (Pas de run précédent — premier run pour cet instrument)")
            for key, label, unit, _ in METRICS_TO_COMPARE:
                v = curr.get(key, "N/A")
                if isinstance(v, float):
                    lines.append(f"    {label:<22s}: {v:.3f}{unit}")
                else:
                    lines.append(f"    {label:<22s}: {v}{unit}")
            continue

        rows = compare_runs(prev, curr)
        header = f"  {'Métrique':<22s} {'Précédent':>12s} {'Actuel':>12s} {'Delta':>12s}  "
        lines.append(header)
        lines.append(f"  {'─'*58}")
        for label, s_prev, s_curr, s_delta, flag in rows:
            lines.append(f"  {label:<22s} {s_prev:>12s} {s_curr:>12s} {s_delta:>12s}  {flag}")

        # Collecte pour le résumé
        for label, s_prev, s_curr, s_delta, flag in rows:
            if flag == "⚠️":
                regressions.append(f"{inst} — {label}: {s_prev} → {s_curr} ({s_delta})")
            elif flag == "✅":
                improvements.append(f"{inst} — {label}: {s_prev} → {s_curr} ({s_delta})")

    # Résumé final
    lines.append(f"\n{'='*70}")
    lines.append("  RÉSUMÉ")
    lines.append(f"{'='*70}")
    if improvements:
        lines.append(f"  ✅ AMÉLIORATIONS ({len(improvements)}):")
        for item in improvements:
            lines.append(f"     • {item}")
    if regressions:
        lines.append(f"  ⚠️  RÉGRESSIONS ({len(regressions)}):")
        for item in regressions:
            lines.append(f"     • {item}")
    if not improvements and not regressions:
        lines.append("  Aucun changement significatif détecté.")
    lines.append("=" * 70)

    return "\n".join(lines)


# ── Export trades enrichis ─────────────────────────────────────────

def export_trades(
    curr_results: dict[str, tuple],
    out_path: Path,
    strategy: str,
    start: str | None,
    end: str | None,
) -> None:
    """
    Exporte toutes les positions fermées (in + OOS) dans un fichier JSONL enrichi.
    Format : une ligne JSON par trade, avec instrument + sample_type + run_ts.
    Ce fichier permet de comparer les trades backtest avec les trades paper/live
    une fois que de nouvelles barres Parquet seront disponibles.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ts_run = datetime.now(timezone.utc).isoformat()
    n_total = 0
    with open(out_path, "w") as f:
        for inst, (res_in, res_out) in curr_results.items():
            for sample_type, result in [("in_sample", res_in), ("out_of_sample", res_out)]:
                for pos in result.closed_positions:
                    trade = {
                        "run_ts":       ts_run,
                        "strategy":     strategy,
                        "period_start": start,
                        "period_end":   end,
                        "instrument":   inst,
                        "sample_type":  sample_type,
                        "side":         pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                        "entry":        pos.entry_price,
                        "sl":           pos.sl,
                        "result_r":     round(pos.result_r, 4) if pos.result_r is not None else None,
                        "risk_cash":    pos.risk_cash,
                        "exit_reason":  pos.exit_reason.value if pos.exit_reason and hasattr(pos.exit_reason, "value") else str(pos.exit_reason),
                        "bars_open":    pos.bars_open,
                        "mfe_r":        round(pos.mfe_r, 4) if pos.mfe_r is not None else None,
                        "ts_entry":     pos.ts_entry.isoformat() if pos.ts_entry else None,
                        "ts_exit":      pos.ts_exit.isoformat() if pos.ts_exit else None,
                    }
                    f.write(json.dumps(trade, default=str) + "\n")
                    n_total += 1
    print(f"  📄 {n_total} trades exportés → {out_path}")


# ── CLI ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Arabesque — Compare le run backtest actuel avec le précédent."
    )
    parser.add_argument(
        "--instruments", nargs="+",
        help="Instruments à backtester (défaut : tous les instruments du dernier run)",
    )
    parser.add_argument("--strategy", default="combined",
                        choices=["mean_reversion", "trend", "combined"])
    parser.add_argument("--start", default=None, help="Date début YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="Date fin YYYY-MM-DD")
    parser.add_argument("--period", default="730d", help="Période Yahoo (ignorée si --start/--end)")
    parser.add_argument("--split", type=float, default=0.70)
    parser.add_argument("--balance", type=float, default=100_000)
    parser.add_argument("--risk", type=float, default=0.5)
    parser.add_argument("--no-filter", action="store_true", help="Désactiver le SignalFilter")
    parser.add_argument("--export-trades", action="store_true",
                        help="Exporter les trades dans logs/trades/trades_<date>.jsonl")
    parser.add_argument("--out", default=None,
                        help="Chemin du fichier d'export trades (écrase --export-trades path)")
    parser.add_argument("--no-compare", action="store_true",
                        help="Ne pas comparer avec le run précédent (premier run)")
    args = parser.parse_args()

    # 1. Charger les runs précédents
    all_runs = load_runs()
    prev_instruments = list({r["instrument"] for r in all_runs if r.get("sample") == "out_of_sample"})

    # Déterminer les instruments à traiter
    instruments = args.instruments or prev_instruments
    if not instruments:
        print("  ⚠️  Aucun instrument trouvé. Précisez --instruments XRPUSD SOLUSD ...")
        sys.exit(1)

    print(f"\n  Instruments : {', '.join(instruments)}")
    print(f"  Stratégie   : {args.strategy}")
    print(f"  Période     : {args.start or args.period} → {args.end or 'today'}")

    # 2. Charger les runs précédents pour comparaison
    prev_runs: dict[str, dict] = {}
    if not args.no_compare and all_runs:
        prev_runs = get_last_runs_by_instrument(all_runs, instruments)
        print(f"  Runs précédents trouvés : {len(prev_runs)}/{len(instruments)} instruments")
    else:
        print("  Mode premier run — pas de comparaison.")

    # 3. Lancer les nouveaux backtests
    cfg = BacktestConfig(
        start_balance=args.balance,
        risk_per_trade_pct=args.risk,
        signal_filter_path=None if args.no_filter else "config/signal_filters.yaml",
        verbose=False,
    )

    curr_results: dict[str, tuple] = {}
    for inst in instruments:
        try:
            res_in, res_out = run_backtest(
                inst,
                period=args.period,
                start=args.start,
                end=args.end,
                bt_config=cfg,
                split_pct=args.split,
                verbose=False,
                strategy=args.strategy,
            )
            curr_results[inst] = (res_in, res_out)
        except Exception as e:
            print(f"  ❌ Erreur sur {inst}: {e}")

    if not curr_results:
        print("  ❌ Aucun résultat. Vérifiez les données Parquet.")
        sys.exit(1)

    # 4. Générer le rapport de comparaison
    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
    report = format_comparison_report(prev_runs, curr_results, ts_now)
    print(report)

    # 5. Sauvegarder le rapport
    COMPARE_DIR.mkdir(parents=True, exist_ok=True)
    report_path = COMPARE_DIR / f"compare_{ts_now}.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n  💾 Rapport sauvegardé → {report_path}")

    # 6. Export trades enrichis (optionnel)
    if args.export_trades or args.out:
        out_path = Path(args.out) if args.out else TRADES_DIR / f"trades_{ts_now}.jsonl"
        export_trades(curr_results, out_path, args.strategy, args.start, args.end)


if __name__ == "__main__":
    main()
