"""
Arabesque v2 — TradeLocker REST API Adapter (Goat Funded Trader).

REST API via la lib tradelocker.
Référence : envolees-auto/brokers/tradelocker.py

Dépendances :
    pip install tradelocker

Architecture TradeLocker :
    - REST API (HTTPS)
    - Auth : email + password + serveur
    - Endpoints : /auth, /trade/accounts, /trade/positions, ...
    - La lib tradelocker wrappe tout en méthodes sync
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from arabesque.broker.adapters import BrokerAdapter, OrderResult

logger = logging.getLogger("arabesque.broker.tradelocker")


@dataclass
class TradeLockerConfig:
    """Configuration TradeLocker."""
    email: str = ""
    password: str = ""
    server: str = "live"
    base_url: str = "https://bsb.tradelocker.com"
    account_id: int = 0
    name: str = "tradelocker_gft"

    # Timeouts
    request_timeout: float = 15.0

    # Retry
    max_retries: int = 3
    retry_delay: float = 2.0


class TradeLockerAdapter(BrokerAdapter):
    """Adapter TradeLocker REST API pour Goat Funded Trader.

    Utilise le package tradelocker (pip install tradelocker).
    API synchrone, pas besoin de thread séparé.

    Usage :
        config = TradeLockerConfig(
            email="...", password="...", server="live",
            account_id=12345,
        )
        adapter = TradeLockerAdapter(config)
        adapter.connect()
        quote = adapter.get_quote("EURUSD")
    """

    def __init__(self, config: TradeLockerConfig | dict):
        if isinstance(config, dict):
            config = TradeLockerConfig(**{k: v for k, v in config.items()
                                          if k in TradeLockerConfig.__dataclass_fields__})
        self.cfg = config
        self.name = config.name
        self._connected = False
        self._tl = None              # TLAPI instance
        self._instruments: dict = {} # name → instrument info

    def connect(self) -> bool:
        """Établit la connexion TradeLocker.

        1. Import tradelocker
        2. Auth (email + password)
        3. Sélectionne le compte
        4. Charge la liste des instruments
        """
        try:
            from tradelocker import TLAPI
        except ImportError:
            logger.error("tradelocker not installed. "
                         "Run: pip install tradelocker")
            return False

        try:
            self._tl = TLAPI(
                environment=self.cfg.base_url,
                username=self.cfg.email,
                password=self.cfg.password,
                server=self.cfg.server,
            )

            # Vérifier la connexion en récupérant les comptes
            accounts = self._tl.get_all_accounts()
            if not accounts:
                logger.error(f"[{self.name}] No accounts found")
                return False

            # Sélectionner le bon compte
            if self.cfg.account_id:
                account_found = False
                for acc in accounts:
                    if acc.get("id") == self.cfg.account_id:
                        account_found = True
                        break
                if not account_found:
                    logger.warning(f"[{self.name}] Account {self.cfg.account_id} "
                                   f"not found, using first account")

            # Charger les instruments
            self._load_instruments()

            self._connected = True
            logger.info(f"[{self.name}] Connected. "
                        f"{len(self._instruments)} instruments loaded.")
            return True

        except Exception as e:
            logger.error(f"[{self.name}] Connection failed: {e}")
            return False

    def get_quote(self, symbol: str) -> dict:
        """Obtient bid/ask."""
        if not self._connected or not self._tl:
            return {"bid": 0, "ask": 0, "spread": 0, "error": "not connected"}

        try:
            instrument_id = self._resolve_instrument_id(symbol)
            if not instrument_id:
                return {"bid": 0, "ask": 0, "spread": 0,
                        "error": f"instrument {symbol} not found"}

            # TradeLocker : get_latest_asking_price retourne (bid, ask)
            price_info = self._tl.get_price_history(
                instrument_id=instrument_id,
                resolution="1",     # 1 minute
                start_timestamp=0,   # Dernier prix
                end_timestamp=0,
                lookback_period="1", # 1 bar
            )

            if price_info is not None:
                # La lib retourne un DataFrame avec OHLC
                if len(price_info) > 0:
                    last = price_info.iloc[-1]
                    bid = float(last.get("c", 0))  # close = dernier bid
                    # Estimation ask = bid + spread moyen
                    inst_info = self._instruments.get(symbol, {})
                    spread = inst_info.get("spread", 0.0001)
                    ask = bid + spread
                    return {"bid": bid, "ask": ask, "spread": spread}

            # Fallback : utiliser get_latest_asking_price
            ask_price = self._tl.get_latest_asking_price(instrument_id)
            if ask_price:
                inst_info = self._instruments.get(symbol, {})
                spread = inst_info.get("spread", 0.0001)
                return {
                    "bid": ask_price - spread,
                    "ask": ask_price,
                    "spread": spread,
                }

            return {"bid": 0, "ask": 0, "spread": 0, "error": "no price data"}

        except Exception as e:
            logger.error(f"[{self.name}] get_quote error: {e}")
            return {"bid": 0, "ask": 0, "spread": 0, "error": str(e)}

    def get_account_info(self) -> dict:
        """Obtient balance, equity, marge."""
        if not self._connected or not self._tl:
            return {"balance": 0, "equity": 0, "margin_used": 0,
                    "error": "not connected"}

        try:
            accounts = self._tl.get_all_accounts()
            if accounts:
                acc = accounts[0]  # Premier compte
                return {
                    "balance": float(acc.get("balance", 0)),
                    "equity": float(acc.get("equity", 0)),
                    "margin_used": float(acc.get("usedMargin", 0)),
                }
            return {"balance": 0, "equity": 0, "margin_used": 0}

        except Exception as e:
            logger.error(f"[{self.name}] get_account_info error: {e}")
            return {"balance": 0, "equity": 0, "margin_used": 0, "error": str(e)}

    def compute_volume(self, symbol: str, risk_cash: float,
                       risk_distance: float) -> float:
        """Calcule le volume (lots) pour TradeLocker.

        TradeLocker utilise les lots standards (1 lot = 100,000 unités FX).
        Le step et le min dépendent de l'instrument.
        """
        if risk_distance <= 0 or risk_cash <= 0:
            return 0.0

        inst_info = self._instruments.get(symbol, {})
        lot_step = inst_info.get("lotStep", 0.01)
        min_lot = inst_info.get("minLot", 0.01)
        max_lot = inst_info.get("maxLot", 100.0)

        # Pip value approximation (pour comptes USD)
        pip_size = inst_info.get("pipSize", 0.0001)
        risk_pips = risk_distance / pip_size

        # pip_value ≈ 10 USD/lot pour paires XXX/USD
        pip_value_per_lot = 10.0
        lots = risk_cash / (risk_pips * pip_value_per_lot)

        # Arrondir au lot_step, toujours vers le bas
        lots = math.floor(lots / lot_step) * lot_step

        # Clamp
        lots = max(min_lot, min(lots, max_lot))

        return round(lots, 2)

    def place_order(self, signal: dict, sizing: dict) -> dict:
        """Place un ordre market via TradeLocker REST API."""
        if not self._connected or not self._tl:
            return OrderResult(False, message="not connected").to_dict()

        symbol = signal.get("symbol", "")
        side = signal.get("side", "buy")
        sl = signal.get("sl", 0)

        instrument_id = self._resolve_instrument_id(symbol)
        if not instrument_id:
            return OrderResult(False, message=f"instrument {symbol} not found").to_dict()

        volume = self.compute_volume(
            symbol, sizing.get("risk_cash", 0),
            sizing.get("risk_distance", 0),
        )
        if volume <= 0:
            return OrderResult(False, message="volume=0").to_dict()

        try:
            logger.info(f"[{self.name}] Placing {side.upper()} {symbol} "
                        f"vol={volume:.2f} SL={sl}")

            order_id = self._tl.create_order(
                instrument_id=instrument_id,
                quantity=volume,
                side=side.lower(),
                type_="market",
                stop_loss=sl if sl > 0 else None,
            )

            if order_id:
                # Récupérer le fill price
                fill_price = 0.0
                try:
                    orders = self._tl.get_all_orders()
                    for o in (orders or []):
                        if str(o.get("id")) == str(order_id):
                            fill_price = float(o.get("filledPrice", 0))
                            break
                except Exception:
                    pass

                return OrderResult(
                    success=True,
                    order_id=str(order_id),
                    volume=volume,
                    fill_price=fill_price,
                    message="order placed",
                ).to_dict()

            return OrderResult(False, volume=volume,
                               message="order rejected by TradeLocker").to_dict()

        except Exception as e:
            logger.error(f"[{self.name}] place_order error: {e}")
            return OrderResult(False, message=str(e)).to_dict()

    def close_position(self, position_id: str, symbol: str) -> dict:
        """Ferme une position."""
        if not self._connected or not self._tl:
            return {"success": False, "message": "not connected"}

        try:
            result = self._tl.close_position(int(position_id))
            if result:
                return {"success": True,
                        "message": f"position {position_id} closed"}
            return {"success": False, "message": "close failed"}

        except Exception as e:
            logger.error(f"[{self.name}] close_position error: {e}")
            return {"success": False, "message": str(e)}

    def modify_sl(self, position_id: str, symbol: str, new_sl: float) -> dict:
        """Modifie le SL d'une position ouverte."""
        if not self._connected or not self._tl:
            return {"success": False, "message": "not connected"}

        try:
            # TradeLocker : modify position via update
            result = self._tl.modify_position(
                position_id=int(position_id),
                stop_loss=new_sl,
            )
            if result:
                logger.info(f"[{self.name}] SL modified: "
                            f"pos={position_id} → {new_sl}")
                return {"success": True, "message": f"SL → {new_sl}"}
            return {"success": False, "message": "modify failed"}

        except Exception as e:
            logger.error(f"[{self.name}] modify_sl error: {e}")
            return {"success": False, "message": str(e)}

    # ── Internal helpers ─────────────────────────────────────────────

    def _load_instruments(self):
        """Charge la liste des instruments disponibles."""
        if not self._tl:
            return

        try:
            instruments = self._tl.get_all_instruments()
            if instruments:
                for inst in instruments:
                    name = inst.get("name", "")
                    self._instruments[name] = {
                        "instrumentId": inst.get("tradableInstrumentId", 0),
                        "name": name,
                        "pipSize": float(inst.get("pipSize", 0.0001)),
                        "lotStep": float(inst.get("lotStep", 0.01)),
                        "minLot": float(inst.get("minQuantity", 0.01)),
                        "maxLot": float(inst.get("maxQuantity", 100.0)),
                        "spread": float(inst.get("spread", 0.0001)),
                    }
        except Exception as e:
            logger.error(f"[{self.name}] _load_instruments error: {e}")

    def _resolve_instrument_id(self, symbol: str) -> int | None:
        """Résout le nom de symbole en instrument_id TradeLocker."""
        inst = self._instruments.get(symbol)
        if inst:
            return inst["instrumentId"]

        # Essayer les variantes (EURUSD vs EUR/USD)
        variants = [
            symbol,
            symbol[:3] + "/" + symbol[3:] if len(symbol) == 6 else symbol,
            symbol.replace("/", ""),
        ]
        for v in variants:
            if v in self._instruments:
                return self._instruments[v]["instrumentId"]

        # Dernier recours : recherche partielle
        for name, info in self._instruments.items():
            if symbol.replace("/", "") in name.replace("/", ""):
                return info["instrumentId"]

        return None
