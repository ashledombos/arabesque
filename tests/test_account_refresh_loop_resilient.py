"""Non-régression : la boucle ``_account_refresh_loop`` doit survivre à une
exception levée par ``_refresh_account_state`` ou ``emit_health_report``.

Incident 2026-05-18 23:57 → 2026-05-19 14:30+ : aucun ``health_report``
émis pendant 16h après un restart. La task fire-and-forget créée à
``_start`` (cf. live.py:149) avait probablement été tuée par une
exception remontée d'un ``await`` intérieur (erreurs tradelocker_api
observées dans les premiers logs).

Le patch isole chaque await avec un try/except WARNING ; la boucle ne
peut plus être condamnée par un échec ponctuel.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

from arabesque.execution.live import LiveEngine


def _build_engine_stub() -> LiveEngine:
    engine = LiveEngine.__new__(LiveEngine)
    engine._running = True
    engine._live_monitor = None
    return engine


def test_refresh_exception_does_not_kill_loop(caplog):
    """Si _refresh_account_state lève, la boucle continue (2e itération)."""
    engine = _build_engine_stub()
    refresh_calls = {"n": 0}

    async def fake_refresh():
        refresh_calls["n"] += 1
        if refresh_calls["n"] == 1:
            raise RuntimeError("simulated tradelocker error")
        if refresh_calls["n"] >= 2:
            engine._running = False

    engine._refresh_account_state = fake_refresh

    async def fast_sleep(_):
        # ne pas re-appeler asyncio.sleep (qui est patché → récursion)
        return None

    with patch("arabesque.execution.live.asyncio.sleep", new=fast_sleep), \
         caplog.at_level(logging.WARNING):
        asyncio.run(engine._account_refresh_loop())

    assert refresh_calls["n"] == 2, (
        f"La boucle devait survivre à l'exception et itérer une 2e fois — "
        f"refresh_calls['n']={refresh_calls['n']} (REGRESSION : exception tue la task)."
    )
    assert any("simulated tradelocker error" in m for m in caplog.messages), (
        "L'exception doit être loggée en WARNING — sinon panne silencieuse."
    )


def test_health_report_exception_does_not_kill_loop(caplog):
    """Si emit_health_report lève, la boucle continue."""
    engine = _build_engine_stub()
    refresh_n = {"n": 0}

    async def ok_refresh():
        refresh_n["n"] += 1
        if refresh_n["n"] >= 2:
            engine._running = False

    class _FakeMonitor:
        def __init__(self):
            self.n = 0
        def should_emit_health_report(self):
            return True
        def emit_health_report(self):
            self.n += 1
            raise IOError("simulated append_journal error")

    engine._refresh_account_state = ok_refresh
    engine._live_monitor = _FakeMonitor()

    async def fast_sleep(_):
        # ne pas re-appeler asyncio.sleep (qui est patché → récursion)
        return None

    with patch("arabesque.execution.live.asyncio.sleep", new=fast_sleep), \
         caplog.at_level(logging.WARNING):
        asyncio.run(engine._account_refresh_loop())

    assert engine._live_monitor.n >= 2, (
        "La boucle doit re-tenter emit_health_report après une exception"
    )
    assert refresh_n["n"] >= 2
    assert any("simulated append_journal error" in m for m in caplog.messages)


def test_loop_exits_clean_when_not_running():
    """Si _running est False au démarrage, la boucle entre dans sleep une fois
    puis sort proprement après le check (`while False` post-sleep).
    """
    engine = _build_engine_stub()
    engine._running = False  # déjà arrêté

    refresh_called = {"n": 0}
    async def must_not_be_called():
        refresh_called["n"] += 1
    engine._refresh_account_state = must_not_be_called

    # while self._running: False dès le départ → exit immédiat
    async def fast_sleep(_):
        # ne pas re-appeler asyncio.sleep (qui est patché → récursion)
        return None

    with patch("arabesque.execution.live.asyncio.sleep", new=fast_sleep):
        asyncio.run(asyncio.wait_for(engine._account_refresh_loop(), timeout=1.0))

    assert refresh_called["n"] == 0, (
        "Si _running=False au démarrage, refresh ne doit jamais être appelé."
    )


def test_consecutive_exceptions_keep_loop_alive(caplog):
    """3 exceptions de suite → la boucle survit, 4e itération OK."""
    engine = _build_engine_stub()
    refresh_calls = {"n": 0}

    async def flaky_refresh():
        refresh_calls["n"] += 1
        if refresh_calls["n"] <= 3:
            raise RuntimeError(f"flaky error #{refresh_calls['n']}")
        engine._running = False

    engine._refresh_account_state = flaky_refresh

    async def fast_sleep(_):
        # ne pas re-appeler asyncio.sleep (qui est patché → récursion)
        return None

    with patch("arabesque.execution.live.asyncio.sleep", new=fast_sleep), \
         caplog.at_level(logging.WARNING):
        asyncio.run(engine._account_refresh_loop())

    assert refresh_calls["n"] == 4, (
        f"La boucle doit survivre à 3 exceptions consécutives et atteindre "
        f"la 4e itération. Observé : {refresh_calls['n']}"
    )
    # Les 3 erreurs doivent être loggées
    warnings = [m for m in caplog.messages if "flaky error" in m]
    assert len(warnings) == 3
