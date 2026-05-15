#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Base broker interface and common types.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass
class OrderRequest:
    """Order request to be sent to broker"""
    symbol: str
    side: OrderSide
    order_type: OrderType
    volume: float
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    expiry_timestamp_ms: Optional[int] = None
    label: str = ""
    comment: str = ""
    magic_number: Optional[int] = None
    broker_symbol: Optional[str] = None
    broker_volume: Optional[int] = None


@dataclass
class OrderResult:
    """Result of an order operation"""
    success: bool
    order_id: Optional[str] = None
    message: str = ""
    error_code: Optional[str] = None
    broker_response: Optional[Any] = None
    fill_price: Optional[float] = None
    fill_volume: Optional[float] = None
    # Enrichi par le dispatcher après placement
    risk_cash: float = 0.0          # Risque calculé en devise
    volume_lots: float = 0.0        # Volume calculé en lots (avant ajustement broker)
    fill_time: Optional[datetime] = None


@dataclass
class Position:
    """Open position"""
    position_id: str
    symbol: str
    side: OrderSide
    volume: float
    entry_price: float
    current_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    profit: Optional[float] = None
    swap: float = 0.0
    commission: float = 0.0
    used_margin: float = 0.0
    open_time: Optional[datetime] = None


@dataclass
class PendingOrder:
    """Pending order (not yet filled)"""
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    volume: float
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    created_time: Optional[datetime] = None
    expiry_time: Optional[datetime] = None
    label: str = ""
    comment: str = ""
    broker_id: str = ""
    raw_data: Optional[dict] = None


@dataclass
class AccountInfo:
    """Account information"""
    account_id: str
    broker_name: str
    balance: float
    equity: float
    margin_used: float = 0.0
    margin_free: float = 0.0
    currency: str = "USD"
    leverage: int = 100
    is_demo: bool = True


@dataclass
class SymbolInfo:
    """Symbol/instrument information"""
    symbol: str
    broker_symbol: str
    description: str = ""
    pip_value: float = 0.0001
    pip_size: float = 0.0001
    lot_size: float = 100000
    min_volume: float = 0.01
    max_volume: float = 100
    volume_step: float = 0.01
    tick_size: float = 0.00001
    digits: int = 5
    is_tradable: bool = True

    def round_price_to_tick(self, price: float, direction: str = "nearest") -> float:
        import math
        if self.tick_size <= 0:
            return round(price, self.digits)
        ticks = price / self.tick_size
        if direction == "up":
            rounded_ticks = math.ceil(ticks)
        elif direction == "down":
            rounded_ticks = math.floor(ticks)
        else:
            rounded_ticks = round(ticks)
        return round(rounded_ticks * self.tick_size, self.digits)

    def round_sl_conservative(self, sl_price: float, entry_price: float) -> float:
        if sl_price < entry_price:
            return self.round_price_to_tick(sl_price, "down")
        else:
            return self.round_price_to_tick(sl_price, "up")

    def round_tp_conservative(self, tp_price: float, entry_price: float) -> float:
        if tp_price > entry_price:
            return self.round_price_to_tick(tp_price, "down")
        else:
            return self.round_price_to_tick(tp_price, "up")

    def round_entry_conservative(self, entry_price: float, side: 'OrderSide') -> float:
        if side == OrderSide.BUY:
            return self.round_price_to_tick(entry_price, "up")
        else:
            return self.round_price_to_tick(entry_price, "down")


@dataclass
class OrderValidation:
    """Result of post-order validation"""
    is_valid: bool
    warnings: List[str] = field(default_factory=list)
    requested_sl: Optional[float] = None
    actual_sl: Optional[float] = None
    sl_deviation_pips: Optional[float] = None
    requested_tp: Optional[float] = None
    actual_tp: Optional[float] = None
    tp_deviation_pips: Optional[float] = None
    requested_volume: Optional[float] = None
    actual_volume: Optional[float] = None
    volume_deviation_percent: Optional[float] = None
    risk_deviation_percent: Optional[float] = None


