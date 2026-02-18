"""
Arabesque — Live Bar Poller.

Remplace le webhook TradingView par un flux cTrader natif.

Flux :
    cTrader ProtoOASubscribeLiveTrendbarReq (H1)
        → bougie fermée
        → SignalGenerator (même code que backtest)
        → Orchestrator.handle_signal()   (guards + sizing + ordre)
        → Orchestrator.update_positions() (trailing + exits)

Utilisation :
    from arabesque.live.bar_poller import BarPoller, BarPollerConfig
    from arabesque.webhook.orchestrator import Orchestrator
    from arabesque.broker.ctrader import CTraderAdapter, CTraderConfig
    from arabesque.config import ArabesqueConfig

    ctrader = CTraderAdapter(CTraderConfig(
        client_id="...", client_secret="...",
        access_token="...", account_id=12345,
    ))
    orchestrator = Orchestrator(
        config=ArabesqueConfig(mode="dry_run", ...),
        brokers={"ctrader": ctrader},
    )
    poller = BarPoller(
        ctrader_adapter=ctrader,
        orchestrator=orchestrator,
        instruments=["ALGUSD", "XTZUSD", "BCHUSD", ...],
    )
    poller.start()   # bloquant — utiliser un thread ou systemd

Dépendances :
    pip install ctrader-open-api twisted
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger("arabesque.live.bar_poller")


# ── Instruments viables issus du dernier pipeline run ──────────────────────
DEFAULT_INSTRUMENTS = [
    "AAVUSD", "ALGUSD", "AVAUSD", "BCHUSD", "BNBUSD",
    "DASHUSD", "GRTUSD", "ICPUSD", "IMXUSD", "LNKUSD",
    "NEOUSD", "NERUSD", "SOLUSD", "UNIUSD", "VECUSD",
    "XAUUSD", "XLMUSD", "XRPUSD", "XTZUSD",
]

# Instruments disponibles sur Goat Funded Trader (subset)
GOAT_INSTRUMENTS = ["BCHUSD", "BNBUSD", "SOLUSD"]


@dataclass
class BarPollerConfig:
    """Configuration du BarPoller."""
    instruments: list[str] = field(default_factory=lambda: list(DEFAULT_INSTRUMENTS))
    timeframe: str = "H1"                 # Seul timeframe supporté pour l'instant
    reconnect_delay: float = 30.0         # secondes avant reconnexion
    max_reconnect_attempts: int = 10
    signal_strategy: str = "combined"     # stratégie SignalGenerator
    dry_run: bool = True                  # True = paper trading, False = live
    # Polling fallback (si le stream live ne fonctionne pas)
    use_polling_fallback: bool = True
    poll_interval_sec: int = 60           # vérification toutes les 60s


class BarPoller:
    """
    Souscrit aux barres 1H cTrader et déclenche le pipeline Arabesque
    à chaque fermeture de bougie.

    Deux modes de réception des barres :
    1. **Stream natif** : ProtoOASubscribeLiveTrendbarReq → callback spotEvent
       C'est le mode préféré — réactif à la milliseconde.
    2. **Polling fallback** : vérification toutes les `poll_interval_sec`
       secondes si une nouvelle bougie s'est fermée depuis la dernière.
       Activé si le stream ne répond pas ou si `use_polling_fallback=True`.
    """

    def __init__(
        self,
        ctrader_adapter,
        orchestrator,
        config: BarPollerConfig | None = None,
        on_bar_closed: Callable | None = None,
    ):
        """
        Args:
            ctrader_adapter: Instance connectée de CTraderAdapter
            orchestrator: Instance de Orchestrator
            config: BarPollerConfig (defaults si None)
            on_bar_closed: callback optionnel(instrument, bar) appelé après
                           handle_signal, utile pour les tests
        """
        self.adapter = ctrader_adapter
        self.orchestrator = orchestrator
        self.cfg = config or BarPollerConfig()
        self.on_bar_closed = on_bar_closed

        self._running = False
        self._reconnect_count = 0
        self._lock = threading.Lock()

        # Dernière bougie vue par instrument : ts (epoch seconds arrondi à l'heure)
        self._last_bar_ts: dict[str, int] = {}

        # Cache des dernières barres reçues : instrument → list[OHLCV]
        # Taille fenêtre = ce que SignalGenerator a besoin (≥ 200 bougies)
        self._bar_cache: dict[str, list[dict]] = {
            inst: [] for inst in self.cfg.instruments
        }
        self._cache_max_size = 300

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self, blocking: bool = True):
        """
        Démarre le poller.

        Si blocking=True (défaut), bloque le thread courant.
        Si blocking=False, démarre dans un thread daemon.
        """
        self._running = True
        logger.info(
            f"BarPoller starting | mode={'DRY_RUN' if self.cfg.dry_run else 'LIVE'} "
            f"| instruments={len(self.cfg.instruments)} "
            f"| {self.cfg.instruments}"
        )

        if blocking:
            self._run_loop()
        else:
            t = threading.Thread(target=self._run_loop, daemon=True, name="bar-poller")
            t.start()

    def stop(self):
        """Arrête proprement le poller."""
        logger.info("BarPoller stopping...")
        self._running = False

    # ── Main loop ───────────────────────────────────────────────────────────

    def _run_loop(self):
        """Boucle principale avec reconnexion automatique."""
        while self._running:
            try:
                self._connect_and_subscribe()
            except Exception as e:
                self._reconnect_count += 1
                logger.error(
                    f"BarPoller error (attempt {self._reconnect_count}): {e}"
                )
                if self._reconnect_count >= self.cfg.max_reconnect_attempts:
                    logger.critical("Max reconnect attempts reached. Stopping.")
                    self._running = False
                    break
                logger.info(f"Reconnecting in {self.cfg.reconnect_delay}s...")
                time.sleep(self.cfg.reconnect_delay)

    def _connect_and_subscribe(self):
        """
        1. Charge l'historique H1 (seed du cache)
        2. Souscrit aux trendbar live
        3. Si fallback activé, lance aussi le polling
        """
        # S'assurer que l'adapter est connecté
        if not getattr(self.adapter, '_connected', False):
            ok = self.adapter.connect()
            if not ok:
                raise ConnectionError("CTraderAdapter.connect() failed")

        # Résoudre les symbol IDs
        self._resolve_symbol_ids()

        # Seed : charger l'historique H1 pour chaque instrument
        for instrument in self.cfg.instruments:
            self._load_history(instrument)

        # Souscrire au stream live H1
        self._subscribe_live_trendbars()

        # Fallback polling (au cas où le stream manque une bougie)
        if self.cfg.use_polling_fallback:
            self._run_polling_fallback()
        else:
            # Bloquer en attendant les events du reactor Twisted
            while self._running:
                time.sleep(1)

    # ── Symbol resolution ───────────────────────────────────────────────────

    def _resolve_symbol_ids(self):
        """S'assure que les symboles sont chargés dans l'adapter."""
        symbols = getattr(self.adapter, '_symbols', {})
        if not symbols:
            logger.warning("No symbols loaded in adapter — trying _load_symbols()")
            if hasattr(self.adapter, '_load_symbols'):
                self.adapter._load_symbols()
            symbols = getattr(self.adapter, '_symbols', {})

        missing = [i for i in self.cfg.instruments if i not in symbols]
        if missing:
            logger.warning(f"Symbols not found in cTrader: {missing}")
            # Retirer les instruments non trouvés pour ne pas bloquer
            self.cfg.instruments = [i for i in self.cfg.instruments if i in symbols]
            logger.info(f"Active instruments after filter: {self.cfg.instruments}")

    # ── History seed ────────────────────────────────────────────────────────

    def _load_history(self, instrument: str):
        """
        Charge les dernières 250 bougies H1 via ProtoOAGetTrendbarsReq.
        Nécessaire pour que SignalGenerator ait assez de données
        pour calculer EMA200, ATR, etc.
        """
        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAGetTrendbarsReq,
            )
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
                ProtoOATrendbarPeriod,
            )

            sym_info = self.adapter._symbols.get(instrument)
            if not sym_info:
                logger.warning(f"_load_history: {instrument} not in symbols")
                return

            symbol_id = sym_info["symbolId"]
            pip_pos = sym_info.get("pipPosition", 4)
            divisor = 10 ** pip_pos

            # 250 bougies H1 = ~10 jours
            req = Protobuf.extract(
                ProtoOAGetTrendbarsReq(
                    ctidTraderAccountId=self.adapter.cfg.account_id,
                    symbolId=symbol_id,
                    period=ProtoOATrendbarPeriod.H1,
                    count=250,
                )
            )
            resp = self.adapter._send_and_wait(req, timeout=15.0)

            if resp and hasattr(resp, "trendbar"):
                bars = []
                for tb in resp.trendbar:
                    ts = tb.utcTimestampInMinutes * 60  # → epoch seconds
                    bar = {
                        "ts": ts,
                        "open":  tb.open / divisor  if hasattr(tb, 'open')  else 0,
                        "high":  (tb.open + tb.high) / divisor if hasattr(tb, 'high') else 0,
                        "low":   (tb.open + tb.low)  / divisor if hasattr(tb, 'low')  else 0,
                        "close": (tb.open + tb.deltaClose) / divisor if hasattr(tb, 'deltaClose') else 0,
                        "volume": tb.volume if hasattr(tb, 'volume') else 0,
                    }
                    bars.append(bar)

                # Trier par timestamp croissant
                bars.sort(key=lambda b: b["ts"])
                self._bar_cache[instrument] = bars[-self._cache_max_size:]

                if bars:
                    self._last_bar_ts[instrument] = bars[-1]["ts"]

                logger.info(
                    f"[{instrument}] History loaded: {len(bars)} H1 bars "
                    f"(last: {datetime.fromtimestamp(bars[-1]['ts'], tz=timezone.utc).isoformat() if bars else 'N/A'})"
                )
            else:
                logger.warning(f"[{instrument}] No history data received")

        except Exception as e:
            logger.error(f"_load_history({instrument}) error: {e}")

    # ── Live stream subscription ─────────────────────────────────────────────

    def _subscribe_live_trendbars(self):
        """
        Souscrit aux trendbar live H1 pour tous les instruments.
        Le callback est déclenché par le reactor Twisted à chaque tick
        puis à la fermeture de la bougie.
        """
        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOASubscribeLiveTrendbarReq,
            )
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
                ProtoOATrendbarPeriod,
            )

            for instrument in self.cfg.instruments:
                sym_info = self.adapter._symbols.get(instrument)
                if not sym_info:
                    continue

                symbol_id = sym_info["symbolId"]

                req = Protobuf.extract(
                    ProtoOASubscribeLiveTrendbarReq(
                        ctidTraderAccountId=self.adapter.cfg.account_id,
                        symbolId=symbol_id,
                        period=ProtoOATrendbarPeriod.H1,
                    )
                )

                # Enregistrer le callback trendbar
                def make_callback(inst):
                    def _on_trendbar(msg):
                        self._on_live_trendbar(inst, msg)
                    return _on_trendbar

                self.adapter._client.setMessageCallback(
                    make_callback(instrument)
                )
                self.adapter._send_and_wait(req, timeout=5.0)
                logger.info(f"[{instrument}] Subscribed to live H1 trendbars")

        except Exception as e:
            logger.error(f"_subscribe_live_trendbars error: {e}")

    def _on_live_trendbar(self, instrument: str, msg):
        """
        Callback déclenché par le reactor Twisted à chaque mise à jour
        de trendbar.

        cTrader envoie :
        - Des mises à jour intermédiaires (bougie en cours)
        - Un message final quand la bougie se ferme

        On détecte la fermeture par changement de timestamp.
        """
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOASpotEvent,
                ProtoOAGetTrendbarsRes,
            )

            if not hasattr(msg, 'trendbar') or not msg.trendbar:
                return

            sym_info = self.adapter._symbols.get(instrument)
            if not sym_info:
                return

            pip_pos = sym_info.get("pipPosition", 4)
            divisor = 10 ** pip_pos

            for tb in msg.trendbar:
                ts = tb.utcTimestampInMinutes * 60
                last_ts = self._last_bar_ts.get(instrument, 0)

                # Nouvelle bougie = timestamp différent du dernier connu
                if ts > last_ts and last_ts > 0:
                    bar = {
                        "ts": last_ts,  # On traite la bougie FERMÉE (ts précédent)
                        "open":  tb.open / divisor if hasattr(tb, 'open') else 0,
                        "high":  (tb.open + tb.high) / divisor if hasattr(tb, 'high') else 0,
                        "low":   (tb.open + tb.low)  / divisor if hasattr(tb, 'low')  else 0,
                        "close": (tb.open + tb.deltaClose) / divisor if hasattr(tb, 'deltaClose') else 0,
                        "volume": tb.volume if hasattr(tb, 'volume') else 0,
                    }
                    self._last_bar_ts[instrument] = ts
                    self._on_bar_closed(instrument, bar)
                elif ts != last_ts:
                    # Premier bar reçu
                    self._last_bar_ts[instrument] = ts

        except Exception as e:
            logger.error(f"_on_live_trendbar({instrument}) error: {e}")

    # ── Polling fallback ─────────────────────────────────────────────────────

    def _run_polling_fallback(self):
        """
        Fallback : toutes les `poll_interval_sec` secondes, vérifie si
        une nouvelle bougie H1 s'est fermée depuis la dernière vue.

        Comparaison par epoch timestamp arrondi à l'heure :
            current_hour_ts = now - (now % 3600)
        """
        logger.info(
            f"Polling fallback active (interval={self.cfg.poll_interval_sec}s)"
        )
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                logger.error(f"Polling error: {e}")
            time.sleep(self.cfg.poll_interval_sec)

    def _poll_once(self):
        """Une passe de polling : récupère la dernière bougie fermée H1."""
        now_ts = int(time.time())
        # Timestamp de la bougie H1 qui vient de se fermer
        closed_bar_ts = now_ts - (now_ts % 3600)

        for instrument in self.cfg.instruments:
            last_ts = self._last_bar_ts.get(instrument, 0)

            # On n'a pas encore vu cette bougie
            if closed_bar_ts > last_ts:
                bar = self._fetch_last_closed_bar(instrument)
                if bar:
                    self._last_bar_ts[instrument] = bar["ts"]
                    self._on_bar_closed(instrument, bar)

    def _fetch_last_closed_bar(self, instrument: str) -> dict | None:
        """
        Récupère la dernière bougie H1 fermée via ProtoOAGetTrendbarsReq(count=2).
        On prend l'avant-dernière (la dernière est en cours de formation).
        """
        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAGetTrendbarsReq,
            )
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
                ProtoOATrendbarPeriod,
            )

            sym_info = self.adapter._symbols.get(instrument)
            if not sym_info:
                return None

            symbol_id = sym_info["symbolId"]
            pip_pos = sym_info.get("pipPosition", 4)
            divisor = 10 ** pip_pos

            req = Protobuf.extract(
                ProtoOAGetTrendbarsReq(
                    ctidTraderAccountId=self.adapter.cfg.account_id,
                    symbolId=symbol_id,
                    period=ProtoOATrendbarPeriod.H1,
                    count=2,  # dernière fermée + en cours
                )
            )
            resp = self.adapter._send_and_wait(req, timeout=10.0)

            if resp and hasattr(resp, "trendbar") and len(resp.trendbar) >= 1:
                # Prendre l'avant-dernière si ≥ 2, sinon la seule disponible
                tb = resp.trendbar[-2] if len(resp.trendbar) >= 2 else resp.trendbar[-1]
                ts = tb.utcTimestampInMinutes * 60
                return {
                    "ts": ts,
                    "open":  tb.open / divisor if hasattr(tb, 'open') else 0,
                    "high":  (tb.open + tb.high) / divisor if hasattr(tb, 'high') else 0,
                    "low":   (tb.open + tb.low)  / divisor if hasattr(tb, 'low')  else 0,
                    "close": (tb.open + tb.deltaClose) / divisor if hasattr(tb, 'deltaClose') else 0,
                    "volume": tb.volume if hasattr(tb, 'volume') else 0,
                }

        except Exception as e:
            logger.error(f"_fetch_last_closed_bar({instrument}) error: {e}")
        return None

    # ── Signal processing ────────────────────────────────────────────────────

    def _on_bar_closed(self, instrument: str, bar: dict):
        """
        Appelé à chaque fermeture de bougie H1.

        1. Met à jour le cache de barres
        2. Génère les signaux via SignalGenerator
        3. Pour chaque signal : orchestrator.handle_signal()
        4. orchestrator.update_positions() (trailing/exits)
        5. Callback optionnel on_bar_closed
        """
        with self._lock:
            ts_dt = datetime.fromtimestamp(bar["ts"], tz=timezone.utc)
            logger.debug(
                f"[{instrument}] Bar closed @ {ts_dt.isoformat()} "
                f"O={bar['open']:.5f} H={bar['high']:.5f} "
                f"L={bar['low']:.5f} C={bar['close']:.5f}"
            )

            # 1. Mettre à jour le cache
            cache = self._bar_cache.setdefault(instrument, [])
            cache.append(bar)
            if len(cache) > self._cache_max_size:
                cache.pop(0)

            # Besoin de suffisamment de barres pour les indicateurs
            if len(cache) < 50:
                logger.debug(
                    f"[{instrument}] Not enough bars yet "
                    f"({len(cache)}/50 minimum)"
                )
                return

            # 2. Générer les signaux
            signals = self._generate_signals(instrument, cache)

            # 3. Traiter chaque signal
            for sig_data in signals:
                result = self.orchestrator.handle_signal(sig_data)
                logger.info(
                    f"[{instrument}] handle_signal → {result.get('status')} "
                    f"({result.get('reason', result.get('position_id', ''))})"
                )

            # 4. Mettre à jour les positions ouvertes
            self.orchestrator.update_positions(
                instrument=instrument,
                high=bar["high"],
                low=bar["low"],
                close=bar["close"],
            )

            # 5. Callback optionnel
            if self.on_bar_closed:
                self.on_bar_closed(instrument, bar)

    def _generate_signals(self, instrument: str, bars: list[dict]) -> list[dict]:
        """
        Appelle le même SignalGenerator que le backtest.

        Construit un DataFrame OHLCV depuis le cache,
        passe dans CombinedSignalGenerator, retourne les signaux
        formatés pour Orchestrator.handle_signal().
        """
        try:
            import pandas as pd
            from arabesque.core.signals import CombinedSignalGenerator

            # Construire DataFrame
            df = pd.DataFrame(bars)
            df["timestamp"] = pd.to_datetime(df["ts"], unit="s", utc=True)
            df = df.set_index("timestamp").sort_index()
            df = df.rename(columns={
                "open": "Open", "high": "High",
                "low": "Low", "close": "Close", "volume": "Volume"
            })

            # Générer le signal sur la dernière bougie
            gen = CombinedSignalGenerator()
            raw = gen.generate(df, instrument)

            if raw is None or raw.get("signal") == 0:
                return []

            # Formater pour handle_signal() (même format que le webhook)
            close = bars[-1]["close"]
            ts_iso = datetime.fromtimestamp(
                bars[-1]["ts"], tz=timezone.utc
            ).isoformat()

            side = "buy" if raw["signal"] > 0 else "sell"
            sl = raw.get("sl", 0)
            tp = raw.get("tp", 0)
            atr = raw.get("atr", 0)

            signal_data = {
                "instrument": instrument,
                "symbol": instrument,
                "side": side,
                "tv_close": close,
                "close": close,
                "sl": sl,
                "tp": tp,
                "atr": atr,
                "ts": ts_iso,
                "strategy": self.cfg.signal_strategy,
                "source": "ctrader_live",
            }

            logger.info(
                f"[{instrument}] Signal: {side.upper()} close={close:.5f} "
                f"SL={sl:.5f} TP={tp:.5f} ATR={atr:.5f}"
            )
            return [signal_data]

        except Exception as e:
            logger.error(f"_generate_signals({instrument}) error: {e}")
            return []
