#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BrokerPriceSnapshotter — capture périodique des cotations multi-broker
pour chaque symbole actuellement en position.

But : rendre visibles les divergences de prix / spread entre brokers
(FTMO cTrader vs GFT TradeLocker) pendant qu'une position est ouverte,
sans backtest. Les données sont écrites en JSONL et peuvent être
annotées a posteriori dans le journal de trading.

Paramétrage (cadence, rétention, brokers inclus) : volontairement figé
pour l'instant. Sera exposé en config si l'usage se confirme pertinent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = Path("logs/multi_broker_snapshots.jsonl")
DEFAULT_INTERVAL_S = 30.0


class BrokerPriceSnapshotter:
    """Écrit périodiquement les quotes de chaque broker pour les symboles
    actuellement en position."""

    def __init__(
        self,
        brokers: Dict,
        position_monitor,
        log_path: Path = DEFAULT_LOG_PATH,
        interval_s: float = DEFAULT_INTERVAL_S,
    ):
        self.brokers = brokers
        self.position_monitor = position_monitor
        self.log_path = Path(log_path)
        self.interval_s = interval_s
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _open_symbols(self) -> Dict[str, set]:
        """Retourne {symbol: {broker_id_qui_a_position, ...}}."""
        out: Dict[str, set] = {}
        try:
            positions = self.position_monitor.open_positions
        except Exception:
            return out
        for pos in positions:
            out.setdefault(pos.symbol, set()).add(pos.broker_id)
        return out

    async def snapshot_once(self) -> int:
        """Écrit une ligne JSONL par (symbol, broker) pour chaque symbole
        actuellement en position. Retourne le nombre de lignes écrites."""
        open_syms = self._open_symbols()
        if not open_syms:
            return 0

        ts = datetime.now(timezone.utc).isoformat()
        lines = []
        for symbol, holder_brokers in open_syms.items():
            for broker_id, broker in self.brokers.items():
                getter = getattr(broker, "get_quote", None)
                if not getter:
                    continue
                try:
                    tick = await getter(symbol)
                except Exception as e:
                    logger.debug(
                        f"[Snapshot] {broker_id} get_quote({symbol}) failed: {e}"
                    )
                    tick = None
                if not tick:
                    continue
                rec = {
                    "ts": ts,
                    "symbol": symbol,
                    "broker": broker_id,
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "mid": tick.mid,
                    "spread": tick.spread,
                    "has_position": broker_id in holder_brokers,
                }
                lines.append(json.dumps(rec, separators=(",", ":")))
        if lines:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        return len(lines)

    async def run_forever(self, stop_flag) -> None:
        """Boucle principale. stop_flag() doit renvoyer True pour arrêter."""
        logger.info(
            f"[Snapshot] actif — {self.interval_s:.0f}s cadence → {self.log_path}"
        )
        while stop_flag():
            try:
                n = await self.snapshot_once()
                if n:
                    logger.debug(f"[Snapshot] {n} lignes écrites")
            except Exception as e:
                logger.warning(f"[Snapshot] erreur: {e}")
            await asyncio.sleep(self.interval_s)
