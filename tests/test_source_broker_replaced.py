"""Fix incident 2026-06-08 — propagation du broker source après force-reconnect.

Quand le PriceFeed recrée un broker neuf (force-reconnect après feed stale),
``LiveEngine._on_source_broker_replaced`` doit basculer la référence partagée
``self._brokers[source_broker_id]`` (utilisée par le canal trading : lecture
pending orders, ordres, reconcile) sur la nouvelle connexion, ainsi que la
référence ``broker`` des BarAggregators (get_history). Sans ça, le feed se
reconnecte mais le trading reste bloqué fail-closed (~22h observées).
"""
from __future__ import annotations

from arabesque.execution.live import LiveEngine


class _Broker:
    def __init__(self, name):
        self.name = name


class _Agg:
    def __init__(self, broker):
        self.broker = broker


def _engine(brokers, aggs, source="ftmo_challenge"):
    eng = LiveEngine.__new__(LiveEngine)
    eng.settings = {"price_feed": {"source_broker": source}}
    eng._brokers = brokers
    eng._bar_aggregators = aggs
    return eng


def test_replaces_shared_broker_reference():
    old = _Broker("old")
    new = _Broker("new")
    brokers = {"ftmo_challenge": old, "gft_compte1": _Broker("gft")}
    aggs = {("h4", "extension"): _Agg(old), ("h1", "glissade"): _Agg(old)}
    eng = _engine(brokers, aggs)

    eng._on_source_broker_replaced(new)

    # Le dict partagé pointe sur le nouveau broker (canal trading rebasculé)
    assert brokers["ftmo_challenge"] is new
    # GFT non touché
    assert brokers["gft_compte1"].name == "gft"
    # Les aggregators qui tenaient l'ancien broker sont mis à jour
    assert all(a.broker is new for a in aggs.values())


def test_noop_when_same_broker():
    same = _Broker("same")
    brokers = {"ftmo_challenge": same}
    agg = _Agg(same)
    eng = _engine(brokers, {("h4", "extension"): agg})

    eng._on_source_broker_replaced(same)

    assert brokers["ftmo_challenge"] is same
    assert agg.broker is same


def test_aggregator_pointing_elsewhere_untouched():
    old = _Broker("old")
    new = _Broker("new")
    other = _Broker("other")
    brokers = {"ftmo_challenge": old}
    agg_old = _Agg(old)
    agg_other = _Agg(other)  # ne pointe pas sur l'ancien source → inchangé
    eng = _engine(brokers, {("a", "x"): agg_old, ("b", "y"): agg_other})

    eng._on_source_broker_replaced(new)

    assert agg_old.broker is new
    assert agg_other.broker is other


def test_no_source_broker_configured_is_safe():
    brokers = {"ftmo_challenge": _Broker("old")}
    eng = _engine(brokers, {}, source="")
    # ne doit pas lever ni rien changer
    eng._on_source_broker_replaced(_Broker("new"))
    assert brokers["ftmo_challenge"].name == "old"
