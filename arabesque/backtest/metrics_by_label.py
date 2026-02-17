"""
Arabesque v2 — Metrics by Signal Sub-Type (Phase 1.3).

Placement : arabesque/backtest/metrics_by_label.py

Ventile les résultats de backtest par sous-type de signal.
Répond à la question : quel type de signal porte l'edge, et sur quelles catégories ?

Usage :
    from arabesque.backtest.metrics_by_label import analyze_by_subtype, format_subtype_report

    # Après un backtest
    report = analyze_by_subtype(runner.closed_positions)
    print(format_subtype_report(report))
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arabesque.models import Position

import numpy as np


# ── Structures de résultat ──────────────────────────────────────────

@dataclass
class SubTypeMetrics:
    """Métriques pour un sous-type de signal."""
    sub_type: str
    n_trades: int = 0
    n_wins: int = 0
    results_r: list = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.n_wins / self.n_trades if self.n_trades > 0 else 0.0

    @property
    def expectancy(self) -> float:
        return np.mean(self.results_r) if self.results_r else 0.0

    @property
    def profit_factor(self) -> float:
        gains = sum(r for r in self.results_r if r > 0)
        losses = abs(sum(r for r in self.results_r if r < 0))
        return gains / losses if losses > 0 else float('inf') if gains > 0 else 0.0

    @property
    def avg_win(self) -> float:
        wins = [r for r in self.results_r if r > 0]
        return np.mean(wins) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [r for r in self.results_r if r < 0]
        return np.mean(losses) if losses else 0.0

    @property
    def std_r(self) -> float:
        return np.std(self.results_r) if len(self.results_r) > 1 else 0.0

    @property
    def total_r(self) -> float:
        return sum(self.results_r)


@dataclass
class FactorAnalysis:
    """Analyse d'un facteur continu par rapport aux résultats."""
    factor_name: str
    correlation: float = 0.0      # Pearson correlation avec result_r
    mean_winners: float = 0.0     # Valeur moyenne du facteur chez les gagnants
    mean_losers: float = 0.0      # Valeur moyenne chez les perdants
    n_samples: int = 0


# ── Analyse principale ──────────────────────────────────────────────

def analyze_by_subtype(
    positions: list,
    min_trades: int = 5,
) -> dict[str, SubTypeMetrics]:
    """Ventile les positions fermées par sub_type du signal d'origine.

    Args:
        positions: Liste de Position fermées (avec signal_data)
        min_trades: Minimum de trades pour inclure un sub_type

    Returns:
        Dict sub_type → SubTypeMetrics
    """
    groups: dict[str, SubTypeMetrics] = defaultdict(lambda: SubTypeMetrics(sub_type=""))

    for pos in positions:
        if pos.is_open or pos.result_r is None:
            continue

        # Extraire le sub_type du signal d'origine
        sub_type = _get_sub_type(pos)
        if not sub_type:
            sub_type = "unlabeled"

        if groups[sub_type].sub_type == "":
            groups[sub_type].sub_type = sub_type

        groups[sub_type].n_trades += 1
        groups[sub_type].results_r.append(pos.result_r)
        if pos.result_r > 0:
            groups[sub_type].n_wins += 1

    # Filtrer par min_trades
    return {k: v for k, v in groups.items() if v.n_trades >= min_trades}


def analyze_factors(
    positions: list,
) -> list[FactorAnalysis]:
    """Analyse les facteurs continus et leur corrélation avec les résultats.

    Args:
        positions: Liste de Position fermées

    Returns:
        Liste de FactorAnalysis triée par |correlation|
    """
    # Collecter facteurs et résultats
    factor_values: dict[str, list] = defaultdict(list)
    results: list[float] = []

    for pos in positions:
        if pos.is_open or pos.result_r is None:
            continue

        label_factors = _get_label_factors(pos)
        if not label_factors:
            continue

        results.append(pos.result_r)
        for key, val in label_factors.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                factor_values[key].append(val)
            elif isinstance(val, bool):
                factor_values[key].append(1.0 if val else 0.0)

    if len(results) < 10:
        return []

    analyses = []
    results_arr = np.array(results)

    for factor_name, values in factor_values.items():
        if len(values) != len(results):
            continue

        vals_arr = np.array(values)

        # Pearson correlation
        if np.std(vals_arr) > 0 and np.std(results_arr) > 0:
            corr = np.corrcoef(vals_arr, results_arr)[0, 1]
        else:
            corr = 0.0

        # Mean for winners vs losers
        win_mask = results_arr > 0
        lose_mask = results_arr < 0
        mean_w = float(np.mean(vals_arr[win_mask])) if win_mask.sum() > 0 else 0.0
        mean_l = float(np.mean(vals_arr[lose_mask])) if lose_mask.sum() > 0 else 0.0

        analyses.append(FactorAnalysis(
            factor_name=factor_name,
            correlation=float(corr),
            mean_winners=mean_w,
            mean_losers=mean_l,
            n_samples=len(values),
        ))

    # Trier par |correlation| décroissante
    analyses.sort(key=lambda a: abs(a.correlation), reverse=True)
    return analyses


