"""Sensibilité de la baseline Extension au spread et au slippage entry.

Roule la baseline 20 mois sur quelques instruments représentatifs avec
différents niveaux de spread/slippage pour mesurer combien l'Exp tombe
quand on passe d'un BT optimiste à un BT plus pessimiste.

NOTE: ne couvre PAS le slippage SL/TP (trou non encore comblé dans le BT).
Couverture intentionnellement partielle pour cette première mesure — on
quantifie d'abord l'impact de spread+slip_entry, slippage SL viendra après.

Usage:
    python scripts/recalibrate_bt_sensitivity.py
    python scripts/recalibrate_bt_sensitivity.py --instruments XAUUSD GBPJPY
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from arabesque.execution.backtest import (
    BacktestConfig,
    run_backtest,
)


@dataclass
class SensCase:
    label: str
    spread_pct: float
    slippage_r: float


CASES = [
    SensCase("baseline", spread_pct=0.00015, slippage_r=0.03),  # config historique
    SensCase("spread_x2", spread_pct=0.00030, slippage_r=0.03),
    SensCase("spread_x4", spread_pct=0.00060, slippage_r=0.03),
    SensCase("slip_x2", spread_pct=0.00015, slippage_r=0.06),
    SensCase("slip_x4", spread_pct=0.00015, slippage_r=0.12),
    SensCase("realiste", spread_pct=0.00040, slippage_r=0.08),  # spread moyen FTMO/GFT + slip 2.7×
    SensCase("pessimiste", spread_pct=0.00060, slippage_r=0.12),
]


def run_one(instrument: str, case: SensCase, period: str, interval: str = "1h") -> dict:
    cfg = BacktestConfig(
        spread_pct=case.spread_pct,
        slippage_r=case.slippage_r,
        verbose=False,
    )
    try:
        result_in, result_out = run_backtest(
            instrument=instrument,
            period=period,
            bt_config=cfg,
            split_pct=0.70,
            verbose=False,
            strategy="extension",
        )
    except Exception as e:
        return {"error": str(e)}
    # On agrège IS+OOS pour avoir une vue "20 mois complets"
    n_in = result_in.metrics.n_trades
    n_out = result_out.metrics.n_trades
    if n_in + n_out == 0:
        return {"n": 0}
    wr_w = (result_in.metrics.win_rate * n_in + result_out.metrics.win_rate * n_out) / (n_in + n_out)
    exp_w = (result_in.metrics.expectancy_r * n_in + result_out.metrics.expectancy_r * n_out) / (n_in + n_out)
    total_r = result_in.metrics.expectancy_r * n_in + result_out.metrics.expectancy_r * n_out
    return {
        "n": n_in + n_out,
        "wr": wr_w,
        "exp": exp_w,
        "total_r": total_r,
        "max_dd_in": result_in.metrics.max_dd_pct,
        "max_dd_out": result_out.metrics.max_dd_pct,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--instruments", nargs="+", default=["XAUUSD", "GBPJPY"])
    p.add_argument("--period", default="600d")
    p.add_argument("--interval", default="1h")
    args = p.parse_args()

    print(f"\n{'='*88}")
    print(f"  SENSIBILITÉ BT — spread × slippage_entry — Extension {args.interval}")
    print(f"  Instruments: {' '.join(args.instruments)}    Période: {args.period}")
    print(f"{'='*88}\n")

    print(f"{'Inst':<8s} {'Case':<14s} {'spread%':<9s} {'slip_r':<7s} {'n':<5s} {'WR%':<7s} {'Exp(R)':<10s} {'ΣR':<8s}")
    print("-" * 88)

    by_inst: dict[str, dict[str, dict]] = {}
    for inst in args.instruments:
        by_inst[inst] = {}
        for case in CASES:
            res = run_one(inst, case, args.period, args.interval)
            by_inst[inst][case.label] = res
            if res.get("error"):
                print(f"{inst:<8s} {case.label:<14s} ERR: {res['error']}")
                continue
            n = res.get("n", 0)
            wr = res.get("wr", 0) * 100
            exp = res.get("exp", 0)
            total_r = res.get("total_r", 0)
            print(f"{inst:<8s} {case.label:<14s} {case.spread_pct*100:<9.4f} {case.slippage_r:<7.2f} "
                  f"{n:<5d} {wr:<7.1f} {exp:<+10.4f} {total_r:<+8.1f}")
        print()

    # Δ vs baseline
    print(f"{'='*88}")
    print("  Δ vs baseline (Exp recalibré − Exp baseline)")
    print(f"{'='*88}")
    print(f"{'Inst':<8s} " + " ".join(f"{c.label:<13s}" for c in CASES if c.label != "baseline"))
    for inst in args.instruments:
        baseline_exp = by_inst[inst].get("baseline", {}).get("exp", 0)
        line = f"{inst:<8s} "
        for c in CASES:
            if c.label == "baseline":
                continue
            res = by_inst[inst].get(c.label, {})
            if "error" in res or res.get("n", 0) == 0:
                line += f"{'—':<13s} "
                continue
            d = res["exp"] - baseline_exp
            line += f"{d:<+13.4f} "
        print(line)

    return 0


if __name__ == "__main__":
    sys.exit(main())
