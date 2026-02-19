#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arabesque — Price Feed Manager.

Gère la connexion au broker cTrader source, la souscription aux ticks
prix, et la distribution vers les consommateurs (stratégie, guards, etc.).

Fonctionnalités :
  - Connexion et reconnexion automatique (backoff exponentiel)
  - Refresh préventif du token cTrader toutes les 60h (token valide ~3-4 jours)
  - Bus de ticks thread-safe : les consommateurs s'abonnent via subscribe()
  - Dernier tick accessible immédiatement via get_last_tick(symbol)
  - Support multi-symboles depuis config/settings.yaml [price_feed.symbols]

Usage minimal :
    from arabesque.live.price_feed import PriceFeedManager

    feed = PriceFeedManager.from_config("config/settings.yaml", "config/secrets.yaml")

    async def on_tick(tick):
        print(f"{tick.symbol} bid={tick.bid} ask={tick.ask}")

    await feed.subscribe("EURUSD", on_tick)
    await feed.start()      # non-bloquant, tourne en tâche de fond
    ...
    await feed.stop()
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Dict, List, Optional, Set

logger = logging.getLogger("arabesque.live.price_feed")


class PriceFeedManager:
    """
    Gestionnaire du price feed cTrader.

    Paramètres
    ----------
    broker_id   : clé du broker dans settings.yaml (ex: "ftmo_ctrader")
    broker_cfg  : dict de config mergée (settings + secrets + instruments_mapping)
    symbols     : liste de symboles unifiés à surveiller (ex: ["EURUSD", "XAUUSD"])
    reconnect_delay_s : délai initial de reconnexion (doublé à chaque tentative, max 120s)
    token_refresh_interval_h : intervalle de refresh préventif du token (défaut 60h)
    """

    def __init__(
        self,
        broker_id: str,
        broker_cfg: dict,
        symbols: List[str],
        reconnect_delay_s: float = 5.0,
        token_refresh_interval_h: float = 60.0,
    ):
        self.broker_id = broker_id
        self.broker_cfg = broker_cfg
        self.symbols = list(symbols)
        self.reconnect_delay_s = reconnect_delay_s
        self.token_refresh_interval_h = token_refresh_interval_h

        # State
        self._broker = None          # CTraderBroker instance
        self._running = False
        self._connected = False
        self._main_task: Optional[asyncio.Task] = None
        self._token_refresh_task: Optional[asyncio.Task] = None

        # Callbacks : symbol -> [callable(PriceTick)]
        self._callbacks: Dict[str, List[Callable]] = {}

        # Stats
        self._tick_counts: Dict[str, int] = {}
        self._last_tick_times: Dict[str, datetime] = {}
        self._reconnect_count = 0
        self._start_time: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        settings_path: str = "config/settings.yaml",
        secrets_path: str = "config/secrets.yaml",
    ) -> "PriceFeedManager":
        """
        Construit un PriceFeedManager depuis les fichiers de config.
        Lit price_feed.source_broker et price_feed.symbols dans settings.yaml.
        """
        from arabesque.config import load_full_config
        settings, secrets, instruments = load_full_config(settings_path, secrets_path)

        pf_cfg = settings.get("price_feed", {})
        source_broker_id = pf_cfg.get("source_broker", "")
        symbols = pf_cfg.get("symbols", [])

        if not source_broker_id:
            raise ValueError(
                "price_feed.source_broker non défini dans settings.yaml. "
                "Exemple: price_feed:\n  source_broker: ftmo_ctrader"
            )

        brokers_cfg = settings.get("brokers", {})
        broker_cfg = dict(brokers_cfg.get(source_broker_id, {}))

        if not broker_cfg:
            raise ValueError(f"Broker '{source_broker_id}' non trouvé dans settings.yaml")

        broker_type = broker_cfg.get("type", "").lower()
        if broker_type != "ctrader":
            raise ValueError(
                f"Le price feed ne supporte que cTrader pour l'instant "
                f"(broker '{source_broker_id}' est de type '{broker_type}')"
            )

        # Merger les secrets
        if source_broker_id in secrets:
            broker_cfg.update(secrets[source_broker_id])

        # Construire le mapping instruments pour ce broker
        instruments_mapping = {}
        for sym, inst_data in instruments.items():
            if isinstance(inst_data, dict) and source_broker_id in inst_data:
                instruments_mapping[sym] = inst_data[source_broker_id]
        if instruments_mapping:
            broker_cfg["instruments_mapping"] = instruments_mapping

        # Si symbols vide, prendre tous les instruments mappés sur ce broker
        if not symbols:
            symbols = list(instruments_mapping.keys())
            logger.info(
                f"price_feed.symbols vide → surveillance de tous les instruments "
                f"mappés sur {source_broker_id} ({len(symbols)} symboles)"
            )

        return cls(broker_id=source_broker_id, broker_cfg=broker_cfg, symbols=symbols)

    # ------------------------------------------------------------------
    # Abonnements consommateurs
    # ------------------------------------------------------------------

    async def subscribe(self, symbol: str, callback: Callable) -> None:
        """
        Abonner un callback aux ticks d'un symbole.
        callback(tick: PriceTick) — peut être sync ou async.
        """
        if symbol not in self._callbacks:
            self._callbacks[symbol] = []
        if callback not in self._callbacks[symbol]:
            self._callbacks[symbol].append(callback)
        # Si déjà connecté, souscrire immédiatement au broker
        if self._connected and self._broker:
            await self._broker.subscribe_spots(symbol, callback)

    async def unsubscribe(self, symbol: str, callback: Callable) -> None:
        if symbol in self._callbacks:
            try:
                self._callbacks[symbol].remove(callback)
            except ValueError:
                pass

    def get_last_tick(self, symbol: str):
        """Retourne le dernier PriceTick connu pour un symbole (ou None)."""
        if self._broker:
            return self._broker.get_last_tick(symbol)
        return None

    def get_stats(self) -> dict:
        """Statistiques du feed (ticks reçus, uptime, reconnexions)."""
        uptime = None
        if self._start_time:
            uptime = (datetime.now(timezone.utc) - self._start_time).total_seconds()
        return {
            "running": self._running,
            "connected": self._connected,
            "symbols": self.symbols,
            "tick_counts": dict(self._tick_counts),
            "last_tick_times": {
                k: v.isoformat() for k, v in self._last_tick_times.items()
            },
            "reconnect_count": self._reconnect_count,
            "uptime_seconds": uptime,
        }

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Démarre le price feed en tâche de fond (non-bloquant).
        La reconnexion automatique tourne en boucle tant que running=True.
        """
        if self._running:
            logger.warning("PriceFeedManager already running")
            return

        self._running = True
        self._start_time = datetime.now(timezone.utc)
        self._main_task = asyncio.create_task(self._run_loop())
        self._token_refresh_task = asyncio.create_task(self._token_refresh_loop())
        logger.info(
            f"[PriceFeed] Démarrage — broker: {self.broker_id}, "
            f"symboles: {self.symbols}"
        )

    async def stop(self) -> None:
        """Arrête proprement le price feed."""
        self._running = False
        if self._token_refresh_task and not self._token_refresh_task.done():
            self._token_refresh_task.cancel()
        if self._broker and self._connected:
            try:
                await self._broker.disconnect()
            except Exception as e:
                logger.debug(f"[PriceFeed] Disconnect error: {e}")
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass
        self._connected = False
        logger.info("[PriceFeed] Arrêté")

    async def run_forever(self) -> None:
        """Démarre et attend indéfiniment (usage en point d'entrée principal)."""
        await self.start()
        try:
            while self._running:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.stop()

    # ------------------------------------------------------------------
    # Boucle principale avec reconnexion
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        delay = self.reconnect_delay_s
        while self._running:
            try:
                await self._connect_and_subscribe()
                delay = self.reconnect_delay_s  # reset après succès
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[PriceFeed] Erreur: {e}")

            if not self._running:
                break

            self._connected = False
            self._reconnect_count += 1
            logger.warning(
                f"[PriceFeed] Reconnexion dans {delay:.0f}s "
                f"(tentative #{self._reconnect_count})"
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120.0)  # backoff exponentiel, max 2 min

    async def _connect_and_subscribe(self) -> None:
        from arabesque.broker.ctrader import CTraderBroker
        self._broker = CTraderBroker(self.broker_id, self.broker_cfg)

        logger.info(f"[PriceFeed] Connexion à cTrader ({self.broker_id})...")
        connected = await self._broker.connect()
        if not connected:
            raise ConnectionError(f"Impossible de se connecter à {self.broker_id}")

        self._connected = True
        logger.info(f"[PriceFeed] ✅ Connecté — chargement des symboles...")

        # Charger les symboles pour que le mapping symbolId soit disponible
        await self._broker.get_symbols()

        # Créer un callback interne qui met à jour les stats
        async def _internal_callback(tick):
            sym = tick.symbol
            self._tick_counts[sym] = self._tick_counts.get(sym, 0) + 1
            self._last_tick_times[sym] = tick.timestamp

        # Souscrire à chaque symbole configuré
        subscribed = []
        for symbol in self.symbols:
            # Callback stats interne
            await self._broker.subscribe_spots(symbol, _internal_callback)
            # Callbacks consommateurs enregistrés
            for cb in self._callbacks.get(symbol, []):
                await self._broker.subscribe_spots(symbol, cb)
            subscribed.append(symbol)

        logger.info(
            f"[PriceFeed] 📡 Souscrit à {len(subscribed)} symbole(s): "
            f"{', '.join(subscribed)}"
        )

        # Attendre indéfiniment en surveillant la connexion
        await self._watch_connection()

    async def _watch_connection(self) -> None:
        """
        Surveille la connexion. Lève une exception si le feed semble mort
        (aucun tick reçu depuis plus de 5 minutes sur un symbole actif).
        """
        STALE_THRESHOLD_S = 300  # 5 minutes sans tick = reconnexion
        CHECK_INTERVAL_S = 30

        while self._running and self._connected:
            await asyncio.sleep(CHECK_INTERVAL_S)

            if not self._running:
                break

            # Vérifier si les ticks arrivent encore
            now = datetime.now(timezone.utc)
            for symbol in self.symbols:
                last = self._last_tick_times.get(symbol)
                if last is None:
                    # Pas encore reçu de tick — pas d'alerte avant 2 min
                    if self._start_time and (now - self._start_time).total_seconds() > 120:
                        logger.warning(
                            f"[PriceFeed] ⚠️  Aucun tick reçu pour {symbol} "
                            f"depuis le démarrage (marché fermé ?)"
                        )
                    continue

                age_s = (now - last).total_seconds()
                if age_s > STALE_THRESHOLD_S:
                    raise ConnectionError(
                        f"Feed stale: aucun tick pour {symbol} depuis {age_s:.0f}s"
                    )

    # ------------------------------------------------------------------
    # Refresh préventif du token cTrader
    # ------------------------------------------------------------------

    async def _token_refresh_loop(self) -> None:
        """
        Rafraîchit le token cTrader de manière préventive.

        Le token a une durée de vie d'environ 3-4 jours.
        On le rafraîchit toutes les 60h pour ne jamais être en rupture.
        Après un refresh, le nouveau token est sauvegardé dans secrets.yaml
        via update_broker_tokens() afin que la prochaine reconnexion l'utilise.
        """
        interval_s = self.token_refresh_interval_h * 3600
        logger.info(
            f"[PriceFeed] Token refresh planifié toutes les "
            f"{self.token_refresh_interval_h:.0f}h"
        )

        while self._running:
            try:
                await asyncio.sleep(interval_s)
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            logger.info("[PriceFeed] 🔑 Refresh préventif du token cTrader...")
            if self._broker and self._broker._refresh_access_token():
                # Mettre à jour la config locale pour la prochaine reconnexion
                self.broker_cfg["access_token"] = self._broker.access_token
                self.broker_cfg["refresh_token"] = self._broker.refresh_token
                logger.info("[PriceFeed] ✅ Token rafraîchi et sauvegardé")
            else:
                logger.warning(
                    "[PriceFeed] ⚠️  Token refresh échoué — "
                    "la connexion pourrait expirer bientôt"
                )