@dataclass
class PriceTick:
    """Prix temps réel d'un instrument (depuis le price feed cTrader)"""
    symbol: str
    bid: float
    ask: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class FreshQuote:
    """Quote fraîche obtenue par requête broker explicite (REST/RPC), distincte
    du stream de ticks PriceFeed.

    Introduit par la Phase 2.5 pour casser la dépendance BE↔PriceFeed :
    quand le stream cTrader meurt silencieusement (cas 14/05), un polling
    broker direct via cette interface continue d'armer le BE.

    Champs :
        symbol      : nom unifié (EURUSD, XAUUSD…)
        price       : prix unique côté pertinent (bid pour LONG, ask pour SHORT)
        quote_type  : "bid" ou "ask" — explicite, le caller demande un côté précis
        market_ts   : timestamp serveur du tick (ex: cTrader ProtoOATickData.timestamp).
                      None si le broker ne fournit pas de timestamp marché
                      (ex: TradeLocker REST). Le caller doit alors retomber
                      sur observed_at avec un flag de fiabilité dégradée.
        observed_at : timestamp client à la réception — toujours rempli.
                      Sert de borne supérieure pour la freshness quand
                      market_ts est absent.
    """
    symbol: str
    price: float
    quote_type: str
    market_ts: Optional[datetime] = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class OHLCBar:
    """
    Bougie OHLCV.
    Format standard retourné par get_history() et consommé par BarAggregator.
    """
    ts: int          # timestamp Unix (secondes) du début de la bougie
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


def validate_placed_order(
    requested: 'OrderRequest',
    actual_sl: Optional[float],
    actual_tp: Optional[float],
    actual_volume: Optional[float],
    pip_size: float = 0.0001,
    max_sl_deviation_pips: float = 5.0,
    max_volume_deviation_percent: float = 5.0
) -> OrderValidation:
    validation = OrderValidation(is_valid=True)
    warnings = []

    if requested.stop_loss and actual_sl:
        validation.requested_sl = requested.stop_loss
        validation.actual_sl = actual_sl
        sl_diff = abs(actual_sl - requested.stop_loss)
        sl_pips = sl_diff / pip_size
        validation.sl_deviation_pips = sl_pips
        if sl_pips > max_sl_deviation_pips:
            if requested.side == OrderSide.BUY:
                if actual_sl < requested.stop_loss:
                    warnings.append(f"⚠️ SL {sl_pips:.1f} pips FURTHER than requested - risk INCREASED")
                    validation.is_valid = False
                else:
                    warnings.append(f"ℹ️ SL {sl_pips:.1f} pips closer than requested - risk reduced")
            else:
                if actual_sl > requested.stop_loss:
                    warnings.append(f"⚠️ SL {sl_pips:.1f} pips FURTHER than requested - risk INCREASED")
                    validation.is_valid = False
                else:
                    warnings.append(f"ℹ️ SL {sl_pips:.1f} pips closer than requested - risk reduced")

    if requested.take_profit and actual_tp:
        validation.requested_tp = requested.take_profit
        validation.actual_tp = actual_tp
        tp_diff = abs(actual_tp - requested.take_profit)
        tp_pips = tp_diff / pip_size
        validation.tp_deviation_pips = tp_pips
        if tp_pips > max_sl_deviation_pips:
            warnings.append(f"ℹ️ TP differs by {tp_pips:.1f} pips from requested")

    if requested.volume and actual_volume:
        validation.requested_volume = requested.volume
        validation.actual_volume = actual_volume
        vol_diff = abs(actual_volume - requested.volume)
        vol_pct = (vol_diff / requested.volume) * 100
        validation.volume_deviation_percent = vol_pct
        if vol_pct > max_volume_deviation_percent:
            if actual_volume > requested.volume:
                warnings.append(f"⚠️ Volume {vol_pct:.1f}% LARGER than requested - risk INCREASED")
                validation.is_valid = False
            else:
                warnings.append(f"ℹ️ Volume {vol_pct:.1f}% smaller than requested")

    validation.warnings = warnings
    return validation


