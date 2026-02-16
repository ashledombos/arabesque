"""
Arabesque v2 — Pipeline de pre-filtering multi-instrument.

Placement : arabesque/backtest/pipeline.py

Architecture en 3 stages pour tester efficacement 120+ instruments :

Stage 1 — Signal Count (2s/instrument)
  Compte les signaux bruts, élimine si < 50 signaux ou < 2000 barres.

Stage 2 — IS Only Backtest (10s/instrument)
  Backtest sur in-sample uniquement, élimine si clairement non viable.

Stage 3 — Full IS+OOS + Stats (30s/instrument)
  Backtest complet avec analyse statistique avancée.

Gain d'efficacité :
  120 instruments → ~14 survivors
  Temps : 19 minutes au lieu de 90 minutes

Usage :
    from arabesque.backtest.pipeline import Pipeline, PipelineConfig
    
    pipeline = Pipeline(PipelineConfig())
    results = pipeline.run(instruments=["EURUSD", "GBPUSD", "XAUUSD", ...])
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import time


@dataclass
class PipelineConfig:
    """Configuration du pipeline de screening."""
    # Stage 1 thresholds
    min_signals: int = 50           # Minimum signaux bruts
    min_bars: int = 2000            # Minimum barres de données (~3 mois 1H)

    # Stage 2 thresholds (IS only)
    min_trades_is: int = 30         # Minimum trades en IS
    min_expectancy_r: float = -0.10 # Éliminer si clairement négatif
    min_profit_factor: float = 0.8  # Pas d'edge visible
    max_dd_pct: float = 10.0        # Incompatible prop firm
    max_rejection_rate: float = 0.90  # Trop de signaux rejetés

    # Stage 3 config
    n_simulations: int = 10_000     # Monte Carlo simulations
    split_pct: float = 0.70         # IS/OOS split

    # Data
    period: str = "730d"            # 2 ans de données
    strategy: str = "combined"


@dataclass
class StageResult:
    """Résultat d'un stage pour un instrument."""
    instrument: str
    stage: int
    passed: bool
    reason: str = ""
    duration_s: float = 0.0
    metrics: dict = field(default_factory=dict)


@dataclass
class PipelineResult:
    """Résultat complet du pipeline."""
    total_instruments: int = 0
    stage1_passed: list[str] = field(default_factory=list)
    stage2_passed: list[str] = field(default_factory=list)
    stage3_passed: list[str] = field(default_factory=list)
    stage1_eliminated: list[StageResult] = field(default_factory=list)
    stage2_eliminated: list[StageResult] = field(default_factory=list)
    stage3_results: dict[str, Any] = field(default_factory=dict)
    total_duration_s: float = 0.0


