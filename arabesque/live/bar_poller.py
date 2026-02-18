"""
Arabesque — Live Bar Poller.

Remplace le webhook TradingView par un flux cTrader natif.

Flux :
    cTrader ProtoOASubscribeLiveTrendbarReq (H1)
        → bougie fermée
        → CombinedSignalGenerator (même code que backtest)
        → Orchestrator.handle_signal()   (guards + sizing + ordre)
        → Orchestrator.update_positions() (trailing + exits)

En mode dry_run sans credentials cTrader :
    Utiliser ParquetClock (arabesque/live/parquet_clock.py) comme source
    de barres — il rejoue les parquets locaux bougie par bougie.

Utilisation (paper trading depuis parquet) :
    from arabesque.live.parquet_clock import ParquetClock
    clock = ParquetClock(instruments=["ALGUSD", "XTZUSD"])
    clock.run(orchestrator)   # bloquant

Utilisation (live cTrader) :
    from arabesque.live.bar_poller import BarPoller, BarPollerConfig
    poller = BarPoller(ctrader_adapter=adapter, orchestrator=orchestrator)
    poller.start()

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

from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
from arabesque.models import Side

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
    timeframe: str = "H1"
    reconnect_delay: float = 30.0
    max_reconnect_attempts: int = 10
    signal_strategy: str = "combined"
    dry_run: bool = True
    use_polling_fallback: bool = True
    poll_interval_sec: int = 60


def _signal_to_webhook_dict(sig, instrument: str, close: float, atr: float) -> dict:
    """
    Convertit un objet Signal (arabesque.models) en dict
    compatible avec Orchestrator.handle_signal().
    """
    side = "buy" if sig.side == Side.LONG else "sell"
    ts_iso = sig.timestamp.isoformat() if sig.timestamp else datetime.now(timezone.utc).isoformat()
    return {
        "instrument": instrument,
        "symbol":     instrument,
        "side":       side,
        "tv_close":   close,
        "close":      close,
        "sl":         sig.sl if sig.sl else 0.0,
        "tp":         sig.tp if sig.tp else 0.0,
        "atr":        atr,
        "ts":         ts_iso,
        "strategy":   sig.strategy_type or "combined",
        "source":     "ctrader_live",
    }


class BarPoller:
    """
    Souscrit aux barres 1H cTrader et déclenche le pipeline Arabesque
    à chaque fermeture de bougie.

    Deux modes de réception :
    1. Stream natif  : ProtoOASubscribeLiveTrendbarReq → callback Twisted
    2. Polling fallback : vérif toutes les poll_interval_sec secondes
    """

    def __init__(
        self,
        ctrader_adapter,
        orchestrator,
        config: BarPollerConfig | None = None,
        on_bar_closed: Callable | None = None,
    ):
        self.adapter     = ctrader_adapter
        self.orchestrator = orchestrator
        self.cfg         = config or BarPollerConfig()
        self.on_bar_closed = on_bar_closed

        self._running        = False
        self._reconnect_count = 0
        self._lock           = threading.Lock()
        self._last_bar_ts: dict[str, int] = {}
        self._bar_cache: dict[str, list[dict]] = {
            inst: [] for inst in self.cfg.instruments
        }
        self._cache_max_size = 300
        # CombinedSignalGenerator réutilisé pour tous les instruments
        self._sig_gen = CombinedSignalGenerator()

    # ── Public API ────────────────────────────────────────────────────────

    def start(self, blocking: bool = True):
        self._running = True
        logger.info(
            f"BarPoller starting | mode={'DRY_RUN' if self.cfg.dry_run else 'LIVE'} "
            f"| {len(self.cfg.instruments)} instruments"
        )
        if blocking:
            self._run_loop()
        else:
            t = threading.Thread(target=self._run_loop, daemon=True, name="bar-poller")
            t.start()

    def stop(self):
        logger.info("BarPoller stopping...")
        self._running = False

    # ── Main loop ─────────────────────────────────────────────────────────

    def _run_loop(self):
        while self._running:
            try:
                self._connect_and_subscribe()
            except Exception as e:
                self._reconnect_count += 1
                logger.error(f"BarPoller error (attempt {self._reconnect_count}): {e}")
                if self._reconnect_count >= self.cfg.max_reconnect_attempts:
                    logger.critical("Max reconnect attempts reached. Stopping.")
                    self._running = False
                    break
                logger.info(f"Reconnecting in {self.cfg.reconnect_delay}s...")
                time.sleep(self.cfg.reconnect_delay)

    def _connect_and_subscribe(self):
        if not getattr(self.adapter, '_connected', False):
            ok = self.adapter.connect()
            if not ok:
                raise ConnectionError("CTraderAdapter.connect() failed")

        self._resolve_symbol_ids()

        for instrument in self.cfg.instruments:
            self._load_history(instrument)

        self._subscribe_live_trendbars()

        if self.cfg.use_polling_fallback:
            self._run_polling_fallback()
        else:
            while self._running:
                time.sleep(1)

    # ── Symbol resolution ─────────────────────────────────────────────────

    def _resolve_symbol_ids(self):
        symbols = getattr(self.adapter, '_symbols', {})
        if not symbols and hasattr(self.adapter, '_load_symbols'):
            self.adapter._load_symbols()
            symbols = getattr(self.adapter, '_symbols', {})

        missing = [i for i in self.cfg.instruments if i not in symbols]
        if missing:
            logger.warning(f"Symbols not found in cTrader: {missing}")
            self.cfg.instruments = [i for i in self.cfg.instruments if i in symbols]

    # ── History seed ──────────────────────────────────────────────────────

    def _load_history(self, instrument: str):
        """Charge 250 bougies H1 historiques via ProtoOAGetTrendbarsReq."""
        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetTrendbarsReq
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoOATrendbarPeriod

            sym_info = self.adapter._symbols.get(instrument)
            if not sym_info:
                return

            symbol_id = sym_info["symbolId"]
            divisor   = 10 ** sym_info.get("pipPosition", 4)

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
                bars = [self._tb_to_dict(tb, divisor) for tb in resp.trendbar]
                bars.sort(key=lambda b: b["ts"])
                self._bar_cache[instrument] = bars[-self._cache_max_size:]
                if bars:
                    self._last_bar_ts[instrument] = bars[-1]["ts"]
                logger.info(f"[{instrument}] History: {len(bars)} H1 bars loaded")

        except Exception as e:
            logger.error(f"_load_history({instrument}): {e}")

    # ── Live stream ───────────────────────────────────────────────────────

    def _subscribe_live_trendbars(self):
        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASubscribeLiveTrendbarReq
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoOATrendbarPeriod

            for instrument in self.cfg.instruments:
                sym_info = self.adapter._symbols.get(instrument)
                if not sym_info:
                    continue

                req = Protobuf.extract(
                    ProtoOASubscribeLiveTrendbarReq(
                        ctidTraderAccountId=self.adapter.cfg.account_id,
                        symbolId=sym_info["symbolId"],
                        period=ProtoOATrendbarPeriod.H1,
                    )
                )

                def make_cb(inst):
                    def _cb(msg): self._on_live_trendbar(inst, msg)
                    return _cb

                self.adapter._client.setMessageCallback(make_cb(instrument))
                self.adapter._send_and_wait(req, timeout=5.0)
                logger.info(f"[{instrument}] Subscribed to live H1 trendbars")

        except Exception as e:
            logger.error(f"_subscribe_live_trendbars: {e}")

    def _on_live_trendbar(self, instrument: str, msg):
        """Callback Twisted — détecte la fermeture par changement de timestamp."""
        try:
            if not hasattr(msg, 'trendbar') or not msg.trendbar:
                return
            sym_info = self.adapter._symbols.get(instrument)
            if not sym_info:
                return
            divisor = 10 ** sym_info.get("pipPosition", 4)

            for tb in msg.trendbar:
                ts       = tb.utcTimestampInMinutes * 60
                last_ts  = self._last_bar_ts.get(instrument, 0)
                if ts > last_ts and last_ts > 0:
                    # La bougie last_ts vient de se fermer
                    bar = self._tb_to_dict(tb, divisor)
                    bar["ts"] = last_ts
                    self._last_bar_ts[instrument] = ts
                    self._on_bar_closed(instrument, bar)
                elif ts != last_ts:
                    self._last_bar_ts[instrument] = ts
        except Exception as e:
            logger.error(f"_on_live_trendbar({instrument}): {e}")

    # ── Polling fallback ──────────────────────────────────────────────────

    def _run_polling_fallback(self):
        logger.info(f"Polling fallback active (interval={self.cfg.poll_interval_sec}s)")
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                logger.error(f"Polling error: {e}")
            time.sleep(self.cfg.poll_interval_sec)

    def _poll_once(self):
        now_ts = int(time.time())
        closed_bar_ts = now_ts - (now_ts % 3600)
        for instrument in self.cfg.instruments:
            if closed_bar_ts > self._last_bar_ts.get(instrument, 0):
                bar = self._fetch_last_closed_bar(instrument)
                if bar:
                    self._last_bar_ts[instrument] = bar["ts"]
                    self._on_bar_closed(instrument, bar)

    def _fetch_last_closed_bar(self, instrument: str) -> dict | None:
        """Récupère la dernière bougie H1 fermée (count=2, on prend l'avant-dernière)."""
        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetTrendbarsReq
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoOATrendbarPeriod

            sym_info = self.adapter._symbols.get(instrument)
            if not sym_info:
                return None
            divisor = 10 ** sym_info.get("pipPosition", 4)

            req = Protobuf.extract(
                ProtoOAGetTrendbarsReq(
                    ctidTraderAccountId=self.adapter.cfg.account_id,
                    symbolId=sym_info["symbolId"],
                    period=ProtoOATrendbarPeriod.H1,
                    count=2,
                )
            )
            resp = self.adapter._send_and_wait(req, timeout=10.0)
            if resp and hasattr(resp, "trendbar") and resp.trendbar:
                tb = resp.trendbar[-2] if len(resp.trendbar) >= 2 else resp.trendbar[-1]
                return self._tb_to_dict(tb, divisor)
        except Exception as e:
            logger.error(f"_fetch_last_closed_bar({instrument}): {e}")
        return None

    # ── Signal processing ─────────────────────────────────────────────────

    def _on_bar_closed(self, instrument: str, bar: dict):
        """
        Appelé à chaque fermeture de bougie H1 :
        1. Met à jour le cache
        2. CombinedSignalGenerator.generate_signals() sur le DataFrame complet
        3. Pour le signal sur la dernière bougie : orchestrator.handle_signal()
        4. orchestrator.update_positions() (trailing / exits)
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

            if len(cache) < 50:
                logger.debug(f"[{instrument}] Not enough bars ({len(cache)}/50)")
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

            # 4. Mettre à jour les positions
            self.orchestrator.update_positions(
                instrument=instrument,
                high=bar["high"],
                low=bar["low"],
                close=bar["close"],
            )

            if self.on_bar_closed:
                self.on_bar_closed(instrument, bar)

    def _generate_signals(self, instrument: str, bars: list[dict]) -> list[dict]:
        """
        Construit un DataFrame OHLCV depuis le cache, passe dans
        CombinedSignalGenerator, retourne le(s) signal(s) de la dernière
        bougie sous forme de dict compatible Orchestrator.handle_signal().
        """
        try:
            import pandas as pd

            df = pd.DataFrame(bars)
            df["timestamp"] = pd.to_datetime(df["ts"], unit="s", utc=True)
            df = df.set_index("timestamp").sort_index()
            df = df.rename(columns={
                "open": "Open", "high": "High",
                "low":  "Low",  "close": "Close", "volume": "Volume"
            })

            # Préparer les indicateurs (EMA, BB, ATR, RSI, CMF...)
            df = self._sig_gen.prepare(df)

            # Générer tous les signaux sur tout le DataFrame
            all_signals = self._sig_gen.generate_signals(df, instrument)
            # → liste de (bar_index, Signal)

            if not all_signals:
                return []

            # Ne garder que le signal sur la toute dernière bougie
            last_idx = len(df) - 1
            last_signals = [(i, s) for i, s in all_signals if i == last_idx]

            if not last_signals:
                return []

            close = bars[-1]["close"]
            # ATR de la dernière bougie si disponible
            atr = float(df["atr"].iloc[-1]) if "atr" in df.columns else 0.0

            result = []
            for _, sig in last_signals:
                result.append(_signal_to_webhook_dict(sig, instrument, close, atr))
                logger.info(
                    f"[{instrument}] Signal: {sig.side.value} "
                    f"close={close:.5f} SL={sig.sl:.5f} strategy={sig.strategy_type}"
                )
            return result

        except Exception as e:
            logger.error(f"_generate_signals({instrument}): {e}")
            return []

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _tb_to_dict(tb, divisor: float) -> dict:
        """Convertit un ProtoOA Trendbar en dict OHLCV."""
        ts    = tb.utcTimestampInMinutes * 60
        open_ = tb.open / divisor if hasattr(tb, 'open') else 0.0
        high  = (tb.open + tb.high)       / divisor if hasattr(tb, 'high')       else open_
        low   = (tb.open + tb.low)        / divisor if hasattr(tb, 'low')        else open_
        close = (tb.open + tb.deltaClose) / divisor if hasattr(tb, 'deltaClose') else open_
        return {
            "ts":     ts,
            "open":   open_,
            "high":   high,
            "low":    low,
            "close":  close,
            "volume": tb.volume if hasattr(tb, 'volume') else 0,
        }
