"""
Arabesque v2 — Backtest metrics.

Calcule :
- Expectancy (R et cash)
- Profit Factor
- Max DD (equity curve)
- Jours disqualifiants (DD_daily >= 3% ou DD_total >= 8%)
- Win rate, avg win, avg loss
- Slippage sensitivity
- Sortie par type (SL, TP, trailing, giveback, deadfish, time-stop)
- Ventilation par trailing tier (NOUVEAU)
- Distribution MFE (NOUVEAU)
"""

from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
import pandas as pd

from arabesque.models import Position, Side


@dataclass
class BacktestMetrics:
    """Résultats complets d'un backtest."""
    # Identification
    instrument: str = ""
    sample_type: str = ""       # "in_sample" ou "out_of_sample"
    start_date: str = ""
    end_date: str = ""
    n_bars: int = 0

    # Trades
    n_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0
    win_rate: float = 0.0

    # R-based
    expectancy_r: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    total_r: float = 0.0
    median_r: float = 0.0
    best_trade_r: float = 0.0
    worst_trade_r: float = 0.0

    # Cash-based
    expectancy_cash: float = 0.0
    total_pnl_cash: float = 0.0

    # Profit Factor
    profit_factor: float = 0.0

    # Drawdown
    max_dd_pct: float = 0.0
    max_dd_cash: float = 0.0
    max_dd_duration_bars: int = 0

    # Prop firm
    n_disqualifying_days: int = 0
    disqualifying_days: list[str] = field(default_factory=list)
    worst_daily_dd_pct: float = 0.0

    # Timing
    avg_bars_in_trade: float = 0.0
    avg_bars_win: float = 0.0
    avg_bars_loss: float = 0.0

    # Exits breakdown
    exits_by_type: dict[str, int] = field(default_factory=dict)
    exits_by_type_r: dict[str, float] = field(default_factory=dict)

    # ── NOUVEAU : Trailing tier breakdown ──
    exits_by_trailing_tier: dict[int, int] = field(default_factory=dict)
    exits_by_trailing_tier_r: dict[int, float] = field(default_factory=dict)
    mfe_distribution: dict[str, int] = field(default_factory=dict)  # "<0.25R": count

    # Slippage sensitivity
    slippage_sensitivity: dict[str, float] = field(default_factory=dict)

    # Equity curve (pour plot)
    equity_curve: list[float] = field(default_factory=list)
    equity_dates: list[str] = field(default_factory=list)

    # Signals
    n_signals_generated: int = 0
    n_signals_rejected: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)


def compute_metrics(
    closed_positions: list[Position],
    start_balance: float = 100_000.0,
    risk_per_trade_pct: float = 0.5,
    daily_dd_limit_pct: float = 3.0,
    total_dd_limit_pct: float = 8.0,
    instrument: str = "",
    sample_type: str = "",
) -> BacktestMetrics:
    """Calcule toutes les métriques à partir des positions fermées."""

    m = BacktestMetrics(instrument=instrument, sample_type=sample_type)

    if not closed_positions:
        return m

    # ── Basiques ──
    results_r = [p.result_r for p in closed_positions if p.result_r is not None]
    if not results_r:
        return m

    m.n_trades = len(results_r)
    m.n_wins = sum(1 for r in results_r if r > 0)
    m.n_losses = sum(1 for r in results_r if r <= 0)
    m.win_rate = m.n_wins / m.n_trades if m.n_trades > 0 else 0

    wins_r = [r for r in results_r if r > 0]
    losses_r = [r for r in results_r if r <= 0]

    m.avg_win_r = np.mean(wins_r) if wins_r else 0
    m.avg_loss_r = np.mean(losses_r) if losses_r else 0
    m.expectancy_r = np.mean(results_r)
    m.total_r = sum(results_r)
    m.median_r = float(np.median(results_r))
    m.best_trade_r = max(results_r)
    m.worst_trade_r = min(results_r)

    # ── Cash ──
    risk_cash = start_balance * (risk_per_trade_pct / 100)
    results_cash = [r * risk_cash for r in results_r]
    m.expectancy_cash = np.mean(results_cash)
    m.total_pnl_cash = sum(results_cash)

    # ── Profit Factor ──
    gross_profit = sum(r for r in results_cash if r > 0)
    gross_loss = abs(sum(r for r in results_cash if r < 0))
    m.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # ── Equity curve + Drawdown ──
    equity = [start_balance]
    for pnl in results_cash:
        equity.append(equity[-1] + pnl)
    m.equity_curve = equity

    # Max DD
    peak = equity[0]
    max_dd = 0
    max_dd_cash = 0
    dd_start = 0
    max_dd_duration = 0
    current_dd_start = 0

    for i, eq in enumerate(equity):
        if eq > peak:
            peak = eq
            current_dd_start = i
        dd = (peak - eq) / start_balance * 100
        dd_cash = peak - eq
        if dd > max_dd:
            max_dd = dd
            max_dd_cash = dd_cash
            max_dd_duration = i - current_dd_start

    m.max_dd_pct = round(max_dd, 2)
    m.max_dd_cash = round(max_dd_cash, 2)
    m.max_dd_duration_bars = max_dd_duration

    # ── Jours disqualifiants (prop firm) ──
    _compute_disqualifying_days(closed_positions, m, start_balance,
                                 risk_per_trade_pct, daily_dd_limit_pct,
                                 total_dd_limit_pct)

    # ── Timing ──
    bars = [p.bars_open for p in closed_positions]
    m.avg_bars_in_trade = np.mean(bars) if bars else 0
    win_bars = [p.bars_open for p in closed_positions if p.result_r and p.result_r > 0]
    loss_bars = [p.bars_open for p in closed_positions if p.result_r is not None and p.result_r <= 0]
    m.avg_bars_win = np.mean(win_bars) if win_bars else 0
    m.avg_bars_loss = np.mean(loss_bars) if loss_bars else 0

    # ── Exits breakdown ──
    for p in closed_positions:
        reason = p.exit_reason or "unknown"
        m.exits_by_type[reason] = m.exits_by_type.get(reason, 0) + 1
        r = p.result_r or 0
        m.exits_by_type_r[reason] = m.exits_by_type_r.get(reason, 0) + r

    # ── NOUVEAU : Trailing tier breakdown + MFE distribution ──
    _compute_trailing_breakdown(closed_positions, m)

    # Dates
    dates = [p.ts_entry for p in closed_positions if p.ts_entry]
    if dates:
        m.start_date = str(min(dates).date())
        m.end_date = str(max(dates).date())

    return m


