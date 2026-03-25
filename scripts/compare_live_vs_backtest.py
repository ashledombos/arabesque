#!/usr/bin/env python3
"""
Arabesque — Compare live trades vs backtest sur la même période.

Usage:
    python scripts/compare_live_vs_backtest.py
    python scripts/compare_live_vs_backtest.py --start 2026-03-18 --end 2026-03-22
    python scripts/compare_live_vs_backtest.py --last 7       # derniers 7 jours

Presets (équivalents filtres cTrader) :
    python scripts/compare_live_vs_backtest.py --period today
    python scripts/compare_live_vs_backtest.py --period yesterday
    python scripts/compare_live_vs_backtest.py --period this_week
    python scripts/compare_live_vs_backtest.py --period this_month
    python scripts/compare_live_vs_backtest.py --period prev_month
    python scripts/compare_live_vs_backtest.py --period 3m
    python scripts/compare_live_vs_backtest.py --period 12m

Lit le trade_journal.jsonl, relance un backtest sur la même période
et les mêmes instruments, puis affiche un tableau comparatif.

Le backtest utilise les données M1 comme sub-bar replay (même résolution
intra-barre que le moteur live : BE trigger, trailing, ordre SL/TP).
Si les M1 ne sont pas disponibles, repli silencieux sur H/L agrégé.

Sans risque : lecture seule du journal + backtest offline.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

import pandas as pd

# Ajouter le repo au path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arabesque.data.store import load_ohlc
from arabesque.execution.backtest import BacktestRunner, BacktestConfig, manager_config_for
from arabesque.strategies.extension.signal import ExtensionSignalGenerator, ExtensionConfig


FOREX_METALS = {
    "XAUUSD", "XAGUSD", "GBPJPY", "AUDJPY", "CHFJPY",
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD",
    "USDCAD", "USDCHF",
}


def resolve_period(period: str) -> tuple[datetime, datetime]:
    """Traduit un preset en (start, end) UTC."""
    today = date.today()
    if period == "today":
        start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
        end = datetime.now(timezone.utc)
    elif period == "yesterday":
        yesterday = today - timedelta(days=1)
        start = datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=timezone.utc)
        end = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    elif period == "this_week":
        monday = today - timedelta(days=today.weekday())
        start = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
        end = datetime.now(timezone.utc)
    elif period == "this_month":
        start = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
        end = datetime.now(timezone.utc)
    elif period == "prev_month":
        first_this = date(today.year, today.month, 1)
        last_prev = first_this - timedelta(days=1)
        start = datetime(last_prev.year, last_prev.month, 1, tzinfo=timezone.utc)
        end = datetime(first_this.year, first_this.month, first_this.day, tzinfo=timezone.utc)
    elif period == "3m":
        start = datetime.now(timezone.utc) - timedelta(days=91)
        end = datetime.now(timezone.utc)
    elif period == "12m":
        start = datetime.now(timezone.utc) - timedelta(days=365)
        end = datetime.now(timezone.utc)
    else:
        raise ValueError(f"Période inconnue: {period}. Valeurs: today, yesterday, this_week, this_month, prev_month, 3m, 12m")
    return start, end


def load_journal(path: str = "logs/trade_journal.jsonl") -> pd.DataFrame:
    """Charge le journal des trades live."""
    entries = []
    with open(path) as f:
        for line in f:
            entry = json.loads(line.strip())
            if entry.get("event") == "exit":
                entries.append(entry)
    if not entries:
        print("❌ Aucun trade 'exit' dans le journal.")
        sys.exit(1)
    df = pd.DataFrame(entries)
    df["ts_dt"] = pd.to_datetime(df["ts"], utc=True)
    return df


def run_backtest_for_instrument(instrument: str, start: str, end: str) -> dict:
    """Lance un backtest Extension pour un instrument sur la période donnée.

    Charge 90 jours de contexte avant start pour le warmup des indicateurs,
    puis filtre les trades pour ne garder que ceux dans [start, end].
    """
    try:
        interval = "1h" if instrument in FOREX_METALS else "4h"

        # Warmup : 90 jours avant start pour les indicateurs (BB, etc.)
        start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        warmup_start = (start_dt - timedelta(days=90)).strftime("%Y-%m-%d")

        df = load_ohlc(instrument, interval=interval, start=warmup_start, end=end)
        if df is None or len(df) < 50:
            return {"instrument": instrument, "error": f"Pas assez de données ({interval})"}

        # Sub-bar M1 pour résolution intra-barre (BE trigger, trailing, ordre SL/TP)
        # Même comportement que le moteur live — repli silencieux sur H/L si indispo.
        sub_bar_df = None
        try:
            df_m1 = load_ohlc(instrument, interval="min1", start=warmup_start, end=end)
            if df_m1 is not None and len(df_m1) > 0:
                if "close" in df_m1.columns and "Close" not in df_m1.columns:
                    df_m1.columns = [c.capitalize() for c in df_m1.columns]
                sub_bar_df = df_m1
        except Exception:
            pass

        sig_gen = ExtensionSignalGenerator(ExtensionConfig())
        df_prepared = sig_gen.prepare(df)

        mgr_cfg = manager_config_for(instrument, interval)
        runner = BacktestRunner(
            bt_config=BacktestConfig(risk_per_trade_pct=0.45, start_balance=100_000),
            manager_config=mgr_cfg,
            signal_generator=sig_gen,
        )
        result = runner.run(df_prepared, instrument=instrument, sample_type="comparison",
                            sub_bar_df=sub_bar_df)

        # Filtrer les trades dans la fenêtre demandée seulement
        all_trades = result.closed_positions or []
        trades_in_window = [
            t for t in all_trades
            if hasattr(t, "ts_exit") and t.ts_exit and t.ts_exit >= start_dt
        ]

        # Si pas de trades filtrables, utiliser les métriques globales (approx)
        if not trades_in_window:
            return {
                "instrument": instrument,
                "timeframe": interval,
                "sub_bar": sub_bar_df is not None,
                "bt_trades": 0,
                "bt_wr": float("nan"),
                "bt_exp": float("nan"),
                "bt_total_r": 0.0,
            }

        n = len(trades_in_window)
        wins = sum(1 for t in trades_in_window if hasattr(t, "result_r") and t.result_r > 0)
        wr = wins / n * 100 if n > 0 else float("nan")
        results_r = [t.result_r for t in trades_in_window if hasattr(t, "result_r")]
        exp = sum(results_r) / len(results_r) if results_r else float("nan")
        total_r = sum(results_r)

        return {
            "instrument": instrument,
            "timeframe": interval,
            "sub_bar": sub_bar_df is not None,
            "bt_trades": n,
            "bt_wr": wr,
            "bt_exp": exp,
            "bt_total_r": total_r,
        }
    except Exception as e:
        return {"instrument": instrument, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Compare live vs backtest")
    parser.add_argument("--start", type=str, help="Date début (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="Date fin (YYYY-MM-DD)")
    parser.add_argument("--last", type=int, help="Derniers N jours", default=None)
    parser.add_argument("--period", type=str,
                        choices=["today", "yesterday", "this_week", "this_month",
                                 "prev_month", "3m", "12m"],
                        help="Preset temporel (équivalent filtres cTrader)")
    parser.add_argument("--journal", type=str, default="logs/trade_journal.jsonl")
    args = parser.parse_args()

    # Résoudre la période
    if args.period:
        start_dt, end_dt = resolve_period(args.period)
    elif args.last:
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=args.last)
    elif args.start:
        start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = (datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                  if args.end else datetime.now(timezone.utc))
    else:
        # Période couverte par le journal
        journal_all = load_journal(args.journal)
        start_dt = journal_all["ts_dt"].min() - timedelta(days=1)
        end_dt = journal_all["ts_dt"].max() + timedelta(days=1)

    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    # Charger et filtrer le journal sur la période
    journal_all = load_journal(args.journal)
    journal = journal_all[
        (journal_all["ts_dt"] >= start_dt) & (journal_all["ts_dt"] <= end_dt)
    ]

    period_label = args.period or (f"--last {args.last}" if args.last else f"{start_str} → {end_str}")
    print(f"\n{'='*70}")
    print(f"  COMPARAISON LIVE vs BACKTEST  [{period_label}]")
    print(f"  Période : {start_str} → {end_str}")
    print(f"  Trades live dans la fenêtre : {len(journal)}")
    print(f"{'='*70}\n")

    if len(journal) == 0:
        print("Aucun trade live sur cette période.")
        print("Le backtest est lancé sur les instruments du journal global.\n")
        instruments = journal_all["instrument"].unique()
    else:
        instruments = journal["instrument"].unique()

    # Stats live par instrument (sur la période filtrée)
    live_stats = {}
    for inst in instruments:
        trades = journal[journal["instrument"] == inst] if len(journal) > 0 else pd.DataFrame()
        n = len(trades)
        wr = (trades["result_r"] > 0).mean() * 100 if n > 0 else float("nan")
        exp = trades["result_r"].mean() if n > 0 else float("nan")
        total_r = trades["result_r"].sum() if n > 0 else 0.0
        live_stats[inst] = {
            "live_trades": n,
            "live_wr": round(wr, 1) if n > 0 else "-",
            "live_exp": round(exp, 3) if n > 0 else "-",
            "live_total_r": round(total_r, 1),
        }

    # Backtest pour chaque instrument
    print("Backtests en cours...\n")
    rows = []
    for inst in sorted(instruments):
        bt = run_backtest_for_instrument(inst, start_str, end_str)
        live = live_stats[inst]

        if "error" in bt:
            rows.append({
                "Instrument": inst,
                "Live T": live["live_trades"],
                "Live WR": f"{live['live_wr']}%",
                "Live Exp": f"{live['live_exp']}R" if live["live_trades"] > 0 else "-",
                "Live ΣR": f"{live['live_total_r']:+.1f}R",
                "BT T": bt.get("error", "?"),
                "BT WR": "-", "BT Exp": "-", "BT ΣR": "-",
                "Δ WR": "-", "Δ Exp": "-",
            })
            continue

        delta_wr = (live["live_wr"] - bt["bt_wr"]) if (live["live_trades"] > 0 and bt["bt_trades"] > 0) else float("nan")
        delta_exp = (live["live_exp"] - bt["bt_exp"]) if (live["live_trades"] > 0 and bt["bt_trades"] > 0) else float("nan")

        rows.append({
            "Instrument": inst,
            "TF": bt["timeframe"],
            "M1": "✓" if bt.get("sub_bar") else "~",
            "Live T": live["live_trades"],
            "Live WR": f"{live['live_wr']}%" if live["live_trades"] > 0 else "-",
            "Live Exp": f"{live['live_exp']:+.3f}R" if live["live_trades"] > 0 else "-",
            "Live ΣR": f"{live['live_total_r']:+.1f}R",
            "BT T": bt["bt_trades"],
            "BT WR": f"{bt['bt_wr']:.0f}%" if not pd.isna(bt["bt_wr"]) else "-",
            "BT Exp": f"{bt['bt_exp']:+.3f}R" if not pd.isna(bt["bt_exp"]) else "-",
            "BT ΣR": f"{bt['bt_total_r']:+.1f}R",
            "Δ WR": f"{delta_wr:+.0f}pp" if not pd.isna(delta_wr) else "-",
            "Δ Exp": f"{delta_exp:+.3f}R" if not pd.isna(delta_exp) else "-",
        })

    df_result = pd.DataFrame(rows)
    print(df_result.to_string(index=False))

    # Totaux live
    if len(journal) > 0:
        total_r = journal["result_r"].sum()
        total_wr = (journal["result_r"] > 0).mean() * 100
        print(f"\n{'─'*70}")
        print(f"  LIVE total : {len(journal)} trades, WR {total_wr:.0f}%, ΣR {total_r:+.1f}R")
        print(f"{'─'*70}")

        if len(journal) < 30:
            print(f"\n  ⚠️  {len(journal)} trades — trop peu pour conclure (< 30), bruit statistique probable.")
        else:
            print("\n  ✅ Échantillon suffisant pour une première analyse.")

        for inst in instruments:
            live = live_stats[inst]
            if isinstance(live["live_exp"], float) and live["live_exp"] < -0.1 and live["live_trades"] >= 5:
                print(f"  🔴 {inst} : Exp live {live['live_exp']:+.3f}R < -0.10R sur {live['live_trades']} trades — surveiller")

    print()


if __name__ == "__main__":
    main()
