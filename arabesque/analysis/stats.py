"""
Arabesque v2 — Analyse statistique avancée.

Placement : arabesque/backtest/stats.py

Trois outils complémentaires :
1. Wilson Score Interval — IC sur le win rate (mieux que binomial pour n < 100)
2. Bootstrap Monte Carlo — IC sur l'expectancy (tirage avec remplacement)
3. Monte Carlo Equity Curve — Distribution du max drawdown (séquences aléatoires)

Usage :
    from arabesque.backtest.stats import full_statistical_analysis
    report = full_statistical_analysis(results_r, risk_per_trade_pct=0.5)
    print(report)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class WilsonResult:
    """Résultat du Wilson Score Interval."""
    observed_wr: float
    n: int
    ci80_low: float
    ci80_high: float
    ci95_low: float
    ci95_high: float
    significant_at_95: bool  # True si CI95 lower > 50%


@dataclass
class BootstrapResult:
    """Résultat du bootstrap Monte Carlo sur l'expectancy."""
    observed_exp: float
    n: int
    mean_exp: float
    std_exp: float
    ci80_low: float
    ci80_high: float
    ci95_low: float
    ci95_high: float
    p_positive: float         # P(expectancy > 0)
    significant_at_95: bool   # True si CI95 lower > 0


@dataclass
class DrawdownResult:
    """Résultat du Monte Carlo equity curve."""
    observed_dd: float
    median_dd: float
    p95_dd: float
    p99_dd: float
    p_breach_daily: float     # P(breach 3% daily)
    p_breach_total: float     # P(breach 8% total)
    ftmo_compatible: bool     # P(breach) < 10%


def wilson_score_interval(
    wins: int,
    total: int,
    z80: float = 1.282,
    z95: float = 1.960,
) -> WilsonResult:
    """Calcule le Wilson Score Interval pour le win rate."""
    if total == 0:
        return WilsonResult(0, 0, 0, 0, 0, 0, False)

    p = wins / total
    n = total

    def _wilson(z):
        denom = 1 + z * z / n
        center = p + z * z / (2 * n)
        spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
        return (center - spread) / denom, (center + spread) / denom

    low80, high80 = _wilson(z80)
    low95, high95 = _wilson(z95)

    return WilsonResult(
        observed_wr=round(p, 4), n=n,
        ci80_low=round(low80, 4), ci80_high=round(high80, 4),
        ci95_low=round(low95, 4), ci95_high=round(high95, 4),
        significant_at_95=low95 > 0.5,
    )


def bootstrap_expectancy(
    results_r: list[float],
    n_simulations: int = 10_000,
    seed: int | None = 42,
) -> BootstrapResult:
    """Bootstrap Monte Carlo pour l'IC sur l'expectancy."""
    if not results_r:
        return BootstrapResult(0, 0, 0, 0, 0, 0, 0, 0, 0, False)

    if seed is not None:
        random.seed(seed)

    n = len(results_r)
    observed = sum(results_r) / n

    bootstrap_means = []
    for _ in range(n_simulations):
        sample = random.choices(results_r, k=n)
        bootstrap_means.append(sum(sample) / n)

    bootstrap_means.sort()

    mean_exp = sum(bootstrap_means) / len(bootstrap_means)
    variance = sum((x - mean_exp) ** 2 for x in bootstrap_means) / len(bootstrap_means)
    std_exp = math.sqrt(variance)

    def _pct(data, p):
        idx = int(len(data) * p)
        return data[min(idx, len(data) - 1)]

    ci80_low = _pct(bootstrap_means, 0.10)
    ci80_high = _pct(bootstrap_means, 0.90)
    ci95_low = _pct(bootstrap_means, 0.025)
    ci95_high = _pct(bootstrap_means, 0.975)
    p_positive = sum(1 for x in bootstrap_means if x > 0) / len(bootstrap_means)

    return BootstrapResult(
        observed_exp=round(observed, 4), n=n,
        mean_exp=round(mean_exp, 4), std_exp=round(std_exp, 4),
        ci80_low=round(ci80_low, 4), ci80_high=round(ci80_high, 4),
        ci95_low=round(ci95_low, 4), ci95_high=round(ci95_high, 4),
        p_positive=round(p_positive, 4),
        significant_at_95=ci95_low > 0,
    )


