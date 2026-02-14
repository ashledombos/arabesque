"""Quick test for Arabesque v2 pipeline."""

import sys
sys.path.insert(0, "/home/claude/arabesque_v2")

from arabesque.models import Signal, Side
from arabesque.guards import Guards, PropConfig, ExecConfig, AccountState
from arabesque.position.manager import PositionManager, ManagerConfig
from arabesque.audit import AuditLogger

# 1. Create signal
signal = Signal.from_webhook_json({
    "symbol": "EURUSD", "side": "buy", "tf": "60",
    "tv_close": 1.07650, "tv_open": 1.07700,
    "sl": 1.07500, "tp_indicative": 1.07850, "rr": 1.33,
    "atr": 0.00120, "rsi": 28.5, "cmf": -0.15,
    "bb_lower": 1.07660, "bb_mid": 1.07850, "bb_upper": 1.08040,
    "bb_width": 0.0035, "regime": "bull_range", "max_spread_atr": 0.30,
})

# 2. Guards
guards = Guards(PropConfig(), ExecConfig())
account = AccountState()  # Already initialized to 100k
audit = AuditLogger(log_dir="/tmp/arabesque_test_logs")

# Broker quote (realistic dry run)
bid, ask = 1.07648, 1.07660

ok, decision = guards.check_all(signal, account, bid, ask)
audit.log_decision(decision)
print(f"Guards: {'PASS' if ok else 'FAIL'} — {decision.reason}")

if not ok:
    sys.exit(1)

# 3. Sizing
sizing = guards.compute_sizing(signal, account)
print(f"Sizing: risk_cash={sizing['risk_cash']}, risk_dist={sizing['risk_distance']:.5f}")

# 4. Open position (fill = ask for LONG)
manager = PositionManager()
pos = manager.open_position(signal, fill_price=ask, risk_cash=sizing["risk_cash"], volume=0.33)
print(f"Position opened: {pos.summary()}")
print(f"  Entry(fill): {pos.entry:.5f}  SL(recalc): {pos.sl:.5f}  R={pos.R:.5f}")

# 5. Simulate 15 bars with OHLC
import random
random.seed(42)
price = ask

print("\nSimulating 15 bars with OHLC:")
for i in range(15):
    noise = random.gauss(0.00010, 0.00050)
    price += noise
    high = price + abs(random.gauss(0, 0.0003))
    low = price - abs(random.gauss(0, 0.0003))
    close = price + random.gauss(0, 0.0001)

    indicators = {
        "rsi": 30 + random.gauss(5, 8),
        "cmf": random.gauss(-0.1, 0.15),
        "bb_width": 0.0035,
        "ema200": 1.07500,
    }

    decisions = manager.update_position(pos, high, low, close, indicators)

    status = f"  Bar {i+1:2d}: H={high:.5f} L={low:.5f} C={close:.5f} | " \
             f"profit={pos.current_r:+.2f}R MFE={pos.mfe_r:.2f}R SL={pos.sl:.5f}"
    if decisions:
        status += f" | {', '.join(d.decision_type.value for d in decisions)}"
    print(status)

    if not pos.is_open:
        print(f"  >>> CLOSED: {pos.exit_reason} @ {pos.exit_price:.5f} = {pos.result_r:+.2f}R")
        break

print(f"\nAudit: {audit.summary()}")
print("\nv2 pipeline OK ✓")
