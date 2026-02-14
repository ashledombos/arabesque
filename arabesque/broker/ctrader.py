"""
Arabesque v2 — cTrader Open API Adapter (FTMO).

Connexion async + protobuf via ctrader-open-api.
Référence : envolees-auto/brokers/ctrader.py

Dépendances :
    pip install ctrader-open-api twisted

Architecture cTrader Open API :
    - Connexion TCP/TLS (port 5035)
    - Auth OAuth2 (client_id + client_secret + access_token)
    - Messages protobuf (ProtoOA*)
    - Async via Twisted reactor (mais on wrappe en sync pour Arabesque)

Symboles cTrader :
    - Chaque symbole a un symbolId (int) qu'on doit résoudre
    - Volume en unités (100_000 = 1 lot standard FX)
    - Prix en pipettes (multiply par pipPosition pour le vrai prix)
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import threading
from dataclasses import dataclass, field

from arabesque.broker.adapters import BrokerAdapter, OrderResult

logger = logging.getLogger("arabesque.broker.ctrader")


@dataclass
class CTraderConfig:
    """Configuration cTrader Open API."""
    host: str = "demo.ctraderapi.com"
    port: int = 5035
    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""
    account_id: int = 0                # ctidTraderAccountId
    name: str = "ctrader_ftmo"

    # Timeouts
    connect_timeout: float = 10.0
    order_timeout: float = 15.0

    # Retry
    max_retries: int = 3
    retry_delay: float = 2.0


class CTraderAdapter(BrokerAdapter):
    """Adapter cTrader Open API pour FTMO.

    Utilise le package ctrader-open-api (protobuf + Twisted).
    En interne, tourne un thread Twisted pour l'async.

    Usage :
        config = CTraderConfig(
            client_id="...", client_secret="...",
            access_token="...", account_id=12345,
        )
        adapter = CTraderAdapter(config)
        adapter.connect()
        quote = adapter.get_quote("EURUSD")
    """

    def __init__(self, config: CTraderConfig | dict):
        if isinstance(config, dict):
            config = CTraderConfig(**{k: v for k, v in config.items()
                                      if k in CTraderConfig.__dataclass_fields__})
        self.cfg = config
        self.name = config.name
        self._connected = False
        self._client = None
        self._symbols: dict[str, dict] = {}   # name → symbol info
        self._symbol_ids: dict[int, dict] = {} # id → symbol info
        self._reactor_thread = None

    def connect(self) -> bool:
        """Établit la connexion cTrader Open API.

        1. Import ctrader-open-api
        2. Démarre le reactor Twisted dans un thread
        3. Auth OAuth2
        4. Charge la liste des symboles du compte
        """
        try:
            from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
                ProtoOAApplicationAuthReq,
                ProtoOAAccountAuthReq,
            )
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOASymbolsListReq,
                ProtoOASubscribeSpotsReq,
            )
        except ImportError:
            logger.error("ctrader-open-api not installed. "
                         "Run: pip install ctrader-open-api")
            return False

        try:
            # Créer le client
            self._client = Client(
                self.cfg.host, self.cfg.port,
                TcpProtocol,
            )

            # Démarrer le reactor dans un thread background
            self._reactor_thread = threading.Thread(
                target=self._run_reactor, daemon=True
            )
            self._reactor_thread.start()
            time.sleep(1)  # Laisser le reactor démarrer

            # Auth application
            app_auth = Protobuf.extract(
                ProtoOAApplicationAuthReq(
                    clientId=self.cfg.client_id,
                    clientSecret=self.cfg.client_secret,
                )
            )
            self._send_and_wait(app_auth)

            # Auth compte
            account_auth = Protobuf.extract(
                ProtoOAAccountAuthReq(
                    ctidTraderAccountId=self.cfg.account_id,
                    accessToken=self.cfg.access_token,
                )
            )
            self._send_and_wait(account_auth)

            # Charger la liste des symboles
            self._load_symbols()

            self._connected = True
            logger.info(f"[{self.name}] Connected. {len(self._symbols)} symbols loaded.")
            return True

        except Exception as e:
            logger.error(f"[{self.name}] Connection failed: {e}")
            return False

    def get_quote(self, symbol: str) -> dict:
        """Obtient bid/ask via spot subscription ou snapshot."""
        if not self._connected:
            return {"bid": 0, "ask": 0, "spread": 0, "error": "not connected"}

        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOASubscribeSpotsReq,
            )

            sym_info = self._symbols.get(symbol)
            if not sym_info:
                return {"bid": 0, "ask": 0, "spread": 0,
                        "error": f"symbol {symbol} not found"}

            symbol_id = sym_info["symbolId"]
            pip_pos = sym_info.get("pipPosition", 4)
            divisor = 10 ** pip_pos

            # Subscribe spots (si pas déjà fait)
            req = Protobuf.extract(
                ProtoOASubscribeSpotsReq(
                    ctidTraderAccountId=self.cfg.account_id,
                    symbolId=[symbol_id],
                )
            )
            resp = self._send_and_wait(req, timeout=5.0)

            if resp and hasattr(resp, "bid") and hasattr(resp, "ask"):
                bid = resp.bid / divisor
                ask = resp.ask / divisor
                return {"bid": bid, "ask": ask, "spread": ask - bid}

            # Fallback : pas de spot data, retourner vide
            return {"bid": 0, "ask": 0, "spread": 0,
                    "error": "no spot data received"}

        except Exception as e:
            logger.error(f"[{self.name}] get_quote error: {e}")
            return {"bid": 0, "ask": 0, "spread": 0, "error": str(e)}

    def get_account_info(self) -> dict:
        """Obtient balance, equity, marge."""
        if not self._connected:
            return {"balance": 0, "equity": 0, "margin_used": 0,
                    "error": "not connected"}

        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOATraderReq,
            )

            req = Protobuf.extract(
                ProtoOATraderReq(
                    ctidTraderAccountId=self.cfg.account_id,
                )
            )
            resp = self._send_and_wait(req)

            if resp and hasattr(resp, "trader"):
                trader = resp.trader
                # cTrader retourne les montants en centièmes
                balance = trader.balance / 100
                return {
                    "balance": balance,
                    "equity": balance,  # equity = balance + floating P&L
                    "margin_used": 0,
                }

            return {"balance": 0, "equity": 0, "margin_used": 0}

        except Exception as e:
            logger.error(f"[{self.name}] get_account_info error: {e}")
            return {"balance": 0, "equity": 0, "margin_used": 0, "error": str(e)}

    def compute_volume(self, symbol: str, risk_cash: float,
                       risk_distance: float) -> float:
        """Calcule le volume en lots pour cTrader.

        cTrader utilise le volume en unités (100_000 = 1 lot standard).
        Le volume minimum et le step dépendent du symbole.

        Formule :
            volume_units = risk_cash / (risk_distance_pips * pip_value_per_unit)
            lots = volume_units / 100_000

        Pour les paires XXX/USD : pip_value = 0.0001 * volume_units = 10$/lot
        Pour les paires XXX/YYY : pip_value dépend du taux YYY/USD
        """
        if risk_distance <= 0 or risk_cash <= 0:
            return 0.0

        sym_info = self._symbols.get(symbol, {})
        pip_position = sym_info.get("pipPosition", 4)
        pip_size = 10 ** (-pip_position)
        step_volume = sym_info.get("stepVolume", 1000)  # min step en unités
        min_volume = sym_info.get("minVolume", 1000)

        # Risk distance en pips
        risk_pips = risk_distance / pip_size

        # Pip value approximation (pour USD-denominated accounts)
        # Pour XXX/USD : pip_value = pip_size * lot_size
        # Pour USD/XXX : pip_value = pip_size * lot_size / rate
        # Simplification : pip_value ≈ 10 USD/lot pour majeures
        pip_value_per_lot = 10.0  # USD par pip par lot standard

        # Volume en lots
        lots = risk_cash / (risk_pips * pip_value_per_lot)

        # Convertir en unités cTrader
        volume_units = lots * 100_000

        # Arrondir au step_volume, toujours vers le bas
        volume_units = math.floor(volume_units / step_volume) * step_volume

        # Minimum
        if volume_units < min_volume:
            volume_units = min_volume

        # Retourner en lots
        return volume_units / 100_000

    def place_order(self, signal: dict, sizing: dict) -> dict:
        """Place un ordre market via cTrader Open API.

        Args:
            signal: JSON du signal TradingView
            sizing: {"risk_cash": float, "risk_distance": float}

        Returns:
            OrderResult.to_dict()
        """
        if not self._connected:
            return OrderResult(False, message="not connected").to_dict()

        symbol = signal.get("symbol", "")
        side = signal.get("side", "buy")
        sl = signal.get("sl", 0)

        volume = self.compute_volume(
            symbol, sizing.get("risk_cash", 0),
            sizing.get("risk_distance", 0),
        )
        if volume <= 0:
            return OrderResult(False, message="volume=0").to_dict()

        volume_units = int(volume * 100_000)

        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOANewOrderReq,
            )
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
                ProtoOAOrderType,
                ProtoOATradeSide,
            )

            sym_info = self._symbols.get(symbol, {})
            symbol_id = sym_info.get("symbolId", 0)
            if symbol_id == 0:
                return OrderResult(False, message=f"symbol {symbol} not found").to_dict()

            # Construire l'ordre
            trade_side = (ProtoOATradeSide.BUY if side.lower() == "buy"
                          else ProtoOATradeSide.SELL)

            # SL en prix absolu (cTrader accepte les deux)
            pip_pos = sym_info.get("pipPosition", 4)
            divisor = 10 ** pip_pos

            order_req = ProtoOANewOrderReq(
                ctidTraderAccountId=self.cfg.account_id,
                symbolId=symbol_id,
                orderType=ProtoOAOrderType.MARKET,
                tradeSide=trade_side,
                volume=volume_units,
            )

            # Ajouter SL si fourni
            if sl > 0:
                order_req.stopLoss = sl
                order_req.stopLossInPips = False  # Prix absolu

            logger.info(f"[{self.name}] Placing {side.upper()} {symbol} "
                        f"vol={volume:.2f} lots SL={sl}")

            resp = self._send_and_wait(
                Protobuf.extract(order_req),
                timeout=self.cfg.order_timeout,
            )

            if resp and hasattr(resp, "order"):
                order = resp.order
                fill_price = (order.executionPrice / divisor
                              if hasattr(order, "executionPrice") else 0)
                return OrderResult(
                    success=True,
                    order_id=str(order.orderId) if hasattr(order, "orderId") else "",
                    volume=volume,
                    fill_price=fill_price,
                    message="order placed",
                ).to_dict()

            return OrderResult(
                False, volume=volume,
                message="no response from cTrader",
            ).to_dict()

        except Exception as e:
            logger.error(f"[{self.name}] place_order error: {e}")
            return OrderResult(False, message=str(e)).to_dict()

    def close_position(self, position_id: str, symbol: str) -> dict:
        """Ferme une position via close market order."""
        if not self._connected:
            return {"success": False, "message": "not connected"}

        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAClosePositionReq,
            )

            req = Protobuf.extract(
                ProtoOAClosePositionReq(
                    ctidTraderAccountId=self.cfg.account_id,
                    positionId=int(position_id),
                    volume=0,  # 0 = close all
                )
            )
            resp = self._send_and_wait(req, timeout=self.cfg.order_timeout)

            if resp:
                return {"success": True, "message": f"position {position_id} closed"}
            return {"success": False, "message": "no response"}

        except Exception as e:
            logger.error(f"[{self.name}] close_position error: {e}")
            return {"success": False, "message": str(e)}

    def modify_sl(self, position_id: str, symbol: str, new_sl: float) -> dict:
        """Modifie le SL d'une position ouverte."""
        if not self._connected:
            return {"success": False, "message": "not connected"}

        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAAmendPositionSLTPReq,
            )

            req = Protobuf.extract(
                ProtoOAAmendPositionSLTPReq(
                    ctidTraderAccountId=self.cfg.account_id,
                    positionId=int(position_id),
                    stopLoss=new_sl,
                    stopLossInPips=False,
                )
            )
            resp = self._send_and_wait(req, timeout=self.cfg.order_timeout)

            if resp:
                logger.info(f"[{self.name}] SL modified: pos={position_id} → {new_sl}")
                return {"success": True, "message": f"SL → {new_sl}"}
            return {"success": False, "message": "no response"}

        except Exception as e:
            logger.error(f"[{self.name}] modify_sl error: {e}")
            return {"success": False, "message": str(e)}

    # ── Internal helpers ─────────────────────────────────────────────

    def _load_symbols(self):
        """Charge la liste des symboles disponibles."""
        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOASymbolsListReq,
            )

            req = Protobuf.extract(
                ProtoOASymbolsListReq(
                    ctidTraderAccountId=self.cfg.account_id,
                )
            )
            resp = self._send_and_wait(req, timeout=10.0)

            if resp and hasattr(resp, "symbol"):
                for sym in resp.symbol:
                    info = {
                        "symbolId": sym.symbolId,
                        "symbolName": sym.symbolName if hasattr(sym, "symbolName") else "",
                        "pipPosition": sym.pipPosition if hasattr(sym, "pipPosition") else 4,
                        "stepVolume": sym.stepVolume if hasattr(sym, "stepVolume") else 1000,
                        "minVolume": sym.minVolume if hasattr(sym, "minVolume") else 1000,
                        "maxVolume": sym.maxVolume if hasattr(sym, "maxVolume") else 50_000_000,
                    }
                    name = info["symbolName"]
                    self._symbols[name] = info
                    self._symbol_ids[sym.symbolId] = info

        except Exception as e:
            logger.error(f"[{self.name}] _load_symbols error: {e}")

    def _send_and_wait(self, message, timeout: float = 10.0):
        """Envoie un message protobuf et attend la réponse.

        NOTE : wrapping simplifié. En production, utiliser le pattern
        callback du client ctrader-open-api ou asyncio.
        """
        if self._client is None:
            return None
        try:
            # Le client ctrader-open-api utilise Twisted deferreds
            # On wrappe avec un Event pour sync
            result = [None]
            event = threading.Event()

            def callback(response):
                result[0] = response
                event.set()

            self._client.send(message, callback=callback)
            event.wait(timeout=timeout)
            return result[0]
        except Exception as e:
            logger.error(f"[{self.name}] _send_and_wait error: {e}")
            return None

    def _run_reactor(self):
        """Démarre le reactor Twisted dans un thread."""
        try:
            from twisted.internet import reactor
            if not reactor.running:
                reactor.run(installSignalHandlers=False)
        except Exception as e:
            logger.error(f"[{self.name}] reactor error: {e}")
