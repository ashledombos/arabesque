#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arabesque — Order Dispatcher.

Surveille les ticks de prix (via PriceFeedManager) et place les ordres
sur tous les comptes configurés (cTrader + TradeLocker) quand le prix
atteint un niveau d'entrée.

Flux :
  Signal reçu (webhook / stratégie)
      ↓
  Guards.check_all() → reject si non conforme
      ↓
  PendingSignal enregistré (attend le prix)
      ↓
  Tick reçu sur le symbole
      ↓
  _check_trigger() — le prix a-t-il atteint l'entrée ?
      ↓
  _dispatch_to_all_brokers() — place sur chaque compte
      ↓
  Rapport + audit trail
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Callable

from arabesque.core.models import Signal, Side
from arabesque.core.guards import Guards, PropConfig, ExecConfig, AccountState
from arabesque.broker.base import (
    OrderRequest, OrderResult, OrderSide, OrderType, PriceTick, BaseBroker,
)

logger = logging.getLogger("arabesque.live.order_dispatcher")

# JSONL pour shadow filters — persiste chaque trade accepté avec ses indicateurs
SHADOW_LOG_PATH = Path("logs/shadow_filters.jsonl")

# JSONL pour le weekend crypto guard — trace chaque blocage et chaque gap weekend
WEEKEND_GUARD_LOG_PATH = Path("logs/weekend_crypto_guard.jsonl")

# JSONL pour les rejets propres a un broker (quote/preflight/quarantaine).
# Necessaire pour distinguer une execution volontairement bloquee d'un signal
# silencieusement perdu lors des replays live/theorie.
BROKER_REJECT_LOG_PATH = Path("logs/broker_guard_rejects.jsonl")
GFT_QUOTE_COHERENCE_LOG_PATH = Path("logs/gft_quote_coherence.jsonl")


# =============================================================================
# PendingSignal
# =============================================================================

@dataclass
class PendingSignal:
    """
    Signal en attente de déclenchement prix.

    Un signal est "déclenché" quand le prix bid (SELL) ou ask (BUY)
    touche ou dépasse le niveau d'entrée configuré.

    Pour une entrée LIMIT :
      BUY  : on attend ask <= entry_price (prix redescend vers nous)
      SELL : on attend bid >= entry_price (prix remonte vers nous)

    Pour une entrée STOP (breakout) :
      BUY  : on attend ask >= entry_price (cassure hausse)
      SELL : on attend bid <= entry_price (cassure baisse)
    """
    signal: Signal
    entry_price: float          # Prix cible d'entrée
    order_type: OrderType       # LIMIT ou STOP
    volume_lots: float          # Taille calculée (lots)
    risk_cash: float            # Risque en devise (pour AccountState)
    expiry: datetime            # Heure d'expiration
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    triggered: bool = False
    trigger_price: Optional[float] = None
    trigger_time: Optional[datetime] = None

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expiry

    @property
    def symbol(self) -> str:
        return self.signal.instrument

    @property
    def side(self) -> Side:
        return self.signal.side

    def is_triggered_by(self, tick: PriceTick) -> bool:
        """Retourne True si le tick déclenche cet ordre."""
        if self.triggered or self.is_expired:
            return False

        if self.order_type == OrderType.LIMIT:
            if self.side == Side.LONG:
                # BUY LIMIT : on entre quand l'ask redescend à notre prix
                return tick.ask <= self.entry_price
            else:
                # SELL LIMIT : on entre quand le bid remonte à notre prix
                return tick.bid >= self.entry_price

        elif self.order_type == OrderType.STOP:
            if self.side == Side.LONG:
                # BUY STOP : cassure hausse
                return tick.ask >= self.entry_price
            else:
                # SELL STOP : cassure baisse
                return tick.bid <= self.entry_price

        return False


# =============================================================================
# OrderDispatcher
# =============================================================================

