"""
Arabesque v2 — Guards (filtres pré-exécution).

CORRECTIONS vs v1 :
1. broker_quote est OBLIGATOIRE (même en dry-run, le DryRunAdapter le fournit)
2. AccountState a des valeurs initiales cohérentes
3. Sizing plafonne au DD restant (pas juste risk_per_trade)
4. Guard duplicate instrument (v0.1)

CORRECTIONS v2.1 :
5. Pause automatique à (max_total_dd - 1%) pour éviter la zombie phase
6. Sizing progressif : réduction linéaire du risque selon DD restant

CORRECTIONS v2.2 :
7. Guard max_positions remplacé par guard d'exposition DD cumulée (open_risk_limit)
   - PropConfig.max_open_risk_pct : % du start_balance max en risque ouvert simultané
   - PropConfig.max_positions relevé à 10 (filet absolu anti-bug)
   - AccountState.open_risk_cash : somme des risk_cash des positions ouvertes
   Rationale : 19 instruments crypto corrélés → rafales de 8-12 signaux sur la même
   bougie. max_positions=3 rejetait des signaux valides alors que l'exposition réelle
   était faible (positions sizées à 70-80$ après réduction DD).
8. Corrige bug _daily_trades() : utilisait RejectReason.MAX_POSITIONS au lieu de MAX_DAILY_TRADES

CORRECTIONS v2.3 (2026-02-20) — TD-001 :
9. daily_dd_pct : diviseur corrigé start_balance → daily_start_balance
   Avant : ((equity - daily_start_balance) / start_balance) * 100
         → sous-estimait le DD journalier → guard DAILY_DD_LIMIT ne se déclenchait jamais
   Après : ((equity - daily_start_balance) / daily_start_balance) * 100
10. compute_sizing : remaining_daily corrigé de même (start_balance → daily_start_balance)
    pour cohérence avec daily_dd_pct

CORRECTIONS v2.4 (2026-02-20) — TD-007 :
11. Remplacement signal.tv_close → signal.close dans _slippage() et compute_sizing()
    Les alias tv_close/tv_open (héritage TradingView webhook) sont supprimés de models.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum

from arabesque.core.models import Signal, Decision, Counterfactual, DecisionType, RejectReason, Side

logger = logging.getLogger("arabesque.guards")


@dataclass
class PropConfig:
    max_daily_dd_pct: float = 3.0
    max_total_dd_pct: float = 8.0
    max_positions: int = 10           # Filet absolu anti-bug (relevé de 3 → 10)
    max_open_risk_pct: float = 2.0    # % du start_balance max en risque ouvert simultané
    max_daily_trades: int = 10
    risk_per_trade_pct: float = 0.45
    # v3.4 (2026-03-18): relevé de 0.40% à 0.45%.
    # Historique: 0.50% → DD 10.3% (breach FTMO), 0.40% → DD 8.2%.
    # Estimation 0.45% → DD ~9.2% sans protection.
    # Avec LiveMonitor actif (CAUTION/DANGER/EMERGENCY), le risk est
    # réduit automatiquement à ×0.50 / ×0.25 / ×0 selon le DD courant,
    # ce qui ramène le DD effectif bien en dessous de 9.2%.
    # Return reste > +90% (largement au-dessus du 10% target challenge).
    # Marge de sécurité avant le seuil fatal (en points de %).
    # Pause dès que total_dd <= -(max_total_dd - dd_safety_margin).
    # Ex : max=8%, margin=1% → pause à -7%.
    dd_safety_margin_pct: float = 1.0
    # v3.3 (2026-02-24) — Timezone du reset journalier.
    # FTMO: minuit CE(S)T. Topstep: 17h CT. GFT: vérifier.
    # Le DST est géré automatiquement via pytz/zoneinfo.
    reset_timezone: str = "Europe/Prague"  # FTMO = CET/CEST
    reset_hour: int = 0                    # 0 = minuit


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
    open_risk_cash: float = 0.0       # Somme des risk_cash des positions ouvertes
    daily_trades: int = 0
    open_instruments: list[str] = field(default_factory=list)

    @property
    def daily_dd_pct(self) -> float:
        """DD journalier en % — base = solde de début de journée.

        CORRECTION v2.3 (2026-02-20) : diviseur corrigé start_balance → daily_start_balance.
        Avec start_balance (~100k) au dénominateur, un DD de 3k en journée
        donnait daily_dd_pct = -3.0% sur un compte de 100k mais seulement
        -2.6% sur un compte de 115k après gains — le guard se déclenchait
        trop tard ou jamais. Avec daily_start_balance au dénominateur, le
        calcul est cohérent avec la règle FTMO (3% du solde de ce matin).
        """
        if self.daily_start_balance == 0:
            return 0.0
        return ((self.equity - self.daily_start_balance) / self.daily_start_balance) * 100

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

    def __init__(self, prop: PropConfig, exec_cfg: ExecConfig, live_mode: bool = False):
        self.prop = prop
        self.exec = exec_cfg
        self.live_mode = live_mode  # worst_case_budget ONLY in live (equity tracking broken in replay)

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

        # worst_case_budget: LIVE ONLY.
        # En replay, le DryRunAdapter ne track pas l'equity correctement,
        # ce qui fait que open_risk_cash s'accumule sans se décroître → faux rejets.
        # Diagnostic 2026-02-24: ce guard causait 1151 rejets fantômes (1998→999 trades).
        if self.live_mode:
            checks.append(self._worst_case_budget(signal, account))

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
        max_open_risk = account.start_balance * (self.prop.max_open_risk_pct / 100)
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
                "open_risk_cash": round(account.open_risk_cash, 2),
                "max_open_risk": round(max_open_risk, 2),
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
        """Pause dès (max_total_dd - dd_safety_margin) pour conserver
        une marge de 1% avant le seuil prop fatal.
        Ex : max=8%, margin=1% → pause à -7%.
        """
        pause_threshold = -(self.prop.max_total_dd_pct - self.prop.dd_safety_margin_pct)
        if a.total_dd_pct <= pause_threshold:
            return False, RejectReason.MAX_DD_LIMIT, (
                f"DD total {a.total_dd_pct:.1f}% <= pause {pause_threshold:.1f}%"
            )
        return True, None, ""

    def _max_positions(self, a: AccountState):
        """Guard d'exposition DD cumulée + filet absolu sur nb positions.

        Logique :
        1. Filet absolu : si open_positions >= max_positions (10), bloquer.
           (protection anti-bug, ne devrait jamais se déclencher normalement)
        2. Guard réel : si open_risk_cash >= max_open_risk_pct% du start_balance, bloquer.
           Exemple : max_open_risk_pct=2.0%, start_balance=100k → seuil=2000$
           Avec risk_per_trade=0.5% (500$) → ~4 positions pleines autorisées.
           Avec sizing réduit (DD avancé, ~70$/trade) → 28 positions autorisées
           (mais bloqué par le filet à 10).
        """
        # Filet absolu
        if a.open_positions >= self.prop.max_positions:
            return False, RejectReason.MAX_POSITIONS, (
                f"{a.open_positions}/{self.prop.max_positions} (filet absolu)"
            )
        # Guard exposition cumulée
        max_open_risk = a.start_balance * (self.prop.max_open_risk_pct / 100)
        if a.open_risk_cash >= max_open_risk:
            return False, RejectReason.OPEN_RISK_LIMIT, (
                f"open_risk {a.open_risk_cash:.0f}$ >= max {max_open_risk:.0f}$"
            )
        return True, None, ""

    def _daily_trades(self, a: AccountState):
        if a.daily_trades >= self.prop.max_daily_trades:
            return False, RejectReason.MAX_DAILY_TRADES, (
                f"trades {a.daily_trades}/{self.prop.max_daily_trades}"
            )
        return True, None, ""

    def _duplicate_instrument(self, signal: Signal, a: AccountState):
        if signal.instrument in a.open_instruments:
            return False, RejectReason.DUPLICATE_INSTRUMENT, f"déjà ouvert: {signal.instrument}"
        return True, None, ""

    def _bb_squeeze(self, signal: Signal):
        if signal.bb_width < 0.003:
            return False, RejectReason.BB_SQUEEZE, f"bb_width={signal.bb_width:.4f}"
        return True, None, ""

    def _worst_case_budget(self, signal: Signal, a: AccountState):
        """Worst-case pre-trade: si TOUS les SL ouverts sont touchés
        + ce nouveau trade aussi, est-ce qu'on dépasse le daily DD limit?

        C'est LE guard le plus important pour les prop firms.
        Sans lui, on peut accepter un trade qui, combiné aux positions
        ouvertes, causerait un breach même si chaque trade individuellement
        respecte les limites.

        Calcul: open_risk_cash = somme des risk_cash de toutes les positions.
        Si (open_risk_cash + new_risk_cash) > daily_dd_remaining → reject.
        """
        # Risque du nouveau trade
        risk_distance = abs(signal.close - signal.sl)
        if risk_distance == 0:
            return True, None, ""

        new_risk_cash = a.start_balance * (self.prop.risk_per_trade_pct / 100)

        # Budget daily restant (en cash)
        daily_dd_remaining_cash = max(
            0,
            (self.prop.max_daily_dd_pct + a.daily_dd_pct) / 100 * a.daily_start_balance
        )

        # Worst case: toutes les positions ouvertes touchent leur SL + ce trade aussi
        worst_case_total = a.open_risk_cash + new_risk_cash
        if worst_case_total > daily_dd_remaining_cash:
            return False, RejectReason.WORST_CASE_BUDGET, (
                f"worst_case {worst_case_total:.0f}$ > daily budget {daily_dd_remaining_cash:.0f}$"
            )
        return True, None, ""

    def _spread(self, spread: float, atr: float, max_ratio: float):
        ratio = spread / atr
        if ratio > max_ratio:
            return False, RejectReason.SPREAD_TOO_WIDE, f"spread {ratio:.2f}ATR > {max_ratio}"
        return True, None, ""

    def _slippage(self, signal: Signal, bid: float, ask: float, atr: float):
        """Slippage = |fill estimé - signal.close| / ATR.

        CORRECTION v2.4 (2026-02-20) : signal.tv_close → signal.close
        """
        fill_est = ask if signal.side == Side.LONG else bid
        slip = abs(fill_est - signal.close) / atr
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
        """Calcule le risk_cash avec réduction linéaire selon le DD.

        Logique :
        - Entre 0% et -dd_safety_margin% DD : risque plein.
        - Entre -dd_safety_margin% et -(max_total_dd - margin)% :
          réduction linéaire de 100% à MIN_RISK_RATIO (10%).
        - Au-delà : le guard _total_dd bloque avant d'arriver ici.

        Exemple avec max=8%, margin=1% (pause à -7%) :
          DD =  0% → ratio = 1.00 → risk = 500$
          DD = -3% → ratio = 0.71 → risk = 357$
          DD = -5% → ratio = 0.43 → risk = 214$
          DD = -6% → ratio = 0.29 → risk = 143$
          DD = -7% → guard bloque (jamais atteint ici)

        CORRECTION v2.4 (2026-02-20) : signal.tv_close → signal.close
        """
        MIN_RISK_RATIO = 0.10  # plancher à 10% du risque nominal

        entry = signal.close
        sl = signal.sl
        if entry == 0 or sl == 0:
            return {"risk_cash": 0, "risk_distance": 0, "error": "missing entry/sl"}

        risk_distance = abs(entry - sl)
        risk_nominal = account.start_balance * (self.prop.risk_per_trade_pct / 100)

        # Réduction linéaire selon le DD total
        # pause_zone = plage entre 0% et -(max - margin)%
        pause_threshold_pct = self.prop.max_total_dd_pct - self.prop.dd_safety_margin_pct
        total_dd_abs = abs(min(0.0, account.total_dd_pct))  # 0 si positif
        if total_dd_abs == 0:
            dd_ratio = 1.0
        else:
            dd_ratio = max(
                MIN_RISK_RATIO,
                1.0 - (total_dd_abs / pause_threshold_pct) * (1.0 - MIN_RISK_RATIO)
            )

        # Plafonner au DD daily restant (protection intraday)
        # CORRECTION v2.3 (2026-02-20) : diviseur daily_start_balance (cohérent avec daily_dd_pct)
        remaining_daily = max(
            0,
            (self.prop.max_daily_dd_pct + account.daily_dd_pct) / 100 * account.daily_start_balance
        )
        max_risk_daily = remaining_daily * 0.5

        risk_cash = min(risk_nominal * dd_ratio, max_risk_daily)
        risk_cash = max(0.0, risk_cash)

        return {
            "risk_cash": round(risk_cash, 2),
            "risk_distance": risk_distance,
        }


# ══════════════════════════════════════════════════════════════════════
# Circuit Breaker — freeze le compte en cas d'incident critique
# ══════════════════════════════════════════════════════════════════════

class CircuitBreakerState(Enum):
    RUNNING = "running"      # Tout va bien, trading normal
    CAUTION = "caution"      # Incident mineur, trading autorisé mais surveillé
    FROZEN = "frozen"        # Incident critique, plus aucun trade

    @classmethod
    def _missing_(cls, value):
        return cls.RUNNING


@dataclass
class Incident:
    """Un incident enregistré par le circuit breaker."""
    timestamp: datetime
    severity: str                  # "warning" | "critical"
    category: str                  # "sl_missing" | "sizing_mismatch" | "execution_divergence" | ...
    expected: str                  # Valeur attendue (texte)
    actual: str                    # Valeur constatée (texte)
    context: dict = field(default_factory=dict)
    account_id: str = ""


class CircuitBreaker:
    """Disjoncteur de sécurité pour le trading live.

    3 états :
      RUNNING  — normal, tout est ok
      CAUTION  — incident mineur détecté, log + alerte, trading ok
      FROZEN   — incident critique, plus aucun trade, déblocage manuel

    Déclencheurs FROZEN :
      - SL absent sur position ouverte
      - Divergence sizing > seuil (position réelle ≠ position attendue)
      - Exception broker non gérée
      - DD safety margin atteint (déjà dans guards, mais backup ici)

    Déclencheurs CAUTION :
      - Slippage élevé mais dans les tolérances
      - SL modifié par le broker (arrondi)
      - Fill partiel

    Usage :
        cb = CircuitBreaker()
        cb.report_incident("sl_missing", "critical", expected="SL at 1.0800", actual="no SL found")
        if not cb.can_trade():
            return  # bloqué
    """

    def __init__(self, max_warnings: int = 3, auto_freeze_on_critical: bool = True):
        self.state: CircuitBreakerState = CircuitBreakerState.RUNNING
        self.incidents: list[Incident] = []
        self.max_warnings = max_warnings
        self.auto_freeze_on_critical = auto_freeze_on_critical
        self._warning_count = 0

    def can_trade(self) -> bool:
        """Le compte est-il autorisé à trader?"""
        return self.state == CircuitBreakerState.RUNNING or self.state == CircuitBreakerState.CAUTION

    def is_frozen(self) -> bool:
        return self.state == CircuitBreakerState.FROZEN

    def report_incident(
        self,
        category: str,
        severity: str,
        expected: str = "",
        actual: str = "",
        context: dict | None = None,
        account_id: str = "",
    ) -> Incident:
        """Enregistre un incident et ajuste l'état.

        severity: "warning" → CAUTION (après max_warnings → FROZEN)
                  "critical" → FROZEN immédiat
        """
        incident = Incident(
            timestamp=datetime.now(timezone.utc),
            severity=severity,
            category=category,
            expected=expected,
            actual=actual,
            context=context or {},
            account_id=account_id,
        )
        self.incidents.append(incident)

        if severity == "critical":
            if self.auto_freeze_on_critical:
                self.state = CircuitBreakerState.FROZEN
                logger.critical(
                    f"🔴 CIRCUIT BREAKER FROZEN — {category}: "
                    f"expected={expected}, actual={actual}"
                )
        elif severity == "warning":
            self._warning_count += 1
            if self._warning_count >= self.max_warnings:
                self.state = CircuitBreakerState.FROZEN
                logger.critical(
                    f"🔴 CIRCUIT BREAKER FROZEN — {self._warning_count} warnings accumulated"
                )
            else:
                self.state = CircuitBreakerState.CAUTION
                logger.warning(
                    f"🟡 CIRCUIT BREAKER CAUTION — {category}: "
                    f"expected={expected}, actual={actual} "
                    f"({self._warning_count}/{self.max_warnings})"
                )

        return incident

    def manual_reset(self, reason: str = "") -> None:
        """Déblocage manuel — UNIQUEMENT par action humaine."""
        logger.info(f"🟢 CIRCUIT BREAKER RESET — reason: {reason}")
        self.state = CircuitBreakerState.RUNNING
        self._warning_count = 0

    def get_incidents_summary(self) -> dict:
        """Résumé pour le journal."""
        return {
            "state": self.state.value,
            "total_incidents": len(self.incidents),
            "warnings": sum(1 for i in self.incidents if i.severity == "warning"),
            "criticals": sum(1 for i in self.incidents if i.severity == "critical"),
            "last_incident": (
                {
                    "timestamp": self.incidents[-1].timestamp.isoformat(),
                    "category": self.incidents[-1].category,
                    "severity": self.incidents[-1].severity,
                }
                if self.incidents else None
            ),
        }


# ══════════════════════════════════════════════════════════════════════
# Timezone-aware day reset
# ══════════════════════════════════════════════════════════════════════

def is_new_day(now_utc: datetime, last_reset_utc: datetime, prop: PropConfig) -> bool:
    """Détermine si le jour de trading a changé (gère le DST).

    Utilise la timezone et l'heure de reset de la config prop firm.
    FTMO: minuit CE(S)T (Europe/Prague).
    Topstep: 17h CT (America/Chicago).

    Le DST est géré automatiquement: en hiver CET=UTC+1, en été CEST=UTC+2.
    Le reset se produit à minuit heure locale, quel que soit le décalage UTC.
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # Python < 3.9

    tz = ZoneInfo(prop.reset_timezone)
    now_local = now_utc.astimezone(tz)
    last_local = last_reset_utc.astimezone(tz)

    # Le "jour de trading" commence à reset_hour heure locale
    # Un jour a changé si la date locale (ajustée pour reset_hour) est différente
    def trading_date(dt_local):
        """Date du jour de trading: si l'heure est avant reset, c'est encore 'hier'."""
        if dt_local.hour < prop.reset_hour:
            return (dt_local - timedelta(hours=24)).date()
        return dt_local.date()

    return trading_date(now_local) > trading_date(last_local)
