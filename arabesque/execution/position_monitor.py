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
from arabesque.modules.position_manager import (
    next_session_deadline_utc,
    parse_session_exit,
)

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

    # Session exit (stratégies à heure de mur, ex. session-or) —
    # session_exit_at > 0 ⇒ AUCUN overlay (BE/trailing/polling désactivés),
    # sortie market au passage du mur par la boucle _session_close_loop.
    strategy: str = ""
    session_exit_at: float = 0.0          # epoch UTC du mur (0 = pas de mur)
    exit_label: str = ""                  # raison imposée au reconcile (ex. "session_exit")
    exit_price_hint: float = 0.0          # quote côté position au moment du close (estimation)
    session_close_failures: int = 0
    session_close_requested_at: float = 0.0   # throttle re-close (anti-spam POSITION_NOT_FOUND)
    last_session_close_alert_time: float = 0.0

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
    # Session exit (stratégies à heure de mur, ex. session-or 08:00 Londres).
    # Map strategy → "HH:MM@Zone/IANA". Une position de ces stratégies n'a
    # AUCUN overlay (BE/trailing/polling skippés) et est fermée market au mur.
    session_exit_by_strategy: Dict[str, str] = field(default_factory=dict)
    session_close_interval_s: float = 10.0
    # Broker injoignable au mur : retry chaque cycle, alerte URGENT à partir
    # de N échecs consécutifs (cooldown anti-spam), JAMAIS d'exit inventé.
    session_close_failures_before_alert: int = 3
    session_close_alert_cooldown_s: float = 1800.0
    # Close accepté mais position toujours trackée (reconcile pas encore
    # passé, ou broker menteur) : re-tenter le close après ce délai.
    session_close_reissue_after_s: float = 180.0


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
        on_session_close_failed: Optional[Callable[[dict], None]] = None,
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
        # Session exit — parse fail-fast des specs (config invalide = erreur
        # au démarrage, pas au milieu d'une session ouverte). Même parseur et
        # même règle de deadline que PositionManager (source unique).
        self._on_session_close_failed = on_session_close_failed
        self._session_exit_specs: Dict[str, tuple] = {
            strat: parse_session_exit(spec)
            for strat, spec in (self._cfg.session_exit_by_strategy or {}).items()
        }
        self._session_close_task: Optional[asyncio.Task] = None
        self._session_close_stop: Optional[asyncio.Event] = None
        # Snapshot du state file AU DÉMARRAGE : register_position() persiste
        # immédiatement (Hot Path #2) et ÉCRASE le fichier pendant la
        # réconciliation de démarrage, AVANT l'appel à load_state() — sans ce
        # snapshot, l'état pré-restart (MFE/BE/trailing/mur session) serait
        # perdu à chaque restart (bug capté par le test save/load du lot 2).
        self._state_snapshot: dict = {}
        if STATE_FILE.exists():
            try:
                self._state_snapshot = json.loads(STATE_FILE.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(
                    f"[Monitor] ⚠️ Snapshot état illisible {STATE_FILE}: {e}")

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
                "strategy": pos.strategy,
                "session_exit_at": pos.session_exit_at,
                "exit_label": pos.exit_label,
                "exit_price_hint": pos.exit_price_hint,
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
        # Priorité au snapshot pris à l'init (le fichier a pu être écrasé par
        # les save_state() des register de la réconciliation de démarrage)
        state = self._state_snapshot
        self._state_snapshot = {}
        if not state:
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
            # Session exit : l'état sauvegardé prime (mur ancré sur l'entrée
            # d'origine ; un close déjà accepté garde son label pour reconcile)
            if saved.get("strategy"):
                pos.strategy = saved["strategy"]
            if saved.get("session_exit_at"):
                pos.session_exit_at = saved["session_exit_at"]
            if saved.get("exit_label"):
                pos.exit_label = saved["exit_label"]
            if saved.get("exit_price_hint"):
                pos.exit_price_hint = saved["exit_price_hint"]
            restored += 1
            logger.info(
                f"[Monitor] 🔄 État restauré: {pos.symbol} "
                f"MFE={pos.mfe_r:.2f}R BE={'✓' if pos.breakeven_set else '✗'} "
                f"trail={pos.trailing_tier}"
            )

        # Re-persister l'état ENRICHI (les save_state des register de la
        # réconciliation avaient écrit l'état frais) ; supprime le fichier
        # s'il n'y a plus de position (comportement save_state standard).
        try:
            self.save_state()
        except Exception as e:
            logger.debug(f"[Monitor] save_state post-restore failed: {e}")
        if restored:
            logger.info(f"[Monitor] ✅ {restored} position(s) restaurée(s) depuis état précédent")
            # Une position à mur restaurée doit relancer la boucle session
            # (un mur passé pendant le downtime ferme au premier cycle)
            self._maybe_start_session_close()
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
        strategy: str = "",
        entry_ts: "datetime | str | None" = None,
    ) -> TrackedPosition:
        """Enregistre une position après un fill réussi.

        ``strategy`` + ``entry_ts`` (datetime aware/naïf-UTC ou ISO string)
        servent au mécanisme session_exit : si la stratégie est mappée dans
        ``session_exit_by_strategy``, le mur est ancré sur l'heure d'entrée
        (journal à la ré-adoption, sinon maintenant). Un mur déjà passé
        déclenche le close au premier cycle de la boucle session.
        """
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
            strategy=strategy,
        )
        spec = self._session_exit_specs.get(strategy) if strategy else None
        if spec is not None:
            anchor = self._normalize_entry_ts(entry_ts)
            deadline = next_session_deadline_utc(anchor, spec[0], spec[1])
            pos.session_exit_at = deadline.timestamp()
        self._positions[key] = pos
        session_note = (
            f" mur_session={datetime.fromtimestamp(pos.session_exit_at, tz=timezone.utc).isoformat()}"
            if pos.session_exit_at > 0 else ""
        )
        logger.info(
            f"[Monitor] 📋 Registered {symbol} {side.value} "
            f"entry={entry:.{digits}f} SL={sl:.{digits}f} TP={tp:.{digits}f} "
            f"R={pos.R:.{digits}f} ({broker_id}:{position_id}){session_note}"
        )
        # Hot Path #2 — persistance immédiate pour que les composants externes
        # (feed_watchdog notamment, qui décide du skip weekend) voient
        # l'ouverture sans attendre le cycle reconcile (~2 min).
        try:
            self.save_state()
        except Exception as e:
            logger.debug(f"[Monitor] save_state on register failed: {e}")
        # Hot Path #1 — auto-démarre la boucle broker_reconcile dès la
        # première position trackée (no-op si déjà en route ou désactivée).
        self._maybe_start_broker_reconcile()
        # Session exit — auto-démarre la boucle de fermeture au mur dès la
        # première position à mur (no-op si déjà en route ou aucune).
        self._maybe_start_session_close()
        return pos

    @staticmethod
    def _normalize_entry_ts(entry_ts) -> datetime:
        """Normalise l'ancre du mur en datetime UTC aware (défaut : now)."""
        if entry_ts is None:
            return datetime.now(timezone.utc)
        if isinstance(entry_ts, str):
            try:
                entry_ts = datetime.fromisoformat(entry_ts)
            except ValueError:
                logger.warning(
                    f"[Monitor] entry_ts illisible ({entry_ts!r}) — ancre=now")
                return datetime.now(timezone.utc)
        if entry_ts.tzinfo is None:
            return entry_ts.replace(tzinfo=timezone.utc)
        return entry_ts.astimezone(timezone.utc)

    def unregister_position(self, broker_id: str, position_id: str):
        """Retire une position (fermée ou annulée)."""
        key = f"{broker_id}:{position_id}"
        if key in self._positions:
            pos = self._positions.pop(key)
            logger.info(
                f"[Monitor] 🗑️ Unregistered {pos.symbol} ({broker_id}:{position_id})"
            )
            # Hot Path #2 — persistance immédiate. Quand la dernière position
            # se ferme, save_state supprime le fichier d'état → le watchdog
            # peut rebasculer en skip weekend dès le prochain cycle.
            try:
                self.save_state()
            except Exception as e:
                logger.debug(f"[Monitor] save_state on unregister failed: {e}")

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

            # Session exit : AUCUN overlay (le mur est la seule sortie gérée,
            # design session-or 07-04 §4 — BE/trailing détruisent l'edge)
            if pos.session_exit_at > 0:
                continue

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

        # Session exit : AUCUN overlay (MFE tracké pour l'audit uniquement)
        if pos.session_exit_at > 0:
            return False

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
        skip_reasons: dict[str, int] = {}
        now = datetime.now(timezone.utc)

        def count_skip(reason: str) -> None:
            nonlocal skipped
            skipped += 1
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

        # Snapshot pour ne pas itérer sur un dict modifié pendant le polling
        positions_snapshot = list(self._positions.values())

        for pos in positions_snapshot:
            if pos.session_exit_at > 0:
                continue  # session exit : pas de BE, le polling ne s'applique pas
            if pos.breakeven_set:
                continue  # déjà BE, rien à faire ici
            broker = self._brokers.get(pos.broker_id)
            if broker is None:
                count_skip("broker_missing")
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
                count_skip("quote_error")
                continue

            if fq is None:
                # Pas de tick frais ou broker non supporté — on attend le
                # prochain cycle. Pas d'alerte, pas de reconcile.
                count_skip("quote_none")
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
                    count_skip("ctrader_missing_market_ts")
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
                count_skip("quote_stale_or_clock_skew")
                continue

            # 3. Traitement BE-only — _amend_in_progress côté
            # _process_pos_from_price → _check_breakeven → _try_amend_sl
            # garantit l'idempotence vs on_tick concurrent.
            # On capture old_sl AVANT l'amend pour l'audit : c'est le SL
            # effectif au moment où le polling déclenche le BE (peut
            # différer de sl_initial si un trailing précédent l'a déjà bougé).
            old_sl = pos.sl
            was_be_set = pos.breakeven_set
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
                count_skip("process_error")
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
            self._emit_be_polling_decision(
                pos=pos,
                fq=fq,
                age_s=age_s,
                freshness_kind=freshness_kind,
                broker_kind=broker_kind,
                old_sl=old_sl,
                was_be_set=was_be_set,
                be_just_armed=be_just_armed,
            )

        self._emit_be_polling_pass_audit(
            checked=checked,
            armed=armed,
            skipped=skipped,
            open_positions=len(positions_snapshot),
            skip_reasons=skip_reasons,
        )
        return checked, armed, skipped

    def _emit_be_polling_pass_audit(
        self,
        *,
        checked: int,
        armed: int,
        skipped: int,
        open_positions: int,
        skip_reasons: dict[str, int],
    ) -> None:
        """Audit every BE polling pass while positions are open.

        ``be_polling_armed`` proves the happy path. This pass-level metric also
        proves the backup was alive when it had nothing to amend, or why it
        could not evaluate an open position.
        """
        if not self._on_audit_event or open_positions <= 0:
            return
        try:
            self._on_audit_event({
                "event": "be_polling_pass",
                "ts": datetime.now(timezone.utc).isoformat(),
                "open_positions": open_positions,
                "checked": checked,
                "armed": armed,
                "skipped": skipped,
                "skip_reasons": skip_reasons,
            })
        except Exception as e:
            logger.debug(f"[Monitor] be_polling pass audit emit error: {e}")

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

    def _emit_be_polling_decision(
        self,
        *,
        pos: TrackedPosition,
        fq,  # FreshQuote (broker.base) — pas importé pour éviter import cyclique
        age_s: float,
        freshness_kind: str,
        broker_kind: str,
        old_sl: float,
        was_be_set: bool,
        be_just_armed: bool,
    ) -> None:
        """Audit une position évaluée par le polling BE.

        ``be_polling_armed`` prouve uniquement le happy path. Cet event
        couvre aussi les cas utiles au diagnostic post-mortem : prix pas encore
        éligible, position déjà couverte, ou position éligible mais non armée
        (min interval, amend concurrent, broker manquant, etc.).
        """
        if not self._on_audit_event:
            return
        try:
            if be_just_armed:
                decision = "armed"
                reason = None
            elif pos.breakeven_set:
                decision = "already_armed"
                reason = None
            elif pos.mfe_r < self._cfg.be_trigger_r:
                decision = "not_eligible"
                reason = "mfe_below_trigger"
            else:
                decision = "eligible_not_armed"
                reason = self._be_not_armed_reason(pos, fq.price)

            payload = {
                "event": "be_polling_decision",
                "ts": datetime.now(timezone.utc).isoformat(),
                "broker_id": pos.broker_id,
                "broker_kind": broker_kind,
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "side": pos.side.value,
                "entry": pos.entry,
                "old_sl": old_sl,
                "current_sl": pos.sl,
                "sl_initial": pos.sl_initial,
                "tp": pos.tp,
                "mfe_r": round(pos.mfe_r, 4),
                "be_trigger_r": self._cfg.be_trigger_r,
                "be_offset_r": self._cfg.be_offset_r,
                "breakeven_set_before": was_be_set,
                "breakeven_set_after": pos.breakeven_set,
                "decision": decision,
                "reason": reason,
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
            logger.debug(f"[Monitor] be_polling decision emit error: {e}")

    def _be_not_armed_reason(self, pos: TrackedPosition, current_price: float) -> str:
        """Best-effort reason for an eligible BE that did not arm."""
        broker = self._brokers.get(pos.broker_id)
        if broker is None:
            return "broker_missing"
        if pos._amend_in_progress:
            return "amend_in_progress"
        if time.time() - pos.last_amend_time < self._cfg.min_amend_interval_s:
            return "min_amend_interval"
        if pos.side == Side.LONG:
            be_level = pos.entry + self._cfg.be_offset_r * pos.R
            if round(be_level, pos.digits) <= pos.sl:
                return "sl_already_covers_be"
            if current_price > 0 and round(be_level, pos.digits) > current_price:
                return "price_fell_back_before_amend"
        else:
            be_level = pos.entry - self._cfg.be_offset_r * pos.R
            if round(be_level, pos.digits) >= pos.sl:
                return "sl_already_covers_be"
            if current_price > 0 and round(be_level, pos.digits) < current_price:
                return "price_fell_back_before_amend"
        if pos.amend_failures > 0:
            return "amend_failed"
        return "unknown"

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

    # ------------------------------------------------------------------
    # Session exit — fermeture à heure de mur (ex. session-or 08:00 Londres)
    # ------------------------------------------------------------------

    def _maybe_start_session_close(self) -> None:
        """Démarre la boucle si une position à mur est trackée ; idempotent."""
        if not any(p.session_exit_at > 0 for p in self._positions.values()):
            return
        if self._session_close_task and not self._session_close_task.done():
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return  # pas de loop courante (cas hors live)
        if not loop.is_running():
            return
        self._session_close_stop = asyncio.Event()
        self._session_close_task = asyncio.create_task(
            self._session_close_loop(),
            name="session_close_loop",
        )
        logger.info(
            f"[Monitor] ⏰ session_close actif "
            f"(interval={self._cfg.session_close_interval_s}s, "
            f"strategies={sorted(self._session_exit_specs)})"
        )

    async def stop_session_close(self) -> None:
        """Annule la boucle proprement (signal + cancel + await)."""
        task = self._session_close_task
        if not task:
            return
        if self._session_close_stop is not None:
            self._session_close_stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"[Monitor] session_close stop : exception ignorée: {e}")
        self._session_close_task = None
        self._session_close_stop = None

    async def _session_close_loop(self) -> None:
        """Boucle interne. S'arrête d'elle-même quand plus aucune position
        à mur n'est trackée. Toute exception d'une passe est avalée pour ne
        JAMAIS tuer la boucle (le retry est la protection, pas le crash)."""
        interval = self._cfg.session_close_interval_s
        stop_evt = self._session_close_stop
        assert stop_evt is not None

        try:
            while not stop_evt.is_set():
                try:
                    await asyncio.wait_for(stop_evt.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    pass
                if stop_evt.is_set():
                    break

                if not any(p.session_exit_at > 0 for p in self._positions.values()):
                    logger.info(
                        "[Monitor] ⏰ session_close arrêté (plus de position à mur)")
                    break

                try:
                    await self._session_close_pass()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(
                        f"[Monitor] session_close pass error (loop continues): {e}")
                    continue
        except asyncio.CancelledError:
            raise
        finally:
            self._session_close_task = None
            self._session_close_stop = None

    async def _session_close_pass(self, now: float | None = None) -> int:
        """Un passage : ferme market toute position dont le mur est passé.

        Retourne le nombre de close ACCEPTÉS par le broker pendant ce passage.
        La position n'est PAS retirée du tracking ici : reconcile() corrobore
        la fermeture (vrai fill) et notifie avec ``exit_label`` — on ne
        fabrique jamais d'exit (cf. règle reconcile broker injoignable).
        """
        now = time.time() if now is None else now
        closed = 0
        for pos in list(self._positions.values()):
            if pos.session_exit_at <= 0 or now < pos.session_exit_at:
                continue
            # Close déjà demandé récemment → on laisse reconcile confirmer ;
            # re-tente après session_close_reissue_after_s (broker menteur,
            # ou reconcile pas encore passé).
            if (pos.session_close_requested_at
                    and now - pos.session_close_requested_at
                    < self._cfg.session_close_reissue_after_s):
                continue
            if await self._close_session_position(pos, now):
                closed += 1
        return closed

    async def _close_session_position(self, pos: TrackedPosition,
                                      now: float) -> bool:
        """Tente le close market d'une position au mur. True si accepté."""
        broker = self._brokers.get(pos.broker_id)
        if broker is None:
            self._register_session_close_failure(pos, now, "broker_missing")
            return False

        # Quote best-effort AVANT le close : estimation honnête du fill si le
        # broker n'expose pas le closed_detail (reconcile préfère le vrai fill).
        try:
            tick = await broker.get_quote(pos.symbol)
            if tick:
                hint = tick.bid if pos.side == Side.LONG else tick.ask
                if hint:
                    pos.exit_price_hint = float(hint)
        except Exception:
            pass

        try:
            result = await broker.close_position(pos.position_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._register_session_close_failure(pos, now, str(e))
            return False

        if result is not None and getattr(result, "success", False):
            pos.exit_label = "session_exit"
            pos.session_close_requested_at = now
            pos.session_close_failures = 0
            logger.info(
                f"[Monitor] ⏰ Session exit: close market {pos.symbol} "
                f"{pos.position_id} ({pos.broker_id}) au mur "
                f"{datetime.fromtimestamp(pos.session_exit_at, tz=timezone.utc).isoformat()} "
                f"(MFE={pos.mfe_r:.2f}R, hint={pos.exit_price_hint:.{pos.digits}f}) "
                f"— reconcile confirmera le fill"
            )
            try:
                self.save_state()
            except Exception as e:
                logger.debug(f"[Monitor] save_state on session close failed: {e}")
            return True

        message = str(getattr(result, "message", result))
        if "POSITION_NOT_FOUND" in message:
            # Déjà fermée broker-side (SL touché avant le mur, close manuel…).
            # PAS de label session : reconcile estimera la vraie raison.
            pos.session_close_requested_at = now  # throttle les re-tentatives
            logger.info(
                f"[Monitor] ⏰ Session exit {pos.symbol} {pos.position_id}: "
                f"POSITION_NOT_FOUND — déjà fermée broker-side, reconcile nettoiera"
            )
            return False

        self._register_session_close_failure(pos, now, message)
        return False

    def _register_session_close_failure(self, pos: TrackedPosition,
                                        now: float, error: str) -> None:
        """Comptabilise un échec de close au mur. Retry au prochain cycle,
        alerte URGENT à partir de N échecs consécutifs (cooldown anti-spam).
        La position reste trackée, protégée par son SL broker-side — JAMAIS
        d'exit inventé, l'edge se dégrade marginalement (risque n°2 assumé)."""
        pos.session_close_failures += 1
        threshold = self._cfg.session_close_failures_before_alert
        logger.warning(
            f"[Monitor] ⚠️ Session close échoué "
            f"({pos.session_close_failures}/{threshold} avant alerte): "
            f"{pos.symbol} {pos.position_id} ({pos.broker_id}) — {error} "
            f"— retry dans {self._cfg.session_close_interval_s:.0f}s"
        )
        if pos.session_close_failures < threshold:
            return
        if now - pos.last_session_close_alert_time < self._cfg.session_close_alert_cooldown_s:
            return
        pos.last_session_close_alert_time = now
        logger.error(
            f"[Monitor] 🚨 URGENT — session close IMPOSSIBLE: {pos.symbol} "
            f"{pos.position_id} ({pos.broker_id}) après "
            f"{pos.session_close_failures} tentatives — position déborde du mur "
            f"(SL broker-side toujours en place), dernier échec: {error}"
        )
        if self._on_session_close_failed is None:
            return
        try:
            self._on_session_close_failed({
                "broker_id": pos.broker_id,
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "side": pos.side.value,
                "strategy": pos.strategy,
                "entry": pos.entry,
                "sl": pos.sl,
                "session_exit_at": datetime.fromtimestamp(
                    pos.session_exit_at, tz=timezone.utc).isoformat(),
                "failures": pos.session_close_failures,
                "last_error": error,
                "mfe_r": round(pos.mfe_r, 4),
            })
        except Exception as cb_exc:
            logger.warning(
                f"[Monitor] on_session_close_failed callback failed: {cb_exc}")

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
                        pos.position_id,
                        stop_loss=new_sl,
                        # cTrader interprets an omitted TP on
                        # ProtoOAAmendPositionSLTPReq as TP removal.  Preserve
                        # the strategy target whenever BE/trailing tightens
                        # only the stop.
                        take_profit=pos.tp if pos.tp > 0 else None,
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
                            broker_gross_x = broker_commission_x = broker_swap_x = None
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
                                        # P&L réalisé broker (additif, best-effort)
                                        broker_gross_x = real_fill.get("gross_profit")
                                        broker_commission_x = real_fill.get("commission")
                                        broker_swap_x = real_fill.get("swap")
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
                                        broker_gross_profit=broker_gross_x,
                                        broker_commission=broker_commission_x,
                                        broker_swap=broker_swap_x,
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
                    broker_gross_x = broker_commission_x = broker_swap_x = None
                    if real_fill and real_fill.get("exit_price"):
                        real_price = real_fill["exit_price"]
                        # P&L réalisé broker (additif, best-effort)
                        broker_gross_x = real_fill.get("gross_profit")
                        broker_commission_x = real_fill.get("commission")
                        broker_swap_x = real_fill.get("swap")
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
                                broker_gross_profit=broker_gross_x,
                                broker_commission=broker_commission_x,
                                broker_swap=broker_swap_x,
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
        if pos.exit_label:
            # Fermeture initiée par le monitor lui-même (ex. session_exit au
            # mur) : la raison est CONNUE, pas estimée.
            return pos.exit_label
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
        if reason == "session_exit":
            # Close market au mur : quote capturée juste avant le close,
            # sinon entry (neutre). Le vrai fill de reconcile prime toujours.
            return pos.exit_price_hint or pos.entry
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
                    "strategy": p.strategy,
                    "session_exit_at": (
                        datetime.fromtimestamp(
                            p.session_exit_at, tz=timezone.utc).isoformat()
                        if p.session_exit_at > 0 else None
                    ),
                }
                for p in self._positions.values()
            ],
        }
