"""Weekend guard ROI — counterfactuel sur les blocked events.

Pour chaque event ``blocked`` de ``logs/weekend_crypto_guard.jsonl``, on simule
ce qu'aurait donné le trade s'il avait été ouvert : entrée à ``entry_price``,
SL au ``sl`` du signal, TP à 2R, BE 0.3R / offset 0.20R, sur les bougies post-
signal du parquet, jusqu'à hit SL/TP ou 7 jours max (le weekend).

Sortie : par stratégie, n_blocked, n_win/n_loss/n_be (cf), WR_cf, Exp_cf, ΣR_cf.
Comparaison avec WR/Exp de la stratégie en semaine sur la même période
(``trade_journal.jsonl`` filtré exits hors weekend).

Verdict :
- ``WR_cf > WR_semaine + 10pp`` ET ``n_blocked >= 30`` → propose désactivation
- ``Exp_cf < 0`` → confirme le guard
- sinon → grey zone

Usage::

    python scripts/weekend_guard_review.py --since 2026-03-01
    python scripts/weekend_guard_review.py --since 2026-03-01 --notify
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
GUARD_LOG = ROOT / "logs" / "weekend_crypto_guard.jsonl"
JOURNAL = ROOT / "logs" / "trade_journal.jsonl"

BE_TRIGGER_R = 0.3
BE_OFFSET_R = 0.20
TP_R = 2.0
MAX_HOLD_HOURS = 7 * 24  # 7 jours max — couvre weekend complet


def _load_blocked(since: dt.datetime) -> list[dict]:
    if not GUARD_LOG.exists():
        return []
    out = []
    for line in GUARD_LOG.read_text().splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") != "blocked":
            continue
        ts = dt.datetime.fromisoformat(obj["ts"])
        if ts < since:
            continue
        out.append(obj)
    return out


def _simulate_one(blocked: dict) -> dict | None:
    """Simule TP/SL/BE sur les bougies post-signal. Renvoie {strategy, R, outcome}."""
    from arabesque.data.store import load_ohlc

    instr = blocked["instrument"]
    side = blocked["side"].upper()
    entry = float(blocked["entry_price"])
    sl = float(blocked["sl"])
    ts_signal = pd.Timestamp(blocked["ts"])

    risk = abs(entry - sl)
    if risk <= 0:
        return None
    if side == "LONG":
        sl_price = sl
        tp_price = entry + TP_R * risk
        be_trigger_price = entry + BE_TRIGGER_R * risk
        be_price = entry + BE_OFFSET_R * risk
    else:
        sl_price = sl
        tp_price = entry - TP_R * risk
        be_trigger_price = entry - BE_TRIGGER_R * risk
        be_price = entry - BE_OFFSET_R * risk

    end = ts_signal + pd.Timedelta(hours=MAX_HOLD_HOURS)
    try:
        df = load_ohlc(
            instr,
            interval="1h",
            start=ts_signal.strftime("%Y-%m-%d"),
            end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        )
    except Exception as e:
        return {"strategy": blocked.get("strategy") or "?", "R": None,
                "outcome": "no_data", "err": str(e)[:50]}
    if df.empty:
        return {"strategy": blocked.get("strategy") or "?", "R": None,
                "outcome": "no_data"}

    df = df[(df.index >= ts_signal) & (df.index <= end)]
    if df.empty:
        return {"strategy": blocked.get("strategy") or "?", "R": None,
                "outcome": "no_data"}

    be_armed = False
    eff_sl = sl_price
    for ts, row in df.iterrows():
        h, l = float(row["High"]), float(row["Low"])
        if not be_armed:
            if (side == "LONG" and h >= be_trigger_price) or (
                side == "SHORT" and l <= be_trigger_price
            ):
                be_armed = True
                eff_sl = be_price
        # Conservateur : SL avant TP si la bougie touche les deux
        if side == "LONG":
            if l <= eff_sl:
                R = (eff_sl - entry) / risk
                return {"strategy": blocked.get("strategy") or "?",
                        "R": R, "outcome": "be" if be_armed else "sl"}
            if h >= tp_price:
                return {"strategy": blocked.get("strategy") or "?",
                        "R": TP_R, "outcome": "tp"}
        else:
            if h >= eff_sl:
                R = (entry - eff_sl) / risk
                return {"strategy": blocked.get("strategy") or "?",
                        "R": R, "outcome": "be" if be_armed else "sl"}
            if l <= tp_price:
                return {"strategy": blocked.get("strategy") or "?",
                        "R": TP_R, "outcome": "tp"}
    last_close = float(df.iloc[-1]["Close"])
    R = (last_close - entry) / risk if side == "LONG" else (entry - last_close) / risk
    return {"strategy": blocked.get("strategy") or "?",
            "R": R, "outcome": "timeout"}


def _live_weekday_stats(since: dt.datetime) -> dict:
    """Aggrège WR/Exp en semaine (lundi-vendredi <15h UTC) depuis trade_journal."""
    if not JOURNAL.exists():
        return {}
    by_strat: dict[str, list[float]] = {}
    for line in JOURNAL.read_text().splitlines():
        if not line.strip() or '"event": "exit"' not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = dt.datetime.fromisoformat(obj["ts"])
        if ts < since:
            continue
        wd = ts.weekday()
        if wd in (5, 6) or (wd == 4 and ts.hour >= 15):
            continue
        strat = obj.get("strategy") or obj.get("strategy_type") or "?"
        r = obj.get("result_r")
        if r is None:
            continue
        by_strat.setdefault(strat, []).append(float(r))
    out = {}
    for strat, rs in by_strat.items():
        n = len(rs)
        wins = sum(1 for r in rs if r > 0.25)
        bes = sum(1 for r in rs if -0.25 <= r <= 0.25)
        wr = (wins + 0.5 * bes) / n if n else 0.0
        exp = sum(rs) / n if n else 0.0
        out[strat] = {"n": n, "WR": wr, "Exp": exp}
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=(
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)
    ).strftime("%Y-%m-%d"), help="ISO date, défaut J-60")
    p.add_argument("--notify", action="store_true", help="Notif apprise (Telegram+ntfy)")
    args = p.parse_args()

    since = dt.datetime.fromisoformat(args.since).replace(tzinfo=dt.timezone.utc)
    blocked = _load_blocked(since)
    print(f"Weekend guard review — depuis {since:%Y-%m-%d} : {len(blocked)} blocked events")
    if not blocked:
        print("Rien à simuler.")
        return 0

    by_strat: dict[str, list[dict]] = {}
    for ev in blocked:
        sim = _simulate_one(ev)
        if sim and sim.get("R") is not None:
            by_strat.setdefault(sim["strategy"], []).append(sim)

    weekday_stats = _live_weekday_stats(since)

    lines = []
    verdicts = []
    for strat, sims in sorted(by_strat.items()):
        n = len(sims)
        rs = [s["R"] for s in sims]
        wins = sum(1 for r in rs if r > 0.25)
        bes = sum(1 for r in rs if -0.25 <= r <= 0.25)
        losses = sum(1 for r in rs if r < -0.25)
        wr_cf = (wins + 0.5 * bes) / n if n else 0
        exp_cf = sum(rs) / n if n else 0
        sumr_cf = sum(rs)
        wd = weekday_stats.get(strat, {})
        wr_wd = wd.get("WR", 0)
        n_wd = wd.get("n", 0)
        delta_wr = (wr_cf - wr_wd) * 100 if n_wd else None

        verdict = "?"
        if exp_cf < 0:
            verdict = "✅ confirme guard (Exp_cf < 0)"
        elif delta_wr is not None and delta_wr > 10 and n >= 30:
            verdict = "⚠️ propose désactivation"
        else:
            verdict = "🟡 grey zone — recheck"
        verdicts.append((strat, verdict))

        lines.append(
            f"  {strat:10s} blocked n={n:3d} W={wins:2d} BE={bes:2d} L={losses:2d} | "
            f"WR_cf={wr_cf*100:5.1f}% Exp_cf={exp_cf:+.3f}R ΣR={sumr_cf:+.1f} "
            f"| semaine n={n_wd:3d} WR={wr_wd*100:5.1f}% "
            f"| Δ_WR={delta_wr:+.1f}pp" if delta_wr is not None else
            f"  {strat:10s} blocked n={n:3d} W={wins:2d} BE={bes:2d} L={losses:2d} | "
            f"WR_cf={wr_cf*100:5.1f}% Exp_cf={exp_cf:+.3f}R ΣR={sumr_cf:+.1f} "
            f"| semaine n=0 (pas de comparable)"
        )

    print("\n".join(lines))
    print()
    for strat, v in verdicts:
        print(f"  → {strat:10s} : {v}")

    if args.notify:
        try:
            import apprise
            import yaml
            secrets = yaml.safe_load((ROOT / "config/secrets.yaml").read_text())
            channels = secrets.get("notifications", {}).get("channels", []) or []
            if channels:
                ap = apprise.Apprise()
                for ch in channels:
                    if isinstance(ch, str):
                        ap.add(ch)
                body = "🛡️ Weekend guard ROI\n" + "\n".join(
                    f"• {s} : {v}" for s, v in verdicts
                )
                asyncio.run(ap.async_notify(body=body, title="Arabesque /bilan"))
        except Exception as e:
            print(f"notif err: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