class OrderDispatcher:
    """
    Surveille les ticks et dispatche les ordres sur tous les comptes.

    Paramètres
    ----------
    brokers         : dict broker_id -> BaseBroker (déjà connectés)
    instruments_cfg : dict du fichier instruments.yaml (pour le mapping)
    prop_config     : configuration des limites prop firm
    exec_config     : configuration d'exécution
    delay_ms        : (min_ms, max_ms) délai aléatoire entre chaque broker
    dry_run         : si True, simule sans envoyer d'ordres réels
    on_order_result : callback optionnel appelé après chaque placement
                      signature: (broker_id, signal, result: OrderResult)
    """

    def __init__(
        self,
        brokers: Dict[str, BaseBroker],
        instruments_cfg: dict,
        prop_config: Optional[PropConfig] = None,
        exec_config: Optional[ExecConfig] = None,
        delay_ms: tuple = (500, 3000),
        dry_run: bool = False,
        on_order_result: Optional[Callable] = None,
        on_slippage_reject: Optional[Callable] = None,
        risk_multiplier_fn: Optional[Callable] = None,
        risk_multiplier_by_tf: Optional[Dict[str, float]] = None,
        rodage_config: Optional[Dict] = None,
        max_slippage_atr: float = 0.5,
        max_executed_risk_ratio: float = 1.25,
        settings: Optional[dict] = None,
        prop_configs_by_broker: Optional[Dict[str, PropConfig]] = None,
    ):
        self.brokers = brokers
        self.instruments_cfg = instruments_cfg
        resolved_exec_cfg = exec_config or ExecConfig()
        self.guards = Guards(
            prop=prop_config or PropConfig(),
            exec_cfg=resolved_exec_cfg,
            live_mode=True,
        )
        self._guards_by_broker = {
            broker_id: Guards(
                prop=broker_prop,
                exec_cfg=resolved_exec_cfg,
                live_mode=True,
            )
            for broker_id, broker_prop in (prop_configs_by_broker or {}).items()
        }
        self.delay_ms = delay_ms
        self.dry_run = dry_run
        self.on_order_result = on_order_result
        self.on_slippage_reject = on_slippage_reject
        self._risk_multiplier_fn = risk_multiplier_fn
        self._risk_multiplier_by_tf = risk_multiplier_by_tf or {}
        self._max_slippage_atr = max_slippage_atr
        # Broker minimum/step rounding must not turn a reduced budget into
        # materially larger exposure.
        self._max_executed_risk_ratio = max_executed_risk_ratio
        # Fail-closed quarantine for a broker whose last accepted position
        # cannot be proven safe (e.g. missing server-side SL/TP after fill).
        # Existing positions continue to be monitored; only new entries stop.
        self._execution_blocked_brokers: Dict[str, str] = {}

        gft_preflight = ((settings or {}).get("execution", {}) or {}).get(
            "gft_preflight", {}
        )
        self._gft_preflight_enabled = gft_preflight.get("enabled", True)
        self._gft_require_quote = gft_preflight.get("require_quote", True)
        self._gft_max_adverse_slippage_r = float(
            gft_preflight.get("max_adverse_slippage_r", 0.25)
        )

        # Weekend crypto guard config
        wcg = (settings or {}).get("weekend_crypto_guard", {})
        self._wcg_enabled = wcg.get("enabled", False)
        self._wcg_cutoff_hour = wcg.get("cutoff_utc_hour", 15)
        self._wcg_broker_types = set(wcg.get("broker_types", ["ctrader"]))
        if self._wcg_enabled:
            logger.info(
                f"[Dispatcher] Weekend crypto guard actif: "
                f"vendredi >= {self._wcg_cutoff_hour}h UTC, "
                f"brokers: {self._wcg_broker_types}"
            )

        # Weekend gap guard config.
        #
        # Distinct du crypto guard : ici on vise les instruments fermés le
        # week-end (FX/metals) qui peuvent rouvrir au-delà du SL broker-side.
        # Le guard ne ferme jamais une position ; il bloque uniquement les
        # nouvelles entrées tard le vendredi sur une liste explicite.
        wgg = (settings or {}).get("weekend_gap_guard", {})
        self._wgg_enabled = wgg.get("enabled", False)
        self._wgg_cutoff_hour = wgg.get("cutoff_utc_hour", 15)
        self._wgg_symbols = set(wgg.get("symbols", []))
        self._wgg_broker_types = set(wgg.get("broker_types", []))
        if self._wgg_enabled:
            logger.info(
                f"[Dispatcher] Weekend gap guard actif: "
                f"vendredi >= {self._wgg_cutoff_hour}h UTC, "
                f"symbols: {sorted(self._wgg_symbols)}, "
                f"brokers: {self._wgg_broker_types or 'all'}"
            )

        # Strategy × broker exclusions (e.g. cabriole -> [gft_compte1])
        sbe_cfg = (settings or {}).get("strategy_broker_exclusions", {}) or {}
        self._strategy_broker_exclusions: Dict[str, set] = {
            strat: set(brokers_list or [])
            for strat, brokers_list in sbe_cfg.items()
        }
        if self._strategy_broker_exclusions:
            logger.info(
                f"[Dispatcher] Exclusions stratégie×broker: "
                f"{self._strategy_broker_exclusions}"
            )

        # Rodage: risk multiplier for strategies in break-in period
        # Ultra-rodage prioritaire sur rodage normal (×0.10 vs ×0.25 par défaut)
        self._rodage_strategies: set = set()
        self._rodage_multiplier: float = 1.0
        self._rodage_strategies_ultra: set = set()
        self._rodage_multiplier_ultra: float = 1.0
        if rodage_config and rodage_config.get("enabled", False):
            self._rodage_strategies = set(rodage_config.get("strategies", []))
            self._rodage_multiplier = rodage_config.get("risk_multiplier", 0.5)
            self._rodage_strategies_ultra = set(rodage_config.get("strategies_ultra", []))
            self._rodage_multiplier_ultra = rodage_config.get("risk_multiplier_ultra", 0.10)
            if self._rodage_strategies:
                logger.info(
                    f"[Dispatcher] 🔬 Rodage actif: {self._rodage_strategies} "
                    f"× {self._rodage_multiplier}"
                )
            if self._rodage_strategies_ultra:
                logger.info(
                    f"[Dispatcher] 🧪 Ultra-rodage actif: {self._rodage_strategies_ultra} "
                    f"× {self._rodage_multiplier_ultra}"
                )

        # Signaux en attente, indexés par symbole
        self._pending: Dict[str, List[PendingSignal]] = {}

        # File d'attente pour dispatch séquentiel (FIFO)
        # Empêche les placements concurrents qui corrompent _pending_requests
        self._dispatch_queue: asyncio.Queue = asyncio.Queue()
        self._dispatch_worker_task: Optional[asyncio.Task] = None

        # État du compte consolidé (mis à jour après chaque placement)
        self._account_state = AccountState()
        self._account_states_by_broker: Dict[str, AccountState] = {}
        self._primary_broker_id = next(iter(brokers), "")

        # Statistiques
        self._stats = {
            "signals_received": 0,
            "signals_rejected": 0,
            "signals_triggered": 0,
            "signals_expired": 0,
            "signals_slippage_rejected": 0,
            "orders_placed": 0,
            "orders_failed": 0,
        }

    # ------------------------------------------------------------------
    # Quarantaine d'integrite broker
    # ------------------------------------------------------------------

    def block_broker_entries(self, broker_id: str, reason: str) -> None:
        """Block new entries on a broker after an execution-integrity failure."""
        self._execution_blocked_brokers[broker_id] = reason
        logger.error(
            f"[Dispatcher] 🔒 {broker_id}: nouvelles entrées bloquées — {reason}"
        )

    def unblock_broker_entries(self, broker_id: str) -> None:
        """Administrative reset after an operator has validated broker safety."""
        self._execution_blocked_brokers.pop(broker_id, None)

    def _log_broker_reject(
        self, broker_id: str, signal: Signal, reason: str, metrics: dict | None = None
    ) -> None:
        """Persist a broker-local reject for later live/theory attribution."""
        try:
            BROKER_REJECT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            row = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "broker_guard_reject",
                "broker_id": broker_id,
                "instrument": signal.instrument,
                "strategy": getattr(signal, "strategy_type", ""),
                "side": signal.side.value,
                "signal_id": signal.signal_id,
                "reason": reason,
            }
            if metrics:
                row.update(metrics)
            with BROKER_REJECT_LOG_PATH.open("a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as e:
            logger.warning(f"[Dispatcher] broker reject log failed: {e}")

    def _log_gft_quote_coherence(
        self,
        broker_id: str,
        signal: Signal,
        broker_tick: PriceTick,
        reference_tick: PriceTick,
        *,
        decision: str,
        reason: str = "",
    ) -> None:
        """Persist GFT REST quote vs cTrader trigger quote before entry.

        This is measurement first, not auto-correction. It lets us prove an
        offset is stable before any broker-specific model is considered.
        """
        try:
            direction = 1.0 if signal.side == Side.LONG else -1.0
            gft_price = (
                float(broker_tick.ask) if signal.side == Side.LONG
                else float(broker_tick.bid)
            )
            ref_price = (
                float(reference_tick.ask) if signal.side == Side.LONG
                else float(reference_tick.bid)
            )
            risk_distance = abs(float(signal.close) - float(signal.sl))
            offset_price = gft_price - ref_price
            adverse_price = max(0.0, offset_price * direction)
            offset_r = offset_price / risk_distance if risk_distance > 0 else 0.0
            adverse_r = adverse_price / risk_distance if risk_distance > 0 else 0.0
            offset_atr = offset_price / signal.atr if signal.atr > 0 else 0.0
            spread_r = (
                broker_tick.spread / risk_distance if risk_distance > 0 else 0.0
            )
            row = {
                "event": "gft_quote_coherence_check",
                "ts": datetime.now(timezone.utc).isoformat(),
                "broker_id": broker_id,
                "instrument": signal.instrument,
                "strategy": getattr(signal, "strategy_type", ""),
                "side": signal.side.value,
                "signal_id": signal.signal_id,
                "signal_price": signal.close,
                "reference_bid": reference_tick.bid,
                "reference_ask": reference_tick.ask,
                "reference_trade_price": ref_price,
                "gft_bid": broker_tick.bid,
                "gft_ask": broker_tick.ask,
                "gft_trade_price": gft_price,
                "gft_spread": broker_tick.spread,
                "offset_price": round(offset_price, 8),
                "offset_r": round(offset_r, 6),
                "offset_atr": round(offset_atr, 6),
                "adverse_r_vs_reference": round(adverse_r, 6),
                "spread_r": round(spread_r, 6),
                "decision": decision,
                "reason": reason,
            }
            GFT_QUOTE_COHERENCE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with GFT_QUOTE_COHERENCE_LOG_PATH.open("a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as e:
            logger.warning(f"[Dispatcher] GFT quote coherence log failed: {e}")

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def update_account_state(self, state: AccountState, broker_id: str = "") -> None:
        """Update current account state, retaining a separate state per broker."""
        if broker_id:
            self._account_states_by_broker[broker_id] = state
            if broker_id == self._primary_broker_id:
                self._account_state = state
        else:
            self._account_state = state

    def invalidate_account_state(self, broker_id: str) -> None:
        """Remove stale broker risk state after an unavailable broker read.

        A disconnected broker is not evidence of zero positions. Removing the
        last state makes the final pre-order gate fail closed until a fresh
        account/position refresh succeeds.
        """
        self._account_states_by_broker.pop(broker_id, None)
        if broker_id == self._primary_broker_id:
            self._account_state = AccountState()

    async def receive_signal(self, signal: Signal) -> bool:
        """
        Reçoit un signal et l'enregistre si les guards passent.

        Retourne True si le signal est accepté (mis en attente de prix).
        """
        self._stats["signals_received"] += 1

        if (
            self._primary_broker_id
            and self._primary_broker_id not in self._account_states_by_broker
        ):
            self._stats["signals_rejected"] += 1
            logger.warning(
                "[Dispatcher] Etat risque primaire indisponible - "
                f"signal {signal.instrument} bloque fail-closed"
            )
            return False

        # Prix courant via le dernier tick connu (fourni par le feed)
        tick = self._get_last_tick(signal.instrument)
        if tick is None:
            logger.warning(
                f"[Dispatcher] ⚠️  Aucun tick connu pour {signal.instrument} — "
                f"signal mis en attente sans vérification spread"
            )
            bid, ask = signal.close, signal.close
        else:
            bid, ask = tick.bid, tick.ask

        # Guards
        ok, decision = self.guards.check_all(
            signal=signal,
            account=self._account_state,
            broker_bid=bid,
            broker_ask=ask,
        )

        if not ok:
            self._stats["signals_rejected"] += 1
            logger.info(
                f"[Dispatcher] ❌ Signal rejeté {signal.instrument} {signal.side.value}: "
                f"{decision.reason}"
            )
            return False

        # Marquer immédiatement l'instrument comme "en cours" pour bloquer
        # les doublons avant que l'ordre ne soit effectivement placé
        if signal.instrument not in self._account_state.open_instruments:
            self._account_state.open_instruments.append(signal.instrument)
            self._account_state.open_positions += 1

        # Sizing
        sizing = self.guards.compute_sizing(signal, self._account_state)
        if sizing.get("risk_cash", 0) <= 0:
            logger.warning(f"[Dispatcher] risk_cash=0 pour {signal.instrument}, signal ignoré")
            self._stats["signals_rejected"] += 1
            return False

        sizing["risk_cash"] = self._apply_risk_modifiers(
            signal, sizing["risk_cash"], log=True
        )

        # Calculer le volume en lots
        risk_distance = sizing["risk_distance"]
        risk_cash = sizing["risk_cash"]
        volume_lots = self._compute_lots(signal, risk_cash, risk_distance)

        # Note: le volume préliminaire est recalculé per-broker dans _dispatch_to_broker
        # avec le pip_size réel du broker (peut différer du YAML).
        # La validation du sizing se fait au moment du dispatch, pas ici.

        # Déterminer le type d'ordre (LIMIT vs STOP)
        # Arabesque utilise les entrées LIMIT par défaut (mean-reversion)
        # Si l'entrée est au-delà du close dans le sens du trade → STOP
        order_type = self._determine_order_type(signal)

        # Expiration : N bougies du timeframe
        tf_minutes = int(signal.timeframe) if signal.timeframe.isdigit() else 60
        expiry_candles = 4  # défaut
        expiry = datetime.now(timezone.utc) + timedelta(
            minutes=tf_minutes * expiry_candles
        )

        pending = PendingSignal(
            signal=signal,
            entry_price=signal.sl if signal.sl != signal.close else signal.close,
            order_type=order_type,
            volume_lots=volume_lots,
            risk_cash=risk_cash,
            expiry=expiry,
        )
        # L'entrée cible est le close du signal (on entre à ce niveau ou mieux)
        pending.entry_price = signal.close

        sym = signal.instrument
        if sym not in self._pending:
            self._pending[sym] = []
        self._pending[sym].append(pending)

        logger.info(
            f"[Dispatcher] ✅ Signal accepté: {sym} {signal.side.value} "
            f"entry={pending.entry_price} SL={signal.sl} "
            f"risk={risk_cash:.1f}€ vol={volume_lots:.3f}L "
            f"expire={expiry.strftime('%H:%M:%S')} UTC"
        )

        # Shadow filters — log seulement, n'empêche pas le trade
        # Permet d'évaluer a posteriori si le filtre améliorerait les résultats
        wr = getattr(signal, 'wr_14', 0)
        rsi_div = getattr(signal, 'rsi_div', 0)
        rsi = getattr(signal, 'rsi', 50)
        cmf = getattr(signal, 'cmf', 0)
        bb_width = getattr(signal, 'bb_width', 0)

        shadow_flags: dict[str, bool] = {}

        # Williams %R shadow
        wr_would_filter = False
        if wr != 0:
            if signal.side == Side.LONG and wr < -30:
                wr_would_filter = True
                logger.info(
                    f"[Dispatcher] 👻 WR shadow: {sym} LONG wr_14={wr:.1f} < -30 "
                    f"→ AURAIT été filtré (momentum faible)"
                )
            elif signal.side == Side.SHORT and wr > -70:
                wr_would_filter = True
                logger.info(
                    f"[Dispatcher] 👻 WR shadow: {sym} SHORT wr_14={wr:.1f} > -70 "
                    f"→ AURAIT été filtré (momentum faible)"
                )
        shadow_flags["wr_momentum"] = wr_would_filter

        # RSI divergence shadow
        div_would_filter = False
        if rsi_div != 0:
            if signal.side == Side.LONG and rsi_div == -1:
                div_would_filter = True
                logger.info(
                    f"[Dispatcher] 👻 DIV shadow: {sym} LONG rsi_div=BEARISH "
                    f"(prix ↑ RSI ↓ sur 5 barres) → AURAIT été filtré"
                )
            elif signal.side == Side.SHORT and rsi_div == 1:
                div_would_filter = True
                logger.info(
                    f"[Dispatcher] 👻 DIV shadow: {sym} SHORT rsi_div=BULLISH "
                    f"(prix ↓ RSI ↑ sur 5 barres) → AURAIT été filtré"
                )
        shadow_flags["rsi_divergence"] = div_would_filter

        # RSI extreme contre-sens shadow
        rsi_extreme = False
        if (signal.side == Side.LONG and rsi > 75) or (signal.side == Side.SHORT and rsi < 25):
            rsi_extreme = True
        shadow_flags["rsi_extreme"] = rsi_extreme

        # CMF contre-sens shadow
        cmf_against = False
        if (signal.side == Side.LONG and cmf < -0.10) or (signal.side == Side.SHORT and cmf > 0.10):
            cmf_against = True
        shadow_flags["cmf_contre_sens"] = cmf_against

        # Persist to JSONL — every accepted trade, with all shadow filter states
        self._log_shadow_entry(signal, shadow_flags, {
            "wr_14": round(wr, 2), "rsi": round(rsi, 1),
            "rsi_div": rsi_div, "cmf": round(cmf, 3),
            "bb_width": round(bb_width, 5), "rr": round(signal.rr, 2),
        })

        return True

    def _apply_risk_modifiers(
        self, signal: Signal, risk_cash: float, log: bool = True
    ) -> float:
        """Apply strategy and live-risk modifiers to a broker risk budget."""
        # Per-timeframe risk multiplier (H4 → higher risk, validated by backtest)
        tf_key = getattr(signal, "timeframe", "1h").lower()
        tf_mult = self._risk_multiplier_by_tf.get(tf_key, 1.0)
        if tf_mult != 1.0:
            original = risk_cash
            risk_cash = round(original * tf_mult, 2)
            if log:
                logger.info(
                f"[Dispatcher] 📊 TF risk adjust: {original:.0f}$ × "
                f"{tf_mult:.2f} ({tf_key}) = {risk_cash:.0f}$"
                )

        # Rodage: risk réduit pour les stratégies en période de rodage
        # Ultra-rodage prioritaire (×0.10 par défaut), sinon rodage normal (×0.25)
        strat_name = getattr(signal, "strategy_type", "")
        if strat_name in self._rodage_strategies_ultra:
            original = risk_cash
            risk_cash = round(original * self._rodage_multiplier_ultra, 2)
            if log:
                logger.info(
                f"[Dispatcher] 🧪 Ultra-rodage: {strat_name} {original:.0f}$ × "
                f"{self._rodage_multiplier_ultra} = {risk_cash:.0f}$"
                )
        elif strat_name in self._rodage_strategies:
            original = risk_cash
            risk_cash = round(original * self._rodage_multiplier, 2)
            if log:
                logger.info(
                f"[Dispatcher] 🔬 Rodage: {strat_name} {original:.0f}$ × "
                f"{self._rodage_multiplier} = {risk_cash:.0f}$"
                )

        # Correlation discount: reduce risk for same-category positions
        corr_mult = self._correlation_discount(signal.instrument)
        if corr_mult < 1.0:
            original = risk_cash
            risk_cash = round(original * corr_mult, 2)
            if log:
                logger.info(
                f"[Dispatcher] 🔗 Corrélation: {original:.0f}$ × "
                f"{corr_mult:.2f} = {risk_cash:.0f}$ "
                f"(même catégorie déjà ouverte)"
                )

        # Apply live monitor risk multiplier (protection tiers)
        if self._risk_multiplier_fn:
            multiplier = self._risk_multiplier_fn()
            if multiplier < 1.0:
                original = risk_cash
                risk_cash = round(original * multiplier, 2)
                if log:
                    logger.info(
                    f"[Dispatcher] 🛡️ Risk réduit: {original:.0f}$ × "
                    f"{multiplier:.0%} = {risk_cash:.0f}$ "
                    f"(protection active)"
                    )
        return risk_cash

    def _log_shadow_entry(self, signal: Signal, flags: dict, indicators: dict) -> None:
        """Persiste un enregistrement shadow filter en JSONL."""
        try:
            SHADOW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "instrument": signal.instrument,
                "side": signal.side.value,
                "strategy": getattr(signal, "strategy_type", ""),
                "entry_price": signal.close,
                "sl": signal.sl,
                "timeframe": getattr(signal, "timeframe", "1h"),
                "indicators": indicators,
                "shadow_filters": flags,
                "any_would_filter": any(flags.values()),
            }
            with open(SHADOW_LOG_PATH, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.debug(f"[Dispatcher] shadow log error: {e}")

    async def on_tick(self, tick: PriceTick) -> None:
        """
        Callback appelé par le PriceFeedManager à chaque tick.
        Vérifie si des signaux pendants sont déclenchés.
        """
        sym = tick.symbol
        pending_list = self._pending.get(sym, [])
        if not pending_list:
            return

        triggered = []
        expired = []
        still_pending = []

        for ps in pending_list:
            if ps.is_expired:
                expired.append(ps)
            elif ps.is_triggered_by(tick):
                triggered.append(ps)
                ps.triggered = True
                ps.trigger_price = tick.ask if ps.side == Side.LONG else tick.bid
                ps.trigger_time = tick.timestamp
            else:
                still_pending.append(ps)

        # Log les expirés
        for ps in expired:
            self._stats["signals_expired"] += 1
            logger.info(
                f"[Dispatcher] ⏰ Signal expiré: {sym} {ps.side.value} "
                f"entry={ps.entry_price} (jamais déclenché)"
            )

        # Mettre à jour la liste
        self._pending[sym] = still_pending

        # Dispatcher les déclenchés — FIFO séquentiel (pas de create_task !)
        for ps in triggered:
            # ── Slippage guard (H4) ──
            # Rejeter si le prix de trigger a trop dévié du signal
            signal = ps.signal
            if signal.atr > 0 and self._max_slippage_atr > 0:
                slippage = abs(ps.trigger_price - signal.close)
                slippage_atr = slippage / signal.atr
                if slippage_atr > self._max_slippage_atr:
                    self._stats["signals_slippage_rejected"] += 1
                    logger.info(
                        f"[Dispatcher] 🛑 Slippage rejeté: {signal.instrument} "
                        f"{signal.side.value} — trigger={ps.trigger_price:.5f} vs "
                        f"signal={signal.close:.5f} — slip={slippage_atr:.2f} ATR "
                        f"(max={self._max_slippage_atr:.2f})"
                    )
                    # Créer un counterfactual pour mesurer l'impact
                    if self.on_slippage_reject:
                        try:
                            self.on_slippage_reject(signal, ps.trigger_price, slippage_atr)
                        except Exception as e:
                            logger.debug(f"[Dispatcher] slippage reject callback error: {e}")
                    # Libérer le slot instrument dans account_state
                    if signal.instrument in self._account_state.open_instruments:
                        self._account_state.open_instruments.remove(signal.instrument)
                        self._account_state.open_positions = max(
                            0, self._account_state.open_positions - 1
                        )
                    continue

            self._stats["signals_triggered"] += 1
            await self._dispatch_queue.put((ps, tick))
            # Démarrer le worker si pas encore actif
            if self._dispatch_worker_task is None or self._dispatch_worker_task.done():
                self._dispatch_worker_task = asyncio.create_task(
                    self._dispatch_worker()
                )

    async def _dispatch_worker(self) -> None:
        """Worker FIFO : traite les signaux déclenchés un par un.

        Garantit qu'un seul ordre est en vol à la fois, évitant la corruption
        de _pending_requests dans le broker (clé fixe "order_place").
        """
        while not self._dispatch_queue.empty():
            try:
                ps, tick = await asyncio.wait_for(
                    self._dispatch_queue.get(), timeout=1.0
                )
                await self._dispatch_to_all_brokers(ps, tick)
                self._dispatch_queue.task_done()
            except asyncio.TimeoutError:
                break  # Queue vide, worker s'arrête
            except Exception as e:
                logger.error(f"[Dispatcher] Worker error: {e}")

    async def get_stats(self) -> dict:
        """Statistiques du dispatcher."""
        pending_count = sum(len(v) for v in self._pending.values())
        return {
            **self._stats,
            "pending_signals": pending_count,
            "pending_by_symbol": {
                k: len(v) for k, v in self._pending.items() if v
            },
        }

    # ------------------------------------------------------------------
    # Dispatch multi-brokers
    # ------------------------------------------------------------------

    async def _dispatch_to_all_brokers(self, ps: PendingSignal, tick: PriceTick) -> None:
        """
        Place l'ordre sur tous les brokers activés, avec délai aléatoire
        entre chaque broker pour éviter la détection copy-trading.
        """
        signal = ps.signal
        logger.info(
            f"[Dispatcher] 🟢 Déclenchement {signal.instrument} {signal.side.value} "
            f"@ {ps.trigger_price:.5f} — dispatch sur {len(self.brokers)} compte(s)"
        )

        broker_order = list(self.brokers.items())
        # Mélanger l'ordre pour varier quel broker est le "premier"
        random.shuffle(broker_order)

        results = []
        for i, (broker_id, broker) in enumerate(broker_order):
            # Délai aléatoire entre brokers (sauf le premier)
            if i > 0:
                delay_s = random.uniform(
                    self.delay_ms[0] / 1000,
                    self.delay_ms[1] / 1000
                )
                await asyncio.sleep(delay_s)

            result = await self._place_on_broker(broker_id, broker, ps, tick)
            results.append((broker_id, result))

            if self.on_order_result:
                try:
                    if asyncio.iscoroutinefunction(self.on_order_result):
                        await self.on_order_result(broker_id, signal, result)
                    else:
                        self.on_order_result(broker_id, signal, result)
                except Exception as e:
                    logger.warning(f"[Dispatcher] Callback on_order_result error: {e}")

        # Résumé
        successes = sum(1 for _, r in results if r.success)
        failures = sum(1 for _, r in results if not r.success)
        self._stats["orders_placed"] += successes
        self._stats["orders_failed"] += failures

        logger.info(
            f"[Dispatcher] 📊 {signal.instrument}: "
            f"{successes}/{ len(results)} ordre(s) placé(s) avec succès"
        )

    def _is_weekend_crypto_blocked(
        self, broker_id: str, broker: BaseBroker, signal: Signal
    ) -> bool:
        """Vérifie si le signal est bloqué par le weekend crypto guard.

        cTrader ferme les CFD crypto le vendredi soir, créant des gaps de prix
        à la réouverture. Ce guard empêche d'ouvrir de nouvelles positions
        crypto sur les brokers cTrader après le cutoff du vendredi.
        """
        if not self._wcg_enabled:
            return False

        # Vérifier le type de broker
        broker_type = broker.config.get("type", "")
        if broker_type not in self._wcg_broker_types:
            return False

        # Vérifier si l'instrument est crypto (session_model: 24x7)
        inst_cfg = self.instruments_cfg.get(signal.instrument, {})
        if inst_cfg.get("session_model") != "24x7":
            return False

        # Bloquer vendredi après cutoff, samedi (5), dimanche (6).
        # Avant : ne bloquait que vendredi → samedi/dimanche bypassed.
        now = datetime.now(timezone.utc)
        wd = now.weekday()
        if wd in (5, 6):
            return True
        if wd == 4 and now.hour >= self._wcg_cutoff_hour:
            return True
        return False

    def _is_weekend_gap_blocked(
        self, broker_id: str, broker: BaseBroker, signal: Signal
    ) -> bool:
        """Bloque les nouvelles entrées exposées au gap de réouverture.

        AUDJPY 2026-05-29 -> 2026-05-31 a montré qu'un instrument non-crypto
        fermé le week-end peut rouvrir au-delà du SL (-1.565R réel). Cette
        garde est volontairement explicite par symbole pour ne pas transformer
        un incident isolé en filtre global non validé.
        """
        if not self._wgg_enabled:
            return False

        if self._wgg_symbols and signal.instrument not in self._wgg_symbols:
            return False

        broker_type = broker.config.get("type", "")
        if self._wgg_broker_types and broker_type not in self._wgg_broker_types:
            return False

        # Les crypto ont déjà un guard dédié, plus précis pour cTrader.
        inst_cfg = self.instruments_cfg.get(signal.instrument, {})
        if inst_cfg.get("session_model") == "24x7":
            return False

        now = datetime.now(timezone.utc)
        wd = now.weekday()
        if wd in (5, 6):
            return True
        if wd == 4 and now.hour >= self._wgg_cutoff_hour:
            return True
        return False

    def _log_weekend_guard(self, event: str, signal: Signal, broker_id: str,
                           extra: dict | None = None) -> None:
        """Persiste un événement du weekend crypto guard en JSONL."""
        try:
            WEEKEND_GUARD_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "instrument": signal.instrument,
                "side": signal.side.value,
                "strategy": getattr(signal, "strategy_type", ""),
                "entry_price": signal.close,
                "sl": signal.sl,
                "broker_id": broker_id,
            }
            if extra:
                entry.update(extra)
            with open(WEEKEND_GUARD_LOG_PATH, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.debug(f"[Dispatcher] weekend guard log error: {e}")

    async def _place_on_broker(
        self,
        broker_id: str,
        broker: BaseBroker,
        ps: PendingSignal,
        tick: PriceTick,
    ) -> OrderResult:
        """Place l'ordre sur un broker spécifique."""
        signal = ps.signal
        sym = signal.instrument

        blocked_reason = self._execution_blocked_brokers.get(broker_id)
        if blocked_reason:
            logger.error(
                f"[Dispatcher] ⛔ {broker_id}: ordre {sym} bloqué — "
                f"quarantaine exécution: {blocked_reason}"
            )
            self._log_broker_reject(
                broker_id, signal, "execution_quarantine",
                {"detail": blocked_reason},
            )
            return OrderResult(
                success=False,
                message=f"Quarantaine exécution {broker_id}: {blocked_reason}",
            )

        # Strategy × broker exclusion — block the pair (config-driven).
        excluded = self._strategy_broker_exclusions.get(
            signal.strategy_type, set()
        )
        if broker_id in excluded:
            logger.info(
                f"[Dispatcher] 🚫 {signal.strategy_type} bloqué sur {broker_id} "
                f"(strategy_broker_exclusions) — {sym} {signal.side.value}"
            )
            return OrderResult(
                success=False,
                message=f"{signal.strategy_type} exclu de {broker_id}",
            )

        # Weekend crypto guard — bloque les nouvelles positions crypto
        # sur cTrader le vendredi après le cutoff (gap risk à la réouverture)
        if self._is_weekend_crypto_blocked(broker_id, broker, signal):
            logger.info(
                f"[Dispatcher] 🛡️ Weekend crypto guard: {sym} {signal.side.value} "
                f"bloqué sur {broker_id} (vendredi >= {self._wcg_cutoff_hour}h UTC)"
            )
            self._log_weekend_guard("blocked", signal, broker_id)
            return OrderResult(
                success=False,
                message=f"Weekend crypto guard: {sym} bloqué sur {broker_id}"
            )

        # Weekend gap guard — bloque les nouvelles positions sur instruments
        # fermés le week-end et listés explicitement (ex: AUDJPY après incident
        # -1.565R à la réouverture du 2026-05-31).
        if self._is_weekend_gap_blocked(broker_id, broker, signal):
            logger.info(
                f"[Dispatcher] 🛡️ Weekend gap guard: {sym} {signal.side.value} "
                f"bloqué sur {broker_id} (vendredi >= {self._wgg_cutoff_hour}h UTC)"
            )
            self._log_weekend_guard(
                "blocked_gap",
                signal,
                broker_id,
                {"guard": "weekend_gap_guard"},
            )
            return OrderResult(
                success=False,
                message=f"Weekend gap guard: {sym} bloqué sur {broker_id}"
            )

        # Vérifier si l'instrument est disponible sur ce broker
        broker_sym = broker.map_symbol(sym)
        if not broker_sym:
            logger.debug(
                f"[Dispatcher] {broker_id}: {sym} non mappé, ignoré"
            )
            return OrderResult(
                success=False,
                message=f"{sym} non disponible sur {broker_id}"
            )

        broker_guards = self._guards_by_broker.get(broker_id)
        broker_state = self._account_states_by_broker.get(broker_id)
        if broker_guards is None or broker_state is None:
            logger.error(
                f"[Dispatcher] ⛔ {broker_id}: état/guards broker absent — "
                f"ordre {sym} bloqué fail-closed"
            )
            return OrderResult(
                success=False,
                message=f"Etat risque indisponible pour {broker_id}",
            )

        # TradeLocker does not share the cTrader feed used to trigger the
        # signal. Read its REST quote immediately before submitting the order
        # so GFT cannot accept an entry already materially worse than theory.
        is_tradelocker = broker.config.get("type", "") == "tradelocker"
        if is_tradelocker and self._gft_preflight_enabled and not self.dry_run:
            try:
                broker_tick = await broker.get_quote(sym)
            except Exception as e:
                broker_tick = None
                logger.warning(
                    f"[Dispatcher] GFT preflight quote error {sym}: {e}"
                )
            if broker_tick is None:
                if self._gft_require_quote:
                    logger.warning(
                        f"[Dispatcher] ⛔ {broker_id}: pré-vol {sym} rejeté — "
                        "quote REST TradeLocker indisponible"
                    )
                    self._log_broker_reject(broker_id, signal, "gft_quote_unavailable")
                    return OrderResult(
                        success=False,
                        message="Pre-flight GFT: quote REST indisponible",
                    )
            else:
                broker_price = (
                    broker_tick.ask if signal.side == Side.LONG
                    else broker_tick.bid
                )
                direction = 1.0 if signal.side == Side.LONG else -1.0
                adverse = max(0.0, (broker_price - signal.close) * direction)
                risk_distance = abs(signal.close - signal.sl)
                adverse_r = adverse / risk_distance if risk_distance > 0 else 0.0
                adverse_atr = adverse / signal.atr if signal.atr > 0 else 0.0
                spread_atr = (
                    broker_tick.spread / signal.atr if signal.atr > 0 else 0.0
                )
                if signal.atr > 0 and spread_atr > signal.max_spread_atr:
                    self._log_gft_quote_coherence(
                        broker_id,
                        signal,
                        broker_tick,
                        tick,
                        decision="block",
                        reason="gft_spread_too_wide",
                    )
                    self._log_broker_reject(
                        broker_id, signal, "gft_spread_too_wide",
                        {"spread_atr": round(spread_atr, 4)},
                    )
                    return OrderResult(
                        success=False,
                        message=(
                            f"Pre-flight GFT: spread {spread_atr:.2f}ATR > "
                            f"{signal.max_spread_atr:.2f}"
                        ),
                    )
                if (
                    (signal.atr > 0 and adverse_atr > self._max_slippage_atr)
                    or (
                        self._gft_max_adverse_slippage_r > 0
                        and adverse_r > self._gft_max_adverse_slippage_r
                    )
                ):
                    self._log_gft_quote_coherence(
                        broker_id,
                        signal,
                        broker_tick,
                        tick,
                        decision="block",
                        reason="gft_adverse_entry_slippage",
                    )
                    logger.warning(
                        f"[Dispatcher] ⛔ {broker_id}: pré-vol {sym} rejeté — "
                        f"dérive défavorable={adverse_r:.2f}R/"
                        f"{adverse_atr:.2f}ATR"
                    )
                    self._log_broker_reject(
                        broker_id, signal, "gft_adverse_entry_slippage",
                        {
                            "adverse_r": round(adverse_r, 4),
                            "adverse_atr": round(adverse_atr, 4),
                            "spread_atr": round(spread_atr, 4),
                        },
                    )
                    return OrderResult(
                        success=False,
                        message=(
                            f"Pre-flight GFT: dérive défavorable "
                            f"{adverse_r:.2f}R/{adverse_atr:.2f}ATR"
                        ),
                    )
                logger.info(
                    f"[Dispatcher] GFT pré-vol {sym}: quote={broker_price:.5f} "
                    f"spread={spread_atr:.2f}ATR adverse={adverse_r:.2f}R/"
                    f"{adverse_atr:.2f}ATR"
                )
                self._log_gft_quote_coherence(
                    broker_id,
                    signal,
                    broker_tick,
                    tick,
                    decision="allow",
                )

        safe, reason = broker_guards.check_account_limits(signal, broker_state)
        if not safe:
            logger.warning(
                f"[Dispatcher] ⛔ {broker_id}: guard compte bloque {sym} — {reason}"
            )
            return OrderResult(success=False, message=f"Guard {broker_id}: {reason}")

        broker_sizing = broker_guards.compute_sizing(signal, broker_state)
        broker_risk_cash = self._apply_risk_modifiers(
            signal, broker_sizing.get("risk_cash", 0.0), log=True
        )
        if broker_risk_cash <= 0:
            return OrderResult(
                success=False,
                message=f"Risk cash = 0 pour {sym} sur {broker_id}",
            )

        # --- Calcul du volume SPÉCIFIQUE À CE BROKER ---
        # Le lot_size (contract_size) varie entre brokers pour le même symbole
        risk_distance = abs(signal.close - signal.sl)
        broker_volume = await self._compute_lots_for_broker(
            broker, broker_id, signal, broker_risk_cash, risk_distance,
            current_price=signal.close,
        )
        if broker_volume <= 0:
            return OrderResult(
                success=False,
                message=f"Volume calculé = 0 pour {sym} sur {broker_id}"
            )

        if self.dry_run:
            logger.info(
                f"[Dispatcher] [DRY RUN] {broker_id}: {sym} {signal.side.value} "
                f"{broker_volume:.3f}L @ {ps.entry_price:.5f} "
                f"SL={signal.sl:.5f} TP={signal.tp_indicative:.5f}"
            )
            result = OrderResult(
                success=True,
                order_id="dry_run",
                message="[DRY RUN] Ordre simulé"
            )
            result.risk_cash = broker_risk_cash
            result.volume_lots = broker_volume
            return result

        # Validation pré-envoi (volume min/max, arrondi prix)
        from arabesque.broker.normalizer import validate_order
        order_side = OrderSide.BUY if signal.side == Side.LONG else OrderSide.SELL
        validation = validate_order(
            broker=broker,
            symbol=sym,
            volume_lots=broker_volume,
            stop_loss=signal.sl if signal.sl > 0 else None,
            take_profit=signal.tp_indicative if signal.tp_indicative > 0 else None,
            entry_price=ps.entry_price,
            side=order_side.value,
        )
        if not validation.valid:
            logger.warning(
                f"[Dispatcher] ⛔ {broker_id}: pré-vol rejeté {sym} — "
                f"{validation.reason}"
            )
            return OrderResult(
                success=False,
                message=f"Pre-flight: {validation.reason}"
            )

        # Construire la requête d'ordre avec valeurs validées

        order = OrderRequest(
            symbol=sym,
            side=order_side,
            order_type=ps.order_type,
            volume=validation.volume_lots,
            entry_price=ps.entry_price if ps.order_type != OrderType.MARKET else None,
            stop_loss=validation.stop_loss if validation.stop_loss > 0 else None,
            take_profit=validation.take_profit if validation.take_profit > 0 else None,
            broker_symbol=broker_sym,
            label=f"arb_{signal.strategy_type[:3]}_{signal.signal_id[:8]}",
            comment=f"{signal.strategy_type}/{signal.regime}/{getattr(signal, 'timeframe', '')}",
        )

        try:
            result = await broker.place_order(order)
            result.risk_cash = broker_risk_cash
            result.volume_lots = validation.volume_lots
            if result.success:
                # Post-trade validation log
                self._log_trade_validation(
                    broker_id, sym, signal, order, validation, tick
                )
            else:
                logger.warning(
                    f"[Dispatcher] ❌ {broker_id}: échec ordre {sym} — {result.message}"
                )
            return result
        except Exception as e:
            logger.error(f"[Dispatcher] ❌ {broker_id}: exception: {e}")
            return OrderResult(success=False, message=str(e))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_last_tick(self, symbol: str) -> Optional[PriceTick]:
        """Accès au dernier tick via l'attribut injecté par LiveEngine."""
        if hasattr(self, '_price_feed') and self._price_feed:
            return self._price_feed.get_last_tick(symbol)
        return None

    def _determine_order_type(self, signal: Signal) -> OrderType:
        """
        Détermine si l'entrée est LIMIT ou STOP.

        Arabesque (mean-reversion) : on entre toujours sur pull-back → LIMIT
        Arabesque (trend) : on entre sur cassure → STOP
        Heuristique fallback : si entry > close (BUY) → STOP, sinon LIMIT
        """
        if signal.strategy_type == "mean_reversion":
            return OrderType.LIMIT
        elif signal.strategy_type in ("trend", "extension"):
            return OrderType.STOP
        else:
            # Heuristique
            if signal.side == Side.LONG:
                return OrderType.STOP if signal.close < signal.sl else OrderType.LIMIT
            else:
                return OrderType.STOP if signal.close > signal.sl else OrderType.LIMIT

    # ------------------------------------------------------------------
    # Corrélation inter-positions
    # ------------------------------------------------------------------

    @staticmethod
    def _instrument_category(instrument: str) -> str:
        """Catégorise un instrument pour le calcul de corrélation."""
        from arabesque.data.store import _categorize
        return _categorize(instrument)

    def _correlation_discount(self, instrument: str) -> float:
        """Calcule un facteur de réduction du risk pour les positions corrélées.

        Positions déjà ouvertes dans la même catégorie réduisent le risk :
          - 0 positions même catégorie → 1.0 (pas de réduction)
          - 1 position même catégorie → 0.70
          - 2 positions → 0.50
          - 3+ positions → 0.35
        Les positions cross-catégorie ne sont pas affectées.
        """
        cat = self._instrument_category(instrument)
        # Compter les positions ouvertes dans la même catégorie
        same_cat_count = 0
        for sym in self._account_state.open_instruments:
            if sym != instrument and self._instrument_category(sym) == cat:
                same_cat_count += 1

        # Facteurs de corrélation par palier
        if same_cat_count == 0:
            return 1.0
        elif same_cat_count == 1:
            return 0.70
        elif same_cat_count == 2:
            return 0.50
        else:
            return 0.35

    def _compute_lots(
        self,
        signal: Signal,
        risk_cash: float,
        risk_distance: float,
    ) -> float:
        """
        Calcule le volume en lots (référence, basé sur instruments.yaml).

        Utilisé uniquement pour le log d'acceptation du signal.
        Le volume réel par broker est calculé par _compute_lots_for_broker().
        """
        sym = signal.instrument
        inst = self.instruments_cfg.get(sym, {})
        pip_size = inst.get("pip_size", 0.0001)
        pip_value = inst.get("pip_value_per_lot", 10)

        if pip_value is None or pip_value == 0:
            pip_value = 10

        if pip_size <= 0 or risk_distance <= 0:
            return 0.01

        pips = risk_distance / pip_size
        if pips == 0:
            return 0.01

        lots = risk_cash / (pips * pip_value)

        min_lot = inst.get("min_lot", 0.01)
        max_lot = inst.get("max_lot", 10.0)
        step = inst.get("lot_step", 0.01)

        lots = max(min_lot, min(lots, max_lot))
        lots = round(round(lots / step) * step, 2)
        return lots

    async def _compute_lots_for_broker(
        self,
        broker: BaseBroker,
        broker_id: str,
        signal: Signal,
        risk_cash: float,
        risk_distance: float,
        current_price: float = 0,
    ) -> float:
        """Volume par broker avec conversion devise cotation.

        pip_value en USD selon la devise de cotation :
        - XXX/USD (EURUSD, crypto) : lot_size * pip_size (direct)
        - USD/XXX (USDJPY)         : lot_size * pip_size / price
        - Cross (NZDCAD, EURGBP)   : fallback instruments.yaml
        """
        sym = signal.instrument
        inst = self.instruments_cfg.get(sym, {})
        yaml_pip_size = inst.get("pip_size", 0.0001)
        pip_size = yaml_pip_size
        if pip_size <= 0 or risk_distance <= 0:
            return 0.01

        broker_lot_size = None
        broker_min_vol = 0.01
        broker_max_vol = 10000.0
        broker_step = 0.01
        broker_pip_size = None
        try:
            sym_info = await broker.get_symbol_info(sym)
            if sym_info and sym_info.lot_size > 0:
                broker_lot_size = sym_info.lot_size
                broker_min_vol = sym_info.min_volume
                broker_max_vol = sym_info.max_volume
                broker_step = sym_info.volume_step
                # Utiliser le pip_size du broker s'il diffère du yaml
                # (ex: GFT XAUUSD pip_size=0.0001 vs yaml 0.01)
                if sym_info.pip_size > 0:
                    broker_pip_size = sym_info.pip_size
        except Exception as e:
            logger.debug(f"[Dispatcher] get_symbol_info failed for {sym}: {e}")

        # pip_size du broker prioritaire — les conventions diffèrent entre brokers
        if broker_pip_size and broker_pip_size != pip_size:
            logger.info(
                f"[Dispatcher] {broker_id} {sym}: pip_size broker={broker_pip_size} "
                f"!= yaml={pip_size}, using broker value"
            )
            pip_size = broker_pip_size

        pip_value = None
        conversion = "direct"

        if broker_lot_size:
            raw_pip_value = broker_lot_size * pip_size  # en devise de cotation
            quote_ccy = sym[-3:] if len(sym) >= 6 else ''
            base_ccy = sym[:3] if len(sym) >= 6 else ''

            if quote_ccy == "USD":
                # XXX/USD ou crypto : préférer yaml pip_value avec rescaling
                # broker lot_size n'est pas fiable pour crypto
                # (TradeLocker reporte 100k comme pour forex, mais 1 lot = 1 unité)
                yaml_pv = inst.get("pip_value_per_lot")
                if yaml_pv and yaml_pv > 0:
                    pip_ratio = pip_size / yaml_pip_size if yaml_pip_size > 0 else 1
                    pip_value = yaml_pv * pip_ratio
                    conversion = f"USD-yaml(×{pip_ratio:.2g})" if pip_ratio != 1 else "USD-direct"
                else:
                    pip_value = raw_pip_value
                    conversion = "USD-quoted"
            elif base_ccy == "USD" and current_price > 0:
                # USD/XXX : diviser par le prix courant
                pip_value = raw_pip_value / current_price
                conversion = f"/{current_price:.3f}"
            else:
                # Cross pair : utiliser yaml pip_value_per_lot
                yaml_pv = inst.get("pip_value_per_lot")
                if yaml_pv and yaml_pv > 0:
                    # yaml_pv calibré pour yaml_pip_size — rescaler si broker pip_size diffère
                    pip_ratio = pip_size / yaml_pip_size if yaml_pip_size > 0 else 1
                    pip_value = yaml_pv * pip_ratio
                    conversion = f"yaml-cross(×{pip_ratio:.0f})" if pip_ratio != 1 else "yaml-cross"
                else:
                    pip_value = raw_pip_value / max(current_price, 1)
                    conversion = f"est/{current_price:.3f}"

        if not pip_value or pip_value <= 0:
            yaml_pv = inst.get("pip_value_per_lot", 10) or 10
            # Rescaler si pip_size a été overridé par le broker
            pip_ratio = pip_size / yaml_pip_size if yaml_pip_size > 0 else 1
            pip_value = yaml_pv * pip_ratio
            conversion = f"yaml-fallback(×{pip_ratio:.0f})" if pip_ratio != 1 else "yaml-fallback"

        pips = risk_distance / pip_size
        lots = risk_cash / (pips * pip_value)

        lots = max(broker_min_vol, min(lots, broker_max_vol))
        if broker_step > 0:
            lots = round(round(lots / broker_step) * broker_step, 8)

        executed_risk_cash = pips * pip_value * lots
        if (
            risk_cash > 0 and self._max_executed_risk_ratio > 0
            and executed_risk_cash > risk_cash * self._max_executed_risk_ratio
        ):
            logger.warning(
                f"[Dispatcher] ⛔ {broker_id} {sym}: risk overshoot apres "
                f"minimum/step broker — cible={risk_cash:.2f}$, "
                f"execute={executed_risk_cash:.2f}$ "
                f"({executed_risk_cash / risk_cash:.2f}x > "
                f"{self._max_executed_risk_ratio:.2f}x), ordre bloque"
            )
            return 0.0

        yaml_pip_value = inst.get("pip_value_per_lot", "N/A")
        logger.info(
            f"[Dispatcher] sizing {broker_id} {sym}: "
            f"risk={risk_cash:.0f}$ dist={risk_distance:.5f} "
            f"lot_size={broker_lot_size or 'yaml'} "
            f"pip_val={pip_value:.4f}({conversion}) "
            f"(yaml={yaml_pip_value}) "
            f"-> {lots:.3f}L "
            f"[{broker_min_vol:.4f}-{broker_max_vol:.0f} step={broker_step}]"
        )
        return lots

    def _log_trade_validation(
        self,
        broker_id: str,
        sym: str,
        signal: Signal,
        order: OrderRequest,
        validation,
        tick: PriceTick,
    ):
        """Log de validation post-trade pour vérifier la cohérence.

        Vérifie et logge:
        - Type d'ordre (MARKET vs pending)
        - Slippage entry vs signal.close
        - SL/TP correctement positionnés
        - Risque théorique en € et R
        - Cohérence volume/risque
        """
        inst = self.instruments_cfg.get(sym, {})
        pip_size = inst.get("pip_size", 0.0001)

        # Slippage
        fill_price = tick.mid if hasattr(tick, 'mid') else (tick.bid + tick.ask) / 2
        slippage = abs(fill_price - signal.close) if fill_price > 0 else 0
        slip_pips = slippage / pip_size if pip_size > 0 else 0

        # Risk
        risk_dist = abs(signal.close - signal.sl)
        risk_r = risk_dist / abs(signal.close - signal.sl) if signal.sl != signal.close else 0

        # Reward/Risk
        tp_dist = abs(signal.tp_indicative - signal.close) if signal.tp_indicative else 0
        rr = tp_dist / risk_dist if risk_dist > 0 else 0

        logger.info(
            f"[Dispatcher] ✅ {broker_id}: TRADE PLACÉ {sym} {signal.side.value} "
            f"{order.volume:.3f}L | "
            f"type={order.order_type.value} | "
            f"entry={signal.close:.5f} fill≈{fill_price:.5f} slip={slip_pips:.1f}pip | "
            f"SL={order.stop_loss} TP={order.take_profit} RR={rr:.2f} | "
            f"risk_dist={risk_dist:.5f}"
        )
