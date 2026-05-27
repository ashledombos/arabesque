"""Replay signals vs live — détecte les trades qui auraient dû être ouverts.

Pour chaque stratégie active (extension, cabriole, glissade, fouette), rejoue
les signaux théoriques sur les parquets de la période et compare aux entries
réelles du ``trade_journal.jsonl``.

Un signal théorique est *manquant* s'il n'a :
1. Aucun entry correspondant dans ``trade_journal.jsonl`` (match par
   ``(strategy, instrument, ts ±tolerance)``).
2. Aucun blocage justifié dans ``weekend_crypto_guard.jsonl``.
3. Aucune ``strategy_broker_exclusions`` qui le couvre par design.

Les signaux consécutifs sur le même instrument (séparés de ≤ 1 barre) sont
regroupés en une seule "session de signal" — seul le premier représentant
est conservé. Évite les faux positifs pour les stratégies comme Extension
dont la condition peut rester vraie plusieurs barres de suite (BB squeeze).

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
sys.path.insert(0, str(ROOT))

from arabesque.notifications import select_notification_channels

SETTINGS = ROOT / "config" / "settings.yaml"
INSTRUMENTS = ROOT / "config" / "instruments.yaml"
ACCOUNTS = ROOT / "config" / "accounts.yaml"
JOURNAL = ROOT / "logs" / "trade_journal.jsonl"
GUARD_LOG = ROOT / "logs" / "weekend_crypto_guard.jsonl"
BROKER_REJECT_LOG = ROOT / "logs" / "broker_guard_rejects.jsonl"
SHADOW_LOG = ROOT / "logs" / "shadow_filters.jsonl"

ENTRY_TOL_HOURS = {"M1": 0.5, "H1": 2.0, "H4": 6.0, "1h": 2.0, "4h": 6.0, "min1": 0.5}


def _load_active_brokers() -> dict[str, str]:
    """Renvoie {broker_id: broker_type} pour les comptes non-protégés."""
    if not ACCOUNTS.exists():
        return {}
    cfg = yaml.safe_load(ACCOUNTS.read_text()) or {}
    out: dict[str, str] = {}
    for bid, meta in (cfg.get("accounts") or {}).items():
        if not isinstance(meta, dict):
            continue
        if meta.get("protected"):
            continue
        out[bid] = meta.get("type", "?")
    return out


def _expected_brokers(strategy: str, active: dict[str, str],
                      exclusions: dict[str, list[str]]) -> list[str]:
    """Brokers où la stratégie est censée tirer (hors exclusions hard-codées)."""
    excl = set((exclusions or {}).get(strategy, []) or [])
    return [b for b in active if b not in excl]


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
    # IMPORTANT : le live lit `timeframe` (cf. arabesque/execution/live.py),
    # `tf` reste accepté comme alias legacy. Sans cette unification, Extension
    # crypto (timeframe: H4) était rejouée en H1 — diagnostics faussés.
    for inst, meta in (instruments_cfg or {}).items():
        if not isinstance(meta, dict) or not meta.get("follow"):
            continue
        tf = (meta.get("timeframe") or meta.get("tf") or "h1").upper()
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


# Strict-data mode : par défaut, on refuse les fallbacks Yahoo (source ≠ parquet),
# car Yahoo peut diverger de la source de validation locale et fausser le diag.
# `--allow-yahoo` lève la garde. La liste globale collecte les skip pour le rapport.
_ALLOW_YAHOO: bool = False
_SKIPPED_NO_PARQUET: list[tuple[str, str, str]] = []  # (strategy, tf, instrument)


def _replay(strategy: str, tf: str, instrument: str, start: dt.datetime,
            end: dt.datetime) -> list[pd.Timestamp]:
    """Renvoie la liste des entry_ts théoriques sur la période."""
    from arabesque.data.store import load_ohlc, get_last_source_info

    interval_map = {"M1": "min1", "H1": "1h", "H4": "4h"}
    iv = interval_map.get(tf.upper(), tf.lower())
    # Marge avant pour les indicateurs (EMA200, etc.)
    fetch_start = (start - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    fetch_end = (end + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        df = load_ohlc(instrument, interval=iv, start=fetch_start, end=fetch_end)
    except Exception:
        _SKIPPED_NO_PARQUET.append((strategy, tf, instrument))
        return []
    src = get_last_source_info()
    if not _ALLOW_YAHOO and (src is None or not src.source.startswith("parquet")):
        _SKIPPED_NO_PARQUET.append((strategy, tf, instrument))
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
    return _dedup_sessions(entry_ts_list, tf)


def _load_live_entries(since: dt.datetime, until: dt.datetime) -> dict:
    """Renvoie {(strategy, instrument, broker): [ts, ...]} des entries live."""
    out: dict[tuple[str, str, str], list[pd.Timestamp]] = defaultdict(list)
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
        # "trend" est l'ancien alias loggué pour Extension
        if strat == "trend":
            strat = "extension"
        instr = obj.get("instrument") or "?"
        broker = obj.get("broker_id") or "?"
        out[(strat, instr, broker)].append(ts)
    return out


def _load_blocked(since: dt.datetime, until: dt.datetime) -> dict:
    """Renvoie les signaux consciemment bloques par un guard live.

    Inclut le weekend guard et les rejets propres a un broker (pre-vol GFT,
    quarantaine integrite). Sans ce second journal, un ordre GFT refuse pour
    securite ressortirait a tort comme panne silencieuse du connecteur.
    """
    out: dict[tuple[str, str, str], list[pd.Timestamp]] = defaultdict(list)
    for path, expected_event in (
        (GUARD_LOG, "blocked"),
        (BROKER_REJECT_LOG, "broker_guard_reject"),
    ):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event") != expected_event:
                continue
            ts = pd.Timestamp(obj["ts"])
            if ts < since or ts > until:
                continue
            strat = obj.get("strategy") or "?"
            instr = obj.get("instrument") or "?"
            broker = obj.get("broker_id") or "?"
            out[(strat, instr, broker)].append(ts)
    return out


def _dedup_sessions(ts_list: list[pd.Timestamp],
                    tf: str) -> list[pd.Timestamp]:
    """Regroupe les signaux consécutifs en sessions — retourne un représentant par session.

    Deux signaux consécutifs sur le même instrument appartiennent à la même
    session si l'écart entre eux est ≤ 1.5 × la durée d'une barre (tolérance
    pour les weekends/gaps de marché). On garde le premier signal de chaque session.
    """
    if not ts_list:
        return []
    tf_hours = {"M1": 1/60, "MIN1": 1/60, "H1": 1.0, "1H": 1.0,
                "H4": 4.0, "4H": 4.0, "D1": 24.0}
    bar_h = tf_hours.get(tf.upper(), 1.0)
    gap_threshold = pd.Timedelta(hours=bar_h * 1.5)
    sorted_ts = sorted(ts_list)
    sessions: list[pd.Timestamp] = [sorted_ts[0]]
    for prev, curr in zip(sorted_ts, sorted_ts[1:]):
        if curr - prev > gap_threshold:
            sessions.append(curr)
    return sessions


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
    p.add_argument("--min-missing", type=int, default=10,
                   help="Seuil de manquants pour notif/alerte (défaut 10). "
                        "Extension génère ~40-50 manquants/mois sur J-30 même "
                        "en fonctionnement normal (positions déjà ouvertes, "
                        "engine down events). Abaisser à 3-5 seulement si on "
                        "veut détecter un engine aveugle en J-7.")
    p.add_argument("--allow-yahoo", action="store_true",
                   help="Autoriser le fallback Yahoo si parquet local manquant. "
                        "Par défaut (strict-data), un instrument sans parquet est "
                        "skippé et listé dans le rapport — Yahoo peut diverger de "
                        "la source de validation locale.")
    args = p.parse_args()
    global _ALLOW_YAHOO
    _ALLOW_YAHOO = bool(args.allow_yahoo)

    since = pd.Timestamp(args.since, tz="UTC")
    until = pd.Timestamp(args.until, tz="UTC") if args.until else pd.Timestamp.now(tz="UTC")

    settings = yaml.safe_load(SETTINGS.read_text())
    instruments_cfg = yaml.safe_load(INSTRUMENTS.read_text()) if INSTRUMENTS.exists() else {}
    active_brokers = _load_active_brokers()
    exclusions = settings.get("strategy_broker_exclusions", {}) or {}

    targets = _build_targets(settings, instruments_cfg)
    print(f"Replay signals vs live — {since:%Y-%m-%d} → {until:%Y-%m-%d}")
    print(f"  {len(targets)} (strategy, tf, instrument) targets")
    print(f"  Brokers actifs : {', '.join(sorted(active_brokers)) or '(aucun)'}")

    live_entries = _load_live_entries(since, until)
    blocked = _load_blocked(since, until)

    # `source` = 0 broker fired (feed/engine down → 1 root cause × N brokers)
    # `broker_specific` = signal pris sur ≥1 broker mais raté sur ≥1 autre (rejet local)
    by_strat = defaultdict(lambda: {
        "theo": 0,
        "covered": 0,           # tous les brokers attendus = live OU blocked
        "source_missing": [],   # 0 broker → liste des signaux
        "broker_missing": [],   # ≥1 broker raté (mais pas tous) → liste avec détail
    })

    for strat, tf, instr in targets:
        theo_ts = _replay(strat, tf, instr, since, until)
        if not theo_ts:
            continue
        tol = ENTRY_TOL_HOURS.get(tf.upper(), 2.0)
        expected = _expected_brokers(strat, active_brokers, exclusions)
        if not expected:
            continue
        for t in theo_ts:
            by_strat[strat]["theo"] += 1
            status_by_broker: dict[str, str] = {}
            for broker in expected:
                if _match(t, live_entries.get((strat, instr, broker), []), tol):
                    status_by_broker[broker] = "live"
                elif _match(t, blocked.get((strat, instr, broker), []), tol):
                    status_by_broker[broker] = "blocked"
                else:
                    status_by_broker[broker] = "missing"
            missing_brokers = [b for b, s in status_by_broker.items() if s == "missing"]
            if not missing_brokers:
                by_strat[strat]["covered"] += 1
            elif len(missing_brokers) == len(expected):
                # Aucun broker n'a tiré → cause à la source
                by_strat[strat]["source_missing"].append({
                    "instrument": instr, "ts": t.isoformat(), "tf": tf,
                    "n_brokers": len(expected),
                })
            else:
                # Au moins un broker a tiré → rejet broker-specific
                taken = [b for b, s in status_by_broker.items() if s == "live"]
                by_strat[strat]["broker_missing"].append({
                    "instrument": instr, "ts": t.isoformat(), "tf": tf,
                    "missed_on": missing_brokers, "taken_on": taken,
                })

    print()
    notif_lines = []
    for strat in ("extension", "cabriole", "glissade", "fouette"):
        s = by_strat.get(strat)
        if not s or s["theo"] == 0:
            continue
        n_src = len(s["source_missing"])
        n_brk = len(s["broker_missing"])
        n_total = n_src + n_brk
        line = (f"  {strat:10s} théoriques={s['theo']:3d} "
                f"covered={s['covered']:3d} "
                f"source={n_src:3d} broker-specific={n_brk:3d}")
        print(line)
        if n_src > 0:
            for m in s["source_missing"][:5]:
                print(f"    🛰️ source  {m['ts']}  {m['instrument']:<8} ({m['tf']}) "
                      f"× {m['n_brokers']} brokers")
            if n_src > 5:
                print(f"    ... et {n_src-5} autres (source)")
        if n_brk > 0:
            for m in s["broker_missing"][:5]:
                miss = "+".join(m["missed_on"])
                ok = "+".join(m["taken_on"]) or "—"
                print(f"    ⚠️ broker  {m['ts']}  {m['instrument']:<8} ({m['tf']}) "
                      f"raté:{miss}  ok:{ok}")
            if n_brk > 5:
                print(f"    ... et {n_brk-5} autres (broker-specific)")
        if n_total > args.min_missing:
            notif_lines.append(
                f"• {strat} : {n_src} source + {n_brk} broker-specific "
                f"(seuil={args.min_missing})"
            )

    # Rapport strict-data : instruments skippés faute de parquet local
    if _SKIPPED_NO_PARQUET:
        print()
        print(f"⚠️  Strict-data : {len(_SKIPPED_NO_PARQUET)} target(s) "
              f"skippé(s) (parquet manquant ou source ≠ parquet)")
        for strat, tf, instr in _SKIPPED_NO_PARQUET[:15]:
            print(f"    {strat:10s} {tf:3s}  {instr}")
        if len(_SKIPPED_NO_PARQUET) > 15:
            print(f"    ... et {len(_SKIPPED_NO_PARQUET) - 15} autres")
        print("    → rejouer avec --allow-yahoo pour fallback, ou ingérer "
              "le parquet manquant.")

    if args.notify and notif_lines:
        try:
            import apprise
            secrets = yaml.safe_load((ROOT / "config/secrets.yaml").read_text())
            channels = select_notification_channels(
                secrets.get("notifications", {}).get("channels", []) or [],
                urgent=False,
            )
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

    return 0 if all(
        (len(s["source_missing"]) + len(s["broker_missing"])) <= args.min_missing
        for s in by_strat.values()
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
