"""Arabesque — Audit edge live vs backtest pleine fenêtre, persistant.

Pour CHAQUE stratégie active, sur la fenêtre live demandée :
  - Mesure live FTMO + GFT (n, WR, Exp, ΣR)
  - Lance le backtest pleine fenêtre sur tous les instruments configurés
    (pas seulement ceux tradés en live → capte les signaux théoriques manqués)
  - Calcule Δ_Exp = live_exp − backtest_exp
  - Verdict par seuils :
      ΔExp ∈ [-0.10, +0.10]                     → ✅ edge_intact
      ΔExp ∈ [-0.30, -0.10]                     → ⚠️ drift_modere (à surveiller)
      ΔExp < -0.30 ET n_live ≥ 30               → 🔶 drift_structurel (action)
      backtest_exp < 0 ET live_exp ≈ backtest   → 🟡 regime_defavorable (live colle)
      n_live < 5                                → 💤 small_n_inconclusif

Persistance : append une ligne JSONL dans logs/edge_audit.jsonl à chaque run.
Aussi : écrit logs/edge_audit_latest.md (résumé Markdown du dernier audit) pour
relecture humaine post-compactage / post-reboot.

Invocations :
    python scripts/audit_edge_live_vs_backtest.py                  # défaut intelligent
    python scripts/audit_edge_live_vs_backtest.py --since 2026-04-13
    python scripts/audit_edge_live_vs_backtest.py --period this_month
    python scripts/audit_edge_live_vs_backtest.py --strategy cabriole
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

# Réutilise les fonctions du compare script
sys.path.insert(0, str(Path(__file__).parent.absolute()))
from compare_live_vs_backtest import run_backtest_for_instrument, resolve_period


JOURNAL = "logs/trade_journal.jsonl"
SETTINGS = "config/settings.yaml"
INSTRUMENTS_YAML = "config/instruments.yaml"
AUDIT_LOG = "logs/edge_audit.jsonl"
AUDIT_LATEST_MD = "logs/edge_audit_latest.md"

# Baselines backtest 20 mois (issues de docs/STATUS et bilans antérieurs)
BASELINES = {
    "extension": {"exp": 0.083, "wr": 75.0, "source": "20 mois multi-instruments"},
    "cabriole":  {"exp": 0.034, "wr": 78.0, "source": "20 mois 6 cryptos H4"},
    "glissade":  {"exp": 0.196, "wr": 86.0, "source": "20 mois XAUUSD+BTCUSD H1"},
    "fouette":   {"exp": 0.150, "wr": 65.0, "source": "20 mois XAUUSD London + BTCUSD NY M1"},
}


def wilson_ic95(wins: int, n: int) -> tuple[float, float]:
    """Wilson score interval 95%."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = wins / n
    denom = 1 + z*z/n
    centre = (p + z*z/(2*n)) / denom
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denom
    return (max(0.0, (centre-half)*100), min(100.0, (centre+half)*100))


def load_active_strategies() -> dict:
    """Renvoie {strategy: {timeframe, instruments}} depuis config/settings.yaml.

    Extension : si non listée, utilise tous les instruments avec follow:true
    dans instruments.yaml (comportement par défaut).
    """
    with open(SETTINGS) as f:
        cfg = yaml.safe_load(f)
    assigns = cfg.get("strategy_assignments", {}) or {}

    out = {}
    for strat, conf in assigns.items():
        out[strat] = {
            "timeframe": conf.get("timeframe"),
            "instruments": list(conf.get("instruments") or []),
        }

    if "extension" not in out:
        with open(INSTRUMENTS_YAML) as f:
            inst_cfg = yaml.safe_load(f) or {}
        instruments = []
        for sym, conf in inst_cfg.items():
            if isinstance(conf, dict) and conf.get("follow") is True:
                instruments.append(sym)
        out["extension"] = {"timeframe": None, "instruments": instruments}
    return out


