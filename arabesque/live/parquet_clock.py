"""
Arabesque — Parquet Clock (dry-run simulator).

Permet de tester l'intégralité du pipeline live (Orchestrator, Guards,
PositionManager, Audit, notifications) SANS connexion cTrader et SANS
attendre les vraies heures.

Principe :
    Rejoue les barres H1 des parquets locaux en avance rapide,
    bougie par bougie, comme si elles arrivaient en temps réel.
    La vitesse est configurable (replay_speed=0 → aussi vite que possible).

Usage :
    from arabesque.live.parquet_clock import ParquetClock
    from arabesque.webhook.orchestrator import Orchestrator
    from arabesque.broker.adapters import DryRunAdapter
    from arabesque.config import ArabesqueConfig

    cfg = ArabesqueConfig(mode="dry_run", start_balance=10_000, ...)
    orchestrator = Orchestrator(cfg, brokers={"dry_run": DryRunAdapter()})

    clock = ParquetClock(
        instruments=["ALGUSD", "XTZUSD", "BCHUSD"],
        start="2025-06-01",   # rejouer depuis cette date
        end="2026-01-01",     # jusqu'ici (None = tout le parquet)
        replay_speed=0,       # 0 = aussi vite que possible
    )
    clock.run(orchestrator)   # bloquant

OU via le runner :
    python -m arabesque.live.runner --mode dry_run --source parquet
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from arabesque.backtest.data import load_ohlc
from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
from arabesque.live.bar_poller import DEFAULT_INSTRUMENTS, _signal_to_webhook_dict

logger = logging.getLogger("arabesque.live.parquet_clock")


@dataclass
class ParquetClockConfig:
    instruments: list[str]  = field(default_factory=lambda: list(DEFAULT_INSTRUMENTS))
    start: str | None       = None    # "YYYY-MM-DD" ou None = depuis le début du parquet
    end:   str | None       = None    # "YYYY-MM-DD" ou None = jusqu'à la fin
    replay_speed: float     = 0.0     # secondes entre 2 bougies (0 = max speed)
    min_bars_for_signal: int = 50     # barres minimales avant de générer des signaux


class ParquetClock:
    """
    Rejoue les barres H1 depuis les parquets locaux et déclenche
    le même pipeline que BarPoller (Orchestrator.handle_signal +
    update_positions).

    Toutes les barres de tous les instruments sont fusionnées et
    rejouées dans l'ordre chronologique, comme si elles arrivaient
    en temps réel sur plusieurs instruments en parallèle.
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
        self._sig_gen = CombinedSignalGenerator()
        self._bar_cache: dict[str, list[dict]] = {}

    def run(self, orchestrator, blocking: bool = True):
        """
        Lance le replay.

        Args:
            orchestrator: Instance de Orchestrator
            blocking: Si False, tourne dans un thread daemon
        """
        if blocking:
            self._replay(orchestrator)
        else:
            import threading
            t = threading.Thread(
                target=self._replay, args=(orchestrator,),
                daemon=True, name="parquet-clock"
            )
            t.start()

    # ── Internals ─────────────────────────────────────────────────────────

    def _replay(self, orchestrator):
        """Charge les parquets, fusionne, rejoue bougie par bougie."""
        logger.info(
            f"ParquetClock starting | {len(self.cfg.instruments)} instruments "
            f"| {self.cfg.start or 'beginning'} → {self.cfg.end or 'end'} "
            f"| speed={'MAX' if self.cfg.replay_speed == 0 else f'{self.cfg.replay_speed}s/bar'}"
        )

        # ── 1. Charger tous les DataFrames ──
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
                    logger.warning(f"[{inst}] Not enough data ({len(df) if df is not None else 0} bars), skipping")
                    continue
                frames[inst] = df
                logger.info(f"[{inst}] Loaded {len(df)} bars")
            except Exception as e:
                logger.error(f"[{inst}] Failed to load: {e}")

        if not frames:
            logger.error("No data loaded, aborting.")
            return

        # ── 2. Construire le planning chronologique global ──
        # Chaque entrée : (timestamp, instrument, row)
        events: list[tuple[pd.Timestamp, str, pd.Series]] = []
        for inst, df in frames.items():
            for ts, row in df.iterrows():
                events.append((ts, inst, row))

        events.sort(key=lambda e: e[0])
        logger.info(f"Total events to replay: {len(events):,}")

        # ── 3. Replay ──
        n_bars   = 0
        n_signals = 0
        t_start  = time.time()

        for ts, instrument, row in events:
            bar = {
                "ts":     int(ts.timestamp()),
                "open":   float(row["Open"]),
                "high":   float(row["High"]),
                "low":    float(row["Low"]),
                "close":  float(row["Close"]),
                "volume": float(row.get("Volume", 0)),
            }

            # Mettre à jour le cache de cet instrument
            cache = self._bar_cache.setdefault(instrument, [])
            cache.append(bar)
            if len(cache) > 300:
                cache.pop(0)

            # Attendre d'avoir assez de barres pour les indicateurs
            if len(cache) < self.cfg.min_bars_for_signal:
                continue

            # Générer les signaux sur la dernière bougie
            signals = self._generate_signals(instrument, cache)
            n_signals += len(signals)

            for sig_data in signals:
                result = orchestrator.handle_signal(sig_data)
                logger.info(
                    f"[{instrument}] {ts.isoformat()} "
                    f"→ {sig_data['side'].upper()} "
                    f"→ {result.get('status')} "
                    f"({result.get('reason', result.get('position_id', ''))})"
                )

            # Mettre à jour le trailing / exits
            orchestrator.update_positions(
                instrument=instrument,
                high=bar["high"],
                low=bar["low"],
                close=bar["close"],
            )

            n_bars += 1
            if self.on_bar_closed:
                self.on_bar_closed(instrument, bar)

            # Respecter le replay_speed
            if self.cfg.replay_speed > 0:
                time.sleep(self.cfg.replay_speed)

        elapsed = time.time() - t_start
        logger.info(
            f"ParquetClock replay complete | "
            f"{n_bars:,} bars | {n_signals} signals | {elapsed:.1f}s"
        )

        # Résumé final
        status = orchestrator.get_status()
        logger.info(
            f"Final account: balance={status['account']['balance']:.0f} "
            f"equity={status['account']['equity']:.0f} "
            f"daily_pnl={status['account']['daily_pnl']:.0f}"
        )

    def _generate_signals(self, instrument: str, bars: list[dict]) -> list[dict]:
        """Même logique que BarPoller._generate_signals."""
        try:
            df = pd.DataFrame(bars)
            df["timestamp"] = pd.to_datetime(df["ts"], unit="s", utc=True)
            df = df.set_index("timestamp").sort_index()
            df = df.rename(columns={
                "open": "Open", "high": "High",
                "low":  "Low",  "close": "Close", "volume": "Volume"
            })

            df = self._sig_gen.prepare(df)
            all_signals = self._sig_gen.generate_signals(df, instrument)

            if not all_signals:
                return []

            last_idx = len(df) - 1
            last_signals = [(i, s) for i, s in all_signals if i == last_idx]

            if not last_signals:
                return []

            close = bars[-1]["close"]
            atr   = float(df["atr"].iloc[-1]) if "atr" in df.columns else 0.0

            return [
                _signal_to_webhook_dict(sig, instrument, close, atr)
                for _, sig in last_signals
            ]

        except Exception as e:
            logger.error(f"_generate_signals({instrument}): {e}")
            return []
