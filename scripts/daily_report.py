#!/usr/bin/env python3
"""
Arabesque — Rapport quotidien / hebdomadaire automatisé.

Lit le trade_journal.jsonl et equity_snapshots.jsonl, produit un résumé
et l'envoie via les canaux de notification configurés (Telegram + ntfy).

Usage:
    python scripts/daily_report.py                  # rapport dernières 24h
    python scripts/daily_report.py --period weekly   # rapport 7 derniers jours
    python scripts/daily_report.py --period daily --dry-run  # preview sans envoi

Appelé automatiquement par :
    arabesque-report-daily.timer   (tous les jours 21:00 UTC)
    arabesque-report-weekly.timer  (dimanche 20:00 UTC)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ajouter le repo au path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

TRADE_JOURNAL = REPO / "logs" / "trade_journal.jsonl"
EQUITY_SNAPSHOTS = REPO / "logs" / "equity_snapshots.jsonl"


def load_journal(since: datetime) -> list[dict]:
    """Charge les trades depuis trade_journal.jsonl après `since`."""
    if not TRADE_JOURNAL.exists():
        return []
    trades = []
    for line in TRADE_JOURNAL.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_str = entry.get("timestamp") or entry.get("ts") or ""
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if ts >= since:
            trades.append(entry)
    return trades


def load_equity(since: datetime) -> list[dict]:
    """Charge les snapshots d'equity depuis equity_snapshots.jsonl."""
    if not EQUITY_SNAPSHOTS.exists():
        return []
    snaps = []
    for line in EQUITY_SNAPSHOTS.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_str = entry.get("timestamp") or entry.get("ts") or ""
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if ts >= since:
            snaps.append(entry)
    return snaps


def compute_stats(trades: list[dict]) -> dict:
    """Calcule WR, Exp, TotalR, distribution par stratégie/instrument."""
    if not trades:
        return {"n_trades": 0}

    # Filtrer seulement les exits (ont un exit_reason)
    exits = [t for t in trades if t.get("type") == "exit" or t.get("exit_reason")]
    entries = [t for t in trades if t.get("type") == "entry" or (not t.get("exit_reason") and t.get("instrument"))]

    if not exits:
        return {"n_trades": 0, "n_entries": len(entries)}

    # Calculer les R pour chaque exit
    results_r = []
    by_strategy = defaultdict(list)
    by_instrument = defaultdict(list)
    be_exits = 0
    sl_exits = 0
    tp_exits = 0
    trail_exits = 0

    for t in exits:
        r = t.get("result_r") or t.get("pnl_r") or 0
        results_r.append(r)

        strat = t.get("strategy", "unknown")
        sym = t.get("instrument") or t.get("symbol", "unknown")
        by_strategy[strat].append(r)
        by_instrument[sym].append(r)

        reason = t.get("exit_reason", "")
        if "breakeven" in reason:
            be_exits += 1
        elif "stop_loss" in reason:
            sl_exits += 1
        elif "take_profit" in reason:
            tp_exits += 1
        elif "trailing" in reason:
            trail_exits += 1

    n = len(results_r)
    wins = sum(1 for r in results_r if r > 0)
    wr = wins / n * 100 if n > 0 else 0
    total_r = sum(results_r)
    exp = total_r / n if n > 0 else 0

    # Par stratégie
    strat_stats = {}
    for strat, rs in by_strategy.items():
        sw = sum(1 for r in rs if r > 0)
        strat_stats[strat] = {
            "trades": len(rs),
            "wr": round(sw / len(rs) * 100, 1) if rs else 0,
            "total_r": round(sum(rs), 2),
            "exp": round(sum(rs) / len(rs), 3) if rs else 0,
        }

    return {
        "n_trades": n,
        "n_entries": len(entries),
        "wins": wins,
        "losses": n - wins,
        "wr": round(wr, 1),
        "total_r": round(total_r, 2),
        "exp": round(exp, 3),
        "be_exits": be_exits,
        "sl_exits": sl_exits,
        "tp_exits": tp_exits,
        "trail_exits": trail_exits,
        "by_strategy": strat_stats,
    }


