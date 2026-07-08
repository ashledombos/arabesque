"""Hot Path Mode étape 1 — heartbeat ``ReconcileReq`` 60s + détection de
position absente broker (task #35).

Incident fondateur : 2026-05-20→22, position DASHUSD #53110148 fermée
broker-side (SL touché à -1R) **sans que l'engine le sache** pendant ~16h.
Le canal #3 « état broker » était complètement aveugle en régime normal.
Cf. ``docs/audit/HOT_PATH_MODE_2026-05-23.md`` §1 et §5.

Invariants verrouillés :
  1. Polling **inactif** quand ``_tracked_positions`` est vide
  2. Polling **actif** dès ``register_position()``
  3. Polling **s'arrête** quand la dernière position est unregistered
  4. Intervalle = ``broker_reconcile_interval_s`` entre 2 ReconcileReq successifs
  5. Timeout / broker injoignable (None) → log WARNING + retry au prochain cycle
  6. ``broker_reconcile_missing_threshold`` (=3) timeouts consécutifs → log ERROR
  7. Position locale présente + broker répond avec liste où elle est absente,
     pendant ``broker_reconcile_missing_threshold`` cycles consécutifs →
     callback URGENT (1 fois, cooldown implicite par retrait du tracking)
  8. Position retrouvée broker après absence partielle → reset compteur
  9. Position locale absente, broker la connaît → orpheline broker non gérée
     ici (laissée à ``reconcile()`` 120s, qui sait gérer les orphelines)
 10. Pas de spam : après alerte URGENT, la position est retirée de
     ``_tracked_positions`` ; aucun second event n'est émis pour la même
     position (le tracking est terminé)

Le test broker (`_MockBroker.list_open_positions_proto`) doit pouvoir
retourner :
  - ``None`` pour simuler timeout / broker injoignable
  - ``[]`` pour simuler « broker répond mais 0 position »
  - ``[Position(...)]`` pour simuler « broker répond avec liste »
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional

import pytest

from arabesque.core.models import Side
from arabesque.execution.position_monitor import (
    LivePositionMonitor,
    MonitorConfig,
)


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


@dataclass
class _MockPosition:
    """Mini-clone de ``arabesque.broker.base.Position`` (seul ``position_id``
    est lu par le hot path)."""
    position_id: str
    symbol: str = "TEST"


@dataclass
class _AmendResult:
    success: bool = True
    message: str = "ok"


class _MockBroker:
    """Broker minimal exposant ``list_open_positions_proto``.

    ``responses`` est une liste de valeurs renvoyées tour à tour (round-robin
    si plus de cycles que d'éléments). Chaque appel pop le premier élément ;
    une fois épuisé, le dernier élément est répété.
    """

    def __init__(self, responses: List[Optional[List[_MockPosition]]]):
        # Copie défensive (le test peut modifier la liste)
        self._responses = list(responses)
        self.calls: List[float] = []
        self.amends: List[tuple] = []

    async def list_open_positions_proto(
        self, timeout_s: float = 10.0
    ) -> Optional[List[_MockPosition]]:
        import time as _t
        self.calls.append(_t.monotonic())
        if not self._responses:
            return []
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)

    async def amend_position_sltp(self, position_id, stop_loss=None, take_profit=None):
        self.amends.append((position_id, stop_loss))
        return _AmendResult()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_monitor(
    *,
    broker: _MockBroker | None = None,
    interval_s: float = 0.05,
    timeout_s: float = 0.05,
    missing_threshold: int = 3,
    broker_reconcile_enabled: bool = True,
    on_position_missing_broker=None,
) -> LivePositionMonitor:
    cfg = MonitorConfig(
        min_amend_interval_s=0.0,
        tick_check_interval_s=0.0,
        broker_reconcile_enabled=broker_reconcile_enabled,
        broker_reconcile_interval_s=interval_s,
        broker_reconcile_timeout_s=timeout_s,
        broker_reconcile_missing_threshold=missing_threshold,
    )
    brokers = {"ftmo": broker} if broker else {}
    return LivePositionMonitor(
        brokers=brokers,
        config=cfg,
        on_position_missing_broker=on_position_missing_broker,
    )


def _register(mon: LivePositionMonitor, *, position_id: str = "P1",
              symbol: str = "DASHUSD", broker_id: str = "ftmo"):
    return mon.register_position(
        broker_id=broker_id,
        position_id=position_id,
        symbol=symbol,
        side=Side.LONG,
        entry=49.40,
        sl=46.70,
        tp=54.70,
        volume=1.0,
        digits=2,
    )


async def _wait_for(predicate, timeout: float = 2.0, poll: float = 0.01):
    """Petite barrière asynchrone pour éviter les sleeps fixes ; lève si
    ``predicate()`` ne devient pas truthy avant ``timeout``."""
    import time as _t
    start = _t.monotonic()
    while _t.monotonic() - start < timeout:
        if predicate():
            return True
        await asyncio.sleep(poll)
    raise AssertionError(f"predicate not met after {timeout}s")


# ---------------------------------------------------------------------------
# Invariants 1-3 — start / stop sur register / unregister
# ---------------------------------------------------------------------------


def test_broker_reconcile_inactive_when_no_positions():
    """Invariant 1 : 0 position trackée → aucune requête broker émise."""
    async def _runner():
        broker = _MockBroker(responses=[[]])
        mon = _make_monitor(broker=broker, interval_s=0.02)
        # On laisse le temps à un éventuel cycle de tourner
        await asyncio.sleep(0.1)
        assert mon._broker_reconcile_task is None
        assert broker.calls == []

    asyncio.run(_runner())


def test_broker_reconcile_starts_on_register_position():
    """Invariant 2 : register_position() démarre la boucle (au moins 1 appel)."""
    async def _runner():
        broker = _MockBroker(responses=[[_MockPosition("P1")]])
        mon = _make_monitor(broker=broker, interval_s=0.02)
        _register(mon)
        await _wait_for(lambda: len(broker.calls) >= 1, timeout=1.0)
        assert mon._broker_reconcile_task is not None
        await mon.stop_broker_reconcile()

    asyncio.run(_runner())


def test_broker_reconcile_stops_when_last_position_unregistered():
    """Invariant 3 : retirer la dernière position arrête la boucle."""
    async def _runner():
        broker = _MockBroker(responses=[[_MockPosition("P1")]])
        mon = _make_monitor(broker=broker, interval_s=0.02)
        _register(mon)
        await _wait_for(lambda: len(broker.calls) >= 1, timeout=1.0)
        n_before = len(broker.calls)
        mon.unregister_position("ftmo", "P1")
        # Laisser plusieurs intervalles passer
        await asyncio.sleep(0.15)
        # La boucle doit avoir détecté la vacuité et s'être arrêtée
        assert mon._broker_reconcile_task is None or mon._broker_reconcile_task.done()
        # Plus de nouveaux appels broker
        n_after = len(broker.calls)
        # Tolère 1 appel "queue" si le cycle tournait quand unregister a eu lieu
        assert n_after - n_before <= 1

    asyncio.run(_runner())


def test_broker_reconcile_disabled_by_config_never_starts():
    """Si ``broker_reconcile_enabled=False``, register ne démarre rien."""
    async def _runner():
        broker = _MockBroker(responses=[[_MockPosition("P1")]])
        mon = _make_monitor(
            broker=broker, interval_s=0.02, broker_reconcile_enabled=False
        )
        _register(mon)
        await asyncio.sleep(0.1)
        assert mon._broker_reconcile_task is None
        assert broker.calls == []

    asyncio.run(_runner())


# ---------------------------------------------------------------------------
# Invariant 4 — intervalle respecté
# ---------------------------------------------------------------------------


def test_broker_reconcile_respects_interval():
    """Invariant 4 : 2 appels consécutifs sont séparés d'au moins
    ``broker_reconcile_interval_s`` (test sur 3 appels)."""
    async def _runner():
        broker = _MockBroker(responses=[[_MockPosition("P1")]])
        mon = _make_monitor(broker=broker, interval_s=0.10)
        _register(mon)
        await _wait_for(lambda: len(broker.calls) >= 3, timeout=2.0)
        await mon.stop_broker_reconcile()
        deltas = [
            broker.calls[i + 1] - broker.calls[i]
            for i in range(len(broker.calls) - 1)
        ]
        # Pas exact (drift asyncio), on tolère une fenêtre [0.07, 0.20]s
        for d in deltas[:2]:
            assert 0.07 <= d <= 0.20, f"interval {d:.3f}s hors plage"

    asyncio.run(_runner())


# ---------------------------------------------------------------------------
# Invariants 5-6 — broker injoignable
# ---------------------------------------------------------------------------


def test_broker_reconcile_timeout_logs_warning_and_continues(caplog):
    """Invariant 5 : ``None`` (timeout) → log WARNING + cycle suivant tente
    encore. Pas d'alerte URGENT ni de retrait position tant qu'on n'a pas la
    preuve broker que la position a disparu."""
    async def _runner():
        # Première réponse None (timeout), puis position revue normalement
        broker = _MockBroker(responses=[None, [_MockPosition("P1")]])
        alerts: list[dict] = []
        mon = _make_monitor(
            broker=broker,
            interval_s=0.02,
            on_position_missing_broker=lambda p: alerts.append(p),
        )
        _register(mon)
        with caplog.at_level(logging.WARNING, logger="arabesque.live.position_monitor"):
            await _wait_for(lambda: len(broker.calls) >= 2, timeout=1.0)
            await mon.stop_broker_reconcile()
        # Pas d'alerte position absente (broker n'a pas répondu, c'est différent)
        assert alerts == []
        # Position toujours trackée
        assert "ftmo:P1" in mon._positions
        # WARNING émis pour le timeout
        msgs = [r.getMessage() for r in caplog.records]
        assert any("reconcile" in m.lower() and ("timeout" in m.lower() or "injoignable" in m.lower()) for m in msgs), \
            f"expected timeout WARNING, got: {msgs}"

    asyncio.run(_runner())


def test_broker_reconcile_three_consecutive_timeouts_logs_error(caplog):
    """Invariant 6 : 3 timeouts consécutifs → log ERROR explicite (canal
    trading mort)."""
    async def _runner():
        broker = _MockBroker(responses=[None])  # toutes les réponses = None
        alerts: list[dict] = []
        mon = _make_monitor(
            broker=broker,
            interval_s=0.02,
            on_position_missing_broker=lambda p: alerts.append(p),
        )
        _register(mon)
        with caplog.at_level(logging.ERROR, logger="arabesque.live.position_monitor"):
            await _wait_for(lambda: len(broker.calls) >= 3, timeout=1.0)
            # Laisser le 3ᵉ cycle se logger
            await asyncio.sleep(0.05)
            await mon.stop_broker_reconcile()
        # Aucune alerte position absente (on a pas la preuve)
        assert alerts == []
        # Position toujours trackée
        assert "ftmo:P1" in mon._positions
        # Au moins un ERROR émis après 3 timeouts
        err_msgs = [
            r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR
        ]
        assert any(
            "reconcile" in m.lower() and ("3" in m or "canal" in m.lower())
            for m in err_msgs
        ), f"expected ERROR after 3 timeouts, got: {err_msgs}"

    asyncio.run(_runner())


# ---------------------------------------------------------------------------
# Invariant 7 — alerte URGENT après ``missing_threshold`` cycles consécutifs
# ---------------------------------------------------------------------------


def test_broker_reconcile_alert_urgent_after_three_missing_cycles():
    """Invariant 7 : position locale présente, broker répond [] pendant
    3 cycles consécutifs → callback ``on_position_missing_broker`` appelé
    une fois avec payload explicite (broker_id, position_id, symbol,
    entry, sl, mfe_r), puis position retirée du tracking."""
    async def _runner():
        # Broker répond toujours [] (position absente côté broker)
        broker = _MockBroker(responses=[[]])
        alerts: list[dict] = []
        mon = _make_monitor(
            broker=broker,
            interval_s=0.02,
            missing_threshold=3,
            on_position_missing_broker=lambda p: alerts.append(p),
        )
        _register(mon)
        # On attend l'alerte (max 1s, devrait tomber sous 0.1s)
        await _wait_for(lambda: len(alerts) >= 1, timeout=1.5)
        await mon.stop_broker_reconcile()

        assert len(alerts) == 1, f"expected exactly 1 alert, got {len(alerts)}"
        payload = alerts[0]
        assert payload["broker_id"] == "ftmo"
        assert payload["position_id"] == "P1"
        assert payload["symbol"] == "DASHUSD"
        assert payload["entry"] == pytest.approx(49.40)
        assert payload["sl"] == pytest.approx(46.70)
        assert "mfe_r" in payload
        assert payload["missing_cycles"] == 3
        # Position retirée du tracking après alerte (anti-spam)
        assert "ftmo:P1" not in mon._positions

    asyncio.run(_runner())


def test_broker_reconcile_no_alert_below_threshold():
    """Invariant 7 (négatif) : 2 cycles d'absence puis position retrouvée →
    aucune alerte."""
    async def _runner():
        # Absent 2 cycles puis présent
        broker = _MockBroker(responses=[[], [], [_MockPosition("P1")]])
        alerts: list[dict] = []
        mon = _make_monitor(
            broker=broker,
            interval_s=0.02,
            missing_threshold=3,
            on_position_missing_broker=lambda p: alerts.append(p),
        )
        _register(mon)
        # Attendre au moins 4 appels broker (les 3 réponses + 1 cycle après)
        await _wait_for(lambda: len(broker.calls) >= 4, timeout=1.0)
        await mon.stop_broker_reconcile()
        assert alerts == []
        assert "ftmo:P1" in mon._positions

    asyncio.run(_runner())


# ---------------------------------------------------------------------------
# Invariant 8 — réapparition réinitialise le compteur
# ---------------------------------------------------------------------------


def test_broker_reconcile_reappearance_resets_counter():
    """Invariant 8 : pattern absent×2, présent×1, absent×3 → l'alerte tombe
    seulement après la deuxième séquence (le retour broker réinitialise le
    compteur)."""
    async def _runner():
        # absent, absent, présent, absent, absent, absent, ...
        broker = _MockBroker(responses=[
            [], [], [_MockPosition("P1")], [], [], [],
        ])
        alerts: list[dict] = []
        mon = _make_monitor(
            broker=broker,
            interval_s=0.02,
            missing_threshold=3,
            on_position_missing_broker=lambda p: alerts.append(p),
        )
        _register(mon)
        await _wait_for(lambda: len(alerts) >= 1, timeout=2.0)
        await mon.stop_broker_reconcile()
        # 1 seule alerte (jamais retomberait si compteur pas reset à l'index 2)
        assert len(alerts) == 1
        # Le compteur missing_cycles dans le payload doit être 3 (pas 5)
        assert alerts[0]["missing_cycles"] == 3

    asyncio.run(_runner())


# ---------------------------------------------------------------------------
# Invariant 10 — pas de spam : un seul event puis silence
# ---------------------------------------------------------------------------


def test_broker_reconcile_no_duplicate_alert_after_removal():
    """Invariant 10 : après l'alerte URGENT et le retrait du tracking,
    aucun second event n'est émis (la position n'est plus surveillée).
    """
    async def _runner():
        broker = _MockBroker(responses=[[]])
        alerts: list[dict] = []
        mon = _make_monitor(
            broker=broker,
            interval_s=0.02,
            missing_threshold=3,
            on_position_missing_broker=lambda p: alerts.append(p),
        )
        _register(mon)
        await _wait_for(lambda: len(alerts) >= 1, timeout=1.5)
        # Laisser plusieurs cycles supplémentaires
        n_calls_at_alert = len(broker.calls)
        await asyncio.sleep(0.15)
        await mon.stop_broker_reconcile()
        # Pas de 2ᵉ alerte
        assert len(alerts) == 1
        # La boucle doit s'être arrêtée car _positions vide après retrait
        # (donc à peu près aucun appel broker supplémentaire)
        assert len(broker.calls) <= n_calls_at_alert + 1

    asyncio.run(_runner())


# ---------------------------------------------------------------------------
# Bonus — multi-positions : 1 absente, 1 présente → 1 seule alerte
# ---------------------------------------------------------------------------


def test_broker_reconcile_mixed_positions_alerts_only_missing_one():
    """Si 2 positions trackées et le broker n'en confirme qu'une, seule
    l'absente déclenche l'alerte URGENT."""
    async def _runner():
        broker = _MockBroker(responses=[[_MockPosition("P2")]])
        alerts: list[dict] = []
        mon = _make_monitor(
            broker=broker,
            interval_s=0.02,
            missing_threshold=3,
            on_position_missing_broker=lambda p: alerts.append(p),
        )
        _register(mon, position_id="P1", symbol="DASHUSD")
        _register(mon, position_id="P2", symbol="BTCUSD")
        await _wait_for(lambda: len(alerts) >= 1, timeout=1.5)
        await mon.stop_broker_reconcile()
        assert len(alerts) == 1
        assert alerts[0]["position_id"] == "P1"
        # P2 toujours trackée, P1 retirée
        assert "ftmo:P1" not in mon._positions
        assert "ftmo:P2" in mon._positions

    asyncio.run(_runner())
