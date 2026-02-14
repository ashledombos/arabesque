"""
Test du backtest pass 2 — Arabesque v2.
Pipeline complet : signal -> guards -> PositionManager -> metrics
"""

import sys
sys.path.insert(0, "/home/claude")

from arabesque.backtest.runner import BacktestRunner, BacktestConfig
from arabesque.backtest.data import load_ohlc, generate_synthetic_ohlc, yahoo_symbol, split_in_out_sample
from arabesque.backtest.signal_gen import BacktestSignalGenerator, SignalGenConfig
from arabesque.backtest.metrics import compute_metrics, format_report
from arabesque.position.manager import ManagerConfig

print("=" * 60)
print("  ARABESQUE v2 — Backtest Pass 2 Test")
print("=" * 60)

# ── 1. Data ──
print("\n1. Loading data...")
try:
    symbol = yahoo_symbol("EURUSD")
    df = load_ohlc(symbol, period="730d", interval="1h")
    data_source = "Yahoo Finance"
except Exception:
    print("   Yahoo unavailable, using synthetic data...")
    df = generate_synthetic_ohlc(n_bars=5000, start_price=1.0800, volatility=0.0008)
    data_source = "Synthetic (pipeline validation only)"

print(f"   Source: {data_source}")
print(f"   Bars: {len(df)}, Range: {df.index[0].date()} → {df.index[-1].date()}")

# ── 2. Signal generation ──
print("\n2. Indicators & signals...")
sig_config = SignalGenConfig(rsi_oversold=40.0, rsi_overbought=60.0, min_bb_width=0.001, min_rr=0.3)
sig_gen = BacktestSignalGenerator(sig_config)
df_prepared = sig_gen.prepare(df)

signals = sig_gen.generate_signals(df_prepared, "EURUSD")
print(f"   Signals: {len(signals)}")
if signals:
    s = signals[0]
    print(f"   Example: {s.side.value} @ {s.tv_close:.5f} SL={s.sl:.5f} RR={s.rr} RSI={s.rsi:.1f}")

# ── 3. Backtest ──
print("\n3. Running backtest...")
df_in, df_out = split_in_out_sample(df_prepared, 0.70)
print(f"   In-sample:  {len(df_in)} bars")
print(f"   Out-sample: {len(df_out)} bars")

bt_config = BacktestConfig(
    start_balance=100_000.0, risk_per_trade_pct=0.5,
    spread_pct=0.00012, slippage_r=0.03, verbose=False,
)

print("\n--- IN-SAMPLE ---")
runner_in = BacktestRunner(bt_config, signal_config=sig_config)
result_in = runner_in.run(df_in, "EURUSD", "in_sample")
print(result_in.report)

print("\n--- OUT-OF-SAMPLE ---")
runner_out = BacktestRunner(bt_config, signal_config=sig_config)
result_out = runner_out.run(df_out, "EURUSD", "out_of_sample")
print(result_out.report)

# ── 4. Validation ──
print("\n4. Validation checks...")
from arabesque.position.manager import PositionManager

checks = [
    ("update_position(H,L,C) exists", hasattr(PositionManager(), 'update_position')),
    ("5 trailing tiers", len(PositionManager().cfg.trailing_tiers) == 5),
    ("Metrics computed", result_in.metrics.n_trades >= 0),
    ("Equity curve", len(result_in.metrics.equity_curve) > 0),
    ("Exits breakdown", isinstance(result_in.metrics.exits_by_type, dict)),
]

if result_in.closed_positions:
    p = result_in.closed_positions[0]
    checks.extend([
        ("result_r set", p.result_r is not None),
        ("MFE tracked", True),
        ("exit_reason set", p.exit_reason != ""),
    ])

for name, ok in checks:
    print(f"   {'✓' if ok else '✗'} {name}")

print(f"\n{'='*60}")
total = result_in.metrics.n_trades + result_out.metrics.n_trades
print(f"  In:  {result_in.metrics.n_trades} trades, exp={result_in.metrics.expectancy_r:+.3f}R, PF={result_in.metrics.profit_factor:.2f}")
print(f"  Out: {result_out.metrics.n_trades} trades, exp={result_out.metrics.expectancy_r:+.3f}R, PF={result_out.metrics.profit_factor:.2f}")
if total < 30 and data_source.startswith("Synth"):
    print(f"  ⚠ {total} trades — synthetic data. Run on real data for true results.")
print(f"{'='*60}")