def format_report(stats: dict, equity_snaps: list[dict], period: str) -> str:
    """Formate le rapport en texte pour Telegram."""
    label = "QUOTIDIEN" if period == "daily" else "HEBDOMADAIRE"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [f"📊 RAPPORT {label} — {now}", ""]

    if stats["n_trades"] == 0:
        lines.append("Aucun trade clôturé sur la période.")
        if stats.get("n_entries", 0) > 0:
            lines.append(f"Nouvelles entrées : {stats['n_entries']}")
    else:
        lines.append(f"Trades clôturés : {stats['n_trades']}")
        lines.append(f"WR : {stats['wr']}% ({stats['wins']}W / {stats['losses']}L)")
        lines.append(f"Expectancy : {stats['exp']:+.3f}R")
        lines.append(f"Total R : {stats['total_r']:+.2f}R")
        lines.append("")
        lines.append(
            f"Sorties : {stats['tp_exits']} TP, {stats['be_exits']} BE, "
            f"{stats['trail_exits']} Trail, {stats['sl_exits']} SL"
        )

        if stats.get("by_strategy"):
            lines.append("")
            lines.append("Par stratégie :")
            for strat, ss in stats["by_strategy"].items():
                lines.append(
                    f"  {strat}: {ss['trades']}t WR={ss['wr']}% "
                    f"Exp={ss['exp']:+.3f}R Total={ss['total_r']:+.2f}R"
                )

    # Equity
    if equity_snaps:
        first = equity_snaps[0]
        last = equity_snaps[-1]
        bal_start = first.get("balance") or first.get("bal", 0)
        bal_end = last.get("balance") or last.get("bal", 0)
        eq_end = last.get("equity") or last.get("eq", bal_end)
        if bal_start and bal_end:
            delta = bal_end - bal_start
            pct = delta / bal_start * 100 if bal_start else 0
            lines.append("")
            lines.append(f"💰 Balance : ${bal_end:,.0f} ({pct:+.2f}%)")
            lines.append(f"💰 Equity  : ${eq_end:,.0f}")

        # Protection level
        level = last.get("protection_level") or last.get("level", "")
        if level and level != "normal":
            lines.append(f"🛡️ Protection : {level.upper()}")

    return "\n".join(lines)


async def send_report(report: str, secrets_path: Path) -> None:
    """Envoie le rapport via Apprise (Telegram + ntfy)."""
    import yaml
    try:
        import apprise
    except ImportError:
        print("apprise non installé — rapport non envoyé")
        print(report)
        return

    secrets = yaml.safe_load(secrets_path.read_text()) or {}
    channels = secrets.get("notifications", {}).get("channels", [])
    if not channels:
        print("Aucun canal de notification configuré")
        print(report)
        return

    ap = apprise.Apprise()
    for ch in channels:
        if isinstance(ch, str):
            ap.add(ch)

    ok = await ap.async_notify(body=report, title="Arabesque Report")
    status = "✅" if ok else "❌"
    print(f"Notification {status}")


def main():
    parser = argparse.ArgumentParser(description="Arabesque — Rapport automatisé")
    parser.add_argument(
        "--period", choices=["daily", "weekly"], default="daily",
        help="Période du rapport (default: daily)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Affiche le rapport sans l'envoyer"
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    if args.period == "daily":
        since = now - timedelta(hours=24)
    else:
        since = now - timedelta(days=7)

    trades = load_journal(since)
    equity = load_equity(since)
    stats = compute_stats(trades)
    report = format_report(stats, equity, args.period)

    print(report)
    print()

    if not args.dry_run:
        secrets_path = REPO / "config" / "secrets.yaml"
        if secrets_path.exists():
            asyncio.run(send_report(report, secrets_path))
        else:
            print("⚠️ secrets.yaml non trouvé — rapport non envoyé")


if __name__ == "__main__":
    main()
