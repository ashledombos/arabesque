"""Option 1 (2026-05-18) — bypass `existing_broker._connected` quand feed stale.

Root cause (cf. docs/REVIEW_BTCUSD_STALE_EXISTING_BROKER_2026-05-18.md) :
``_watch_connection`` lève ``ConnectionError`` quand le feed est stale, mais
``_broker._connected`` reste ``True`` côté Python. À la retry suivante,
``_connect_and_subscribe`` tombe dans la branche « Réutilisation du broker
existant » et ne refait jamais de vraie reconnexion TCP — boucle infinie
(45 reconnects observés le 2026-05-18 17:18→19:13 UTC, BTCUSD stale 6766s).

Patch Option 1 : ``_run_loop`` arme ``self._force_reconnect = True`` après
toute exception, et ``_connect_and_subscribe`` consomme ce flag en début
pour détruire/reset le broker existant **avant** de retomber dans le chemin
standard. La branche « Réutilisation » reste utilisable au boot normal.

Ces tests verrouillent les invariants :
  1. ``_force_reconnect`` est ``False`` par défaut.
  2. Path startup (flag=False, broker sain) → réutilisation inchangée,
     pas de ``_cleanup_for_retry``, pas de reset broker.
  3. Path stale (flag=True, broker sain en apparence) → log warning
     « force reconnect after stale feed — bypass existing broker »,
     ``_cleanup_for_retry`` appelé, ``_broker`` reset à ``None``, flag
     remis à ``False``.
  4. Pas d'ordre/close/amend envoyé pendant le cleanup.
  5. Si flag armé sans broker (cas dégénéré) → flag reset, pas de crash.
  6. Si ``_cleanup_for_retry`` lève → on continue, broker reset quand même.
  7. ``_run_loop`` arme le flag après toute exception remontée par
     ``_connect_and_subscribe`` / ``_watch_connection``.
  8. ``self._callbacks`` (callbacks consumer enregistrés via ``subscribe()``)
     est préservé à travers le bypass.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List
from unittest.mock import patch

import pytest

from arabesque.execution.price_feed import PriceFeedManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_feed_stub(symbols: List[str] | None = None) -> PriceFeedManager:
    """PriceFeedManager minimal sans réseau (bypass __init__ réseau-touchant)."""
    feed = PriceFeedManager.__new__(PriceFeedManager)
    feed.broker_id = "stub_broker"
    feed.broker_cfg = {}
    feed.symbols = list(symbols or ["BTCUSD", "ETHUSD"])
    feed.reconnect_delay_s = 5.0
    feed.token_refresh_interval_h = 60.0
    feed._broker = None
    feed._running = False
    feed._connected = False
    feed._main_task = None
    feed._token_refresh_task = None
    feed._callbacks = {}
    feed._tick_counts = {}
    feed._last_tick_times = {}
    feed._reconnect_count = 0
    feed._start_time = None
    feed._alert_sent = False
    feed._alert_threshold_reconnects = 3
    feed._force_reconnect = False
    return feed


class _FakeBroker:
    """Broker stub : trace cleanup/get_symbols/subscribe, n'envoie rien réel."""

    def __init__(self, *, connected: bool = True, raise_cleanup: bool = False):
        self._connected = connected
        self._subscribed_symbol_ids = {1, 2, 3} if connected else set()
        self._spot_callbacks: dict = {}
        self.cleanup_calls = 0
        self.cleanup_raises = raise_cleanup
        self.get_symbols_calls = 0
        self.subscribe_batch_calls: List[dict] = []
        self.send_calls: list = []  # toute requête envoyée (doit rester vide)

    async def _cleanup_for_retry(self) -> None:
        self.cleanup_calls += 1
        if self.cleanup_raises:
            raise RuntimeError("cleanup boom (test)")

    async def get_symbols(self) -> dict:
        self.get_symbols_calls += 1
        return {s: i for i, s in enumerate(("BTCUSD", "ETHUSD"), start=1)}

    async def subscribe_spots_batch(self, symbols_and_callbacks: dict) -> dict:
        self.subscribe_batch_calls.append(symbols_and_callbacks)
        return {s: True for s in symbols_and_callbacks}

    def _resolve_symbol_id(self, symbol: str):
        return {"BTCUSD": 1, "ETHUSD": 2}.get(symbol)


# ---------------------------------------------------------------------------
# 1. Default state
# ---------------------------------------------------------------------------

def test_force_reconnect_flag_default_false_on_init():
    """À l'init complet, ``_force_reconnect`` doit être ``False``.

    Ce test utilise le vrai ``__init__`` (pas le stub) pour verrouiller la
    valeur par défaut côté constructeur.
    """
    feed = PriceFeedManager(
        broker_id="stub",
        broker_cfg={},
        symbols=["BTCUSD"],
    )
    assert feed._force_reconnect is False


# ---------------------------------------------------------------------------
# 2. Path startup normal — flag=False, broker sain → réutilisation préservée
# ---------------------------------------------------------------------------

