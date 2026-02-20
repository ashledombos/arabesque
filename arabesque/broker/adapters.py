"""
Arabesque — Broker Adapters.

Interface de base + implémentations cTrader (FTMO) et TradeLocker (GFT).

Le code de connexion réel est dans envolees-auto. Ce module fournit
l'interface que le webhook attend, à brancher sur les implémentations
existantes.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

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


class CTraderAdapter(BrokerAdapter):
    """Adapter pour cTrader (FTMO).
    
    À brancher sur le code existant d'envolees-auto/brokers/ctrader.py.
    Le code cTrader utilise l'Open API async avec protobuf.
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.name = config.get("name", "ctrader")
        self._connected = False
        # TODO: importer et initialiser le client cTrader depuis envolees-auto
        # from envolees_auto.brokers.ctrader import CTraderClient
        # self.client = CTraderClient(config)
    
    def connect(self) -> bool:
        logger.info(f"[{self.name}] Connecting to cTrader...")
        # TODO: self.client.connect()
        self._connected = True
        return True
    
    def get_quote(self, symbol: str) -> dict:
        # TODO: self.client.get_symbol_quote(symbol)
        return {"bid": 0, "ask": 0, "spread": 0}
    
    def get_account_info(self) -> dict:
        # TODO: self.client.get_account()
        return {"balance": 0, "equity": 0, "margin_used": 0}
    
    def compute_volume(self, symbol: str, risk_cash: float, risk_distance: float) -> float:
        # cTrader volume en unités (100_000 = 1 lot standard)
        # pip_value dépend du symbole
        # TODO: Récupérer pip_value du symbole via self.client
        # volume_units = risk_cash / (risk_distance / pip_size * pip_value)
        # return round_to_step(volume_units, step_volume)
        return 0.0
    
    def place_order(self, signal: dict, sizing: dict) -> dict:
        side = signal.get("side", "buy")
        symbol = signal.get("symbol", "")
        sl = signal.get("sl", 0)
        
        volume = self.compute_volume(
            symbol,
            sizing.get("risk_cash", 0),
            sizing.get("risk_distance", 0),
        )
        
        logger.info(f"[{self.name}] Placing MARKET {side.upper()} {symbol} "
                     f"vol={volume:.2f} SL={sl}")
        
        # TODO: self.client.place_market_order(symbol, side, volume, sl)
        return {"success": False, "message": "cTrader adapter not connected", "volume": volume}
    
    def close_position(self, position_id: str, symbol: str) -> dict:
        # TODO: self.client.close_position(position_id)
        return {"success": False, "message": "not implemented"}
    
    def modify_sl(self, position_id: str, symbol: str, new_sl: float) -> dict:
        # TODO: self.client.modify_sl(position_id, new_sl)
        return {"success": False, "message": "not implemented"}


class TradeLockerAdapter(BrokerAdapter):
    """Adapter pour TradeLocker (Goat Funded Trader).
    
    À brancher sur envolees-auto/brokers/tradelocker.py.
    TradeLocker utilise une REST API + lib Python.
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.name = config.get("name", "tradelocker")
        self._connected = False
        # TODO: from tradelocker import TLAPI
        # self.tl = TLAPI(...)
    
    def connect(self) -> bool:
        logger.info(f"[{self.name}] Connecting to TradeLocker...")
        # TODO: self.tl.authenticate()
        self._connected = True
        return True
    
    def get_quote(self, symbol: str) -> dict:
        return {"bid": 0, "ask": 0, "spread": 0}
    
    def get_account_info(self) -> dict:
        return {"balance": 0, "equity": 0, "margin_used": 0}
    
    def compute_volume(self, symbol: str, risk_cash: float, risk_distance: float) -> float:
        return 0.0
    
    def place_order(self, signal: dict, sizing: dict) -> dict:
        side = signal.get("side", "buy")
        symbol = signal.get("symbol", "")
        sl = signal.get("sl", 0)
        
        logger.info(f"[{self.name}] Placing MARKET {side.upper()} {symbol} SL={sl}")
        
        # TODO: self.tl.create_order(...)
        return {"success": False, "message": "TradeLocker adapter not connected"}
    
    def close_position(self, position_id: str, symbol: str) -> dict:
        return {"success": False, "message": "not implemented"}
    
    def modify_sl(self, position_id: str, symbol: str, new_sl: float) -> dict:
        return {"success": False, "message": "not implemented"}


class DryRunAdapter(BrokerAdapter):
    """Adapter dry-run pour tests sans broker réel."""
    
    def __init__(self, config: dict | None = None):
        self.name = "dry_run"
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
        return {"balance": 100_000, "equity": 100_000, "margin_used": 0}
    
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
