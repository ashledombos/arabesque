"""Patch A+B (2026-05-18) — non-régression sur le retry ALREADY_LOGGED_IN.

Root cause (cf. HANDOFF + DECISIONS) : lors d'une boucle session fantôme,
``_stop_client()`` ferme uniquement le socket TCP — cTrader continue de voir
la session comme authentifiée jusqu'à expiration serveur (plusieurs minutes).
Les retries internes [30, 60, 120, 120, 120]s (7.5 min total) étaient sous
ce TTL → boucles 2h-8h observées en prod (incidents 12-05 / 14-05 / 18-05).

Le patch A+B introduit :
  A. Délais allongés ``_ALREADY_LOGGED_IN_RETRY_DELAYS = (60, 180, 600)``
     (14 min total).
  B. ``_cleanup_for_retry()`` : unsubscribe + stopService + reset état,
     équivalent au ``systemctl stop`` opérateur qui résout systématiquement
     la boucle.

Ces tests verrouillent les invariants :
  1. ``connect()`` sur ALREADY_LOGGED_IN appelle ``_cleanup_for_retry`` avant
     chaque retry (pas ``_stop_client``).
  2. Les délais retry sont exactement (60, 180, 600).
  3. ``_cleanup_for_retry`` est idempotent (rappelable sans exception).
  4. Une erreur non-ALREADY_LOGGED_IN reste sur le chemin actuel
     (``_stop_client`` minimal, return False immédiat).
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from arabesque.broker.ctrader import CTraderBroker


def _build_broker_stub() -> CTraderBroker:
    """Construit un CTraderBroker minimal sans réseau."""
    broker = CTraderBroker.__new__(CTraderBroker)
    broker._client = None
    broker._connected = False
    broker._asyncio_loop = None
    broker._reactor_running = True  # bypass _ensure_reactor_running
    broker._reactor_thread = None
    broker._token_refreshed = True   # bypass token refresh
    broker.refresh_token = ""
    broker.access_token = "stub"
    broker.client_id = "stub"
    broker.client_secret = "stub"
    broker.account_id = 12345
    broker.config = {"auto_refresh_token": False}
    broker._subscribed_symbol_ids = set()
    broker._pending_requests = {}
    return broker


# ---------------------------------------------------------------------------
# 1. retry_delays sont exactement (60, 180, 600)
# ---------------------------------------------------------------------------

def test_retry_delays_are_60_180_600():
    """Le tuple class-level est figé à (60, 180, 600) — total 14 min.

    Si quelqu'un revient à (30, 60, 120, 120, 120) ou raccourcit, on retombe
    sous le TTL cTrader et la boucle session fantôme revient en prod.
    """
    assert CTraderBroker._ALREADY_LOGGED_IN_RETRY_DELAYS == (60, 180, 600), (
        "REGRESSION patch A : les délais retry ALREADY_LOGGED_IN doivent "
        "être (60, 180, 600). Toute valeur < 600 max risque de retomber "
        "sous le TTL serveur cTrader (cf. incidents 14-05/18-05)."
    )


# ---------------------------------------------------------------------------
# 2. ALREADY_LOGGED_IN → cleanup propre avant retry (pas _stop_client)
# ---------------------------------------------------------------------------

def test_already_logged_in_calls_cleanup_for_retry_not_stop_client():
    """Sur ALREADY_LOGGED_IN, ``_cleanup_for_retry`` est appelé avant chaque
    retry, et ``_stop_client`` n'est PAS appelé (sauf in fine si tous les
    retries échouent).
    """
    broker = _build_broker_stub()

    cleanup_calls = []
    stop_calls = []
    sleep_calls = []

    async def fake_connect_once():
        raise Exception("cTrader Error: 2 - ALREADY_LOGGED_IN - "
                        "Open API application is already authorized")

    async def fake_cleanup():
        cleanup_calls.append(True)

    def fake_stop():
        stop_calls.append(True)

    async def fake_sleep(d):
        sleep_calls.append(d)

    broker._connect_once = fake_connect_once
    broker._cleanup_for_retry = fake_cleanup
    broker._stop_client = fake_stop

    with patch("asyncio.sleep", new=fake_sleep):
        result = asyncio.run(broker.connect())

    assert result is False
    # 4 tentatives au total (3 retries + 1 initial), donc 3 cleanups
    # et 1 stop_client final (chemin "attempt >= len(retry_delays)")
    assert len(cleanup_calls) == 3, (
        f"Attendu 3 _cleanup_for_retry, vu {len(cleanup_calls)}. Le retry "
        "ALREADY_LOGGED_IN doit appeler cleanup propre avant CHAQUE retry."
    )
    assert sleep_calls == [60, 180, 600], (
        f"Délais retry incorrects : {sleep_calls}. Doit être [60, 180, 600]."
    )
    # _stop_client est appelé 1× in fine quand on abandonne après 3 retries
    assert len(stop_calls) == 1


# ---------------------------------------------------------------------------
# 3. Erreur non-ALREADY_LOGGED_IN → chemin actuel inchangé (stop_client, no retry)
# ---------------------------------------------------------------------------

def test_other_error_keeps_current_path_no_retry():
    """Une erreur autre que ALREADY_LOGGED_IN doit garder le comportement
    actuel : ``_stop_client`` immédiat, return False, aucun retry, aucun
    cleanup propre (économise du temps sur les vraies erreurs fatales).
    """
    broker = _build_broker_stub()

    cleanup_calls = []
    stop_calls = []
    sleep_calls = []

    async def fake_connect_once():
        raise Exception("cTrader Error: 7 - INVALID_REQUEST - bad token")

    async def fake_cleanup():
        cleanup_calls.append(True)

    def fake_stop():
        stop_calls.append(True)

    async def fake_sleep(d):
        sleep_calls.append(d)

    broker._connect_once = fake_connect_once
    broker._cleanup_for_retry = fake_cleanup
    broker._stop_client = fake_stop

    with patch("asyncio.sleep", new=fake_sleep):
        result = asyncio.run(broker.connect())

    assert result is False
    assert cleanup_calls == [], (
        "REGRESSION : une erreur non-ALREADY_LOGGED_IN ne doit PAS déclencher "
        "_cleanup_for_retry (réservé aux sessions fantômes)."
    )
    assert len(stop_calls) == 1
    assert sleep_calls == [], (
        "REGRESSION : pas de sleep sur erreur fatale non-retryable."
    )


# ---------------------------------------------------------------------------
# 4. _cleanup_for_retry idempotent (rappelable sans exception)
# ---------------------------------------------------------------------------

def test_cleanup_for_retry_idempotent_on_already_clean_state():
    """``_cleanup_for_retry`` sur un broker déjà clean (client=None, pas
    d'abonnement) ne doit pas lever, et laisse l'état dans le bon état final.
    """
    broker = _build_broker_stub()
    # État déjà "clean"
    broker._client = None
    broker._connected = False
    broker._subscribed_symbol_ids = set()

    # Appel 1
    asyncio.run(broker._cleanup_for_retry())
    assert broker._client is None
    assert broker._connected is False
    assert broker._subscribed_symbol_ids == set()

    # Appel 2 (idempotence)
    asyncio.run(broker._cleanup_for_retry())
    assert broker._client is None
    assert broker._connected is False
    assert broker._subscribed_symbol_ids == set()


def test_cleanup_for_retry_resets_state_when_client_alive():
    """Quand un client + abonnements existent, ``_cleanup_for_retry`` doit
    tenter l'unsubscribe + stopService et reset l'état à zéro.
    """
    broker = _build_broker_stub()

    # Simule un client vivant + abonnements
    class _FakeClient:
        def __init__(self):
            self.stopped = False
        def stopService(self):
            self.stopped = True

    fake_client = _FakeClient()
    broker._client = fake_client
    broker._connected = True
    broker._subscribed_symbol_ids = {1, 2, 3}

    send_calls = []
    def fake_send(req):
        send_calls.append(req)
    broker._send_no_response = fake_send

    # Patch reactor.callFromThread pour exécuter directement (pas de Twisted réel)
    import twisted.internet
    original_call = twisted.internet.reactor.callFromThread
    twisted.internet.reactor.callFromThread = lambda fn, *a, **kw: fn(*a, **kw)

    try:
        asyncio.run(broker._cleanup_for_retry())
    finally:
        twisted.internet.reactor.callFromThread = original_call

    # Unsubscribe envoyé
    assert len(send_calls) == 1, "UnsubscribeSpotsReq doit être envoyé"
    # stopService appelé
    assert fake_client.stopped, "stopService doit être appelé"
    # État reseté
    assert broker._client is None
    assert broker._connected is False
    assert broker._subscribed_symbol_ids == set()


# ---------------------------------------------------------------------------
# 5. asyncio.TimeoutError → chemin court (pas de retry)
# ---------------------------------------------------------------------------

def test_timeout_error_no_retry():
    """``asyncio.TimeoutError`` reste sur le chemin court : ``_stop_client``
    minimal + return False. Pas de retry, pas de cleanup propre.
    """
    broker = _build_broker_stub()

    cleanup_calls = []
    stop_calls = []

    async def fake_connect_once():
        raise asyncio.TimeoutError()

    async def fake_cleanup():
        cleanup_calls.append(True)

    def fake_stop():
        stop_calls.append(True)

    broker._connect_once = fake_connect_once
    broker._cleanup_for_retry = fake_cleanup
    broker._stop_client = fake_stop

    result = asyncio.run(broker.connect())
    assert result is False
    assert cleanup_calls == []
    assert len(stop_calls) == 1