class Pipeline:
    """Pipeline de screening multi-instrument en 3 stages."""

    def __init__(self, config: PipelineConfig | None = None):
        self.cfg = config or PipelineConfig()

    def run(self, instruments: list[str]) -> PipelineResult:
        """Exécute le pipeline complet sur la liste d'instruments."""
        result = PipelineResult(total_instruments=len(instruments))
        t0 = time.time()

        print(f"\n{'='*60}")
        print(f"  ARABESQUE PIPELINE — {len(instruments)} instruments")
        print(f"{'='*60}")

        # ── Stage 1 : Signal Count ──
        print(f"\n--- STAGE 1 : Signal Count ---")
        stage1_survivors = []
        for inst in instruments:
            sr = self._stage1(inst)
            if sr.passed:
                stage1_survivors.append(inst)
                result.stage1_passed.append(inst)
            else:
                result.stage1_eliminated.append(sr)

        print(f"  Stage 1 : {len(stage1_survivors)}/{len(instruments)} passed "
              f"({len(instruments) - len(stage1_survivors)} eliminated)")

        # ── Stage 2 : IS Only Backtest ──
        print(f"\n--- STAGE 2 : IS Backtest ---")
        stage2_survivors = []
        for inst in stage1_survivors:
            sr = self._stage2(inst)
            if sr.passed:
                stage2_survivors.append(inst)
                result.stage2_passed.append(inst)
            else:
                result.stage2_eliminated.append(sr)

        print(f"  Stage 2 : {len(stage2_survivors)}/{len(stage1_survivors)} passed "
              f"({len(stage1_survivors) - len(stage2_survivors)} eliminated)")

        # ── Stage 3 : Full IS+OOS + Stats ──
        print(f"\n--- STAGE 3 : Full Analysis ---")
        for inst in stage2_survivors:
            sr = self._stage3(inst)
            result.stage3_results[inst] = sr
            if sr.passed:
                result.stage3_passed.append(inst)

        print(f"  Stage 3 : {len(result.stage3_passed)}/{len(stage2_survivors)} viable")

        result.total_duration_s = time.time() - t0

        # ── Synthèse ──
        self._print_summary(result)

        return result

    def _stage1(self, instrument: str) -> StageResult:
        """Stage 1 : Compte les signaux bruts sans exécuter de backtest."""
        t0 = time.time()
        try:
            from arabesque.backtest.data import load_ohlc, yahoo_symbol
            from arabesque.backtest.signal_gen import BacktestSignalGenerator, SignalGenConfig
            from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator

            symbol = yahoo_symbol(instrument)
            df = load_ohlc(symbol, period=self.cfg.period)

            if len(df) < self.cfg.min_bars:
                return StageResult(
                    instrument=instrument, stage=1, passed=False,
                    reason=f"INSUFFICIENT_DATA ({len(df)} bars < {self.cfg.min_bars})",
                    duration_s=time.time() - t0,
                )

            # Compter les signaux
            if self.cfg.strategy == "combined":
                sig_gen = CombinedSignalGenerator()
            else:
                sig_gen = BacktestSignalGenerator(SignalGenConfig())

            df_prepared = sig_gen.prepare(df)
            signals = sig_gen.generate_signals(df_prepared, instrument)
            n_signals = len(signals)

            if n_signals < self.cfg.min_signals:
                return StageResult(
                    instrument=instrument, stage=1, passed=False,
                    reason=f"TOO_FEW_SIGNALS ({n_signals} < {self.cfg.min_signals})",
                    duration_s=time.time() - t0,
                    metrics={"signals": n_signals, "bars": len(df)},
                )

            return StageResult(
                instrument=instrument, stage=1, passed=True,
                duration_s=time.time() - t0,
                metrics={"signals": n_signals, "bars": len(df)},
            )

        except Exception as e:
            return StageResult(
                instrument=instrument, stage=1, passed=False,
                reason=f"ERROR: {e}", duration_s=time.time() - t0,
            )

    def _stage2(self, instrument: str) -> StageResult:
        """Stage 2 : Backtest IS uniquement, filtrage rapide."""
        t0 = time.time()
        try:
            from arabesque.backtest.runner import BacktestRunner, BacktestConfig
            from arabesque.backtest.data import load_ohlc, split_in_out_sample, yahoo_symbol
            from arabesque.backtest.signal_gen import BacktestSignalGenerator, SignalGenConfig
            from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator

            bt_cfg = BacktestConfig(verbose=False)
            symbol = yahoo_symbol(instrument)
            df = load_ohlc(symbol, period=self.cfg.period)

            if self.cfg.strategy == "combined":
                sig_gen = CombinedSignalGenerator()
            else:
                sig_gen = BacktestSignalGenerator(SignalGenConfig())

            df_prepared = sig_gen.prepare(df)
            df_in, _ = split_in_out_sample(df_prepared, self.cfg.split_pct)

            runner = BacktestRunner(bt_cfg, signal_generator=sig_gen)
            result = runner.run(df_in, instrument, "in_sample")
            m = result.metrics

            # Vérifications
            if m.n_trades < self.cfg.min_trades_is:
                reason = f"INSUFFICIENT_DATA ({m.n_trades} trades < {self.cfg.min_trades_is})"
                return StageResult(instrument=instrument, stage=2, passed=False,
                                   reason=reason, duration_s=time.time() - t0,
                                   metrics={"trades": m.n_trades})

            if m.expectancy_r < self.cfg.min_expectancy_r:
                reason = f"CLEARLY_NEGATIVE (exp={m.expectancy_r:+.3f}R < {self.cfg.min_expectancy_r})"
                return StageResult(instrument=instrument, stage=2, passed=False,
                                   reason=reason, duration_s=time.time() - t0,
                                   metrics={"trades": m.n_trades, "exp": m.expectancy_r})

            if m.profit_factor < self.cfg.min_profit_factor:
                reason = f"NO_EDGE (PF={m.profit_factor:.2f} < {self.cfg.min_profit_factor})"
                return StageResult(instrument=instrument, stage=2, passed=False,
                                   reason=reason, duration_s=time.time() - t0,
                                   metrics={"trades": m.n_trades, "pf": m.profit_factor})

            if m.max_dd_pct > self.cfg.max_dd_pct:
                reason = f"PROP_INCOMPATIBLE (DD={m.max_dd_pct:.1f}% > {self.cfg.max_dd_pct}%)"
                return StageResult(instrument=instrument, stage=2, passed=False,
                                   reason=reason, duration_s=time.time() - t0,
                                   metrics={"trades": m.n_trades, "dd": m.max_dd_pct})

            total_signals = m.n_signals_generated
            if total_signals > 0:
                rejection_rate = m.n_signals_rejected / total_signals
                if rejection_rate > self.cfg.max_rejection_rate:
                    reason = f"TOO_MANY_REJECTIONS ({rejection_rate:.0%} > {self.cfg.max_rejection_rate:.0%})"
                    return StageResult(instrument=instrument, stage=2, passed=False,
                                       reason=reason, duration_s=time.time() - t0)

            return StageResult(
                instrument=instrument, stage=2, passed=True,
                duration_s=time.time() - t0,
                metrics={
                    "trades": m.n_trades,
                    "wr": round(m.win_rate, 3),
                    "exp": round(m.expectancy_r, 4),
                    "pf": round(m.profit_factor, 2),
                    "dd": round(m.max_dd_pct, 1),
                },
            )

        except Exception as e:
            return StageResult(
                instrument=instrument, stage=2, passed=False,
                reason=f"ERROR: {e}", duration_s=time.time() - t0,
            )

    def _stage3(self, instrument: str) -> StageResult:
        """Stage 3 : Full IS+OOS + analyse statistique."""
        t0 = time.time()
        try:
            from arabesque.backtest.runner import run_backtest, BacktestConfig
            from arabesque.backtest.stats import full_statistical_analysis

            bt_cfg = BacktestConfig(verbose=False)

            result_in, result_out = run_backtest(
                instrument,
                period=self.cfg.period,
                bt_config=bt_cfg,
                split_pct=self.cfg.split_pct,
                verbose=False,
                strategy=self.cfg.strategy,
            )

            m_out = result_out.metrics

            # Analyse statistique
            results_r = [p.result_r for p in result_out.closed_positions
                        if p.result_r is not None]

            stats_report = ""
            if results_r:
                stats_report = full_statistical_analysis(
                    results_r,
                    risk_per_trade_pct=bt_cfg.risk_per_trade_pct,
                    start_balance=bt_cfg.start_balance,
                    n_simulations=self.cfg.n_simulations,
                )

            viable = (
                m_out.n_trades >= 30
                and m_out.expectancy_r > 0
                and m_out.profit_factor >= 1.0
                and m_out.n_disqualifying_days == 0
            )

            print(f"  {instrument:12s} : {m_out.n_trades} trades, "
                  f"exp={m_out.expectancy_r:+.3f}R, PF={m_out.profit_factor:.2f}, "
                  f"DD={m_out.max_dd_pct:.1f}%  {'✓ VIABLE' if viable else '✗'}")

            if stats_report:
                # Afficher juste les lignes clés
                for line in stats_report.split("\n"):
                    if "VERDICT" in line:
                        print(f"    {line.strip()}")

            return StageResult(
                instrument=instrument, stage=3, passed=viable,
                duration_s=time.time() - t0,
                metrics={
                    "trades_is": result_in.metrics.n_trades,
                    "trades_oos": m_out.n_trades,
                    "wr_oos": round(m_out.win_rate, 3),
                    "exp_oos": round(m_out.expectancy_r, 4),
                    "pf_oos": round(m_out.profit_factor, 2),
                    "dd_oos": round(m_out.max_dd_pct, 1),
                    "disqual": m_out.n_disqualifying_days,
                    "stats_report": stats_report,
                },
            )

        except Exception as e:
            return StageResult(
                instrument=instrument, stage=3, passed=False,
                reason=f"ERROR: {e}", duration_s=time.time() - t0,
            )

    def _print_summary(self, result: PipelineResult):
        """Affiche la synthèse du pipeline."""
        print(f"\n{'='*60}")
        print(f"  PIPELINE SUMMARY")
        print(f"{'='*60}")
        print(f"  Total instruments : {result.total_instruments}")
        print(f"  Stage 1 passed    : {len(result.stage1_passed)}")
        print(f"  Stage 2 passed    : {len(result.stage2_passed)}")
        print(f"  Stage 3 viable    : {len(result.stage3_passed)}")
        print(f"  Duration          : {result.total_duration_s:.0f}s "
              f"({result.total_duration_s/60:.1f}min)")

        if result.stage3_passed:
            print(f"\n  VIABLE INSTRUMENTS : {', '.join(result.stage3_passed)}")
        else:
            print(f"\n  AUCUN INSTRUMENT VIABLE")

        # Résumé des éliminations
        if result.stage1_eliminated:
            print(f"\n  Stage 1 eliminations :")
            for sr in result.stage1_eliminated[:10]:
                print(f"    {sr.instrument:12s} : {sr.reason}")
            if len(result.stage1_eliminated) > 10:
                print(f"    ... et {len(result.stage1_eliminated) - 10} autres")

        if result.stage2_eliminated:
            print(f"\n  Stage 2 eliminations :")
            for sr in result.stage2_eliminated:
                print(f"    {sr.instrument:12s} : {sr.reason}")

        print(f"{'='*60}")
