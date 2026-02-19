"""
Arabesque — Parquet Clock (dry-run simulator).

Rejoue les barres H1 depuis les parquets locaux, bougie par bougie,
sans connexion cTrader.

Comportement :
- Si ``--end`` est fourni : replay borné, résumé automatique en fin de replay.
- Si ``--end`` est absent  : stream infini, Ctrl+C déclenche le résumé et l'arrêt propre.

Le résumé est aussi exporté en JSONL (un enregistrement par trade + une ligne
``summary``) dans ``dry_run_YYYYMMDD_HHMMSS.jsonl``.
"""

from __future__ import annotations

import json
import logging
import math
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from arabesque.backtest.data import load_ohlc
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

    ``signal_generator`` peut être passé explicitement pour sélectionner
    la stratégie. Si omis, ``CombinedSignalGenerator`` est utilisé par défaut.
    """

    def __init__(
        self,
        instruments: list[str] | None = None,
        start: str | None = None,
        end:   str | None = None,
        replay_speed: float = 0.0,
        on_bar_closed: Callable | None = None,
        config: ParquetClockConfig | None = None,
        signal_generator=None,
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

        if signal_generator is not None:
            self._sig_gen = signal_generator
        else:
            from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
            self._sig_gen = CombinedSignalGenerator()

        self._bar_cache: dict[str, list[dict]] = {}

    def run(self, orchestrator, blocking: bool = True):
        if blocking:
            try:
                self._replay(orchestrator)
            except KeyboardInterrupt:
                logger.info("Interrupted by user — generating summary...")
                self._print_summary(orchestrator)
        else:
            def _run():
                try:
                    self._replay(orchestrator)
                except KeyboardInterrupt:
                    logger.info("Interrupted by user — generating summary...")
                    self._print_summary(orchestrator)
            threading.Thread(target=_run, daemon=True, name="parquet-clock").start()

    # ── Internals ────────────────────────────────────────────────

    def _replay(self, orchestrator):
        logger.info(
            f"ParquetClock starting | {len(self.cfg.instruments)} instruments "
            f"| {self.cfg.start or 'beginning'} → {self.cfg.end or '∞ (Ctrl+C to stop)'} "
            f"| speed={'MAX' if self.cfg.replay_speed == 0 else f'{self.cfg.replay_speed}s/bar'} "
            f"| strategy={type(self._sig_gen).__name__}"
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

        # 2. Planning chronologique global
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

            cache = self._bar_cache.setdefault(instrument, [])
            cache.append(bar)
            if len(cache) > 300:
                cache.pop(0)

            if len(cache) < self.cfg.min_bars_for_signal:
                n_bars += 1
                continue

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
        logger.info(f"ParquetClock replay complete | {n_bars:,} bars | {n_signals} signals | {elapsed:.1f}s")
        self._print_summary(orchestrator)

    # ── Summary ────────────────────────────────────────────────

    def _print_summary(self, orchestrator):
        closed = list(orchestrator.manager.closed_positions)
        open_pos = list(orchestrator.manager.open_positions)
        account = orchestrator.account
        start_balance = account.start_balance
        final_equity = account.equity
        strategy_name = type(self._sig_gen).__name__

        # ─ métriques globales
        results = [p.result_r for p in closed if p.result_r is not None]
        n_trades = len(results)
        wins     = [r for r in results if r > 0]
        losses   = [r for r in results if r <= 0]
        win_rate = len(wins) / n_trades * 100 if n_trades else 0.0
        avg_win  = sum(wins)  / len(wins)   if wins   else 0.0
        avg_loss = sum(losses)/ len(losses) if losses else 0.0
        expectancy = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss) if n_trades else 0.0
        total_r  = sum(results)
        pnl_cash = final_equity - start_balance
        pnl_pct  = pnl_cash / start_balance * 100

        # max DD
        equity_curve = [start_balance]
        running = start_balance
        for p in closed:
            if p.result_r is not None and p.risk_cash:
                running += p.result_r * p.risk_cash
            equity_curve.append(running)
        peak = start_balance
        max_dd_pct = 0.0
        for e in equity_curve:
            peak = max(peak, e)
            dd = (peak - e) / peak * 100
            max_dd_pct = max(max_dd_pct, dd)

        # P&L par instrument
        inst_r: dict[str, float] = defaultdict(float)
        inst_n: dict[str, int]   = defaultdict(int)
        for p in closed:
            if p.result_r is not None:
                inst_r[p.instrument] += p.result_r
                inst_n[p.instrument] += 1

        # Estimation temps pour ±10%
        target_cash = start_balance * 0.10
        avg_risk_cash = (
            sum(p.risk_cash for p in closed if p.risk_cash) / n_trades
            if n_trades else 0.0
        )
        avg_bars_per_trade = (
            sum(p.bars_open for p in closed if p.bars_open) / n_trades
            if n_trades else 0.0
        )
        if expectancy != 0 and avg_risk_cash > 0 and avg_bars_per_trade > 0:
            r_per_bar = (expectancy * avg_risk_cash) / avg_bars_per_trade
            bars_to_10pct = abs(target_cash / r_per_bar) if r_per_bar != 0 else float("inf")
            days_to_10pct = bars_to_10pct / 24
        else:
            days_to_10pct = float("inf")

        # Affichage
        line = "=" * 60
        print(f"\n{line}")
        print(f" DRY-RUN SUMMARY — {strategy_name}")
        print(f" {self.cfg.start or 'start'} → {self.cfg.end or 'now'}")
        print(line)
        print(f" Balance start  : {start_balance:>10,.0f}")
        print(f" Equity final   : {final_equity:>10,.2f}  ({pnl_pct:+.2f}%)")
        print(f" P&L cash       : {pnl_cash:>+10,.2f}")
        print(f" Max DD         : {max_dd_pct:>10.1f}%")
        print(line)
        print(f" Trades         : {n_trades}")
        print(f" Win rate       : {win_rate:.1f}%")
        print(f" Avg win        : {avg_win:+.3f}R")
        print(f" Avg loss       : {avg_loss:+.3f}R")
        print(f" Expectancy     : {expectancy:+.4f}R")
        print(f" Total R        : {total_r:+.2f}R")
        print(line)
        print(" P&L par instrument :")
        for inst in sorted(inst_r, key=lambda i: inst_r[i], reverse=True):
            print(f"   {inst:<10} {inst_r[inst]:>+6.2f}R  ({inst_n[inst]} trades)")
        print(line)
        if math.isfinite(days_to_10pct):
            direction = "+10%" if expectancy > 0 else "-10%"
            print(f" Estimation {direction}   : ~{days_to_10pct:.0f} jours (extrapolation linéaire)")
        else:
            print(" Estimation ±10%    : N/A (expectancy nulle ou données insuffisantes)")
        if open_pos:
            print(f" Positions ouvertes : {len(open_pos)} non clôturées au {self.cfg.end or 'arrêt'}")
            for p in open_pos:
                print(f"   {p.instrument} {p.side.value} entry={p.entry_price:.4f} sl={p.sl:.4f}")
        print(line)

        # Export JSONL
        ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        jsonl_path = Path(f"dry_run_{ts_str}.jsonl")
        with jsonl_path.open("w", encoding="utf-8") as f:
            for p in closed:
                record = {
                    "type": "trade",
                    "instrument": p.instrument,
                    "side": p.side.value if hasattr(p.side, "value") else str(p.side),
                    "entry_price": p.entry_price,
                    "sl": p.sl,
                    "result_r": p.result_r,
                    "risk_cash": p.risk_cash,
                    "exit_reason": p.exit_reason,
                    "bars_open": p.bars_open,
                    "mfe_r": p.mfe_r,
                    "ts_entry": p.ts_entry.isoformat() if p.ts_entry else None,
                    "ts_exit":  p.ts_exit.isoformat()  if p.ts_exit  else None,
                }
                f.write(json.dumps(record) + "\n")
            summary = {
                "type": "summary",
                "strategy": strategy_name,
                "period_start": self.cfg.start,
                "period_end": self.cfg.end,
                "start_balance": start_balance,
                "final_equity": final_equity,
                "pnl_cash": pnl_cash,
                "pnl_pct": round(pnl_pct, 4),
                "max_dd_pct": round(max_dd_pct, 4),
                "n_trades": n_trades,
                "win_rate": round(win_rate, 2),
                "avg_win_r": round(avg_win, 4),
                "avg_loss_r": round(avg_loss, 4),
                "expectancy_r": round(expectancy, 4),
                "total_r": round(total_r, 4),
                "days_to_10pct": round(days_to_10pct, 1) if math.isfinite(days_to_10pct) else None,
                "open_positions_at_end": len(open_pos),
                "pnl_by_instrument": {
                    inst: {"total_r": round(inst_r[inst], 4), "trades": inst_n[inst]}
                    for inst in inst_r
                },
            }
            f.write(json.dumps(summary) + "\n")
        print(f" Export JSONL    : {jsonl_path.resolve()}")
        print(line)
