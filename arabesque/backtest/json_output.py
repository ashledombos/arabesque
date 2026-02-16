"""
Arabesque v2 — Machine-readable backtest output.

Ajoute --output json au CLI pour émettre un JSONL compact
exploitable par un LLM, un pipeline d'optimisation, ou une base de données.

Format : un objet JSON par ligne (JSONL)
- Ligne 1 : metadata (run config, instruments, dates)
- Ligne N : résultat par instrument (in-sample + out-of-sample)
- Dernière ligne : synthèse multi-instrument

Usage :
    python scripts/backtest.py EURUSD XAUUSD BTC --output json > results.jsonl
    python scripts/backtest.py EURUSD XAUUSD BTC --output json --output-file results.jsonl
    
    # Silencieux (que le JSON, pas les logs humains)
    python scripts/backtest.py EURUSD --output json --quiet > results.jsonl
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import TextIO

from arabesque.backtest.metrics import BacktestMetrics


def metrics_to_dict(m: BacktestMetrics) -> dict:
    """Convertit BacktestMetrics en dict compact pour JSON."""
    return {
        "label": m.sample_type,
        "period": f"{m.start_date} → {m.end_date}",
        "bars": m.n_bars,
        "signals_generated": m.n_signals_generated,
        "signals_rejected": m.n_signals_rejected,
        "trades": m.n_trades,
        "sufficient": m.n_trades >= 30,
        "wins": m.n_wins,
        "losses": m.n_losses,
        "win_rate_pct": round(m.win_rate * 100, 1),
        "avg_win_r": round(m.avg_win_r, 3),
        "avg_loss_r": round(m.avg_loss_r, 3),
        "expectancy_r": round(m.expectancy_r, 4),
        "expectancy_cash": round(m.expectancy_cash, 0),
        "total_r": round(m.total_r, 1),
        "median_r": round(m.median_r, 2),
        "best_r": round(m.best_trade_r, 2),
        "worst_r": round(m.worst_trade_r, 2),
        "profit_factor": round(m.profit_factor, 2),
        "max_dd_pct": round(m.max_dd_pct, 1),
        "max_dd_cash": round(m.max_dd_cash, 0),
        "dd_duration_trades": m.max_dd_duration_bars,
        "disqual_days": m.n_disqualifying_days,
        "worst_daily_dd_pct": m.worst_daily_dd_pct,
        "avg_bars": round(m.avg_bars_in_trade),
        "avg_bars_win": round(m.avg_bars_win),
        "avg_bars_loss": round(m.avg_bars_loss),
        "exits": m.exits_by_type,
        "rejections": m.rejection_reasons,
        "slippage_sensitivity": m.slippage_sensitivity,
        # ── NOUVEAU : Trailing et MFE ──
        "trailing_tiers": m.exits_by_trailing_tier,
        "trailing_tiers_r": m.exits_by_trailing_tier_r,
        "mfe_distribution": m.mfe_distribution,
    }


def result_to_jsonl(
    instrument: str,
    result_in,   # BacktestResult
    result_out,  # BacktestResult
    data_source: str = "unknown",
    bars_total: int = 0,
) -> dict:
    """Convertit une paire in/out BacktestResult en dict JSONL."""
    m_in = result_in.metrics
    m_out = result_out.metrics

    return {
        "type": "instrument_result",
        "instrument": instrument,
        "data_source": data_source,
        "bars_total": bars_total,
        "in_sample": metrics_to_dict(m_in),
        "out_of_sample": metrics_to_dict(m_out),
        "delta": {
            "win_rate_pct": round((m_out.win_rate - m_in.win_rate) * 100, 1),
            "expectancy_r": round(m_out.expectancy_r - m_in.expectancy_r, 4),
            "profit_factor": round(m_out.profit_factor - m_in.profit_factor, 2),
            "max_dd_pct": round(m_out.max_dd_pct - m_in.max_dd_pct, 1),
        },
        "viable": _is_viable(m_out),
        "verdict": _verdict(m_out),
    }


def _is_viable(m: BacktestMetrics) -> bool:
    """Heuristique : le résultat OOS est-il exploitable en prop ?"""
    if m.n_trades < 30:
        return False
    if m.expectancy_r <= 0:
        return False
    if m.profit_factor < 1.0:
        return False
    if m.n_disqualifying_days > 0:
        return False
    return True


def _verdict(m: BacktestMetrics) -> str:
    """Verdict textuel court."""
    if m.n_trades < 30:
        return "INSUFFICIENT_DATA"
    if m.n_disqualifying_days > 3:
        return "PROP_FAIL"
    if m.expectancy_r <= -0.1:
        return "NEGATIVE_EDGE"
    if m.expectancy_r <= 0:
        return "NO_EDGE"
    if m.profit_factor < 1.05:
        return "MARGINAL"
    if m.profit_factor >= 1.2 and m.n_disqualifying_days == 0:
        return "VIABLE"
    return "BORDERLINE"


def synthesis_to_jsonl(results: dict) -> dict:
    """Synthèse multi-instrument en JSONL."""
    instruments = []
    for inst, (r_in, r_out) in results.items():
        m = r_out.metrics
        instruments.append({
            "instrument": inst,
            "trades": m.n_trades,
            "win_rate_pct": round(m.win_rate * 100, 1),
            "expectancy_r": round(m.expectancy_r, 4),
            "profit_factor": round(m.profit_factor, 2),
            "max_dd_pct": round(m.max_dd_pct, 1),
            "disqual_days": m.n_disqualifying_days,
            "viable": _is_viable(m),
            "verdict": _verdict(m),
        })

    viable = [i for i in instruments if i["viable"]]

    return {
        "type": "synthesis",
        "total_instruments": len(instruments),
        "viable_count": len(viable),
        "viable_list": [i["instrument"] for i in viable],
        "instruments": instruments,
    }


class JsonOutput:
    """Writer JSONL pour résultats de backtest.
    
    Usage dans le runner :
        jout = JsonOutput(open("results.jsonl", "w"))
        jout.emit({"type": "metadata", ...})
        jout.emit(result_to_jsonl(...))
        jout.emit(synthesis_to_jsonl(...))
    """

    def __init__(self, file: TextIO | None = None):
        self.file = file or sys.stdout

    def emit(self, obj: dict):
        """Émet une ligne JSONL."""
        line = json.dumps(obj, ensure_ascii=False, default=str)
        self.file.write(line + "\n")
        self.file.flush()

    def emit_metadata(
        self,
        instruments: list[str],
        strategy: str,
        period: str | None = None,
        start: str | None = None,
        end: str | None = None,
        balance: float = 100_000,
        risk_pct: float = 0.5,
    ):
        self.emit({
            "type": "metadata",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "instruments": instruments,
            "strategy": strategy,
            "period": period,
            "start": start,
            "end": end,
            "balance": balance,
            "risk_pct": risk_pct,
        })

    def close(self):
        if self.file and self.file != sys.stdout:
            self.file.close()