def _compute_trailing_breakdown(positions: list[Position], m: BacktestMetrics):
    """Ventile les exits par trailing tier et calcule la distribution MFE.

    Trailing tier = le palier atteint par le trade avant de sortir.
    0 = pas de trailing activé (SL original ou autre exit).
    1-5 = paliers du trailing adaptatif.

    MFE distribution = combien loin les trades vont avant de revenir.
    Permet de diagnostiquer si le problème est le signal ou la gestion.
    """
    for p in positions:
        tier = p.trailing_tier
        m.exits_by_trailing_tier[tier] = m.exits_by_trailing_tier.get(tier, 0) + 1
        r = p.result_r or 0
        m.exits_by_trailing_tier_r[tier] = m.exits_by_trailing_tier_r.get(tier, 0) + r

    # Distribution MFE par buckets
    for p in positions:
        mfe = p.mfe_r
        if mfe < 0.25:
            bucket = "<0.25R"
        elif mfe < 0.5:
            bucket = "0.25-0.5R"
        elif mfe < 1.0:
            bucket = "0.5-1.0R"
        elif mfe < 1.5:
            bucket = "1.0-1.5R"
        elif mfe < 2.0:
            bucket = "1.5-2.0R"
        elif mfe < 3.0:
            bucket = "2.0-3.0R"
        else:
            bucket = "3.0R+"
        m.mfe_distribution[bucket] = m.mfe_distribution.get(bucket, 0) + 1


def _compute_disqualifying_days(
    positions: list[Position],
    m: BacktestMetrics,
    start_balance: float,
    risk_pct: float,
    daily_limit: float,
    total_limit: float,
):
    """Calcule les jours où DD >= limites prop firm.

    Simule une equity curve jour par jour en cumulant les P&L des trades fermés.
    """
    risk_cash = start_balance * (risk_pct / 100)

    # Grouper par jour
    daily_pnl: dict[str, float] = {}
    for p in positions:
        if p.ts_exit is None or p.result_r is None:
            continue
        day = str(p.ts_exit.date())
        daily_pnl[day] = daily_pnl.get(day, 0) + p.result_r * risk_cash

    # Simuler jour par jour
    equity = start_balance
    worst_daily = 0.0

    for day in sorted(daily_pnl.keys()):
        pnl = daily_pnl[day]
        daily_dd_pct = abs(min(0, pnl)) / start_balance * 100
        equity += pnl
        total_dd_pct = (start_balance - equity) / start_balance * 100

        if daily_dd_pct > worst_daily:
            worst_daily = daily_dd_pct

        if daily_dd_pct >= daily_limit or total_dd_pct >= total_limit:
            m.n_disqualifying_days += 1
            m.disqualifying_days.append(
                f"{day}: daily={daily_dd_pct:.1f}% total={total_dd_pct:.1f}%"
            )

    m.worst_daily_dd_pct = round(worst_daily, 2)


def slippage_sensitivity(
    base_expectancy_r: float,
    results_r: list[float],
    base_slippage_r: float = 0.05,
    multipliers: list[float] | None = None,
) -> dict[str, float]:
    """Teste la sensibilité de l'expectancy au slippage.

    Chaque trade perd base_slippage_r * multiplier supplémentaire.
    """
    if multipliers is None:
        multipliers = [1.0, 1.5, 2.0, 3.0]

    results = {}
    for mult in multipliers:
        extra_cost = base_slippage_r * mult
        adjusted = [r - extra_cost for r in results_r]
        exp = np.mean(adjusted) if adjusted else 0
        results[f"{mult}x"] = round(float(exp), 4)

    return results


