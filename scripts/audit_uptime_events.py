"""Summarize Arabesque availability samples from ``logs/uptime_events.jsonl``.

The watchdog writes one sample per run. This script turns the append-only log
into an uptime factor: how often the engine was fully usable, degraded, or
unable to trade, and why.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPTIME_EVENTS = ROOT / "logs" / "uptime_events.jsonl"


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def load_events(path: Path, since: datetime | None) -> list[dict]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            ts = _parse_ts(row["ts"])
        except Exception:
            continue
        if since and ts < since:
            continue
        row["_ts"] = ts
        events.append(row)
    events.sort(key=lambda r: r["_ts"])
    return events


def summarize(events: list[dict]) -> dict:
    if len(events) < 2:
        return {"n": len(events), "duration_s": 0, "by_status": {}, "by_cause": {}}
    by_status = defaultdict(float)
    by_cause = defaultdict(float)
    windows = []
    for cur, nxt in zip(events, events[1:]):
        dt_s = max(0.0, (nxt["_ts"] - cur["_ts"]).total_seconds())
        # Watchdog timer is normally 5 min. Cap large gaps so a stopped timer
        # does not falsely assign hours to the last known status.
        dt_s = min(dt_s, 15 * 60)
        status = cur.get("status", "unknown")
        cause = cur.get("cause", "unknown")
        by_status[status] += dt_s
        by_cause[cause] += dt_s
        if cause not in ("ok", "weekend"):
            windows.append({
                "start": cur["_ts"].isoformat(),
                "end": nxt["_ts"].isoformat(),
                "duration_min": round(dt_s / 60, 2),
                "status": status,
                "cause": cause,
                "bar_age_s": cur.get("last_bar_age_seconds"),
            })
    total = sum(by_status.values())
    return {
        "n": len(events),
        "duration_s": total,
        "uptime_factor": (
            round(by_cause.get("ok", 0.0) / total, 4) if total > 0 else None
        ),
        "by_status": {k: round(v, 3) for k, v in sorted(by_status.items())},
        "by_cause": {k: round(v, 3) for k, v in sorted(by_cause.items())},
        "degraded_windows": windows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=None, help="ISO timestamp/date")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    since = _parse_ts(args.since) if args.since else None
    summary = summarize(load_events(UPTIME_EVENTS, since))
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    total_h = summary["duration_s"] / 3600 if summary["duration_s"] else 0
    uptime = summary.get("uptime_factor")
    print(f"Uptime events: n={summary['n']} window≈{total_h:.2f}h")
    if uptime is not None:
        print(f"Uptime factor ok={uptime:.2%}")
    print("By cause:")
    for cause, seconds in summary["by_cause"].items():
        print(f"  {cause:<28s} {seconds/60:8.1f} min")
    if summary["degraded_windows"]:
        print("Recent degraded windows:")
        for w in summary["degraded_windows"][-10:]:
            print(
                f"  {w['start']} → {w['duration_min']:>5.1f} min "
                f"{w['cause']} ({w['status']})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