class BaseBroker(ABC):
    """Abstract base class for broker implementations"""

    def __init__(self, broker_id: str, config: dict):
        self.broker_id = broker_id
        self.config = config
        self.name = config.get("name", broker_id)
        self.is_demo = config.get("is_demo", True)
        self._connected = False
        self._account_info: Optional[AccountInfo] = None
        self._symbols_cache: Dict[str, SymbolInfo] = {}

    @property
    def is_connected(self) -> bool:
        return self._connected

    @abstractmethod
    async def connect(self) -> bool:
        pass

    @abstractmethod
    async def disconnect(self):
        pass

    @abstractmethod
    async def get_account_info(self) -> Optional[AccountInfo]:
        pass

    @abstractmethod
    async def get_symbols(self) -> List[SymbolInfo]:
        pass

    @abstractmethod
    async def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        pass

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResult:
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> OrderResult:
        pass

    @abstractmethod
    async def get_pending_orders(self) -> List[PendingOrder]:
        pass

    @abstractmethod
    async def get_positions(self) -> List[Position]:
        pass

    async def amend_position_sltp(
        self, position_id: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> OrderResult:
        """Modify SL/TP on an open position. Override in subclass."""
        return OrderResult(success=False, message="Not implemented")

    async def close_position(
        self, position_id: str, volume: Optional[float] = None
    ) -> OrderResult:
        """Close a position (full or partial). Override in subclass."""
        return OrderResult(success=False, message="Not implemented")

    async def get_history(
        self,
        symbol: str,
        timeframe: str = "H1",
        count: int = 250,
    ) -> List[dict]:
        """
        Retourne l'historique OHLCV d'un symbole.

        Chaque dict a les clés : ts (int, Unix s), open, high, low, close, volume.
        Trié par ts croissant (le plus ancien en premier).

        L'implémentation par défaut retourne [] — les brokers qui ne servent
        pas de données historiques (ex: TradeLocker) peuvent garder ce défaut.
        Les brokers source de données (ex: cTrader) doivent implémenter cette méthode.

        Args:
            symbol:    Nom unifié du symbole (ex: 'EURUSD', 'XAUUSD')
            timeframe: Timeframe sous forme de chaîne : 'M1','M5','M15','M30',
                       'H1','H4','D1','W1','MN1'. Défaut : 'H1'.
            count:     Nombre de barres à récupérer. Défaut : 250
                       (suffisant pour EMA200 + warmup).

        Returns:
            list[dict] avec clés ts/open/high/low/close/volume,
            ou [] si non disponible / erreur.
        """
        return []

    async def get_fresh_quote(
        self, symbol: str, quote_type: str
    ) -> Optional['FreshQuote']:
        """Quote fraîche obtenue indépendamment du stream PriceFeed.

        Contrat (Phase 2.5) :
        - INDÉPENDANT du cache de ticks alimenté par le stream du broker.
          Si le stream est mort, cette méthode doit rester opérationnelle
          (sinon elle ne sert à rien comme backup).
        - PAS de fallback silencieux : si l'appel échoue ou ne retourne
          rien dans la fenêtre, retourner None plutôt qu'un cache stale.
        - Le caller (boucle de polling BE) inspecte ``market_ts`` (ou
          ``observed_at`` à défaut) pour décider si la quote est encore
          fraîche ; il SKIP si stale > seuil (5 min par défaut).

        Args:
            symbol     : nom unifié (EURUSD, XAUUSD…)
            quote_type : "bid" (pour LONG) ou "ask" (pour SHORT)

        Returns:
            FreshQuote ou None. None signifie : pas de quote disponible
            via ce canal — le polling abandonne ce cycle pour cette position.

        Default : None (broker qui ne supporte pas un canal alternatif).
        """
        return None

    async def get_closed_position_detail(
        self, position_id: str
    ) -> Optional[dict]:
        """Retrieve fill details for a recently closed position.

        Returns a dict with keys: exit_price, exit_time, gross_profit, commission
        or None if not available / not implemented.
        """
        return None

    def map_symbol(self, unified_symbol: str) -> Optional[str]:
        mapping = self.config.get("instruments_mapping", {})
        return mapping.get(unified_symbol)

    def reverse_map_symbol(self, broker_symbol: str) -> Optional[str]:
        mapping = self.config.get("instruments_mapping", {})
        for unified, broker in mapping.items():
            if str(broker) == str(broker_symbol):
                return unified
        return None

    def calculate_lot_size(
        self,
        account_balance: float,
        risk_percent: float,
        stop_loss_pips: float,
        symbol_info: SymbolInfo
    ) -> float:
        risk_amount = account_balance * (risk_percent / 100)
        pip_value_per_lot = symbol_info.lot_size * symbol_info.pip_size
        lots = risk_amount / (stop_loss_pips * pip_value_per_lot)
        lots = max(symbol_info.min_volume, min(lots, symbol_info.max_volume))
        lots = round(lots / symbol_info.volume_step) * symbol_info.volume_step
        return round(lots, 2)

    def __repr__(self):
        status = "connected" if self._connected else "disconnected"
        return f"<{self.__class__.__name__} {self.name} ({status})>"