def aggregate_live(start: datetime, end: datetime, strategies: list[str]) -> dict:
    """Aggrège les exits live par (strategy, broker) sur la fenêtre."""
    agg = defaultdict(lambda: {"n": 0, "wins": 0, "be": 0, "losses": 0,
                                "sumR": 0.0, "instruments": set()})
    with open(JOURNAL) as f:
        for line in f:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("event") != "exit":
                continue
            ts = e.get("ts", "")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            if not (start <= dt <= end):
                continue
            strat = e.get("strategy", "?")
            if strat not in strategies:
                continue
            broker = e.get("broker_id", "?")
            r = e.get("result_r", 0) or 0
            key = (strat, broker)
            a = agg[key]
            a["n"] += 1
            a["sumR"] += r
            if abs(r) < 0.25:
                a["be"] += 1
            elif r > 0:
                a["wins"] += 1
            else:
                a["losses"] += 1
            a["instruments"].add(e.get("instrument", "?"))
    return {k: {**v, "instruments": sorted(v["instruments"])} for k, v in agg.items()}


def run_backtest_full_window(strategy: str, instruments: list[str],
                             start_str: str, end_str: str) -> dict:
    """Lance le backtest sur tous les instruments de la stratégie sur la fenêtre."""
    total = {"n": 0, "wins": 0, "sumR": 0.0, "per_instrument": {}}
    for inst in instruments:
        bt = run_backtest_for_instrument(inst, start_str, end_str, strategy=strategy)
        if bt is None or "error" in bt:
            continue
        n = bt.get("bt_trades", 0)
        if n == 0:
            continue
        wr = bt.get("bt_wr", 0)
        sumR = bt.get("bt_total_r", 0)
        wins = round(n * wr / 100)
        total["n"] += n
        total["wins"] += wins
        total["sumR"] += sumR
        total["per_instrument"][inst] = {"n": n, "wr": wr, "sumR": sumR}
    return total


def compute_verdict(live_exp: float, bt_exp: float, n_live: int,
                    bt_exp_baseline: float) -> tuple[str, str]:
    """Retourne (code, label) selon les seuils."""
    delta = live_exp - bt_exp
    if n_live < 5:
        return ("small_n_inconclusif", "💤 Small-n inconclusif")
    # Drift structurel : live nettement pire que backtest sur n suffisant
    if delta < -0.30 and n_live >= 30:
        return ("drift_structurel", "🔶 Drift structurel — action requise")
    # Régime défavorable : backtest perd aussi, live colle
    if bt_exp < 0 and abs(delta) <= 0.20:
        return ("regime_defavorable", "🟡 Régime défavorable (live colle au backtest)")
    # Drift modéré : -0.30 ≤ delta ≤ -0.10
    if -0.30 <= delta <= -0.10:
        return ("drift_modere", "⚠️ Drift modéré (à surveiller)")
    # Edge intact
    if -0.10 < delta < 0.10:
        return ("edge_intact", "✅ Edge intact (live colle au backtest)")
    # Live nettement meilleur que backtest
    if delta >= 0.10:
        return ("live_meilleur", "✅ Live > backtest (small-n probable)")
    # Drift sévère mais small-n
    return ("drift_a_confirmer", "⚠️ Drift à confirmer (n<30)")