def monte_carlo_drawdown(
    results_r: list[float],
    risk_per_trade_pct: float = 0.5,
    start_balance: float = 100_000,
    n_simulations: int = 10_000,
    daily_dd_limit_pct: float = 3.0,
    total_dd_limit_pct: float = 8.0,
    seed: int | None = 42,
) -> DrawdownResult:
    """Monte Carlo equity curve pour la distribution du max drawdown."""
    if not results_r:
        return DrawdownResult(0, 0, 0, 0, 0, 0, False)

    if seed is not None:
        random.seed(seed)

    risk_cash = start_balance * (risk_per_trade_pct / 100)
    n_trades = len(results_r)

    # DD observé
    equity = start_balance
    peak = equity
    observed_max_dd = 0
    for r in results_r:
        equity += r * risk_cash
        if equity > peak:
            peak = equity
        dd_pct = (peak - equity) / start_balance * 100
        observed_max_dd = max(observed_max_dd, dd_pct)

    # Simulations
    max_dds = []
    daily_breaches = 0
    total_breaches = 0

    for _ in range(n_simulations):
        sequence = random.choices(results_r, k=n_trades)
        equity = start_balance
        peak = equity
        sim_max_dd = 0
        daily_pnl = 0
        worst_daily = 0

        for i, r in enumerate(sequence):
            pnl = r * risk_cash
            equity += pnl
            daily_pnl += pnl
            if equity > peak:
                peak = equity
            dd_pct = (peak - equity) / start_balance * 100
            sim_max_dd = max(sim_max_dd, dd_pct)

            # Simuler fin de journée (approximation : tous les 8 trades)
            if (i + 1) % 8 == 0 or i == n_trades - 1:
                daily_dd = abs(min(0, daily_pnl)) / start_balance * 100
                worst_daily = max(worst_daily, daily_dd)
                daily_pnl = 0

        max_dds.append(sim_max_dd)
        if worst_daily >= daily_dd_limit_pct:
            daily_breaches += 1
        if sim_max_dd >= total_dd_limit_pct:
            total_breaches += 1

    max_dds.sort()

    def _pct(data, p):
        idx = int(len(data) * p)
        return data[min(idx, len(data) - 1)]

    return DrawdownResult(
        observed_dd=round(observed_max_dd, 2),
        median_dd=round(_pct(max_dds, 0.50), 2),
        p95_dd=round(_pct(max_dds, 0.95), 2),
        p99_dd=round(_pct(max_dds, 0.99), 2),
        p_breach_daily=round(daily_breaches / n_simulations, 4),
        p_breach_total=round(total_breaches / n_simulations, 4),
        ftmo_compatible=(total_breaches / n_simulations < 0.10),
    )


def full_statistical_analysis(
    results_r: list[float],
    risk_per_trade_pct: float = 0.5,
    start_balance: float = 100_000,
    n_simulations: int = 10_000,
) -> str:
    """Analyse statistique complète, retourne un rapport texte."""
    n = len(results_r)
    if n == 0:
        return "Aucun trade pour l'analyse statistique."

    wins = sum(1 for r in results_r if r > 0)

    w = wilson_score_interval(wins, n)
    b = bootstrap_expectancy(results_r, n_simulations)
    d = monte_carlo_drawdown(results_r, risk_per_trade_pct, start_balance, n_simulations)

    lines = [
        f"{'='*60}",
        f"  ANALYSE STATISTIQUE AVANCÉE",
        f"{'='*60}",
        f"",
        f"  WILSON SCORE INTERVAL (Win Rate) :",
        f"    Observé     : {w.observed_wr:.1%} ({wins}/{n})",
        f"    IC80        : [{w.ci80_low:.1%}, {w.ci80_high:.1%}]",
        f"    IC95        : [{w.ci95_low:.1%}, {w.ci95_high:.1%}]",
        f"    WR > 50% ?  : {'OUI' if w.significant_at_95 else 'NON — pas significatif'}",
        f"",
        f"  BOOTSTRAP MONTE CARLO (Expectancy, {n_simulations} sims) :",
        f"    Observé     : {b.observed_exp:+.4f}R",
        f"    Moyenne     : {b.mean_exp:+.4f}R  (std: {b.std_exp:.4f})",
        f"    IC80        : [{b.ci80_low:+.4f}R, {b.ci80_high:+.4f}R]",
        f"    IC95        : [{b.ci95_low:+.4f}R, {b.ci95_high:+.4f}R]",
        f"    P(exp > 0)  : {b.p_positive:.1%}",
        f"    Significatif: {'OUI' if b.significant_at_95 else 'NON — IC95 croise zéro'}",
        f"",
        f"  MONTE CARLO EQUITY CURVE (Drawdown, {n_simulations} sims) :",
        f"    DD observé  : {d.observed_dd:.1f}%",
        f"    DD médian   : {d.median_dd:.1f}%",
        f"    DD P95      : {d.p95_dd:.1f}%  (raisonnablement pire)",
        f"    DD P99      : {d.p99_dd:.1f}%  (extrême)",
        f"    P(breach 3% daily)  : {d.p_breach_daily:.1%}",
        f"    P(breach 8% total)  : {d.p_breach_total:.1%}",
        f"    FTMO compatible     : {'OUI' if d.ftmo_compatible else 'NON — P(breach) >= 10%'}",
        f"",
    ]

    if b.significant_at_95 and d.ftmo_compatible:
        verdict = "EDGE SIGNIFICATIF + FTMO COMPATIBLE -> Pret pour forward-test"
    elif not b.significant_at_95 and d.ftmo_compatible:
        verdict = "DD compatible MAIS edge non significatif -> Besoin de plus de trades"
    elif b.significant_at_95 and not d.ftmo_compatible:
        verdict = "Edge significatif MAIS DD trop risque -> Reduire le risque par trade"
    else:
        verdict = "Ni edge significatif ni DD compatible -> Revoir la strategie"

    lines.extend([f"  VERDICT : {verdict}", f"{'='*60}"])
    return "\n".join(lines)
