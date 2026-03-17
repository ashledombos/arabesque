"""
Arabesque v2 — Backtest Runner (Pass 2).

Utilise le MÊME PositionManager que le live. Zéro divergence.

Architecture :
1. Charge les données OHLC (Yahoo Finance 1H)
2. Calcule les indicateurs (mêmes formules que Pine)
3. Itère bar-by-bar :
   a. Vérifie si un signal est émis (bougie confirmée)
   b. Si signal : guards → sizing → open_position (fill = open bougie suivante)
   c. Pour chaque position ouverte : update_position(H, L, C, indicators)
   d. Met à jour AccountState (daily reset, P&L)
   e. Met à jour counterfactuals
4. Calcule les métriques
5. Écrit un rapport JSONL de synthèse dans logs/backtest_runs.jsonl

Spread + slippage simulés :
- Spread = spread_pct * prix (configurable, réaliste par instrument)
- Slippage = slippage_r * ATR (ajouté au fill)

Usage CLI :
    python -m arabesque.backtest.runner XRPUSD --period 730d --strategy trend
    python -m arabesque.backtest.runner XRPUSD SOLUSD --strategy trend --split 0.7

CORRECTION v2.4 (2026-02-20) — TD-007 final :
- signal.tv_close → signal.close dans la logique de guards check (lignes 213-217)
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import numpy as np
import pandas as pd

from arabesque.core.models import Signal, Position, Decision, DecisionType, Side
from arabesque.core.guards import Guards, PropConfig, ExecConfig, AccountState
from arabesque.modules.position_manager import PositionManager, ManagerConfig
from arabesque.strategies.extension.signal import ExtensionSignalGenerator as BacktestSignalGenerator, ExtensionConfig as SignalGenConfig
from arabesque.analysis.metrics import (
    BacktestMetrics, compute_metrics, slippage_sensitivity, format_report,
)
from arabesque.data.store import load_ohlc, split_in_out_sample, split_walk_forward, yahoo_symbol, _categorize
from arabesque.core.signal_filter import SignalFilter

# Fichier JSONL de synthèse des runs backtest
BACKTEST_RUNS_LOG = Path("logs/backtest_runs.jsonl")


def manager_config_for(instrument: str, interval: str) -> ManagerConfig | None:
    """Retourne un ManagerConfig adapté à la famille d'instrument + timeframe.

    Ablation crypto H4 (42 instruments, 1623 trades, sub-bar M1) :
      Avec ROI : Exp +0.044R
      Sans ROI : Exp +0.181R  (ROI détruit l'edge crypto)
    """
    if _categorize(instrument) == "crypto" and interval in ("4h", "H4", "4H"):
        return ManagerConfig(roi_enabled=False)
    return None


@dataclass
class BacktestConfig:
    """Configuration du backtest."""
    start_balance: float = 100_000.0
    risk_per_trade_pct: float = 0.5
    spread_fixed: float = 0.0
    spread_pct: float = 0.00015
    slippage_r: float = 0.03
    daily_dd_limit_pct: float = 3.0
    total_dd_limit_pct: float = 8.0
    max_positions: int = 10
    max_open_risk_pct: float = 2.0
    signal_cooldown_bars: int = 5
    signal_filter_path: str | None = "config/signal_filters.yaml"
    verbose: bool = False
    progress_every: int = 500


@dataclass
class BacktestResult:
    """Résultat complet d'un backtest."""
    config: BacktestConfig
    metrics: BacktestMetrics
    closed_positions: list[Position] = field(default_factory=list)
    all_decisions: list[Decision] = field(default_factory=list)
    report: str = ""


class BacktestRunner:
    """Exécute un backtest complet sur données OHLC.

    Utilise le MÊME PositionManager que le live.
    """

    def __init__(
        self,
        bt_config: BacktestConfig | None = None,
        manager_config: ManagerConfig | None = None,
        signal_config: SignalGenConfig | None = None,
        prop_config: PropConfig | None = None,
        exec_config: ExecConfig | None = None,
        signal_generator: object | None = None,
    ):
        self.bt_cfg = bt_config or BacktestConfig()
        self.manager = PositionManager(manager_config)
        if signal_generator is not None:
            self.sig_gen = signal_generator
        else:
            self.sig_gen = BacktestSignalGenerator(signal_config)
        self.prop_cfg = prop_config or PropConfig(
            risk_per_trade_pct=self.bt_cfg.risk_per_trade_pct,
            max_positions=self.bt_cfg.max_positions,
            max_open_risk_pct=self.bt_cfg.max_open_risk_pct,
            max_daily_dd_pct=self.bt_cfg.daily_dd_limit_pct,
            max_total_dd_pct=self.bt_cfg.total_dd_limit_pct,
        )
        self.exec_cfg = exec_config or ExecConfig()
        self.guards = Guards(self.prop_cfg, self.exec_cfg)
        self.account = AccountState(
            balance=self.bt_cfg.start_balance,
            equity=self.bt_cfg.start_balance,
            start_balance=self.bt_cfg.start_balance,
            daily_start_balance=self.bt_cfg.start_balance,
        )
        if self.bt_cfg.signal_filter_path:
            self.signal_filter: SignalFilter | None = SignalFilter(self.bt_cfg.signal_filter_path)
        else:
            self.signal_filter = None

    def run(
        self,
        df: pd.DataFrame,
        instrument: str = "",
        sample_type: str = "",
        sub_bar_df: pd.DataFrame | None = None,
    ) -> BacktestResult:
        """Exécute le backtest sur un DataFrame OHLC préparé.

        Args:
            sub_bar_df: Données M1 pour le sub-bar replay. Si fourni, les
                positions ouvertes sont mises à jour barre M1 par barre M1
                (au lieu de H/L agrégé), ce qui résout l'ambiguïté intra-barre
                pour le BE trigger, le trailing, et l'ordre SL/TP.
                Doit avoir des colonnes Open/High/Low/Close et un DatetimeIndex.
        """
        self.manager = PositionManager(self.manager.cfg)
        self.account = AccountState(
            balance=self.bt_cfg.start_balance,
            equity=self.bt_cfg.start_balance,
            start_balance=self.bt_cfg.start_balance,
            daily_start_balance=self.bt_cfg.start_balance,
        )

        signals_by_bar = self._precompute_signals(df, instrument)

        # Pré-indexer les sub-bars par timestamp de barre parente
        # pour un lookup O(1) au lieu de filtrer à chaque barre
        sub_bar_index: dict[pd.Timestamp, pd.DataFrame] | None = None
        if sub_bar_df is not None and len(sub_bar_df) > 0:
            sub_bar_index = self._build_sub_bar_index(df, sub_bar_df)

        all_decisions: list[Decision] = []
        n_signals = 0
        n_rejected = 0
        rejection_reasons: dict[str, int] = {}
        current_date = None
        last_signal_bar: dict[str, int] = {}
        n_bars = len(df)
        ts_start = datetime.now(timezone.utc)

        for i in range(len(df)):
            row = df.iloc[i]
            bar_date = df.index[i]
            high = row["High"]
            low = row["Low"]
            close = row["Close"]

            row_date = bar_date.date()
            if current_date is not None and row_date != current_date:
                self.account.new_day()
            current_date = row_date

            indicators = {
                "rsi": row.get("rsi", 50),
                "cmf": row.get("cmf", 0),
                "bb_width": row.get("bb_width", 0.01),
                "ema200": row.get("ema_slow", 0),
            }

            # ── Position update : sub-bar replay ou H/L agrégé ──
            if self.manager.open_positions and sub_bar_index is not None:
                sub_bars = sub_bar_index.get(bar_date)
                if sub_bars is not None and len(sub_bars) > 0:
                    # Sauvegarder bars_open pour chaque position ouverte :
                    # le sub-bar replay ne doit incrémenter bars_open que de 1
                    # par barre parente (pas 60 pour 60 M1 dans 1 H1).
                    saved_bars = {id(pos): pos.bars_open for pos in self.manager.open_positions}

                    # Itérer les M1 en ordre chronologique
                    for _, sb in sub_bars.iterrows():
                        for pos in list(self.manager.open_positions):
                            decisions = self.manager.update_position(
                                pos, sb["High"], sb["Low"], sb["Close"], indicators
                            )
                            all_decisions.extend(decisions)
                            if not pos.is_open and pos.result_r is not None:
                                # Restaurer bars_open correct (1 barre parente)
                                pos.bars_open = saved_bars.get(id(pos), pos.bars_open - 1) + 1
                                sb_ts = sb.name if hasattr(sb, 'name') else bar_date
                                pos.ts_exit = sb_ts.to_pydatetime() if hasattr(sb_ts, 'to_pydatetime') else sb_ts
                                pnl = pos.result_r * pos.risk_cash
                                self.account.equity += pnl
                                self.account.balance += pnl
                                self.account.daily_pnl += pnl
                                self.account.open_positions -= 1
                                self.account.open_risk_cash = max(0.0, self.account.open_risk_cash - pos.risk_cash)
                                if pos.instrument in self.account.open_instruments:
                                    self.account.open_instruments.remove(pos.instrument)

                    # Restaurer bars_open pour les positions encore ouvertes
                    for pos in self.manager.open_positions:
                        if id(pos) in saved_bars:
                            pos.bars_open = saved_bars[id(pos)] + 1
                else:
                    # Pas de sub-bars pour cette barre → fallback H/L agrégé
                    self._update_positions_hlc(high, low, close, bar_date, indicators, all_decisions)
            else:
                # Pas de sub-bar replay → mode classique H/L agrégé
                self._update_positions_hlc(high, low, close, bar_date, indicators, all_decisions)

            self.manager.update_counterfactuals(instrument, high, low, close)

            if i in signals_by_bar and i + 1 < len(df):
                signal = signals_by_bar[i]
                n_signals += 1

                last_bar = last_signal_bar.get(instrument, -999)
                if i - last_bar < self.bt_cfg.signal_cooldown_bars:
                    n_rejected += 1
                    rejection_reasons["cooldown"] = rejection_reasons.get("cooldown", 0) + 1
                    continue

                next_bar = df.iloc[i + 1]
                next_bar_ts = df.index[i + 1]
                fill_price = next_bar["Open"]

                spread = self._compute_spread(fill_price)
                if signal.side == Side.LONG:
                    bid = fill_price
                    ask = fill_price + spread
                    fill_price = ask
                else:
                    bid = fill_price - spread
                    ask = fill_price
                    fill_price = bid

                atr = signal.atr if signal.atr > 0 else row.get("atr", 0)
                slip = self.bt_cfg.slippage_r * atr
                if signal.side == Side.LONG:
                    fill_price += slip
                else:
                    fill_price -= slip

                original_close = signal.close
                signal.close = next_bar["Open"]
                ok, decision = self.guards.check_all(signal, self.account, bid, ask)
                all_decisions.append(decision)
                signal.close = original_close

                if not ok:
                    n_rejected += 1
                    reason = decision.reject_reason.value if decision.reject_reason else "unknown"
                    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                    if self.bt_cfg.verbose:
                        print(f"  [{bar_date}] REJECTED: {reason}")
                    continue

                sizing = self.guards.compute_sizing(signal, self.account)
                risk_cash = sizing["risk_cash"]
                risk_distance = sizing["risk_distance"]

                if risk_cash <= 0 or risk_distance <= 0:
                    n_rejected += 1
                    rejection_reasons["sizing_zero"] = rejection_reasons.get("sizing_zero", 0) + 1
                    continue

                contract_size = self._contract_size(instrument, fill_price)
                volume = risk_cash / (risk_distance * contract_size)
                volume = int(volume * 100) / 100

                if volume <= 0:
                    n_rejected += 1
                    rejection_reasons["volume_zero"] = rejection_reasons.get("volume_zero", 0) + 1
                    continue

                pos = self.manager.open_position(signal, fill_price, risk_cash, volume)
                pos.ts_entry = next_bar_ts.to_pydatetime() if hasattr(next_bar_ts, 'to_pydatetime') else next_bar_ts
                self.account.open_positions += 1
                self.account.open_risk_cash += risk_cash
                self.account.open_instruments.append(instrument)
                self.account.daily_trades += 1
                last_signal_bar[instrument] = i

                if self.bt_cfg.verbose:
                    print(f"  [{bar_date}] OPEN {signal.side.value} @ {fill_price:.5f} "
                          f"SL={pos.sl:.5f} R={pos.R:.5f}")

            if self.bt_cfg.verbose and i > 0 and i % self.bt_cfg.progress_every == 0:
                print(f"  Bar {i}/{n_bars} | Open: {len(self.manager.open_positions)} "
                      f"| Closed: {len(self.manager.closed_positions)} "
                      f"| Equity: {self.account.equity:,.0f}")

        for pos in list(self.manager.open_positions):
            last_close = df.iloc[-1]["Close"]
            d = self.manager._close_position(
                pos, last_close, DecisionType.EXIT_TIME_STOP, "End of data — forced close"
            )
            all_decisions.append(d)
            if pos.result_r is not None:
                pnl = pos.result_r * pos.risk_cash
                self.account.equity += pnl
                self.account.balance += pnl

        closed = self.manager.closed_positions
        metrics = compute_metrics(
            closed,
            start_balance=self.bt_cfg.start_balance,
            risk_per_trade_pct=self.bt_cfg.risk_per_trade_pct,
            daily_dd_limit_pct=self.bt_cfg.daily_dd_limit_pct,
            total_dd_limit_pct=self.bt_cfg.total_dd_limit_pct,
            instrument=instrument,
            sample_type=sample_type,
        )
        metrics.n_bars = n_bars
        metrics.n_signals_generated = n_signals
        metrics.n_signals_rejected = n_rejected
        metrics.rejection_reasons = rejection_reasons

        results_r = [p.result_r for p in closed if p.result_r is not None]
        if results_r:
            metrics.slippage_sensitivity = slippage_sensitivity(
                metrics.expectancy_r, results_r, self.bt_cfg.slippage_r
            )

        report = format_report(metrics)
        strategy_name = getattr(self.sig_gen, 'cfg', None)
        strategy_name = getattr(strategy_name, 'mode', '') if strategy_name else ''
        strategy_type = getattr(self.sig_gen, '__class__', type(self.sig_gen)).__name__
        _write_run_jsonl(
            instrument=instrument, sample_type=sample_type,
            config=self.bt_cfg, metrics=metrics, ts_start=ts_start,
            strategy=strategy_type,
        )

        return BacktestResult(
            config=self.bt_cfg, metrics=metrics,
            closed_positions=closed, all_decisions=all_decisions, report=report,
        )

    def _precompute_signals(self, df: pd.DataFrame, instrument: str) -> dict[int, Signal]:
        signals = self.sig_gen.generate_signals(df, instrument)
        category = _categorize(instrument)
        signal_map: dict[int, Signal] = {}
        n_filtered = 0
        for item in signals:
            if isinstance(item, tuple) and len(item) == 2:
                idx, sig = item
                idx = int(idx)
            else:
                sig = item
                try:
                    idx = df.index.get_loc(sig.timestamp)
                    if not isinstance(idx, (int, np.integer)):
                        continue
                    idx = int(idx)
                except (KeyError, AttributeError):
                    continue
            if self.signal_filter is not None and not self.signal_filter.is_allowed(sig.sub_type, category):
                n_filtered += 1
                continue
            signal_map[idx] = sig
        if n_filtered and self.bt_cfg.verbose:
            print(f"  [{instrument}] SignalFilter: {n_filtered} signal(s) filtrés (sub_type × {category})")
        return signal_map

    def _compute_spread(self, price: float) -> float:
        if self.bt_cfg.spread_fixed > 0:
            return self.bt_cfg.spread_fixed
        return price * self.bt_cfg.spread_pct

    @staticmethod
    def _contract_size(instrument: str, price: float) -> float:
        inst = instrument.upper()
        fx_currencies = {
            "EUR", "GBP", "USD", "CHF", "CAD", "AUD", "NZD", "JPY",
            "CNH", "CZK", "HKD", "MXN", "NOK", "PLN", "SEK", "SGD", "ZAR", "ILS", "HUF",
        }
        if len(inst) == 6 and inst[:3] in fx_currencies and inst[3:] in fx_currencies:
            return 100_000
        if inst in ("XAUUSD", "GOLD"): return 100
        if inst in ("XAGUSD", "SILVER"): return 5_000
        if inst in ("XPTUSD", "PLATINUM", "XPDUSD", "PALLADIUM"): return 100
        if inst in ("COPPER",): return 25_000
        crypto = {"BTC", "ETH", "LTC", "SOL", "BNB", "BCH", "XRP", "DOGE",
                  "ADA", "DOT", "XMR", "DASH", "NEO", "UNI", "XLM", "AAVE",
                  "MANA", "IMX", "GRT", "ETC", "ALGO", "NEAR", "LINK", "AVAX",
                  "XTZ", "FET", "ICP", "SAND", "GAL", "VET"}
        if inst in crypto: return 1
        indices = {"SP500", "NAS100", "US30", "US2000", "GER40", "UK100",
                   "FRA40", "EU50", "IBEX35", "AEX25", "JPN225", "HK50", "AUS200", "USTEC", "USDX"}
        if inst in indices: return 1
        if inst in ("USOIL", "UKOIL", "BRENT"): return 1_000
        if inst in ("NATGAS",): return 10_000
        agri = {"COCOA", "COFFEE", "CORN", "COTTON", "SOYBEAN", "WHEAT", "SUGAR"}
        if inst in agri: return 100
        return 1

    def _update_positions_hlc(
        self,
        high: float, low: float, close: float,
        bar_date, indicators: dict,
        all_decisions: list,
    ) -> None:
        """Met à jour les positions ouvertes avec le H/L/C agrégé d'une barre."""
        for pos in list(self.manager.open_positions):
            decisions = self.manager.update_position(pos, high, low, close, indicators)
            all_decisions.extend(decisions)
            if not pos.is_open and pos.result_r is not None:
                pos.ts_exit = bar_date.to_pydatetime() if hasattr(bar_date, 'to_pydatetime') else bar_date
                pnl = pos.result_r * pos.risk_cash
                self.account.equity += pnl
                self.account.balance += pnl
                self.account.daily_pnl += pnl
                self.account.open_positions -= 1
                self.account.open_risk_cash = max(0.0, self.account.open_risk_cash - pos.risk_cash)
                if pos.instrument in self.account.open_instruments:
                    self.account.open_instruments.remove(pos.instrument)

    @staticmethod
    def _build_sub_bar_index(
        parent_df: pd.DataFrame,
        sub_df: pd.DataFrame,
    ) -> dict:
        """Pré-indexe les sub-bars (M1) par timestamp de barre parente.

        Pour chaque barre parente (H1/H4), trouve les sub-bars M1 dont le
        timestamp tombe dans l'intervalle [parent_ts, next_parent_ts).

        Retourne un dict : parent_timestamp → DataFrame de sub-bars triées.
        """
        index = {}
        parent_times = parent_df.index
        sub_times = sub_df.index

        # Utiliser searchsorted pour un lookup O(n log n) total
        for j in range(len(parent_times)):
            start_ts = parent_times[j]
            end_ts = parent_times[j + 1] if j + 1 < len(parent_times) else start_ts + pd.Timedelta(hours=24)

            i_start = sub_times.searchsorted(start_ts, side="left")
            i_end = sub_times.searchsorted(end_ts, side="left")

            if i_start < i_end:
                index[start_ts] = sub_df.iloc[i_start:i_end]

        return index


# ── JSONL run summary ──────────────────────────────────────────────────

def _write_run_jsonl(
    instrument: str, sample_type: str,
    config: BacktestConfig, metrics: BacktestMetrics, ts_start: datetime,
    strategy: str = "",
) -> None:
    BACKTEST_RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": ts_start.isoformat(), "strategy": strategy,
        "instrument": instrument, "sample": sample_type,
        "category": _categorize(instrument),
        "n_trades": metrics.n_trades, "win_rate": round(metrics.win_rate, 4),
        "expectancy_r": round(metrics.expectancy_r, 4),
        "profit_factor": round(metrics.profit_factor, 3),
        "max_dd_pct": round(metrics.max_dd_pct, 2),
        "n_disq_days": metrics.n_disqualifying_days,
        "n_signals": metrics.n_signals_generated, "n_rejected": metrics.n_signals_rejected,
        "rejection_reasons": metrics.rejection_reasons,
        "slippage_r": config.slippage_r, "spread_pct": config.spread_pct,
        "risk_pct": config.risk_per_trade_pct, "max_open_risk_pct": config.max_open_risk_pct,
    }
    with open(BACKTEST_RUNS_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Convenience functions ───────────────────────────────────────────

def run_backtest(
    instrument: str,
    period: str = "730d",
    start: str | None = None,
    end: str | None = None,
    bt_config: BacktestConfig | None = None,
    manager_config: ManagerConfig | None = None,
    signal_config: SignalGenConfig | None = None,
    split_pct: float = 0.70,
    verbose: bool = True,
    strategy: str = "mean_reversion",
    data_root: str | None = None,
) -> tuple[BacktestResult, BacktestResult]:
    cfg = bt_config or BacktestConfig(verbose=verbose)
    if verbose:
        cfg.verbose = True

    symbol = yahoo_symbol(instrument)
    strat_label = strategy.upper().replace("_", " ")
    print(f"\n{'='*60}")
    print(f"  ARABESQUE BACKTEST — {instrument} ({symbol})")
    print(f"  Strategy: {strat_label}")
    filter_status = "ON" if cfg.signal_filter_path else "OFF"
    print(f"  SignalFilter: {filter_status}" + (f" ({cfg.signal_filter_path})" if cfg.signal_filter_path else ""))
    print(f"{'='*60}")
    print("  Loading data...")

    df = load_ohlc(symbol, period=period, start=start, end=end, instrument=instrument, data_root=data_root)
    print(f"  Loaded {len(df)} bars from {df.index[0]} to {df.index[-1]}")

    if strategy == "trend":
        from arabesque.strategies.extension.signal import ExtensionSignalGenerator as TrendSignalGenerator, ExtensionConfig as TrendSignalConfig
        sig_gen = TrendSignalGenerator(TrendSignalConfig())
    elif strategy == "combined":
        # from arabesque.backtest.signal_gen_combined import  # mean-reversion abandoned CombinedSignalGenerator
        sig_gen = CombinedSignalGenerator(mr_config=signal_config)
    else:
        sig_gen = BacktestSignalGenerator(signal_config)

    df_prepared = sig_gen.prepare(df)
    print("  Indicators computed")

    df_in, df_out = split_in_out_sample(df_prepared, split_pct)
    print(f"  In-sample:  {len(df_in)} bars ({df_in.index[0].date()} → {df_in.index[-1].date()})")
    print(f"  Out-sample: {len(df_out)} bars ({df_out.index[0].date()} → {df_out.index[-1].date()})")

    print("\n--- IN-SAMPLE ---")
    runner_in = BacktestRunner(cfg, manager_config, signal_config, signal_generator=sig_gen)
    result_in = runner_in.run(df_in, instrument, "in_sample")
    print(result_in.report)

    print("\n--- OUT-OF-SAMPLE ---")
    runner_out = BacktestRunner(cfg, manager_config, signal_config, signal_generator=sig_gen)
    result_out = runner_out.run(df_out, instrument, "out_of_sample")
    print(result_out.report)

    _print_comparison(result_in.metrics, result_out.metrics)
    return result_in, result_out


def run_multi_instrument(instruments: list[str], **kwargs) -> dict[str, tuple[BacktestResult, BacktestResult]]:
    results = {}
    for inst in instruments:
        try:
            results[inst] = run_backtest(inst, **kwargs)
        except Exception as e:
            print(f"\n  ERROR on {inst}: {e}")
    if results:
        _print_synthesis(results)
    return results


def _print_comparison(m_in: BacktestMetrics, m_out: BacktestMetrics):
    print(f"\n{'='*60}")
    print("  COMPARISON IN vs OUT")
    print(f"{'='*60}")
    print(f"  {'Metric':<25s} {'In-Sample':>12s} {'Out-Sample':>12s} {'Delta':>12s}")
    print(f"  {'-'*61}")
    rows = [
        ("Trades", f"{m_in.n_trades}", f"{m_out.n_trades}", ""),
        ("Win Rate", f"{m_in.win_rate:.1%}", f"{m_out.win_rate:.1%}", f"{m_out.win_rate - m_in.win_rate:+.1%}"),
        ("Expectancy (R)", f"{m_in.expectancy_r:+.3f}", f"{m_out.expectancy_r:+.3f}", f"{m_out.expectancy_r - m_in.expectancy_r:+.3f}"),
        ("Profit Factor", f"{m_in.profit_factor:.2f}", f"{m_out.profit_factor:.2f}", f"{m_out.profit_factor - m_in.profit_factor:+.2f}"),
        ("Max DD %", f"{m_in.max_dd_pct:.1f}%", f"{m_out.max_dd_pct:.1f}%", f"{m_out.max_dd_pct - m_in.max_dd_pct:+.1f}%"),
        ("Disqual Days", f"{m_in.n_disqualifying_days}", f"{m_out.n_disqualifying_days}", ""),
    ]
    for name, v_in, v_out, delta in rows:
        print(f"  {name:<25s} {v_in:>12s} {v_out:>12s} {delta:>12s}")
    print(f"{'='*60}")


def _print_synthesis(results: dict[str, tuple[BacktestResult, BacktestResult]]):
    print(f"\n{'='*60}")
    print("  MULTI-INSTRUMENT SYNTHESIS")
    print(f"{'='*60}")
    print(f"  {'Instrument':<12s} {'Trades':>7s} {'WR':>6s} {'Exp(R)':>8s} {'PF':>6s} {'MaxDD':>7s} {'Disq':>5s}")
    print(f"  {'-'*51}")
    for inst, (res_in, res_out) in results.items():
        m = res_out.metrics
        print(f"  {inst:<12s} {m.n_trades:>7d} {m.win_rate:>5.0%} "
              f"{m.expectancy_r:>+7.3f} {m.profit_factor:>5.2f} "
              f"{m.max_dd_pct:>6.1f}% {m.n_disqualifying_days:>5d}")
    print(f"{'='*60}")


# ── Walk-forward validation ──────────────────────────────

@dataclass
class WalkForwardWindow:
    """Résultat d'une fenêtre walk-forward."""
    window_idx: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    is_metrics: BacktestMetrics
    oos_metrics: BacktestMetrics


@dataclass
class WalkForwardResult:
    """Résultat agrégé d'un walk-forward complet."""
    instrument: str
    n_windows: int
    windows: list[WalkForwardWindow]
    # Agrégats OOS
    total_oos_trades: int = 0
    aggregate_wr: float = 0.0
    aggregate_exp_r: float = 0.0
    aggregate_pf: float = 0.0
    aggregate_total_r: float = 0.0
    max_dd_pct: float = 0.0
    # Stabilité
    wr_std: float = 0.0
    exp_std: float = 0.0
    is_oos_wr_degradation: float = 0.0  # avg(IS_WR - OOS_WR)
    is_oos_exp_degradation: float = 0.0  # avg(IS_exp - OOS_exp)
    report: str = ""


def run_walk_forward(
    instrument: str,
    is_bars: int = 4380,
    oos_bars: int = 1460,
    step_bars: int | None = None,
    period: str = "730d",
    start: str | None = None,
    end: str | None = None,
    bt_config: BacktestConfig | None = None,
    manager_config: ManagerConfig | None = None,
    signal_generator: object | None = None,
    strategy: str = "trend",
    interval: str = "1h",
    data_root: str | None = None,
    verbose: bool = True,
) -> WalkForwardResult:
    """Walk-forward validation : fenêtres glissantes IS→OOS.

    Args:
        instrument: Nom de l'instrument (ex: XAUUSD).
        is_bars: Taille fenêtre in-sample en barres.
        oos_bars: Taille fenêtre out-of-sample en barres.
        step_bars: Pas d'avancement (défaut = oos_bars).
        strategy: Stratégie à utiliser.
        interval: Timeframe (ex: 1h, 4h).

    Returns:
        WalkForwardResult avec métriques agrégées sur toutes les fenêtres OOS.
    """
    cfg = bt_config or BacktestConfig(verbose=False)

    # Auto-detect per-family ManagerConfig (e.g. ROI disabled for crypto H4)
    if manager_config is None:
        manager_config = manager_config_for(instrument, interval)

    if verbose:
        print(f"\n{'='*70}")
        print(f"  WALK-FORWARD — {instrument} ({interval})")
        print(f"  IS: {is_bars} bars  |  OOS: {oos_bars} bars  |  Step: {step_bars or oos_bars} bars")
        print(f"{'='*70}")

    # Charger les données
    df = load_ohlc(instrument, period=period, start=start, end=end,
                   interval=interval, data_root=data_root)
    if df is None or len(df) < is_bars + oos_bars:
        raise ValueError(f"Données insuffisantes pour {instrument}: {len(df) if df is not None else 0} bars "
                         f"(besoin de {is_bars + oos_bars} minimum)")

    # Charger les sub-bars M1 pour le sub-bar replay (si TF > M1)
    sub_bar_df = None
    if interval not in ("min1", "1m", "M1"):
        try:
            sub_bar_df = load_ohlc(instrument, period=period, start=start, end=end,
                                   interval="min1", data_root=data_root)
            if sub_bar_df is not None and len(sub_bar_df) > 0:
                if "close" in sub_bar_df.columns and "Close" not in sub_bar_df.columns:
                    sub_bar_df.columns = [c.capitalize() for c in sub_bar_df.columns]
                if verbose:
                    print(f"  Sub-bar replay: {len(sub_bar_df)} barres M1 chargées")
            else:
                sub_bar_df = None
        except Exception:
            sub_bar_df = None

    # Préparer le signal generator
    if signal_generator is None:
        if strategy == "trend":
            from arabesque.strategies.extension.signal import ExtensionSignalGenerator, ExtensionConfig
            signal_generator = ExtensionSignalGenerator(ExtensionConfig())
        else:
            signal_generator = BacktestSignalGenerator()

    # Préparer les indicateurs UNE FOIS sur tout le dataset
    df_prepared = signal_generator.prepare(df)

    # Découper en fenêtres
    windows = split_walk_forward(df_prepared, is_bars, oos_bars, step_bars)
    if not windows:
        raise ValueError(f"Pas assez de données pour créer des fenêtres walk-forward "
                         f"({len(df_prepared)} bars, besoin {is_bars}+{oos_bars})")

    if verbose:
        print(f"  {len(windows)} fenêtres créées sur {len(df_prepared)} barres")
        print(f"  Période: {df_prepared.index[0].date()} → {df_prepared.index[-1].date()}")

    # Exécuter chaque fenêtre
    wf_windows: list[WalkForwardWindow] = []
    all_oos_positions: list = []

    for idx, (df_is, df_oos) in enumerate(windows):
        if verbose:
            print(f"\n  ── Fenêtre {idx+1}/{len(windows)} ──")
            print(f"     IS:  {df_is.index[0].date()} → {df_is.index[-1].date()} ({len(df_is)} bars)")
            print(f"     OOS: {df_oos.index[0].date()} → {df_oos.index[-1].date()} ({len(df_oos)} bars)")

        # Run IS
        runner_is = BacktestRunner(cfg, manager_config=manager_config, signal_generator=signal_generator)
        result_is = runner_is.run(df_is, instrument, "in_sample", sub_bar_df=sub_bar_df)

        # Run OOS
        runner_oos = BacktestRunner(cfg, manager_config=manager_config, signal_generator=signal_generator)
        result_oos = runner_oos.run(df_oos, instrument, "out_of_sample", sub_bar_df=sub_bar_df)

        wf_windows.append(WalkForwardWindow(
            window_idx=idx,
            is_start=str(df_is.index[0].date()),
            is_end=str(df_is.index[-1].date()),
            oos_start=str(df_oos.index[0].date()),
            oos_end=str(df_oos.index[-1].date()),
            is_metrics=result_is.metrics,
            oos_metrics=result_oos.metrics,
        ))
        all_oos_positions.extend(result_oos.closed_positions)

        if verbose:
            m_is = result_is.metrics
            m_oos = result_oos.metrics
            print(f"     IS:  {m_is.n_trades} trades  WR {m_is.win_rate:.0%}  Exp {m_is.expectancy_r:+.3f}R")
            print(f"     OOS: {m_oos.n_trades} trades  WR {m_oos.win_rate:.0%}  Exp {m_oos.expectancy_r:+.3f}R")

    # Agrégation
    result = _aggregate_walk_forward(instrument, wf_windows, all_oos_positions, cfg)

    if verbose:
        print(result.report)

    return result


def _aggregate_walk_forward(
    instrument: str,
    windows: list[WalkForwardWindow],
    all_oos_positions: list,
    cfg: BacktestConfig,
) -> WalkForwardResult:
    """Agrège les résultats walk-forward."""
    oos_metrics_list = [w.oos_metrics for w in windows]
    is_metrics_list = [w.is_metrics for w in windows]

    # Trades OOS totaux
    total_trades = sum(m.n_trades for m in oos_metrics_list)
    total_wins = sum(m.n_wins for m in oos_metrics_list)
    total_r = sum(m.total_r for m in oos_metrics_list)

    # WR et Exp agrégés (pondérés par nb trades)
    agg_wr = total_wins / total_trades if total_trades > 0 else 0.0
    agg_exp = total_r / total_trades if total_trades > 0 else 0.0

    # PF agrégé
    total_gross_profit = sum(
        m.avg_win_r * m.n_wins for m in oos_metrics_list if m.n_wins > 0
    )
    total_gross_loss = abs(sum(
        m.avg_loss_r * m.n_losses for m in oos_metrics_list if m.n_losses > 0
    ))
    agg_pf = total_gross_profit / total_gross_loss if total_gross_loss > 0 else float('inf')

    # Max DD
    max_dd = max((m.max_dd_pct for m in oos_metrics_list), default=0.0)

    # Stabilité par fenêtre
    oos_wrs = [m.win_rate for m in oos_metrics_list if m.n_trades >= 5]
    oos_exps = [m.expectancy_r for m in oos_metrics_list if m.n_trades >= 5]
    wr_std = float(np.std(oos_wrs)) if len(oos_wrs) > 1 else 0.0
    exp_std = float(np.std(oos_exps)) if len(oos_exps) > 1 else 0.0

    # Dégradation IS → OOS
    degradations_wr = []
    degradations_exp = []
    for w in windows:
        if w.is_metrics.n_trades >= 5 and w.oos_metrics.n_trades >= 5:
            degradations_wr.append(w.is_metrics.win_rate - w.oos_metrics.win_rate)
            degradations_exp.append(w.is_metrics.expectancy_r - w.oos_metrics.expectancy_r)

    avg_wr_deg = float(np.mean(degradations_wr)) if degradations_wr else 0.0
    avg_exp_deg = float(np.mean(degradations_exp)) if degradations_exp else 0.0

    # Rapport
    report = _format_wf_report(
        instrument, windows, total_trades, agg_wr, agg_exp, agg_pf,
        total_r, max_dd, wr_std, exp_std, avg_wr_deg, avg_exp_deg,
    )

    return WalkForwardResult(
        instrument=instrument,
        n_windows=len(windows),
        windows=windows,
        total_oos_trades=total_trades,
        aggregate_wr=agg_wr,
        aggregate_exp_r=agg_exp,
        aggregate_pf=agg_pf,
        aggregate_total_r=total_r,
        max_dd_pct=max_dd,
        wr_std=wr_std,
        exp_std=exp_std,
        is_oos_wr_degradation=avg_wr_deg,
        is_oos_exp_degradation=avg_exp_deg,
        report=report,
    )


def _format_wf_report(
    instrument, windows, total_trades, agg_wr, agg_exp, agg_pf,
    total_r, max_dd, wr_std, exp_std, wr_deg, exp_deg,
) -> str:
    lines = [
        f"\n{'='*70}",
        f"  WALK-FORWARD RESULTS — {instrument}",
        f"{'='*70}",
        f"  {len(windows)} fenêtres  |  {total_trades} trades OOS total",
        "",
        f"  {'Window':<8s} {'IS Period':<25s} {'OOS Period':<25s} "
        f"{'Tr':>4s} {'WR':>5s} {'Exp(R)':>7s} {'PF':>5s}",
        f"  {'-'*80}",
    ]
    for w in windows:
        m = w.oos_metrics
        lines.append(
            f"  {w.window_idx+1:<8d} {w.is_start}→{w.is_end}  "
            f"{w.oos_start}→{w.oos_end}  "
            f"{m.n_trades:>4d} {m.win_rate:>4.0%} {m.expectancy_r:>+6.3f} "
            f"{m.profit_factor:>5.2f}"
        )

    lines += [
        f"  {'-'*80}",
        f"  AGRÉGÉ OOS:  {total_trades} trades  WR {agg_wr:.1%}  "
        f"Exp {agg_exp:+.3f}R  PF {agg_pf:.2f}  Total {total_r:+.1f}R  MaxDD {max_dd:.1f}%",
        "",
        f"  Stabilité:    WR σ={wr_std:.1%}   Exp σ={exp_std:.3f}R",
        f"  Dégradation:  IS→OOS WR {wr_deg:+.1%}   Exp {exp_deg:+.3f}R",
        "",
    ]

    # Verdict
    passed = agg_exp > 0 and agg_wr >= 0.50
    stable = wr_std < 0.15 and exp_std < 0.10
    if passed and stable:
        lines.append("  VERDICT: PASS — Expectancy positive et stable sur fenêtres glissantes")
    elif passed:
        lines.append("  VERDICT: MARGINAL — Expectancy positive mais instable entre fenêtres")
    else:
        lines.append("  VERDICT: FAIL — Expectancy négative ou WR < 50% en OOS agrégé")

    lines.append(f"{'='*70}")
    return "\n".join(lines)


def run_walk_forward_multi(
    instruments: list[str],
    **kwargs,
) -> dict[str, WalkForwardResult]:
    """Walk-forward sur plusieurs instruments."""
    results = {}
    for inst in instruments:
        try:
            results[inst] = run_walk_forward(inst, **kwargs)
        except Exception as e:
            print(f"\n  ERROR on {inst}: {e}")

    if results:
        _print_wf_synthesis(results)
    return results


def _print_wf_synthesis(results: dict[str, WalkForwardResult]):
    print(f"\n{'='*70}")
    print("  WALK-FORWARD SYNTHESIS")
    print(f"{'='*70}")
    print(f"  {'Instrument':<12s} {'Win':>4s} {'Trades':>7s} {'WR':>6s} "
          f"{'Exp(R)':>8s} {'PF':>6s} {'TotalR':>8s} {'MaxDD':>7s} {'Stable':>7s}")
    print(f"  {'-'*70}")

    total_trades = 0
    total_r = 0.0
    for inst, wf in sorted(results.items()):
        stable = "Yes" if wf.wr_std < 0.15 and wf.exp_std < 0.10 else "No"
        print(f"  {inst:<12s} {wf.n_windows:>4d} {wf.total_oos_trades:>7d} "
              f"{wf.aggregate_wr:>5.0%} {wf.aggregate_exp_r:>+7.3f} "
              f"{wf.aggregate_pf:>5.2f} {wf.aggregate_total_r:>+7.1f} "
              f"{wf.max_dd_pct:>6.1f}% {stable:>7s}")
        total_trades += wf.total_oos_trades
        total_r += wf.aggregate_total_r

    print(f"  {'-'*70}")
    print(f"  {'TOTAL':<12s} {'':>4s} {total_trades:>7d} {'':>6s} "
          f"{'':>8s} {'':>6s} {total_r:>+7.1f}")
    print(f"{'='*70}")


# ── CLI ──────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Arabesque Backtest Runner")
    parser.add_argument("instruments", nargs="+", help="Instrument(s) ex: XRPUSD SOLUSD")
    parser.add_argument("--period", default="730d", help="Période Yahoo ex: 730d (défaut)")
    parser.add_argument("--start", default=None, help="Date début YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="Date fin YYYY-MM-DD")
    parser.add_argument("--strategy", default="mean_reversion",
                        choices=["mean_reversion", "trend", "combined"])
    parser.add_argument("--split", type=float, default=0.70, help="Ratio in-sample (défaut 0.70)")
    parser.add_argument("--risk", type=float, default=0.5, help="Risk %% par trade (défaut 0.5)")
    parser.add_argument("--balance", type=float, default=100_000, help="Capital de départ")
    parser.add_argument("--no-filter", action="store_true", help="Désactiver le SignalFilter")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    cfg = BacktestConfig(
        start_balance=args.balance,
        risk_per_trade_pct=args.risk,
        signal_filter_path=None if args.no_filter else "config/signal_filters.yaml",
        verbose=args.verbose,
    )

    if len(args.instruments) == 1:
        run_backtest(
            args.instruments[0],
            period=args.period,
            start=args.start,
            end=args.end,
            bt_config=cfg,
            split_pct=args.split,
            verbose=True,
            strategy=args.strategy,
        )
    else:
        run_multi_instrument(
            args.instruments,
            period=args.period,
            start=args.start,
            end=args.end,
            bt_config=cfg,
            split_pct=args.split,
            verbose=True,
            strategy=args.strategy,
        )