def audit(start: datetime, end: datetime,
          strategy_filter: str | None = None) -> dict:
    """Pipeline complet d'audit, retourne dict structuré."""
    strategies_cfg = load_active_strategies()
    if strategy_filter:
        if strategy_filter not in strategies_cfg:
            print(f"❌ Stratégie inconnue : {strategy_filter}")
            sys.exit(1)
        strategies_cfg = {strategy_filter: strategies_cfg[strategy_filter]}

    active_strats = list(strategies_cfg.keys())
    live_agg = aggregate_live(start, end, active_strats)

    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    result = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "period_start": start_str,
        "period_end": end_str,
        "strategies": {},
    }

    for strat, conf in strategies_cfg.items():
        instruments = conf["instruments"]

        live_ftmo_key = (strat, "ftmo_challenge")
        live_gft_key = (strat, "gft_compte1")
        L_ftmo = live_agg.get(live_ftmo_key, {"n": 0, "wins": 0, "be": 0, "losses": 0,
                                                "sumR": 0.0, "instruments": []})
        L_gft = live_agg.get(live_gft_key, {"n": 0, "wins": 0, "be": 0, "losses": 0,
                                              "sumR": 0.0, "instruments": []})

        n_live = L_ftmo["n"] + L_gft["n"]
        sumR_live = L_ftmo["sumR"] + L_gft["sumR"]
        live_exp = sumR_live / n_live if n_live else 0.0
        wins_live = L_ftmo["wins"] + L_gft["wins"] + 0.5 * (L_ftmo["be"] + L_gft["be"])
        live_wr = wins_live / n_live * 100 if n_live else 0.0
        ic_low, ic_high = wilson_ic95(L_ftmo["wins"] + L_gft["wins"], n_live)

        bt = run_backtest_full_window(strat, instruments, start_str, end_str)
        bt_exp = bt["sumR"] / bt["n"] if bt["n"] else 0.0
        bt_wr = bt["wins"] / bt["n"] * 100 if bt["n"] else 0.0

        baseline_exp = BASELINES.get(strat, {}).get("exp", 0.0)
        delta_vs_baseline = bt_exp - baseline_exp

        verdict_code, verdict_label = compute_verdict(live_exp, bt_exp, n_live, baseline_exp)

        result["strategies"][strat] = {
            "instruments_count": len(instruments),
            "live": {
                "ftmo": {k: L_ftmo[k] for k in ("n", "wins", "be", "losses", "sumR")},
                "gft": {k: L_gft[k] for k in ("n", "wins", "be", "losses", "sumR")},
                "total_n": n_live,
                "total_sumR": round(sumR_live, 3),
                "exp": round(live_exp, 4),
                "wr": round(live_wr, 1),
                "wr_ic95_low": round(ic_low, 1),
                "wr_ic95_high": round(ic_high, 1),
            },
            "backtest": {
                "n": bt["n"],
                "wins": bt["wins"],
                "sumR": round(bt["sumR"], 3),
                "exp": round(bt_exp, 4),
                "wr": round(bt_wr, 1),
                "per_instrument": bt["per_instrument"],
            },
            "baseline_exp": baseline_exp,
            "delta_exp_live_vs_bt": round(live_exp - bt_exp, 4),
            "delta_exp_bt_vs_baseline": round(delta_vs_baseline, 4),
            "verdict_code": verdict_code,
            "verdict_label": verdict_label,
        }

    return result


