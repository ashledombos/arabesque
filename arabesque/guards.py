"""
Arabesque v2 — Guards (filtres pré-exécution).

CORRECTIONS vs v1 :
1. broker_quote est OBLIGATOIRE (même en dry-run, le DryRunAdapter le fournit)
2. AccountState a des valeurs initiales cohérentes
3. Sizing plafonne au DD restant (pas juste risk_per_trade)
4. Guard duplicate instrument (v0.1)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from arabesque.models import Signal, Decision, Counterfactual, DecisionType, RejectReason, Side


@dataclass
class PropConfig:
    max_daily_dd_pct: float = 3.0
    max_total_dd_pct: float = 8.0
    max_positions: int = 3
    max_daily_trades: int = 10
    risk_per_trade_pct: float = 0.5


@dataclass
class ExecConfig:
    max_spread_atr: float = 0.15
    max_slippage_atr: float = 0.10
    signal_expiry_sec: int = 300
    min_rr: float = 0.5


@dataclass
class AccountState:
    """État du compte — DOIT être initialisé avec des valeurs réelles."""
    balance: float = 100_000.0
    equity: float = 100_000.0
    start_balance: float = 100_000.0
    daily_start_balance: float = 100_000.0
    daily_pnl: float = 0.0
    open_positions: int = 0
    daily_trades: int = 0
    open_instruments: list[str] = field(default_factory=list)

    @property
    def daily_dd_pct(self) -> float:
        if self.start_balance == 0:
            return 0.0
        return ((self.equity - self.daily_start_balance) / self.start_balance) * 100

    @property
    def total_dd_pct(self) -> float:
        if self.start_balance == 0:
            return 0.0
        return ((self.equity - self.start_balance) / self.start_balance) * 100

    def new_day(self):
        self.daily_pnl = 0.0
        self.daily_start_balance = self.equity
        self.daily_trades = 0


class Guards:
    """Évalue tous les guards. Retourne (ok, Decision)."""

    def __init__(self, prop: PropConfig, exec_cfg: ExecConfig):
        self.prop = prop
        self.exec = exec_cfg

    def check_all(
        self,
        signal: Signal,
        account: AccountState,
        broker_bid: float,
        broker_ask: float,
    ) -> tuple[bool, Decision]:
        """Vérifie tous les guards.

        broker_bid/ask sont OBLIGATOIRES.
        En dry-run, le DryRunAdapter les fournit.
        """
        spread = broker_ask - broker_bid
        atr = signal.atr

        checks = [
            self._daily_dd(account),
            self._total_dd(account),
            self._max_positions(account),
            self._daily_trades(account),
            self._duplicate_instrument(signal, account),
            self._bb_squeeze(signal),
        ]

        # Checks nécessitant ATR
        if atr > 0:
            checks.append(self._spread(spread, atr, signal.max_spread_atr))
            checks.append(self._slippage(signal, broker_bid, broker_ask, atr))

        for ok, reason_enum, detail in checks:
            if not ok:
                return self._reject(signal, reason_enum, detail,
                                    broker_ask if signal.side == Side.LONG else broker_bid,
                                    spread)

        # Accepté
        fill_est = broker_ask if signal.side == Side.LONG else broker_bid
        decision = Decision(
            decision_type=DecisionType.SIGNAL_ACCEPTED,
            signal_id=signal.signal_id,
            instrument=signal.instrument,
            reason="All guards passed",
            price_at_decision=fill_est,
            spread_at_decision=spread,
            metadata={
                "spread_atr": round(spread / atr, 3) if atr > 0 else 0,
                "n_open": account.open_positions,
                "daily_dd": round(account.daily_dd_pct, 2),
            },
        )
        return True, decision

    # ── Individual checks ────────────────────────────────────────────

    def _daily_dd(self, a: AccountState):
        if a.daily_dd_pct <= -self.prop.max_daily_dd_pct:
            return False, RejectReason.DAILY_DD_LIMIT, f"DD daily {a.daily_dd_pct:.1f}%"
        return True, None, ""

    def _total_dd(self, a: AccountState):
        if a.total_dd_pct <= -self.prop.max_total_dd_pct:
            return False, RejectReason.MAX_DD_LIMIT, f"DD total {a.total_dd_pct:.1f}%"
        return True, None, ""

    def _max_positions(self, a: AccountState):
        if a.open_positions >= self.prop.max_positions:
            return False, RejectReason.MAX_POSITIONS, f"{a.open_positions}/{self.prop.max_positions}"
        return True, None, ""

    def _daily_trades(self, a: AccountState):
        if a.daily_trades >= self.prop.max_daily_trades:
            return False, RejectReason.MAX_POSITIONS, f"trades {a.daily_trades}/{self.prop.max_daily_trades}"
        return True, None, ""

    def _duplicate_instrument(self, signal: Signal, a: AccountState):
        if signal.instrument in a.open_instruments:
            return False, RejectReason.DUPLICATE_INSTRUMENT, f"déjà ouvert: {signal.instrument}"
        return True, None, ""

    def _bb_squeeze(self, signal: Signal):
        if signal.bb_width < 0.003:
            return False, RejectReason.BB_SQUEEZE, f"bb_width={signal.bb_width:.4f}"
        return True, None, ""

    def _spread(self, spread: float, atr: float, max_ratio: float):
        ratio = spread / atr
        if ratio > max_ratio:
            return False, RejectReason.SPREAD_TOO_WIDE, f"spread {ratio:.2f}ATR > {max_ratio}"
        return True, None, ""

    def _slippage(self, signal: Signal, bid: float, ask: float, atr: float):
        """Slippage = |fill estimé - tv_close| / ATR."""
        fill_est = ask if signal.side == Side.LONG else bid
        slip = abs(fill_est - signal.tv_close) / atr
        if slip > self.exec.max_slippage_atr:
            return False, RejectReason.SLIPPAGE_TOO_HIGH, f"slip {slip:.3f}ATR > {self.exec.max_slippage_atr}"
        return True, None, ""

    # ── Reject + counterfactual ──────────────────────────────────────

    def _reject(self, signal: Signal, reason: RejectReason, detail: str,
                price: float, spread: float) -> tuple[bool, Decision]:
        decision = Decision(
            decision_type=DecisionType.SIGNAL_REJECTED,
            signal_id=signal.signal_id,
            instrument=signal.instrument,
            reason=detail,
            reject_reason=reason,
            price_at_decision=price,
            spread_at_decision=spread,
        )
        return False, decision

    # ── Sizing ───────────────────────────────────────────────────────

    def compute_sizing(self, signal: Signal, account: AccountState) -> dict:
        """Calcule le risk_cash plafonné au DD restant."""
        entry = signal.tv_close
        sl = signal.sl
        if entry == 0 or sl == 0:
            return {"risk_cash": 0, "risk_distance": 0, "error": "missing entry/sl"}

        risk_distance = abs(entry - sl)
        risk_pct_cash = account.start_balance * (self.prop.risk_per_trade_pct / 100)

        # Plafonner au DD restant
        remaining_daily = max(0, (self.prop.max_daily_dd_pct + account.daily_dd_pct) / 100 * account.start_balance)
        remaining_total = max(0, (self.prop.max_total_dd_pct + account.total_dd_pct) / 100 * account.start_balance)
        max_risk = min(risk_pct_cash, remaining_daily * 0.5, remaining_total * 0.3)
        risk_cash = max(0, min(risk_pct_cash, max_risk))

        return {
            "risk_cash": round(risk_cash, 2),
            "risk_distance": risk_distance,
        }
