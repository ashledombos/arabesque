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
        ProtoOAAmendPositionSLTPReq,
        ProtoOAClosePositionReq,
        ProtoOAReconcileReq,
        ProtoOATraderReq,
        ProtoOAErrorRes,
        ProtoOAAssetListReq,
        ProtoOASubscribeSpotsReq,
        ProtoOAUnsubscribeSpotsReq,
        ProtoOAGetTrendbarsReq,
    )
    CTRADER_AVAILABLE = True
except ImportError as e:
    CTRADER_AVAILABLE = False
    print(f"⚠️  ctrader-open-api import failed: {e}")
    import traceback
    traceback.print_exc()


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

# Durée en secondes de chaque timeframe — utilisé pour calculer fromTimestamp
_TIMEFRAME_SECONDS = {
    "M1":  60,
    "M2":  120,
    "M3":  180,
    "M4":  240,
    "M5":  300,
    "M10": 600,
    "M15": 900,
    "M30": 1800,
    "H1":  3600,
    "H4":  14400,
    "H12": 43200,
    "D1":  86400,
    "W1":  604800,
    "MN1": 2592000,   # ~30 jours
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

        # Lock pour empêcher les appels concurrents à get_symbols()
        self._symbols_lock = asyncio.Lock()
        # Référence au loop asyncio pour les callbacks thread-safe depuis Twisted
        self._asyncio_loop: Optional[asyncio.AbstractEventLoop] = None

        # Price feed
        self._price_ticks: Dict[int, PriceTick] = {}
        self._spot_callbacks: Dict[int, List[Callable]] = {}
        self._subscribed_symbol_ids: set = set()
        # Mapping symbolId → nom unifié (EURUSD) pour que tick.symbol soit cohérent
        self._symbol_id_to_unified: Dict[int, str] = {}
        # Diagnostic : log du premier tick par symbole
        self._first_tick_logged: set = set()
        # Cache positions et mapping positionId → symbolId
        self._positions: List = []
        self._pending_orders: List = []
        self._position_symbol_ids: Dict[str, int] = {}

        # Diviseur de prix cTrader : les SpotEvents et Trendbars encodent les prix
        # en entiers. Le diviseur pour décoder est FIXE et ne dépend PAS de
        # pipPosition/digits (qui ne servent qu'à l'arrondi).
        # Sur cTrader, tous les prix sont encodés avec 10^5 de précision,
        # même pour les paires JPY (digits=3) ou crypto (digits=2).
        self._symbol_divisors: Dict[int, int] = {}
        self._DEFAULT_DIVISOR = 100000  # 10^5, standard cTrader

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
        """Retourne le symbolId cTrader pour un nom de symbole.

        Cherche une correspondance exacte, puis normalisée (sans / . - _),
        puis par ID numérique.
        """
        # 1) Recherche par nom exact (ex: "EURUSD" == symbolName cTrader)
        for sid, sinfo in self._symbols.items():
            if sinfo.symbol == name:
                return sid
        # 2) Recherche normalisée (ex: "EURUSD" vs "EUR/USD")
        name_norm = name.upper().replace("/", "").replace(".", "").replace("-", "").replace("_", "")
        for sid, sinfo in self._symbols.items():
            sym_norm = sinfo.symbol.upper().replace("/", "").replace(".", "").replace("-", "").replace("_", "")
            if sym_norm == name_norm:
                return sid
        # 3) Recherche par ID numérique passé en string (ex: "270")
        try:
            sid_int = int(name)
            if sid_int in self._symbols:
                return sid_int
        except ValueError:
            pass
        return None

    @staticmethod
    def _decode_trendbar(tb, divisor: float) -> dict:
        """
        Décode un ProtoOATrendbar en dict OHLCV.

        Structure proto cTrader (ProtoOATrendbar) :
          - low             : prix absolu le plus bas (int64)
          - deltaOpen       : open - low   (uint64, >= 0)
          - deltaHigh       : high - low   (uint64, >= 0)
          - deltaClose      : close - low  (uint64, >= 0)
          - utcTimestampInMinutes : timestamp en minutes UTC (uint32)
          - volume          : volume (uint64)

        Tous les prix sont en unités entières, divisor = 10^(pipPosition+1).
        """
        ts = tb.utcTimestampInMinutes * 60

        low_raw = getattr(tb, "low", 0) or 0
        low   = low_raw / divisor
        open_ = (low_raw + (getattr(tb, "deltaOpen", 0) or 0))  / divisor
        high  = (low_raw + (getattr(tb, "deltaHigh", 0) or 0))  / divisor
        close = (low_raw + (getattr(tb, "deltaClose", 0) or 0)) / divisor
        vol   = getattr(tb, "volume", 0) or 0

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

    def _resolve_future(self, future, value):
        """Thread-safe: résout un asyncio.Future depuis le thread Twisted."""
        if future.done():
            return
        if self._asyncio_loop and self._asyncio_loop.is_running():
            self._asyncio_loop.call_soon_threadsafe(
                lambda: future.set_result(value) if not future.done() else None
            )
        else:
            try:
                future.set_result(value)
            except asyncio.InvalidStateError:
                pass

    def _reject_future(self, future, exc):
        """Thread-safe: rejette un asyncio.Future depuis le thread Twisted."""
        if future.done():
            return
        if self._asyncio_loop and self._asyncio_loop.is_running():
            self._asyncio_loop.call_soon_threadsafe(
                lambda: future.set_exception(exc) if not future.done() else None
            )
        else:
            try:
                future.set_exception(exc)
            except asyncio.InvalidStateError:
                pass

    async def connect(self) -> bool:
        # Capturer le loop asyncio pour les callbacks thread-safe
        self._asyncio_loop = asyncio.get_event_loop()

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
                    self._reject_future(connect_future, Exception(error_msg))
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
                        self._reject_future(connect_future, Exception("No accounts found"))
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
                    self._resolve_future(connect_future, True)

            elif ptype == "ProtoOASymbolsListRes":
                self._process_symbols_response(payload)
                future = self._pending_requests.pop("symbols", None)
                if future and not future.done():
                    self._resolve_future(future, list(self._symbols.values()))

            elif ptype == "ProtoOASymbolByIdRes":
                self._process_symbol_details(payload)
                future = self._pending_requests.pop("symbol_details", None)
                if future and not future.done():
                    self._resolve_future(future, True)

            elif ptype == "ProtoOATraderRes":
                self._process_trader_response(payload)
                future = self._pending_requests.pop("account_info", None)
                if future and not future.done():
                    self._resolve_future(future, self._account_info)

            elif ptype == "ProtoOAReconcileRes":
                self._process_reconcile_response(payload)
                future = self._pending_requests.pop("reconcile", None)
                if future and not future.done():
                    self._resolve_future(future, payload)

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
            self._send_no_response(req)
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

        symbol_id = self._resolve_symbol_id(symbol)

        if symbol_id is None:
            print(f"[cTrader] get_history: symbole '{symbol}' non trouvé")
            return []

        # Diviseur de prix cTrader (fixe, indépendant de digits/pipPosition)
        divisor = self._get_divisor(symbol_id)

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

        # fromTimestamp et toTimestamp sont REQUIS par le proto cTrader
        # Calcul : toTimestamp = maintenant, fromTimestamp = maintenant - (count * durée)
        # On ajoute 50% de marge pour weekends/jours fériés sans cotation
        tf_seconds = _TIMEFRAME_SECONDS.get(tf_upper, 3600)
        now_ms = int(time.time() * 1000)
        span_ms = int(count * tf_seconds * 1.5 * 1000)
        req.fromTimestamp = now_ms - span_ms
        req.toTimestamp = now_ms

        from twisted.internet import reactor
        self._send_via_reactor(req)

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
        divisor = self._get_divisor(symbol_id)

        bars = [
            self._decode_trendbar(tb, divisor)
            for tb in payload.trendbar
        ]
        bars.sort(key=lambda b: b["ts"])

        future = self._pending_requests.pop(matching_key, None)
        if future and not future.done():
            self._resolve_future(future, bars)

    # ------------------------------------------------------------------
    # Price feed (spots)
    # ------------------------------------------------------------------

    def _send_no_response(self, req):
        """Envoie un message proto via Twisted sans attendre de réponse.

        Ajoute un errback sur le Deferred interne de la lib cTrader pour
        supprimer les TimeoutError sur les requêtes qui n'ont pas de réponse
        explicite (ex: SubscribeSpots, UnsubscribeSpots).
        """
        from twisted.internet import reactor

        def _do_send():
            d = self._client.send(req)
            if d and hasattr(d, 'addErrback'):
                d.addErrback(lambda failure: None)  # Suppress Deferred timeout

        reactor.callFromThread(_do_send)

    def _send_via_reactor(self, req):
        """Envoie un message proto via Twisted, en supprimant le timeout Deferred.

        La lib cTrader met un timeout de 5s sur le Deferred interne, mais nos
        requêtes attendent la réponse via un asyncio.Future avec un timeout plus long.
        Sans cette suppression, on obtient 'Unhandled error in Deferred: TimeoutError'.
        """
        from twisted.internet import reactor

        def _do_send():
            d = self._client.send(req)
            if d and hasattr(d, 'addErrback'):
                d.addErrback(lambda failure: None)

        reactor.callFromThread(_do_send)

    async def subscribe_spots(self, symbol: str, callback: Callable) -> bool:
        """
        Souscrire aux ticks de prix pour un symbole unique.
        Pour souscrire en masse, préférer subscribe_spots_batch().
        """
        if not self._connected:
            return False
        if not self._symbols:
            await self.get_symbols()

        symbol_id = self._resolve_symbol_id(symbol)
        if symbol_id is None:
            print(f"[cTrader] ⚠️ subscribe_spots: symbol {symbol} not found in {len(self._symbols)} symbols")
            return False

        # Enregistrer le mapping symbolId → nom unifié
        if symbol_id not in self._symbol_id_to_unified:
            self._symbol_id_to_unified[symbol_id] = symbol

        if symbol_id not in self._spot_callbacks:
            self._spot_callbacks[symbol_id] = []
        self._spot_callbacks[symbol_id].append(callback)

        if symbol_id not in self._subscribed_symbol_ids:
            req = ProtoOASubscribeSpotsReq()
            req.ctidTraderAccountId = self.account_id
            req.symbolId.append(symbol_id)
            self._send_no_response(req)
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
            self._send_no_response(req)
            self._subscribed_symbol_ids.discard(symbol_id)
            print(f"[cTrader] 🔕 Unsubscribed from spots: {symbol}")

    async def subscribe_spots_batch(
        self,
        symbols_and_callbacks: Dict[str, List[Callable]],
    ) -> Dict[str, bool]:
        """
        Souscrire aux ticks pour plusieurs symboles en une seule requête TCP.

        Paramètres:
            symbols_and_callbacks: {symbol_name: [callback1, callback2, ...]}

        Retourne:
            {symbol_name: True/False} selon si la souscription a réussi.
        """
        if not self._connected:
            return {s: False for s in symbols_and_callbacks}
        if not self._symbols:
            await self.get_symbols()

        result = {}
        new_symbol_ids = []

        for symbol, callbacks in symbols_and_callbacks.items():
            # Résolution du symbolId
            symbol_id = self._resolve_symbol_id(symbol)
            if symbol_id is None:
                # Chercher des noms proches pour aider au diagnostic
                name_norm = symbol.upper().replace("/", "").replace(".", "")
                suggestions = []
                for sid, sinfo in self._symbols.items():
                    sn = sinfo.symbol.upper().replace("/", "").replace(".", "")
                    # Chercher si le symbole est contenu dans le nom cTrader ou vice versa
                    if name_norm[:3] in sn and name_norm[-3:] in sn:
                        suggestions.append(f"{sinfo.symbol}(ID:{sid})")
                hint = f" — proches: {', '.join(suggestions[:3])}" if suggestions else ""
                print(
                    f"[cTrader] ⚠️ subscribe_spots_batch: "
                    f"symbol {symbol} not found{hint}"
                )
                result[symbol] = False
                continue

            # Enregistrer les callbacks
            if symbol_id not in self._spot_callbacks:
                self._spot_callbacks[symbol_id] = []
            for cb in callbacks:
                if cb not in self._spot_callbacks[symbol_id]:
                    self._spot_callbacks[symbol_id].append(cb)

            # Mapping symbolId → nom unifié
            if symbol_id not in self._symbol_id_to_unified:
                self._symbol_id_to_unified[symbol_id] = symbol

            # Collecter les IDs à souscrire
            if symbol_id not in self._subscribed_symbol_ids:
                new_symbol_ids.append(symbol_id)
                self._subscribed_symbol_ids.add(symbol_id)

            result[symbol] = True

        # Envoyer UNE SEULE requête avec tous les symbolIds
        if new_symbol_ids:
            req = ProtoOASubscribeSpotsReq()
            req.ctidTraderAccountId = self.account_id
            for sid in new_symbol_ids:
                req.symbolId.append(sid)
            self._send_no_response(req)
            print(
                f"[cTrader] 📡 Batch subscribed to {len(new_symbol_ids)} symbols "
                f"(IDs: {new_symbol_ids[:5]}{'...' if len(new_symbol_ids) > 5 else ''})"
            )

        return result

    def _resolve_symbol_id(self, symbol: str) -> Optional[int]:
        """Résout un nom de symbole en symbolId cTrader.

        Essaie dans l'ordre :
        1. _symbol_id_for_name(symbol) — correspondance directe
        2. map_symbol(symbol) → _symbol_id_for_name ou int()
        """
        symbol_id = self._symbol_id_for_name(symbol)
        if symbol_id is not None:
            return symbol_id
        broker_sym = self.map_symbol(symbol)
        if broker_sym:
            try:
                sid = int(broker_sym)
                if sid in self._symbols:
                    return sid
            except ValueError:
                return self._symbol_id_for_name(broker_sym)
        return None

    def get_last_tick(self, symbol: str) -> Optional[PriceTick]:
        symbol_id = self._resolve_symbol_id(symbol)
        if symbol_id is None:
            return None
        return self._price_ticks.get(symbol_id)

    def _process_spot_event(self, payload):
        """Traite un ProtoOASpotEvent reçu du serveur."""
        symbol_id = payload.symbolId
        sym_info = self._symbols.get(symbol_id)

        # Diviseur de prix cTrader (fixe, NE dépend PAS de digits/pipPosition)
        divisor = self._get_divisor(symbol_id)

        bid = getattr(payload, 'bid', 0)
        ask = getattr(payload, 'ask', 0)

        # cTrader envoie des SpotEvents incrémentaux : seul le champ modifié
        # est non-zéro. On garde le dernier prix connu pour l'autre.
        prev_tick = self._price_ticks.get(symbol_id)
        if bid == 0 and prev_tick:
            bid_f = prev_tick.bid
        else:
            bid_f = bid / divisor if bid else 0.0
        if ask == 0 and prev_tick:
            ask_f = prev_tick.ask
        else:
            ask_f = ask / divisor if ask else 0.0

        # Ignorer si aucun prix valide
        if bid_f <= 0 and ask_f <= 0:
            return

        # Utiliser le nom unifié (EURUSD) au lieu du nom cTrader (peut-être EUR/USD)
        # pour que PriceFeedManager puisse corréler les ticks avec ses symboles
        unified_name = self._symbol_id_to_unified.get(symbol_id)
        if unified_name is None:
            unified_name = sym_info.symbol if sym_info else str(symbol_id)

        tick = PriceTick(
            symbol=unified_name,
            bid=bid_f,
            ask=ask_f,
            timestamp=datetime.now(timezone.utc),
        )
        self._price_ticks[symbol_id] = tick

        # Diagnostic : log le premier tick de chaque symbole
        if symbol_id not in self._first_tick_logged:
            self._first_tick_logged.add(symbol_id)
            ct_name = sym_info.symbol if sym_info else "?"
            print(
                f"[cTrader] 🔔 Premier tick: {unified_name} "
                f"(cTrader: {ct_name}, ID:{symbol_id}) "
                f"bid={bid_f:.5f} ask={ask_f:.5f}"
            )

        # Dispatcher les callbacks vers le thread asyncio
        # (on est dans le thread Twisted, pas d'event loop asyncio ici)
        loop = self._asyncio_loop
        if not loop:
            return

        for cb in self._spot_callbacks.get(symbol_id, []):
            try:
                if asyncio.iscoroutinefunction(cb):
                    loop.call_soon_threadsafe(
                        lambda c=cb, t=tick: asyncio.ensure_future(c(t))
                    )
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
            # ProtoOALightSymbol n'a PAS digits/pipPosition
            # On stocke des valeurs par défaut, elles seront mises à jour
            # par _fetch_symbol_details() qui appelle ProtoOASymbolByIdReq
            self._symbols[symbol_id] = SymbolInfo(
                symbol=symbol_name,
                broker_symbol=str(symbol_id),
                description=getattr(s, "description", ""),
                digits=5,       # Sera corrigé par _fetch_symbol_details
                tick_size=0.00001,
                pip_size=0.0001,
                min_volume=0.01,
                max_volume=100000,
                volume_step=0.01,
                lot_size=100000,
                is_tradable=True
            )
            # Diviseur de prix FIXE pour cTrader — NE PAS changer après
            self._symbol_divisors[symbol_id] = self._DEFAULT_DIVISOR

    def _process_symbol_details(self, payload):
        """Traite ProtoOASymbolByIdRes avec les détails complets (digits, volumes, etc.).

        Met à jour SymbolInfo avec les vrais digits/pipPosition/volumes du broker.
        NOTE: Ne touche PAS _symbol_divisors — le diviseur de prix est FIXE.

        Volumes proto: en centilots (1/100 de lot). Ex: minVolume=1 → 0.01 lots.
        ATTENTION aux defaults: un champ proto absent NE signifie PAS 1 lot.
        """
        count = 0
        for s in payload.symbol:
            symbol_id = s.symbolId
            if symbol_id not in self._symbols:
                continue
            existing = self._symbols[symbol_id]
            digits = getattr(s, "digits", 5)
            pip_position = getattr(s, "pipPosition", max(0, digits - 1))
            tick_size = 10 ** (-digits) if digits > 0 else 0.00001
            pip_size = 10 ** (-pip_position) if pip_position > 0 else tick_size

            # Volumes proto en centilots. Defaults conservateurs (0.01 lot min)
            min_vol_raw = getattr(s, "minVolume", 1)      # 1 centilot = 0.01 lots
            max_vol_raw = getattr(s, "maxVolume", 10000000)
            step_vol_raw = getattr(s, "stepVolume", 1)     # 1 centilot = 0.01 lots
            lot_size = getattr(s, "lotSize", 100000)

            min_volume = min_vol_raw / 100   # centilots → lots
            max_volume = max_vol_raw / 100
            step_volume = step_vol_raw / 100

            # Log les symboles intéressants pour debug (crypto + JPY)
            if (existing.symbol in (
                "BNBUSD", "BTCUSD", "ETHUSD", "SOLUSD",
                "FETUSD", "GALUSD", "USDJPY"
            ) or min_volume > 0.1):  # Alerter si min > 0.1 lots
                print(
                    f"[cTrader] 📊 {existing.symbol}: "
                    f"digits={digits} pipPos={pip_position} "
                    f"minVol={min_vol_raw}→{min_volume:.4f}L "
                    f"maxVol={max_vol_raw}→{max_volume:.1f}L "
                    f"step={step_vol_raw}→{step_volume:.4f}L "
                    f"lotSize={lot_size}"
                )

            self._symbols[symbol_id] = SymbolInfo(
                symbol=existing.symbol,
                broker_symbol=existing.broker_symbol,
                description=existing.description,
                digits=digits,
                tick_size=tick_size,
                pip_size=pip_size,
                min_volume=min_volume,
                max_volume=max_volume,
                volume_step=step_volume,
                lot_size=lot_size,
                is_tradable=True
            )
            count += 1
        print(f"[cTrader] ✅ Symbol details loaded: {count} symbols updated")

    async def fetch_symbol_details(self, symbol_ids: list[int] = None):
        """Appelle ProtoOASymbolByIdReq pour obtenir digits/pipPosition/volumes.
        
        ProtoOASymbolsListRes ne retourne que ProtoOALightSymbol (pas de digits).
        Il faut un appel séparé pour les détails complets.
        """
        if symbol_ids is None:
            symbol_ids = list(self._symbols.keys())
        if not symbol_ids:
            return

        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASymbolByIdReq

        # Batch par 50 pour éviter les limites cTrader
        BATCH_SIZE = 50
        for i in range(0, len(symbol_ids), BATCH_SIZE):
            batch = symbol_ids[i:i + BATCH_SIZE]

            loop = asyncio.get_event_loop()
            future = loop.create_future()
            self._pending_requests["symbol_details"] = future

            req = ProtoOASymbolByIdReq()
            req.ctidTraderAccountId = self.account_id
            for sid in batch:
                req.symbolId.append(sid)

            self._send_via_reactor(req)
            try:
                await asyncio.wait_for(future, timeout=30)
            except asyncio.TimeoutError:
                self._pending_requests.pop("symbol_details", None)
                print(f"[cTrader] ⚠️ fetch_symbol_details timeout (batch {i//BATCH_SIZE+1})")
                break

            if i + BATCH_SIZE < len(symbol_ids):
                await asyncio.sleep(0.2)  # throttle entre batches

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

    def _resolve_symbol_name(self, symbol_id: int) -> str:
        """Résout un symbolId numérique en nom unifié."""
        # D'abord chercher dans _symbol_id_to_unified (mapping des souscriptions)
        if symbol_id in self._symbol_id_to_unified:
            return self._symbol_id_to_unified[symbol_id]
        # Ensuite dans _symbols (catalogue complet)
        if symbol_id in self._symbols:
            ctrader_name = self._symbols[symbol_id].symbol
            # Essayer de trouver le nom unifié via reverse_map
            unified = self.reverse_map_symbol(ctrader_name)
            return unified or ctrader_name
        return str(symbol_id)

    def _get_divisor(self, symbol_id: int) -> int:
        """Retourne le diviseur de prix pour décoder les entiers cTrader.

        IMPORTANT: Ce diviseur est FIXE (10^5) et ne change PAS quand
        _process_symbol_details met à jour digits/pipPosition.
        Les digits du symbole ne servent qu'à l'arrondi des prix.
        """
        return self._symbol_divisors.get(symbol_id, self._DEFAULT_DIVISOR)

    def _get_digits(self, symbol_id: int) -> int:
        """Retourne le nombre de décimales autorisées pour un symbole."""
        if symbol_id in self._symbols:
            return self._symbols[symbol_id].digits
        return 5  # défaut forex

    def _round_price(self, price: float, symbol_id: int) -> float:
        """Arrondit un prix au nombre de décimales du symbole."""
        return round(price, self._get_digits(symbol_id))

    def _get_symbol_id_for_position(self, position_id: str) -> Optional[int]:
        """Trouve le symbolId d'une position via le cache reconcile."""
        # Fast path: direct mapping
        if hasattr(self, '_position_symbol_ids'):
            sid = self._position_symbol_ids.get(str(position_id))
            if sid:
                return sid
        # Slow path: reverse lookup from position name
        for pos in self._positions:
            if str(pos.position_id) == str(position_id):
                sym_name = pos.symbol
                for sid, sinfo in self._symbols.items():
                    if sinfo.symbol == sym_name or self._resolve_symbol_name(sid) == sym_name:
                        return sid
        return None

    def _process_reconcile_response(self, payload):
        self._positions = []
        self._pending_orders = []
        self._position_symbol_ids = {}  # position_id → symbolId
        for pos in payload.position:
            side = OrderSide.BUY if pos.tradeData.tradeSide == 1 else OrderSide.SELL
            pos_id = str(pos.positionId)
            self._position_symbol_ids[pos_id] = pos.tradeData.symbolId
            self._positions.append(Position(
                position_id=str(pos.positionId),
                symbol=self._resolve_symbol_name(pos.tradeData.symbolId),
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
                symbol=self._resolve_symbol_name(order.tradeData.symbolId),
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
        # Extraire les deux IDs possibles
        order_id = None
        position_id = None
        if hasattr(payload, "order") and hasattr(payload.order, "orderId"):
            order_id = payload.order.orderId
        if hasattr(payload, "orderId"):
            order_id = order_id or payload.orderId
        if hasattr(payload, "position") and hasattr(payload.position, "positionId"):
            position_id = payload.position.positionId

        print(f"[cTrader] DEBUG: Received {ptype} orderId={order_id} positionId={position_id}")

        # Déterminer quelle requête en attente correspond
        for key in ("order_place", "position_amend", "position_close", "order_cancel"):
            if key not in self._pending_requests:
                continue
            future = self._pending_requests.pop(key)

            if ptype == "ProtoOAOrderErrorEvent" or "Error" in ptype:
                error_code = getattr(payload, "errorCode", "UNKNOWN")
                description = getattr(payload, "description", "No description")
                self._resolve_future(future, OrderResult(
                    success=False,
                    message=f"{key} rejected: {error_code} - {description}",
                    broker_response=payload
                ))
                return

            # Choisir l'ID approprié selon l'opération
            # - position_amend/position_close → positionId obligatoire
            # - order_place → positionId si MARKET fill, sinon orderId
            # - order_cancel → orderId
            if key in ("position_amend", "position_close"):
                result_id = str(position_id) if position_id else str(order_id or "unknown")
            elif key == "order_place":
                # Pour un MARKET fill, retourner le positionId (plus utile)
                result_id = str(position_id) if position_id else str(order_id or "unknown")
            else:
                result_id = str(order_id) if order_id else str(position_id or "unknown")

            self._resolve_future(future, OrderResult(
                success=True,
                order_id=result_id,
                message=f"{key} OK (orderId={order_id}, positionId={position_id})",
                broker_response=payload
            ))
            return  # Un seul future résolu par message

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
        self._send_via_reactor(req)
        try:
            return await asyncio.wait_for(future, timeout=10)
        except asyncio.TimeoutError:
            self._pending_requests.pop("account_info", None)
            return None

    async def get_symbols(self) -> List[SymbolInfo]:
        if not self._connected:
            return []
        if self._symbols:
            return list(self._symbols.values())

        async with self._symbols_lock:
            # Double-check après le lock (un autre appel a pu charger entre-temps)
            if self._symbols:
                return list(self._symbols.values())

            loop = asyncio.get_event_loop()
            future = loop.create_future()
            self._pending_requests["symbols"] = future
            req = ProtoOASymbolsListReq()
            req.ctidTraderAccountId = self.account_id
            from twisted.internet import reactor
            self._send_via_reactor(req)
            try:
                result = await asyncio.wait_for(future, timeout=15)
            except asyncio.TimeoutError:
                self._pending_requests.pop("symbols", None)
                return []

            # Charger les détails complets (digits, pipPosition, volumes)
            # ProtoOALightSymbol n'a PAS ces champs
            if self._symbols:
                await self.fetch_symbol_details()
                # Log un échantillon pour vérifier
                sample = list(self._symbols.values())[:3]
                for s in sample:
                    print(f"[cTrader] Symbol {s.symbol}: digits={s.digits}, "
                          f"pip_size={s.pip_size}, min_vol={s.min_volume}")

            return list(self._symbols.values())

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
                    req.limitPrice = self._round_price(order.entry_price, symbol_id)
            elif order.order_type == OrderType.STOP:
                req.orderType = self._enum_value(req, "orderType", "STOP")
                if order.entry_price:
                    req.stopPrice = self._round_price(order.entry_price, symbol_id)
            req.tradeSide = self._enum_value(req, "tradeSide", order.side.value)
            # cTrader volume = centilots (1 lot = 100 centilots)
            CENTILOTS = 100
            broker_volume = order.broker_volume or int(round(order.volume * CENTILOTS))

            # Validation volume contre les limites réelles du symbole
            sym_info = self._symbols.get(symbol_id)
            if sym_info:
                min_centilots = int(round(sym_info.min_volume * CENTILOTS))
                max_centilots = int(round(sym_info.max_volume * CENTILOTS))
                step_centilots = max(1, int(round(sym_info.volume_step * CENTILOTS)))

                if broker_volume < min_centilots:
                    return OrderResult(
                        success=False,
                        message=f"Volume {order.volume:.3f}L ({broker_volume} centilots) "
                                f"< min {sym_info.min_volume:.3f}L ({min_centilots} centilots) "
                                f"pour {order.symbol}. "
                                f"Augmenter risk_percent ou réduire le nombre d'instruments."
                    )
                if broker_volume > max_centilots:
                    broker_volume = max_centilots
                    print(f"[cTrader] ⚠️ Volume capé au maximum: {max_centilots} centilots "
                          f"({sym_info.max_volume:.1f}L) pour {order.symbol}")

                # Arrondir au step
                if step_centilots > 1:
                    broker_volume = max(min_centilots,
                                       (broker_volume // step_centilots) * step_centilots)

            req.volume = broker_volume
            if order.stop_loss:
                req.stopLoss = self._round_price(order.stop_loss, symbol_id)
            if order.take_profit:
                req.takeProfit = self._round_price(order.take_profit, symbol_id)
            # timeInForce: pas nécessaire pour MARKET, obligatoire pour LIMIT/STOP
            if order.order_type != OrderType.MARKET:
                if order.expiry_timestamp_ms:
                    req.timeInForce = self._enum_value(req, "timeInForce", "GOOD_TILL_DATE")
                    req.expirationTimestamp = order.expiry_timestamp_ms
                else:
                    req.timeInForce = self._enum_value(req, "timeInForce", "GOOD_TILL_CANCEL")
            if order.label:
                req.label = order.label[:50]
            if order.comment:
                req.comment = order.comment[:100]
            print(f"[cTrader] Placing {order.order_type.value} {order.side.value} "
                  f"{order.volume:.3f} lots ({broker_volume} centilots) on {order.symbol} "
                  f"@ {order.entry_price}"
                  + (f" SL={req.stopLoss} TP={req.takeProfit}" if order.stop_loss else "")
                  + (f" [min={sym_info.min_volume:.3f}L digits={sym_info.digits}]" if sym_info else ""))
            from twisted.internet import reactor
            self._send_via_reactor(req)
            result = await asyncio.wait_for(future, timeout=30)
            # Enregistrer le mapping positionId → symbolId pour amend/close
            if result.success and result.order_id:
                self._position_symbol_ids[str(result.order_id)] = symbol_id
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
        self._send_via_reactor(req)
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
        self._send_via_reactor(req)
        try:
            await asyncio.wait_for(future, timeout=15)
            return self._pending_orders
        except asyncio.TimeoutError:
            return []

    async def get_positions(self) -> List[Position]:
        await self.get_pending_orders()
        return self._positions

    async def amend_position_sltp(
        self, position_id: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> OrderResult:
        """Modify SL/TP on an open position via ProtoOAAmendPositionSLTPReq."""
        if not self._connected:
            return OrderResult(success=False, message="Not connected")

        # Arrondir aux digits du symbole pour éviter TRADING_BAD_STOPS
        sym_id = self._get_symbol_id_for_position(position_id)
        if not sym_id:
            # Cache vide ou position pas trouvée — rafraîchir
            await self.get_positions()
            sym_id = self._get_symbol_id_for_position(position_id)
        if sym_id:
            digits = self._get_digits(sym_id)
            if stop_loss is not None:
                stop_loss = round(stop_loss, digits)
            if take_profit is not None:
                take_profit = round(take_profit, digits)

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests["position_amend"] = future
        req = ProtoOAAmendPositionSLTPReq()
        req.ctidTraderAccountId = self.account_id
        req.positionId = int(position_id)
        if stop_loss is not None:
            req.stopLoss = stop_loss
        if take_profit is not None:
            req.takeProfit = take_profit
        print(f"[cTrader] Amending position {position_id}: SL={stop_loss} TP={take_profit}"
              + (f" ({digits} digits)" if sym_id else " (symbol unknown, no rounding)"))
        self._send_via_reactor(req)
        try:
            return await asyncio.wait_for(future, timeout=15)
        except asyncio.TimeoutError:
            self._pending_requests.pop("position_amend", None)
            return OrderResult(success=False, message="Amend timeout")

    async def close_position(
        self, position_id: str, volume: Optional[float] = None
    ) -> OrderResult:
        """Close a position via ProtoOAClosePositionReq.
        
        volume is REQUIRED by the protobuf. If not provided, we fetch
        the position's current volume via get_positions().
        """
        if not self._connected:
            return OrderResult(success=False, message="Not connected")

        # Si pas de volume fourni, récupérer celui de la position
        if volume is None:
            positions = await self.get_positions()
            matching = [p for p in positions if str(p.position_id) == str(position_id)]
            if matching:
                volume = matching[0].volume
                print(f"[cTrader] Position {position_id}: volume={volume} lots")
            else:
                return OrderResult(
                    success=False,
                    message=f"Position {position_id} not found, cannot determine volume"
                )

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests["position_close"] = future
        req = ProtoOAClosePositionReq()
        req.ctidTraderAccountId = self.account_id
        req.positionId = int(position_id)
        # cTrader volume = centilots (1 lot = 100)
        req.volume = int(round(volume * 100))
        print(f"[cTrader] Closing position {position_id} "
              f"({volume} lots = {req.volume} centilots)")
        self._send_via_reactor(req)
        try:
            return await asyncio.wait_for(future, timeout=15)
        except asyncio.TimeoutError:
            self._pending_requests.pop("position_close", None)
            return OrderResult(success=False, message="Close timeout")


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

    def amend_position_sltp(self, position_id: str, stop_loss=None, take_profit=None) -> OrderResult:
        return self._get_loop().run_until_complete(
            self.broker.amend_position_sltp(position_id, stop_loss, take_profit))

    def close_position(self, position_id: str, volume=None) -> OrderResult:
        return self._get_loop().run_until_complete(
            self.broker.close_position(position_id, volume))

    def get_last_tick(self, symbol: str) -> Optional[PriceTick]:
        return self.broker.get_last_tick(symbol)
