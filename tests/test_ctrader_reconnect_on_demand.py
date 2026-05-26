"""Étage 1 résilience broker (2026-05-21) — reconnect-on-demand quand un
ordre/amend/close tombe sur ``_connected=False``.

Incident fondateur : 2026-05-20T22:00 → 2026-05-21T01:42 UTC. Position DASHUSD
ouverte, canal Protobuf trading passé à ``_connected=False`` silencieusement
(ni ALREADY_LOGGED_IN, ni CH_ACCESS_TOKEN_INVALID, ni feed_stale). Les amend
BE/TSL ont retourné ``OrderResult(success=False, message="Not connected")``
pendant ~10 heures sans qu'aucune tentative de reconnexion ne soit faite. SL
côté broker resté à la valeur initiale ; position laissée sans protection BE
pendant tout le retracement de MFE +1.82R → flottant négatif.

Le patch Étage 1 introduit ``_try_reconnect_for_order(reason)`` :
  - appelé avant chaque retour ``"Not connected"`` (place_order, cancel_order,
    amend_position_sltp, close_position) ;
  - réutilise ``self.connect()`` (qui gère refresh OAuth + retries patches
    existants ALREADY_LOGGED_IN / CH_ACCESS_TOKEN_INVALID) ;
  - anti-boucle : cooldown 30s entre 2 attempts + max 3 attempts glissants
    sur 60s (au-delà, on laisse Étage 0 alerter au lieu de marteler cTrader).

Invariants verrouillés :
  1. ``_connected=False`` → ``_try_reconnect_for_order`` est appelé.
  2. Reconnect réussi → l'ordre est rejoué (un seul connect au passage).
  3. Reconnect échoué → retour ``OrderResult(success=False, "Not connected")``.
  4. Cooldown 30s : 2e appel < 30s après le 1er → bloqué (return False sans
     appeler ``connect``).
  5. Fenêtre 3/60s : un 4e attempt en moins de 60s est bloqué même si le
     cooldown 30s est franchi.
  6. Reconnect bloqué (anti-boucle) → l'ordre retourne quand même
     ``"Not connected"`` (pas de fail catastrophique).
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from arabesque.broker.base import OrderRequest, OrderResult, OrderSide, OrderType
from arabesque.broker.ctrader import CTraderBroker


def _build_broker_stub() -> CTraderBroker:
    """Construit un CTraderBroker minimal sans réseau (mirror du test
    test_ctrader_token_invalid_retry._build_broker_stub)."""
    broker = CTraderBroker.__new__(CTraderBroker)
    broker._client = None
    broker._connected = False
    broker._asyncio_loop = None
    broker._reactor_running = True
    broker._reactor_thread = None
    broker._token_refreshed = True
    broker.refresh_token = "stub-refresh"
    broker.access_token = "stub-access"
    broker.client_id = "stub-cid"
    broker.client_secret = "stub-secret"
    broker.account_id = 12345
    broker.broker_id = "stub-broker"
    broker.config = {"auto_refresh_token": False}
    broker._subscribed_symbol_ids = set()
    broker._pending_requests = {}
    # Étage 1 — flags anti-boucle initialisés dans __init__ normal
    broker._reconnect_cooldown_s = 30.0
    broker._reconnect_window_s = 60.0
    broker._reconnect_window_max = 3
    broker._last_reconnect_attempt = 0.0
    broker._reconnect_attempts_window = []
    # Hot Path #5 — couche 1 pré-câblage (task #39)
    broker._reconnect_failures_window = []
    return broker


def test_pending_orders_read_is_unknown_when_ctrader_disconnected():
    broker = _build_broker_stub()

    with pytest.raises(ConnectionError, match="not connected"):
        asyncio.run(broker.get_pending_orders())


# ---------------------------------------------------------------------------
# 1. Reconnect réussi → l'ordre peut continuer
# ---------------------------------------------------------------------------

def test_reconnect_on_demand_succeeds_first_try():
    """Premier appel à ``_try_reconnect_for_order`` avec ``connect`` qui retourne
    True → la méthode doit retourner True et avoir appelé ``connect`` 1 fois."""
    broker = _build_broker_stub()
    connect_calls = []

    async def fake_connect():
        connect_calls.append(True)
        broker._connected = True
        return True

    broker.connect = fake_connect

    ok = asyncio.run(broker._try_reconnect_for_order("amend_position_sltp"))

    assert ok is True
    assert len(connect_calls) == 1
    assert broker._connected is True
    assert len(broker._reconnect_attempts_window) == 1


# ---------------------------------------------------------------------------
# 2. Reconnect échoué → False (caller retournera "Not connected")
# ---------------------------------------------------------------------------

def test_reconnect_on_demand_returns_false_when_connect_fails():
    broker = _build_broker_stub()

    async def fake_connect():
        # _connected reste False
        return False

    broker.connect = fake_connect
    ok = asyncio.run(broker._try_reconnect_for_order("place_order"))

    assert ok is False
    assert broker._connected is False


def test_reconnect_on_demand_handles_connect_exception():
    """Si ``connect()`` lève une exception, ``_try_reconnect_for_order`` doit
    retourner False proprement (pas de crash du caller)."""
    broker = _build_broker_stub()

    async def fake_connect():
        raise RuntimeError("network down")

    broker.connect = fake_connect
    ok = asyncio.run(broker._try_reconnect_for_order("amend_position_sltp"))

    assert ok is False


# ---------------------------------------------------------------------------
# 3. Cooldown 30s entre 2 attempts
# ---------------------------------------------------------------------------

def test_reconnect_cooldown_blocks_second_attempt():
    """Un 2e appel < 30s après le 1er doit retourner False SANS appeler
    ``connect`` (évite de marteler cTrader)."""
    broker = _build_broker_stub()
    connect_calls = []

    async def fake_connect():
        connect_calls.append(True)
        return False

    broker.connect = fake_connect

    # 1er attempt
    asyncio.run(broker._try_reconnect_for_order("place_order"))
    assert len(connect_calls) == 1

    # 2e attempt immédiat (< 30s) → bloqué
    ok = asyncio.run(broker._try_reconnect_for_order("place_order"))
    assert ok is False
    assert len(connect_calls) == 1, (
        f"Cooldown violé : connect appelé {len(connect_calls)} fois, attendu 1"
    )


def test_reconnect_cooldown_released_after_30s():
    """Après expiration du cooldown 30s, un nouvel attempt doit pouvoir partir."""
    broker = _build_broker_stub()
    connect_calls = []

    async def fake_connect():
        connect_calls.append(True)
        broker._connected = True
        return True

    broker.connect = fake_connect

    # 1er attempt
    asyncio.run(broker._try_reconnect_for_order("place_order"))
    assert len(connect_calls) == 1

    # Simuler 31s plus tard
    broker._last_reconnect_attempt = time.time() - 31.0
    broker._connected = False  # déconnecté à nouveau

    ok = asyncio.run(broker._try_reconnect_for_order("place_order"))
    assert ok is True
    assert len(connect_calls) == 2


# ---------------------------------------------------------------------------
# 4. Fenêtre glissante 3/60s
# ---------------------------------------------------------------------------

def test_reconnect_window_blocks_fourth_attempt_within_60s():
    """4 attempts en moins de 60s : le 4e doit être bloqué par la fenêtre
    glissante, même si le cooldown 30s n'est plus actif."""
    broker = _build_broker_stub()
    connect_calls = []

    async def fake_connect():
        connect_calls.append(True)
        return False

    broker.connect = fake_connect

    now = time.time()
    # Simuler 3 attempts à t=-50s, t=-35s, t=-20s (chacun a respecté cooldown 30s)
    # Wait — pour respecter le cooldown 30s entre chaque, ils sont à 30s d'écart.
    # Au temps actuel t=0, les 3 attempts à t=-60.1s, -30.05s, -0.0001s
    # → tous dans la fenêtre 60s, mais le cooldown 30s va bloquer le 2e si on
    # le rejoue immédiatement.
    # Pour vraiment tester la fenêtre, on remplit la fenêtre artificiellement :
    broker._reconnect_attempts_window = [now - 50, now - 35, now - 20]
    broker._last_reconnect_attempt = now - 35  # le dernier date d'il y a 35s
    # (donc cooldown 30s n'est plus actif)

    # 4e attempt
    ok = asyncio.run(broker._try_reconnect_for_order("place_order"))
    assert ok is False, "Fenêtre 3/60s doit bloquer le 4e attempt"
    assert len(connect_calls) == 0, (
        f"Bloqué par anti-boucle, connect ne doit PAS être appelé, vu {len(connect_calls)}"
    )


