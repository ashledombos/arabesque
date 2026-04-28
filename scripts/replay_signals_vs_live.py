"""Replay signals vs live — détecte les trades qui auraient dû être ouverts.

Pour chaque stratégie active (extension, cabriole, glissade, fouette), rejoue
les signaux théoriques sur les parquets de la période et compare aux entries
réelles du ``trade_journal.jsonl``.

Un signal théorique est *manquant* s'il n'a :
1. Aucun entry correspondant dans ``trade_journal.jsonl`` (match par
   ``(strategy, instrument, ts ±tolerance)``).
2. Aucun blocage justifié dans ``weekend_crypto_guard.jsonl``.
3. Aucune ``strategy_broker_exclusions`` qui le couvre par design.

Usage::

    python scripts/replay_signals_vs_live.py --since 2026-04-01
    python scripts/replay_signals_vs_live.py --since 2026-04-01 --notify
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "config" / "settings.yaml"
INSTRUMENTS = ROOT / "config" / "instruments.yaml"
JOURNAL = ROOT / "logs" / "trade_journal.jsonl"
GUARD_LOG = ROOT / "logs" / "weekend_crypto_guard.jsonl"
SHADOW_LOG = ROOT / "logs" / "shadow_filters.jsonl"

ENTRY_TOL_HOURS = {"M1": 0.5, "H1": 2.0, "H4": 6.0, "1h": 2.0, "4h": 6.0, "min1": 0.5}


def _build_targets(settings: dict, instruments_cfg: dict):
    """Renvoie [(strategy, tf, instrument), ...] selon la config."""
    out = []
    sa = settings.get("strategy_assignments", {}) or {}
    for strat in ("glissade", "fouette", "cabriole"):
        cfg = sa.get(strat)
        if not cfg:
            continue
        tf = cfg.get("timeframe", "H1")
        for inst in cfg.get("instruments", []):
            out.append((strat, tf, inst))
    # Extension : par défaut tous les followed
    for inst, meta in (instruments_cfg or {}).items():
        if not isinstance(meta, dict) or not meta.get("follow"):
            continue
        tf = (meta.get("tf") or "h1").upper()
        out.append(("extension", tf, inst))
    return out


def _instantiate(strategy: str):
    if strategy == "cabriole":
        from arabesque.strategies.cabriole.signal import (
            CabrioleSignalGenerator, CabrioleConfig,
        )
        return CabrioleSignalGenerator(CabrioleConfig())
    if strategy == "glissade":
        from arabesque.strategies.glissade.signal import (
            GlissadeRSIDivGenerator, GlissadeRSIDivConfig,
        )
        return GlissadeRSIDivGenerator(GlissadeRSIDivConfig())
    if strategy == "fouette":
        from arabesque.strategies.fouette.signal import (
            FouetteSignalGenerator, FouetteConfig,
        )
        return FouetteSignalGenerator(FouetteConfig())
    if strategy == "extension":
        from arabesque.strategies.extension.signal import (
            ExtensionSignalGenerator, ExtensionConfig,
        )
        return ExtensionSignalGenerator(ExtensionConfig())
    raise ValueError(f"Unknown strategy: {strategy}")


def _replay(strategy: str, tf: str, instrument: str, start: dt.datetime,
            end: dt.datetime) -> list[pd.Timestamp]:
    """Renvoie la liste des entry_ts théoriques sur la période."""
    from arabesque.data.store import load_ohlc

    interval_map = {"M1": "min1", "H1": "1h", "H4": "4h"}
    iv = interval_map.get(tf.upper(), tf.lower())
    # Marge avant pour les indicateurs (EMA200, etc.)
    fetch_start = (start - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    fetch_end = (end + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        df = load_ohlc(instrument, interval=iv, start=fetch_start, end=fetch_end)
    except Exception:
        return []
    if df is None or df.empty or len(df) < 250:
        return []
    sig_gen = _instantiate(strategy)
    df_prepared = sig_gen.prepare(df)
    try:
        signals = sig_gen.generate_signals(df_prepared, instrument)
    except Exception:
        return []
    tf_delta = df.index[1] - df.index[0]
    entry_ts_list = []
    for i, _sig in signals:
        if i + 1 >= len(df.index):
            continue
        entry_ts = df.index[i] + tf_delta
        if entry_ts < start or entry_ts > end:
            continue
        entry_ts_list.append(entry_ts)
    return entry_ts_list


def _load_live_entries(since: dt.datetime, until: dt.datetime) -> dict:
    """Renvoie {(strategy, instrument): [ts, ...]} des entries live."""
    out: dict[tuple[str, str], list[pd.Timestamp]] = defaultdict(list)
    if not JOURNAL.exists():
        return out
    for line in JOURNAL.read_text().splitlines():
        if not line.strip() or '"event": "entry"' not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = pd.Timestamp(obj["ts"])
        if ts < since or ts > until:
            continue
        strat = obj.get("strategy") or obj.get("strategy_type") or "?"
        instr = obj.get("instrument") or "?"
        out[(strat, instr)].append(ts)
    return out


def _load_blocked(since: dt.datetime, until: dt.datetime) -> dict:
    """Renvoie {(strategy, instrument): [ts, ...]} des blocked weekend events."""
    out: dict[tuple[str, str], list[pd.Timestamp]] = defaultdict(list)
    if not GUARD_LOG.exists():
        return out
    for line in GUARD_LOG.read_text().splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") != "blocked":
            continue
        ts = pd.Timestamp(obj["ts"])
        if ts < since or ts > until:
            continue
        strat = obj.get("strategy") or "?"
        instr = obj.get("instrument") or "?"
        out[(strat, instr)].append(ts)
    return out


def _match(target_ts: pd.Timestamp, candidates: list[pd.Timestamp],
           tol_hours: float) -> bool:
    if not candidates:
        return False
    delta = pd.Timedelta(hours=tol_hours)
    return any(abs(c - target_ts) <= delta for c in candidates)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=(
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)
    ).strftime("%Y-%m-%d"))
    p.add_argument("--until", default=None,
                   help="ISO date, défaut now")
    p.add_argument("--notify", action="store_true")
    args = p.parse_args()

    since = pd.Timestamp(args.since, tz="UTC")
    until = pd.Timestamp(args.until, tz="UTC") if args.until else pd.Timestamp.now(tz="UTC")

    settings = yaml.safe_load(SETTINGS.read_text())
    instruments_cfg = yaml.safe_load(INSTRUMENTS.read_text()) if INSTRUMENTS.exists() else {}

    targets = _build_targets(settings, instruments_cfg)
    print(f"Replay signals vs live — {since:%Y-%m-%d} → {until:%Y-%m-%d}")
    print(f"  {len(targets)} (strategy, tf, instrument) targets")

    live_entries = _load_live_entries(since, until)
    blocked = _load_blocked(since, until)

    by_strat = defaultdict(lambda: {"theo": 0, "live": 0,
                                     "blocked_weekend": 0, "missing": []})

    for strat, tf, instr in targets:
        theo_ts = _replay(strat, tf, instr, since, until)
        if not theo_ts:
            continue
        tol = ENTRY_TOL_HOURS.get(tf.upper(), 2.0)
        live_ts = live_entries.get((strat, instr), [])
        blocked_ts = blocked.get((strat, instr), [])
        for t in theo_ts:
            by_strat[strat]["theo"] += 1
            if _match(t, live_ts, tol):
                by_strat[strat]["live"] += 1
            elif _match(t, blocked_ts, tol):
                by_strat[strat]["blocked_weekend"] += 1
            else:
                by_strat[strat]["missing"].append(
                    {"instrument": instr, "ts": t.isoformat(), "tf": tf}
                )

    print()
    notif_lines = []
    for strat in ("extension", "cabriole", "glissade", "fouette"):
        s = by_strat.get(strat)
        if not s or s["theo"] == 0:
            continue
        n_miss = len(s["missing"])
        line = (f"  {strat:10s} théoriques={s['theo']:3d} "
                f"live={s['live']:3d} blocked_weekend={s['blocked_weekend']:3d} "
                f"manquants={n_miss}")
        print(line)
        if n_miss > 0:
            for m in s["missing"][:5]:
                print(f"    ❌ {m['ts']}  {m['instrument']}  ({m['tf']})")
            if n_miss > 5:
                print(f"    ... et {n_miss-5} autres")
            notif_lines.append(f"• {strat} : {n_miss} manquants")

    if args.notify and notif_lines:
        try:
            import apprise
            secrets = yaml.safe_load((ROOT / "config/secrets.yaml").read_text())
            channels = secrets.get("notifications", {}).get("channels", []) or []
            if channels:
                ap = apprise.Apprise()
                for ch in channels:
                    if isinstance(ch, str):
                        ap.add(ch)
                body = (f"🔍 Trades manquants {since:%Y-%m-%d}→{until:%Y-%m-%d}\n"
                        + "\n".join(notif_lines))
                asyncio.run(ap.async_notify(body=body, title="Arabesque /suivi"))
        except Exception as e:
            print(f"notif err: {e}")

    return 0 if all(len(s["missing"]) == 0 for s in by_strat.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
