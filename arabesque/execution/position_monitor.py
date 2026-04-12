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
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from arabesque.core.models import Side

STATE_FILE = Path("logs/position_monitor_state.json")

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
    last_tick_check: float = 0.0   # Throttle pour on_tick
    amend_failures: int = 0
    registered_at: float = 0.0     # time.time() à l'enregistrement
    _amend_in_progress: bool = False  # Guard contre les amends concurrents
    _skip_count: int = 0              # Compteur de skips (anti-spam log)

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

    def update_mfe_tick(self, price: float):
        """Met à jour le MFE avec un prix tick (bid pour LONG, ask pour SHORT)."""
        if self.side == Side.LONG:
            self.max_favorable_price = max(self.max_favorable_price, price)
        else:
            if self.max_favorable_price == 0:
                self.max_favorable_price = price
            else:
                self.max_favorable_price = min(self.max_favorable_price, price)


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
    # Tick-level monitoring : intervalle min entre deux checks par position
    tick_check_interval_s: float = 10.0  # 10s entre chaque vérif par position


class LivePositionMonitor:
    """Moniteur de positions live — breakeven + trailing.

    Flux:
      1. Engine appelle register_position() après un fill réussi
      2. Engine appelle on_bar_closed() à chaque bougie H1
      3. Le monitor vérifie BE/trailing et appelle amend_position_sltp()
      4. reconcile() nettoie les positions fermées et notifie on_position_closed
    """

    def __init__(
        self,
        brokers: Dict,
        config: MonitorConfig | None = None,
        on_position_closed: Optional[callable] = None,
    ):
        self._brokers = brokers
        self._cfg = config or MonitorConfig()
        self._positions: Dict[str, TrackedPosition] = {}
        self._on_position_closed = on_position_closed
        # Trier trailing tiers du plus haut au plus bas
        self._cfg.trailing_tiers.sort(key=lambda t: t[0], reverse=True)
        # Orphan tracking: {broker_id:position_id -> first_seen_timestamp}
        self._orphan_first_seen: Dict[str, float] = {}
        self._ORPHAN_GRACE_S = 120  # 2 min avant auto-close

    @property
    def open_positions(self) -> List[TrackedPosition]:
        return list(self._positions.values())

    def save_state(self) -> None:
        """Persiste l'état des positions trackées (MFE, BE, trailing) sur disque.

        Appelé lors d'un arrêt gracieux (SIGTERM) pour reprendre le monitoring
        au redémarrage sans perdre l'info MFE/BE/trailing.
        """
        if not self._positions:
            # Supprimer le fichier si aucune position
            STATE_FILE.unlink(missing_ok=True)
            return

        state = {}
        for key, pos in self._positions.items():
            state[key] = {
                "broker_id": pos.broker_id,
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "side": pos.side.value,
                "entry": pos.entry,
                "sl": pos.sl,
                "sl_initial": pos.sl_initial,
                "tp": pos.tp,
                "volume": pos.volume,
                "digits": pos.digits,
                "max_favorable_price": pos.max_favorable_price,
                "breakeven_set": pos.breakeven_set,
                "trailing_active": pos.trailing_active,
                "trailing_tier": pos.trailing_tier,
                "registered_at": pos.registered_at,
            }

        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))
        logger.info(
            f"[Monitor] 💾 État sauvegardé: {len(state)} position(s) → {STATE_FILE}"
        )

    def load_state(self) -> int:
        """Restaure l'état sauvegardé (MFE, BE, trailing) pour les positions encore ouvertes.

        Appelé après _reconcile_existing_positions() pour enrichir les positions
        réconciliées avec l'état précédent (MFE, BE, trailing tier).

        Returns: nombre de positions restaurées.
        """
        if not STATE_FILE.exists():
            return 0

        try:
            state = json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Monitor] ⚠️ Impossible de lire {STATE_FILE}: {e}")
            return 0

        restored = 0
        for key, saved in state.items():
            if key not in self._positions:
                continue  # Position fermée entre-temps
            pos = self._positions[key]
            # Restaurer l'état de tracking
            pos.max_favorable_price = saved.get("max_favorable_price", pos.max_favorable_price)
            pos.breakeven_set = saved.get("breakeven_set", False)
            pos.trailing_active = saved.get("trailing_active", False)
            pos.trailing_tier = saved.get("trailing_tier", 0)
            # Garder le SL le plus protecteur (broker peut avoir bougé)
            saved_sl = saved.get("sl", 0)
            if saved_sl and pos.side == Side.LONG and saved_sl > pos.sl:
                pos.sl = saved_sl
            elif saved_sl and pos.side == Side.SHORT and 0 < saved_sl < pos.sl:
                pos.sl = saved_sl
            restored += 1
            logger.info(
                f"[Monitor] 🔄 État restauré: {pos.symbol} "
                f"MFE={pos.mfe_r:.2f}R BE={'✓' if pos.breakeven_set else '✗'} "
                f"trail={pos.trailing_tier}"
            )

        # Nettoyer le fichier après restauration
        STATE_FILE.unlink(missing_ok=True)
        if restored:
            logger.info(f"[Monitor] ✅ {restored} position(s) restaurée(s) depuis état précédent")
        return restored

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

    async def on_tick(self, tick) -> None:
        """Appelé à chaque tick — vérifie BE/trailing en temps réel.

        Logique identique à on_bar_closed mais utilise le prix tick au lieu
        du close H1. Throttled à 1 check / tick_check_interval_s par position.

        Le tick contient : symbol, bid, ask, timestamp
        """
        sym = tick.symbol
        matching = [
            pos for pos in self._positions.values()
            if pos.symbol == sym
        ]
        if not matching:
            return

        now = time.time()
        bid = tick.bid if hasattr(tick, 'bid') and tick.bid else 0
        ask = tick.ask if hasattr(tick, 'ask') and tick.ask else 0

        for pos in matching:
            # Throttle : max 1 check toutes les N secondes par position
            if now - pos.last_tick_check < self._cfg.tick_check_interval_s:
                continue
            pos.last_tick_check = now

            # Prix de référence : bid pour LONG (on vend au bid),
            # ask pour SHORT (on rachète à l'ask)
            price = bid if pos.side == Side.LONG else ask
            if price <= 0:
                continue

            # Mettre à jour le MFE en continu
            pos.update_mfe_tick(price)

            # 1. Breakeven
            if not pos.breakeven_set:
                await self._check_breakeven(pos, price)

            # 2. Trailing
            await self._check_trailing(pos, price)

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
        lvl = logging.INFO if pos._skip_count == 0 else logging.DEBUG
        logger.log(lvl,
            f"[Monitor] 🔄 BE trigger: {pos.symbol} MFE={pos.mfe_r:.2f}R >= "
            f"{self._cfg.be_trigger_r}R → SL {pos.sl:.{pos.digits}f} → {new_sl:.{pos.digits}f}"
        )

        success = await self._try_amend_sl(pos, new_sl, current_price)
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

        # Skip si le SL n'a pas réellement bougé (anti-spam trailing)
        if abs(new_sl - pos.sl) < 0.5 * 10**(-pos.digits):
            return

        logger.info(
            f"[Monitor] 📈 Trailing tier {best_idx}: {pos.symbol} "
            f"MFE={pos.mfe_r:.2f}R → SL {pos.sl:.{pos.digits}f} → {new_sl:.{pos.digits}f} "
            f"(dist={distance}R from peak={pos.max_favorable_price:.{pos.digits}f})"
        )

        success = await self._try_amend_sl(pos, new_sl, current_price)
        if success:
            pos.sl = new_sl
            pos.trailing_active = True
            pos.trailing_tier = best_idx

    async def _try_amend_sl(
        self, pos: TrackedPosition, new_sl: float, current_price: float = 0
    ) -> bool:
        """Tente de modifier le SL sur le broker avec retry.

        Valide que le new_sl est faisable par rapport au prix courant
        avant d'envoyer au broker (évite TRADING_BAD_STOPS).
        """
        # Validation prix : le broker exige SL <= bid (LONG) ou SL >= ask (SELL)
        if current_price > 0:
            if pos.side == Side.LONG and new_sl > current_price:
                lvl = logging.INFO if pos._skip_count == 0 else logging.DEBUG
                pos._skip_count += 1
                logger.log(lvl,
                    f"[Monitor] ⏸ BE/Trail skipped: {pos.symbol} "
                    f"new_sl={new_sl:.{pos.digits}f} > bid={current_price:.{pos.digits}f} "
                    f"(price fell back, will retry — skip #{pos._skip_count})"
                )
                return False
            elif pos.side == Side.SHORT and new_sl < current_price:
                lvl = logging.INFO if pos._skip_count == 0 else logging.DEBUG
                pos._skip_count += 1
                logger.log(lvl,
                    f"[Monitor] ⏸ BE/Trail skipped: {pos.symbol} "
                    f"new_sl={new_sl:.{pos.digits}f} < ask={current_price:.{pos.digits}f} "
                    f"(price fell back, will retry — skip #{pos._skip_count})"
                )
                return False

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
            last_error = ""
            for attempt in range(1, self._cfg.max_amend_retries + 1):
                try:
                    result = await broker.amend_position_sltp(
                        pos.position_id, stop_loss=new_sl
                    )
                    pos.last_amend_time = time.time()

                    if result.success:
                        pos.amend_failures = 0
                        pos._skip_count = 0
                        logger.info(
                            f"[Monitor] ✅ SL amended: {pos.symbol} "
                            f"→ {new_sl:.{pos.digits}f} ({result.message})"
                        )
                        return True
                    else:
                        pos.amend_failures += 1
                        last_error = str(result.message)
                        # POSITION_NOT_FOUND = position fermée par le broker (SL/TP hit)
                        # → arrêter immédiatement, la réconciliation nettoiera
                        if "POSITION_NOT_FOUND" in last_error:
                            logger.info(
                                f"[Monitor] 🗑️ {pos.symbol} {pos.position_id}: "
                                f"position fermée (POSITION_NOT_FOUND) — arrêt monitoring"
                            )
                            # Notify LiveMonitor immediately
                            exit_reason = self._estimate_exit_reason(pos)
                            exit_price = self._estimate_exit_price(pos, exit_reason)
                            if self._on_position_closed:
                                try:
                                    self._on_position_closed(
                                        broker_id=pos.broker_id,
                                        position_id=pos.position_id,
                                        exit_price=exit_price,
                                        exit_reason=exit_reason,
                                        mfe_r=pos.mfe_r,
                                        be_set=pos.breakeven_set,
                                        trailing_tier=pos.trailing_tier,
                                    )
                                except Exception:
                                    pass
                            # Remove from tracking
                            key = f"{pos.broker_id}:{pos.position_id}"
                            self._positions.pop(key, None)
                            return False
                        logger.warning(
                            f"[Monitor] ❌ Amend failed (attempt {attempt}/"
                            f"{self._cfg.max_amend_retries}): {result.message}"
                        )
                        if attempt < self._cfg.max_amend_retries:
                            await asyncio.sleep(2 * attempt)  # backoff

                except Exception as e:
                    pos.amend_failures += 1
                    last_error = str(e)
                    logger.error(
                        f"[Monitor] ❌ Amend exception (attempt {attempt}): {e}"
                    )
                    if attempt < self._cfg.max_amend_retries:
                        await asyncio.sleep(2 * attempt)

            logger.error(
                f"[Monitor] ⚠️ SL amend ABANDONED after {self._cfg.max_amend_retries} "
                f"attempts: {pos.symbol} {pos.position_id} target_sl={new_sl} "
                f"last_error=[{last_error}]"
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

                    # Estimate exit details for trade journal
                    exit_reason = self._estimate_exit_reason(pos)
                    exit_price = self._estimate_exit_price(pos, exit_reason)

                    logger.info(
                        f"[Monitor] 🗑️ Position {pos.symbol} {pos.position_id} "
                        f"non trouvée sur {broker_id} après {age:.0f}s — retirée "
                        f"(MFE={pos.mfe_r:.2f}R BE={'✓' if pos.breakeven_set else '✗'} "
                        f"trail={pos.trailing_tier} exit≈{exit_reason})"
                    )

                    # Notify LiveMonitor
                    if self._on_position_closed:
                        try:
                            self._on_position_closed(
                                broker_id=broker_id,
                                position_id=pos.position_id,
                                exit_price=exit_price,
                                exit_reason=exit_reason,
                                mfe_r=pos.mfe_r,
                                be_set=pos.breakeven_set,
                                trailing_tier=pos.trailing_tier,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[Monitor] on_position_closed callback error: {e}"
                            )
                # Détection d'orphelins : positions broker non trackées par Arabesque
                tracked_ids = {
                    self._positions[k].position_id
                    for k in keys if k in self._positions
                }
                # Track which orphans are still present this cycle
                current_orphan_keys = set()
                for bp in broker_positions:
                    pid = str(bp.position_id)
                    if pid not in tracked_ids:
                        orphan_key = f"{broker_id}:{pid}"
                        current_orphan_keys.add(orphan_key)
                        has_sl = getattr(bp, 'stop_loss', None) not in (None, 0, 0.0)
                        has_tp = getattr(bp, 'take_profit', None) not in (None, 0, 0.0)
                        sym = getattr(bp, 'symbol', '?')
                        vol = getattr(bp, 'volume', 0)
                        side = getattr(bp, 'side', '?')
                        flags_parts = []
                        if not has_sl:
                            flags_parts.append("PAS DE SL")
                        if not has_tp:
                            flags_parts.append("PAS DE TP")
                        flags = " ".join(flags_parts) if flags_parts else "SL+TP OK"

                        if orphan_key not in self._orphan_first_seen:
                            self._orphan_first_seen[orphan_key] = now
                            logger.warning(
                                f"[Monitor] 👻 Position orpheline détectée: "
                                f"{sym} {side} {vol}L id={pid} sur {broker_id} "
                                f"— {flags} — délai de grâce {self._ORPHAN_GRACE_S}s"
                            )

                        age = now - self._orphan_first_seen[orphan_key]

                        # Auto-close orphelins sans SL après grace period
                        if not has_sl and age >= self._ORPHAN_GRACE_S:
                            logger.warning(
                                f"[Monitor] 🔒 Auto-close orpheline: "
                                f"{sym} {side} {vol}L id={pid} sur {broker_id} "
                                f"— sans SL depuis {age:.0f}s"
                            )
                            try:
                                result = await broker.close_position(pid)
                                logger.info(
                                    f"[Monitor] ✅ Orpheline fermée: {sym} {pid} "
                                    f"sur {broker_id} — {result}"
                                )
                                self._orphan_first_seen.pop(orphan_key, None)
                            except Exception as close_err:
                                logger.error(
                                    f"[Monitor] ❌ Échec fermeture orpheline "
                                    f"{sym} {pid}: {close_err}"
                                )

                # Clean up orphans that disappeared (closed by broker/user)
                stale_keys = [
                    k for k in list(self._orphan_first_seen)
                    if k.startswith(f"{broker_id}:") and k not in current_orphan_keys
                ]
                for k in stale_keys:
                    self._orphan_first_seen.pop(k, None)
            except Exception as e:
                logger.warning(f"[Monitor] Reconcile error for {broker_id}: {e}")

    def _estimate_exit_reason(self, pos: TrackedPosition) -> str:
        """Estime la raison de sortie basée sur l'état du trailing/BE."""
        if pos.trailing_active and pos.trailing_tier > 0:
            return "trailing_stop"
        if pos.breakeven_set:
            # BE was set — could be BE exit or TP
            if pos.tp > 0:
                # If MFE reached TP zone, likely TP hit
                if pos.R > 0:
                    tp_r = abs(pos.tp - pos.entry) / pos.R
                    if pos.mfe_r >= tp_r * 0.95:
                        return "take_profit"
            return "breakeven_exit"
        # No BE set — likely SL hit
        return "stop_loss"

    def _estimate_exit_price(self, pos: TrackedPosition, reason: str) -> float:
        """Estime le prix de sortie basé sur la raison."""
        if reason == "take_profit" and pos.tp > 0:
            return pos.tp
        if reason == "stop_loss":
            return pos.sl_initial
        if reason == "breakeven_exit":
            return pos.entry + (0.20 * pos.R if pos.side == Side.LONG
                                else -0.20 * pos.R)
        if reason == "trailing_stop":
            return pos.sl  # Current (trailed) SL
        return pos.sl

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
