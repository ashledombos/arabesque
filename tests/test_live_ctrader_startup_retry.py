"""Non-regression du backoff cTrader au demarrage du moteur.

Incident 2026-05-27 : apres reboot et coupure reseau, une connexion cTrader
a timeoute localement puis s'est authentifiee cote serveur pendant que
``LiveEngine`` relancait deja une tentative cinq secondes plus tard. Le
second login est tombe dans ``ALREADY_LOGGED_IN``.

Le broker fait le cleanup complet du timeout ; le moteur doit en complement
laisser au serveur un delai de liberation minimum de 60 secondes, uniquement
pour cTrader. Les autres brokers conservent le backoff court actuel.
"""
from __future__ import annotations

import asyncio

from arabesque.execution.live import LiveEngine


class _RetryBroker:
    def __init__(self, broker_type: str):
        self.config = {"type": broker_type, "instruments_mapping": {}}
        self.calls = 0

    async def connect(self) -> bool:
        self.calls += 1
        return self.calls >= 2


def _run_connect(monkeypatch, broker_type: str) -> list[int]:
    broker = _RetryBroker(broker_type)
    engine = LiveEngine({}, {}, {})
    sleeps: list[int] = []

    monkeypatch.setattr(
        "arabesque.broker.factory.create_all_brokers",
        lambda *_args: {"account": broker},
    )

    async def fake_sleep(delay: int) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("arabesque.execution.live.asyncio.sleep", fake_sleep)
    asyncio.run(engine._connect_brokers())
    assert engine._brokers["account"] is broker
    return sleeps


def test_ctrader_startup_retry_waits_for_server_session_release(monkeypatch):
    assert _run_connect(monkeypatch, "ctrader") == [60]


def test_non_ctrader_startup_retry_keeps_existing_short_backoff(monkeypatch):
    assert _run_connect(monkeypatch, "tradelocker") == [5]
