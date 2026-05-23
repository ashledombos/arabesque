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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

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
    missing_cycles: int = 0           # Cycles reconcile consécutifs où la position est absente de get_positions()
    last_amend_alert_time: float = 0.0  # Dernière notif "ABANDONED" envoyée (anti-spam)
    # Hot Path #1 — compteur dédié à la boucle broker_reconcile 60s (distinct
    # de missing_cycles qui est consommé par reconcile() 120s avec fallback
    # de retrait forcé). Ici on alerte URGENT à 3 cycles consécutifs puis on
    # retire la position du tracking (anti-spam par retrait, pas par cooldown).
    broker_missing_cycles: int = 0

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
    # Phase 2.5 — backup BE armé indépendamment du PriceFeed (cf. incident 14/05)
    # Désactivé par défaut. À n'activer qu'après validation 2.8 + replay 14/05.
    be_polling_enabled: bool = False
    be_polling_interval_s: float = 60.0
    be_polling_freshness_threshold_s: float = 300.0  # 5 min — skip si quote plus vieille
    # Étage 0 (incident DASHUSD 2026-05-21) — notif Telegram+ntfy à chaque
    # "ABANDONED" amend SL, avec cooldown 30 min par position pour éviter le
    # spam quand le canal trading reste mort pendant des heures.
    amend_alert_cooldown_s: float = 1800.0
    # Hot Path #1 (incident DASHUSD 2026-05-21) — heartbeat ReconcileReq actif
    # dès qu'une position est ouverte. Sert à détecter qu'une position locale
    # n'existe plus côté broker (fermée à notre insu, ex: SL touché pendant
    # silence trading channel). 3 cycles d'absence consécutifs → alerte URGENT.
    # Désactivé par défaut, activé via config/settings.yaml::live.broker_reconcile_active.
    broker_reconcile_enabled: bool = False
    broker_reconcile_interval_s: float = 60.0
    broker_reconcile_timeout_s: float = 10.0
    broker_reconcile_missing_threshold: int = 3


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
        on_audit_event: Optional[Callable[[dict], None]] = None,
        on_amend_abandoned: Optional[Callable[[dict], None]] = None,
        on_position_missing_broker: Optional[Callable[[dict], None]] = None,
    ):
        self._brokers = brokers
        self._cfg = config or MonitorConfig()
        self._positions: Dict[str, TrackedPosition] = {}
        self._on_position_closed = on_position_closed
        # Callback audit JSONL (event détaillé pour BE polling armé, etc.)
        self._on_audit_event = on_audit_event
        # Étage 0 — callback déclenché quand un amend SL est abandonné après
        # max_amend_retries échecs consécutifs (incident DASHUSD 2026-05-21).
        # Payload: {broker_id, position_id, symbol, target_sl, last_error,
        # amend_failures, mfe_r}. Le callback est responsable du cooldown
        # cross-position ; le monitor gère seulement le cooldown par position.
        self._on_amend_abandoned = on_amend_abandoned
        # Hot Path #1 — callback URGENT déclenché quand une position locale
        # est absente côté broker pendant ``broker_reconcile_missing_threshold``
        # cycles consécutifs. Payload : {broker_id, position_id, symbol, side,
        # entry, sl, mfe_r, breakeven_set, trailing_tier, missing_cycles}.
        # Pas de cooldown — la position est retirée du tracking après l'event
        # (anti-spam par retrait, pas par fenêtre temporelle).
        self._on_position_missing_broker = on_position_missing_broker
        # Trier trailing tiers du plus haut au plus bas
        self._cfg.trailing_tiers.sort(key=lambda t: t[0], reverse=True)
        # Orphan tracking: {broker_id:position_id -> first_seen_timestamp}
        self._orphan_first_seen: Dict[str, float] = {}
        self._ORPHAN_GRACE_S = 120  # 2 min avant auto-close
        # Phase 2.5 — boucle polling backup BE
        self._be_polling_task: Optional[asyncio.Task] = None
        self._be_polling_stop: Optional[asyncio.Event] = None
        # Hot Path #1 — boucle heartbeat broker (ReconcileReq périodique)
        self._broker_reconcile_task: Optional[asyncio.Task] = None
        self._broker_reconcile_stop: Optional[asyncio.Event] = None
        # Compteur de timeouts/None consécutifs côté broker (canal mort)
        self._broker_reconcile_consecutive_timeouts: int = 0

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
        # Hot Path #1 — auto-démarre la boucle broker_reconcile dès la
        # première position trackée (no-op si déjà en route ou désactivée).
        self._maybe_start_broker_reconcile()
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

            await self._process_pos_quote(pos, bid, ask, source="tick")

    async def _process_pos_quote(
        self,
        pos: TrackedPosition,
        bid: float,
        ask: float,
        source: str = "tick",
        do_trailing: bool = True,
    ) -> bool:
        """Évalue BE (et trailing si activé) pour une position contre un couple bid/ask.

        Wrapper qui sélectionne le prix pertinent (bid pour LONG, ask pour SHORT)
        puis délègue à ``_process_pos_from_price``. Utilisé par ``on_tick`` qui
        dispose d'un PriceTick avec bid ET ask.

        Le polling broker (Phase 2.5 étape 2.5+) appellera plutôt directement
        ``_process_pos_from_price`` avec le seul côté pertinent — pas besoin de
        fabriquer un bid/ask factice.
        """
        price = bid if pos.side == Side.LONG else ask
        return await self._process_pos_from_price(pos, price, source, do_trailing)

    async def _process_pos_from_price(
        self,
        pos: TrackedPosition,
        price: float,
        source: str = "tick",
        do_trailing: bool = True,
    ) -> bool:
        """Évalue BE (et trailing si activé) pour une position contre un prix unique.

        Méthode commune utilisée par ``on_tick`` (via ``_process_pos_quote``)
        et par le polling broker direct (qui n'a que le côté pertinent, pas
        besoin de fabriquer un spread complet).

        Le ``price`` doit déjà être le prix pertinent côté position :
        bid pour LONG (on vend au bid), ask pour SHORT (on rachète à l'ask).

        Cette méthode N'inclut PAS le throttle tick (responsabilité de l'appelant).
        Elle suppose que le caller a déjà décidé qu'il était temps de checker.

        Retourne True si le BE vient d'être armé pendant cet appel, False sinon.
        Le ``source`` est passé en argument pour permettre un audit trail futur
        (sera consommé par ``_check_breakeven`` quand on instrumentera l'event).
        """
        if price <= 0:
            return False

        pos.update_mfe_tick(price)

        be_just_armed = False
        if not pos.breakeven_set:
            was_set_before = pos.breakeven_set
            await self._check_breakeven(pos, price)
            be_just_armed = pos.breakeven_set and not was_set_before

        if do_trailing:
            await self._check_trailing(pos, price)

        return be_just_armed

    # ------------------------------------------------------------------
    # Phase 2.5 — boucle polling backup BE (indépendante du PriceFeed)
    # ------------------------------------------------------------------

    async def start_be_polling(self) -> None:
        """Démarre la boucle backup qui poll les brokers pour armer le BE
        même si le PriceFeed est mort silencieusement (cas 14/05).

        No-op si ``be_polling_enabled`` est False ou si la boucle tourne déjà.
        BE-only (do_trailing=False) tant que la v1 n'est pas validée.
        """
        if not self._cfg.be_polling_enabled:
            logger.info("[Monitor] be_polling_backup désactivé (config)")
            return
        if self._be_polling_task and not self._be_polling_task.done():
            return

        self._be_polling_stop = asyncio.Event()
        self._be_polling_task = asyncio.create_task(
            self._be_polling_loop(),
            name="be_polling_loop",
        )
        logger.info(
            f"[Monitor] ⏱  be_polling_backup actif "
            f"(interval={self._cfg.be_polling_interval_s}s, "
            f"freshness_max={self._cfg.be_polling_freshness_threshold_s}s, "
            f"BE-only, do_trailing=False)"
        )

    async def stop_be_polling(self) -> None:
        """Annule proprement la boucle (signal + cancel + await)."""
        task = self._be_polling_task
        if not task:
            return
        if self._be_polling_stop is not None:
            self._be_polling_stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"[Monitor] be_polling stop : exception ignorée: {e}")
        self._be_polling_task = None
        self._be_polling_stop = None
        logger.info("[Monitor] ⏹  be_polling_backup arrêté")

    async def _be_polling_loop(self) -> None:
        """Boucle interne. Réveil toutes les ``be_polling_interval_s`` secondes.

        Toute exception levée par une passe est avalée pour ne JAMAIS tuer
        la boucle : un échec quote/réseau ne doit ni déclencher reconcile,
        ni close, ni incident sur la position.
        """
        interval = self._cfg.be_polling_interval_s
        threshold_s = self._cfg.be_polling_freshness_threshold_s
        stop_evt = self._be_polling_stop
        assert stop_evt is not None

        try:
            while not stop_evt.is_set():
                # Sleep interruptible par stop_be_polling
                try:
                    await asyncio.wait_for(stop_evt.wait(), timeout=interval)
                    break  # event set → on sort
                except asyncio.TimeoutError:
                    pass  # tick normal

                if stop_evt.is_set():
                    break

                try:
                    checked, armed, skipped = await self._be_polling_pass(threshold_s)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"[Monitor] be_polling pass error (loop continues): {e}")
                    continue

                # Log sobre par cycle (debug). Event détaillé seulement si armé.
                if checked or armed or skipped:
                    logger.debug(
                        f"[Monitor] be_polling pass : "
                        f"checked={checked} armed={armed} skipped={skipped}"
                    )
        except asyncio.CancelledError:
            logger.debug("[Monitor] be_polling loop cancelled")
            raise

    async def _be_polling_pass(self, freshness_threshold_s: float) -> Tuple[int, int, int]:
        """Un passage de la boucle. Retourne (checked, armed, skipped).

        - checked : positions pour lesquelles un FreshQuote valide a été obtenu
                    et passé à _process_pos_from_price
        - armed   : positions dont le BE vient d'être armé par CE passage
        - skipped : positions ignorées (quote absente, stale, freshness
                    indéterminée, broker introuvable…)
        """
        checked = 0
        armed = 0
        skipped = 0
        now = datetime.now(timezone.utc)

        # Snapshot pour ne pas itérer sur un dict modifié pendant le polling
        positions_snapshot = list(self._positions.values())

        for pos in positions_snapshot:
            if pos.breakeven_set:
                continue  # déjà BE, rien à faire ici
            broker = self._brokers.get(pos.broker_id)
            if broker is None:
                skipped += 1
                continue

            quote_type = "bid" if pos.side == Side.LONG else "ask"

            # 1. Récupération quote fraîche — toute exception est avalée
            try:
                fq = await broker.get_fresh_quote(pos.symbol, quote_type)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(
                    f"[Monitor] be_polling get_fresh_quote("
                    f"{pos.broker_id}, {pos.symbol}, {quote_type}) failed: {e}"
                )
                skipped += 1
                continue

            if fq is None:
                # Pas de tick frais ou broker non supporté — on attend le
                # prochain cycle. Pas d'alerte, pas de reconcile.
                skipped += 1
                continue

            # 2. Freshness check (différencié par type de timestamp)
            broker_kind = type(broker).__name__
            is_ctrader = broker_kind == "CTraderBroker"

            if is_ctrader:
                # cTrader doit fournir market_ts. Si absent → freshness
                # indéterminée → skip défensif (l'impl native garantit
                # market_ts, mais on ne fait pas confiance aux mocks).
                if fq.market_ts is None:
                    logger.debug(
                        f"[Monitor] be_polling skip {pos.symbol} ({pos.broker_id}): "
                        f"cTrader sans market_ts (freshness indéterminée)"
                    )
                    skipped += 1
                    continue
                ts_ref = fq.market_ts
                freshness_kind = "market_ts"
            else:
                # TradeLocker (et autres brokers sans timestamp marché) :
                # on utilise observed_at (transport client), bien marqué
                # dans l'audit pour qu'on sache que c'est une fiabilité dégradée.
                ts_ref = fq.observed_at
                freshness_kind = "transport_observed_at"

            age_s = (now - ts_ref).total_seconds()
            if age_s > freshness_threshold_s or age_s < -5.0:
                # age négatif > 5s = horloge marché en avance, suspect → skip
                logger.debug(
                    f"[Monitor] be_polling skip {pos.symbol} ({pos.broker_id}): "
                    f"stale ou suspect (age={age_s:.0f}s, kind={freshness_kind})"
                )
                skipped += 1
                continue

            # 3. Traitement BE-only — _amend_in_progress côté
            # _process_pos_from_price → _check_breakeven → _try_amend_sl
            # garantit l'idempotence vs on_tick concurrent.
            # On capture old_sl AVANT l'amend pour l'audit : c'est le SL
            # effectif au moment où le polling déclenche le BE (peut
            # différer de sl_initial si un trailing précédent l'a déjà bougé).
            old_sl = pos.sl
            try:
                be_just_armed = await self._process_pos_from_price(
                    pos,
                    fq.price,
                    source="polling_backup",
                    do_trailing=False,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    f"[Monitor] be_polling _process_pos_from_price "
                    f"{pos.symbol} ({pos.broker_id}): {e}"
                )
                skipped += 1
                continue

            checked += 1

            if be_just_armed:
                armed += 1
                self._emit_be_polling_audit(
                    pos=pos,
                    fq=fq,
                    age_s=age_s,
                    freshness_kind=freshness_kind,
                    broker_kind=broker_kind,
                    old_sl=old_sl,
                )

        return checked, armed, skipped

    def _emit_be_polling_audit(
        self,
        pos: TrackedPosition,
        fq,  # FreshQuote (broker.base) — pas importé pour éviter import cyclique
        age_s: float,
        freshness_kind: str,
        broker_kind: str,
        old_sl: float,
    ) -> None:
        """Audit JSONL — un event par BE armé via le polling backup.

        Le payload respecte le contrat de gate Phase 2.5 : doit contenir
        ``broker_id``, ``quote_source``, ``market_ts``/``observed_at``,
        ``quote_age_s``, ``old_sl``, ``new_sl``. ``old_sl`` est le SL
        juste avant l'amend (peut différer de ``sl_initial`` si un
        trailing l'a déjà bougé). ``new_sl`` est le SL effectivement
        appliqué par le BE.
        """
        if not self._on_audit_event:
            return
        try:
            payload = {
                "event": "be_polling_armed",
                "ts": datetime.now(timezone.utc).isoformat(),
                "broker_id": pos.broker_id,
                "broker_kind": broker_kind,
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "side": pos.side.value,
                "entry": pos.entry,
                "old_sl": old_sl,
                "new_sl": pos.sl,
                "sl_initial": pos.sl_initial,
                "mfe_r_at_arm": round(pos.mfe_r, 4),
                "quote_price": fq.price,
                "quote_type": fq.quote_type,
                "quote_source": "polling_backup",
                "quote_market_ts": fq.market_ts.isoformat() if fq.market_ts else None,
                "quote_observed_at": fq.observed_at.isoformat(),
                "quote_freshness_kind": freshness_kind,
                "quote_age_s": round(age_s, 3),
            }
            self._on_audit_event(payload)
        except Exception as e:
            logger.debug(f"[Monitor] be_polling audit emit error: {e}")

    # ------------------------------------------------------------------
    # Hot Path #1 — heartbeat ReconcileReq 60s (incident DASHUSD 2026-05-21)
    # ------------------------------------------------------------------

    def _maybe_start_broker_reconcile(self) -> None:
        """Démarre la boucle si activée et pas déjà en cours.

        Appelé après chaque ``register_position()`` ; idempotent.
        """
        if not self._cfg.broker_reconcile_enabled:
            return
        if self._broker_reconcile_task and not self._broker_reconcile_task.done():
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return  # pas de loop courante (cas hors live)
        if not loop.is_running():
            return  # idem : on n'utilise pas create_task hors live
        self._broker_reconcile_stop = asyncio.Event()
        self._broker_reconcile_consecutive_timeouts = 0
        self._broker_reconcile_task = asyncio.create_task(
            self._broker_reconcile_loop(),
            name="broker_reconcile_loop",
        )
        logger.info(
            f"[Monitor] 🩺 reconcile broker actif "
            f"(interval={self._cfg.broker_reconcile_interval_s}s, "
            f"timeout={self._cfg.broker_reconcile_timeout_s}s, "
            f"missing_threshold={self._cfg.broker_reconcile_missing_threshold})"
        )

    async def stop_broker_reconcile(self) -> None:
        """Annule la boucle proprement (signal + cancel + await)."""
        task = self._broker_reconcile_task
        if not task:
            return
        if self._broker_reconcile_stop is not None:
            self._broker_reconcile_stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"[Monitor] broker_reconcile stop : exception ignorée: {e}")
        self._broker_reconcile_task = None
        self._broker_reconcile_stop = None

    async def _broker_reconcile_loop(self) -> None:
        """Boucle interne. Réveil toutes les
        ``broker_reconcile_interval_s`` secondes ; s'arrête d'elle-même
        quand ``_positions`` devient vide (anti-coût inutile)."""
        interval = self._cfg.broker_reconcile_interval_s
        stop_evt = self._broker_reconcile_stop
        assert stop_evt is not None

        try:
            while not stop_evt.is_set():
                # Sleep interruptible
                try:
                    await asyncio.wait_for(stop_evt.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    pass
                if stop_evt.is_set():
                    break

                # Auto-stop quand plus rien à surveiller
                if not self._positions:
                    logger.info(
                        "[Monitor] 🩺 reconcile broker arrêté (plus de positions)"
                    )
                    break

                try:
                    await self._broker_reconcile_pass()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(
                        f"[Monitor] broker_reconcile pass error (loop continues): {e}"
                    )
                    continue
        except asyncio.CancelledError:
            raise
        finally:
            # Auto-cleanup en sortie de boucle pour que _maybe_start puisse
            # ré-armer proprement si une nouvelle position s'ouvre plus tard
            self._broker_reconcile_task = None
            self._broker_reconcile_stop = None

    async def _broker_reconcile_pass(self) -> None:
        """Un passage : interroge chaque broker concerné une fois.

        Le résultat de ``list_open_positions_proto`` est :
          - ``None`` → broker injoignable / timeout → log WARNING (ou ERROR
            après ``missing_threshold`` cycles consécutifs), AUCUN compteur
            position incrémenté (on n'a pas la preuve d'absence)
          - liste (peut être vide) → comparaison avec ``_tracked_positions``
            par broker : toute position locale absente incrémente son
            ``broker_missing_cycles`` ; toute position locale présente
            réinitialise son compteur ; à ``missing_threshold`` cycles
            l'alerte URGENT tombe + retrait du tracking.
        """
        # Grouper par broker
        by_broker: Dict[str, List[str]] = {}
        for key, pos in self._positions.items():
            by_broker.setdefault(pos.broker_id, []).append(key)

        for broker_id, keys in by_broker.items():
            broker = self._brokers.get(broker_id)
            if broker is None:
                continue
            list_fn = getattr(broker, "list_open_positions_proto", None)
            if list_fn is None:
                # Broker ne supporte pas le protocole heartbeat (TradeLocker
                # n'a pas encore d'implémentation native) — silence, on
                # laisse la reconcile() 120s faire son job classique.
                continue

            try:
                broker_positions = await list_fn(
                    timeout_s=self._cfg.broker_reconcile_timeout_s
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    f"[Monitor] reconcile broker {broker_id}: appel échoué: {e}"
                )
                broker_positions = None

            if broker_positions is None:
                # Canal broker silencieux. On compte les cycles consécutifs.
                self._broker_reconcile_consecutive_timeouts += 1
                threshold = self._cfg.broker_reconcile_missing_threshold
                if self._broker_reconcile_consecutive_timeouts >= threshold:
                    logger.error(
                        f"[Monitor] 🚨 reconcile broker {broker_id} : "
                        f"{self._broker_reconcile_consecutive_timeouts} "
                        f"timeouts consécutifs — canal trading probablement mort"
                    )
                else:
                    logger.warning(
                        f"[Monitor] ⚠️ reconcile broker {broker_id} : "
                        f"timeout ou injoignable "
                        f"(cycle {self._broker_reconcile_consecutive_timeouts}"
                        f"/{threshold})"
                    )
                # Ne touche AUCUN compteur position (pas de preuve d'absence)
                continue

            # Le broker a répondu — reset compteur global de timeouts
            self._broker_reconcile_consecutive_timeouts = 0
            broker_pos_ids = {str(p.position_id) for p in broker_positions}

            for key in keys:
                pos = self._positions.get(key)
                if pos is None:
                    continue
                if pos.position_id in broker_pos_ids:
                    pos.broker_missing_cycles = 0
                    continue
                # Position locale absente côté broker
                pos.broker_missing_cycles += 1
                threshold = self._cfg.broker_reconcile_missing_threshold
                if pos.broker_missing_cycles < threshold:
                    logger.info(
                        f"[Monitor] ⏳ reconcile broker {broker_id} : "
                        f"position {pos.symbol} {pos.position_id} absente "
                        f"({pos.broker_missing_cycles}/{threshold}) — on re-vérifie"
                    )
                    continue
                # Seuil atteint → alerte URGENT + retrait
                self._emit_position_missing_broker(pos)
                self._positions.pop(key, None)

    def _emit_position_missing_broker(self, pos: TrackedPosition) -> None:
        """Construit le payload URGENT et invoque le callback enregistré.

        Le log ERROR est toujours émis (même si pas de callback wiré) pour
        garantir une trace dans journalctl.
        """
        logger.error(
            f"[Monitor] 🚨 URGENT — position absente broker : "
            f"{pos.symbol} {pos.position_id} ({pos.broker_id}) "
            f"après {pos.broker_missing_cycles} cycles consécutifs "
            f"— fermée broker-side à notre insu "
            f"(entry={pos.entry:.{pos.digits}f} sl={pos.sl:.{pos.digits}f} "
            f"mfe={pos.mfe_r:.2f}R be={'oui' if pos.breakeven_set else 'non'})"
        )
        if self._on_position_missing_broker is None:
            return
        try:
            self._on_position_missing_broker({
                "broker_id": pos.broker_id,
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "side": pos.side.value,
                "entry": pos.entry,
                "sl": pos.sl,
                "sl_initial": pos.sl_initial,
                "tp": pos.tp,
                "mfe_r": round(pos.mfe_r, 4),
                "breakeven_set": pos.breakeven_set,
                "trailing_tier": pos.trailing_tier,
                "missing_cycles": pos.broker_missing_cycles,
            })
        except Exception as cb_exc:
            logger.warning(
                f"[Monitor] on_position_missing_broker callback failed: {cb_exc}"
            )

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
                            # Notify LiveMonitor — try real fill first
                            exit_reason = self._estimate_exit_reason(pos)
                            exit_price = self._estimate_exit_price(pos, exit_reason)
                            exit_price_source = "estimated"
                            broker = self._brokers.get(pos.broker_id)
                            broker_bid_x, broker_ask_x = 0.0, 0.0
                            if broker:
                                try:
                                    real_fill = await broker.get_closed_position_detail(
                                        pos.position_id
                                    )
                                    if real_fill and real_fill.get("exit_price"):
                                        real_price = real_fill["exit_price"]
                                        slippage = abs(real_price - exit_price)
                                        if slippage > 0.0001:
                                            logger.info(
                                                f"[Monitor] 💰 Vrai fill "
                                                f"{pos.symbol}: {real_price:.5f} "
                                                f"(estimé {exit_price:.5f})"
                                            )
                                        exit_price = real_price
                                        exit_price_source = "real_fill"
                                except Exception:
                                    pass
                                try:
                                    tick = await broker.get_quote(pos.symbol)
                                    if tick:
                                        broker_bid_x = float(tick.bid or 0)
                                        broker_ask_x = float(tick.ask or 0)
                                except Exception:
                                    pass
                            if self._on_position_closed:
                                try:
                                    # be_source : path live, pos.breakeven_set
                                    # reflète l'état physique broker (True
                                    # uniquement après amend_position_sltp
                                    # success — cf. _check_breakeven l.674).
                                    self._on_position_closed(
                                        broker_id=pos.broker_id,
                                        position_id=pos.position_id,
                                        exit_price=exit_price,
                                        exit_reason=exit_reason,
                                        mfe_r=pos.mfe_r,
                                        be_set=pos.breakeven_set,
                                        be_source=(
                                            "broker_armed"
                                            if pos.breakeven_set
                                            else "not_armed"
                                        ),
                                        trailing_tier=pos.trailing_tier,
                                        broker_bid=broker_bid_x,
                                        broker_ask=broker_ask_x,
                                        exit_price_source=exit_price_source,
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
            # Étage 0 — notif Telegram+ntfy avec cooldown 30 min par position.
            # On alerte à chaque ABANDONED, mais pas plus d'une fois toutes les
            # 30 min sur la même position (sinon spam si le canal trading reste
            # mort pendant des heures, cf. incident DASHUSD 2026-05-21).
            if self._on_amend_abandoned is not None:
                now_alert = time.time()
                if now_alert - pos.last_amend_alert_time >= self._cfg.amend_alert_cooldown_s:
                    pos.last_amend_alert_time = now_alert
                    try:
                        self._on_amend_abandoned({
                            "broker_id": pos.broker_id,
                            "position_id": pos.position_id,
                            "symbol": pos.symbol,
                            "side": pos.side.value,
                            "target_sl": new_sl,
                            "current_sl": pos.sl,
                            "last_error": last_error,
                            "amend_failures": pos.amend_failures,
                            "mfe_r": pos.mfe_r,
                            "breakeven_set": pos.breakeven_set,
                            "trailing_tier": pos.trailing_tier,
                        })
                    except Exception as cb_exc:
                        logger.warning(
                            f"[Monitor] on_amend_abandoned callback failed: {cb_exc}"
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

                # Retirer les positions qui n'existent plus sur le broker.
                # On exige une corroboration : get_closed_position_detail doit
                # retourner un vrai fill, sinon on compte les cycles d'absence
                # consécutifs et on ne déclare fermée qu'après un fallback
                # (MISSING_CYCLES_FALLBACK cycles de 2 min = ~6 min d'absence
                # continue). Ceci évite les "phantom exits" quand get_positions()
                # retourne momentanément une liste incomplète (race côté broker).
                MISSING_CYCLES_FALLBACK = 3
                for key in keys:
                    pos = self._positions.get(key)
                    if not pos:
                        continue
                    if pos.position_id in broker_pos_ids:
                        # Position réapparue → reset du compteur d'absence
                        pos.missing_cycles = 0
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

                    # Corroborer l'absence via get_closed_position_detail
                    real_fill = None
                    try:
                        real_fill = await broker.get_closed_position_detail(
                            pos.position_id
                        )
                    except Exception as e:
                        logger.debug(
                            f"[Monitor] get_closed_position_detail failed "
                            f"for {pos.position_id}: {e}"
                        )

                    if not (real_fill and real_fill.get("exit_price")):
                        # Pas de confirmation broker → absence non corroborée
                        pos.missing_cycles += 1
                        if pos.missing_cycles < MISSING_CYCLES_FALLBACK:
                            logger.info(
                                f"[Monitor] ⏳ Position {pos.symbol} "
                                f"{pos.position_id} absente de get_positions() "
                                f"({pos.missing_cycles}/{MISSING_CYCLES_FALLBACK}) "
                                f"mais pas de closed_detail — on re-vérifie "
                                f"au prochain cycle"
                            )
                            continue
                        # Fallback : N cycles consécutifs d'absence sans
                        # closed_detail → on considère fermée quand même,
                        # avec estimation (probable si le broker n'expose
                        # tout simplement pas l'historique)
                        logger.warning(
                            f"[Monitor] ⚠️ Position {pos.symbol} "
                            f"{pos.position_id} absente depuis "
                            f"{pos.missing_cycles} cycles sans closed_detail "
                            f"— retrait forcé (fallback)"
                        )

                    self._positions.pop(key, None)

                    # Determine exit price / reason (réel si dispo, sinon estimé)
                    exit_reason = self._estimate_exit_reason(pos)
                    exit_price = self._estimate_exit_price(pos, exit_reason)
                    exit_price_source = "estimated"
                    if real_fill and real_fill.get("exit_price"):
                        real_price = real_fill["exit_price"]
                        slippage = abs(real_price - exit_price)
                        if slippage > 0.0001:
                            logger.info(
                                f"[Monitor] 💰 Vrai fill {pos.symbol} "
                                f"{pos.position_id}: {real_price:.5f} "
                                f"(estimé {exit_price:.5f}, "
                                f"slip={slippage:.5f})"
                            )
                        exit_price = real_price
                        exit_price_source = "real_fill"
                        # Adjust exit_reason if the real price tells us TP was hit
                        if pos.tp > 0:
                            if pos.side == Side.LONG and real_price >= pos.tp * 0.999:
                                exit_reason = "take_profit"
                            elif pos.side == Side.SHORT and real_price <= pos.tp * 1.001:
                                exit_reason = "take_profit"

                    # Snapshot quote courant (peut être ≠ prix d'exécution mais utile
                    # pour mesurer le spread broker au moment de la détection).
                    broker_bid_x, broker_ask_x = 0.0, 0.0
                    try:
                        tick = await broker.get_quote(pos.symbol)
                        if tick:
                            broker_bid_x = float(tick.bid or 0)
                            broker_ask_x = float(tick.ask or 0)
                    except Exception:
                        pass

                    logger.info(
                        f"[Monitor] 🗑️ Position {pos.symbol} {pos.position_id} "
                        f"non trouvée sur {broker_id} après {age:.0f}s — retirée "
                        f"(MFE={pos.mfe_r:.2f}R BE={'✓' if pos.breakeven_set else '✗'} "
                        f"trail={pos.trailing_tier} exit≈{exit_reason}"
                        f"{' REAL' if real_fill else ' EST'})"
                    )

                    # Notify LiveMonitor
                    if self._on_position_closed:
                        try:
                            # be_source : reconcile orphan, pos.breakeven_set
                            # est l'état physique broker mémorisé en live (True
                            # uniquement après amend_position_sltp success).
                            self._on_position_closed(
                                broker_id=broker_id,
                                position_id=pos.position_id,
                                exit_price=exit_price,
                                exit_reason=exit_reason,
                                mfe_r=pos.mfe_r,
                                be_set=pos.breakeven_set,
                                be_source=(
                                    "broker_armed"
                                    if pos.breakeven_set
                                    else "not_armed"
                                ),
                                trailing_tier=pos.trailing_tier,
                                broker_bid=broker_bid_x,
                                broker_ask=broker_ask_x,
                                exit_price_source=exit_price_source,
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

        # Checkpoint : persiste MFE/BE/trail courants pour survivre à un crash
        # dur (pas seulement SIGTERM). À chaque cycle reconcile (~2 min), le
        # state file est ré-écrit avec les valeurs en mémoire. Si l'engine
        # tombe entre deux cycles, au redémarrage `load_state()` restaurera
        # le dernier MFE connu — au pire 2 min de tracking perdu.
        try:
            self.save_state()
        except Exception as e:
            logger.debug(f"[Monitor] save_state checkpoint failed: {e}")

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