# ── Helpers ──────────────────────────────────────────────────────────

def _get_sub_type(pos) -> str:
    """Extrait le sub_type d'une position."""
    # Méthode 1 : attribut direct sur le signal stocké
    sd = getattr(pos, "signal_data", {})
    if isinstance(sd, dict):
        st = sd.get("sub_type", "")
        if st:
            return st

    # Méthode 2 : attribut sub_type sur la position elle-même
    return getattr(pos, "_sub_type", "")


def _get_label_factors(pos) -> dict:
    """Extrait les label_factors d'une position."""
    sd = getattr(pos, "signal_data", {})
    if isinstance(sd, dict):
        return sd.get("label_factors", {})
    return {}


# ── Format report ────────────────────────────────────────────────────

def format_subtype_report(
    groups: dict[str, SubTypeMetrics],
    factors: list[FactorAnalysis] | None = None,
    title: str = "VENTILATION PAR SOUS-TYPE",
) -> str:
    """Formate un rapport de ventilation par sub_type.

    Returns:
        String formatée pour affichage console.
    """
    lines = []
    lines.append(f"\n{'='*70}")
    lines.append(f"  {title}")
    lines.append(f"{'='*70}")

    if not groups:
        lines.append("  Aucun trade labelé.")
        return "\n".join(lines)

    # Trier par expectancy décroissante
    sorted_groups = sorted(groups.values(), key=lambda g: g.expectancy, reverse=True)

    # Header
    lines.append(f"  {'Sub-Type':<22s} {'N':>4s} {'WR':>6s} {'Exp':>8s} {'PF':>6s} "
                 f"{'AvgW':>7s} {'AvgL':>7s} {'TotalR':>8s}")
    lines.append(f"  {'─'*68}")

    total_trades = 0
    total_r = 0.0

    for g in sorted_groups:
        marker = "+" if g.expectancy > 0 else " "
        lines.append(
            f"  {marker}{g.sub_type:<21s} {g.n_trades:>4d} {g.win_rate:>5.1%} "
            f"{g.expectancy:>+7.4f}R {g.profit_factor:>5.2f} "
            f"{g.avg_win:>+6.3f}R {g.avg_loss:>+6.3f}R {g.total_r:>+7.1f}R"
        )
        total_trades += g.n_trades
        total_r += g.total_r

    lines.append(f"  {'─'*68}")
    lines.append(f"  {'TOTAL':<22s} {total_trades:>4d} {'':>6s} {'':>8s} {'':>6s} "
                 f"{'':>7s} {'':>7s} {total_r:>+7.1f}R")

    # Factor analysis
    if factors:
        lines.append(f"\n  FACTEURS (corrélation avec résultat R) :")
        lines.append(f"  {'Facteur':<20s} {'Corr':>7s}  {'Moy gagnants':>14s}  {'Moy perdants':>14s}")
        lines.append(f"  {'─'*58}")
        for f in factors[:8]:  # Top 8
            sig = "***" if abs(f.correlation) > 0.15 else "**" if abs(f.correlation) > 0.10 else "*" if abs(f.correlation) > 0.05 else ""
            lines.append(
                f"  {f.factor_name:<20s} {f.correlation:>+6.3f}{sig:<3s} "
                f"{f.mean_winners:>14.3f}  {f.mean_losers:>14.3f}"
            )

    return "\n".join(lines)


# ── Pipeline integration ─────────────────────────────────────────────

