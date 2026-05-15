"""Phase 2.5 — non-régression critique sur ``CTraderBroker.get_fresh_quote``.

Le contrat fondamental est : ``get_fresh_quote()`` ne doit JAMAIS lire
``_price_ticks`` (le cache alimenté par le stream SpotEvent). C'est tout
l'intérêt de la méthode : être indépendante du PriceFeed pour servir de
backup quand le stream meurt silencieusement (cas ALREADY_LOGGED_IN du
14/05).

Deux tests complémentaires :
1. **Statique** — inspection du source : aucune référence au cache.
2. **Fonctionnel** — stub ``_send_via_reactor`` retournant un payload vide ;
   vérifie que la méthode retourne ``None`` même si ``_price_ticks`` contient
   une valeur favorable (pas de fallback silencieux).
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from arabesque.broker.base import PriceTick
from arabesque.broker.ctrader import CTraderBroker


def _collect_all_names(code) -> set[str]:
    """Récupère co_names + co_varnames + co_freevars + co_cellvars
    pour ce code object ET récursivement pour les inner functions/closures.
    """
    names: set[str] = set()
    names.update(code.co_names)
    names.update(code.co_varnames)
    names.update(getattr(code, "co_freevars", ()))
    names.update(getattr(code, "co_cellvars", ()))
    for const in code.co_consts:
        if hasattr(const, "co_names"):
            names |= _collect_all_names(const)
    return names


def test_get_fresh_quote_bytecode_does_not_reference_price_ticks_cache():
    """Inspection du bytecode (pas du source — évite les faux positifs sur
    la docstring) : ``get_fresh_quote`` et ses inner functions ne référencent
    NI ``_price_ticks`` NI ``get_last_tick``.

    Si une refacto réintroduit un fallback cache, ce test échoue
    instantanément, avant même de tenter une exécution.
    """
    code = CTraderBroker.get_fresh_quote.__code__
    referenced = _collect_all_names(code)

    forbidden = {"_price_ticks", "get_last_tick"}
    leaks = forbidden & referenced
    assert not leaks, (
        f"REGRESSION Phase 2.5 : get_fresh_quote référence {leaks} dans son "
        "bytecode. Le cache SpotEvent peut être stale (stream mort) — c'est "
        "précisément le cas que get_fresh_quote doit éviter. Aucun fallback "
        "silencieux autorisé."
    )


class _StubPayload:
    """Mime ``ProtoOAGetTickDataRes`` avec un champ ``tickData``."""

    def __init__(self, tick_data=None):
        self.tickData = tick_data or []


def _build_broker_with_stub(*, stub_empty: bool = True) -> CTraderBroker:
    """Construit un CTraderBroker minimal sans réseau, avec ``_send_via_reactor``
    stubbé pour résoudre immédiatement la future avec un payload vide
    (simule "aucun tick dans la fenêtre").
    """
    broker = CTraderBroker.__new__(CTraderBroker)
    broker._connected = True
    broker.account_id = 12345
    broker._pending_requests = {}
    broker._symbols_lock = asyncio.Lock()
    broker._tick_data_lock = asyncio.Lock()

    class _SymbolInfo:
        digits = 5

    broker._symbols = {99: _SymbolInfo()}
    broker._symbol_id_for_name = lambda name: 99 if name == "TEST" else None
    broker.map_symbol = lambda name: None
    broker._get_divisor = lambda sid: 100000.0
    broker._symbol_id_to_unified = {99: "TEST"}

    # Cache STALE favorable — si get_fresh_quote y fallback, on le détecte
    broker._price_ticks = {
        99: PriceTick(symbol="TEST", bid=100.50, ask=100.52)
    }

    def _stub_send(req):
        loop = asyncio.get_event_loop()
        def _resolve():
            fut = broker._pending_requests.get("tickdata")
            if fut and not fut.done():
                payload = _StubPayload(tick_data=[])
                fut.set_result(payload)
        loop.call_soon(_resolve)

    broker._send_via_reactor = _stub_send
    broker._resolve_future = lambda f, v: (
        f.set_result(v) if not f.done() else None
    )
    return broker


def test_get_fresh_quote_returns_none_when_proto_empty_does_not_fallback_to_cache():
    """Stream Proto retourne tickData=[] sur les 2 fenêtres → None.

    Critique : même si ``_price_ticks`` contient un PriceTick avec bid favorable,
    ``get_fresh_quote`` doit retourner None (pas de fallback silencieux).
    """
    broker = _build_broker_with_stub(stub_empty=True)

    result = asyncio.run(broker.get_fresh_quote("TEST", "bid"))

    assert result is None, (
        "REGRESSION Phase 2.5 : Proto vide → get_fresh_quote doit retourner None. "
        "Si tu as un PriceTick, c'est que tu fallback sur le cache — interdit."
    )


def test_get_fresh_quote_returns_none_when_unknown_symbol():
    """Symbole inconnu → None sans même tenter de requête."""
    broker = _build_broker_with_stub()
    result = asyncio.run(broker.get_fresh_quote("UNKNOWN_SYMBOL", "bid"))
    assert result is None


def test_get_fresh_quote_rejects_invalid_quote_type():
    """quote_type ∉ {bid, ask} → None."""
    broker = _build_broker_with_stub()
    result = asyncio.run(broker.get_fresh_quote("TEST", "mid"))
    assert result is None


def test_get_fresh_quote_returns_none_when_disconnected():
    """Broker déconnecté → None immédiatement (pas de requête réseau)."""
    broker = _build_broker_with_stub()
    broker._connected = False
    result = asyncio.run(broker.get_fresh_quote("TEST", "bid"))
    assert result is None
