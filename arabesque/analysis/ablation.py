"""
Arabesque — Ablation study framework.

Teste l'impact de chaque composant de la stratégie en le désactivant un par un.
Permet d'identifier les leviers réels de l'edge (BE, trailing, ROI, giveback, etc.).

Usage CLI :
    python -m arabesque ablation --universe crypto --interval 4h
    python -m arabesque ablation XAUUSD BTCUSD --interval 1h

Usage programmatique :
    from arabesque.analysis.ablation import run_ablation
    results = run_ablation(["BTCUSD", "ETHUSD"], interval="4h")
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from arabesque.execution.backtest import (
    BacktestRunner, BacktestConfig, BacktestResult,
)
from arabesque.modules.position_manager import ManagerConfig, RoiTier
from arabesque.strategies.extension.signal import ExtensionSignalGenerator, ExtensionConfig
from arabesque.analysis.metrics import BacktestMetrics
from arabesque.data.store import load_ohlc, _categorize

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Ablation variants
# ═══════════════════════════════════════════════════════════════════════════════

def _baseline() -> ManagerConfig:
    """Configuration de référence (production)."""
    return ManagerConfig()


def _no_be() -> ManagerConfig:
    """Désactive le break-even."""
    cfg = ManagerConfig()
    cfg.be_trigger_r = 999.0  # jamais atteint
    return cfg


def _no_trailing() -> ManagerConfig:
    """Désactive le trailing stop."""
    cfg = ManagerConfig()
    cfg.trailing_tiers = []
    return cfg


def _no_roi() -> ManagerConfig:
    """Désactive le ROI backstop."""
    cfg = ManagerConfig()
    cfg.roi_enabled = False
    return cfg


def _no_giveback() -> ManagerConfig:
    """Désactive le giveback exit."""
    cfg = ManagerConfig()
    cfg.giveback_enabled = False
    return cfg


def _no_deadfish() -> ManagerConfig:
    """Désactive le deadfish exit."""
    cfg = ManagerConfig()
    cfg.deadfish_enabled = False
    return cfg


def _no_time_stop() -> ManagerConfig:
    """Désactive le time-stop."""
    cfg = ManagerConfig()
    cfg.time_stop_enabled = False
    return cfg


def _be_only() -> ManagerConfig:
    """Seulement le BE, pas de trailing/ROI/giveback/deadfish."""
    cfg = ManagerConfig()
    cfg.trailing_tiers = []
    cfg.roi_enabled = False
    cfg.giveback_enabled = False
    cfg.deadfish_enabled = False
    cfg.time_stop_enabled = False
    return cfg


# ── Variantes feuille de route BB_RPB_TSL (2026-03-28) ──────────────

def _no_tp_fixed() -> ManagerConfig:
    """M2: Désactive le TP fixe — tout sort par trailing/BE/giveback.
    BB_RPB_TSL n'a aucun TP fixe. Hypothèse : le TP 1.5R coupe les runners."""
    cfg = ManagerConfig()
    cfg.tp_r_by_subtype = {}
    return cfg


def _giveback_aggressive() -> ManagerConfig:
    """H7: Giveback plus agressif — MFE ≥ 0.3R (vs 0.5), current ≤ 0.10R (vs 0.15).
    Hypothèse : seuils actuels trop conservateurs, laissent des profits s'éroder."""
    cfg = ManagerConfig()
    cfg.giveback_mfe_min_r = 0.3
    cfg.giveback_current_max_r = 0.10
    return cfg


def _giveback_very_aggressive() -> ManagerConfig:
    """H7b: Giveback très agressif — MFE ≥ 0.3R, current ≤ 0.05R.
    Variante extrême pour borner l'impact."""
    cfg = ManagerConfig()
    cfg.giveback_mfe_min_r = 0.3
    cfg.giveback_current_max_r = 0.05
    return cfg


def _rsi_extreme_exit() -> ManagerConfig:
    """H2: Sortie RSI extrême + profit.
    RSI > 80 (LONG) ou RSI < 20 (SHORT) avec profit > 0.3R → exit."""
    cfg = ManagerConfig()
    cfg.rsi_extreme_exit_enabled = True
    return cfg


def _rsi_extreme_75() -> ManagerConfig:
    """H2b: Sortie RSI extrême seuil 75 (plus sensible)."""
    cfg = ManagerConfig()
    cfg.rsi_extreme_exit_enabled = True
    cfg.rsi_extreme_threshold = 75.0
    return cfg


def _regime_invalidation() -> ManagerConfig:
    """M3: Sortie par invalidation de régime.
    Si un LONG est ouvert et le régime passe en bear_trend, sortir.
    Min profit = 0.0R (sortir même à breakeven)."""
    cfg = ManagerConfig()
    cfg.regime_invalidation_enabled = True
    cfg.regime_invalidation_min_profit_r = 0.0
    return cfg


