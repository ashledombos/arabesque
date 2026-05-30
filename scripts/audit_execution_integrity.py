"""Arabesque — Audit intégrité d'exécution (forward-looking, audit only).

Compteur quotidien des signatures de bugs d'exécution sur les trades live.
Sert à vérifier que les correctifs récents (cutoff = commit `6b3f464`
2026-05-29 22:14 +0200, qui suit `968280f`) ferment effectivement les fuites
observées en Phase 4.

Catégories de signatures (mutuellement exclusives pour le chiffrage principal,
ordre = priorité décroissante) :

  1. RECONCILED_STOP_HIGH_MFE  — reconciled_stop_loss + mfe_r >= 0.3R.
     Cas le plus grave : le SL initial a déclenché alors que BE/trailing
     auraient dû être armés broker (cas DASHUSD 2026-05-22).
  2. RECONCILED_HIGH_MFE        — autre exit reconcilé avec mfe_r >= 0.3R.
  3. BE_MISSED                  — mfe_r >= 0.3R et BE *non armé broker*
     (be_source != "broker_armed").
  4. EXIT_NO_BROKER_QUOTE       — spread_at_exit=0 ou bid/ask broker absents
     sur la sortie (canal/feed mort).
  5. MFE_ZERO_LOSER             — mfe_r==0 et result_r<0 (informational,
     pas classé bug — gardé pour diagnostic).
  6. CLEAN                      — aucune signature ci-dessus.

Une vue séparée `tags_multiples` liste les trades qui matchent ≥2 catégories,
sans rentrer dans le chiffrage principal (qui reste exclusif).

Anomalies hors-exit (événements séparés) :
  - protection_check (confirmed=false)
  - risk_integrity_check (status over/under)
  - exit_invalidated_by_bug
  - emergency_close_all
  - orphan_cleanup

Sortie :
  - logs/execution_integrity_latest.md (rapport humain)
  - logs/execution_integrity.jsonl      (historique append-only, 1 ligne/run)

Audit ONLY — ne touche à aucun composant live.

Usage :
    python scripts/audit_execution_integrity.py                # 7 derniers jours
    python scripts/audit_execution_integrity.py --days 14
    python scripts/audit_execution_integrity.py --since 2026-05-01
    python scripts/audit_execution_integrity.py --no-write     # dry-run console
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

JOURNAL = "logs/trade_journal.jsonl"
OUT_MD = "logs/execution_integrity_latest.md"
OUT_JSONL = "logs/execution_integrity.jsonl"

# Cutoff forward = max(timestamps des commits clés)
# 968280f (audit be polling)      : 2026-05-29 22:07 +0200 → 20:07 UTC
# 6b3f464 (block autorestart open): 2026-05-29 22:14 +0200 → 20:14 UTC
FORWARD_CUTOFF_UTC = datetime(2026, 5, 29, 20, 14, 47, tzinfo=timezone.utc)
FORWARD_CUTOFF_REF = "post-968280f / post-6b3f464 (2026-05-29 20:14 UTC)"

# Seuil MFE au-delà duquel BE aurait dû s'armer (Extension : BE trigger 0.3R)
MFE_THRESHOLD = 0.30

# Ordre = priorité décroissante. Le premier match dans `classify_exit` devient
# la catégorie primaire exclusive.
SIGNATURES_PRIORITY = [
    "RECONCILED_STOP_HIGH_MFE",
    "RECONCILED_HIGH_MFE",
    "BE_MISSED",
    "EXIT_NO_BROKER_QUOTE",
    "MFE_ZERO_LOSER",
    "CLEAN",
]

# Signatures qui doivent être à zéro sur la fenêtre forward pour valider les fixes
CRITICAL_SIGNATURES = {
    "RECONCILED_STOP_HIGH_MFE",
    "RECONCILED_HIGH_MFE",
    "BE_MISSED",
}

STRATEGY_ALIASES = {"trend": "extension"}


# ─────────────────────────────────────────────────────────────────────────────
# Classification (pure, testable)
# ─────────────────────────────────────────────────────────────────────────────

def classify_exit(ev: dict) -> tuple[str, list[str]]:
    """Classifie un exit journalisé.

    Retourne ``(primary, tags)`` :
      - ``primary`` : catégorie unique (mutuellement exclusive, par priorité)
      - ``tags`` : liste de toutes les catégories qui matchent (peut être vide)

    Règles :
      - RECONCILED_STOP_HIGH_MFE : exit_reason == "reconciled_stop_loss" et
        mfe_r >= 0.30.
      - RECONCILED_HIGH_MFE      : exit reconcilé (autre que stop) et mfe_r >= 0.30.
      - BE_MISSED                : mfe_r >= 0.30 et BE non armé broker
        (be_source ∈ {None, "not_armed", "inferred_from_mfe"} ou be_set=False).
      - EXIT_NO_BROKER_QUOTE     : spread_at_exit == 0.0 ou bid/ask broker
        absents (== 0.0) à la sortie.
      - MFE_ZERO_LOSER           : mfe_r == 0 et result_r < 0 (informational).

    Données manquantes (champs pré-mi-mai) : on flag conservativement BE_MISSED
    si be_source est absent — segmentation pré/post-strict assurée par la
    fenêtre forward cutoff au niveau du verdict.
    """
    mfe = _f(ev.get("mfe_r"))
    exit_reason = ev.get("exit_reason") or ""
    eps = ev.get("exit_price_source")
    be_set = bool(ev.get("be_set"))
    be_source = ev.get("be_source")
    result_r = _f(ev.get("result_r"))
    spread_x = ev.get("spread_at_exit")
    bid_x = ev.get("broker_bid_at_exit")
    ask_x = ev.get("broker_ask_at_exit")

    is_reconciled_stop = exit_reason == "reconciled_stop_loss"
    is_reconciled_any = (
        is_reconciled_stop
        or exit_reason.startswith("reconciled_")
        or eps == "reconciled"
    )

    # « BE armé broker » = seul be_source = "broker_armed" est une preuve.
    # Tout le reste (None pré-strict, "not_armed", "inferred_from_mfe", be_set=false)
    # est considéré non-armé pour le flag — on segmente pré/post-cutoff au niveau verdict.
    be_broker_armed = (be_source == "broker_armed")
    be_not_broker_armed = not be_broker_armed

    no_broker_quote = (
        (spread_x is not None and spread_x == 0.0)
        or (bid_x is not None and ask_x is not None
            and bid_x == 0.0 and ask_x == 0.0)
    )

    tags: list[str] = []
    if is_reconciled_stop and mfe >= MFE_THRESHOLD:
        tags.append("RECONCILED_STOP_HIGH_MFE")
    if is_reconciled_any and mfe >= MFE_THRESHOLD:
        tags.append("RECONCILED_HIGH_MFE")
    if mfe >= MFE_THRESHOLD and be_not_broker_armed:
        tags.append("BE_MISSED")
    if no_broker_quote:
        tags.append("EXIT_NO_BROKER_QUOTE")
    if mfe == 0.0 and result_r < 0:
        tags.append("MFE_ZERO_LOSER")

    primary = "CLEAN"
    for cat in SIGNATURES_PRIORITY:
        if cat in tags:
            primary = cat
            break

    return primary, tags


def classify_non_exit_anomaly(ev: dict) -> str | None:
    """Classifie un événement non-exit en anomalie nommée, sinon None."""
    et = ev.get("event", "")
    if et == "protection_check" and not ev.get("confirmed", True):
        return "PROTECTION_CHECK_FAILED"
    if et == "risk_integrity_check":
        status = ev.get("status", "")
        if status in ("over_risk", "over_risk_critical", "under_risk"):
            return f"RISK_INTEGRITY_{status.upper()}"
    if et == "exit_invalidated_by_bug":
        return "EXIT_INVALIDATED_BY_BUG"
    if et == "emergency_close_all":
        return "EMERGENCY_CLOSE_ALL"
    if et == "orphan_cleanup":
        return "ORPHAN_CLEANUP"
    return None


def _f(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Agrégation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Bucket:
    n_exits: int = 0
    primary_counts: Counter = field(default_factory=Counter)
    primary_sum_r: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    primary_trade_ids: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    # Décomposition loss/non-loss pour ne pas mélanger un bug "à coût négatif"
    # (cas typique : BE_MISSED rattrapé par operator_auto_close avec R>0) avec
    # un bug "à coût réel" (BE_MISSED qui finit par déclencher le SL initial).
    # La signature reste identique ; on segmente seulement le chiffrage en R.
    primary_loss_counts: Counter = field(default_factory=Counter)
    primary_loss_sum_r: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    primary_nonloss_counts: Counter = field(default_factory=Counter)
    primary_nonloss_sum_r: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    primary_nonloss_trade_ids: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    non_exit_anomalies: Counter = field(default_factory=Counter)
    multi_tag_examples: list[dict] = field(default_factory=list)


def load_events(path: str, since: datetime, until: datetime) -> list[dict]:
    """Charge les événements du journal dans la fenêtre [since, until]."""
    events = []
    p = Path(path)
    if not p.exists():
        return events
    with p.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_str = ev.get("ts")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if not (since <= ts <= until):
                continue
            ev["_ts_dt"] = ts
            events.append(ev)
    return events


def aggregate(events: Iterable[dict]) -> dict:
    """Agrège en buckets : overall, par jour, par (jour, broker, strat, instr)."""
    overall = Bucket()
    by_day: dict[str, Bucket] = defaultdict(Bucket)
    by_dim: dict[tuple, Bucket] = defaultdict(Bucket)
    non_exit_examples: dict[str, list[dict]] = defaultdict(list)

    for ev in events:
        ts = ev["_ts_dt"]
        day = ts.date().isoformat()
        et = ev.get("event")

        if et == "exit":
            primary, tags = classify_exit(ev)
            broker = ev.get("broker_id", "?")
            strat = STRATEGY_ALIASES.get(ev.get("strategy", "?"), ev.get("strategy", "?"))
            instrument = ev.get("instrument", "?")
            tid = ev.get("trade_id", "?")
            r = _f(ev.get("result_r"))

            is_loss = r < 0
            for bucket in (overall, by_day[day], by_dim[(day, broker, strat, instrument)]):
                bucket.n_exits += 1
                bucket.primary_counts[primary] += 1
                bucket.primary_sum_r[primary] += r
                bucket.primary_trade_ids[primary].append(tid)
                if is_loss:
                    bucket.primary_loss_counts[primary] += 1
                    bucket.primary_loss_sum_r[primary] += r
                else:
                    bucket.primary_nonloss_counts[primary] += 1
                    bucket.primary_nonloss_sum_r[primary] += r
                    bucket.primary_nonloss_trade_ids[primary].append(tid)

            if len(tags) > 1:
                overall.multi_tag_examples.append({
                    "trade_id": tid,
                    "ts": ts.isoformat(),
                    "instrument": instrument,
                    "broker": broker,
                    "strategy": strat,
                    "tags": tags,
                    "primary": primary,
                    "result_r": r,
                    "mfe_r": _f(ev.get("mfe_r")),
                })
        else:
            anomaly = classify_non_exit_anomaly(ev)
            if anomaly:
                overall.non_exit_anomalies[anomaly] += 1
                by_day[day].non_exit_anomalies[anomaly] += 1
                if len(non_exit_examples[anomaly]) < 5:
                    non_exit_examples[anomaly].append({
                        "ts": ts.isoformat(),
                        "broker": ev.get("broker_id"),
                        "instrument": ev.get("instrument"),
                        "details": {k: v for k, v in ev.items() if k != "_ts_dt"},
                    })

    return {
        "overall": overall,
        "by_day": dict(by_day),
        "by_dim": dict(by_dim),
        "non_exit_examples": dict(non_exit_examples),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Verdict 7j
# ─────────────────────────────────────────────────────────────────────────────

def compute_verdict(agg: dict, cutoff: datetime, now: datetime,
                    required_forward_days: float = 7.0) -> dict:
    """Statut par signature critique :

    - ``open``           : ≥1 occurrence après cutoff → fuite non refermée.
    - ``fixed_forward``  : 0 après cutoff, ≥1 avant cutoff, ≥7j de forward.
    - ``no_occurrence``  : 0 avant et après cutoff, ≥7j de forward.
    - ``monitoring``     : 0 après cutoff mais <7j de forward (verdict trop tôt).
    """
    days_since_cutoff = (now - cutoff).total_seconds() / 86_400.0
    cutoff_date = cutoff.date()

    pre_counts: Counter = Counter()
    post_counts: Counter = Counter()
    post_tids: dict[str, list[str]] = defaultdict(list)

    for day, bucket in agg["by_day"].items():
        day_date = datetime.fromisoformat(day).date()
        is_post = day_date >= cutoff_date
        for sig in CRITICAL_SIGNATURES:
            n = bucket.primary_counts.get(sig, 0)
            if not n:
                continue
            if is_post:
                post_counts[sig] += n
                post_tids[sig].extend(bucket.primary_trade_ids.get(sig, []))
            else:
                pre_counts[sig] += n

    per_sig = {}
    for sig in sorted(CRITICAL_SIGNATURES):
        post = post_counts.get(sig, 0)
        pre = pre_counts.get(sig, 0)
        if post > 0:
            status = "open"
        elif days_since_cutoff >= required_forward_days:
            status = "fixed_forward" if pre > 0 else "no_occurrence"
        else:
            status = "monitoring"
        per_sig[sig] = {
            "pre_cutoff": pre,
            "post_cutoff": post,
            "status": status,
            "post_trade_ids": post_tids.get(sig, []),
        }

    if any(v["status"] == "open" for v in per_sig.values()):
        overall = "RED"
    elif any(v["status"] == "monitoring" for v in per_sig.values()):
        overall = "MONITORING"
    else:
        overall = "GREEN"

    return {
        "overall_status": overall,
        "per_signature": per_sig,
        "days_since_cutoff": round(days_since_cutoff, 2),
        "required_forward_days": required_forward_days,
        "cutoff_utc": cutoff.isoformat(),
        "cutoff_ref": FORWARD_CUTOFF_REF,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Rendu Markdown
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_EMOJI = {
    "open": "🔴",
    "monitoring": "🟡",
    "fixed_forward": "🟢",
    "no_occurrence": "⚪",
    "RED": "🔴",
    "MONITORING": "🟡",
    "GREEN": "🟢",
}


def render_markdown(agg: dict, verdict: dict, since: datetime, until: datetime) -> str:
    overall: Bucket = agg["overall"]
    lines: list[str] = []
    L = lines.append

    L("# Audit intégrité d'exécution")
    L("")
    L(f"- **Période** : {since.date()} → {until.date()}")
    L(f"- **Forward cutoff** : {verdict['cutoff_ref']}")
    L(f"- **Jours forward écoulés** : {verdict['days_since_cutoff']} / {verdict['required_forward_days']} requis")
    L(f"- **Exits analysés** : {overall.n_exits}")
    L("")

    # Verdict 7j
    L(f"## Verdict {int(verdict['required_forward_days'])}j — signatures critiques")
    L("")
    L(f"**Statut global : {_STATUS_EMOJI[verdict['overall_status']]} {verdict['overall_status']}**")
    L("")
    L("| Signature | Pré-cutoff | Post-cutoff | Statut |")
    L("|---|---:|---:|---|")
    for sig, info in verdict["per_signature"].items():
        emoji = _STATUS_EMOJI.get(info["status"], "?")
        L(f"| `{sig}` | {info['pre_cutoff']} | {info['post_cutoff']} | {emoji} {info['status']} |")
    L("")

    # Cas post-cutoff à investiguer
    open_sigs = {sig: info for sig, info in verdict["per_signature"].items()
                 if info["status"] == "open"}
    if open_sigs:
        L("### ⚠️ Cas post-cutoff (fuite non refermée)")
        for sig, info in open_sigs.items():
            L(f"- `{sig}` : {info['post_cutoff']} trade(s) → {info['post_trade_ids']}")
        L("")

    # Tableau principal des signatures (exclusif), décomposé loss / non-loss
    L("## Décomposition par signature primaire (mutuellement exclusive)")
    L("")
    L("La colonne **ΣR coût net** n'inclut que les trades en perte (`result_r < 0`).")
    L("Les cas non-perte (bug technique sans coût net, ex. rattrapé par operator)")
    L("sont comptés à part — ils restent une anomalie à investiguer, pas un coût.")
    L("")
    L("| Signature | n total | n perte | ΣR coût net | n non-perte | exemples (perte) |")
    L("|---|---:|---:|---:|---:|---|")
    for sig in SIGNATURES_PRIORITY:
        n = overall.primary_counts.get(sig, 0)
        if n == 0:
            continue
        n_loss = overall.primary_loss_counts.get(sig, 0)
        sumr_loss = overall.primary_loss_sum_r.get(sig, 0.0)
        n_nonloss = overall.primary_nonloss_counts.get(sig, 0)
        # Exemples : préférer les pertes (cas de coût net)
        loss_tids = [tid for tid in overall.primary_trade_ids.get(sig, [])
                     if tid not in overall.primary_nonloss_trade_ids.get(sig, [])][:5]
        ex = ", ".join(loss_tids) if loss_tids else "—"
        L(f"| `{sig}` | {n} | {n_loss} | {sumr_loss:+.2f}R | {n_nonloss} | {ex} |")
    L("")

    # Cas non-perte sur signatures critiques : isoler pour ne pas confondre
    nonloss_critical = []
    for sig in CRITICAL_SIGNATURES:
        for tid in overall.primary_nonloss_trade_ids.get(sig, []):
            nonloss_critical.append((sig, tid))
    if nonloss_critical:
        L("### Cas signature critique sans coût net (à investiguer mais hors chiffrage)")
        L("")
        L("| Signature | trade_id | note |")
        L("|---|---|---|")
        for sig, tid in nonloss_critical:
            L(f"| `{sig}` | {tid} | bug technique rattrapé (R ≥ 0) |")
        L("")

    # Par jour
    L("## Par jour")
    L("")
    L("Colonne `ΣR coût net` = uniquement pertes sur signatures critiques.")
    L("")
    L("| Jour | n exits | CLEAN | bugs critiques (n) | ΣR coût net |")
    L("|---|---:|---:|---:|---:|")
    for day in sorted(agg["by_day"].keys()):
        b = agg["by_day"][day]
        clean = b.primary_counts.get("CLEAN", 0)
        bugs = sum(b.primary_counts.get(s, 0) for s in CRITICAL_SIGNATURES)
        bugs_loss_r = sum(b.primary_loss_sum_r.get(s, 0.0) for s in CRITICAL_SIGNATURES)
        L(f"| {day} | {b.n_exits} | {clean} | {bugs} | {bugs_loss_r:+.2f}R |")
    L("")

    # Par (broker, strat, instrument)
    L("## Par broker × stratégie × instrument (uniquement lignes avec bug critique)")
    L("")
    L("| Jour | Broker | Strat | Instr | n | Signatures bugs |")
    L("|---|---|---|---|---:|---|")
    dim_rows = []
    for (day, broker, strat, instr), b in agg["by_dim"].items():
        bug_counts = {s: b.primary_counts[s] for s in CRITICAL_SIGNATURES
                      if b.primary_counts.get(s, 0) > 0}
        if not bug_counts:
            continue
        sig_str = ", ".join(f"{s}×{n}" for s, n in bug_counts.items())
        dim_rows.append((day, broker, strat, instr, b.n_exits, sig_str))
    for row in sorted(dim_rows):
        L(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} | {row[5]} |")
    if not dim_rows:
        L("| — | — | — | — | — | aucune ligne avec bug critique |")
    L("")

    # Anomalies hors-exit
    L("## Anomalies hors-exit (événements séparés)")
    L("")
    if overall.non_exit_anomalies:
        L("| Type | n |")
        L("|---|---:|")
        for anomaly, n in overall.non_exit_anomalies.most_common():
            L(f"| `{anomaly}` | {n} |")
        L("")
        for anomaly, examples in agg["non_exit_examples"].items():
            L(f"### `{anomaly}` (jusqu'à 5 exemples)")
            for ex in examples:
                L(f"- {ex['ts']} broker={ex['broker']} instr={ex['instrument']}")
            L("")
    else:
        L("Aucune anomalie hors-exit dans la fenêtre.")
        L("")

    # Tags multiples
    L("## Tags multiples (diagnostic, hors chiffrage principal)")
    L("")
    if overall.multi_tag_examples:
        L("| trade_id | instr | broker | primary | tous tags | R | MFE |")
        L("|---|---|---|---|---|---:|---:|")
        for e in overall.multi_tag_examples:
            tags = " + ".join(e["tags"])
            L(f"| {e['trade_id']} | {e['instrument']} | {e['broker']} | `{e['primary']}` | {tags} | {e['result_r']:+.2f}R | {e['mfe_r']:.2f}R |")
    else:
        L("Aucun trade ne matche plusieurs signatures dans la fenêtre.")
    L("")

    L("---")
    L(f"_Persisté dans `{OUT_JSONL}` (append-only, 1 ligne/run)._")
    L("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit intégrité d'exécution (audit only)")
    parser.add_argument("--days", type=int, default=7,
                        help="Nombre de jours glissants (défaut : 7)")
    parser.add_argument("--since", type=str, default=None,
                        help="Date début YYYY-MM-DD (prend le pas sur --days)")
    parser.add_argument("--until", type=str, default=None,
                        help="Date fin YYYY-MM-DD (défaut : maintenant)")
    parser.add_argument("--journal", type=str, default=JOURNAL)
    parser.add_argument("--out-md", type=str, default=OUT_MD)
    parser.add_argument("--out-jsonl", type=str, default=OUT_JSONL)
    parser.add_argument("--no-write", action="store_true",
                        help="N'écrit rien sur disque (dry-run console)")
    parser.add_argument("--required-forward-days", type=float, default=7.0)
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    else:
        since = now - timedelta(days=args.days)
    until = (datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc)
             if args.until else now)

    events = load_events(args.journal, since, until)
    agg = aggregate(events)
    verdict = compute_verdict(agg, FORWARD_CUTOFF_UTC, now,
                              required_forward_days=args.required_forward_days)
    md = render_markdown(agg, verdict, since, until)

    print(md)

    if not args.no_write:
        Path(args.out_md).write_text(md)
        record = {
            "ts": now.isoformat(),
            "period_start": since.isoformat(),
            "period_end": until.isoformat(),
            "cutoff_utc": verdict["cutoff_utc"],
            "days_since_cutoff": verdict["days_since_cutoff"],
            "overall_status": verdict["overall_status"],
            "per_signature": verdict["per_signature"],
            "primary_counts": dict(agg["overall"].primary_counts),
            "primary_sum_r": {k: round(v, 4)
                              for k, v in agg["overall"].primary_sum_r.items()},
            "primary_loss_counts": dict(agg["overall"].primary_loss_counts),
            "primary_loss_sum_r": {k: round(v, 4)
                                   for k, v in agg["overall"].primary_loss_sum_r.items()},
            "primary_nonloss_counts": dict(agg["overall"].primary_nonloss_counts),
            "non_exit_anomalies": dict(agg["overall"].non_exit_anomalies),
            "n_multi_tag": len(agg["overall"].multi_tag_examples),
        }
        with open(args.out_jsonl, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        print(f"\n✓ Markdown : {args.out_md}")
        print(f"✓ Append    : {args.out_jsonl}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