def ventilate_pipeline_results(
    all_positions: dict[str, list],
    instrument_categories: dict[str, str],
    min_trades: int = 10,
) -> str:
    """Analyse croisée sub_type × catégorie pour le pipeline.

    Args:
        all_positions: Dict instrument → list[Position]
        instrument_categories: Dict instrument → category (fx, crypto, metals...)
        min_trades: Minimum de trades par cellule

    Returns:
        Rapport formaté.
    """
    # Grouper par (category, sub_type)
    cross: dict[tuple[str, str], SubTypeMetrics] = defaultdict(
        lambda: SubTypeMetrics(sub_type="")
    )
    cat_totals: dict[str, SubTypeMetrics] = defaultdict(
        lambda: SubTypeMetrics(sub_type="")
    )

    for inst, positions in all_positions.items():
        cat = instrument_categories.get(inst, "other")
        for pos in positions:
            if pos.is_open or pos.result_r is None:
                continue
            sub = _get_sub_type(pos)
            if not sub:
                sub = "unlabeled"

            key = (cat, sub)
            if cross[key].sub_type == "":
                cross[key].sub_type = sub
            cross[key].n_trades += 1
            cross[key].results_r.append(pos.result_r)
            if pos.result_r > 0:
                cross[key].n_wins += 1

            if cat_totals[cat].sub_type == "":
                cat_totals[cat].sub_type = cat
            cat_totals[cat].n_trades += 1
            cat_totals[cat].results_r.append(pos.result_r)
            if pos.result_r > 0:
                cat_totals[cat].n_wins += 1

    # Collecter les sub_types existants
    all_subs = sorted(set(sub for (_, sub) in cross.keys()))
    all_cats = sorted(set(cat for (cat, _) in cross.keys()))

    lines = []
    lines.append(f"\n{'='*80}")
    lines.append(f"  MATRICE SUB-TYPE × CATÉGORIE (expectancy OOS en R)")
    lines.append(f"{'='*80}")

    # Header
    header = f"  {'':20s}"
    for sub in all_subs:
        header += f" {sub[:14]:>14s}"
    header += f" {'TOTAL':>10s}"
    lines.append(header)
    lines.append(f"  {'─'*78}")

    for cat in all_cats:
        row = f"  {cat:<20s}"
        for sub in all_subs:
            key = (cat, sub)
            if key in cross and cross[key].n_trades >= min_trades:
                m = cross[key]
                exp_str = f"{m.expectancy:+.3f}R({m.n_trades})"
                row += f" {exp_str:>14s}"
            elif key in cross:
                row += f" {'<'+str(cross[key].n_trades)+'t':>14s}"
            else:
                row += f" {'—':>14s}"

        # Total for category
        if cat in cat_totals and cat_totals[cat].n_trades > 0:
            ct = cat_totals[cat]
            row += f" {ct.expectancy:+.3f}R({ct.n_trades})"
        lines.append(row)

    # Totals row
    lines.append(f"  {'─'*78}")
    row = f"  {'TOTAL':<20s}"
    for sub in all_subs:
        sub_total = SubTypeMetrics(sub_type=sub)
        for cat in all_cats:
            key = (cat, sub)
            if key in cross:
                sub_total.n_trades += cross[key].n_trades
                sub_total.results_r.extend(cross[key].results_r)
                sub_total.n_wins += cross[key].n_wins
        if sub_total.n_trades > 0:
            row += f" {sub_total.expectancy:+.3f}R({sub_total.n_trades})"
        else:
            row += f" {'—':>14s}"
    lines.append(row)

    # Interpretation
    lines.append(f"\n  INTERPRÉTATION :")
    best_sub = None
    best_exp = -999
    for sub in all_subs:
        sub_total = SubTypeMetrics(sub_type=sub)
        for cat in all_cats:
            key = (cat, sub)
            if key in cross:
                sub_total.results_r.extend(cross[key].results_r)
                sub_total.n_trades += cross[key].n_trades
        if sub_total.n_trades >= min_trades and sub_total.expectancy > best_exp:
            best_exp = sub_total.expectancy
            best_sub = sub
    if best_sub:
        lines.append(f"    Meilleur sub-type global : {best_sub} (exp={best_exp:+.4f}R)")

    # Check if any sub-type works across categories
    for sub in all_subs:
        cats_positive = []
        cats_negative = []
        for cat in all_cats:
            key = (cat, sub)
            if key in cross and cross[key].n_trades >= min_trades:
                if cross[key].expectancy > 0:
                    cats_positive.append(cat)
                else:
                    cats_negative.append(cat)
        if len(cats_positive) >= 2:
            lines.append(f"    {sub} : positif sur {', '.join(cats_positive)}"
                        + (f" (négatif sur {', '.join(cats_negative)})" if cats_negative else ""))

    return "\n".join(lines)