def render_markdown(audit_result: dict) -> str:
    """Renvoie un résumé Markdown lisible humain."""
    period = f"{audit_result['period_start']} → {audit_result['period_end']}"
    ts = audit_result["ts"]
    lines = [
        f"# Audit edge live vs backtest",
        f"",
        f"- **Généré** : {ts}",
        f"- **Période analysée** : {period}",
        f"",
        f"## Synthèse par stratégie",
        f"",
        f"| Strat | Live n | Live Exp | BT n | BT Exp | ΔExp | BT vs baseline | Verdict |",
        f"|---|---|---|---|---|---|---|---|",
    ]
    for strat, s in audit_result["strategies"].items():
        L = s["live"]
        B = s["backtest"]
        lines.append(
            f"| **{strat}** | {L['total_n']} (FTMO {L['ftmo']['n']}/GFT {L['gft']['n']}) "
            f"| {L['exp']:+.3f}R | {B['n']} | {B['exp']:+.3f}R "
            f"| {s['delta_exp_live_vs_bt']:+.3f}R "
            f"| {s['delta_exp_bt_vs_baseline']:+.3f}R "
            f"| {s['verdict_label']} |"
        )
    lines.append("")
    lines.append("## Détails par stratégie")
    lines.append("")
    for strat, s in audit_result["strategies"].items():
        L = s["live"]
        B = s["backtest"]
        baseline = BASELINES.get(strat, {})
        lines.append(f"### {strat}")
        lines.append("")
        lines.append(f"- **Live** : n={L['total_n']} (FTMO {L['ftmo']['n']} / GFT {L['gft']['n']}), "
                     f"WR {L['wr']}% (IC95 {L['wr_ic95_low']}-{L['wr_ic95_high']}%), "
                     f"Exp {L['exp']:+.3f}R, ΣR {L['total_sumR']:+.2f}")
        lines.append(f"- **Backtest pleine fenêtre** : n={B['n']}, WR {B['wr']}%, "
                     f"Exp {B['exp']:+.3f}R, ΣR {B['sumR']:+.2f} "
                     f"sur {s['instruments_count']} instruments")
        lines.append(f"- **Baseline 20 mois** : Exp {baseline.get('exp', 0):+.3f}R, "
                     f"WR {baseline.get('wr', 0):.0f}% ({baseline.get('source', '?')})")
        lines.append(f"- **Δ Exp live vs backtest** : {s['delta_exp_live_vs_bt']:+.3f}R/trade")
        lines.append(f"- **Δ Exp backtest vs baseline** : {s['delta_exp_bt_vs_baseline']:+.3f}R/trade "
                     f"(le marché actuel est {'défavorable' if s['delta_exp_bt_vs_baseline']<0 else 'favorable'})")
        lines.append(f"- **Verdict** : {s['verdict_label']}")
        lines.append("")
    lines.append("## Lecture")
    lines.append("")
    lines.append("- **Δ Exp live vs backtest** : si écart > -0.30R sur n≥30, drift d'exécution structurel.")
    lines.append("- **Δ Exp backtest vs baseline** : si négatif, c'est le marché qui ne donne pas, pas l'edge qui fuit.")
    lines.append("- Quand backtest perd aussi → on attend (régime défavorable), on ne stoppe pas.")
    lines.append("")
    lines.append("Persisté dans `logs/edge_audit.jsonl` (1 ligne par run, append-only).")
    return "\n".join(lines)


def render_console(audit_result: dict) -> str:
    """Tableau console concis."""
    lines = []
    lines.append("=" * 100)
    lines.append(f"  AUDIT EDGE — {audit_result['period_start']} → {audit_result['period_end']}")
    lines.append("=" * 100)
    lines.append(f"{'Strat':<12}{'Live n':<10}{'Live Exp':<12}"
                 f"{'BT n':<8}{'BT Exp':<12}{'ΔExp':<10}{'BT-base':<10}{'Verdict'}")
    lines.append("-" * 100)
    for strat, s in audit_result["strategies"].items():
        L = s["live"]
        B = s["backtest"]
        lines.append(
            f"{strat:<12}"
            f"{L['total_n']:<10}"
            f"{L['exp']:+.3f}R    "
            f"{B['n']:<8}"
            f"{B['exp']:+.3f}R    "
            f"{s['delta_exp_live_vs_bt']:+.3f}R   "
            f"{s['delta_exp_bt_vs_baseline']:+.3f}R   "
            f"{s['verdict_label']}"
        )
    lines.append("-" * 100)
    return "\n".join(lines)


def append_audit_log(audit_result: dict):
    Path("logs").mkdir(exist_ok=True)
    with open(AUDIT_LOG, "a") as f:
        f.write(json.dumps(audit_result, default=str) + "\n")


def write_latest_md(audit_result: dict):
    Path("logs").mkdir(exist_ok=True)
    md = render_markdown(audit_result)
    with open(AUDIT_LATEST_MD, "w") as f:
        f.write(md)


