#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cTrader Open API broker implementation.
Price feed (ticks) + historical bars (trendbars) + order placement.
"""

import asyncio
import time
import requests
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Callable
from concurrent.futures import Future
import threading

from .base import (
    BaseBroker, OrderRequest, OrderResult, OrderSide, OrderType, OrderStatus,
    Position, PendingOrder, AccountInfo, SymbolInfo, PriceTick,
)

try:
    from twisted.internet import reactor, threads
    from twisted.internet.defer import Deferred
    from ctrader_open_api import Client, TcpProtocol, EndPoints, Protobuf
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAApplicationAuthReq,
        ProtoOAAccountAuthReq,
        ProtoOAGetAccountListByAccessTokenReq,
        ProtoOASymbolsListReq,
        ProtoOANewOrderReq,
        ProtoOACancelOrderReq,
        ProtoOAReconcileReq,
        ProtoOATraderReq,
        ProtoOAErrorRes,
        ProtoOAAssetListReq,
        ProtoOASubscribeSpotsReq,
        ProtoOAUnsubscribeSpotsReq,
        ProtoOAGetTrendbarsReq,
    )
    from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
        ProtoOATrendbarPeriod,
    )
    CTRADER_AVAILABLE = True
except ImportError:
    CTRADER_AVAILABLE = False
    print("⚠️  ctrader-open-api not installed. cTrader support disabled.")


# Mapping timeframe string → ProtoOATrendbarPeriod enum value
# Utilisé par get_history()
_TIMEFRAME_MAP = {
    "M1":  1,   # ProtoOATrendbarPeriod.M1
    "M2":  2,
    "M3":  3,
    "M4":  4,
    "M5":  5,   # ProtoOATrendbarPeriod.M5
    "M10": 6,
    "M15": 7,   # ProtoOATrendbarPeriod.M15
    "M30": 8,   # ProtoOATrendbarPeriod.M30
    "H1":  9,   # ProtoOATrendbarPeriod.H1
    "H4":  10,  # ProtoOATrendbarPeriod.H4
    "H12": 11,
    "D1":  12,  # ProtoOATrendbarPeriod.D1
    "W1":  13,  # ProtoOATrendbarPeriod.W1
    "MN1": 14,  # ProtoOATrendbarPeriod.MN1
}


class CTraderBroker(BaseBroker):
    """cTrader Open API broker implementation with price feed + history support."""

    def __init__(self, broker_id: str, config: dict):
        super().__init__(broker_id, config)

        if not CTRADER_AVAILABLE:
            raise ImportError("ctrader-open-api is required for cTrader support")

        self.client_id = config.get("client_id", "")
        self.client_secret = config.get("client_secret", "")
        self.access_token = config.get("access_token", "")
        self.refresh_token = config.get("refresh_token", "")

        acc_id = config.get("account_id")
        self.account_id = int(acc_id) if acc_id else None

        self.is_demo = config.get("is_demo", True)
        self.host = EndPoints.PROTOBUF_DEMO_HOST if self.is_demo else EndPoints.PROTOBUF_LIVE_HOST
        self.port = EndPoints.PROTOBUF_PORT

        self._client: Optional[Client] = None
        self._pending_requests: Dict[str, Future] = {}
        self._symbols: Dict[int, SymbolInfo] = {}
        self._message_handlers: Dict[str, Callable] = {}

        self._reactor_thread: Optional[threading.Thread] = None
        self._reactor_running = False
        self._token_refreshed = False

        # Price feed
        self._price_ticks: Dict[int, PriceTick] = {}
        self._spot_callbacks: Dict[int, List[Callable]] = {}
        self._subscribed_symbol_ids: set = set()

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _should_refresh_token(self) -> bool:
        if not self.refresh_token:
            return False
        if not self.config.get("auto_refresh_token", True):
            return False
        if self._token_refreshed:
            return False
        return True

    def _ensure_reactor_running(self):
        if self._reactor_running:
            return

        def run_reactor():
            from twisted.internet import reactor
            if not reactor.running:
                reactor.run(installSignalHandlers=False)

        self._reactor_thread = threading.Thread(target=run_reactor, daemon=True)
        self._reactor_thread.start()
        self._reactor_running = True
        time.sleep(0.5)

    def _refresh_access_token(self) -> bool:
        if not self.refresh_token:
            print("[cTrader] ⚠️  No refresh token available")
            return False

        old_access_token = self.access_token
        old_refresh_token = self.refresh_token
        token_url = "https://openapi.ctrader.com/apps/token"
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }

        try:
            print("[cTrader] Refreshing access token...")
            response = requests.post(token_url, data=payload, timeout=15)
            if response.status_code == 200:
                data = response.json()
                if not data:
                    print("[cTrader] ❌ Empty response from token endpoint")
                    return False
                new_access = data.get("accessToken") or data.get("access_token")
                new_refresh = data.get("refreshToken") or data.get("refresh_token")
                if not new_access:
                    print("[cTrader] ❌ No access token in response")
                    return False
                self.access_token = new_access
                if new_refresh:
                    self.refresh_token = new_refresh
                print(f"[cTrader] ✅ Token refreshed successfully")
                self._save_tokens_to_config()
                return True
            else:
                print(f"[cTrader] ❌ Token refresh failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"[cTrader] ❌ Token refresh error: {e}")
            self.access_token = old_access_token
            self.refresh_token = old_refresh_token
            return False

    def _save_tokens_to_config(self):
        try:
            from arabesque.config import update_broker_tokens
            update_broker_tokens(
                broker_id=self.broker_id,
                access_token=self.access_token,
                refresh_token=self.refresh_token
            )
            print(f"[cTrader] 💾 Tokens saved to config")
        except Exception as e:
            print(f"[cTrader] ⚠️  Could not save tokens: {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _enum_value(self, message_obj, field_name: str, wanted: str) -> int:
        field = message_obj.DESCRIPTOR.fields_by_name[field_name]
        if field.enum_type is None:
            raise ValueError(f"Field {field_name} is not an enum")
        wanted_u = wanted.upper()
        values = list(field.enum_type.values)
        for v in values:
            if v.name.upper() == wanted_u:
                return v.number
        for v in values:
            name_u = v.name.upper()
            if name_u.endswith("_" + wanted_u) or name_u.endswith(wanted_u) or wanted_u in name_u:
                return v.number
        available = ", ".join([f"{v.name}={v.number}" for v in values])
        raise ValueError(f"Enum not found for {field_name}={wanted}. Available: {available}")

    def _symbol_id_for_name(self, name: str) -> Optional[int]:
        """Retourne le symbolId cTrader pour un nom de symbole."""
        for sid, sinfo in self._symbols.items():
            if sinfo.symbol == name or sinfo.broker_symbol == str(sid):
                return sid
        return None

    @staticmethod
    def _decode_trendbar(tb, divisor: float) -> dict:
        """
        Décode un ProtoOATrendbar en dict OHLCV.

        Dans le protocole cTrader :
          - open  : valeur absolue (déjà correcte)
          - high  = open + deltaHigh  (delta positif)
          - low   = open + deltaLow   (delta négatif)
          - close = open + deltaClose (delta, positif ou négatif)

        Tous les prix sont en unités entières, divisor = 10^pipPosition.
        """
        ts    = tb.utcTimestampInMinutes * 60
        open_ = tb.open / divisor
        high  = (tb.open + tb.high)       / divisor if tb.high   else open_
        low   = (tb.open + tb.low)        / divisor if tb.low    else open_
        close = (tb.open + tb.deltaClose) / divisor if hasattr(tb, "deltaClose") else open_
        vol   = tb.volume if hasattr(tb, "volume") else 0
        return {
            "ts":     ts,
            "open":   open_,
            "high":   high,
            "low":    low,
            "close":  close,
            "volume": vol,
        }

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        if self._should_refresh_token():
            if self._refresh_access_token():
                self._token_refreshed = True

        self._ensure_reactor_running()
        self._client = Client(self.host, self.port, TcpProtocol)

        connect_future = asyncio.get_event_loop().create_future()

        def on_connected(client):
            print(f"[cTrader] Connected to {self.host}:{self.port}")
            req = ProtoOAApplicationAuthReq()
            req.clientId = self.client_id
            req.clientSecret = self.client_secret
            client.send(req)

        def on_message(client, message):
            payload = Protobuf.extract(message)
            ptype = payload.DESCRIPTOR.name

            if isinstance(payload, ProtoOAErrorRes):
                error_msg = f"cTrader Error: {payload.errorCode} - {payload.description}"
                print(f"[cTrader] ❌ {error_msg}")
                if not connect_future.done():
                    connect_future.set_exception(Exception(error_msg))
                return

            if ptype == "ProtoOAApplicationAuthRes":
                print("[cTrader] ✅ Application authenticated")
                if self.account_id:
                    req = ProtoOAAccountAuthReq()
                    req.ctidTraderAccountId = self.account_id
                    req.accessToken = self.access_token
                    client.send(req)
                else:
                    req = ProtoOAGetAccountListByAccessTokenReq()
                    req.accessToken = self.access_token
                    client.send(req)

            elif ptype == "ProtoOAGetAccountListByAccessTokenRes":
                accounts = list(payload.ctidTraderAccount)
                if not accounts:
                    if not connect_future.done():
                        connect_future.set_exception(Exception("No accounts found"))
                    return
                self.account_id = accounts[0].ctidTraderAccountId
                print(f"[cTrader] Found {len(accounts)} account(s), using: {self.account_id}")
                req = ProtoOAAccountAuthReq()
                req.ctidTraderAccountId = self.account_id
                req.accessToken = self.access_token
                client.send(req)

            elif ptype == "ProtoOAAccountAuthRes":
                print(f"[cTrader] ✅ Account {self.account_id} authenticated")
                self._connected = True
                if not connect_future.done():
                    connect_future.set_result(True)

            elif ptype == "ProtoOASymbolsListRes":
                self._process_symbols_response(payload)
                if "symbols" in self._pending_requests:
                    self._pending_requests["symbols"].set_result(list(self._symbols.values()))

            elif ptype == "ProtoOATraderRes":
                self._process_trader_response(payload)
                if "account_info" in self._pending_requests:
                    self._pending_requests["account_info"].set_result(self._account_info)

            elif ptype == "ProtoOAReconcileRes":
                self._process_reconcile_response(payload)
                if "reconcile" in self._pending_requests:
                    self._pending_requests["reconcile"].set_result(payload)

            elif ptype == "ProtoOAGetTrendbarsRes":
                # Réponse historique — dispatcher par symbolId
                self._process_trendbar_response(payload)

            elif ptype == "ProtoOASpotEvent":
                self._process_spot_event(payload)

            elif "Order" in ptype or "Execution" in ptype:
                self._process_order_response(payload, ptype)

        self._client.setConnectedCallback(on_connected)
        self._client.setMessageReceivedCallback(on_message)

        from twisted.internet import reactor
        reactor.callFromThread(self._client.startService)

        try:
            await asyncio.wait_for(connect_future, timeout=30)
            return True
        except asyncio.TimeoutError:
            print("[cTrader] ❌ Connection timeout")
            return False
        except Exception as e:
            print(f"[cTrader] ❌ Connection error: {e}")
            return False

    async def disconnect(self):
        if self._subscribed_symbol_ids and self._client:
            req = ProtoOAUnsubscribeSpotsReq()
            req.ctidTraderAccountId = self.account_id
            for sid in list(self._subscribed_symbol_ids):
                req.symbolId.append(sid)
            from twisted.internet import reactor
            reactor.callFromThread(self._client.send, req)
            await asyncio.sleep(0.3)
        if self._client:
            from twisted.internet import reactor
            reactor.callFromThread(self._client.stopService)
        self._connected = False

    # ------------------------------------------------------------------
    # Historical bars
    # ------------------------------------------------------------------

    async def get_history(
        self,
        symbol: str,
        timeframe: str = "H1",
        count: int = 250,
    ) -> List[dict]:
        """
        Récupère count barres OHLCV depuis cTrader (ProtoOAGetTrendbarsReq).

        Args:
            symbol:    Nom unifié du symbole (ex: 'EURUSD', 'XAUUSD')
            timeframe: 'M1','M5','M15','M30','H1','H4','D1','W1','MN1'
            count:     Nombre de barres (max 5000 par requête cTrader)

        Returns:
            list[dict] triée par ts croissant,
            chaque dict = {ts, open, high, low, close, volume}
        """
        if not self._connected:
            print(f"[cTrader] get_history({symbol}): non connecté")
            return []

        # Résolution du symbole
        if not self._symbols:
            await self.get_symbols()

        symbol_id = self._symbol_id_for_name(symbol)
        if symbol_id is None:
            broker_sym = self.map_symbol(symbol)
            if broker_sym:
                try:
                    symbol_id = int(broker_sym)
                except ValueError:
                    symbol_id = self._symbol_id_for_name(broker_sym)

        if symbol_id is None:
            print(f"[cTrader] get_history: symbole '{symbol}' non trouvé")
            return []

        # Résolution du diviseur de prix (pipPosition)
        sym_info = self._symbols.get(symbol_id)
        if sym_info:
            # pipPosition est stocké implicitement via pip_size
            import math
            pip_pos = -int(round(math.log10(sym_info.pip_size)))
            divisor = 10 ** (pip_pos + 1)  # digits = pipPosition + 1
        else:
            divisor = 100000  # défaut 5 décimales

        # Résolution du timeframe
        tf_upper = timeframe.upper()
        period = _TIMEFRAME_MAP.get(tf_upper)
        if period is None:
            print(f"[cTrader] get_history: timeframe '{timeframe}' inconnu, utilisation H1")
            period = _TIMEFRAME_MAP["H1"]

        # Clé unique pour cette requête
        req_key = f"history_{symbol_id}_{tf_upper}"

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests[req_key] = future

        req = ProtoOAGetTrendbarsReq()
        req.ctidTraderAccountId = self.account_id
        req.symbolId = symbol_id
        req.period = period
        req.count = min(count, 5000)

        from twisted.internet import reactor
        reactor.callFromThread(self._client.send, req)

        try:
            bars = await asyncio.wait_for(future, timeout=20.0)
            print(
                f"[cTrader] 📊 get_history({symbol}, {timeframe}): "
                f"{len(bars)} barres chargées"
            )
            return bars
        except asyncio.TimeoutError:
            print(f"[cTrader] ⏱ get_history({symbol}, {timeframe}): timeout")
            self._pending_requests.pop(req_key, None)
            return []
        except Exception as e:
            print(f"[cTrader] ❌ get_history({symbol}): {e}")
            self._pending_requests.pop(req_key, None)
            return []

    def _process_trendbar_response(self, payload):
        """
        Traite un ProtoOAGetTrendbarsRes.
        Résout la Future correspondante via la clé history_{symbolId}_{period}.
        """
        symbol_id = payload.symbolId
        period = payload.period

        # Chercher la clé correspondante dans les requêtes en attente
        # (on cherche par préfixe + symbolId)
        matching_key = None
        for key in list(self._pending_requests.keys()):
            if key.startswith(f"history_{symbol_id}_"):
                matching_key = key
                break

        if not matching_key:
            # Réponse non attendue, ignorer
            return

        sym_info = self._symbols.get(symbol_id)
        if sym_info:
            import math
            pip_pos = -int(round(math.log10(sym_info.pip_size)))
            divisor = 10 ** (pip_pos + 1)
        else:
            divisor = 100000

        bars = [
            self._decode_trendbar(tb, divisor)
            for tb in payload.trendbar
        ]
        bars.sort(key=lambda b: b["ts"])

        future = self._pending_requests.pop(matching_key, None)
        if future and not future.done():
            future.set_result(bars)

    # ------------------------------------------------------------------
    # Price feed (spots)
    # ------------------------------------------------------------------

    async def subscribe_spots(self, symbol: str, callback: Callable) -> bool:
        """
        Souscrire aux ticks de prix pour un symbole.
        Le callback reçoit un objet PriceTick à chaque tick.
        """
        if not self._connected:
            return False
        if not self._symbols:
            await self.get_symbols()

        symbol_id = self._symbol_id_for_name(symbol)
        broker_sym = self.map_symbol(symbol)
        if symbol_id is None and broker_sym:
            symbol_id = self._symbol_id_for_name(broker_sym)
        if symbol_id is None:
            print(f"[cTrader] ⚠️ subscribe_spots: symbol {symbol} not found")
            return False

        if symbol_id not in self._spot_callbacks:
            self._spot_callbacks[symbol_id] = []
        self._spot_callbacks[symbol_id].append(callback)

        if symbol_id not in self._subscribed_symbol_ids:
            req = ProtoOASubscribeSpotsReq()
            req.ctidTraderAccountId = self.account_id
            req.symbolId.append(symbol_id)
            from twisted.internet import reactor
            reactor.callFromThread(self._client.send, req)
            self._subscribed_symbol_ids.add(symbol_id)
            print(f"[cTrader] 📡 Subscribed to spots: {symbol} (ID {symbol_id})")

        return True

    async def unsubscribe_spots(self, symbol: str):
        if not self._connected or not self._client:
            return
        symbol_id = self._symbol_id_for_name(symbol)
        if symbol_id is None:
            return
        self._spot_callbacks.pop(symbol_id, None)
        if symbol_id in self._subscribed_symbol_ids:
            req = ProtoOAUnsubscribeSpotsReq()
            req.ctidTraderAccountId = self.account_id
            req.symbolId.append(symbol_id)
            from twisted.internet import reactor
            reactor.callFromThread(self._client.send, req)
            self._subscribed_symbol_ids.discard(symbol_id)
            print(f"[cTrader] 🔕 Unsubscribed from spots: {symbol}")

    def get_last_tick(self, symbol: str) -> Optional[PriceTick]:
        symbol_id = self._symbol_id_for_name(symbol)
        if symbol_id is None:
            return None
        return self._price_ticks.get(symbol_id)

    def _process_spot_event(self, payload):
        """Traite un ProtoOASpotEvent reçu du serveur."""
        symbol_id = payload.symbolId
        divisor = 100000
        bid = getattr(payload, 'bid', None)
        ask = getattr(payload, 'ask', None)
        if bid is None or ask is None:
            return
        bid_f = bid / divisor
        ask_f = ask / divisor
        sym_info = self._symbols.get(symbol_id)
        sym_name = sym_info.symbol if sym_info else str(symbol_id)
        tick = PriceTick(
            symbol=sym_name,
            bid=bid_f,
            ask=ask_f,
            timestamp=datetime.now(timezone.utc),
        )
        self._price_ticks[symbol_id] = tick
        loop = asyncio.get_event_loop()
        for cb in self._spot_callbacks.get(symbol_id, []):
            try:
                if asyncio.iscoroutinefunction(cb):
                    loop.call_soon_threadsafe(lambda c=cb, t=tick: asyncio.ensure_future(c(t)))
                else:
                    loop.call_soon_threadsafe(cb, tick)
            except Exception as e:
                print(f"[cTrader] ⚠️ Spot callback error: {e}")

    # ------------------------------------------------------------------
    # Symbols
    # ------------------------------------------------------------------

    def _process_symbols_response(self, payload):
        for s in payload.symbol:
            symbol_id = s.symbolId
            symbol_name = getattr(s, "symbolName", f"ID:{symbol_id}")
            digits = getattr(s, "digits", 5)
            pip_position = getattr(s, "pipPosition", digits - 1)
            tick_size = 10 ** (-digits)
            pip_size = 10 ** (-pip_position) if pip_position > 0 else tick_size
            min_volume = getattr(s, "minVolume", 1000) / 100
            max_volume = getattr(s, "maxVolume", 10000000) / 100
            step_volume = getattr(s, "stepVolume", 1000) / 100
            self._symbols[symbol_id] = SymbolInfo(
                symbol=symbol_name,
                broker_symbol=str(symbol_id),
                description=getattr(s, "description", ""),
                digits=digits,
                tick_size=tick_size,
                pip_size=pip_size,
                min_volume=min_volume,
                max_volume=max_volume,
                volume_step=step_volume,
                lot_size=100000,
                is_tradable=True
            )

    def _process_trader_response(self, payload):
        trader = payload.trader
        self._account_info = AccountInfo(
            account_id=str(self.account_id),
            broker_name=self.name,
            balance=trader.balance / 100,
            equity=trader.balance / 100,
            margin_used=getattr(trader, "usedMargin", 0) / 100,
            currency=getattr(trader, "depositAssetId", "USD"),
            leverage=getattr(trader, "leverageInCents", 10000) // 100,
            is_demo=self.is_demo
        )

    def _process_reconcile_response(self, payload):
        self._positions = []
        self._pending_orders = []
        for pos in payload.position:
            side = OrderSide.BUY if pos.tradeData.tradeSide == 1 else OrderSide.SELL
            self._positions.append(Position(
                position_id=str(pos.positionId),
                symbol=self.reverse_map_symbol(pos.tradeData.symbolId) or str(pos.tradeData.symbolId),
                side=side,
                volume=pos.tradeData.volume / 100,
                entry_price=pos.price,
                stop_loss=getattr(pos, "stopLoss", None),
                take_profit=getattr(pos, "takeProfit", None),
            ))
        for order in payload.order:
            side = OrderSide.BUY if order.tradeData.tradeSide == 1 else OrderSide.SELL
            order_type = OrderType.LIMIT if order.orderType == 1 else OrderType.STOP
            self._pending_orders.append(PendingOrder(
                order_id=str(order.orderId),
                symbol=self.reverse_map_symbol(order.tradeData.symbolId) or str(order.tradeData.symbolId),
                side=side,
                order_type=order_type,
                volume=order.tradeData.volume / 100,
                entry_price=getattr(order, "limitPrice", getattr(order, "stopPrice", 0)),
                stop_loss=getattr(order, "stopLoss", None),
                take_profit=getattr(order, "takeProfit", None),
                created_time=datetime.fromtimestamp(order.tradeData.openTimestamp / 1000, tz=timezone.utc),
                label=getattr(order, "label", ""),
                comment=getattr(order, "comment", ""),
                broker_id=self.broker_id,
            ))

    def _process_order_response(self, payload, ptype: str):
        print(f"[cTrader] DEBUG: Received {ptype}")
        if "order_place" in self._pending_requests:
            future = self._pending_requests.pop("order_place")
            if ptype == "ProtoOAOrderErrorEvent" or "Error" in ptype:
                error_code = getattr(payload, "errorCode", "UNKNOWN")
                description = getattr(payload, "description", "No description")
                future.set_result(OrderResult(
                    success=False,
                    message=f"Order rejected: {error_code} - {description}",
                    broker_response=payload
                ))
                return
            order_id = None
            if hasattr(payload, "order") and hasattr(payload.order, "orderId"):
                order_id = payload.order.orderId
            if not order_id and hasattr(payload, "orderId"):
                order_id = payload.orderId
            if not order_id and hasattr(payload, "position"):
                if hasattr(payload.position, "positionId"):
                    order_id = payload.position.positionId
            if order_id and order_id != 0:
                future.set_result(OrderResult(
                    success=True,
                    order_id=str(order_id),
                    message="Order placed successfully",
                    broker_response=payload
                ))
            else:
                future.set_result(OrderResult(
                    success=True,
                    order_id="unknown",
                    message=f"Response: {ptype}",
                    broker_response=payload
                ))
        if "order_cancel" in self._pending_requests:
            future = self._pending_requests.pop("order_cancel")
            future.set_result(OrderResult(
                success=True,
                message="Order cancelled",
                broker_response=payload
            ))

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def get_account_info(self) -> Optional[AccountInfo]:
        if not self._connected:
            return None
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests["account_info"] = future
        req = ProtoOATraderReq()
        req.ctidTraderAccountId = self.account_id
        from twisted.internet import reactor
        reactor.callFromThread(self._client.send, req)
        try:
            return await asyncio.wait_for(future, timeout=10)
        except asyncio.TimeoutError:
            return None

    async def get_symbols(self) -> List[SymbolInfo]:
        if not self._connected:
            return []
        if self._symbols:
            return list(self._symbols.values())
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests["symbols"] = future
        req = ProtoOASymbolsListReq()
        req.ctidTraderAccountId = self.account_id
        from twisted.internet import reactor
        reactor.callFromThread(self._client.send, req)
        try:
            return await asyncio.wait_for(future, timeout=15)
        except asyncio.TimeoutError:
            return []

    async def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        if not self._symbols:
            await self.get_symbols()
        for s in self._symbols.values():
            if s.symbol == symbol or s.broker_symbol == symbol:
                return s
        broker_symbol = self.map_symbol(symbol)
        if broker_symbol and int(broker_symbol) in self._symbols:
            return self._symbols[int(broker_symbol)]
        return None

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def place_order(self, order: OrderRequest) -> OrderResult:
        if not self._connected:
            return OrderResult(success=False, message="Not connected")

        broker_symbol = order.broker_symbol or self.map_symbol(order.symbol)
        if not broker_symbol:
            return OrderResult(success=False, message=f"Symbol {order.symbol} not mapped for cTrader")

        symbol_id = None
        try:
            symbol_id = int(broker_symbol)
        except ValueError:
            symbol_info = await self.get_symbol_info(broker_symbol)
            if symbol_info:
                symbol_id = int(symbol_info.broker_symbol)
            else:
                return OrderResult(success=False, message=f"Symbol {broker_symbol} not found in cTrader")

        if not symbol_id:
            return OrderResult(success=False, message=f"Could not resolve symbol ID for {broker_symbol}")

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests["order_place"] = future

        try:
            req = ProtoOANewOrderReq()
            req.ctidTraderAccountId = self.account_id
            req.symbolId = symbol_id
            if order.order_type == OrderType.MARKET:
                req.orderType = self._enum_value(req, "orderType", "MARKET")
            elif order.order_type == OrderType.LIMIT:
                req.orderType = self._enum_value(req, "orderType", "LIMIT")
                if order.entry_price:
                    req.limitPrice = order.entry_price
            elif order.order_type == OrderType.STOP:
                req.orderType = self._enum_value(req, "orderType", "STOP")
                if order.entry_price:
                    req.stopPrice = order.entry_price
            req.tradeSide = self._enum_value(req, "tradeSide", order.side.value)
            volume_multiplier = 10000000
            broker_volume = order.broker_volume or int(order.volume * volume_multiplier)
            req.volume = broker_volume
            if order.stop_loss:
                req.stopLoss = order.stop_loss
            if order.take_profit:
                req.takeProfit = order.take_profit
            if order.expiry_timestamp_ms:
                req.timeInForce = self._enum_value(req, "timeInForce", "GOOD_TILL_DATE")
                req.expirationTimestamp = order.expiry_timestamp_ms
            else:
                req.timeInForce = self._enum_value(req, "timeInForce", "GTC")
            if order.label:
                req.label = order.label[:50]
            if order.comment:
                req.comment = order.comment[:100]
            print(f"[cTrader] Placing {order.order_type.value} {order.side.value} "
                  f"{order.volume} lots on {order.symbol} @ {order.entry_price}")
            from twisted.internet import reactor
            reactor.callFromThread(self._client.send, req)
            result = await asyncio.wait_for(future, timeout=30)
            return result
        except asyncio.TimeoutError:
            return OrderResult(success=False, message="Order timeout")
        except Exception as e:
            return OrderResult(success=False, message=str(e))

    async def cancel_order(self, order_id: str) -> OrderResult:
        if not self._connected:
            return OrderResult(success=False, message="Not connected")
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests["order_cancel"] = future
        req = ProtoOACancelOrderReq()
        req.ctidTraderAccountId = self.account_id
        req.orderId = int(order_id)
        from twisted.internet import reactor
        reactor.callFromThread(self._client.send, req)
        try:
            return await asyncio.wait_for(future, timeout=15)
        except asyncio.TimeoutError:
            return OrderResult(success=False, message="Cancel timeout")

    async def get_pending_orders(self) -> List[PendingOrder]:
        if not self._connected:
            return []
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests["reconcile"] = future
        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = self.account_id
        from twisted.internet import reactor
        reactor.callFromThread(self._client.send, req)
        try:
            await asyncio.wait_for(future, timeout=15)
            return self._pending_orders
        except asyncio.TimeoutError:
            return []

    async def get_positions(self) -> List[Position]:
        await self.get_pending_orders()
        return self._positions


# =============================================================================
# Synchronous wrapper
# =============================================================================

class CTraderBrokerSync:
    """Wrapper synchrone pour CTraderBroker (usage CLI/scripts)."""

    def __init__(self, broker_id: str, config: dict):
        self.broker = CTraderBroker(broker_id, config)
        self._loop = None

    def _get_loop(self):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop

    def connect(self) -> bool:
        return self._get_loop().run_until_complete(self.broker.connect())

    def disconnect(self):
        self._get_loop().run_until_complete(self.broker.disconnect())

    def get_account_info(self) -> Optional[AccountInfo]:
        return self._get_loop().run_until_complete(self.broker.get_account_info())

    def get_symbols(self) -> List[SymbolInfo]:
        return self._get_loop().run_until_complete(self.broker.get_symbols())

    def get_history(
        self,
        symbol: str,
        timeframe: str = "H1",
        count: int = 250,
    ) -> List[dict]:
        return self._get_loop().run_until_complete(
            self.broker.get_history(symbol, timeframe, count)
        )

    def place_order(self, order: OrderRequest) -> OrderResult:
        return self._get_loop().run_until_complete(self.broker.place_order(order))

    def cancel_order(self, order_id: str) -> OrderResult:
        return self._get_loop().run_until_complete(self.broker.cancel_order(order_id))

    def get_pending_orders(self) -> List[PendingOrder]:
        return self._get_loop().run_until_complete(self.broker.get_pending_orders())

    def get_positions(self) -> List[Position]:
        return self._get_loop().run_until_complete(self.broker.get_positions())

    def get_last_tick(self, symbol: str) -> Optional[PriceTick]:
        return self.broker.get_last_tick(symbol)
