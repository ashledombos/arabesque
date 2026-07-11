"""Ombre « données » d'Adage — jalon 4 (décision opérateur 2026-07-11).

Forme d'ombre actée pour Adage (signal déterministe à l'horloge : la chaîne
live n'ajoute presque rien statistiquement, et l'ombre chaîne-live aurait
demandé un lot de dev sur le chemin live — cf. DECISIONS 2026-07-11) :

1. Rafraîchit les min1 XAUUSD Dukascopy (fusion incrémentale, `--no-fetch`
   pour un run offline).
2. Rejoue TOUTE la série (2024 → dernière donnée) à travers la même chaîne
   Orchestrator que le dry-run du jalon 3 (guards → sizing 0,25 %/session →
   fill à l'open → PositionManager(adage_manager_config()) → audit) — une
   seule implémentation, zéro nouvelle convention (leçon |Δr| du lot 3).
3. Écrit `logs/adage_ombre_sessions.jsonl` (série complète, sessions d'ombre
   flaggées `is_ombre` = fill ≥ 2026-07-11) + `logs/adage_ombre_state.json`
   (verdict lisible par /suivi).

Seuils PRÉ-ENREGISTRÉS (gravés ici avant la première session d'ombre) :
- `tripwire_dd`   : le maxDD net de la série complète dépasse -16,2R (le pire
                    creux historique gravé à la dérogation DD du jalon 1) →
                    l'hypothèse « creux dans la distribution » est invalidée
                    FACTUELLEMENT → alerte opérateur, proposition de KILL.
- `revue_due`     : n_ombre ≥ 30 sessions (~6 semaines) → revue opérateur
                    (go/no-go jalon 5 micro-live à 0,20-0,30 %/session).
- `collecte`      : sinon — silencieux, pas de notification.

Divergences assumées vs backtest CLI (documentées, pessimisme préservé par
la convention de coût du dossier) : spread synthétique 1 bps dans la chaîne
dry-run (le guard « nuit anormale » ne rejette pas ici : ~4 nuits/634 à
-0,14R qui seraient évitées en vrai = lecture légèrement pessimiste) ; fill
à l'open sans coût d'entrée, le net applique 2,4 bps/session (spread+swap
mesurés au dossier).

Usage :
    python scripts/adage_ombre_daily.py            # fetch + rejeu + verdict
    python scripts/adage_ombre_daily.py --no-fetch # rejeu seul (offline)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from arabesque.broker.adapters import DryRunAdapter          # noqa: E402
from arabesque.config import ArabesqueConfig                 # noqa: E402
from arabesque.data.store import load_ohlc                   # noqa: E402
from arabesque.execution.orchestrator import Orchestrator    # noqa: E402
from arabesque.modules.position_manager import PositionManager  # noqa: E402
from arabesque.strategies.adage.signal import (              # noqa: E402
    AdageConfig, AdageSignalGenerator, adage_manager_config,
)

INSTRUMENT = "XAUUSD"
OMBRE_START = pd.Timestamp("2026-07-11", tz="UTC")   # début de l'ombre (jalon 4)
RISK_PCT = 0.25            # milieu du sizing gravé 0,20-0,30 %/session
COST_BPS = 2.4             # coût primaire dossier (1,0 spread + 1,4 swap)
TRIPWIRE_MAXDD_R = -16.2   # pire creux historique (dérogation DD jalon 1)
REVUE_N = 30               # sessions d'ombre → revue opérateur

SESSIONS_JSONL = REPO / "logs" / "adage_ombre_sessions.jsonl"
STATE_JSON = REPO / "logs" / "adage_ombre_state.json"


def fetch_recent(days: int) -> None:
    """Rafraîchit les min1 XAUUSD (fusion incrémentale via merge_store)."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    cmd = [
        sys.executable, "-m", "arabesque.data.fetch",
        "--start", start.isoformat(), "--end", end.isoformat(),
        "--filter", "^XAUUSD$", "--only", "dukascopy",
    ]
    print(f"[fetch] {' '.join(cmd[2:])}")
    res = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=900)
    if res.returncode != 0:
        # Non fatal : on rejoue avec les données déjà présentes.
        print(f"[fetch] ⚠️ échec (code {res.returncode}) — rejeu sur données existantes.\n"
              f"{res.stderr[-500:]}")


