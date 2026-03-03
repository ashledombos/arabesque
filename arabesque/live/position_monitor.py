"""
Arabesque — Live Position Monitor.

Gère le breakeven et le trailing sur les positions ouvertes en live.
Miroir de la logique PositionManager du backtest, mais opère sur le broker réel
via amend_position_sltp().

MÉCANISMES ACTIFS:
  1. Breakeven : MFE >= 0.3R → SL déplacé à entry + 0.20R
  2. Trailing paliers : MFE >= 1.5R/2.0R/3.0R → SL suit le prix

APPEL:
  - register_position() après chaque fill réussi
  - on_bar_closed() à chaque fermeture de bougie H1
  - reconcile() périodiquement pour nettoyer les positions fermées
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from arabesque.models import Side

logger = logging.getLogger("arabesque.live.position_monitor")


@dataclass
class TrackedPosition:
    """Position suivie en live pour gestion BE/trailing."""
    broker_id: str
    position_id: str          # ID position broker (cTrader positionId)
    symbol: str
    side: Side
    entry: float
    sl: float                 # SL courant (peut avoir bougé)
    sl_initial: float         # SL initial (ne change jamais)
    tp: float
    volume: float
    digits: int = 5           # Nombre de décimales autorisées par le broker

    # Tracking
    max_favorable_price: float = 0.0
    breakeven_set: bool = False
    trailing_active: bool = False
    trailing_tier: int = 0
    last_amend_time: float = 0.0
    amend_failures: int = 0
    registered_at: float = 0.0     # time.time() à l'enregistrement
    _amend_in_progress: bool = False  # Guard contre les amends concurrents

    @property
    def R(self) -> float:
        """Risque en unités de prix."""
        return abs(self.entry - self.sl_initial) if self.sl_initial != 0 else 0

    @property
    def mfe_r(self) -> float:
        """Maximum Favorable Excursion en R."""
        if self.R == 0:
            return 0.0
        if self.side == Side.LONG:
            return (self.max_favorable_price - self.entry) / self.R
        return (self.entry - self.max_favorable_price) / self.R

    def update_mfe(self, high: float, low: float):
        """Met à jour le MFE avec les extrêmes de la bougie."""
        if self.side == Side.LONG:
            self.max_favorable_price = max(self.max_favorable_price, high)
        else:
            if self.max_favorable_price == 0:
                self.max_favorable_price = low
            else:
                self.max_favorable_price = min(self.max_favorable_price, low)


# Configuration BE/trailing (v3.3 validée)
@dataclass
class MonitorConfig:
    be_trigger_r: float = 0.3
    be_offset_r: float = 0.20
    trailing_tiers: List[Tuple[float, float]] = field(default_factory=lambda: [
        (3.0, 1.5),   # MFE >= 3.0R → trail à 1.5R du sommet
        (2.0, 1.0),   # MFE >= 2.0R → trail à 1.0R du sommet
        (1.5, 0.7),   # MFE >= 1.5R → trail à 0.7R du sommet
    ])
    # Retry pour les amends échoués
    max_amend_retries: int = 3
    min_amend_interval_s: float = 5.0  # anti-spam


class LivePositionMonitor:
    """Moniteur de positions live — breakeven + trailing.

    Flux:
      1. Engine appelle register_position() après un fill réussi
      2. Engine appelle on_bar_closed() à chaque bougie H1
      3. Le monitor vérifie BE/trailing et appelle amend_position_sltp()
      4. reconcile() nettoie les positions fermées
    """

    def __init__(
        self,
        brokers: Dict,
        config: MonitorConfig | None = None,
    ):
        self._brokers = brokers
        self._cfg = config or MonitorConfig()
        self._positions: Dict[str, TrackedPosition] = {}
        # Trier trailing tiers du plus haut au plus bas
        self._cfg.trailing_tiers.sort(key=lambda t: t[0], reverse=True)

    @property
    def open_positions(self) -> List[TrackedPosition]:
        return list(self._positions.values())

    def register_position(
        self,
        broker_id: str,
        position_id: str,
        symbol: str,
        side: Side,
        entry: float,
        sl: float,
        tp: float,
        volume: float,
        digits: int = 5,
    ) -> TrackedPosition:
        """Enregistre une position après un fill réussi."""
        key = f"{broker_id}:{position_id}"
        pos = TrackedPosition(
            broker_id=broker_id,
            position_id=position_id,
            symbol=symbol,
            side=side,
            entry=entry,
            sl=sl,
            sl_initial=sl,
            tp=tp,
            volume=volume,
            digits=digits,
            max_favorable_price=entry,
            registered_at=time.time(),
        )
        self._positions[key] = pos
        logger.info(
            f"[Monitor] 📋 Registered {symbol} {side.value} "
            f"entry={entry:.{digits}f} SL={sl:.{digits}f} TP={tp:.{digits}f} "
            f"R={pos.R:.{digits}f} ({broker_id}:{position_id})"
        )
        return pos

    def unregister_position(self, broker_id: str, position_id: str):
        """Retire une position (fermée ou annulée)."""
        key = f"{broker_id}:{position_id}"
        if key in self._positions:
            pos = self._positions.pop(key)
            logger.info(
                f"[Monitor] 🗑️ Unregistered {pos.symbol} ({broker_id}:{position_id})"
            )

    async def on_bar_closed(self, symbol: str, high: float, low: float, close: float):
        """Appelé à chaque fermeture de bougie H1.

        Vérifie toutes les positions ouvertes sur ce symbole
        et applique BE/trailing si les conditions sont remplies.
        """
        matching = [
            pos for pos in self._positions.values()
            if pos.symbol == symbol
        ]
        if not matching:
            return

        for pos in matching:
            # Mettre à jour le MFE avec la bougie
            pos.update_mfe(high, low)

            # 1. Breakeven
            if not pos.breakeven_set:
                await self._check_breakeven(pos, close)

            # 2. Trailing (indépendant du BE)
            await self._check_trailing(pos, close)

    async def _check_breakeven(self, pos: TrackedPosition, current_price: float):
        """Si MFE >= 0.3R → déplacer SL à entry + 0.20R."""
        if pos.mfe_r < self._cfg.be_trigger_r:
            return

        if pos.side == Side.LONG:
            be_level = pos.entry + self._cfg.be_offset_r * pos.R
            if be_level <= pos.sl:
                return  # SL déjà au-dessus du BE
        else:
            be_level = pos.entry - self._cfg.be_offset_r * pos.R
            if be_level >= pos.sl:
                return  # SL déjà en-dessous du BE

        new_sl = round(be_level, pos.digits)
        logger.info(
            f"[Monitor] 🔄 BE trigger: {pos.symbol} MFE={pos.mfe_r:.2f}R >= "
            f"{self._cfg.be_trigger_r}R → SL {pos.sl:.{pos.digits}f} → {new_sl:.{pos.digits}f}"
        )

        success = await self._try_amend_sl(pos, new_sl)
        if success:
            pos.sl = new_sl
            pos.breakeven_set = True

    async def _check_trailing(self, pos: TrackedPosition, current_price: float):
        """Trailing paliers : déplace le SL derrière le prix quand MFE atteint un seuil."""
        # Trouver le meilleur tier applicable
        best_tier = None
        best_idx = 0
        for idx, (threshold, distance) in enumerate(self._cfg.trailing_tiers):
            if pos.mfe_r >= threshold:
                best_tier = (threshold, distance)
                best_idx = idx + 1
                break  # Trié desc, le premier match est le meilleur

        if best_tier is None:
            return

        threshold, distance = best_tier

        # Calculer le nouveau SL
        if pos.side == Side.LONG:
            new_sl = pos.max_favorable_price - distance * pos.R
            if new_sl <= pos.sl:
                return  # Le trailing ne peut que resserrer le SL
        else:
            new_sl = pos.max_favorable_price + distance * pos.R
            if new_sl >= pos.sl:
                return

        new_sl = round(new_sl, pos.digits)

        logger.info(
            f"[Monitor] 📈 Trailing tier {best_idx}: {pos.symbol} "
            f"MFE={pos.mfe_r:.2f}R → SL {pos.sl:.{pos.digits}f} → {new_sl:.{pos.digits}f} "
            f"(dist={distance}R from peak={pos.max_favorable_price:.{pos.digits}f})"
        )

        success = await self._try_amend_sl(pos, new_sl)
        if success:
            pos.sl = new_sl
            pos.trailing_active = True
            pos.trailing_tier = best_idx

    async def _try_amend_sl(self, pos: TrackedPosition, new_sl: float) -> bool:
        """Tente de modifier le SL sur le broker avec retry."""
        # Anti-spam: pas plus d'un amend toutes les N secondes
        now = time.time()
        if now - pos.last_amend_time < self._cfg.min_amend_interval_s:
            return False

        # Guard concurrence : un seul amend à la fois par position
        if pos._amend_in_progress:
            return False
        pos._amend_in_progress = True

        broker = self._brokers.get(pos.broker_id)
        if not broker:
            logger.error(f"[Monitor] Broker {pos.broker_id} not found")
            pos._amend_in_progress = False
            return False

        try:
            for attempt in range(1, self._cfg.max_amend_retries + 1):
                try:
                    result = await broker.amend_position_sltp(
                        pos.position_id, stop_loss=new_sl
                    )
                    pos.last_amend_time = time.time()

                    if result.success:
                        pos.amend_failures = 0
                        logger.info(
                            f"[Monitor] ✅ SL amended: {pos.symbol} "
                            f"→ {new_sl:.{pos.digits}f} ({result.message})"
                        )
                        return True
                    else:
                        pos.amend_failures += 1
                        logger.warning(
                            f"[Monitor] ❌ Amend failed (attempt {attempt}/"
                            f"{self._cfg.max_amend_retries}): {result.message}"
                        )
                        if attempt < self._cfg.max_amend_retries:
                            await asyncio.sleep(2 * attempt)  # backoff

                except Exception as e:
                    pos.amend_failures += 1
                    logger.error(
                        f"[Monitor] ❌ Amend exception (attempt {attempt}): {e}"
                    )
                    if attempt < self._cfg.max_amend_retries:
                        await asyncio.sleep(2 * attempt)

            logger.error(
                f"[Monitor] ⚠️ SL amend ABANDONED after {self._cfg.max_amend_retries} "
                f"attempts: {pos.symbol} {pos.position_id} target_sl={new_sl}"
            )
            return False
        finally:
            pos._amend_in_progress = False

    async def reconcile(self):
        """Synchronise avec le broker — retire les positions fermées.

        Grace period: ne retire pas les positions < 5 min (le broker peut
        mettre du temps à confirmer le fill).
        """
        if not self._positions:
            return

        GRACE_PERIOD_S = 300  # 5 minutes
        now = time.time()

        # Grouper par broker
        by_broker: Dict[str, List[str]] = {}
        for key, pos in self._positions.items():
            by_broker.setdefault(pos.broker_id, []).append(key)

        for broker_id, keys in by_broker.items():
            broker = self._brokers.get(broker_id)
            if not broker:
                continue

            try:
                broker_positions = await broker.get_positions()
                broker_pos_ids = {str(p.position_id) for p in broker_positions}

                # Retirer les positions qui n'existent plus sur le broker
                for key in keys:
                    pos = self._positions.get(key)
                    if not pos:
                        continue
                    if pos.position_id in broker_pos_ids:
                        continue  # Toujours ouverte

                    # Vérifier la grace period
                    age = now - pos.registered_at
                    if age < GRACE_PERIOD_S:
                        logger.debug(
                            f"[Monitor] Position {pos.symbol} {pos.position_id} "
                            f"non trouvée mais enregistrée il y a {age:.0f}s < "
                            f"{GRACE_PERIOD_S}s — conservée (grace period)"
                        )
                        continue

                    self._positions.pop(key, None)
                    logger.info(
                        f"[Monitor] 🗑️ Position {pos.symbol} {pos.position_id} "
                        f"non trouvée sur {broker_id} après {age:.0f}s — retirée "
                        f"(MFE={pos.mfe_r:.2f}R BE={'✓' if pos.breakeven_set else '✗'} "
                        f"trail={pos.trailing_tier})"
                    )
            except Exception as e:
                logger.warning(f"[Monitor] Reconcile error for {broker_id}: {e}")

    def get_stats(self) -> dict:
        """Statistiques du monitor."""
        return {
            "tracked_positions": len(self._positions),
            "positions": [
                {
                    "symbol": p.symbol,
                    "side": p.side.value,
                    "entry": p.entry,
                    "sl": p.sl,
                    "sl_initial": p.sl_initial,
                    "mfe_r": round(p.mfe_r, 2),
                    "breakeven_set": p.breakeven_set,
                    "trailing_tier": p.trailing_tier,
                    "broker_id": p.broker_id,
                    "position_id": p.position_id,
                }
                for p in self._positions.values()
            ],
        }
