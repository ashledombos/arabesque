#!/usr/bin/env python3
"""
Arabesque — Évaluation multi-shadow-filter en une passe.

Lance un backtest complet sur un ensemble d'instruments et évalue
en post-processing l'impact de N filtres candidats sur les résultats.

Pour chaque filtre, calcule :
  - N trades bloqués
  - WR des trades bloqués (est-ce qu'ils perdaient ?)
  - Impact R total (combien de R on aurait gagné/perdu en bloquant)
  - % des trades filtrés (coût en volume de trades)

Usage :
    python scripts/analyze_shadow_filters.py BTCUSD XAUUSD EURUSD
    python scripts/analyze_shadow_filters.py --preset crypto_all
    python scripts/analyze_shadow_filters.py BTCUSD --period 730d --split 0.7
    python scripts/analyze_shadow_filters.py BTCUSD --csv results/shadow_analysis.csv

Arguments :
    instruments     Symboles à analyser (ex: BTCUSD XAUUSD)
    --preset        Preset d'instruments (fx_majors, crypto_all, metals, all...)
    --period        Période de données (défaut: 730d)
    --split         Fraction IS pour le split IS/OOS (défaut: 0.7)
    --risk          Risque par trade % (défaut: 0.40)
    --balance       Capital de départ (défaut: 100000)
    --csv           Exporter les résultats en CSV
    --verbose       Afficher le détail des trades filtrés
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

# Ajouter le repo au path si nécessaire
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arabesque.execution.backtest import BacktestRunner, BacktestConfig
from arabesque.data.store import load_ohlc, split_in_out_sample
from arabesque.core.models import Position, Side


# =============================================================================
# Définition des filtres candidats
# =============================================================================

@dataclass
class ShadowFilter:
    """Un filtre candidat à évaluer."""
    name: str
    description: str
    condition: Callable[[dict, str], bool]
    # condition(signal_data, side) -> True = signal AURAIT ÉTÉ BLOQUÉ


# Catalogue des filtres à évaluer
# Ajouter ici de nouveaux filtres sans modifier le reste du script
SHADOW_FILTERS: list[ShadowFilter] = [

    ShadowFilter(
        name="WR_momentum",
        description="Williams %R faible : WR < -30 pour LONG, > -70 pour SHORT",
        condition=lambda sd, side: (
            (side == "LONG" and sd.get("wr_14", -50) < -30) or
            (side == "SHORT" and sd.get("wr_14", -50) > -70)
        ),
    ),

    ShadowFilter(
        name="RSI_divergence",
        description="Divergence RSI : bearish div sur LONG, bullish div sur SHORT",
        condition=lambda sd, side: (
            (side == "LONG" and sd.get("rsi_div", 0) == -1) or
            (side == "SHORT" and sd.get("rsi_div", 0) == 1)
        ),
    ),

    ShadowFilter(
        name="RSI_extreme_contre_sens",
        description="RSI en territoire extrême contre la direction (RSI<40 SHORT ou RSI>60 LONG)",
        condition=lambda sd, side: (
            (side == "LONG" and sd.get("rsi", 50) < 40) or
            (side == "SHORT" and sd.get("rsi", 50) > 60)
        ),
    ),

    ShadowFilter(
        name="CMF_contre_sens",
        description="CMF contre la direction (argent sort pour LONG, entre pour SHORT)",
        condition=lambda sd, side: (
            (side == "LONG" and sd.get("cmf", 0) < -0.1) or
            (side == "SHORT" and sd.get("cmf", 0) > 0.1)
        ),
    ),

    ShadowFilter(
        name="BB_width_trop_etroit",
        description="BB width très étroit (< 0.005) : pas de room pour un move",
        condition=lambda sd, side: sd.get("bb_width", 0.01) < 0.005,
    ),

    ShadowFilter(
        name="HTF_ADX_faible",
        description="ADX HTF faible (< 15) : pas de tendance sur le 4H",
        condition=lambda sd, side: sd.get("htf_adx", 25) < 15,
    ),

    ShadowFilter(
        name="Regime_contre_direction",
        description="Régime HTF opposé à la direction (bear_trend pour LONG, bull_trend pour SHORT)",
        condition=lambda sd, side: (
            (side == "LONG" and sd.get("regime", "") == "bear_trend") or
            (side == "SHORT" and sd.get("regime", "") == "bull_trend")
        ),
    ),

    ShadowFilter(
        name="WR_ET_RSI_div",
        description="WR faible ET RSI div (combinaison des deux)",
        condition=lambda sd, side: (
            (
                (side == "LONG" and sd.get("wr_14", -50) < -30) or
                (side == "SHORT" and sd.get("wr_14", -50) > -70)
            ) and (
                (side == "LONG" and sd.get("rsi_div", 0) == -1) or
                (side == "SHORT" and sd.get("rsi_div", 0) == 1)
            )
        ),
    ),

    # Ajouter d'autres filtres ici...
]


# =============================================================================
# Évaluation
# =============================================================================

@dataclass
class FilterResult:
    filter_name: str
    description: str
    n_total: int
    n_blocked: int
    pct_blocked: float
    wr_blocked: float      # WR des trades qui auraient été bloqués
    wr_not_blocked: float  # WR des trades qui passent
    r_blocked_avg: float   # R moyen des trades bloqués
    r_impact: float        # R total récupéré si on avait bloqué (négatif = on aurait perdu des gains)
    verdict: str           # "✅ Filtre utile", "❌ Filtre nocif", "⚠️  Neutre"


def evaluate_filters(
    positions: list[Position],
    filters: list[ShadowFilter],
) -> list[FilterResult]:
    """Évalue chaque filtre sur la liste des positions fermées."""
    results = []
    n_total = len(positions)
    if n_total == 0:
        return []

    total_r = sum(p.result_r or 0 for p in positions)
    wr_baseline = sum(1 for p in positions if (p.result_r or 0) > 0) / n_total

    for sf in filters:
        blocked = []
        not_blocked = []

        for pos in positions:
            sd = pos.signal_data or {}
            side = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
            would_block = sf.condition(sd, side)
            if would_block:
                blocked.append(pos)
            else:
                not_blocked.append(pos)

        n_blocked = len(blocked)
        if n_blocked == 0:
            results.append(FilterResult(
                filter_name=sf.name,
                description=sf.description,
                n_total=n_total,
                n_blocked=0,
                pct_blocked=0,
                wr_blocked=0,
                wr_not_blocked=wr_baseline,
                r_blocked_avg=0,
                r_impact=0,
                verdict="⚪ Aucun trade filtré",
            ))
            continue

        r_blocked = [p.result_r or 0 for p in blocked]
        r_not_blocked = [p.result_r or 0 for p in not_blocked]

        wr_blocked = sum(1 for r in r_blocked if r > 0) / len(r_blocked)
        wr_not_blocked = (
            sum(1 for r in r_not_blocked if r > 0) / len(r_not_blocked)
            if r_not_blocked else 0
        )
        r_blocked_avg = sum(r_blocked) / len(r_blocked)
        r_impact = -sum(r_blocked)  # R récupéré si on avait bloqué (positif = bénéfique)

        pct_blocked = n_blocked / n_total * 100

        # Verdict
        if r_impact > 0.5 and wr_blocked < 0.4 and pct_blocked < 30:
            verdict = "✅ Filtre utile"
        elif r_impact < -1.0 or wr_blocked > 0.7:
            verdict = "❌ Filtre nocif (bloque de bons trades)"
        elif pct_blocked > 40:
            verdict = "⚠️  Trop agressif (bloque trop)"
        else:
            verdict = "⚠️  Neutre / insuffisant"

        results.append(FilterResult(
            filter_name=sf.name,
            description=sf.description,
            n_total=n_total,
            n_blocked=n_blocked,
            pct_blocked=pct_blocked,
            wr_blocked=wr_blocked,
            wr_not_blocked=wr_not_blocked,
            r_blocked_avg=r_blocked_avg,
            r_impact=r_impact,
            verdict=verdict,
        ))

    return sorted(results, key=lambda r: -r.r_impact)


def print_report(
    results: list[FilterResult],
    baseline_wr: float,
    baseline_r: float,
    n_instruments: int,
    period: str,
    sample_type: str,
) -> None:
    """Affiche le rapport de comparaison des filtres."""
    print(f"\n{'═'*72}")
    print(f"  Shadow Filter Analysis — {n_instruments} instruments | {period} | {sample_type}")
    print(f"  Baseline : N={results[0].n_total if results else 0} trades | "
          f"WR={baseline_wr:.1%} | Total={baseline_r:+.1f}R")
    print(f"{'═'*72}")
    print(f"  {'Filtre':<22} {'Bloqués':>8} {'%':>5} {'WR♟':>6} {'WR✓':>6} "
          f"{'ΔR':>7}  Verdict")
    print(f"  {'─'*22} {'─'*8} {'─'*5} {'─'*6} {'─'*6} {'─'*7}  {'─'*20}")

    for r in results:
        print(
            f"  {r.filter_name:<22} {r.n_blocked:>8} {r.pct_blocked:>4.0f}% "
            f"{r.wr_blocked:>5.0%} {r.wr_not_blocked:>5.0%} "
            f"{r.r_impact:>+7.1f}R  {r.verdict}"
        )
    print(f"{'═'*72}")
    print("  ΔR = R récupéré si filtre activé (positif = bénéfique)")
    print("  WR♟ = WR des trades bloqués | WR✓ = WR des trades qui passent")


# =============================================================================
# CLI
# =============================================================================

PRESETS = {
    "crypto_top":  ["BTCUSD", "ETHUSD", "SOLUSD", "BNBUSD", "LNKUSD", "XRPUSD"],
    "fx_majors":   ["EURUSD", "GBPUSD", "USDJPY", "NZDCAD", "USDPLN", "USDSEK"],
    "metals":      ["XAUUSD", "XAGUSD", "XAUEUR", "XAGEUR"],
    "validated":   ["BTCUSD", "ETHUSD", "SOLUSD", "LNKUSD", "XRPUSD",
                    "EURUSD", "GBPUSD", "USDJPY", "NZDCAD", "XAUUSD"],
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Évaluation multi-shadow-filter en une passe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("instruments", nargs="*", help="Symboles (ex: BTCUSD XAUUSD)")
    parser.add_argument("--preset", choices=list(PRESETS.keys()), help="Preset d'instruments")
    parser.add_argument("--period", default="730d", help="Période (défaut: 730d)")
    parser.add_argument("--split", type=float, default=0.7, help="Fraction IS (défaut: 0.7)")
    parser.add_argument("--risk", type=float, default=0.40, help="Risque %% (défaut: 0.40)")
    parser.add_argument("--balance", type=float, default=100_000, help="Capital (défaut: 100k)")
    parser.add_argument("--csv", default=None, help="Export CSV des résultats")
    parser.add_argument("--oos-only", action="store_true", help="Évaluer sur OOS uniquement")
    parser.add_argument("--verbose", "-v", action="store_true", help="Détail des trades filtrés")
    args = parser.parse_args()

    # Résoudre les instruments
    instruments = list(args.instruments)
    if args.preset:
        instruments = PRESETS[args.preset]
        print(f"Preset '{args.preset}' : {len(instruments)} instruments")
    if not instruments:
        parser.print_help()
        sys.exit(0)

    # Collecter toutes les positions
    all_positions: list[Position] = []
    bt_config = BacktestConfig(
        start_balance=args.balance,
        risk_per_trade_pct=args.risk,
        verbose=False,
    )

    print(f"Chargement et backtest ({len(instruments)} instruments)...")
    for inst in instruments:
        df = load_ohlc(inst, period=args.period)
        if df is None or len(df) < 300:
            print(f"  ⚠️  {inst} : données insuffisantes, ignoré")
            continue

        if args.oos_only:
            _, df = split_in_out_sample(df, args.split)
            sample_label = "OOS"
        else:
            sample_label = "IS+OOS"

        runner = BacktestRunner(bt_config)
        result = runner.run(df, inst)
        closed = result.closed_positions
        all_positions.extend(closed)
        n_win = sum(1 for p in closed if (p.result_r or 0) > 0)
        total_r = sum(p.result_r or 0 for p in closed)
        wr = n_win / len(closed) if closed else 0
        print(f"  {inst:10s} : {len(closed):3d} trades | WR={wr:.0%} | {total_r:+.1f}R")

    if not all_positions:
        print("Aucune position collectée.")
        sys.exit(1)

    # Baseline
    n_total = len(all_positions)
    baseline_r = sum(p.result_r or 0 for p in all_positions)
    baseline_wr = sum(1 for p in all_positions if (p.result_r or 0) > 0) / n_total

    # Évaluation des filtres
    results = evaluate_filters(all_positions, SHADOW_FILTERS)

    # Rapport
    print_report(results, baseline_wr, baseline_r, len(instruments), args.period, sample_label)

    # Export CSV
    if args.csv:
        rows = []
        for r in results:
            rows.append({
                "filter": r.filter_name,
                "description": r.description,
                "n_blocked": r.n_blocked,
                "pct_blocked": round(r.pct_blocked, 1),
                "wr_blocked": round(r.wr_blocked, 3),
                "wr_not_blocked": round(r.wr_not_blocked, 3),
                "r_impact": round(r.r_impact, 2),
                "verdict": r.verdict,
            })
        pd.DataFrame(rows).to_csv(args.csv, index=False)
        print(f"\n  CSV exporté : {args.csv}")


if __name__ == "__main__":
    main()