def render_ntfy(audit_result: dict) -> str:
    """Message ntfy court et humain (≤ 5 lignes, pas de jargon brut)."""
    period_str = f"{audit_result['period_start']} → {audit_result['period_end']}"
    strats = audit_result["strategies"]

    # Catégorise les verdicts
    bad = []      # drift_structurel (action requise)
    watch = []    # drift_modere
    regime = []   # regime_defavorable (live colle au backtest dans une période rouge)
    ok = []
    sleep = []
    for name, s in strats.items():
        v = s["verdict_code"]
        if v == "drift_structurel":
            bad.append(name)
        elif v == "drift_modere" or v == "drift_a_confirmer":
            watch.append(name)
        elif v == "regime_defavorable":
            regime.append(name)
        elif v == "edge_intact" or v == "live_meilleur":
            ok.append(name)
        else:
            sleep.append(name)

    if bad:
        head = f"🚨 Edge en danger — action requise : {', '.join(bad)}"
    elif watch:
        head = f"⚠️ Edge à surveiller : {', '.join(watch)}"
    elif regime and not ok:
        head = "🟡 Marché difficile mais l'edge tient (live colle au backtest)"
    elif ok:
        head = f"✅ Edge intact : {', '.join(ok)}"
    else:
        head = "💤 Pas assez de trades pour conclure"

    lines = [f"Audit edge {period_str}", head]
    if regime:
        lines.append(f"Régime défavorable (on attend) : {', '.join(regime)}")
    if sleep and not bad and not watch:
        lines.append(f"Stratégies inactives (pas de trade) : {', '.join(sleep)}")
    return "\n".join(lines)


def render_telegram(audit_result: dict) -> str:
    """Message Telegram détaillé mais lisible — explique les chiffres en mots."""
    period_str = f"{audit_result['period_start']} → {audit_result['period_end']}"
    lines = [
        f"📊 *Audit edge live vs backtest*",
        f"Période : {period_str}",
        f"",
        f"_L'edge mesuré en backtest tient-il en live ?_",
        f"",
    ]

    for name, s in audit_result["strategies"].items():
        L = s["live"]
        B = s["backtest"]
        baseline_exp = BASELINES.get(name, {}).get("exp", 0.0)
        verdict = s["verdict_label"]
        delta = s["delta_exp_live_vs_bt"]
        delta_baseline = s["delta_exp_bt_vs_baseline"]
        n_live = L["total_n"]

        lines.append(f"*{name.upper()}* — {verdict}")

        if n_live == 0:
            lines.append(f"  Aucun trade live sur la période.")
            if B["n"] == 0:
                lines.append(f"  Backtest aussi : 0 signal — conditions de marché.")
            else:
                lines.append(f"  Backtest : {B['n']} signaux théoriques (Exp {B['exp']:+.2f}R).")
            lines.append("")
            continue

        lines.append(
            f"  Live : {n_live} trades (FTMO {L['ftmo']['n']} / GFT {L['gft']['n']}), "
            f"Exp {L['exp']:+.2f}R/trade"
        )
        lines.append(
            f"  Backtest pleine fenêtre : {B['n']} trades, Exp {B['exp']:+.2f}R/trade"
        )
        lines.append(f"  Baseline 20 mois : Exp {baseline_exp:+.2f}R/trade")

        # Explication en mots
        if s["verdict_code"] == "drift_structurel":
            lines.append(
                f"  ❗ Live perd {abs(delta):.2f}R/trade de plus que le backtest. "
                f"Sur n={n_live} trades, c'est significatif → l'exécution mange l'edge."
            )
        elif s["verdict_code"] == "drift_modere":
            lines.append(
                f"  Live un peu pire que backtest ({delta:+.2f}R/trade). À surveiller."
            )
        elif s["verdict_code"] == "drift_a_confirmer":
            lines.append(
                f"  Écart {delta:+.2f}R/trade mais small-n (n={n_live}). À reverifier."
            )
        elif s["verdict_code"] == "regime_defavorable":
            lines.append(
                f"  Backtest perd aussi ({delta_baseline:+.2f}R sous baseline). "
                f"C'est le marché, pas l'edge. On attend."
            )
        elif s["verdict_code"] == "edge_intact":
            lines.append(f"  Live colle au backtest ({delta:+.2f}R écart) — edge conservé.")
        elif s["verdict_code"] == "live_meilleur":
            lines.append(f"  Live mieux que backtest (small-n).")
        elif s["verdict_code"] == "small_n_inconclusif":
            lines.append(f"  Trop peu de trades (n={n_live}) — pas de conclusion.")
        lines.append("")

    lines.append("📁 Détails persistants : `logs/edge_audit_latest.md`")
    return "\n".join(lines)


