"""Hot Path #5 — pré-câblage couche 1 (task #39).

Compteur d'échecs de ``_try_reconnect_for_order`` exposé via la méthode
publique ``recent_reconnect_failures_count(window_s)``. Le watchdog
consommera ce compteur plus tard pour déclencher un restart service quand
le canal trading cTrader est durablement mort (≥ 2 échecs en 5 min en mode
hot path = position ouverte).

Phase actuelle : pré-câblage **passif**. Le broker compte, personne ne lit
encore. Aucun effet sur le live, aucun risque (juste de la mesure).

Invariants verrouillés :
  1. À l'init, le compteur est à zéro.
  2. Un reconnect qui réussit n'incrémente PAS le compteur.
  3. Un reconnect qui échoue parce que ``connect()`` retourne False incrémente.
  4. Un reconnect qui échoue parce que ``_connected`` reste False après
     ``connect()=True`` incrémente.
  5. Une exception levée par ``connect()`` est comptée comme échec.
  6. Un appel bloqué par anti-boucle (fenêtre 3/60s saturée) est compté
     comme échec (le canal est effectivement mort si on en est là).
  7. Un appel skipé par le cooldown 30s n'est PAS compté (c'est juste une
     attente, pas un échec de la cible).
  8. La fenêtre glissante respecte le paramètre ``window_s`` (entrées plus
     anciennes sont exclues du count).
  9. La méthode est purement lecture : appels successifs ne modifient pas
     la liste interne (lazy purge optionnelle, idempotente).
"""
from __future__ import annotations

import asyncio
import time

import pytest

from arabesque.broker.ctrader import CTraderBroker


def _build_broker_stub() -> CTraderBroker:
    """Stub minimal mirror de ``tests/test_ctrader_reconnect_on_demand.py``."""
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
    broker._reconnect_cooldown_s = 30.0
    broker._reconnect_window_s = 60.0
    broker._reconnect_window_max = 3
    broker._last_reconnect_attempt = 0.0
    broker._reconnect_attempts_window = []
    broker._reconnect_failures_window = []
    return broker


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. Init
# ---------------------------------------------------------------------------

def test_failures_counter_starts_at_zero():
    broker = _build_broker_stub()
    assert broker.recent_reconnect_failures_count() == 0
    assert broker.recent_reconnect_failures_count(window_s=60) == 0
    assert broker.recent_reconnect_failures_count(window_s=3600) == 0


# ---------------------------------------------------------------------------
# 2. Reconnect succeed → pas d'incrément
# ---------------------------------------------------------------------------

def test_successful_reconnect_does_not_increment():
    broker = _build_broker_stub()

    async def fake_connect():
        broker._connected = True
        return True

    broker.connect = fake_connect
    ok = _run(broker._try_reconnect_for_order("test_success"))
    assert ok is True
    assert broker.recent_reconnect_failures_count() == 0


# ---------------------------------------------------------------------------
# 3. connect() retourne False → incrément
# ---------------------------------------------------------------------------

def test_failed_reconnect_connect_returns_false_increments():
    broker = _build_broker_stub()

    async def fake_connect():
        return False

    broker.connect = fake_connect
    ok = _run(broker._try_reconnect_for_order("test_fail_returns_false"))
    assert ok is False
    assert broker.recent_reconnect_failures_count() == 1


# ---------------------------------------------------------------------------
# 4. connect() retourne True mais _connected reste False → incrément
# ---------------------------------------------------------------------------

def test_failed_reconnect_connect_true_but_disconnected_increments():
    broker = _build_broker_stub()

    async def fake_connect():
        # Cas pathologique : connect retourne True mais le flag reste à False
        # (par exemple si la subscription post-connect a échoué).
        return True

    broker.connect = fake_connect
    # _connected reste False par construction du stub
    ok = _run(broker._try_reconnect_for_order("test_true_but_disconnected"))
    assert ok is False
    assert broker.recent_reconnect_failures_count() == 1


# ---------------------------------------------------------------------------
# 5. Exception levée par connect() → incrément
# ---------------------------------------------------------------------------

def test_exception_during_reconnect_increments():
    broker = _build_broker_stub()

    async def fake_connect():
        raise RuntimeError("network down")

    broker.connect = fake_connect
    ok = _run(broker._try_reconnect_for_order("test_exception"))
    assert ok is False
    assert broker.recent_reconnect_failures_count() == 1


# ---------------------------------------------------------------------------
# 6. Anti-boucle (fenêtre 3/60s saturée) → incrément
# ---------------------------------------------------------------------------

def test_antiloop_blocked_increments_failures():
    """Quand l'anti-boucle bloque, le canal est effectivement mort (3 tentatives
    en 60s sans succès = silence broker). On compte ça comme échec pour
    permettre au watchdog d'escalader."""
    broker = _build_broker_stub()
    now = time.time()
    # Pré-charge 3 attempts récents pour saturer la fenêtre
    broker._reconnect_attempts_window = [now - 50, now - 30, now - 10]
    # Et un _last_reconnect_attempt > cooldown pour qu'on passe le check cooldown
    broker._last_reconnect_attempt = now - 31

    async def fake_connect():
        # Ne devrait jamais être appelé — l'anti-boucle bloque avant
        raise AssertionError("connect should not be called when antiloop blocks")

    broker.connect = fake_connect
    ok = _run(broker._try_reconnect_for_order("test_antiloop"))
    assert ok is False
    assert broker.recent_reconnect_failures_count() == 1


# ---------------------------------------------------------------------------
# 7. Cooldown skip → PAS d'incrément (c'est juste une attente)
# ---------------------------------------------------------------------------

def test_cooldown_skip_does_not_increment_failures():
    broker = _build_broker_stub()
    now = time.time()
    # _last_reconnect_attempt il y a 10s → cooldown 30s actif
    broker._last_reconnect_attempt = now - 10

    async def fake_connect():
        raise AssertionError("connect should not be called during cooldown")

    broker.connect = fake_connect
    ok = _run(broker._try_reconnect_for_order("test_cooldown"))
    assert ok is False
    # Pas d'incrément : le cooldown n'est pas un échec, c'est un skip volontaire
    assert broker.recent_reconnect_failures_count() == 0


# ---------------------------------------------------------------------------
# 8. Fenêtre glissante respectée
# ---------------------------------------------------------------------------

def test_window_excludes_old_failures():
    broker = _build_broker_stub()
    now = time.time()
    # Pré-charge : 1 vieux échec (10 min), 2 récents (1 min, 2 min)
    broker._reconnect_failures_window = [now - 600, now - 120, now - 60]

    # Fenêtre 300s (5 min) → exclut le vieux à 600s
    assert broker.recent_reconnect_failures_count(window_s=300) == 2
    # Fenêtre 1000s → inclut les 3
    assert broker.recent_reconnect_failures_count(window_s=1000) == 3
    # Fenêtre 30s → exclut tout
    assert broker.recent_reconnect_failures_count(window_s=30) == 0


# ---------------------------------------------------------------------------
# 9. Méthode read-only : appels successifs idempotents (ne consomment pas)
# ---------------------------------------------------------------------------

def test_count_is_idempotent():
    broker = _build_broker_stub()
    now = time.time()
    broker._reconnect_failures_window = [now - 60, now - 30, now - 5]

    c1 = broker.recent_reconnect_failures_count(window_s=300)
    c2 = broker.recent_reconnect_failures_count(window_s=300)
    c3 = broker.recent_reconnect_failures_count(window_s=300)
    assert c1 == c2 == c3 == 3
