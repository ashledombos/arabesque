"""
Arabesque — Live Bar Poller.

Flux :
    cTrader ProtoOASubscribeLiveTrendbarReq (H1)
        → bougie fermée
        → CombinedSignalGenerator (même code que backtest)
        → Orchestrator.handle_signal()   (guards + sizing + ordre)
        → Orchestrator.update_positions() (trailing + exits)

En mode dry_run sans credentials cTrader :
    Utiliser ParquetClock (arabesque/live/parquet_clock.py).

Dépendances : pip install ctrader-open-api twisted
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
from arabesque.models import Side

logger = logging.getLogger("arabesque.live.bar_poller")


DEFAULT_INSTRUMENTS = [
    "AAVUSD", "ALGUSD", "AVAUSD", "BCHUSD", "BNBUSD",
    "DASHUSD", "GRTUSD", "ICPUSD", "IMXUSD", "LNKUSD",
    "NEOUSD", "NERUSD", "SOLUSD", "UNIUSD", "VECUSD",
    "XAUUSD", "XLMUSD", "XRPUSD", "XTZUSD",
]

GOAT_INSTRUMENTS = ["BCHUSD", "BNBUSD", "SOLUSD"]


@dataclass
class BarPollerConfig:
    instruments: list[str] = field(default_factory=lambda: list(DEFAULT_INSTRUMENTS))
    timeframe: str = "H1"
    reconnect_delay: float = 30.0
    max_reconnect_attempts: int = 10
    signal_strategy: str = "combined"
    dry_run: bool = True
    use_polling_fallback: bool = True
    poll_interval_sec: int = 60


def _signal_to_webhook_dict(
    sig,
    instrument: str,
    close: float,
    atr: float,
    df_row: "pd.Series | None" = None,
) -> dict:
    """
    Convertit un objet Signal (arabesque.models) en dict
    compatible avec Signal.from_webhook_json() / Orchestrator.handle_signal().

    Tous les float sont castés en float() natif Python (pas np.float64).

    RR : calculé depuis le close passé en argument (qui doit être
    df.iloc[idx]["Close"], le close AU MOMENT DU SIGNAL, pas le close courant).
    """
    side   = "buy" if sig.side == Side.LONG else "sell"
    ts_iso = sig.timestamp.isoformat() if sig.timestamp else datetime.now(timezone.utc).isoformat()

    # ---- niveaux du signal ----
    sl_val  = float(getattr(sig, "sl",            0.0) or 0.0)
    tp_ind  = float(getattr(sig, "tp_indicative", 0.0) or 0.0)
    atr_sig = float(getattr(sig, "atr",           0.0) or 0.0)

    # close de référence pour le RR = argument `close` (doit être le close du signal)
    sig_close = float(close) if close else float(getattr(sig, "tv_close", 0.0) or 0.0)

    # RR
    if sl_val and tp_ind and sig_close and abs(sig_close - sl_val) > 0:
        rr = abs(tp_ind - sig_close) / abs(sig_close - sl_val)
    else:
        rr = float(getattr(sig, "rr", 0.0) or 0.0)

    # ---- contexte technique : df_row en priorité, Signal en fallback ----
    def _f(col_df, attr_sig):
        """Extrait depuis df_row si disponible, sinon depuis sig. Cast float. NaN guard."""
        if df_row is not None and col_df in df_row.index:
            v = df_row[col_df]
            return float(v) if v == v else float(getattr(sig, attr_sig, 0.0))  # v==v : NaN guard
        return float(getattr(sig, attr_sig, 0.0) or 0.0)

    # ema_slow = EMA200 LTF (le sig gen ne crée pas de colonne "ema200")
    ema200_val = (
        float(df_row["ema200"])    if df_row is not None and "ema200"    in df_row.index else
        float(df_row["ema_slow"]) if df_row is not None and "ema_slow" in df_row.index else
        float(getattr(sig, "ema200_ltf", 0.0) or 0.0)
    )

    atr_val = _f("atr", "atr") or atr_sig or float(atr)

    return {
        "instrument":    instrument,
        "symbol":        instrument,
        "side":          side,
        "ts":            ts_iso,
        "source":        "ctrader_live",
        "strategy":      sig.strategy_type or "combined",
        "tv_close":      float(sig_close),
        "close":         float(sig_close),
        "sl":            sl_val,
        "tp_indicative": tp_ind,
        "atr":           atr_val,
        "rr":            round(float(rr), 3),
        "rsi":           _f("rsi",      "rsi"),
        "cmf":           _f("cmf",      "cmf"),
        "bb_lower":      _f("bb_lower", "bb_lower"),
        "bb_mid":        _f("bb_mid",   "bb_mid"),
        "bb_upper":      _f("bb_upper", "bb_upper"),
        "bb_width":      _f("bb_width", "bb_width"),
        "ema200_ltf":    ema200_val,
    }


class BarPoller:
    """
    Souscrit aux barres 1H cTrader et déclenche le pipeline Arabesque
    à chaque fermeture de bougie.
    """

    def __init__(
        self,
        ctrader_adapter,
        orchestrator,
        config: BarPollerConfig | None = None,
        on_bar_closed: Callable | None = None,
    ):
        self.adapter      = ctrader_adapter
        self.orchestrator = orchestrator
        self.cfg          = config or BarPollerConfig()
        self.on_bar_closed = on_bar_closed
        self._running         = False
        self._reconnect_count = 0
        self._lock            = threading.Lock()
        self._last_bar_ts:  dict[str, int]        = {}
        self._bar_cache:    dict[str, list[dict]] = {
            inst: [] for inst in self.cfg.instruments
        }
        self._cache_max_size = 300
        self._sig_gen = CombinedSignalGenerator()

    def start(self, blocking: bool = True):
        self._running = True
        logger.info(
            f"BarPoller starting | mode={'DRY_RUN' if self.cfg.dry_run else 'LIVE'} "
            f"| {len(self.cfg.instruments)} instruments"
        )
        if blocking:
            self._run_loop()
        else:
            threading.Thread(target=self._run_loop, daemon=True, name="bar-poller").start()

    def stop(self):
        logger.info("BarPoller stopping...")
        self._running = False

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
            if not self.adapter.connect():
                raise ConnectionError("CTraderAdapter.connect() failed")
        self._resolve_symbol_ids()
        for inst in self.cfg.instruments:
            self._load_history(inst)
        self._subscribe_live_trendbars()
        if self.cfg.use_polling_fallback:
            self._run_polling_fallback()
        else:
            while self._running:
                time.sleep(1)

    def _resolve_symbol_ids(self):
        symbols = getattr(self.adapter, '_symbols', {})
        if not symbols and hasattr(self.adapter, '_load_symbols'):
            self.adapter._load_symbols()
            symbols = getattr(self.adapter, '_symbols', {})
        missing = [i for i in self.cfg.instruments if i not in symbols]
        if missing:
            logger.warning(f"Symbols not found in cTrader: {missing}")
            self.cfg.instruments = [i for i in self.cfg.instruments if i in symbols]

    def _load_history(self, instrument: str):
        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetTrendbarsReq
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoOATrendbarPeriod
            sym_info = self.adapter._symbols.get(instrument)
            if not sym_info:
                return
            divisor = 10 ** sym_info.get("pipPosition", 4)
            req = Protobuf.extract(ProtoOAGetTrendbarsReq(
                ctidTraderAccountId=self.adapter.cfg.account_id,
                symbolId=sym_info["symbolId"],
                period=ProtoOATrendbarPeriod.H1,
                count=250,
            ))
            resp = self.adapter._send_and_wait(req, timeout=15.0)
            if resp and hasattr(resp, "trendbar"):
                bars = sorted(
                    [self._tb_to_dict(tb, divisor) for tb in resp.trendbar],
                    key=lambda b: b["ts"]
                )
                self._bar_cache[instrument] = bars[-self._cache_max_size:]
                if bars:
                    self._last_bar_ts[instrument] = bars[-1]["ts"]
                logger.info(f"[{instrument}] History: {len(bars)} H1 bars loaded")
        except Exception as e:
            logger.error(f"_load_history({instrument}): {e}")

    def _subscribe_live_trendbars(self):
        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASubscribeLiveTrendbarReq
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoOATrendbarPeriod
            for instrument in self.cfg.instruments:
                sym_info = self.adapter._symbols.get(instrument)
                if not sym_info:
                    continue
                req = Protobuf.extract(ProtoOASubscribeLiveTrendbarReq(
                    ctidTraderAccountId=self.adapter.cfg.account_id,
                    symbolId=sym_info["symbolId"],
                    period=ProtoOATrendbarPeriod.H1,
                ))
                def make_cb(inst):
                    def _cb(msg): self._on_live_trendbar(inst, msg)
                    return _cb
                self.adapter._client.setMessageCallback(make_cb(instrument))
                self.adapter._send_and_wait(req, timeout=5.0)
                logger.info(f"[{instrument}] Subscribed to live H1 trendbars")
        except Exception as e:
            logger.error(f"_subscribe_live_trendbars: {e}")

    def _on_live_trendbar(self, instrument: str, msg):
        try:
            if not hasattr(msg, 'trendbar') or not msg.trendbar:
                return
            sym_info = self.adapter._symbols.get(instrument)
            if not sym_info:
                return
            divisor = 10 ** sym_info.get("pipPosition", 4)
            for tb in msg.trendbar:
                ts      = tb.utcTimestampInMinutes * 60
                last_ts = self._last_bar_ts.get(instrument, 0)
                if ts > last_ts and last_ts > 0:
                    bar = self._tb_to_dict(tb, divisor)
                    bar["ts"] = last_ts
                    self._last_bar_ts[instrument] = ts
                    self._on_bar_closed(instrument, bar)
                elif ts != last_ts:
                    self._last_bar_ts[instrument] = ts
        except Exception as e:
            logger.error(f"_on_live_trendbar({instrument}): {e}")

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
        try:
            from ctrader_open_api import Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetTrendbarsReq
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoOATrendbarPeriod
            sym_info = self.adapter._symbols.get(instrument)
            if not sym_info:
                return None
            divisor = 10 ** sym_info.get("pipPosition", 4)
            req = Protobuf.extract(ProtoOAGetTrendbarsReq(
                ctidTraderAccountId=self.adapter.cfg.account_id,
                symbolId=sym_info["symbolId"],
                period=ProtoOATrendbarPeriod.H1,
                count=2,
            ))
            resp = self.adapter._send_and_wait(req, timeout=10.0)
            if resp and hasattr(resp, "trendbar") and resp.trendbar:
                tb = resp.trendbar[-2] if len(resp.trendbar) >= 2 else resp.trendbar[-1]
                return self._tb_to_dict(tb, divisor)
        except Exception as e:
            logger.error(f"_fetch_last_closed_bar({instrument}): {e}")
        return None

    def _on_bar_closed(self, instrument: str, bar: dict):
        with self._lock:
            ts_dt = datetime.fromtimestamp(bar["ts"], tz=timezone.utc)
            logger.debug(
                f"[{instrument}] Bar closed @ {ts_dt.isoformat()} "
                f"O={bar['open']:.5f} H={bar['high']:.5f} "
                f"L={bar['low']:.5f} C={bar['close']:.5f}"
            )
            cache = self._bar_cache.setdefault(instrument, [])
            cache.append(bar)
            if len(cache) > self._cache_max_size:
                cache.pop(0)
            if len(cache) < 50:
                logger.debug(f"[{instrument}] Not enough bars ({len(cache)}/50)")
                return
            signals = self._generate_signals(instrument, cache)
            for sig_data in signals:
                result = self.orchestrator.handle_signal(sig_data)
                logger.info(
                    f"[{instrument}] handle_signal → {result.get('status')} "
                    f"({result.get('reason', result.get('position_id', ''))})"
                )
            self.orchestrator.update_positions(
                instrument=instrument,
                high=bar["high"],
                low=bar["low"],
                close=bar["close"],
            )
            if self.on_bar_closed:
                self.on_bar_closed(instrument, bar)

    def _generate_signals(self, instrument: str, bars: list[dict]) -> list[dict]:
        return _generate_signals_from_cache(
            instrument=instrument,
            bars=bars,
            sig_gen=self._sig_gen,
        )

    @staticmethod
    def _tb_to_dict(tb, divisor: float) -> dict:
        ts    = tb.utcTimestampInMinutes * 60
        open_ = tb.open / divisor if hasattr(tb, 'open') else 0.0
        high  = (tb.open + tb.high)       / divisor if hasattr(tb, 'high')       else open_
        low   = (tb.open + tb.low)        / divisor if hasattr(tb, 'low')        else open_
        close = (tb.open + tb.deltaClose) / divisor if hasattr(tb, 'deltaClose') else open_
        return {"ts": ts, "open": open_, "high": high, "low": low, "close": close,
                "volume": tb.volume if hasattr(tb, 'volume') else 0}


# ── Fonction partagée BarPoller + ParquetClock ───────────────────────

def _generate_signals_from_cache(
    instrument: str,
    bars: list[dict],
    sig_gen: CombinedSignalGenerator,
) -> list[dict]:
    """
    Fonction partagée entre BarPoller et ParquetClock.

    Construit un DataFrame OHLCV depuis le cache, applique
    CombinedSignalGenerator, et retourne TOUS les signaux générés
    sous forme de dict compatible Orchestrator.handle_signal().

    CORRECTION 2026-02-19 : retourne TOUS les signaux (pas seulement
    ceux de la dernière bougie). En live (BarPoller), le cache est
    stable et on veut uniquement les signaux de la nouvelle bougie.
    En replay (ParquetClock), le cache change à chaque itération et
    les signaux sont filtrés par la logique de pending queue.
    """
    try:
        df = pd.DataFrame(bars)
        df["timestamp"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        df = df.set_index("timestamp").sort_index()
        df = df.rename(columns={
            "open": "Open", "high": "High",
            "low":  "Low",  "close": "Close", "volume": "Volume"
        })

        df = sig_gen.prepare(df)
        all_signals = sig_gen.generate_signals(df, instrument)
        if not all_signals:
            return []

        # Retourner TOUS les signaux (pas de filtre last_idx)
        result = []
        for idx, sig in all_signals:
            # close AU MOMENT DU SIGNAL (pas le close courant du cache)
            sig_close = float(df.iloc[idx]["Close"])
            df_row = df.iloc[idx]
            atr = float(df["atr"].iloc[idx]) if "atr" in df.columns else 0.0
            d = _signal_to_webhook_dict(sig, instrument, sig_close, atr, df_row=df_row)
            result.append(d)
            logger.info(
                f"[{instrument}] Signal {sig.strategy_type}: {sig.side.value} "
                f"close={d['tv_close']:.5f} sl={d['sl']:.5f} "
                f"tp={d['tp_indicative']:.5f} rr={d['rr']:.2f} rsi={d['rsi']:.1f}"
            )
        return result

    except Exception as e:
        logger.error(f"_generate_signals_from_cache({instrument}): {e}")
        return []