def replay_full_series() -> tuple[list[dict], list]:
    """Rejoue toute la série via la chaîne Orchestrator (cf. jalon 3)."""
    df = load_ohlc(INSTRUMENT, period="3000d", interval="min1")
    sg = AdageSignalGenerator(AdageConfig())
    df = sg.prepare(df)
    all_signals = sg.generate_signals(df, INSTRUMENT)

    idx = df.index
    opens = df["Open"].to_numpy()
    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    closes = df["Close"].to_numpy()

    pending: dict[int, dict] = {}
    sig_meta: dict[int, dict] = {}
    for i, sig in all_signals:
        j = i + 1
        if j >= len(df):
            continue
        pending[j] = {
            "instrument": sig.instrument, "side": "long",
            "timeframe": sig.timeframe, "close": sig.close, "open": sig.open_,
            "sl": sig.sl, "tp_indicative": 0.0, "atr": sig.atr,
            "rsi": sig.rsi, "cmf": sig.cmf, "bb_lower": sig.bb_lower,
            "bb_mid": sig.bb_mid, "bb_upper": sig.bb_upper,
            "bb_width": sig.bb_width, "ema200_ltf": sig.ema200_ltf,
            "htf_adx": sig.htf_adx, "regime": sig.regime,
            "max_spread_atr": sig.max_spread_atr, "rr": sig.rr,
            "strategy_type": sig.strategy_type, "sub_type": sig.sub_type,
        }
        sig_meta[j] = dict(sig.label_factors)

    cfg = ArabesqueConfig(
        mode="dry_run", start_balance=100_000.0, risk_per_trade_pct=RISK_PCT,
        max_daily_dd_pct=4.0, max_total_dd_pct=9.0, max_positions=7,
        max_daily_trades=10, max_spread_atr=0.10, max_slippage_atr=0.5,
        audit_dir=str(REPO / "tmp" / "adage_ombre_audit"),
    )
    orch = Orchestrator(config=cfg, brokers={"dry_run": DryRunAdapter(start_balance=cfg.start_balance)})
    orch.manager = PositionManager(adage_manager_config())

    accepted: dict[str, dict] = {}
    rejects: list[dict] = []
    cur_date = None
    for p in range(min(pending) - 1, len(df)):
        ts = idx[p]
        if ts.date() != cur_date:
            if cur_date is not None:
                orch.account.new_day()
            cur_date = ts.date()
        sig_data = pending.get(p)
        if sig_data is not None:
            sig_data = dict(sig_data)
            sig_data["close"] = float(opens[p])
            res = orch.handle_signal(sig_data)
            if res.get("status") == "accepted":
                accepted[res["position_id"]] = {"fill_ts": ts.isoformat(), **sig_meta[p]}
            else:
                rejects.append({"ts": ts.isoformat(), **res})
        orch.update_positions(instrument=INSTRUMENT, high=float(highs[p]),
                              low=float(lows[p]), close=float(closes[p]), bar_ts=ts)

    rows = []
    for pos in orch.manager.closed_positions:
        meta = accepted.get(pos.position_id, {})
        sigma = float(meta.get("sigma", 0) or 0)
        r = float(pos.result_r or 0.0)
        cost_r = (COST_BPS * 1e-4 / sigma) if sigma > 0 else 0.0
        fill_ts = meta.get("fill_ts", "")
        rows.append({
            "fill_ts": fill_ts,
            "is_ombre": bool(fill_ts and pd.Timestamp(fill_ts) >= OMBRE_START),
            "exit_reason": pos.exit_reason,
            "result_r": round(r, 4),
            "result_r_net": round(r - cost_r, 4),
            "sigma": sigma,
            "mfe_r": round(float(pos.mfe_r or 0), 3),
            "bars_open": pos.bars_open,
        })
    rows.sort(key=lambda x: x["fill_ts"])
    return rows, rejects


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-fetch", action="store_true", help="Rejeu seul, sans téléchargement")
    ap.add_argument("--fetch-days", type=int, default=7, help="Fenêtre du fetch incrémental")
    args = ap.parse_args()

    import logging
    logging.basicConfig(level=logging.WARNING)

    if not args.no_fetch:
        fetch_recent(args.fetch_days)

    rows, rejects = replay_full_series()
    with open(SESSIONS_JSONL, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    r_net = np.array([x["result_r_net"] for x in rows])
    eq = np.cumsum(r_net)
    maxdd_full = float(np.min(eq - np.maximum.accumulate(eq)))
    ombre = [x for x in rows if x["is_ombre"]]
    n_ombre = len(ombre)
    r_ombre = np.array([x["result_r_net"] for x in ombre]) if ombre else np.array([])

    if maxdd_full < TRIPWIRE_MAXDD_R:
        verdict = "tripwire_dd"
    elif n_ombre >= REVUE_N:
        verdict = "revue_due"
    else:
        verdict = "collecte"

    state = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "data_end": rows[-1]["fill_ts"] if rows else None,
        "n_total": len(rows),
        "n_ombre": n_ombre,
        "ombre_start": OMBRE_START.isoformat(),
        "exp_net_ombre": round(float(r_ombre.mean()), 4) if n_ombre else None,
        "sum_net_ombre": round(float(r_ombre.sum()), 2) if n_ombre else 0.0,
        "maxdd_full_net_r": round(maxdd_full, 2),
        "tripwire_maxdd_r": TRIPWIRE_MAXDD_R,
        "revue_n": REVUE_N,
        "rejects_last_run": len(rejects),
    }
    STATE_JSON.write_text(json.dumps(state, indent=2) + "\n")

    print(json.dumps(state, indent=2))
    if verdict == "tripwire_dd":
        print("\n🚨 TRIPWIRE DD : le creux dépasse le pire historique gravé "
              f"({maxdd_full:.1f}R < {TRIPWIRE_MAXDD_R}R) — alerte opérateur, proposer KILL.")
    elif verdict == "revue_due":
        print(f"\n📋 REVUE DUE : {n_ombre} sessions d'ombre ≥ {REVUE_N} — "
              "préparer la revue opérateur (go/no-go jalon 5).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
