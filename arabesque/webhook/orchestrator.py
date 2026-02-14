"""
Arabesque v2 — Orchestrator.

Pipeline complet : Signal → Guards → Broker → PositionManager → Audit.

Utilisé par :
- Le webhook server (signal live)
- Le paper trading (dry-run avec vrais signaux)

C'est le MÊME flux, que le broker soit réel ou dry-run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from arabesque.models import Signal, Decision, DecisionType, Side
from arabesque.guards import Guards, PropConfig, ExecConfig, AccountState
from arabesque.position.manager import PositionManager, ManagerConfig
from arabesque.audit import AuditLogger
from arabesque.broker.adapters import BrokerAdapter
from arabesque.config import ArabesqueConfig

logger = logging.getLogger("arabesque.orchestrator")


class Orchestrator:
    """Orchestre le pipeline complet de trading.

    Flux pour un signal entrant :
    1. Parse le signal JSON → Signal
    2. Vérifie le timing (expiry)
    3. Obtient le quote broker (bid/ask)
    4. Passe les guards (prop + exec)
    5. Calcule le sizing
    6. Place l'ordre (broker)
    7. Ouvre la position (PositionManager)
    8. Loggue tout (Audit)
    9. Notifie (Telegram/ntfy)

    Flux pour une mise à jour (chaque bougie 1H) :
    1. Met à jour chaque position ouverte (OHLC + indicateurs)
    2. Si le manager génère une décision (trailing, exit...) :
       a. Envoie la modification/fermeture au broker
       b. Met à jour le compte
       c. Loggue + notifie
    """

    def __init__(
        self,
        config: ArabesqueConfig,
        brokers: dict[str, BrokerAdapter],
    ):
        self.config = config
        self.brokers = brokers

        # Guards
        self.guards = Guards(
            PropConfig(
                max_daily_dd_pct=config.max_daily_dd_pct,
                max_total_dd_pct=config.max_total_dd_pct,
                max_positions=config.max_positions,
                max_daily_trades=config.max_daily_trades,
                risk_per_trade_pct=config.risk_per_trade_pct,
            ),
            ExecConfig(
                max_spread_atr=config.max_spread_atr,
                max_slippage_atr=config.max_slippage_atr,
                signal_expiry_sec=config.signal_expiry_sec,
                min_rr=config.min_rr,
            ),
        )

        # Account state
        self.account = AccountState(
            balance=config.start_balance,
            equity=config.start_balance,
            start_balance=config.start_balance,
            daily_start_balance=config.start_balance,
        )

        # Position Manager (MÊME code que le backtest)
        self.manager = PositionManager(ManagerConfig())

        # Audit
        self.audit = AuditLogger(log_dir=config.audit_dir)

        # State tracking
        self._last_daily_reset: str = ""
        self._position_broker_map: dict[str, str] = {}  # position_id → broker_name
        self._position_broker_id: dict[str, str] = {}   # position_id → broker_order_id

    def handle_signal(self, data: dict) -> dict:
        """Traite un signal entrant (depuis le webhook).

        Args:
            data: JSON du signal TradingView

        Returns:
            {"status": "accepted"|"rejected"|"error", "details": ...}
        """
        try:
            # 1. Parse le signal
            signal = Signal.from_webhook_json(data)
            logger.info(f"Signal received: {signal.instrument} {signal.side.value} "
                        f"@ {signal.tv_close}")

            # 2. Check daily reset
            self._check_daily_reset()

            # 3. Check instrument autorisé
            if (self.config.instruments and
                    signal.instrument not in self.config.instruments):
                logger.info(f"Instrument {signal.instrument} not in allowed list")
                return {"status": "rejected", "reason": "instrument not allowed"}

            # 4. Sélectionner le broker
            broker = self._select_broker(signal.instrument)
            if broker is None:
                return {"status": "error", "reason": "no broker available"}

            # 5. Obtenir le quote broker
            # Pour le dry-run, seeder avec le prix du signal
            if hasattr(broker, '_last_prices') and signal.tv_close > 0:
                broker._last_prices[signal.instrument] = signal.tv_close

            quote = broker.get_quote(signal.instrument)
            bid = quote.get("bid", 0)
            ask = quote.get("ask", 0)

            if bid <= 0 or ask <= 0:
                logger.warning(f"Invalid quote for {signal.instrument}: {quote}")
                return {"status": "error", "reason": "invalid broker quote"}

            # 6. Guards
            ok, decision = self.guards.check_all(signal, self.account, bid, ask)
            self.audit.log_decision(decision)

            if not ok:
                reason = (decision.reject_reason.value
                          if decision.reject_reason else decision.reason)
                logger.info(f"Signal REJECTED: {reason}")

                # Counterfactual pour signaux rejetés
                self._create_rejection_counterfactual(signal, decision, bid, ask)

                self._notify(
                    f"❌ {signal.instrument} {signal.side.value} REJETÉ: {reason}"
                )
                return {"status": "rejected", "reason": reason}

            # 7. Sizing
            sizing = self.guards.compute_sizing(signal, self.account)
            risk_cash = sizing["risk_cash"]
            risk_distance = sizing["risk_distance"]

            if risk_cash <= 0:
                return {"status": "rejected", "reason": "sizing=0"}

            # 8. Placer l'ordre
            order_result = broker.place_order(
                data,  # Passer le JSON original
                sizing,
            )

            if not order_result.get("success"):
                msg = order_result.get("message", "unknown error")
                logger.error(f"Order failed: {msg}")
                return {"status": "error", "reason": f"order failed: {msg}"}

            fill_price = order_result.get("fill_price", 0)
            if fill_price <= 0:
                # Fallback : utiliser le quote
                fill_price = ask if signal.side == Side.LONG else bid

            volume = order_result.get("volume", 0)
            broker_order_id = order_result.get("order_id", "")

            # 9. Ouvrir la position (PositionManager)
            pos = self.manager.open_position(
                signal, fill_price, risk_cash, volume
            )

            # Tracker le mapping position → broker
            broker_name = next(
                (n for n, b in self.brokers.items() if b is broker),
                "unknown"
            )
            self._position_broker_map[pos.position_id] = broker_name
            self._position_broker_id[pos.position_id] = broker_order_id

            # 10. Mettre à jour le compte
            self.account.open_positions += 1
            self.account.open_instruments.append(signal.instrument)
            self.account.daily_trades += 1

            # 11. Log + Notify
            self.audit.log_decision(Decision(
                decision_type=DecisionType.ORDER_FILLED,
                signal_id=signal.signal_id,
                position_id=pos.position_id,
                instrument=signal.instrument,
                reason=f"Filled @ {fill_price:.5f}",
                price_at_decision=fill_price,
                metadata={
                    "volume": volume,
                    "broker": broker_name,
                    "broker_order_id": broker_order_id,
                    "sl": pos.sl,
                    "tp": pos.tp,
                    "R": pos.R,
                },
            ))

            self._notify(
                f"✅ {signal.instrument} {signal.side.value} OUVERT\n"
                f"   Fill: {fill_price:.5f} | SL: {pos.sl:.5f} | "
                f"R: {pos.R:.5f}\n"
                f"   Volume: {volume:.2f} lots | Risk: ${risk_cash:.0f}"
            )

            logger.info(f"Position opened: {pos.summary()}")

            return {
                "status": "accepted",
                "position_id": pos.position_id,
                "fill_price": fill_price,
                "sl": pos.sl,
                "tp": pos.tp,
                "volume": volume,
            }

        except Exception as e:
            logger.exception(f"handle_signal error: {e}")
            return {"status": "error", "reason": str(e)}

    def update_positions(
        self,
        instrument: str,
        high: float,
        low: float,
        close: float,
        indicators: dict | None = None,
    ) -> list[dict]:
        """Met à jour les positions ouvertes pour un instrument.

        Appelé à chaque nouvelle bougie 1H (via cron ou webhook).

        Returns:
            Liste des actions effectuées
        """
        actions = []

        for pos in list(self.manager.open_positions):
            if pos.instrument != instrument:
                continue

            decisions = self.manager.update_position(
                pos, high, low, close, indicators
            )

            for decision in decisions:
                self.audit.log_decision(decision)

                # SL modifié → envoyer au broker
                if decision.decision_type in (
                    DecisionType.SL_BREAKEVEN,
                    DecisionType.TRAILING_ACTIVATED,
                    DecisionType.TRAILING_TIGHTENED,
                ):
                    self._broker_modify_sl(pos)
                    actions.append({
                        "action": "sl_modified",
                        "position_id": pos.position_id,
                        "new_sl": pos.sl,
                        "reason": decision.reason,
                    })

                # Position fermée
                if not pos.is_open:
                    self._broker_close_position(pos)
                    self._update_account_on_close(pos)

                    result = pos.result_r or 0
                    pnl = result * pos.risk_cash

                    self._notify(
                        f"{'🟢' if result > 0 else '🔴'} "
                        f"{pos.instrument} {pos.side.value} FERMÉ\n"
                        f"   {pos.exit_reason} | {result:+.2f}R | "
                        f"${pnl:+,.0f}\n"
                        f"   MFE: {pos.mfe_r:.2f}R | Bars: {pos.bars_open}"
                    )

                    actions.append({
                        "action": "closed",
                        "position_id": pos.position_id,
                        "result_r": result,
                        "exit_reason": pos.exit_reason,
                    })

        # Mettre à jour les counterfactuals
        self.manager.update_counterfactuals(instrument, high, low, close)

        return actions

    def get_status(self) -> dict:
        """Retourne l'état courant du système."""
        open_pos = self.manager.open_positions
        closed_pos = self.manager.closed_positions

        return {
            "mode": self.config.mode,
            "account": {
                "balance": self.account.balance,
                "equity": self.account.equity,
                "daily_pnl": self.account.daily_pnl,
                "daily_dd_pct": round(self.account.daily_dd_pct, 2),
                "total_dd_pct": round(self.account.total_dd_pct, 2),
                "open_positions": self.account.open_positions,
                "daily_trades": self.account.daily_trades,
            },
            "positions": {
                "open": [p.summary() for p in open_pos],
                "closed_today": len([
                    p for p in closed_pos
                    if p.ts_exit and p.ts_exit.date() == datetime.now(timezone.utc).date()
                ]),
                "total_closed": len(closed_pos),
            },
            "audit": self.audit.summary(),
            "brokers": {
                name: {"connected": True}
                for name, b in self.brokers.items()
            },
        }

    # ── Internal helpers ─────────────────────────────────────────────

    def _select_broker(self, instrument: str) -> BrokerAdapter | None:
        """Sélectionne le broker approprié pour l'instrument."""
        if not self.brokers:
            return None

        # En mode dry_run, utiliser le dry_run adapter
        if self.config.mode == "dry_run":
            return self.brokers.get("dry_run") or next(iter(self.brokers.values()))

        # Sinon, utiliser le premier broker connecté
        # (à enrichir : routage par instrument)
        for name, broker in self.brokers.items():
            if name != "dry_run":
                return broker

        return next(iter(self.brokers.values()))

    def _broker_modify_sl(self, pos):
        """Envoie la modification de SL au broker."""
        broker_name = self._position_broker_map.get(pos.position_id)
        broker_id = self._position_broker_id.get(pos.position_id)
        if broker_name and broker_id:
            broker = self.brokers.get(broker_name)
            if broker:
                result = broker.modify_sl(broker_id, pos.instrument, pos.sl)
                if not result.get("success"):
                    logger.error(f"Failed to modify SL: {result}")

    def _broker_close_position(self, pos):
        """Envoie la fermeture au broker."""
        broker_name = self._position_broker_map.get(pos.position_id)
        broker_id = self._position_broker_id.get(pos.position_id)
        if broker_name and broker_id:
            broker = self.brokers.get(broker_name)
            if broker:
                result = broker.close_position(broker_id, pos.instrument)
                if not result.get("success"):
                    logger.error(f"Failed to close position: {result}")

    def _update_account_on_close(self, pos):
        """Met à jour le compte après fermeture."""
        if pos.result_r is not None:
            pnl = pos.result_r * pos.risk_cash
            self.account.equity += pnl
            self.account.balance += pnl
            self.account.daily_pnl += pnl
        self.account.open_positions = max(0, self.account.open_positions - 1)
        if pos.instrument in self.account.open_instruments:
            self.account.open_instruments.remove(pos.instrument)

    def _create_rejection_counterfactual(self, signal, decision, bid, ask):
        """Crée un counterfactual pour un signal rejeté."""
        from arabesque.models import Counterfactual
        fill_est = ask if signal.side == Side.LONG else bid
        cf = Counterfactual(
            signal_id=signal.signal_id,
            decision_type=DecisionType.SIGNAL_REJECTED,
            instrument=signal.instrument,
            side=signal.side,
            hypothetical_entry=fill_est,
            hypothetical_sl=signal.sl,
            hypothetical_tp=signal.tp_indicative,
            ts_decision=datetime.now(timezone.utc),
            price_at_decision=fill_est,
            mfe_after=fill_est,
            mae_after=fill_est,
        )
        self.manager.counterfactuals.append(cf)

    def _check_daily_reset(self):
        """Reset le compte quotidien si on a changé de jour."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_daily_reset:
            self.account.new_day()
            self._last_daily_reset = today
            logger.info(f"Daily reset: equity={self.account.equity:.0f}")

    def _notify(self, message: str):
        """Envoie une notification (Telegram + ntfy)."""
        logger.info(f"NOTIFY: {message}")

        # Telegram
        if self.config.telegram_token and self.config.telegram_chat_id:
            try:
                import requests
                url = (f"https://api.telegram.org/bot{self.config.telegram_token}"
                       f"/sendMessage")
                requests.post(url, json={
                    "chat_id": self.config.telegram_chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                }, timeout=5)
            except Exception as e:
                logger.error(f"Telegram notification failed: {e}")

        # ntfy
        if self.config.ntfy_topic:
            try:
                import requests
                url = f"{self.config.ntfy_url}/{self.config.ntfy_topic}"
                requests.post(url, data=message.encode("utf-8"), timeout=5)
            except Exception as e:
                logger.error(f"ntfy notification failed: {e}")
