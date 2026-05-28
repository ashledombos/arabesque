"""Shadow reference check — read-only live/theory integrity bundle.

This script is a portable entrypoint for humans or agents: it runs the two
existing reference checks together, persists one machine-readable verdict, and
optionally notifies Telegram. It does not place orders, change risk, or write
to broker state.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import apprise
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arabesque.notifications import select_notification_channels

OUT_LOG = ROOT / "logs" / "shadow_reference_checks.jsonl"
SECRETS = ROOT / "config" / "secrets.yaml"


def _run(cmd: list[str]) -> dict:
    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout_tail": "\n".join(result.stdout.splitlines()[-40:]),
        "stderr_tail": "\n".join(result.stderr.splitlines()[-20:]),
    }


def _notify(body: str) -> bool:
    if not SECRETS.exists():
        return False
    secrets = yaml.safe_load(SECRETS.read_text()) or {}
    channels = select_notification_channels(
        (secrets.get("notifications") or {}).get("channels") or [],
        urgent=False,
    )
    if not channels:
        return False
    ap = apprise.Apprise()
    for ch in channels:
        if isinstance(ch, str):
            ap.add(ch)
    return asyncio.run(ap.async_notify(
        body=body,
        title="Arabesque shadow reference",
        body_format=apprise.NotifyFormat.TEXT,
    ))


def build_commands(args: argparse.Namespace) -> list[list[str]]:
    base = [sys.executable]
    signals = base + [
        "scripts/replay_signals_vs_live.py",
        "--since", args.since,
        "--min-missing", str(args.min_missing),
    ]
    live_theory = base + [
        "scripts/replay_live_vs_theory.py",
        "--since", args.since,
        "--no-persist",
    ]
    if args.until:
        signals.extend(["--until", args.until])
    if args.strategy:
        live_theory.extend(["--strategy", args.strategy])
    if args.broker:
        live_theory.extend(["--broker", args.broker])
    if args.allow_yahoo:
        signals.append("--allow-yahoo")
        live_theory.append("--allow-yahoo")
    return [signals, live_theory]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-05-16T08:44:00Z")
    parser.add_argument("--until", default=None)
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--broker", default=None)
    parser.add_argument("--min-missing", type=int, default=0)
    parser.add_argument("--allow-yahoo", action="store_true")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    results = [_run(cmd) for cmd in build_commands(args)]
    ok = all(r["returncode"] == 0 for r in results)
    record = {
        "event": "shadow_reference_check",
        "ts": datetime.now(timezone.utc).isoformat(),
        "since": args.since,
        "until": args.until,
        "strategy": args.strategy,
        "broker": args.broker,
        "ok": ok,
        "duration_s": round(
            (datetime.now(timezone.utc) - started).total_seconds(), 3
        ),
        "checks": results,
    }
    OUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with OUT_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")

    status = "OK" if ok else "ALERTE"
    print(f"Shadow reference {status} — {len(results)} checks")
    for result in results:
        print(f"  rc={result['returncode']} {' '.join(result['cmd'][1:])}")

    if args.notify:
        body = (
            f"Shadow reference {status}\n"
            f"since={args.since}\n"
            + "\n".join(
                f"rc={r['returncode']} {' '.join(r['cmd'][1:3])}"
                for r in results
            )
        )
        _notify(body)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
