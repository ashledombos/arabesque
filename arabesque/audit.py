"""
Arabesque v2 — Audit Logger.

JSONL append-only. Chaque Decision est une ligne.
Counterfactuels trackés et résolus au fil de l'eau.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from arabesque.models import Decision, Counterfactual


class AuditLogger:
    def __init__(self, log_dir: str = "logs/audit"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.stats = {
            "signals": 0, "accepted": 0, "rejected": 0,
            "rejections": {},
            "cf_profit": 0, "cf_loss": 0,
        }

    def log_decision(self, decision: Decision) -> None:
        self.stats["signals"] += 1
        if decision.decision_type.value == "signal_accepted":
            self.stats["accepted"] += 1
        elif decision.decision_type.value == "signal_rejected":
            self.stats["rejected"] += 1
            r = decision.reason
            self.stats["rejections"][r] = self.stats["rejections"].get(r, 0) + 1
        self._write(decision)

    def log_counterfactual(self, cf: Counterfactual) -> None:
        if cf.hypothetical_result_r > 0:
            self.stats["cf_profit"] += 1
        else:
            self.stats["cf_loss"] += 1
        self._write_cf(cf)

    def summary(self) -> str:
        s = self.stats
        total = s["signals"]
        if total == 0:
            return "No signals yet."
        lines = [
            f"Signals: {total}, accepted {s['accepted']}, rejected {s['rejected']}",
        ]
        if s["rejections"]:
            top = sorted(s["rejections"].items(), key=lambda x: -x[1])[:5]
            for reason, count in top:
                lines.append(f"  {reason}: {count}")
        cf_total = s["cf_profit"] + s["cf_loss"]
        if cf_total > 0:
            lines.append(f"Counterfactuals: {cf_total} ({s['cf_profit']} would profit, {s['cf_loss']} would lose)")
        return "\n".join(lines)

    def _write(self, decision: Decision) -> None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.log_dir / f"decisions_{date_str}.jsonl"
        entry = {
            "ts": decision.timestamp.isoformat(),
            "type": decision.decision_type.value,
            "signal_id": decision.signal_id,
            "position_id": decision.position_id,
            "instrument": decision.instrument,
            "reason": decision.reason,
            "reject_reason": decision.reject_reason.value if decision.reject_reason else None,
            "price": decision.price_at_decision,
            "spread": decision.spread_at_decision,
            "before": decision.value_before,
            "after": decision.value_after,
            "meta": decision.metadata,
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def _write_cf(self, cf: Counterfactual) -> None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.log_dir / f"counterfactuals_{date_str}.jsonl"
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "cf_id": cf.cf_id,
            "signal_id": cf.signal_id,
            "instrument": cf.instrument,
            "verdict": cf.verdict,
            "result_r": cf.hypothetical_result_r,
            "bars": cf.bars_tracked,
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
