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
    exits_raw = [t for t in trades if t.get("type") == "exit" or t.get("exit_reason")]
    entries = [t for t in trades if t.get("type") == "entry" or (not t.get("exit_reason") and t.get("instrument"))]

    # Dédupliquer par trade_id (même trade exécuté sur plusieurs brokers)
    seen_tids: dict[str, dict] = {}
    for t in exits_raw:
        tid = t.get("trade_id", "")
        if tid and tid in seen_tids:
            continue  # déjà compté
        if tid:
            seen_tids[tid] = t
    exits = list(seen_tids.values()) if seen_tids else exits_raw

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


def _strategy_activity_summary() -> str:
    """Résumé de l'activité par stratégie (dernier trade, jours sans signal).

    Inclut les stratégies configurées (settings.yaml strategy_assignments)
    même si elles n'ont jamais produit de trade.
    """
    # Known strategies from journal
    last_by_strat: dict[str, str] = {}
    if TRADE_JOURNAL.exists():
        for line in TRADE_JOURNAL.read_text().splitlines():
            try:
                e = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if e.get("event") not in ("entry", "exit"):
                continue
            strat = e.get("strategy", "")
            ts = e.get("ts", "")
            if strat and ts:
                last_by_strat[strat] = max(last_by_strat.get(strat, ""), ts)

    # Also check configured strategies from settings.yaml
    settings_path = REPO / "config" / "settings.yaml"
    if settings_path.exists():
        try:
            import yaml
            settings = yaml.safe_load(settings_path.read_text()) or {}
            for strat_name in (settings.get("strategy_assignments") or {}):
                if strat_name not in last_by_strat:
                    last_by_strat[strat_name] = ""  # never traded
        except Exception:
            pass

    if not last_by_strat:
        return ""

    now = datetime.now(timezone.utc)
    parts = []
    for strat in sorted(last_by_strat):
        ts_str = last_by_strat[strat]
        if not ts_str:
            parts.append(f"{strat}: 0 trades")
            continue
        try:
            last = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            days = (now - last).days
            if days >= 2:
                parts.append(f"{strat}: {days}j")
        except (ValueError, TypeError):
            pass

    if not parts:
        return ""
    return "Inactif: " + ", ".join(parts)


def format_report(stats: dict, equity_snaps: list[dict], period: str) -> str:
    """Formate le rapport en texte compact pour Telegram."""
    label = "jour" if period == "daily" else "semaine"
    now_str = datetime.now(timezone.utc).strftime("%d/%m %Hh")

    lines = []

    # Trades
    if stats["n_trades"] == 0:
        lines.append(f"Rapport {label} {now_str} — aucun trade")
    else:
        n = stats["n_trades"]
        lines.append(
            f"Rapport {label} {now_str} — {n}t "
            f"WR {stats['wr']}% Exp {stats['exp']:+.3f}R "
            f"({stats['total_r']:+.2f}R)"
        )
        # Exit distribution compact
        parts = []
        if stats['tp_exits']:
            parts.append(f"{stats['tp_exits']}TP")
        if stats['be_exits']:
            parts.append(f"{stats['be_exits']}BE")
        if stats['trail_exits']:
            parts.append(f"{stats['trail_exits']}Trail")
        if stats['sl_exits']:
            parts.append(f"{stats['sl_exits']}SL")
        if parts:
            lines.append(" ".join(parts))

        if stats.get("by_strategy"):
            for strat, ss in stats["by_strategy"].items():
                lines.append(
                    f"  {strat}: {ss['trades']}t WR={ss['wr']}% "
                    f"Exp={ss['exp']:+.3f}R"
                )

    # Equity — compact per broker
    if equity_snaps:
        from collections import defaultdict as _dd
        by_broker = _dd(list)
        for s in equity_snaps:
            by_broker[s.get("broker_id", "")].append(s)

        broker_parts = []
        worst_level = "normal"
        for bid in sorted(by_broker.keys()):
            snaps_b = by_broker[bid]
            first, last = snaps_b[0], snaps_b[-1]
            bal_start = first.get("balance") or first.get("bal", 0)
            bal_end = last.get("balance") or last.get("bal", 0)
            dd_total = last.get("total_dd_pct", 0)
            bid_short = bid.replace("_challenge", "").replace("_compte1", "")
            if bal_start and bal_end:
                delta = bal_end - bal_start
                broker_parts.append(f"{bid_short} ${bal_end:,.0f} ({dd_total:+.1f}%)")
            level = last.get("protection_level") or last.get("level", "normal")
            if level != "normal":
                worst_level = level

        if broker_parts:
            lines.append(" | ".join(broker_parts))

        if worst_level != "normal":
            lines.append(f"Protection: {worst_level.upper()}")

    # Strategy activity
    activity = _strategy_activity_summary()
    if activity:
        lines.append(activity)

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