def test_startup_reuses_healthy_broker_when_flag_false():
    """Path nominal : ``_force_reconnect=False`` + ``_broker._connected=True``
    → on entre dans la branche « Réutilisation du broker existant »,
    pas de cleanup, pas de reset broker.
    """
    feed = _build_feed_stub()
    fake = _FakeBroker(connected=True)
    feed._broker = fake
    feed._force_reconnect = False

    # Stub _watch_connection pour ne pas bloquer
    async def fake_watch():
        return

    feed._watch_connection = fake_watch

    asyncio.run(feed._connect_and_subscribe())

    # Pas de cleanup_for_retry sur path nominal
    assert fake.cleanup_calls == 0, (
        "REGRESSION : path startup normal ne doit PAS appeler "
        "_cleanup_for_retry sur un broker sain."
    )
    # Broker préservé (réutilisation)
    assert feed._broker is fake
    # Callbacks Python rafraîchis (clear sur réutilisation)
    # Souscription TCP pas refaite (already_subscribed=True)
    assert fake.subscribe_batch_calls == [], (
        "Path réutilisation : pas de nouvelle souscription TCP attendue."
    )
    # Flag inchangé / reset
    assert feed._force_reconnect is False


# ---------------------------------------------------------------------------
# 3. Path stale — flag=True, broker en apparence sain → bypass
# ---------------------------------------------------------------------------

def test_stale_force_reconnect_cleanups_and_resets_broker(caplog):
    """Path stale : ``_force_reconnect=True`` + ``_broker._connected=True``
    → log warning explicite, ``_cleanup_for_retry`` appelé, ``_broker`` reset
    à ``None``, flag remis à ``False``.
    """
    feed = _build_feed_stub()
    fake = _FakeBroker(connected=True)
    feed._broker = fake
    feed._force_reconnect = True

    # Stub la suite de _connect_and_subscribe (qui va créer un nouveau broker
    # et lever ConnectionError parce qu'on n'a pas patché CTraderBroker).
    # On capte juste l'état après le bloc bypass via une exception artificielle.
    with patch(
        "arabesque.broker.ctrader.CTraderBroker",
        side_effect=AssertionError("STOP: new broker creation reached"),
    ), caplog.at_level(logging.WARNING, logger="arabesque.live.price_feed"):
        with pytest.raises(AssertionError, match="STOP"):
            asyncio.run(feed._connect_and_subscribe())

    # Cleanup appelé exactement 1×
    assert fake.cleanup_calls == 1, (
        f"Attendu 1 _cleanup_for_retry, vu {fake.cleanup_calls}."
    )
    # Broker reset à None (force le chemin "nouveau broker" en aval)
    assert feed._broker is None
    # Flag consommé
    assert feed._force_reconnect is False
    # Log warning explicite émis
    assert any(
        "force reconnect after stale feed — bypass existing broker" in r.message
        for r in caplog.records
    ), "Le log warning d'identification doit être émis (audit/grep)."


# ---------------------------------------------------------------------------
# 4. Pas d'ordre/close/amend pendant cleanup (invariant trade-safety)
# ---------------------------------------------------------------------------

def test_force_reconnect_does_not_send_orders():
    """Le bypass ne doit envoyer **aucune** requête trade (ordre, close, amend).

    Le ``_cleanup_for_retry`` côté CTraderBroker n'émet qu'un
    ``ProtoOAUnsubscribeSpotsReq`` (téléchargement de ticks) puis ``stopService``
    — pas de payload trade. On vérifie ici qu'aucun appel "send" trade-related
    n'est routé par le bypass côté PriceFeed.
    """
    feed = _build_feed_stub()

    class _TraceBroker(_FakeBroker):
        def __init__(self):
            super().__init__(connected=True)
            self.order_sends = 0

        def send_market_order(self, *a, **kw):
            self.order_sends += 1

        def close_position(self, *a, **kw):
            self.order_sends += 1

        def amend_position(self, *a, **kw):
            self.order_sends += 1

    trace = _TraceBroker()
    feed._broker = trace
    feed._force_reconnect = True

    with patch(
        "arabesque.broker.ctrader.CTraderBroker",
        side_effect=AssertionError("STOP"),
    ):
        with pytest.raises(AssertionError):
            asyncio.run(feed._connect_and_subscribe())

    assert trace.order_sends == 0, (
        "REGRESSION : le bypass ne doit JAMAIS envoyer d'ordre / close / amend."
    )


# ---------------------------------------------------------------------------
# 5. Flag armé sans broker — cas dégénéré
# ---------------------------------------------------------------------------

def test_force_reconnect_with_no_broker_resets_flag():
    """``_force_reconnect=True`` mais ``_broker is None`` → flag reset à False,
    pas de crash, branche else (nouveau broker) prend le relais normalement.
    """
    feed = _build_feed_stub()
    feed._broker = None
    feed._force_reconnect = True

    with patch(
        "arabesque.broker.ctrader.CTraderBroker",
        side_effect=AssertionError("STOP"),
    ):
        with pytest.raises(AssertionError):
            asyncio.run(feed._connect_and_subscribe())

    assert feed._force_reconnect is False
    assert feed._broker is None


# ---------------------------------------------------------------------------
# 6. cleanup qui lève → on continue, broker reset quand même
# ---------------------------------------------------------------------------