def format_report(m: BacktestMetrics) -> str:
    """Formate un rapport lisible des métriques."""
    lines = [
        f"{'='*60}",
        f"  ARABESQUE BACKTEST — {m.instrument or 'All'} ({m.sample_type})",
        f"{'='*60}",
        f"  Period     : {m.start_date} → {m.end_date}",
        f"  Bars       : {m.n_bars}",
        f"  Signals    : {m.n_signals_generated} generated, {m.n_signals_rejected} rejected",
        f"",
        f"  TRADES     : {m.n_trades}  (min 30 = {'OK' if m.n_trades >= 30 else 'INSUFFISANT'})",
        f"  Win rate   : {m.win_rate:.1%}",
        f"  Avg win    : {m.avg_win_r:+.2f}R",
        f"  Avg loss   : {m.avg_loss_r:+.2f}R",
        f"",
        f"  EXPECTANCY : {m.expectancy_r:+.3f}R  ({m.expectancy_cash:+.0f} cash)",
        f"  Total R    : {m.total_r:+.1f}R  ({m.total_pnl_cash:+,.0f} cash)",
        f"  Median R   : {m.median_r:+.2f}R",
        f"  Best/Worst : {m.best_trade_r:+.2f}R / {m.worst_trade_r:+.2f}R",
        f"",
        f"  PROFIT FACTOR : {m.profit_factor:.2f}",
        f"",
        f"  MAX DD     : {m.max_dd_pct:.1f}%  ({m.max_dd_cash:,.0f} cash)",
        f"  DD duration: {m.max_dd_duration_bars} trades",
        f"",
        f"  PROP FIRM  :",
        f"    Jours disqualifiants : {m.n_disqualifying_days}",
        f"    Pire DD daily        : {m.worst_daily_dd_pct:.1f}%",
    ]

    if m.disqualifying_days:
        for d in m.disqualifying_days[:5]:
            lines.append(f"      {d}")
        if len(m.disqualifying_days) > 5:
            lines.append(f"      ... et {len(m.disqualifying_days) - 5} autres")

    lines.extend([
        f"",
        f"  TIMING     :",
        f"    Avg bars   : {m.avg_bars_in_trade:.0f}  (wins: {m.avg_bars_win:.0f}, losses: {m.avg_bars_loss:.0f})",
        f"",
        f"  EXITS      :",
    ])
    for exit_type, count in sorted(m.exits_by_type.items(), key=lambda x: -x[1]):
        avg_r = m.exits_by_type_r.get(exit_type, 0) / count if count > 0 else 0
        lines.append(f"    {exit_type:25s} : {count:3d} trades  avg {avg_r:+.2f}R")

    # ── NOUVEAU : Trailing tiers ──
    lines.extend(_format_trailing_section(m))

    if m.rejection_reasons:
        lines.extend([f"", f"  REJECTIONS :"])
        for reason, count in sorted(m.rejection_reasons.items(), key=lambda x: -x[1]):
            lines.append(f"    {reason:25s} : {count:3d}")

    if m.slippage_sensitivity:
        lines.extend([f"", f"  SLIPPAGE SENSITIVITY :"])
        for mult, exp in m.slippage_sensitivity.items():
            lines.append(f"    {mult:5s} slippage : expectancy = {exp:+.4f}R")

    lines.append(f"{'='*60}")
    return "\n".join(lines)


def _format_trailing_section(m: BacktestMetrics) -> list[str]:
    """Section trailing tier + MFE distribution pour format_report."""
    lines = []

    if m.exits_by_trailing_tier:
        lines.extend([f"", f"  TRAILING TIERS :"])
        for tier in sorted(m.exits_by_trailing_tier.keys()):
            count = m.exits_by_trailing_tier[tier]
            total_r = m.exits_by_trailing_tier_r.get(tier, 0)
            avg_r = total_r / count if count > 0 else 0
            label = f"Tier {tier}" if tier > 0 else "No trail"
            lines.append(f"    {label:15s} : {count:3d} trades  avg {avg_r:+.2f}R  total {total_r:+.1f}R")

    if m.mfe_distribution:
        lines.extend([f"", f"  MFE DISTRIBUTION (combien loin vont les trades) :"])
        # Ordre fixe des buckets
        bucket_order = ["<0.25R", "0.25-0.5R", "0.5-1.0R",
                        "1.0-1.5R", "1.5-2.0R", "2.0-3.0R", "3.0R+"]
        total = sum(m.mfe_distribution.values())
        for bucket in bucket_order:
            count = m.mfe_distribution.get(bucket, 0)
            pct = count / total * 100 if total > 0 else 0
            bar = "█" * int(pct / 2)
            lines.append(f"    {bucket:12s} : {count:3d} ({pct:4.1f}%) {bar}")

    return lines
