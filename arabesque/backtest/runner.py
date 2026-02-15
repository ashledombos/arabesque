"""
Arabesque v2 — Backtest Runner (Pass 2).

Utilise le MÊME PositionManager que le live. Zéro divergence.

Architecture :
1. Charge les données OHLC (Yahoo Finance 1H)
2. Calcule les indicateurs (même formules que Pine)
3. Itère bar-by-bar :
   a. Vérifie si un signal est émis (bougie confirmée)
   b. Si signal : guards → sizing → open_position (fill = open bougie suivante)
   c. Pour chaque position ouverte : update_position(H, L, C, indicators)
   d. Met à jour AccountState (daily reset, P&L)
   e. Met à jour counterfactuals
4. Calcule les métriques

Spread + slippage simulés :
- Spread = spread_pct * prix (configurable, réaliste par instrument)
- Slippage = slippage_r * ATR (ajouté au fill)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
import numpy as np
import pandas as pd

from arabesque.models import Signal, Position, Decision, DecisionType, Side
from arabesque.guards import Guards, PropConfig, ExecConfig, AccountState
from arabesque.position.manager import PositionManager, ManagerConfig
from arabesque.backtest.signal_gen import BacktestSignalGenerator, SignalGenConfig
from arabesque.backtest.metrics import (
    BacktestMetrics, compute_metrics, slippage_sensitivity, format_report,
)
from arabesque.backtest.data import load_ohlc, split_in_out_sample, yahoo_symbol


@dataclass
class BacktestConfig:
    """Configuration du backtest."""
    # Compte
    start_balance: float = 100_000.0
    risk_per_trade_pct: float = 0.5

    # Spread simulé (en fraction du prix, ex: 0.0001 = 1 pip pour EURUSD)
    spread_fixed: float = 0.0       # Si > 0, spread fixe en points
    spread_pct: float = 0.00015     # Sinon, spread = prix * spread_pct

    # Slippage simulé (en multiples d'ATR, ajouté au fill)
    slippage_r: float = 0.03        # 3% d'un ATR

    # Prop firm
    daily_dd_limit_pct: float = 3.0
    total_dd_limit_pct: float = 8.0
    max_positions: int = 3

    # Cooldown entre signaux sur le même instrument (barres)
    signal_cooldown_bars: int = 5

    # Verbose
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
        # Accept any generator with prepare() + generate_signals()
        if signal_generator is not None:
            self.sig_gen = signal_generator
        else:
            self.sig_gen = BacktestSignalGenerator(signal_config)
        self.prop_cfg = prop_config or PropConfig(
            risk_per_trade_pct=self.bt_cfg.risk_per_trade_pct,
            max_positions=self.bt_cfg.max_positions,
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

    def run(
        self,
        df: pd.DataFrame,
        instrument: str = "",
        sample_type: str = "",
    ) -> BacktestResult:
        """Exécute le backtest sur un DataFrame OHLC préparé.

        Le DataFrame doit déjà avoir les indicateurs calculés
        (appeler sig_gen.prepare() avant).

        Args:
            df: DataFrame OHLC avec indicateurs
            instrument: Nom de l'instrument
            sample_type: "in_sample" ou "out_of_sample"

        Returns:
            BacktestResult complet
        """
        # Reset state
        self.manager = PositionManager(self.manager.cfg)
        self.account = AccountState(
            balance=self.bt_cfg.start_balance,
            equity=self.bt_cfg.start_balance,
            start_balance=self.bt_cfg.start_balance,
            daily_start_balance=self.bt_cfg.start_balance,
        )

        # Pré-générer les signaux avec leurs index
        signals_by_bar = self._precompute_signals(df, instrument)

        all_decisions: list[Decision] = []
        n_signals = 0
        n_rejected = 0
        rejection_reasons: dict[str, int] = {}
        current_date = None
        last_signal_bar: dict[str, int] = {}  # cooldown tracker

        n_bars = len(df)

        for i in range(len(df)):
            row = df.iloc[i]
            bar_date = df.index[i]
            high = row["High"]
            low = row["Low"]
            close = row["Close"]
            opn = row["Open"]

            # ── Daily reset ──
            row_date = bar_date.date()
            if current_date is not None and row_date != current_date:
                self.account.new_day()
            current_date = row_date

            # ── Update positions ouvertes ──
            indicators = {
                "rsi": row.get("rsi", 50),
                "cmf": row.get("cmf", 0),
                "bb_width": row.get("bb_width", 0.01),
                "ema200": row.get("ema_slow", 0),
            }

            for pos in list(self.manager.open_positions):
                decisions = self.manager.update_position(pos, high, low, close, indicators)
                all_decisions.extend(decisions)

                # Si position fermée, mettre à jour le compte
                if not pos.is_open and pos.result_r is not None:
                    # Fix timestamp for backtest
                    pos.ts_exit = bar_date.to_pydatetime() if hasattr(bar_date, 'to_pydatetime') else bar_date
                    pnl = pos.result_r * pos.risk_cash
                    self.account.equity += pnl
                    self.account.balance += pnl
                    self.account.daily_pnl += pnl
                    self.account.open_positions -= 1
                    if pos.instrument in self.account.open_instruments:
                        self.account.open_instruments.remove(pos.instrument)

            # ── Update counterfactuals ──
            self.manager.update_counterfactuals(instrument, high, low, close)

            # ── Check signal sur cette bougie ──
            # Anti-lookahead : le signal est sur la bougie confirmée (i),
            # l'entrée sera au OPEN de i+1
            if i in signals_by_bar and i + 1 < len(df):
                signal = signals_by_bar[i]
                n_signals += 1

                # Cooldown check
                last_bar = last_signal_bar.get(instrument, -999)
                if i - last_bar < self.bt_cfg.signal_cooldown_bars:
                    n_rejected += 1
                    rejection_reasons["cooldown"] = rejection_reasons.get("cooldown", 0) + 1
                    continue

                # Simuler le fill au OPEN de la bougie suivante
                next_bar = df.iloc[i + 1]
                next_bar_ts = df.index[i + 1]
                fill_price = next_bar["Open"]

                # Ajouter spread
                spread = self._compute_spread(fill_price)
                if signal.side == Side.LONG:
                    bid = fill_price
                    ask = fill_price + spread
                    fill_price = ask   # LONG = buy @ ask
                else:
                    bid = fill_price - spread
                    ask = fill_price
                    fill_price = bid   # SHORT = sell @ bid

                # Ajouter slippage
                atr = signal.atr if signal.atr > 0 else row.get("atr", 0)
                slip = self.bt_cfg.slippage_r * atr
                if signal.side == Side.LONG:
                    fill_price += slip
                else:
                    fill_price -= slip

                # ── FIX: Pour le guard slippage, comparer au prix d'exécution,
                # pas au close de la bougie précédente.
                # En live: signal fire → fill en secondes → gap minime.
                # En backtest: signal fire → fill 1h plus tard → gap = mouvement normal.
                # On update tv_close au open de la barre suivante pour le guard.
                original_tv_close = signal.tv_close
                signal.tv_close = next_bar["Open"]

                # Guards
                ok, decision = self.guards.check_all(signal, self.account, bid, ask)
                all_decisions.append(decision)

                # Restaurer tv_close pour le PositionManager
                signal.tv_close = original_tv_close

                if not ok:
                    n_rejected += 1
                    reason = decision.reject_reason.value if decision.reject_reason else "unknown"
                    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                    if self.bt_cfg.verbose:
                        print(f"  [{bar_date}] REJECTED: {reason}")
                    continue

                # Sizing
                sizing = self.guards.compute_sizing(signal, self.account)
                risk_cash = sizing["risk_cash"]
                risk_distance = sizing["risk_distance"]

                if risk_cash <= 0 or risk_distance <= 0:
                    n_rejected += 1
                    rejection_reasons["sizing_zero"] = rejection_reasons.get("sizing_zero", 0) + 1
                    continue

                # Volume (lots) — taille du contrat dépend de l'instrument
                contract_size = self._contract_size(instrument, fill_price)
                volume = risk_cash / (risk_distance * contract_size)
                volume = int(volume * 100) / 100  # Arrondi 0.01 lot

                if volume <= 0:
                    n_rejected += 1
                    rejection_reasons["volume_zero"] = rejection_reasons.get("volume_zero", 0) + 1
                    continue

                # Ouvrir la position
                pos = self.manager.open_position(signal, fill_price, risk_cash, volume)

                # Fix timestamp for backtest (pas datetime.now())
                pos.ts_entry = next_bar_ts.to_pydatetime() if hasattr(next_bar_ts, 'to_pydatetime') else next_bar_ts

                self.account.open_positions += 1
                self.account.open_instruments.append(instrument)
                self.account.daily_trades += 1
                last_signal_bar[instrument] = i

                if self.bt_cfg.verbose:
                    print(f"  [{bar_date}] OPEN {signal.side.value} @ {fill_price:.5f} "
                          f"SL={pos.sl:.5f} R={pos.R:.5f}")

            # Progress
            if self.bt_cfg.verbose and i > 0 and i % self.bt_cfg.progress_every == 0:
                n_open = len(self.manager.open_positions)
                n_closed = len(self.manager.closed_positions)
                print(f"  Bar {i}/{n_bars} | Open: {n_open} | Closed: {n_closed} | "
                      f"Equity: {self.account.equity:,.0f}")

        # ── Forcer la fermeture des positions restantes ──
        for pos in list(self.manager.open_positions):
            last_close = df.iloc[-1]["Close"]
            d = self.manager._close_position(
                pos, last_close, DecisionType.EXIT_TIME_STOP,
                "End of data — forced close"
            )
            all_decisions.append(d)
            if pos.result_r is not None:
                pnl = pos.result_r * pos.risk_cash
                self.account.equity += pnl
                self.account.balance += pnl

        # ── Métriques ──
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

        # Slippage sensitivity
        results_r = [p.result_r for p in closed if p.result_r is not None]
        if results_r:
            metrics.slippage_sensitivity = slippage_sensitivity(
                metrics.expectancy_r, results_r, self.bt_cfg.slippage_r
            )

        report = format_report(metrics)

        return BacktestResult(
            config=self.bt_cfg,
            metrics=metrics,
            closed_positions=closed,
            all_decisions=all_decisions,
            report=report,
        )

    def _precompute_signals(
        self, df: pd.DataFrame, instrument: str
    ) -> dict[int, Signal]:
        """Pré-calcule les signaux pour chaque barre.

        Retourne un dict {bar_index: Signal}.
        Gère deux formats de retour du signal generator :
        - list[Signal] (mean-reversion) → lookup par timestamp
        - list[tuple[int, Signal]] (trend/combined) → index direct
        """
        signals = self.sig_gen.generate_signals(df, instrument)
        signal_map: dict[int, Signal] = {}

        for item in signals:
            if isinstance(item, tuple) and len(item) == 2:
                # Format (bar_index, Signal)
                idx, sig = item
                signal_map[int(idx)] = sig
            else:
                # Format Signal direct → lookup par timestamp
                sig = item
                try:
                    idx = df.index.get_loc(sig.timestamp)
                    if isinstance(idx, (int, np.integer)):
                        signal_map[int(idx)] = sig
                except (KeyError, AttributeError):
                    continue
        return signal_map

    def _compute_spread(self, price: float) -> float:
        """Calcule le spread simulé."""
        if self.bt_cfg.spread_fixed > 0:
            return self.bt_cfg.spread_fixed
        return price * self.bt_cfg.spread_pct

    @staticmethod
    def _contract_size(instrument: str, price: float) -> float:
        """Taille du contrat pour le sizing.

        Retourne la valeur en devise de base par lot (1.0 lot).
        FX = 100,000 unités. Gold = 100 oz. Indices = $1/point. etc.
        """
        inst = instrument.upper()

        # FX pairs (6 chars ou contient USD + autre devise)
        fx_currencies = {
            "EUR", "GBP", "USD", "CHF", "CAD", "AUD", "NZD", "JPY",
            "CNH", "CZK", "HKD", "MXN", "NOK", "PLN", "SEK", "SGD",
            "ZAR", "ILS", "HUF",
        }
        if len(inst) == 6 and inst[:3] in fx_currencies and inst[3:] in fx_currencies:
            return 100_000

        # Métaux précieux
        if inst in ("XAUUSD", "GOLD"):
            return 100  # 100 oz per lot
        if inst in ("XAGUSD", "SILVER"):
            return 5_000  # 5000 oz per lot
        if inst in ("XPTUSD", "PLATINUM", "XPDUSD", "PALLADIUM"):
            return 100
        if inst in ("COPPER",):
            return 25_000  # 25000 lbs

        # Crypto (1 lot = 1 unité de la crypto)
        crypto = {"BTC", "ETH", "LTC", "SOL", "BNB", "BCH", "XRP", "DOGE",
                  "ADA", "DOT", "XMR", "DASH", "NEO", "UNI", "XLM", "AAVE",
                  "MANA", "IMX", "GRT", "ETC", "ALGO", "NEAR", "LINK", "AVAX",
                  "XTZ", "FET", "ICP", "SAND", "GAL", "VET"}
        if inst in crypto:
            return 1  # 1 lot = 1 coin

        # Indices CFD (1 lot = $1 per point typiquement)
        indices = {"SP500", "NAS100", "US30", "US2000", "GER40", "UK100",
                   "FRA40", "EU50", "IBEX35", "AEX25", "JPN225", "HK50",
                   "AUS200", "USTEC", "USDX"}
        if inst in indices:
            return 1  # CFD : 1 lot = $1/point

        # Energies
        if inst in ("USOIL", "UKOIL", "BRENT"):
            return 1_000  # 1000 barils
        if inst in ("NATGAS",):
            return 10_000  # 10000 MMBtu

        # Commodités agricoles
        agri = {"COCOA", "COFFEE", "CORN", "COTTON", "SOYBEAN", "WHEAT", "SUGAR"}
        if inst in agri:
            return 100  # Simplifié

        # Actions (1 lot = 1 action en CFD)
        return 1


# ── Convenience functions ────────────────────────────────────────────

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
    """Lance un backtest complet avec in-sample / out-of-sample.

    Args:
        strategy: "mean_reversion", "trend", ou "combined"

    Returns:
        (result_in, result_out)
    """
    cfg = bt_config or BacktestConfig(verbose=verbose)
    if verbose:
        cfg.verbose = True

    # 1. Charger les données
    symbol = yahoo_symbol(instrument)
    strat_label = strategy.upper().replace("_", " ")
    print(f"\n{'='*60}")
    print(f"  ARABESQUE BACKTEST — {instrument} ({symbol})")
    print(f"  Strategy: {strat_label}")
    print(f"{'='*60}")
    print(f"  Loading data...")

    df = load_ohlc(symbol, period=period, start=start, end=end,
                   instrument=instrument, data_root=data_root)
    print(f"  Loaded {len(df)} bars from {df.index[0]} to {df.index[-1]}")

    # 2. Créer le signal generator
    if strategy == "trend":
        from arabesque.backtest.signal_gen_trend import TrendSignalGenerator, TrendSignalConfig
        sig_gen = TrendSignalGenerator(TrendSignalConfig())
    elif strategy == "combined":
        from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
        sig_gen = CombinedSignalGenerator(mr_config=signal_config)
    else:
        sig_gen = BacktestSignalGenerator(signal_config)

    df_prepared = sig_gen.prepare(df)
    print(f"  Indicators computed")

    # 3. Split in-sample / out-of-sample
    df_in, df_out = split_in_out_sample(df_prepared, split_pct)
    print(f"  In-sample:  {len(df_in)} bars ({df_in.index[0].date()} → {df_in.index[-1].date()})")
    print(f"  Out-sample: {len(df_out)} bars ({df_out.index[0].date()} → {df_out.index[-1].date()})")

    # 4. Run in-sample
    print(f"\n--- IN-SAMPLE ---")
    runner_in = BacktestRunner(cfg, manager_config, signal_config,
                               signal_generator=sig_gen)
    result_in = runner_in.run(df_in, instrument, "in_sample")
    print(result_in.report)

    # 5. Run out-of-sample
    print(f"\n--- OUT-OF-SAMPLE ---")
    runner_out = BacktestRunner(cfg, manager_config, signal_config,
                                signal_generator=sig_gen)
    result_out = runner_out.run(df_out, instrument, "out_of_sample")
    print(result_out.report)

    # 6. Comparaison
    _print_comparison(result_in.metrics, result_out.metrics)

    return result_in, result_out


def run_multi_instrument(
    instruments: list[str],
    **kwargs,
) -> dict[str, tuple[BacktestResult, BacktestResult]]:
    """Lance le backtest sur plusieurs instruments."""
    results = {}
    for inst in instruments:
        try:
            result = run_backtest(inst, **kwargs)
            results[inst] = result
        except Exception as e:
            print(f"\n  ERROR on {inst}: {e}")
            continue

    # Synthèse
    if results:
        _print_synthesis(results)

    return results


def _print_comparison(m_in: BacktestMetrics, m_out: BacktestMetrics):
    """Compare in-sample vs out-of-sample."""
    print(f"\n{'='*60}")
    print(f"  COMPARISON IN vs OUT")
    print(f"{'='*60}")
    print(f"  {'Metric':<25s} {'In-Sample':>12s} {'Out-Sample':>12s} {'Delta':>12s}")
    print(f"  {'-'*61}")

    rows = [
        ("Trades", f"{m_in.n_trades}", f"{m_out.n_trades}", ""),
        ("Win Rate", f"{m_in.win_rate:.1%}", f"{m_out.win_rate:.1%}",
         f"{m_out.win_rate - m_in.win_rate:+.1%}"),
        ("Expectancy (R)", f"{m_in.expectancy_r:+.3f}", f"{m_out.expectancy_r:+.3f}",
         f"{m_out.expectancy_r - m_in.expectancy_r:+.3f}"),
        ("Profit Factor", f"{m_in.profit_factor:.2f}", f"{m_out.profit_factor:.2f}",
         f"{m_out.profit_factor - m_in.profit_factor:+.2f}"),
        ("Max DD %", f"{m_in.max_dd_pct:.1f}%", f"{m_out.max_dd_pct:.1f}%",
         f"{m_out.max_dd_pct - m_in.max_dd_pct:+.1f}%"),
        ("Disqual Days", f"{m_in.n_disqualifying_days}", f"{m_out.n_disqualifying_days}", ""),
    ]

    for name, v_in, v_out, delta in rows:
        print(f"  {name:<25s} {v_in:>12s} {v_out:>12s} {delta:>12s}")
    print(f"{'='*60}")


def _print_synthesis(results: dict[str, tuple[BacktestResult, BacktestResult]]):
    """Synthèse multi-instrument."""
    print(f"\n{'='*60}")
    print(f"  MULTI-INSTRUMENT SYNTHESIS")
    print(f"{'='*60}")
    print(f"  {'Instrument':<12s} {'Trades':>7s} {'WR':>6s} {'Exp(R)':>8s} {'PF':>6s} {'MaxDD':>7s} {'Disq':>5s}")
    print(f"  {'-'*51}")

    for inst, (res_in, res_out) in results.items():
        m = res_out.metrics  # Montrer l'out-of-sample
        print(f"  {inst:<12s} {m.n_trades:>7d} {m.win_rate:>5.0%} "
              f"{m.expectancy_r:>+7.3f} {m.profit_factor:>5.2f} "
              f"{m.max_dd_pct:>6.1f}% {m.n_disqualifying_days:>5d}")
    print(f"{'='*60}")