def _regime_invalidation_02() -> ManagerConfig:
    """M3b: Régime invalidation avec min profit 0.2R.
    Sortir seulement si en profit."""
    cfg = ManagerConfig()
    cfg.regime_invalidation_enabled = True
    cfg.regime_invalidation_min_profit_r = 0.2
    return cfg


def _ema200_exit() -> ManagerConfig:
    """H1: Sortie agressive sous EMA 200.
    Quand le prix est du mauvais côté de l'EMA 200, le giveback
    se déclenche sans exiger RSI < 46 + CMF < 0."""
    cfg = ManagerConfig()
    cfg.ema200_exit_enabled = True
    return cfg


def _trailing_continuous() -> ManagerConfig:
    """M1: Trailing continu — SL suit MFE avec ratio 0.5, floor 0.3R.
    Hypothèse : plus réactif que les paliers discrets (1.5R/2R/3R)."""
    cfg = ManagerConfig()
    cfg.trailing_continuous = True
    cfg.trailing_continuous_ratio = 0.5
    cfg.trailing_continuous_floor_r = 0.3
    cfg.trailing_continuous_trigger_r = 0.3
    return cfg


def _trailing_continuous_tight() -> ManagerConfig:
    """M1b: Trailing continu tight — ratio 0.4, floor 0.2R.
    Variante plus serrée pour capturer plus de profit."""
    cfg = ManagerConfig()
    cfg.trailing_continuous = True
    cfg.trailing_continuous_ratio = 0.4
    cfg.trailing_continuous_floor_r = 0.2
    cfg.trailing_continuous_trigger_r = 0.3
    return cfg


def _trailing_continuous_wide() -> ManagerConfig:
    """M1c: Trailing continu wide — ratio 0.6, floor 0.4R.
    Variante plus large pour laisser respirer les runners."""
    cfg = ManagerConfig()
    cfg.trailing_continuous = True
    cfg.trailing_continuous_ratio = 0.6
    cfg.trailing_continuous_floor_r = 0.4
    cfg.trailing_continuous_trigger_r = 0.3
    return cfg


def _trailing_dow() -> ManagerConfig:
    """H6: Trailing Dow Theory — SL suit le dernier swing confirmé.
    Hypothèse : structure > niveaux arbitraires pour le trailing."""
    cfg = ManagerConfig()
    cfg.trailing_dow = True
    cfg.trailing_dow_trigger_r = 0.3
    cfg.trailing_dow_offset_r = 0.1
    return cfg


def _trailing_dow_tight() -> ManagerConfig:
    """H6b: Trailing Dow tight — offset 0.05R (plus serré)."""
    cfg = ManagerConfig()
    cfg.trailing_dow = True
    cfg.trailing_dow_trigger_r = 0.3
    cfg.trailing_dow_offset_r = 0.05
    return cfg