def send_notifications(audit_result: dict) -> None:
    """Envoie ntfy (court) et Telegram (détaillé) séparément.

    Filtre les channels par préfixe : 'ntfy' → ntfy, 'tgram' → Telegram.
    Ne notifie pas si tout est en `edge_intact`/`small_n` (pas de bruit inutile).
    """
    # Décide si on notifie : seulement s'il y a au moins un drift ou un regime
    verdicts = [s["verdict_code"] for s in audit_result["strategies"].values()]
    interesting = any(
        v in ("drift_structurel", "drift_modere", "drift_a_confirmer", "regime_defavorable")
        for v in verdicts
    )
    has_data = any(v not in ("small_n_inconclusif",) for v in verdicts)

    if not has_data:
        print("(Pas de données suffisantes — notification non envoyée)")
        return
    if not interesting:
        print("(Aucun drift ni régime particulier — notification non envoyée)")
        return

    try:
        import asyncio, yaml, apprise
        secrets_path = Path(__file__).resolve().parent.parent / "config" / "secrets.yaml"
        secrets = yaml.safe_load(secrets_path.read_text()) or {}
        channels = secrets.get("notifications", {}).get("channels", []) or []

        ntfy_channels = [c for c in channels if isinstance(c, str) and c.startswith("ntfy")]
        telegram_channels = [c for c in channels if isinstance(c, str) and c.startswith("tgram")]

        ntfy_body = render_ntfy(audit_result)
        telegram_body = render_telegram(audit_result)

        async def _send_all():
            tasks = []
            if ntfy_channels:
                ap_n = apprise.Apprise()
                for ch in ntfy_channels:
                    ap_n.add(ch)
                tasks.append(ap_n.async_notify(body=ntfy_body, title="Audit edge Arabesque"))
            if telegram_channels:
                ap_t = apprise.Apprise()
                for ch in telegram_channels:
                    ap_t.add(ch)
                tasks.append(ap_t.async_notify(body=telegram_body, title="📊 Audit edge Arabesque",
                                                body_format=apprise.NotifyFormat.MARKDOWN))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return results

        results = asyncio.run(_send_all())
        ok_n = "✅" if (ntfy_channels and results and results[0] is True) else ("—" if not ntfy_channels else "❌")
        ok_t = "✅" if (telegram_channels and results and results[-1] is True) else ("—" if not telegram_channels else "❌")
        print(f"Notif ntfy: {ok_n}  |  Telegram: {ok_t}")
    except Exception as e:
        print(f"Notification error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Audit edge live vs backtest pleine fenêtre")
    parser.add_argument("--since", type=str, help="Date de début (YYYY-MM-DD)")
    parser.add_argument("--until", type=str, help="Date de fin (YYYY-MM-DD)")
    parser.add_argument("--period", type=str,
                        choices=["today", "yesterday", "this_week", "this_month",
                                 "prev_month", "3m", "12m"],
                        help="Preset de période")
    parser.add_argument("--strategy", type=str, default=None,
                        help="Filtre une seule stratégie (sinon : toutes les actives)")
    parser.add_argument("--no-persist", action="store_true",
                        help="Ne pas écrire dans logs/edge_audit.jsonl")
    parser.add_argument("--notify", action="store_true",
                        help="Envoyer notif ntfy (court) + Telegram (détaillé) si drift/régime")
    args = parser.parse_args()

    if args.period:
        start, end = resolve_period(args.period)
    elif args.since:
        start = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.until:
            end = datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            end = datetime.now(timezone.utc)
    else:
        # Défaut : 30 derniers jours
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=30)

    print(f"\n🔬 Audit edge live vs backtest sur {start.date()} → {end.date()}\n")
    audit_result = audit(start, end, strategy_filter=args.strategy)

    print(render_console(audit_result))
    print()

    if not args.no_persist:
        append_audit_log(audit_result)
        write_latest_md(audit_result)
        print(f"✓ Append : {AUDIT_LOG}")
        print(f"✓ Markdown : {AUDIT_LATEST_MD}")

    if args.notify:
        send_notifications(audit_result)


if __name__ == "__main__":
    main()
