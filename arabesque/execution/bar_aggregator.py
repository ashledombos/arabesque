#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arabesque — Bar Aggregator.

Remplace BarPoller + l'intermédiaire _signal_to_webhook_dict.

Flux complet sans TradingView :

  PriceFeedManager (ticks cTrader)
      ↓  on_tick()
  BarAggregator
      ↓  bougie H1 fermée
  signal_gen.prepare() + generate_signals()
      ↓  list[Signal] (Python pur, pas de JSON)
  LiveEngine.receive_signal(signal)
      ↓
  OrderDispatcher → Guards → multi-brokers

Rupture clé avec l'ancien système :
  - Pas de webhook HTTP
  - Pas de round-trip JSON
  - Pas de dépendance à TradingView
  - Pas d'import de ctrader_open_api ici (les ticks arrivent déjà décodés
    via PriceTick depuis le broker cTrader)

Chargement historique :
  Au démarrage, le BarAggregator demande les 250 dernières barres H1
  via broker.get_history() pour préchauffer les indicateurs (EMA 200,
  ATR, BB). Sans ces 200+ barres, les signaux seraient invalides.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import pandas as pd

from arabesque.core.models import Signal

logger = logging.getLogger("arabesque.live.bar_aggregator")

# Nombre minimum de barres H1 pour que les indicateurs soient valides
# EMA200 a besoin d'au moins 200 barres + warmup
MIN_BARS_FOR_SIGNAL = 210

# Taille maximale du cache de barres par instrument
BAR_CACHE_MAX = 300

# Période de la bougie (secondes) — 1H = 3600s
BAR_PERIOD_S = 3600


@dataclass
class BarAggregatorConfig:
    instruments: list[str] = field(default_factory=list)
    timeframe_s: int = BAR_PERIOD_S       # 3600 = 1H
    history_bars: int = 250               # barres à précharger au démarrage
    min_bars: int = MIN_BARS_FOR_SIGNAL   # minimum pour générer des signaux
    signal_strategy: str = "trend"         # "mean_reversion", "trend", "combined" — v3.3: trend-only validé


