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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import pandas as pd

from arabesque.models import Signal

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

        # Générateur de signaux
        self._sig_gen = self._make_signal_generator()

        # Statistiques
        self._bars_closed: Dict[str, int] = {k: 0 for k in config.instruments}
        self._signals_emitted: int = 0

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Précharge l'historique pour chaque instrument.
        À appeler avant de brancher on_tick() au price feed.
        Charge en parallèle avec une concurrence limitée pour ne pas
        surcharger le broker.
        """
        if not self.broker:
            logger.warning(
                "[BarAggregator] Pas de broker pour le préchargement historique — "
                f"les signaux ne seront disponibles qu'après {MIN_BARS_FOR_SIGNAL} barres live"
            )
            return

        logger.info(
            f"[BarAggregator] Préchargement de {self.cfg.history_bars} barres H1 "
            f"pour {len(self.cfg.instruments)} instrument(s)..."
        )

        # Charger en parallèle par lots de 5 pour limiter la charge
        sem = asyncio.Semaphore(5)

        async def _load_with_sem(instrument):
            async with sem:
                await self._load_history(instrument)

        await asyncio.gather(
            *[_load_with_sem(inst) for inst in self.cfg.instruments],
            return_exceptions=True,
        )

        loaded = sum(1 for inst in self.cfg.instruments if self._bar_cache.get(inst))
        logger.info(
            f"[BarAggregator] Préchargement terminé: "
            f"{loaded}/{len(self.cfg.instruments)} instrument(s) chargés"
        )

    async def _load_history(self, instrument: str) -> None:
        """Charge l'historique H1 depuis le broker et l'ajoute au cache."""
        try:
            bars = await self.broker.get_history(
                symbol=instrument,
                timeframe="H1",
                count=self.cfg.history_bars,
            )
            if bars:
                # bars : list[dict] avec clés ts, open, high, low, close, volume
                self._bar_cache[instrument] = bars[-BAR_CACHE_MAX:]
                if bars:
                    # Initialiser le timestamp de la dernière bougie connue
                    self._current_bar_start[instrument] = bars[-1]["ts"]
                logger.info(
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
        logger.debug(
            f"[BarAggregator] {instrument} Bar fermée @ {ts_dt.isoformat()} "
            f"O={bar['open']:.5f} H={bar['high']:.5f} "
            f"L={bar['low']:.5f} C={bar['close']:.5f}"
        )

        # Ajouter au cache
        cache = self._bar_cache[instrument]
        cache.append(bar)
        if len(cache) > BAR_CACHE_MAX:
            cache.pop(0)

        self._bars_closed[instrument] = self._bars_closed.get(instrument, 0) + 1

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

            for _, signal in new_signals:
                self._signals_emitted += 1
                logger.info(
                    f"[BarAggregator] 📈 Signal {instrument} {signal.side.value} "
                    f"close={signal.close:.5f} sl={signal.sl:.5f} "
                    f"tp={signal.tp_indicative:.5f} rr={signal.rr:.2f} "
                    f"rsi={signal.rsi:.1f} strat={signal.strategy_type}"
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
            from arabesque.backtest.signal_gen import BacktestSignalGenerator, SignalGenConfig
            return BacktestSignalGenerator(SignalGenConfig(), live_mode=True)
        elif strategy == "trend":
            from arabesque.backtest.signal_gen_trend import TrendSignalGenerator, TrendSignalConfig
            return TrendSignalGenerator(TrendSignalConfig(), live_mode=True)
        else:  # combined
            from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
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
