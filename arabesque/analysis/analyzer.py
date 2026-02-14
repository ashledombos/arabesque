"""
Arabesque v2 — Audit Log Analyzer.

Parse les logs JSONL du paper trading / live et génère :
- Rapport de performance (equity curve, expectancy, PF, DD)
- Calibration des guards (counterfactuels → est-ce que les rejets étaient justifiés ?)
- Timeline des événements (pour debug)
- Export CSV pour analyse externe

Usage :
    from arabesque.analysis.analyzer import AuditAnalyzer
    a = AuditAnalyzer("logs/audit")
    a.load()
    print(a.performance_report())
    print(a.guard_calibration_report())
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import numpy as np


class AuditAnalyzer:
    """Analyse les logs d'audit JSONL."""

    def __init__(self, audit_dir: str = "logs/audit"):
        self.audit_dir = Path(audit_dir)
        self.decisions: list[dict] = []
        self.counterfactuals: list[dict] = []
        self._trades: list[dict] | None = None

    def load(self, days_back: int = 0) -> "AuditAnalyzer":
        """Charge tous les fichiers JSONL du répertoire.

        Args:
            days_back: Si > 0, ne charge que les N derniers jours.
        """
        self.decisions = []
        self.counterfactuals = []
        self._trades = None

        cutoff = None
        if days_back > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).date()

        # Decisions
        for path in sorted(self.audit_dir.glob("decisions_*.jsonl")):
            if cutoff:
                date_str = path.stem.replace("decisions_", "")
                try:
                    file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    if file_date < cutoff:
                        continue
                except ValueError:
                    pass
            self.decisions.extend(self._read_jsonl(path))

        # Counterfactuals
        for path in sorted(self.audit_dir.glob("counterfactuals_*.jsonl")):
            if cutoff:
                date_str = path.stem.replace("counterfactuals_", "")
                try:
                    file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    if file_date < cutoff:
                        continue
                except ValueError:
                    pass
            self.counterfactuals.extend(self._read_jsonl(path))

        return self

    # ── Performance Report ───────────────────────────────────────────

    def performance_report(
        self,
        start_balance: float = 100_000.0,
        risk_per_trade_pct: float = 0.5,
    ) -> str:
        """Rapport de performance basé sur les trades fermés dans les logs."""
        trades = self._extract_trades()
        if not trades:
            return "Aucun trade fermé trouvé dans les logs."

        risk_cash = start_balance * (risk_per_trade_pct / 100)
        results_r = [t["result_r"] for t in trades if t["result_r"] is not None]

        if not results_r:
            return "Aucun trade avec résultat trouvé."

        n = len(results_r)
        wins = [r for r in results_r if r > 0]
        losses = [r for r in results_r if r <= 0]

        # Equity curve
        equity = [start_balance]
        for r in results_r:
            equity.append(equity[-1] + r * risk_cash)

        # Max DD
        peak = equity[0]
        max_dd = 0
        for eq in equity:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / start_balance * 100
            max_dd = max(max_dd, dd)

        # Daily P&L
        daily_pnl = self._daily_pnl(trades, risk_cash)
        worst_day = min(daily_pnl.values()) if daily_pnl else 0
        worst_day_pct = abs(worst_day) / start_balance * 100

        # Profit factor
        gross_profit = sum(r * risk_cash for r in results_r if r > 0)
        gross_loss = abs(sum(r * risk_cash for r in results_r if r < 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # By instrument
        by_inst = defaultdict(list)
        for t in trades:
            by_inst[t["instrument"]].append(t["result_r"] or 0)

        # By exit type
        by_exit = defaultdict(list)
        for t in trades:
            by_exit[t["exit_reason"]].append(t["result_r"] or 0)

        lines = [
            "=" * 60,
            "  ARABESQUE — PERFORMANCE REPORT (Paper Trading)",
            "=" * 60,
            f"  Period  : {trades[0].get('date', '?')} → {trades[-1].get('date', '?')}",
            f"  Trades  : {n}  ({'OK' if n >= 30 else 'INSUFFISANT, min 30'})",
            f"  Win rate: {len(wins)/n:.1%}",
            "",
            f"  Expectancy : {np.mean(results_r):+.3f}R  "
            f"(${np.mean(results_r) * risk_cash:+,.0f})",
            f"  Total      : {sum(results_r):+.1f}R  "
            f"(${sum(results_r) * risk_cash:+,.0f})",
            f"  Avg win    : {np.mean(wins):+.2f}R" if wins else "  Avg win    : N/A",
            f"  Avg loss   : {np.mean(losses):+.2f}R" if losses else "  Avg loss   : N/A",
            f"  Best/Worst : {max(results_r):+.2f}R / {min(results_r):+.2f}R",
            "",
            f"  Profit Factor : {pf:.2f}",
            f"  Max DD        : {max_dd:.1f}%",
            f"  Worst day     : {worst_day_pct:.1f}%  "
            f"({'DANGER' if worst_day_pct >= 3.0 else 'OK'})",
            f"  Final equity  : ${equity[-1]:,.0f}",
        ]

        # Par instrument
        if len(by_inst) > 1:
            lines.extend(["", "  Par instrument :"])
            for inst in sorted(by_inst, key=lambda x: sum(by_inst[x]), reverse=True):
                rs = by_inst[inst]
                lines.append(
                    f"    {inst:12s} : {len(rs):3d} trades  "
                    f"exp={np.mean(rs):+.3f}R  WR={sum(1 for r in rs if r>0)/len(rs):.0%}"
                )

        # Par type de sortie
        if by_exit:
            lines.extend(["", "  Par type de sortie :"])
            for exit_t in sorted(by_exit, key=lambda x: len(by_exit[x]), reverse=True):
                rs = by_exit[exit_t]
                lines.append(
                    f"    {exit_t:25s} : {len(rs):3d}  avg={np.mean(rs):+.2f}R"
                )

        lines.append("=" * 60)
        return "\n".join(lines)

    # ── Guard Calibration ────────────────────────────────────────────

    def guard_calibration_report(self) -> str:
        """Analyse les counterfactuels pour calibrer les guards.

        Question : les signaux rejetés auraient-ils été profitables ?
        Si oui → le guard est peut-être trop agressif.
        """
        if not self.counterfactuals:
            return "Aucun counterfactuel trouvé."

        # Par verdict
        by_verdict = defaultdict(list)
        for cf in self.counterfactuals:
            by_verdict[cf.get("verdict", "?")].append(cf)

        # Par raison de rejet (chercher dans les decisions)
        rejection_cfs = [cf for cf in self.counterfactuals
                         if "reject" in cf.get("verdict", "").lower()
                         or cf.get("verdict", "").startswith("good_")
                         or cf.get("verdict", "").startswith("missed_")]

        lines = [
            "=" * 60,
            "  ARABESQUE — GUARD CALIBRATION",
            "=" * 60,
            f"  Counterfactuels analysés : {len(self.counterfactuals)}",
            "",
        ]

        for verdict, cfs in sorted(by_verdict.items()):
            results = [cf.get("result_r", 0) for cf in cfs]
            n_profit = sum(1 for r in results if r > 0)
            n_loss = sum(1 for r in results if r <= 0)
            avg = np.mean(results) if results else 0

            tag = ""
            if verdict == "good_reject" and n_profit / len(cfs) > 0.6:
                tag = " ← CALIBRER (60%+ auraient profité)"
            elif verdict == "missed_gain":
                tag = " ← OPPORTUNITÉS MANQUÉES"

            lines.append(
                f"  {verdict:20s} : {len(cfs):3d}  "
                f"({n_profit} profit, {n_loss} loss)  "
                f"avg={avg:+.2f}R{tag}"
            )

        # Par instrument
        by_inst = defaultdict(list)
        for cf in self.counterfactuals:
            by_inst[cf.get("instrument", "?")].append(cf.get("result_r", 0))

        if len(by_inst) > 1:
            lines.extend(["", "  Par instrument :"])
            for inst, rs in sorted(by_inst.items()):
                pct_profit = sum(1 for r in rs if r > 0) / len(rs) if rs else 0
                lines.append(
                    f"    {inst:12s} : {len(rs):3d} CF  "
                    f"avg={np.mean(rs):+.2f}R  {pct_profit:.0%} auraient profité"
                )

        # Recommandation
        all_results = [cf.get("result_r", 0) for cf in self.counterfactuals]
        if all_results:
            pct_profit_total = sum(1 for r in all_results if r > 0) / len(all_results)
            avg_total = np.mean(all_results)
            lines.extend([
                "",
                f"  SYNTHÈSE : {pct_profit_total:.0%} des rejets auraient profité "
                f"(avg {avg_total:+.2f}R)",
            ])
            if pct_profit_total > 0.6 and avg_total > 0.2:
                lines.append("  → Les guards sont TROP RESTRICTIFS. "
                             "Considérer assouplir les seuils.")
            elif pct_profit_total < 0.4:
                lines.append("  → Les guards PROTÈGENT bien. Garder les seuils.")
            else:
                lines.append("  → Les guards sont ÉQUILIBRÉS.")

        lines.append("=" * 60)
        return "\n".join(lines)

    # ── Signal Flow Timeline ─────────────────────────────────────────

    def timeline(self, last_n: int = 50) -> str:
        """Affiche les N derniers événements sous forme de timeline."""
        events = sorted(self.decisions, key=lambda d: d.get("ts", ""))
        events = events[-last_n:]

        lines = ["  TIMELINE (last {} events)".format(len(events)),
                 "  " + "-" * 55]

        for e in events:
            ts = e.get("ts", "?")[:19]
            dtype = e.get("type", "?")
            inst = e.get("instrument", "")
            reason = e.get("reason", "")[:40]
            meta = e.get("meta", {})

            # Icône
            icon = {
                "signal_accepted": "✅",
                "signal_rejected": "❌",
                "order_filled": "📈",
                "sl_breakeven": "🔒",
                "trailing_activated": "📐",
                "trailing_tightened": "📐",
                "exit_sl": "🔴",
                "exit_tp": "🟢",
                "exit_trailing": "🟡",
                "exit_giveback": "🟠",
                "exit_deadfish": "💀",
                "exit_time_stop": "⏰",
            }.get(dtype, "•")

            r_str = ""
            if "result_r" in meta and meta["result_r"] is not None:
                r_str = f" [{meta['result_r']:+.2f}R]"

            lines.append(f"  {ts} {icon} {dtype:25s} {inst:8s} {reason}{r_str}")

        return "\n".join(lines)

    # ── Daily Summary ────────────────────────────────────────────────

    def daily_summary(self) -> str:
        """Résumé jour par jour."""
        trades = self._extract_trades()
        if not trades:
            return "Aucun trade trouvé."

        by_day: dict[str, list] = defaultdict(list)
        for t in trades:
            day = t.get("date", "?")
            by_day[day].append(t.get("result_r", 0) or 0)

        # Signaux par jour
        signals_by_day: dict[str, dict] = defaultdict(lambda: {"accepted": 0, "rejected": 0})
        for d in self.decisions:
            day = d.get("ts", "")[:10]
            if d.get("type") == "signal_accepted":
                signals_by_day[day]["accepted"] += 1
            elif d.get("type") == "signal_rejected":
                signals_by_day[day]["rejected"] += 1

        lines = [
            "  DAILY SUMMARY",
            "  " + "-" * 70,
            f"  {'Date':12s} {'Signals':>8s} {'Trades':>7s} {'WR':>5s} "
            f"{'Exp(R)':>8s} {'Total(R)':>9s} {'DD?':>4s}",
            "  " + "-" * 70,
        ]

        all_days = sorted(set(list(by_day.keys()) + list(signals_by_day.keys())))
        for day in all_days:
            trades_day = by_day.get(day, [])
            sigs = signals_by_day.get(day, {"accepted": 0, "rejected": 0})
            n_sig = sigs["accepted"] + sigs["rejected"]
            n_trades = len(trades_day)
            wr = sum(1 for r in trades_day if r > 0) / n_trades if n_trades else 0
            exp = np.mean(trades_day) if trades_day else 0
            total = sum(trades_day)
            dd_flag = "⚠️" if abs(total) > 2.0 else ""

            lines.append(
                f"  {day:12s} {n_sig:>5d}({sigs['rejected']}❌) "
                f"{n_trades:>7d} {wr:>4.0%} "
                f"{exp:>+7.3f} {total:>+8.2f} {dd_flag:>4s}"
            )

        lines.append("  " + "-" * 70)
        return "\n".join(lines)

    # ── Export CSV ───────────────────────────────────────────────────

    def export_trades_csv(self, path: str = "trades.csv") -> str:
        """Exporte les trades en CSV pour analyse externe."""
        trades = self._extract_trades()
        if not trades:
            return "Aucun trade à exporter."

        headers = [
            "date", "instrument", "side", "entry", "exit", "result_r",
            "mfe_r", "mae_r", "bars", "exit_reason", "trailing_tier",
        ]

        lines = [",".join(headers)]
        for t in trades:
            row = [
                t.get("date", ""),
                t.get("instrument", ""),
                t.get("side", ""),
                str(t.get("entry", "")),
                str(t.get("exit", "")),
                f"{t.get('result_r', 0):.3f}",
                f"{t.get('mfe_r', 0):.3f}",
                f"{t.get('mae_r', 0):.3f}",
                str(t.get("bars", 0)),
                t.get("exit_reason", ""),
                str(t.get("trailing_tier", 0)),
            ]
            lines.append(",".join(row))

        with open(path, "w") as f:
            f.write("\n".join(lines))

        return f"Exporté {len(trades)} trades → {path}"

    # ── Internal ─────────────────────────────────────────────────────

    def _extract_trades(self) -> list[dict]:
        """Reconstruit les trades à partir des décisions.

        Un trade = signal_accepted/order_filled + exit_* correspondant.
        """
        if self._trades is not None:
            return self._trades

        trades = []

        # Collecter les fills et exits par position_id
        fills: dict[str, dict] = {}
        exits: dict[str, dict] = {}

        for d in self.decisions:
            pos_id = d.get("position_id", "")
            dtype = d.get("type", "")

            if dtype == "order_filled":
                fills[pos_id] = d
            elif dtype.startswith("exit_"):
                exits[pos_id] = d

        for pos_id, exit_d in exits.items():
            fill_d = fills.get(pos_id, {})
            meta = exit_d.get("meta", {})

            trade = {
                "position_id": pos_id,
                "date": exit_d.get("ts", "")[:10],
                "instrument": exit_d.get("instrument", fill_d.get("instrument", "")),
                "side": fill_d.get("meta", {}).get("side", ""),
                "entry": fill_d.get("price", 0),
                "exit": exit_d.get("price", 0),
                "result_r": meta.get("result_r"),
                "mfe_r": meta.get("mfe_r", 0),
                "mae_r": meta.get("mae_r", 0),
                "bars": meta.get("bars_open", 0),
                "exit_reason": exit_d.get("type", ""),
                "trailing_tier": meta.get("trailing_tier", 0),
            }
            trades.append(trade)

        self._trades = sorted(trades, key=lambda t: t.get("date", ""))
        return self._trades

    def _daily_pnl(self, trades: list[dict], risk_cash: float) -> dict[str, float]:
        """Calcule le P&L par jour."""
        daily: dict[str, float] = defaultdict(float)
        for t in trades:
            day = t.get("date", "?")
            daily[day] += (t.get("result_r", 0) or 0) * risk_cash
        return dict(daily)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        """Lit un fichier JSONL."""
        entries = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries
