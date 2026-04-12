#!/usr/bin/env python3
"""
Arabesque — Health check complet (quotidien automatisé).

Vérifie la santé globale du système de trading et alerte si anomalie.
Conçu pour être appelé par le timer systemd daily en complément du
rapport et du drift check.

Checks effectués :
  1. DD compte (daily + total) vs seuils FTMO/GFT
  2. Sizing aberrant (lots journal vs lots attendus)
  3. Positions sans SL/TP (orphelines)
  4. Positions en perte > -0.8R sans BE
  5. Broker connectivity (les comptes répondent)
  6. Corrélation excessive (trop de positions même catégorie)
  7. Rodage : WR/Exp par stratégie en rodage
  8. Données parquet à jour
  9. Counterfactuals (slippage rejects accumulés)
  10. Losing streak (séries de pertes consécutives)
  11. Moteur live actif (systemd service running)
  12. Winrate en chute vs backtest historique

Usage:
    python scripts/health_check.py                  # run + print
    python scripts/health_check.py --notify          # run + envoyer alertes
    python scripts/health_check.py --dry-run         # preview sans envoi
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

TRADE_JOURNAL = REPO / "logs" / "trade_journal.jsonl"
EQUITY_SNAPSHOTS = REPO / "logs" / "equity_snapshots.jsonl"
SLIPPAGE_REJECTS = REPO / "logs" / "slippage_rejects.jsonl"


# ─── Severity levels ───────────────────────────────────────────────
class Severity:
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRIT"


class Alert:
    def __init__(self, severity: str, category: str, message: str):
        self.severity = severity
        self.category = category
        self.message = message

    def __str__(self):
        icon = {"INFO": "ℹ️", "WARN": "⚠️", "CRIT": "🚨"}.get(self.severity, "?")
        return f"{icon} [{self.severity}] {self.category}: {self.message}"


# ─── Data loaders ──────────────────────────────────────────────────
def load_journal_entries(since: datetime | None = None) -> list[dict]:
    if not TRADE_JOURNAL.exists():
        return []
    entries = []
    for line in TRADE_JOURNAL.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since:
            ts_str = entry.get("ts") or entry.get("timestamp") or ""
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts < since:
                    continue
            except (ValueError, TypeError):
                continue
        entries.append(entry)
    return entries


def _dedup_exits(entries: list[dict]) -> list[dict]:
    """Extrait les exits et déduplique par trade_id (multi-broker)."""
    exits_raw = [e for e in entries if e.get("event") == "exit"]
    seen: dict[str, dict] = {}
    for t in exits_raw:
        tid = t.get("trade_id", "")
        if tid and tid in seen:
            continue
        if tid:
            seen[tid] = t
    return list(seen.values()) if seen else exits_raw


def load_equity_snapshots(since: datetime | None = None) -> list[dict]:
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
        if since:
            ts_str = entry.get("ts") or entry.get("timestamp") or ""
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts < since:
                    continue
            except (ValueError, TypeError):
                continue
        snaps.append(entry)
    return snaps


def load_accounts_config() -> dict:
    path = REPO / "config" / "accounts.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def load_settings() -> dict:
    path = REPO / "config" / "settings.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


# ─── Check functions ───────────────────────────────────────────────

def check_dd_levels(alerts: list[Alert]):
    """Check DD vs account thresholds from latest equity snapshot."""
    snaps = load_equity_snapshots(since=datetime.now(timezone.utc) - timedelta(hours=6))
    if not snaps:
        alerts.append(Alert(Severity.WARN, "DD", "Aucun equity snapshot dans les 6 dernières heures"))
        return

    latest = snaps[-1]
    daily_dd = latest.get("daily_dd_pct", 0)
    total_dd = latest.get("total_dd_pct", 0)
    level = latest.get("protection_level", "normal")

    accounts = load_accounts_config().get("accounts", {})

    # Check each account's thresholds
    for acc_name, acc in accounts.items():
        max_daily = acc.get("max_daily_dd_pct", 3.0)
        max_total = acc.get("max_total_dd_pct", 8.0)
        prop_daily = {"ftmo": 5.0, "gft": 4.0}.get(acc.get("prop_firm", ""), 5.0)
        prop_total = 10.0

        # Daily DD alerts (using internal thresholds)
        if abs(daily_dd) >= max_daily * 0.8:
            sev = Severity.CRITICAL if abs(daily_dd) >= max_daily else Severity.WARN
            alerts.append(Alert(sev, "DD",
                f"Daily DD {daily_dd:.1f}% — seuil interne {max_daily}%, "
                f"limite prop {prop_daily}%"))

        # Total DD alerts
        if abs(total_dd) >= max_total * 0.8:
            sev = Severity.CRITICAL if abs(total_dd) >= max_total else Severity.WARN
            alerts.append(Alert(sev, "DD",
                f"Total DD {total_dd:.1f}% — seuil interne {max_total}%, "
                f"limite prop {prop_total}%"))

    # Protection level
    if level not in ("normal", ""):
        sev = Severity.CRITICAL if level in ("danger", "emergency") else Severity.WARN
        alerts.append(Alert(sev, "DD", f"Protection active: {level.upper()}"))


def check_sizing_anomalies(alerts: list[Alert]):
    """Check for aberrant lot sizes in recent trades."""
    entries = load_journal_entries(since=datetime.now(timezone.utc) - timedelta(days=7))
    entry_trades = [e for e in entries if e.get("event") == "entry"]

    if not entry_trades:
        return

    settings = load_settings()
    base_risk_pct = settings.get("general", {}).get("risk_percent", 0.45)

    for trade in entry_trades:
        risk_cash = trade.get("risk_cash", 0)
        volume = trade.get("volume", 0)
        instrument = trade.get("instrument", "?")
        strategy = trade.get("strategy", "?")

        # Check: risk_cash should be reasonable (0.1% to 2% of ~100k = $100-$2000)
        if risk_cash > 0:
            if risk_cash > 3000:
                alerts.append(Alert(Severity.CRITICAL, "Sizing",
                    f"{instrument} ({strategy}): risk_cash=${risk_cash:.0f} anormalement élevé"))
            elif risk_cash < 10:
                alerts.append(Alert(Severity.WARN, "Sizing",
                    f"{instrument} ({strategy}): risk_cash=${risk_cash:.0f} anormalement bas"))

        # Check: volume should be > 0
        if volume <= 0:
            alerts.append(Alert(Severity.CRITICAL, "Sizing",
                f"{instrument} ({strategy}): volume={volume} — nul ou négatif"))


def check_orphan_positions(alerts: list[Alert]):
    """Check for positions without SL/TP in recent entries."""
    entries = load_journal_entries(since=datetime.now(timezone.utc) - timedelta(days=3))
    entry_trades = [e for e in entries if e.get("event") == "entry"]

    for trade in entry_trades:
        instrument = trade.get("instrument", "?")
        sl = trade.get("sl")
        tp = trade.get("tp")

        if sl is None or sl == 0:
            alerts.append(Alert(Severity.CRITICAL, "Orphan",
                f"{instrument}: position sans SL — risque illimité"))
        if tp is None or tp == 0:
            alerts.append(Alert(Severity.WARN, "Orphan",
                f"{instrument}: position sans TP"))


def _normalize_strategy(name: str) -> str:
    """Map journal strategy names to canonical names."""
    return {"trend": "extension"}.get(name, name)


def check_losing_streak(alerts: list[Alert]):
    """Check for consecutive losses in recent trades."""
    entries = load_journal_entries()
    exits = _dedup_exits(entries)

    if not exits:
        return

    # Sort by timestamp
    exits.sort(key=lambda e: e.get("ts") or e.get("timestamp") or "")

    # Count consecutive losses at the tail
    streak = 0
    for trade in reversed(exits):
        r = trade.get("result_r", 0)
        if r < -0.1:  # Real loss (not just BE slippage)
            streak += 1
        else:
            break

    if streak >= 8:
        alerts.append(Alert(Severity.CRITICAL, "Streak",
            f"{streak} pertes consécutives — vérifier la stratégie"))
    elif streak >= 5:
        alerts.append(Alert(Severity.WARN, "Streak",
            f"{streak} pertes consécutives — surveiller"))

    # Also check per-strategy streaks
    by_strategy = defaultdict(list)
    for trade in exits:
        strat = _normalize_strategy(trade.get("strategy", "unknown"))
        by_strategy[strat].append(trade.get("result_r", 0))

    for strat, results in by_strategy.items():
        strat_streak = 0
        for r in reversed(results):
            if r < -0.1:
                strat_streak += 1
            else:
                break
        if strat_streak >= 5:
            alerts.append(Alert(Severity.WARN, "Streak",
                f"{strat}: {strat_streak} pertes consécutives"))


def check_correlation(alerts: list[Alert]):
    """Check if too many positions are open in the same category."""
    entries = load_journal_entries(since=datetime.now(timezone.utc) - timedelta(days=1))
    # Reconstruct currently open positions from entry/exit events
    open_positions = {}
    for e in entries:
        trade_id = e.get("trade_id", "")
        if e.get("event") == "entry":
            open_positions[trade_id] = e
        elif e.get("event") == "exit" and trade_id in open_positions:
            del open_positions[trade_id]

    if len(open_positions) < 2:
        return

    from arabesque.data.store import _categorize

    by_category = defaultdict(list)
    for tid, pos in open_positions.items():
        cat = _categorize(pos.get("instrument", ""))
        by_category[cat].append(pos.get("instrument", "?"))

    for cat, instruments in by_category.items():
        if len(instruments) >= 4:
            alerts.append(Alert(Severity.WARN, "Correlation",
                f"{len(instruments)} positions {cat} ouvertes simultanément: "
                f"{', '.join(instruments[:5])}"))


def check_rodage_performance(alerts: list[Alert]):
    """Check performance of strategies in rodage period."""
    settings = load_settings()
    rodage_cfg = settings.get("rodage", {})
    if not rodage_cfg.get("enabled", False):
        return

    rodage_strategies = rodage_cfg.get("strategies", [])
    if not rodage_strategies:
        return

    entries = load_journal_entries()
    exits = _dedup_exits(entries)

    for strat in rodage_strategies:
        strat_trades = [e for e in exits if _normalize_strategy(e.get("strategy", "")) == strat]
        n = len(strat_trades)
        if n == 0:
            alerts.append(Alert(Severity.INFO, "Rodage",
                f"{strat}: 0 trades — pas encore de données"))
            continue

        results = [t.get("result_r", 0) for t in strat_trades]
        wr = sum(1 for r in results if r > 0) / n * 100
        exp = sum(results) / n
        total_r = sum(results)

        alerts.append(Alert(Severity.INFO, "Rodage",
            f"{strat}: {n}t WR={wr:.0f}% Exp={exp:+.3f}R ΣR={total_r:+.1f}R"
            f"{' (< 30 trades, bruit)' if n < 30 else ''}"))

        # Alert if performing badly
        if n >= 10 and exp < -0.15:
            alerts.append(Alert(Severity.WARN, "Rodage",
                f"{strat}: Exp={exp:+.3f}R sur {n} trades — performance faible"))
        if n >= 20 and wr < 50:
            alerts.append(Alert(Severity.WARN, "Rodage",
                f"{strat}: WR={wr:.0f}% sur {n} trades — WR sous 50%"))


def check_stale_data(alerts: list[Alert]):
    """Check if parquet data is stale (fetch not running)."""
    data_dirs = [
        REPO / "barres_au_sol" / "dukascopy" / "min1",
        REPO / "barres_au_sol" / "ccxt" / "min1",
    ]

    for d in data_dirs:
        if not d.exists():
            continue
        # Find the most recently modified parquet
        parquets = sorted(d.glob("*.parquet"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not parquets:
            continue
        newest = parquets[0]
        age_hours = (datetime.now().timestamp() - newest.stat().st_mtime) / 3600
        provider = d.parent.name
        if age_hours > 48:
            alerts.append(Alert(Severity.WARN, "Data",
                f"Parquets {provider} pas mis à jour depuis {age_hours:.0f}h "
                f"(dernier: {newest.name})"))


def check_slippage_rejects(alerts: list[Alert]):
    """Check accumulated slippage rejects."""
    if not SLIPPAGE_REJECTS.exists():
        return

    now = datetime.now(timezone.utc)
    recent = []
    for line in SLIPPAGE_REJECTS.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_str = entry.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts >= now - timedelta(days=7):
                recent.append(entry)
        except (ValueError, TypeError):
            continue

    if len(recent) >= 10:
        by_inst = defaultdict(int)
        for r in recent:
            by_inst[r.get("instrument", "?")] += 1
        top = sorted(by_inst.items(), key=lambda x: -x[1])[:3]
        top_str = ", ".join(f"{inst}({n})" for inst, n in top)
        alerts.append(Alert(Severity.WARN, "Slippage",
            f"{len(recent)} signaux rejetés (slippage) en 7j — top: {top_str}"))
    elif len(recent) > 0:
        alerts.append(Alert(Severity.INFO, "Slippage",
            f"{len(recent)} signaux rejetés (slippage) en 7j"))


def check_engine_running(alerts: list[Alert]):
    """Check if the live engine systemd service is running."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "arabesque-live"],
            capture_output=True, text=True, timeout=5,
        )
        status = result.stdout.strip()
        if status != "active":
            alerts.append(Alert(Severity.CRITICAL, "Engine",
                f"Service arabesque-live: {status} — moteur non actif"))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        alerts.append(Alert(Severity.WARN, "Engine",
            "Impossible de vérifier le service systemd"))