class BarAggregator:
    """
    Agrège les ticks en barres OHLCV et déclenche la génération de signaux.

    Deux sources de barres :
    1. Historique : chargé au démarrage via broker.get_history()
       pour préchauffer EMA200 + ATR + BB.
    2. Live : construit tick par tick via on_tick().
       Quand une bougie se ferme, on_bar_closed() est appelée.
    """

    def __init__(
        self,
        config: BarAggregatorConfig,
        on_signal: Callable,       # async def on_signal(signal: Signal)
        broker=None,               # pour get_history() au démarrage
    ):
        self.cfg = config
        self.on_signal = on_signal
        self.broker = broker

        # Cache de barres fermées par instrument
        self._bar_cache: Dict[str, List[dict]] = {
            inst: [] for inst in config.instruments
        }

        # Bougie en cours de construction (tick par tick)
        self._current_bar: Dict[str, Optional[dict]] = {
            inst: None for inst in config.instruments
        }

        # Timestamp de début de la bougie courante
        self._current_bar_start: Dict[str, int] = {}
        # Guard contre les fermetures dupliquées (race condition async)
        self._last_closed_ts: Dict[str, int] = {}

        # Générateur de signaux
        self._sig_gen = self._make_signal_generator()

        # Statistiques
        self._bars_closed: Dict[str, int] = {k: 0 for k in config.instruments}
        self._signals_emitted: int = 0

        # Batch summary : accumule les fermetures pour un résumé groupé
        self._batch_bars_closed: int = 0
        self._batch_signals: int = 0
        self._batch_start_time: Optional[float] = None
        self._BATCH_WINDOW_S = 120  # 2 min pour grouper les fermetures

        # Callbacks additionnels appelés à chaque fermeture de bougie
        # signature: async def callback(symbol, high, low, close)
        self._bar_closed_callbacks: List[Callable] = []

    def add_bar_closed_callback(self, callback: Callable):
        """Ajoute un callback appelé à chaque fermeture de bougie H1."""
        self._bar_closed_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Précharge l'historique pour chaque instrument.
        À appeler avant de brancher on_tick() au price feed.

        IMPORTANT: le chargement est séquentiel car le broker cTrader utilise
        une seule connexion TCP. Les requêtes parallèles provoquent des timeouts
        et des erreurs InvalidStateError sur les futures.
        """
        if not self.broker:
            logger.warning(
                "[BarAggregator] Pas de broker pour le préchargement historique — "
                f"les signaux ne seront disponibles qu'après {MIN_BARS_FOR_SIGNAL} barres live"
            )
            return

        n_instruments = len(self.cfg.instruments)
        tf_label = self._timeframe_label()
        logger.info(
            f"[BarAggregator] Préchargement de {self.cfg.history_bars} barres {tf_label} "
            f"pour {n_instruments} instrument(s)..."
        )

        loaded = 0
        failed = 0
        for i, instrument in enumerate(self.cfg.instruments):
            try:
                await self._load_history(instrument)
                if self._bar_cache.get(instrument):
                    loaded += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"[BarAggregator] {instrument}: {e}")
                failed += 1

            # Petit délai entre les requêtes pour ne pas saturer la connexion cTrader
            if i < n_instruments - 1:
                await asyncio.sleep(0.15)

            # Log de progression tous les 10 instruments
            if (i + 1) % 10 == 0:
                logger.info(
                    f"[BarAggregator] Progression: {i + 1}/{n_instruments} "
                    f"({loaded} chargés, {failed} échoués)"
                )

        logger.info(
            f"[BarAggregator] Préchargement terminé: "
            f"{loaded}/{n_instruments} instrument(s) chargés"
            + (f", {failed} échoué(s)" if failed else "")
        )

    def _timeframe_label(self) -> str:
        """Label lisible pour le timeframe configuré."""
        mapping = {60: "M1", 300: "M5", 900: "M15", 1800: "M30",
                   3600: "H1", 14400: "H4", 86400: "D1"}
        return mapping.get(self.cfg.timeframe_s, f"{self.cfg.timeframe_s}s")

    async def _load_history(self, instrument: str) -> None:
        """Charge l'historique depuis le broker au timeframe configuré."""
        tf_label = self._timeframe_label()
        try:
            bars = await self.broker.get_history(
                symbol=instrument,
                timeframe=tf_label,
                count=self.cfg.history_bars,
            )
            if bars:
                # bars : list[dict] avec clés ts, open, high, low, close, volume
                self._bar_cache[instrument] = bars[-BAR_CACHE_MAX:]
                if bars:
                    # Initialiser le timestamp de la dernière bougie connue
                    self._current_bar_start[instrument] = bars[-1]["ts"]
                logger.debug(
                    f"[BarAggregator] {instrument}: {len(bars)} barres historiques chargées"
                )
            else:
                logger.warning(f"[BarAggregator] {instrument}: historique vide")
        except Exception as e:
            logger.error(f"[BarAggregator] _load_history({instrument}): {e}")

    # ------------------------------------------------------------------
    # Traitement des ticks
    # ------------------------------------------------------------------

    async def on_tick(self, tick) -> None:
        """
        Callback branché sur PriceFeedManager.subscribe().
        Appelé à chaque PriceTick reçu.

        Logique :
        - Calcule le timestamp de début de la bougie courante (arrondi à l'heure)
        - Si c'est une nouvelle période : ferme la bougie précédente, en ouvre une nouvelle
        - Met à jour la bougie courante (high, low, close)
        """
        sym = tick.symbol
        if sym not in self._bar_cache:
            return

        # Prix mid pour la construction de la bougie
        mid = tick.mid if tick.mid and tick.mid > 0 else (tick.bid + tick.ask) / 2
        ts = tick.timestamp
        if ts is None:
            ts = datetime.now(timezone.utc)

        # Timestamp de début de la bougie courante (arrondi à l'heure)
        bar_start_ts = int(ts.timestamp()) // self.cfg.timeframe_s * self.cfg.timeframe_s

        current_start = self._current_bar_start.get(sym)

        if current_start is None:
            # Première tick reçu : initialiser
            self._current_bar_start[sym] = bar_start_ts
            self._current_bar[sym] = self._new_bar(bar_start_ts, mid)
            return

        if bar_start_ts > current_start:
            # Nouvelle période : fermer la bougie précédente
            # Guard contre la race condition async : si déjà fermée, skip
            last_closed = self._last_closed_ts.get(sym, 0)
            if current_start <= last_closed:
                # Déjà fermée par un tick concurrent — juste mettre à jour
                self._current_bar_start[sym] = bar_start_ts
                bar = self._current_bar.get(sym)
                if bar:
                    bar["high"] = max(bar["high"], mid)
                    bar["low"] = min(bar["low"], mid)
                    bar["close"] = mid
                    bar["volume"] = bar.get("volume", 0) + 1
                else:
                    self._current_bar[sym] = self._new_bar(bar_start_ts, mid)
                return

            self._last_closed_ts[sym] = current_start
            closed = self._current_bar.get(sym)
            if closed:
                await self._on_bar_closed(sym, closed)

            # Ouvrir la nouvelle bougie
            self._current_bar_start[sym] = bar_start_ts
            self._current_bar[sym] = self._new_bar(bar_start_ts, mid)
        else:
            # Même période : mettre à jour
            bar = self._current_bar.get(sym)
            if bar:
                bar["high"] = max(bar["high"], mid)
                bar["low"] = min(bar["low"], mid)
                bar["close"] = mid
                bar["volume"] = bar.get("volume", 0) + 1  # compteur de ticks

    @staticmethod
    def _new_bar(ts: int, price: float) -> dict:
        return {
            "ts": ts,
            "open": price, "high": price,
            "low": price, "close": price,
            "volume": 1,
        }

    # ------------------------------------------------------------------
    # Fermeture de bougie
    # ------------------------------------------------------------------

    async def _on_bar_closed(self, instrument: str, bar: dict) -> None:
        """
        Appelé quand une bougie H1 se ferme.
        Ajoute la bougie au cache, génère les signaux.
        """
        ts_dt = datetime.fromtimestamp(bar["ts"], tz=timezone.utc)
        tf_label = self._timeframe_label()
        logger.debug(
            f"[BarAggregator] 🕯️ {instrument} Bar {tf_label} fermée @ {ts_dt.strftime('%H:%M')} UTC "
            f"O={bar['open']:.5f} H={bar['high']:.5f} "
            f"L={bar['low']:.5f} C={bar['close']:.5f} "
            f"vol={bar.get('volume', 0)} ticks"
        )

        # Ajouter au cache
        cache = self._bar_cache[instrument]
        cache.append(bar)
        if len(cache) > BAR_CACHE_MAX:
            cache.pop(0)

        self._bars_closed[instrument] = self._bars_closed.get(instrument, 0) + 1

        # Batch tracking : accumule pour résumé groupé
        now = time.time()
        if self._batch_start_time is None or (now - self._batch_start_time) > self._BATCH_WINDOW_S:
            # Nouveau batch (ou premier) — log le résumé du batch précédent
            if self._batch_bars_closed > 0:
                logger.info(
                    f"[BarAggregator] ✅ Résumé: {self._batch_bars_closed} barre(s) fermée(s), "
                    f"{self._batch_signals} signal(s) émis"
                )
            self._batch_bars_closed = 0
            self._batch_signals = 0
            self._batch_start_time = now
        self._batch_bars_closed += 1

        # Notifier les callbacks de fermeture de bougie (position monitor etc.)
        for cb in self._bar_closed_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(instrument, bar["high"], bar["low"], bar["close"])
                else:
                    cb(instrument, bar["high"], bar["low"], bar["close"])
            except Exception as e:
                logger.warning(f"[BarAggregator] bar_closed callback error: {e}")

        # Vérifier qu'on a assez de barres pour les indicateurs
        if len(cache) < self.cfg.min_bars:
            logger.debug(
                f"[BarAggregator] {instrument}: {len(cache)}/{self.cfg.min_bars} barres "
                f"(préchauffe en cours)"
            )
            return

        # Générer les signaux
        await self._generate_and_emit(instrument, cache)

    async def _generate_and_emit(self, instrument: str, bars: List[dict]) -> None:
        """
        Construit le DataFrame, calcule les indicateurs, émet les signaux.
        Toujours en live_mode=True : on travaille sur la dernière bougie fermée.
        """
        try:
            df = pd.DataFrame(bars)
            df["timestamp"] = pd.to_datetime(df["ts"], unit="s", utc=True)
            df = df.set_index("timestamp").sort_index()
            df = df.rename(columns={
                "open": "Open", "high": "High",
                "low": "Low", "close": "Close", "volume": "Volume",
            })

            # Calculer les indicateurs
            df = self._sig_gen.prepare(df)

            # Générer — live_mode=True : inclut la dernière bougie
            all_signals = self._sig_gen.generate_signals(df, instrument)

            # En live on ne prend que le signal de la dernière bougie
            last_idx = len(df) - 1
            new_signals = [(i, s) for i, s in all_signals if i == last_idx]

            if not new_signals:
                logger.debug(
                    f"[BarAggregator] {instrument}: pas de signal (dernière bougie)"
                )

            for _, signal in new_signals:
                self._signals_emitted += 1
                self._batch_signals += 1
                logger.info(
                    f"[BarAggregator] 📈 Signal {instrument} {signal.side.value} "
                    f"close={signal.close:.5f} sl={signal.sl:.5f} "
                    f"tp={signal.tp_indicative:.5f} rr={signal.rr:.2f} "
                    f"rsi={signal.rsi:.1f} wr={signal.wr_14:.1f} "
                    f"div={signal.rsi_div:+d} strat={signal.strategy_type}"
                )
                # Envoyer directement au LiveEngine (pas de JSON, pas de webhook)
                if asyncio.iscoroutinefunction(self.on_signal):
                    await self.on_signal(signal)
                else:
                    self.on_signal(signal)

        except Exception as e:
            logger.error(f"[BarAggregator] _generate_and_emit({instrument}): {e}")

    # ------------------------------------------------------------------
    # Générateur de signaux
    # ------------------------------------------------------------------

    def _make_signal_generator(self):
        """Instancie le générateur selon la stratégie configurée."""
        strategy = self.cfg.signal_strategy
        if strategy == "mean_reversion":
            from arabesque.strategies.extension.signal import ExtensionSignalGenerator as BacktestSignalGenerator, ExtensionConfig as SignalGenConfig
            return BacktestSignalGenerator(SignalGenConfig(), live_mode=True)
        elif strategy == "trend":
            from arabesque.strategies.extension.signal import ExtensionSignalGenerator as TrendSignalGenerator, ExtensionConfig as TrendSignalConfig
            return TrendSignalGenerator(TrendSignalConfig())
        else:  # combined
            # abandoned: from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
            return CombinedSignalGenerator()  # live_mode non supporté par CombinedSignalGenerator

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        return {
            "instruments": self.cfg.instruments,
            "bars_closed": dict(self._bars_closed),
            "signals_emitted": self._signals_emitted,
            "cache_sizes": {k: len(v) for k, v in self._bar_cache.items()},
        }