def test_reconnect_window_purges_old_attempts():
    """Un attempt vieux de > 60s ne doit plus compter dans la fenêtre.
    3 attempts vieux de 70s + 1 nouveau attempt → doit passer."""
    broker = _build_broker_stub()
    connect_calls = []

    async def fake_connect():
        connect_calls.append(True)
        broker._connected = True
        return True

    broker.connect = fake_connect

    now = time.time()
    broker._reconnect_attempts_window = [now - 80, now - 75, now - 70]
    broker._last_reconnect_attempt = now - 70

    ok = asyncio.run(broker._try_reconnect_for_order("place_order"))
    assert ok is True, (
        "Les 3 attempts hors fenêtre 60s doivent être purgés, le nouveau doit passer"
    )
    assert len(connect_calls) == 1
    # La fenêtre ne doit contenir que le nouveau attempt
    assert len(broker._reconnect_attempts_window) == 1


# ---------------------------------------------------------------------------
# 5. Intégration : place_order avec _connected=False déclenche reconnect
# ---------------------------------------------------------------------------

def test_place_order_triggers_reconnect_when_disconnected():
    """``place_order`` avec ``_connected=False`` doit appeler
    ``_try_reconnect_for_order`` AVANT de retourner ``"Not connected"``."""
    broker = _build_broker_stub()
    reconnect_calls = []

    async def fake_reconnect(reason):
        reconnect_calls.append(reason)
        return False  # le reconnect échoue → comportement legacy

    broker._try_reconnect_for_order = fake_reconnect

    order = OrderRequest(
        symbol="EURUSD",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        volume=0.01,
    )
    result = asyncio.run(broker.place_order(order))

    assert result.success is False
    assert result.message == "Not connected"
    assert reconnect_calls == ["place_order"], (
        f"Reconnect doit être tenté avec raison 'place_order', vu {reconnect_calls}"
    )


