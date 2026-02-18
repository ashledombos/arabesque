"""
Arabesque v2 — Pipeline de screening multi-instrument (v2).

Placement : arabesque/backtest/pipeline.py

Sortie compacte par défaut. Auto-export JSONL horodaté.
Alertes pour données manquantes. Groupement par catégorie.

Usage :
    from arabesque.backtest.pipeline import Pipeline, PipelineConfig
    result = Pipeline().run()  # Tous les instruments FTMO avec Parquet dispo
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class PipelineConfig:
    """Configuration du pipeline."""
    # Stage 1 (signal count)
    min_signals: int = 50
    min_bars: int = 2000

    # Stage 2 (IS backtest)
    min_trades_is: int = 30
    min_expectancy_r: float = -0.10
    min_profit_factor: float = 0.8
    max_dd_pct: float = 10.0
    max_rejection_rate: float = 0.90

    # Stage 3 (full IS+OOS+stats)
    n_simulations: int = 10_000
    split_pct: float = 0.70

    # Data
    period: str = "730d"
    strategy: str = "combined"
    data_root: str | None = None   # Parquet root (auto-détecté si None)

    # Output
    output_dir: str = "results"
    verbose: bool = False          # Détails par instrument
    auto_json: bool = True         # Export JSONL automatique


@dataclass
class StageResult:
    instrument: str
    stage: int
    passed: bool
    reason: str = ""
    duration_s: float = 0.0
    data_source: str = ""          # "parquet" ou "yahoo"
    category: str = ""
    metrics: dict = field(default_factory=dict)


@dataclass
class PipelineResult:
    total_instruments: int = 0
    tested_instruments: int = 0
    stage1_passed: list[str] = field(default_factory=list)
    stage2_passed: list[str] = field(default_factory=list)
    stage3_passed: list[str] = field(default_factory=list)
    all_results: dict[str, StageResult] = field(default_factory=dict)
    alerts: list[str] = field(default_factory=list)
    total_duration_s: float = 0.0
    json_path: str = ""


class Pipeline:
    """Pipeline de screening v2 — compact, auto-JSON, alertes."""

    def __init__(self, config: PipelineConfig | None = None):
        self.cfg = config or PipelineConfig()

    def run(self, instruments: list[str] | None = None) -> PipelineResult:
        """Lance le pipeline.

        Si instruments=None, utilise tous les instruments FTMO
        qui ont des données Parquet disponibles.
        """
        from arabesque.backtest.data import (
            list_available_parquet, list_all_ftmo_instruments, _categorize,
        )

        result = PipelineResult()
        t0 = time.time()

        # ── Résolution des instruments ──
        if instruments is None:
            available = list_available_parquet(self.cfg.data_root)
            instruments = sorted(available.keys())
            if not instruments:
                result.alerts.append(
                    "AUCUN fichier Parquet 1h trouvé. "
                    "Vérifier ARABESQUE_DATA_ROOT ou barres_au_sol/data"
                )
                self._print_result(result)
                return result
        else:
            available = list_available_parquet(self.cfg.data_root)

        result.total_instruments = len(instruments)

        # Catégoriser
        categories: dict[str, list[str]] = {}
        for inst in instruments:
            cat = _categorize(inst)
            categories.setdefault(cat, []).append(inst)

        # Alertes pour instruments sans Parquet
        no_parquet = [i for i in instruments if i not in available]
        if no_parquet:
            cats: dict[str, list[str]] = {}
            for i in no_parquet:
                c = _categorize(i)
                cats.setdefault(c, []).append(i)
            for cat, insts in sorted(cats.items()):
                result.alerts.append(
                    f"PAS DE PARQUET ({cat}): {', '.join(insts)} -> fallback Yahoo"
                )

        # ── Header compact ──
        print(f"\n  ARABESQUE PIPELINE — {len(instruments)} instruments")
        for cat in sorted(categories.keys()):
            insts = categories[cat]
            preview = ', '.join(insts[:5])
            suffix = f'...' if len(insts) > 5 else ''
            print(f"    {cat:12s}: {len(insts):2d} ({preview}{suffix})")
        print()

        # ── Stage 1 : Signal Count ──
        s1_survivors = []
        s1_eliminated: dict[str, list] = {}
        for inst in instruments:
            sr = self._stage1(inst)
            sr.category = _categorize(inst)
            result.all_results[inst] = sr
            if sr.passed:
                s1_survivors.append(inst)
                result.stage1_passed.append(inst)
            else:
                s1_eliminated.setdefault(sr.category, []).append((inst, sr.reason))

        print(f"  Stage 1 (signals) : {len(s1_survivors)}/{len(instruments)} passed")
        if self.cfg.verbose and s1_eliminated:
            for cat, items in sorted(s1_eliminated.items()):
                for inst, reason in items:
                    print(f"    x {inst:12s} [{cat}] {reason}")

        # ── Stage 2 : IS Backtest ──
        s2_survivors = []
        s2_eliminated: dict[str, list] = {}
        for inst in s1_survivors:
            sr = self._stage2(inst)
            sr.category = _categorize(inst)
            result.all_results[inst] = sr
            if sr.passed:
                s2_survivors.append(inst)
                result.stage2_passed.append(inst)
            else:
                s2_eliminated.setdefault(sr.category, []).append((inst, sr.reason))

        print(f"  Stage 2 (IS test) : {len(s2_survivors)}/{len(s1_survivors)} passed")
        if s2_eliminated:
            for cat, items in sorted(s2_eliminated.items()):
                for inst, reason in items:
                    print(f"    x {inst:12s} [{cat}] {reason}")

        # ── Stage 3 : Full IS+OOS+Stats ──
        if s2_survivors:
            print(f"\n  Stage 3 (OOS + stats) :")
        for inst in s2_survivors:
            sr = self._stage3(inst)
            sr.category = _categorize(inst)
            result.all_results[inst] = sr
            if sr.passed:
                result.stage3_passed.append(inst)

        result.tested_instruments = len(instruments)
        result.total_duration_s = time.time() - t0

        # ── Synthèse ──
        self._print_result(result)

        # ── Auto-export JSON ──
        if self.cfg.auto_json:
            result.json_path = self._export_json(result)

        return result

    # ── Stages ───────────────────────────────────────────────────

    def _stage1(self, instrument: str) -> StageResult:
        """Stage 1 : Compte signaux, vérifie données suffisantes."""
        t0 = time.time()
        try:
            from arabesque.backtest.data import load_ohlc, get_last_source_info
            from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
            from arabesque.backtest.signal_gen import BacktestSignalGenerator, SignalGenConfig

            df = load_ohlc(instrument, period=self.cfg.period, data_root=self.cfg.data_root)
            src = get_last_source_info()
            data_source = src.source if src else "unknown"

            if len(df) < self.cfg.min_bars:
                return StageResult(instrument=instrument, stage=1, passed=False,
                    reason=f"INSUFFICIENT_DATA ({len(df)} bars < {self.cfg.min_bars})",
                    duration_s=time.time()-t0, data_source=data_source)

            if self.cfg.strategy == "combined":
                sig_gen = CombinedSignalGenerator()
            else:
                sig_gen = BacktestSignalGenerator(SignalGenConfig())

            df_prepared = sig_gen.prepare(df)
            signals = sig_gen.generate_signals(df_prepared, instrument)

            if len(signals) < self.cfg.min_signals:
                return StageResult(instrument=instrument, stage=1, passed=False,
                    reason=f"TOO_FEW_SIGNALS ({len(signals)} < {self.cfg.min_signals})",
                    duration_s=time.time()-t0, data_source=data_source,
                    metrics={"signals": len(signals), "bars": len(df)})

            return StageResult(instrument=instrument, stage=1, passed=True,
                duration_s=time.time()-t0, data_source=data_source,
                metrics={"signals": len(signals), "bars": len(df)})

        except Exception as e:
            return StageResult(instrument=instrument, stage=1, passed=False,
                reason=f"ERROR: {e}", duration_s=time.time()-t0)

    def _stage2(self, instrument: str) -> StageResult:
        """Stage 2 : Backtest IS rapide."""
        t0 = time.time()
        try:
            from arabesque.backtest.runner import BacktestRunner, BacktestConfig
            from arabesque.backtest.data import load_ohlc, split_in_out_sample, get_last_source_info
            from arabesque.backtest.signal_gen import BacktestSignalGenerator, SignalGenConfig
            from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator

            bt_cfg = BacktestConfig(verbose=False)
            df = load_ohlc(instrument, period=self.cfg.period, data_root=self.cfg.data_root)
            src = get_last_source_info()
            data_source = src.source if src else "unknown"

            if self.cfg.strategy == "combined":
                sig_gen = CombinedSignalGenerator()
            else:
                sig_gen = BacktestSignalGenerator(SignalGenConfig())

            df_prepared = sig_gen.prepare(df)
            df_in, _ = split_in_out_sample(df_prepared, self.cfg.split_pct)

            runner = BacktestRunner(bt_cfg, signal_generator=sig_gen)
            result = runner.run(df_in, instrument, "in_sample")
            m = result.metrics

            checks = [
                (m.n_trades < self.cfg.min_trades_is,
                 f"INSUFFICIENT_TRADES ({m.n_trades} < {self.cfg.min_trades_is})"),
                (m.expectancy_r < self.cfg.min_expectancy_r,
                 f"CLEARLY_NEGATIVE (exp={m.expectancy_r:+.3f}R)"),
                (m.profit_factor < self.cfg.min_profit_factor,
                 f"NO_EDGE (PF={m.profit_factor:.2f})"),
                (m.max_dd_pct > self.cfg.max_dd_pct,
                 f"PROP_INCOMPATIBLE (DD={m.max_dd_pct:.1f}%)"),
            ]
            if m.n_signals_generated > 0:
                rej_rate = m.n_signals_rejected / m.n_signals_generated
                checks.append((rej_rate > self.cfg.max_rejection_rate,
                    f"TOO_MANY_REJECTIONS ({rej_rate:.0%})"))

            for failed, reason in checks:
                if failed:
                    return StageResult(instrument=instrument, stage=2, passed=False,
                        reason=reason, duration_s=time.time()-t0, data_source=data_source,
                        metrics={"trades": m.n_trades, "exp": round(m.expectancy_r, 4),
                                 "pf": round(m.profit_factor, 2), "dd": round(m.max_dd_pct, 1)})

            return StageResult(instrument=instrument, stage=2, passed=True,
                duration_s=time.time()-t0, data_source=data_source,
                metrics={"trades": m.n_trades, "wr": round(m.win_rate, 3),
                         "exp": round(m.expectancy_r, 4), "pf": round(m.profit_factor, 2),
                         "dd": round(m.max_dd_pct, 1)})

        except Exception as e:
            return StageResult(instrument=instrument, stage=2, passed=False,
                reason=f"ERROR: {e}", duration_s=time.time()-t0)

    def _stage3(self, instrument: str) -> StageResult:
        """Stage 3 : Full IS+OOS + stats."""
        t0 = time.time()
        try:
            from arabesque.backtest.runner import run_backtest
            from arabesque.backtest.stats import (
                wilson_score_interval, bootstrap_expectancy, monte_carlo_drawdown,
            )
            from arabesque.backtest.data import get_last_source_info

            result_in, result_out = run_backtest(
                instrument, period=self.cfg.period,
                verbose=False, strategy=self.cfg.strategy,
            )

            src = get_last_source_info()
            data_source = src.source if src else "unknown"
            m_in = result_in.metrics
            m_out = result_out.metrics

            # Stats rapides
            results_r = [p.result_r for p in result_out.closed_positions
                        if p.result_r is not None]
            stats = {}
            if results_r:
                wins = sum(1 for r in results_r if r > 0)
                w = wilson_score_interval(wins, len(results_r))
                b = bootstrap_expectancy(results_r, self.cfg.n_simulations)
                d = monte_carlo_drawdown(results_r, n_simulations=self.cfg.n_simulations)
                stats = {
                    "wr_ci95": f"[{w.ci95_low:.1%},{w.ci95_high:.1%}]",
                    "exp_ci95": f"[{b.ci95_low:+.3f},{b.ci95_high:+.3f}]",
                    "p_exp_pos": round(b.p_positive, 3),
                    "dd_p95": round(d.p95_dd, 1),
                    "ftmo_ok": bool(d.ftmo_compatible),
                    "edge_sig": bool(b.significant_at_95),
                }

            viable = bool(
                m_out.n_trades >= 30
                and m_out.expectancy_r > 0
                and m_out.profit_factor >= 1.0
                and m_out.n_disqualifying_days == 0
            )

            # Verdict court
            if stats.get("edge_sig") and stats.get("ftmo_ok"):
                verdict = "EDGE+FTMO"
            elif stats.get("ftmo_ok") and not stats.get("edge_sig"):
                verdict = "FTMO_OK_EDGE?"
            elif stats.get("edge_sig") and not stats.get("ftmo_ok"):
                verdict = "EDGE_OK_DD!"
            else:
                verdict = "WEAK"

            metrics = {
                # IS
                "trades_is": m_in.n_trades,
                "wr_is": round(m_in.win_rate, 3),
                "exp_is": round(m_in.expectancy_r, 4),
                "pf_is": round(m_in.profit_factor, 2),
                "dd_is": round(m_in.max_dd_pct, 1),
                "disqual_is": m_in.n_disqualifying_days,
                # OOS
                "trades_oos": m_out.n_trades,
                "wr_oos": round(m_out.win_rate, 3),
                "exp_oos": round(m_out.expectancy_r, 4),
                "pf_oos": round(m_out.profit_factor, 2),
                "dd_oos": round(m_out.max_dd_pct, 1),
                "disqual": m_out.n_disqualifying_days,
                "verdict": verdict,
                **stats,
            }

            # Affichage ligne compacte
            src_tag = "P" if data_source == "parquet" else "Y"
            status = "V" if viable else "x"
            print(f"    {status} {instrument:12s} [{src_tag}] "
                  f"{m_out.n_trades:3d}t  WR={m_out.win_rate:.0%}  "
                  f"E={m_out.expectancy_r:+.3f}R  PF={m_out.profit_factor:.2f}  "
                  f"DD={m_out.max_dd_pct:.1f}%  {verdict}")

            return StageResult(instrument=instrument, stage=3, passed=viable,
                duration_s=time.time()-t0, data_source=data_source,
                metrics=metrics)

        except Exception as e:
            print(f"    x {instrument:12s} ERROR: {e}")
            return StageResult(instrument=instrument, stage=3, passed=False,
                reason=f"ERROR: {e}", duration_s=time.time()-t0)

    # ── Affichage ────────────────────────────────────────────────

    def _print_result(self, r: PipelineResult):
        """Synthèse compacte."""
        print(f"\n  {'='*55}")
        print(f"  RESUME  {r.tested_instruments} testes -> "
              f"S1:{len(r.stage1_passed)} -> S2:{len(r.stage2_passed)} -> "
              f"S3:{len(r.stage3_passed)} viables  "
              f"({r.total_duration_s:.0f}s)")
        print(f"  {'='*55}")

        if r.stage3_passed:
            # Grouper par catégorie
            by_cat: dict[str, list[str]] = {}
            for inst in r.stage3_passed:
                sr = r.all_results.get(inst)
                cat = sr.category if sr else "other"
                by_cat.setdefault(cat, []).append(inst)

            print(f"  VIABLES :")
            for cat in sorted(by_cat.keys()):
                print(f"    {cat:12s}: {', '.join(by_cat[cat])}")
        else:
            print(f"  AUCUN INSTRUMENT VIABLE")

        if r.alerts:
            print(f"\n  ALERTES :")
            for alert in r.alerts:
                print(f"    ! {alert}")

        print()

    # ── Export JSON ──────────────────────────────────────────────

    def _export_json(self, r: PipelineResult) -> str:
        """Export JSONL horodaté dans results/."""
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        path = os.path.join(self.cfg.output_dir, f"pipeline_{ts}.jsonl")

        with open(path, "w") as f:
            meta = {
                "type": "pipeline_metadata",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total_instruments": r.total_instruments,
                "stages": [len(r.stage1_passed), len(r.stage2_passed), len(r.stage3_passed)],
                "duration_s": round(r.total_duration_s, 1),
                "config": {
                    "strategy": self.cfg.strategy,
                    "period": self.cfg.period,
                    "min_signals": self.cfg.min_signals,
                    "min_trades_is": self.cfg.min_trades_is,
                    "min_expectancy_r": self.cfg.min_expectancy_r,
                    "split_pct": self.cfg.split_pct,
                },
                "viable": r.stage3_passed,
                "alerts": r.alerts,
            }
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")

            for inst, sr in r.all_results.items():
                entry = {
                    "type": "instrument",
                    "instrument": inst,
                    "category": sr.category,
                    "data_source": sr.data_source,
                    "stage_reached": sr.stage,
                    "passed": sr.passed,
                    "reason": sr.reason,
                    "duration_s": round(sr.duration_s, 2),
                    "metrics": sr.metrics,
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        print(f"  JSON -> {path}")
        return path