def test_cleanup_exception_does_not_block_reset(caplog):
    """Si ``_cleanup_for_retry`` lève, on log warning et on reset le broker
    à ``None`` tout de même (sinon on se condamne à rester bloqué).
    """
    feed = _build_feed_stub()
    boom = _FakeBroker(connected=True, raise_cleanup=True)
    feed._broker = boom
    feed._force_reconnect = True

    with patch(
        "arabesque.broker.ctrader.CTraderBroker",
        side_effect=AssertionError("STOP"),
    ), caplog.at_level(logging.WARNING, logger="arabesque.live.price_feed"):
        with pytest.raises(AssertionError):
            asyncio.run(feed._connect_and_subscribe())

    assert boom.cleanup_calls == 1
    assert feed._broker is None, (
        "Même si cleanup lève, broker doit être reset (sinon boucle infinie)."
    )
    assert feed._force_reconnect is False
    assert any(
        "cleanup_for_retry ignoré" in r.message for r in caplog.records
    ), "Le log warning sur cleanup en échec doit être présent."


# ---------------------------------------------------------------------------
# 6 bis. broker sans _cleanup_for_retry → AttributeError catché, reset propre
# ---------------------------------------------------------------------------

def test_broker_without_cleanup_method_still_resets():
    """Cas dégénéré : un broker dont la classe n'expose pas
    ``_cleanup_for_retry`` (ex: rétro-compat ou broker tiers). L'``await``
    sur méthode absente lève ``AttributeError``, mais elle est catchée par
    ``except Exception`` → broker reset + flag reset comme attendu.
    """
    feed = _build_feed_stub()

    class _BrokerSansCleanup:
        _connected = True
        _subscribed_symbol_ids = {1, 2}
        _spot_callbacks = {}
        # PAS de méthode _cleanup_for_retry

    feed._broker = _BrokerSansCleanup()
    feed._force_reconnect = True

    with patch(
        "arabesque.broker.ctrader.CTraderBroker",
        side_effect=AssertionError("STOP"),
    ):
        with pytest.raises(AssertionError):
            asyncio.run(feed._connect_and_subscribe())

    assert feed._broker is None, (
        "AttributeError sur broker sans _cleanup_for_retry doit quand même "
        "déclencher reset (sinon on reste bloqué)."
    )
    assert feed._force_reconnect is False


# ---------------------------------------------------------------------------
# 7. _run_loop arme le flag sur exception
# ---------------------------------------------------------------------------

def test_run_loop_sets_force_reconnect_on_exception():
    """``_run_loop`` doit armer ``_force_reconnect=True`` après toute exception
    remontée par ``_connect_and_subscribe`` (qui inclut les ConnectionError
    levées par ``_watch_connection`` sur feed stale).
    """
    feed = _build_feed_stub()
    feed._running = True
    feed._force_reconnect = False

    call_count = [0]

    async def fake_connect_and_subscribe():
        call_count[0] += 1
        if call_count[0] == 1:
            # Première itération : on lève comme si feed stale
            raise ConnectionError("Feed stale (majeur crypto): test")
        # Deuxième itération : on stoppe la boucle pour pouvoir asserter
        feed._running = False

    async def fake_sleep(_):
        return  # pas d'attente réelle

    feed._connect_and_subscribe = fake_connect_and_subscribe

    with patch("asyncio.sleep", new=fake_sleep), \
         patch.object(feed, "_send_alert", new=lambda *a, **kw: None):
        asyncio.run(feed._run_loop())

    # Au moins 1 exception levée, donc flag armé après catch
    # (avant que l'itération suivante le consomme via _connect_and_subscribe).
    # Comme le stub n'appelle pas _connect_and_subscribe réellement, le flag
    # reste à True à la fin.
    assert feed._force_reconnect is True, (
        "REGRESSION : _run_loop doit armer _force_reconnect=True après "
        "toute exception remontée (sinon le bypass ne s'amorce jamais)."
    )


# ---------------------------------------------------------------------------
# 8. Callbacks consumer préservés à travers le bypass
# ---------------------------------------------------------------------------

def test_consumer_callbacks_preserved_through_bypass():
    """``self._callbacks`` (callbacks Python enregistrés via ``subscribe()``)
    doit être intact à travers le bypass — seul ``_broker._spot_callbacks``
    (côté broker) est rafraîchi/reset.
    """
    feed = _build_feed_stub()
    async def cb(tick):
        pass
    feed._callbacks = {"BTCUSD": [cb], "ETHUSD": [cb]}

    fake = _FakeBroker(connected=True)
    feed._broker = fake
    feed._force_reconnect = True

    with patch(
        "arabesque.broker.ctrader.CTraderBroker",
        side_effect=AssertionError("STOP"),
    ):
        with pytest.raises(AssertionError):
            asyncio.run(feed._connect_and_subscribe())

    # Callbacks consumer intacts
    assert feed._callbacks == {"BTCUSD": [cb], "ETHUSD": [cb]}, (
        "REGRESSION : le bypass ne doit pas toucher aux callbacks consumer "
        "PriceFeed-level (self._callbacks)."
    )