def test_amend_position_sltp_triggers_reconnect_when_disconnected():
    """``amend_position_sltp`` avec ``_connected=False`` doit appeler
    ``_try_reconnect_for_order``."""
    broker = _build_broker_stub()
    reconnect_calls = []

    async def fake_reconnect(reason):
        reconnect_calls.append(reason)
        return False

    broker._try_reconnect_for_order = fake_reconnect

    result = asyncio.run(broker.amend_position_sltp("12345", stop_loss=50.0))

    assert result.success is False
    assert result.message == "Not connected"
    assert reconnect_calls == ["amend_position_sltp"]


def test_close_position_triggers_reconnect_when_disconnected():
    broker = _build_broker_stub()
    reconnect_calls = []

    async def fake_reconnect(reason):
        reconnect_calls.append(reason)
        return False

    broker._try_reconnect_for_order = fake_reconnect

    result = asyncio.run(broker.close_position("12345", volume=0.01))

    assert result.success is False
    assert result.message == "Not connected"
    assert reconnect_calls == ["close_position"]


def test_cancel_order_triggers_reconnect_when_disconnected():
    broker = _build_broker_stub()
    reconnect_calls = []

    async def fake_reconnect(reason):
        reconnect_calls.append(reason)
        return False

    broker._try_reconnect_for_order = fake_reconnect

    result = asyncio.run(broker.cancel_order("12345"))

    assert result.success is False
    assert result.message == "Not connected"
    assert reconnect_calls == ["cancel_order"]