def check_engine_processing_bars(alerts: list[Alert]):
    """Check if BarAggregator is actually processing bars (not just running).

    The engine can be 'active' per systemd but blind if BarAggregators
    failed to preload (e.g. broker disconnected at startup). Detect this
    by checking journalctl for recent BarAggregator output.
    """
    try:
        result = subprocess.run(
            ["journalctl", "--user", "-u", "arabesque-live",
             "--since", "2 hours ago", "--no-pager", "-q"],
            capture_output=True, text=True, timeout=10,
        )
        logs = result.stdout

        has_bars = "BarAggregator" in logs and "barre(s) fermée(s)" in logs
        has_preload_fail = "Pas de broker pour le préchargement" in logs

        if has_preload_fail:
            alerts.append(Alert(Severity.CRITICAL, "Engine Blind",
                "BarAggregator sans preload historique — moteur aveugle, "
                "aucun signal ne sera généré. Redémarrer le moteur."))
        elif not has_bars:
            # Check if engine just started (< 5 min ago)
            if "Moteur prêt" in logs:
                return  # Just started, give it time
            alerts.append(Alert(Severity.WARN, "Engine Blind",
                "Aucune barre traitée par BarAggregator depuis 2h — "
                "le moteur est peut-être aveugle"))

    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Can't check, don't alert


