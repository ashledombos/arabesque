"""
Arabesque — Parquet Clock (dry-run simulator).

Rejoue les barres H1 depuis les parquets locaux, bougie par bougie,
sans connexion cTrader. Permet de tester l'intégralité du pipeline
(Orchestrator, Guards, PositionManager, Audit, notifications).

Usage :
    python -m arabesque.live.runner --mode dry_run --source parquet
    python -m arabesque.live.runner --mode dry_run --source parquet \\
        --start 2025-10-01 --end 2026-01-01 --instruments ALGUSD BCHUSD
"""

from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import pandas as pd

from arabesque.backtest.data import load_ohlc
from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
from arabesque.live.bar_poller import DEFAULT_INSTRUMENTS, _generate_signals_from_cache

logger = logging.getLogger("arabesque.live.parquet_clock")


@dataclass
class ParquetClockConfig:
    instruments: list[str]   = field(default_factory=lambda: list(DEFAULT_INSTRUMENTS))
    start: str | None        = None
    end:   str | None        = None
    replay_speed: float      = 0.0
    min_bars_for_signal: int = 50


class ParquetClock:
    """
    Rejoue les barres H1 depuis les parquets locaux et déclenche
    le même pipeline que BarPoller.

    Toutes les barres de tous les instruments sont fusionnées et
    rejouées dans l'ordre chronologique (multi-instrument).
    """

    def __init__(
        self,
        instruments: list[str] | None = None,
        start: str | None = None,
        end:   str | None = None,
        replay_speed: float = 0.0,
        on_bar_closed: Callable | None = None,
        config: ParquetClockConfig | None = None,
    ):
        if config:
            self.cfg = config
        else:
            self.cfg = ParquetClockConfig(
                instruments=instruments or list(DEFAULT_INSTRUMENTS),
                start=start,
                end=end,
                replay_speed=replay_speed,
            )
        self.on_bar_closed = on_bar_closed
        self._sig_gen  = CombinedSignalGenerator()
        self._bar_cache: dict[str, list[dict]] = {}

    def run(self, orchestrator, blocking: bool = True):
        if blocking:
            self._replay(orchestrator)
        else:
            threading.Thread(
                target=self._replay, args=(orchestrator,),
                daemon=True, name="parquet-clock"
            ).start()

    # ── Internals ────────────────────────────────────────────────────────

    def _replay(self, orchestrator):
        logger.info(
            f"ParquetClock starting | {len(self.cfg.instruments)} instruments "
            f"| {self.cfg.start or 'beginning'} → {self.cfg.end or 'end'} "
            f"| speed={'MAX' if self.cfg.replay_speed == 0 else f'{self.cfg.replay_speed}s/bar'}"
        )

        # 1. Charger les DataFrames
        frames: dict[str, pd.DataFrame] = {}
        for inst in self.cfg.instruments:
            try:
                df = load_ohlc(
                    inst,
                    instrument=inst,
                    start=self.cfg.start,
                    end=self.cfg.end,
                    prefer_parquet=True,
                )
                if df is None or len(df) < self.cfg.min_bars_for_signal + 10:
                    logger.warning(
                        f"[{inst}] Not enough data "
                        f"({len(df) if df is not None else 0} bars), skipping"
                    )
                    continue
                frames[inst] = df
                logger.info(f"[{inst}] Loaded {len(df)} bars")
            except Exception as e:
                logger.error(f"[{inst}] Failed to load: {e}")

        if not frames:
            logger.error("No data loaded, aborting.")
            return

        # 2. Planning chronologique global (multi-instrument)
        events: list[tuple[pd.Timestamp, str, pd.Series]] = []
        for inst, df in frames.items():
            for ts, row in df.iterrows():
                events.append((ts, inst, row))
        events.sort(key=lambda e: e[0])
        logger.info(f"Total events to replay: {len(events):,}")

        # 3. Replay
        n_bars    = 0
        n_signals = 0
        t_start   = time.time()

        for ts, instrument, row in events:
            bar = {
                "ts":     int(ts.timestamp()),
                "open":   float(row["Open"]),
                "high":   float(row["High"]),
                "low":    float(row["Low"]),
                "close":  float(row["Close"]),
                "volume": float(row.get("Volume", 0)),
            }

            # Mettre à jour le cache
            cache = self._bar_cache.setdefault(instrument, [])
            cache.append(bar)
            if len(cache) > 300:
                cache.pop(0)

            if len(cache) < self.cfg.min_bars_for_signal:
                n_bars += 1
                continue

            # Générer les signaux sur la dernière bougie
            signals = _generate_signals_from_cache(
                instrument=instrument,
                bars=cache,
                sig_gen=self._sig_gen,
            )
            n_signals += len(signals)

            for sig_data in signals:
                result = orchestrator.handle_signal(sig_data)
                status = result.get('status', '?')
                detail = result.get('reason', result.get('position_id', ''))
                logger.info(
                    f"[{instrument}] {ts.strftime('%Y-%m-%d %H:%M')} "
                    f"{sig_data['side'].upper()} close={bar['close']:.4f} "
                    f"sl={sig_data['sl']:.4f} rr={sig_data.get('rr', 0):.2f} "
                    f"→ {status} {detail}"
                )

            # Trailing / exits
            orchestrator.update_positions(
                instrument=instrument,
                high=bar["high"],
                low=bar["low"],
                close=bar["close"],
            )

            n_bars += 1
            if self.on_bar_closed:
                self.on_bar_closed(instrument, bar)

            if self.cfg.replay_speed > 0:
                time.sleep(self.cfg.replay_speed)

        elapsed = time.time() - t_start
        logger.info(
            f"ParquetClock replay complete | "
            f"{n_bars:,} bars | {n_signals} signals | {elapsed:.1f}s"
        )

        # Résumé final
        try:
            status = orchestrator.get_status()
            logger.info(
                f"Final account: balance={status['account']['balance']:.0f} "
                f"equity={status['account']['equity']:.0f} "
                f"open_positions={status.get('open_positions', 0)}"
            )
        except Exception:
            pass
