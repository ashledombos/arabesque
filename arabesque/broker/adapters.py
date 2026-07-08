"""
Arabesque — Broker Adapters (interface simplifiée + dry-run).

Contient l'interface `BrokerAdapter` (consommée par execution/orchestrator.py)
et `DryRunAdapter` (mode dry-run de live.py). Les connecteurs réels vivent
dans `ctrader.py` / `tradelocker.py` (interface complète `broker/base.py`) —
tout nouveau broker (ex. Hyperliquid) suit ce modèle-là, pas celui-ci.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger("arabesque.broker")


@dataclass
class OrderResult:
    success: bool
    order_id: str = ""
    volume: float = 0.0
    fill_price: float = 0.0
    message: str = ""
    
    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "order_id": self.order_id,
            "volume": self.volume,
            "fill_price": self.fill_price,
            "message": self.message,
        }


class BrokerAdapter(ABC):
    """Interface commune pour tous les brokers."""
    
    @abstractmethod
    def connect(self) -> bool:
        """Établir la connexion."""
        ...
    
    @abstractmethod
    def get_quote(self, symbol: str) -> dict:
        """Obtenir bid/ask.
        
        Returns: {"bid": float, "ask": float, "spread": float}
        """
        ...
    
    @abstractmethod
    def get_account_info(self) -> dict:
        """Obtenir balance, equity, marge.
        
        Returns: {"balance": float, "equity": float, "margin_used": float}
        """
        ...
    
    @abstractmethod
    def compute_volume(self, symbol: str, risk_cash: float, 
                       risk_distance: float) -> float:
        """Calcule le volume (lots) pour un risque donné.
        
        Args:
            symbol: Symbole instrument
            risk_cash: Montant à risquer en devise du compte
            risk_distance: Distance entry→SL en prix
        
        Returns:
            Volume en lots, arrondi au step du symbole
        """
        ...
    
    @abstractmethod
    def place_order(self, signal: dict, sizing: dict) -> dict:
        """Place un ordre chez le broker.
        
        Args:
            signal: JSON signal TradingView
            sizing: Dict avec risk_cash, risk_distance, etc.
        
        Returns:
            {"success": bool, "order_id": str, "volume": float, ...}
        """
        ...
    
    @abstractmethod
    def close_position(self, position_id: str, symbol: str) -> dict:
        """Ferme une position.
        
        Returns:
            {"success": bool, "message": str}
        """
        ...
    
    @abstractmethod
    def modify_sl(self, position_id: str, symbol: str, new_sl: float) -> dict:
        """Modifie le SL d'une position ouverte.
        
        Returns:
            {"success": bool, "message": str}
        """
        ...

    def on_trade_closed(self, pnl: float) -> None:
        """Callback appelé par l'orchestrator quand une position se ferme.

        Implémentation par défaut : no-op.  Surcharger dans les adapters
        qui ont besoin de tracker l'equity localement (ex: DryRunAdapter).
        """
        pass


class DryRunAdapter(BrokerAdapter):
    """Adapter dry-run pour tests sans broker réel.

    Tracks equity and balance in real time so that get_account_info()
    returns the actual account state (important for _refresh_account_state
    in the live engine and for P3 cTrader dry-run mode).
    """

    def __init__(self, config: dict | None = None, start_balance: float = 100_000.0):
        self.name = "dry_run"
        self._start_balance = start_balance
        self._equity: float = start_balance
        self._balance: float = start_balance
        self._orders: list[dict] = []
        self._last_prices: dict[str, float] = {}   # symbol → last known price

    def connect(self) -> bool:
        return True

    def get_quote(self, symbol: str, reference_price: float = 0.0) -> dict:
        """Fournit un quote réaliste basé sur le prix de référence.

        Si reference_price=0, utilise le dernier prix connu pour ce symbole.
        Spread simulé = 0.01% du prix (typique FX majeurs).
        """
        if reference_price <= 0:
            reference_price = self._last_prices.get(symbol, 1.0)
        self._last_prices[symbol] = reference_price
        spread = reference_price * 0.0001
        return {"bid": reference_price, "ask": reference_price + spread, "spread": spread}

    def get_account_info(self) -> dict:
        """Retourne l'état réel du compte simulé (equity mise à jour à chaque trade)."""
        return {"balance": self._balance, "equity": self._equity, "margin_used": 0}

    def on_trade_closed(self, pnl: float) -> None:
        """Appelé par l'orchestrator après chaque clôture de position.

        Met à jour equity et balance pour que get_account_info() reflète
        l'état réel du compte dry-run.  Sans cet appel, _refresh_account_state()
        en mode P3 (cTrader dry-run) aurait écrasé l'AccountState avec 100 000 $
        fixes, rendant les guards DD aveugles.
        """
        self._equity += pnl
        self._balance += pnl
        logger.debug(f"[dry_run] on_trade_closed pnl={pnl:+.2f} → equity={self._equity:.2f}")
    
    def compute_volume(self, symbol: str, risk_cash: float, risk_distance: float) -> float:
        if risk_distance == 0:
            return 0.01
        # Simplified: assume pip_value ≈ 10$/lot for major pairs
        lots = risk_cash / (risk_distance * 10_000 * 10)
        return round(max(lots, 0.01), 2)
    
    def place_order(self, signal: dict, sizing: dict) -> dict:
        volume = self.compute_volume(
            signal.get("symbol", ""),
            sizing.get("risk_cash", 0),
            sizing.get("risk_distance", 0),
        )
        order = {
            "success": True,
            "order_id": f"DRY_{len(self._orders) + 1:04d}",
            "volume": volume,
            "fill_price": signal.get("close", 0),
            "message": "dry run",
        }
        self._orders.append(order)
        logger.info(f"[dry_run] Order: {signal.get('side','?').upper()} "
                     f"{signal.get('symbol','')} vol={volume:.2f}")
        return order
    
    def close_position(self, position_id: str, symbol: str) -> dict:
        return {"success": True, "message": f"dry_run close {position_id}"}
    
    def modify_sl(self, position_id: str, symbol: str, new_sl: float) -> dict:
        return {"success": True, "message": f"dry_run modify SL → {new_sl}"}