def check_winrate_decay(alerts: list[Alert]):
    """Compare recent WR vs historical WR to detect decay."""
    entries = load_journal_entries()
    exits = _dedup_exits(entries)

    if len(exits) < 50:
        return

    # Sort by timestamp
    exits.sort(key=lambda e: e.get("ts") or e.get("timestamp") or "")

    # Compare last 30 trades vs all previous
    recent = exits[-30:]
    historical = exits[:-30]

    if len(historical) < 20:
        return

    wr_recent = sum(1 for t in recent if t.get("result_r", 0) > 0) / len(recent) * 100
    wr_hist = sum(1 for t in historical if t.get("result_r", 0) > 0) / len(historical) * 100

    delta_wr = wr_recent - wr_hist
    if delta_wr < -15:
        alerts.append(Alert(Severity.WARN, "WR Decay",
            f"WR récent {wr_recent:.0f}% vs historique {wr_hist:.0f}% "
            f"(Δ={delta_wr:+.0f}pp sur {len(recent)} trades)"))


def check_best_day_guard(alerts: list[Alert]):
    """Check if any single day's P&L exceeds best-day thresholds.

    FTMO consistency rule: no single day should represent > 40-50% of total profit.
    Alert early if a day is becoming too dominant.
    """
    entries = load_journal_entries()
    exits = _dedup_exits(entries)

    if len(exits) < 10:
        return

    daily_pnl = defaultdict(float)
    for trade in exits:
        ts_str = trade.get("ts") or trade.get("timestamp") or ""
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            day = ts.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        daily_pnl[day] += trade.get("result_r", 0)

    if not daily_pnl:
        return

    total_r = sum(daily_pnl.values())
    if total_r <= 0:
        return

    best_day_r = max(daily_pnl.values())
    best_day = max(daily_pnl, key=daily_pnl.get)
    ratio = best_day_r / total_r * 100

    if ratio > 40:
        alerts.append(Alert(Severity.WARN, "Best Day",
            f"Jour {best_day}: {best_day_r:+.1f}R = {ratio:.0f}% du profit total "
            f"— risque consistance FTMO (seuil ~50%)"))