# Registry des variantes
VARIANTS: dict[str, Callable[[], ManagerConfig]] = {
    "baseline": _baseline,
    "no_be": _no_be,
    "no_trailing": _no_trailing,
    "no_roi": _no_roi,
    "no_giveback": _no_giveback,
    "no_deadfish": _no_deadfish,
    "no_time_stop": _no_time_stop,
    "be_only": _be_only,
    # Feuille de route BB_RPB_TSL
    "no_tp_fixed": _no_tp_fixed,
    "giveback_aggr": _giveback_aggressive,
    "giveback_very_aggr": _giveback_very_aggressive,
    "ema200_exit": _ema200_exit,
    "rsi_extreme": _rsi_extreme_exit,
    "rsi_extreme_75": _rsi_extreme_75,
    "regime_inval": _regime_invalidation,
    "regime_inval_02": _regime_invalidation_02,
    # M1 — Trailing continu
    "trail_cont": _trailing_continuous,
    "trail_cont_tight": _trailing_continuous_tight,
    "trail_cont_wide": _trailing_continuous_wide,
    # H6 — Trailing Dow Theory
    "trail_dow": _trailing_dow,
    "trail_dow_tight": _trailing_dow_tight,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Ablation runner
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AblationResult:
    """Résultat d'une ablation pour un instrument × une variante."""
    instrument: str
    category: str
    variant: str
    interval: str
    n_trades: int = 0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    total_r: float = 0.0
    max_dd_pct: float = 0.0
    profit_factor: float = 0.0


@dataclass
class AblationSummary:
    """Résultat agrégé d'une ablation multi-instruments."""
    results: list[AblationResult] = field(default_factory=list)

    def to_dataframe(self) -> pd.DataFrame:
        """Convertit en DataFrame pour analyse."""
        rows = []
        for r in self.results:
            rows.append({
                "instrument": r.instrument,
                "category": r.category,
                "variant": r.variant,
                "interval": r.interval,
                "trades": r.n_trades,
                "WR": r.win_rate,
                "Exp(R)": r.expectancy_r,
                "Total(R)": r.total_r,
                "MaxDD%": r.max_dd_pct,
                "PF": r.profit_factor,
            })
        return pd.DataFrame(rows)

    def summary_by_category(self) -> pd.DataFrame:
        """Agrège par catégorie × variante."""
        df = self.to_dataframe()
        if df.empty:
            return df
        return (
            df.groupby(["category", "variant"])
            .agg(
                instruments=("instrument", "nunique"),
                trades=("trades", "sum"),
                WR=("WR", "mean"),
                Exp_R=("Exp(R)", "mean"),
                Total_R=("Total(R)", "sum"),
                MaxDD_pct=("MaxDD%", "max"),
            )
            .round(3)
        )

    def format_report(self) -> str:
        """Rapport texte lisible."""
        lines = ["=" * 80, "ABLATION STUDY", "=" * 80, ""]

        # Par catégorie
        cat_df = self.summary_by_category()
        if cat_df.empty:
            return "Aucun résultat."

        for cat in cat_df.index.get_level_values("category").unique():
            lines.append(f"\n── {cat.upper()} ──")
            cat_slice = cat_df.loc[cat]
            # Baseline d'abord
            if "baseline" in cat_slice.index:
                bl = cat_slice.loc["baseline"]
                lines.append(
                    f"  baseline      : {int(bl.trades):4d} trades | "
                    f"WR {bl.WR:.1%} | Exp {bl.Exp_R:+.3f}R | "
                    f"Total {bl.Total_R:+.1f}R | MaxDD {bl.MaxDD_pct:.1f}%"
                )
            for variant in cat_slice.index:
                if variant == "baseline":
                    continue
                row = cat_slice.loc[variant]
                bl_exp = cat_slice.loc["baseline"].Exp_R if "baseline" in cat_slice.index else 0
                delta = row.Exp_R - bl_exp
                marker = "↑" if delta > 0 else "↓" if delta < 0 else "="
                lines.append(
                    f"  {variant:<14s}: {int(row.trades):4d} trades | "
                    f"WR {row.WR:.1%} | Exp {row.Exp_R:+.3f}R | "
                    f"Total {row.Total_R:+.1f}R | Δ {delta:+.3f}R {marker}"
                )

        return "\n".join(lines)


def run_ablation(
    instruments: list[str],
    interval: str = "1h",
    period: str = "730d",
    variants: list[str] | None = None,
    risk_pct: float = 0.40,
    use_sub_bar: bool = True,
    start: str | None = None,
    end: str | None = None,
) -> AblationSummary:
    """Exécute l'ablation sur une liste d'instruments.

    Args:
        instruments: Liste d'instruments FTMO.
        interval: Timeframe (ex: "1h", "4h").
        period: Période de données (ex: "730d").
        variants: Variantes à tester. Défaut: toutes.
        risk_pct: Risque par trade.
        use_sub_bar: Utiliser le sub-bar replay M1 si disponible.
        start/end: Dates de filtrage optionnelles.

    Returns:
        AblationSummary avec tous les résultats.
    """
    if variants is None:
        variants = list(VARIANTS.keys())

    summary = AblationSummary()

    for inst in instruments:
        category = _categorize(inst)
        logger.info(f"[ablation] {inst} ({category}) — chargement {interval}...")

        try:
            df = load_ohlc(inst, period=period, interval=interval, start=start, end=end)
        except Exception as e:
            logger.warning(f"[ablation] {inst}: impossible de charger ({e})")
            continue

        # Sub-bar M1
        sub_bar_df = None
        if use_sub_bar:
            try:
                sub_bar_df = load_ohlc(inst, period=period, interval="1m", start=start, end=end)
                if len(sub_bar_df) < len(df):
                    sub_bar_df = None
            except Exception:
                sub_bar_df = None

        # Préparer les signaux une seule fois
        sig_gen = ExtensionSignalGenerator(ExtensionConfig())
        df_prepared = sig_gen.prepare(df)

        for variant_name in variants:
            if variant_name not in VARIANTS:
                logger.warning(f"[ablation] variante inconnue: {variant_name}")
                continue

            manager_cfg = VARIANTS[variant_name]()
            bt_cfg = BacktestConfig(
                risk_per_trade_pct=risk_pct,
                signal_filter_path=None,
            )

            runner = BacktestRunner(
                bt_config=bt_cfg,
                manager_config=manager_cfg,
                signal_config=ExtensionConfig(),
            )

            try:
                result = runner.run(
                    df_prepared, instrument=inst,
                    sample_type="ablation",
                    sub_bar_df=sub_bar_df,
                )
                m = result.metrics
                summary.results.append(AblationResult(
                    instrument=inst,
                    category=category,
                    variant=variant_name,
                    interval=interval,
                    n_trades=m.n_trades,
                    win_rate=m.win_rate,
                    expectancy_r=m.expectancy_r,
                    total_r=m.total_r,
                    max_dd_pct=m.max_dd_pct,
                    profit_factor=m.profit_factor,
                ))
                logger.info(
                    f"  {variant_name:<14s}: {m.n_trades} trades, "
                    f"WR {m.win_rate:.1%}, Exp {m.expectancy_r:+.3f}R"
                )
            except Exception as e:
                logger.error(f"  {variant_name}: erreur ({e})")

    return summary
