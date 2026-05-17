"""Arabesque — invariants d'exécution (détection précoce de bugs de tracking).

Distinct de l'edge audit : ici on mesure si l'engine fait bien son job
(tracking MFE, BE armé, état des positions cohérent), pas si les stratégies
ont un edge.

Triggers calculés sur fenêtre glissante (défaut 7j) :

| ID | Métrique | Seuil ALERT | Seuil CRITIQUE |
|---|---|---|---|
| reconciled_other_ratio | exits "reconciled_other" / total (fallback ambigu) | > 2% (n≥10) | > 5% |
| reconciled_ratio | exits "reconciled_*" / total (info uptime) | > 30% | — |
| mfe_zero_loser | exits avec mfe_r=0 ET result_r ≤ -0.5R | ≥ 3 sur 7j | ≥ 5 sur 7j |
| zero_winner_streak | trades consécutifs avec result_r > 0.25 | — | 0 winner sur n≥20 |
| be_unarmed_ratio | losers (-1R) où be_source ∉ {broker_armed,broker_evidence} alors mfe_r ≥ 0.3R | > 10% | > 25% |
| be_inferred_but_loser | exits be_source=inferred_from_mfe ET result_r ≤ -0.5R | ≥ 1 | ≥ 3 |

Depuis le fix 2026-05-07 : reconciled_take_profit/_breakeven_exit/_stop_loss
sont reconstruits avec un vrai MFE (bars min1) et un vrai exit_price (broker
detail). Seul `reconciled_other` (exit ambigu, ni TP, ni SL, ni BE) reste un
signal de fallback à surveiller.

Le drift_decompose (slip vs tracking_gap vs régime) reste dans
`compare_live_vs_backtest` et `audit_edge_live_vs_backtest`. Ici on attaque
les invariants instantanément vérifiables.

Usage :
    python scripts/check_execution_invariants.py
    python scripts/check_execution_invariants.py --since 2026-04-01
    python scripts/check_execution_invariants.py --json     # sortie machine
    python scripts/check_execution_invariants.py --strict   # exit 2 si CRITIQUE

Exit codes : 0=ok, 1=alert, 2=critique.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "logs" / "trade_journal.jsonl"


def _be_was_armed_broker(exit_record: dict) -> bool:
    """Renvoie True si on a une preuve (directe ou indirecte) que le SL a
    été amendé broker-side.

    Sémantique be_source (cf. DECISIONS.md §3) — taxonomie stricte :
      - "broker_armed"      → True (preuve DIRECTE : amend_position_sltp
                                    success observé en live)
      - "broker_evidence"   → True (preuve INDIRECTE forte : exit broker
                                    ≈ be_target, déduit post-hoc dans le
                                    path reconcile)
      - "inferred_from_mfe" → False (MFE parquet seul, aucune preuve broker)
      - "not_armed"         → False
      - "unknown"/absent    → fallback rétrocompat sur be_set (records pré-fix)

    Le champ be_set seul est INSUFFISANT pour les invariants critiques : il
    mélange "SL réellement amendé broker" et "MFE parquet >= seuil"
    (cf. incident XAUUSD 14-05 où be_set=True par inférence post-hoc alors
    que le SL plein avait été touché côté broker).
    """
    src = exit_record.get("be_source")
    if src and src != "unknown":
        return src in ("broker_armed", "broker_evidence")
    # Rétrocompat : ancien record sans be_source, on retombe sur be_set
    return bool(exit_record.get("be_set", False))


def _load_exits(
    since: dt.datetime,
    until: dt.datetime,
    broker: str | None = None,
) -> list[dict]:
    out = []
    if not JOURNAL.exists():
        return out
    for line in JOURNAL.read_text().splitlines():
        if not line.strip() or '"event": "exit"' not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = dt.datetime.fromisoformat(obj["ts"])
        if not (since <= ts <= until):
            continue
        if broker and obj.get("broker_id") != broker:
            continue
        out.append(obj)
    return out


def _evaluate(exits: list[dict]) -> dict:
    n = len(exits)
    out = {"n_exits": n, "triggers": [], "verdict": "ok", "details": {}}
    if n == 0:
        out["details"]["note"] = "aucun exit sur la fenêtre"
        return out

    # 1) reconciled : on distingue les types depuis le fix 2026-05-07
    #   - reconciled_take_profit / _breakeven_exit / _stop_loss : reconstruction
    #     fiable (broker detail + bars min1) → légitimes après un downtime.
    #   - reconciled_other / _stop_loss avec mfe_r=0 alors que loser franc :
    #     suspects (bug de tracking ou fallback "estimated").
    reconciled = [e for e in exits
                  if str(e.get("exit_reason", "")).startswith("reconciled")]
    rec_pct = 100.0 * len(reconciled) / n
    rec_other = [e for e in reconciled
                 if e.get("exit_reason") == "reconciled_other"]
    rec_pct_other = 100.0 * len(rec_other) / n
    out["details"]["reconciled"] = {
        "count": len(reconciled),
        "pct": round(rec_pct, 1),
        "other_count": len(rec_other),
        "other_pct": round(rec_pct_other, 1),
    }
    # Trigger sur reconciled_other : c'est le fallback ambigu, doit rester rare
    if n >= 10:
        if rec_pct_other > 5:
            out["triggers"].append(("reconciled_other_ratio", "CRITIQUE",
                f"{len(rec_other)}/{n} exits reconciled_other "
                f"({rec_pct_other:.1f}%) — fallback ambigu trop fréquent"))
        elif rec_pct_other > 2:
            out["triggers"].append(("reconciled_other_ratio", "ALERT",
                f"{len(rec_other)}/{n} exits reconciled_other "
                f"({rec_pct_other:.1f}%)"))
    # Le ratio reconciled total reste informatif (uptime broker / reboots)
    if n >= 10 and rec_pct > 30:
        out["triggers"].append(("reconciled_ratio", "ALERT",
            f"{len(reconciled)}/{n} exits reconciled ({rec_pct:.1f}%) — "
            f"uptime engine/broker à investiguer"))

    # 2) mfe_zero_loser : MFE=0 alors que loser franc
    mfe0 = [e for e in exits
            if (e.get("mfe_r") or 0) == 0 and (e.get("result_r") or 0) <= -0.5]
    out["details"]["mfe_zero_loser"] = {"count": len(mfe0)}
    if len(mfe0) >= 5:
        out["triggers"].append(("mfe_zero_loser", "CRITIQUE",
            f"{len(mfe0)} losers avec mfe_r=0 — tracker MFE défaillant"))
    elif len(mfe0) >= 3:
        out["triggers"].append(("mfe_zero_loser", "ALERT",
            f"{len(mfe0)} losers avec mfe_r=0"))

    # 3) zero_winner_streak : aucun winner sur ≥20 trades
    wins = [e for e in exits if (e.get("result_r") or 0) > 0.25]
    out["details"]["wins"] = {"count": len(wins), "pct": round(100*len(wins)/n, 1)}
    if n >= 20 and len(wins) == 0:
        out["triggers"].append(("zero_winner_streak", "CRITIQUE",
            f"0 winner sur {n} trades — edge mort ou exécution cassée"))

    # 4) be_unarmed_ratio : losers -1R alors que mfe_r ≥ 0.3 mais BE pas armé
    #    broker (be_source ≠ "broker_armed"). Lit be_source en priorité,
    #    fallback be_set pour rétrocompat (cf. _be_was_armed_broker).
    losers_full = [e for e in exits if (e.get("result_r") or 0) <= -0.9]
    be_should = [e for e in losers_full
                 if (e.get("mfe_r") or 0) >= 0.3
                 and not _be_was_armed_broker(e)]
    n_lf = len(losers_full)
    pct = 100.0 * len(be_should) / n_lf if n_lf else 0.0
    out["details"]["be_unarmed"] = {"count": len(be_should),
                                     "of_full_losers": n_lf,
                                     "pct": round(pct, 1)}
    if n_lf >= 5:
        if pct > 25:
            out["triggers"].append(("be_unarmed_ratio", "CRITIQUE",
                f"{len(be_should)}/{n_lf} losers -1R avaient MFE≥0.3R sans BE armé broker ({pct:.1f}%)"))
        elif pct > 10:
            out["triggers"].append(("be_unarmed_ratio", "ALERT",
                f"{len(be_should)}/{n_lf} losers -1R avaient MFE≥0.3R sans BE armé broker ({pct:.1f}%)"))

    # 5) be_inferred_but_loser : exits be_source=inferred_from_mfe ET loser
    # significatif. Pattern XAUUSD 14-05 : engine down → tick 0.3R jamais reçu
    # → SL plein hit alors que MFE 0.91R observé post-hoc en parquet. Chaque
    # cas est une preuve forte d'un downtime engine pendant lequel le BE
    # physique n'a pas pu être armé. Distinct de be_unarmed_ratio (ratio
    # global) car ici on traque le cas SPÉCIFIQUE "BE théorique inféré mais
    # broker a confirmé exit≠BE". Ne s'active qu'avec records ayant
    # be_source explicite (pas de reclassification rétroactive).
    be_inferred_losers = [e for e in exits
                          if e.get("be_source") == "inferred_from_mfe"
                          and (e.get("result_r") or 0) <= -0.5]
    out["details"]["be_inferred_but_loser"] = {"count": len(be_inferred_losers)}
    if len(be_inferred_losers) >= 3:
        out["triggers"].append(("be_inferred_but_loser", "CRITIQUE",
            f"{len(be_inferred_losers)} exits be_source=inferred_from_mfe ET "
            f"loser significatif — engine downtime récurrent pendant BE arming"))
    elif len(be_inferred_losers) >= 1:
        out["triggers"].append(("be_inferred_but_loser", "ALERT",
            f"{len(be_inferred_losers)} exit(s) be_source=inferred_from_mfe ET "
            f"loser — BE physique pas armé broker pendant downtime engine"))

    # Verdict global
    if any(t[1] == "CRITIQUE" for t in out["triggers"]):
        out["verdict"] = "critique"
    elif out["triggers"]:
        out["verdict"] = "alert"

    # Distribution exit_reasons (info, pas trigger)
    er_dist = defaultdict(int)
    for e in exits:
        er_dist[e.get("exit_reason", "?")] += 1
    out["details"]["exit_reasons"] = dict(er_dist)

    return out


def _format_human(
    report: dict,
    since: dt.datetime,
    until: dt.datetime,
    broker: str | None = None,
) -> str:
    scope = f" [{broker}]" if broker else ""
    lines = [
        f"🔍 Invariants exécution{scope} {since:%Y-%m-%d}→{until:%Y-%m-%d} "
        f"(n={report['n_exits']} exits)"
    ]
    icons = {"ok": "🟢", "alert": "🟡", "critique": "🚨"}
    lines.append(f"{icons.get(report['verdict'], '?')} Verdict : {report['verdict'].upper()}")

    d = report["details"]
    if "reconciled" in d:
        rec = d["reconciled"]
        lines.append(
            f"  reconciled : {rec['count']} ({rec['pct']}%) — "
            f"dont other={rec.get('other_count', 0)} ({rec.get('other_pct', 0)}%)"
        )
    if "mfe_zero_loser" in d:
        lines.append(f"  mfe=0 losers : {d['mfe_zero_loser']['count']}")
    if "be_unarmed" in d:
        be = d["be_unarmed"]
        lines.append(f"  BE non armé broker sur losers -1R : {be['count']}/{be['of_full_losers']} "
                     f"({be['pct']}%)")
    if "be_inferred_but_loser" in d:
        bil = d["be_inferred_but_loser"]
        if bil["count"] > 0:
            lines.append(f"  BE théorique inféré + loser : {bil['count']} (downtime engine)")
    if "wins" in d:
        lines.append(f"  wins : {d['wins']['count']} ({d['wins']['pct']}%)")

    if report["triggers"]:
        lines.append("")
        lines.append("Triggers :")
        for tid, sev, msg in report["triggers"]:
            icon = "🚨" if sev == "CRITIQUE" else "🟡"
            lines.append(f"  {icon} [{tid}] {msg}")
    return "\n".join(lines)


def _verdict_rank(v: str) -> int:
    return {"ok": 0, "alert": 1, "critique": 2}.get(v, 0)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=None, help="ISO date, défaut J-7")
    p.add_argument("--until", default=None, help="ISO date, défaut now")
    p.add_argument("--broker", default=None,
                   help="Filtrer par broker_id (ex: ftmo_challenge, gft_compte1)")
    p.add_argument("--per-broker", action="store_true",
                   help="Évalue séparément FTMO + GFT, verdict global = max")
    p.add_argument("--json", action="store_true", help="sortie JSON")
    p.add_argument("--strict", action="store_true",
                   help="exit 2 si verdict critique")
    args = p.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    since = (dt.datetime.fromisoformat(args.since).replace(tzinfo=dt.timezone.utc)
             if args.since else now - dt.timedelta(days=7))
    until = (dt.datetime.fromisoformat(args.until).replace(tzinfo=dt.timezone.utc)
             if args.until else now)

    if args.per_broker:
        # Évalue séparément, verdict global = pire des deux
        reports = {}
        for b in ("ftmo_challenge", "gft_compte1"):
            ex = _load_exits(since, until, broker=b)
            reports[b] = _evaluate(ex)
        worst = max(reports.values(), key=lambda r: _verdict_rank(r["verdict"]))
        global_verdict = worst["verdict"]
        if args.json:
            print(json.dumps({
                "global_verdict": global_verdict,
                "per_broker": reports,
            }, indent=2))
        else:
            for b, r in reports.items():
                print(_format_human(r, since, until, broker=b))
                print()
            icon = {"ok": "🟢", "alert": "🟡", "critique": "🚨"}.get(global_verdict, "?")
            print(f"{icon} Verdict global : {global_verdict.upper()} (max des 2 brokers)")
        if args.strict and global_verdict == "critique":
            return 2
        if global_verdict == "alert":
            return 1
        return 0

    exits = _load_exits(since, until, broker=args.broker)
    report = _evaluate(exits)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_format_human(report, since, until, broker=args.broker))

    if args.strict and report["verdict"] == "critique":
        return 2
    if report["verdict"] == "alert":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