def check_no_activity(alerts: list[Alert]):
    """Alert if no trades for an extended period (engine might be stuck)."""
    entries = load_journal_entries()
    if not entries:
        alerts.append(Alert(Severity.WARN, "Activity",
            "Aucun trade dans le journal"))
        return

    # Find latest entry or exit
    latest_ts = None
    for e in reversed(entries):
        ts_str = e.get("ts") or e.get("timestamp") or ""
        try:
            latest_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            break
        except (ValueError, TypeError):
            continue

    if latest_ts is None:
        return

    hours_since = (datetime.now(timezone.utc) - latest_ts).total_seconds() / 3600

    # Weekend check (Fri 22h UTC → Sun 22h UTC = forex closed, crypto still trades)
    now = datetime.now(timezone.utc)
    is_weekend = now.weekday() >= 5 or (now.weekday() == 4 and now.hour >= 22)

    if is_weekend:
        # Crypto should still trade on weekends
        threshold = 72  # 3 days
    else:
        threshold = 48  # 2 days weekday

    if hours_since > threshold:
        alerts.append(Alert(Severity.WARN, "Activity",
            f"Dernier trade il y a {hours_since:.0f}h — moteur actif ?"))


def check_equity_trajectory(alerts: list[Alert]):
    """Check if equity is on a declining trajectory over recent days."""
    snaps = load_equity_snapshots(since=datetime.now(timezone.utc) - timedelta(days=7))
    if len(snaps) < 10:
        return

    # Sample daily: take last snapshot of each day
    by_day = {}
    for s in snaps:
        ts_str = s.get("ts") or s.get("timestamp") or ""
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            day = ts.strftime("%Y-%m-%d")
            by_day[day] = s.get("balance") or s.get("bal", 0)
        except (ValueError, TypeError):
            continue

    if len(by_day) < 3:
        return

    days = sorted(by_day.keys())
    balances = [by_day[d] for d in days]

    # Check if balance has declined every day for 3+ days
    declining_days = 0
    for i in range(1, len(balances)):
        if balances[i] < balances[i - 1]:
            declining_days += 1
        else:
            declining_days = 0

    if declining_days >= 5:
        total_loss = balances[-1] - balances[-declining_days - 1]
        alerts.append(Alert(Severity.WARN, "Trajectory",
            f"Balance en baisse depuis {declining_days} jours consécutifs "
            f"({total_loss:+.0f}$)"))


def check_cross_broker_consistency(alerts: list[Alert]):
    """Compare trade outcomes between brokers for the same signal.

    If a signal is sent to multiple brokers, results should be similar.
    Large divergences indicate sizing/execution issues.
    """
    entries = load_journal_entries(since=datetime.now(timezone.utc) - timedelta(days=14))
    exits = [e for e in entries if e.get("event") == "exit"]

    if not exits:
        return

    # Group exits by approximate timestamp + instrument + side (= same signal)
    from itertools import groupby

    # Build trade pairs: same instrument, same strategy, close timestamps
    by_signal = defaultdict(list)
    for trade in exits:
        inst = trade.get("instrument", "")
        strat = _normalize_strategy(trade.get("strategy", ""))
        side = trade.get("side", "")
        ts_str = trade.get("ts") or trade.get("timestamp") or ""
        broker = trade.get("broker_id", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            # Round to 10-minute window for matching
            window = ts.strftime("%Y-%m-%d %H:") + str(ts.minute // 10)
        except (ValueError, TypeError):
            continue
        key = f"{inst}|{strat}|{side}|{window}"
        by_signal[key].append(trade)

    # Find pairs with divergent outcomes
    for key, trades in by_signal.items():
        brokers = {t.get("broker_id", ""): t for t in trades}
        if len(brokers) < 2:
            continue

        results = {bid: t.get("result_r", 0) for bid, t in brokers.items()}
        inst = trades[0].get("instrument", "?")

        # Check result divergence
        r_values = list(results.values())
        max_diff = max(r_values) - min(r_values)
        if max_diff > 0.5:
            details = ", ".join(f"{bid}={r:+.2f}R" for bid, r in results.items())
            sev = Severity.CRITICAL if max_diff > 1.0 else Severity.WARN
            alerts.append(Alert(sev, "Cross-Broker",
                f"{inst}: résultats divergents ({details}, Δ={max_diff:.1f}R)"))

    # Check lot ratio consistency for entries
    entry_trades = [e for e in entries if e.get("event") == "entry"]
    by_entry_signal = defaultdict(list)
    for trade in entry_trades:
        inst = trade.get("instrument", "")
        side = trade.get("side", "")
        ts_str = trade.get("ts") or trade.get("timestamp") or ""
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            window = ts.strftime("%Y-%m-%d %H:") + str(ts.minute // 10)
        except (ValueError, TypeError):
            continue
        key = f"{inst}|{side}|{window}"
        by_entry_signal[key].append(trade)

    for key, trades in by_entry_signal.items():
        brokers = {t.get("broker_id", ""): t for t in trades}
        if len(brokers) < 2:
            continue

        volumes = {bid: t.get("volume", 0) for bid, t in brokers.items()}
        risk_cashes = {bid: t.get("risk_cash", 0) for bid, t in brokers.items()}
        inst = trades[0].get("instrument", "?")

        # Compare risk_cash proportional to account size
        # FTMO 100k, GFT 150k → risk_cash ratio should be ~1.5
        # Note: expected_risk is the NOMINAL value before DD reduction + protection.
        # With DD -5.5% (linear ×0.29) + CAUTION (×0.50), actual can be ~0.15× nominal.
        # Use a generous floor to avoid false positives from DD/protection reductions.
        accounts = load_accounts_config().get("accounts", {})
        ratios = {}
        for bid in brokers:
            acc = accounts.get(bid, {})
            initial = acc.get("initial_balance", 100000)
            risk_pct = acc.get("risk_per_trade_pct", 0.45)
            expected_risk = initial * risk_pct / 100
            actual_risk = risk_cashes.get(bid, 0)
            if expected_risk > 0 and actual_risk > 0:
                ratios[bid] = actual_risk / expected_risk

        # Alert thresholds account for DD reduction (min ×0.10) + protection (×0.10)
        # → legitimate minimum is ~0.01× nominal, so alert only at extremes
        for bid, ratio in ratios.items():
            if ratio > 3.0:
                alerts.append(Alert(Severity.CRITICAL, "Cross-Broker",
                    f"{inst} {bid}: risk_cash {ratio:.1f}× l'attendu — sizing aberrant"))
            elif ratio < 0.05:
                alerts.append(Alert(Severity.WARN, "Cross-Broker",
                    f"{inst} {bid}: risk_cash {ratio:.2f}× l'attendu — trop petit"))


def check_broker_dd_levels(alerts: list[Alert]):
    """Check DD levels per broker from equity snapshots."""
    snaps = load_equity_snapshots(since=datetime.now(timezone.utc) - timedelta(hours=6))
    if not snaps:
        return

    accounts = load_accounts_config().get("accounts", {})

    # Group by broker_id
    by_broker = defaultdict(list)
    for s in snaps:
        bid = s.get("broker_id", "")
        by_broker[bid].append(s)

    for bid, broker_snaps in by_broker.items():
        if not bid:
            continue  # Primary broker already checked in check_dd_levels
        latest = broker_snaps[-1]
        total_dd = latest.get("total_dd_pct", 0)
        balance = latest.get("balance", 0)

        acc = accounts.get(bid, {})
        max_daily = acc.get("max_daily_dd_pct", 3.0)
        max_total = acc.get("max_total_dd_pct", 8.0)
        prop_firm = acc.get("prop_firm", "")
        label = acc.get("label", bid)

        if abs(total_dd) >= max_total * 0.8:
            sev = Severity.CRITICAL if abs(total_dd) >= max_total else Severity.WARN
            alerts.append(Alert(sev, f"DD {bid}",
                f"{label}: Total DD {total_dd:.1f}% (seuil interne {max_total}%)"))

        # Report balance for secondary brokers
        if balance > 0:
            alerts.append(Alert(Severity.INFO, f"Balance {bid}",
                f"{label}: ${balance:,.0f} (DD {total_dd:.1f}%)"))


def check_broker_missing_trades(alerts: list[Alert]):
    """Check if one broker received a signal but the other didn't."""
    entries = load_journal_entries(since=datetime.now(timezone.utc) - timedelta(days=7))
    entry_trades = [e for e in entries if e.get("event") == "entry"]

    if not entry_trades:
        return

    # Group entries by approximate signal time + instrument
    by_signal = defaultdict(set)
    by_signal_detail = defaultdict(dict)
    for trade in entry_trades:
        inst = trade.get("instrument", "")
        ts_str = trade.get("ts") or trade.get("timestamp") or ""
        broker = trade.get("broker_id", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            window = ts.strftime("%Y-%m-%d %H:") + str(ts.minute // 10)
        except (ValueError, TypeError):
            continue
        key = f"{inst}|{window}"
        by_signal[key].add(broker)
        by_signal_detail[key][broker] = trade

    # Get all active brokers from config
    settings = load_settings()
    brokers_cfg = settings.get("brokers", {})
    active_brokers = {k for k, v in brokers_cfg.items() if v.get("enabled", False)}

    if len(active_brokers) < 2:
        return

    # Find instruments that have been traded on BOTH brokers historically
    # Only flag misses for instruments that both brokers are known to handle
    broker_instruments = defaultdict(set)
    for trade in entry_trades:
        broker_instruments[trade.get("broker_id", "")].add(trade.get("instrument", ""))

    # Count missed trades per broker — only for shared instruments
    missed = defaultdict(list)
    for key, brokers_present in by_signal.items():
        inst = key.split("|")[0]
        for ab in active_brokers:
            if ab not in brokers_present and len(brokers_present) > 0:
                # Only flag if this broker has traded this instrument before
                if inst not in broker_instruments.get(ab, set()):
                    continue
                missed[ab].append(inst)

    for broker, instruments in missed.items():
        if len(instruments) >= 3:
            alerts.append(Alert(Severity.WARN, "Missing Trades",
                f"{broker}: {len(instruments)} trades manqués pour instruments partagés en 7j "
                f"({', '.join(instruments[:5])})"))
        elif len(instruments) >= 1:
            alerts.append(Alert(Severity.INFO, "Missing Trades",
                f"{broker}: {len(instruments)} trade(s) manqué(s) — {', '.join(instruments)}"))


# ─── Main ──────────────────────────────────────────────────────────

def run_all_checks() -> list[Alert]:
    alerts = []

    check_engine_running(alerts)
    check_engine_processing_bars(alerts)
    check_dd_levels(alerts)
    check_broker_dd_levels(alerts)
    check_sizing_anomalies(alerts)
    check_orphan_positions(alerts)
    check_losing_streak(alerts)
    check_correlation(alerts)
    check_rodage_performance(alerts)
    check_stale_data(alerts)
    check_slippage_rejects(alerts)
    check_winrate_decay(alerts)
    check_best_day_guard(alerts)
    check_no_activity(alerts)
    check_equity_trajectory(alerts)
    check_cross_broker_consistency(alerts)
    check_broker_missing_trades(alerts)

    return alerts


def format_report(alerts: list[Alert]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"🏥 HEALTH CHECK — {now}", ""]

    crits = [a for a in alerts if a.severity == Severity.CRITICAL]
    warns = [a for a in alerts if a.severity == Severity.WARN]
    infos = [a for a in alerts if a.severity == Severity.INFO]

    if crits:
        lines.append(f"🚨 {len(crits)} CRITIQUE(S):")
        for a in crits:
            lines.append(f"  {a}")
        lines.append("")

    if warns:
        lines.append(f"⚠️ {len(warns)} AVERTISSEMENT(S):")
        for a in warns:
            lines.append(f"  {a}")
        lines.append("")

    if infos:
        lines.append(f"ℹ️ {len(infos)} INFO(S):")
        for a in infos:
            lines.append(f"  {a}")
        lines.append("")

    if not crits and not warns:
        lines.append("✅ Tous les checks OK")

    return "\n".join(lines)


async def send_notification(report: str) -> None:
    try:
        import apprise
    except ImportError:
        print("apprise non installé — notification non envoyée")
        return

    secrets_path = REPO / "config" / "secrets.yaml"
    if not secrets_path.exists():
        print("secrets.yaml non trouvé")
        return

    secrets = yaml.safe_load(secrets_path.read_text()) or {}
    channels = secrets.get("notifications", {}).get("channels", [])
    if not channels:
        print("Aucun canal de notification")
        return

    ap = apprise.Apprise()
    for ch in channels:
        if isinstance(ch, str):
            ap.add(ch)

    ok = await ap.async_notify(body=report, title="Arabesque Health")
    print(f"Notification: {'✅' if ok else '❌'}")


def main():
    parser = argparse.ArgumentParser(description="Arabesque — Health Check")
    parser.add_argument("--notify", action="store_true",
                        help="Envoyer les alertes via notifications")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview sans envoi")
    parser.add_argument("--warn-only", action="store_true",
                        help="N'envoyer que si WARN ou CRIT présents")
    args = parser.parse_args()

    alerts = run_all_checks()
    report = format_report(alerts)
    print(report)

    has_issues = any(a.severity in (Severity.WARN, Severity.CRITICAL) for a in alerts)

    if args.notify and not args.dry_run:
        if args.warn_only and not has_issues:
            print("\n(Pas d'alerte WARN/CRIT — notification non envoyée)")
        else:
            asyncio.run(send_notification(report))

    # Exit code: 2 if critical, 1 if warning, 0 if clean
    if any(a.severity == Severity.CRITICAL for a in alerts):
        sys.exit(2)
    elif has_issues:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
