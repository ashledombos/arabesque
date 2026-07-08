"""Phase 2.5 étape 2.5-2.8 — tests de la boucle ``_be_polling_loop`` et de
``_be_polling_pass`` sur ``LivePositionMonitor``.

Vérifie :
- start_be_polling no-op quand ``be_polling_enabled=False`` ;
- start_be_polling crée la tâche quand activé, stop_be_polling l'annule
  proprement ;
- un passage de _be_polling_pass arme bien le BE quand la quote broker
  est fraîche et MFE >= 0.3R ;
- un FreshQuote stale (age > threshold) est skipped, pas d'amend ;
- un FreshQuote cTrader sans market_ts → skip (freshness indéterminée) ;
- un FreshQuote TradeLocker sans market_ts mais observed_at récent → OK ;
- l'event audit est émis quand le BE est armé via polling, pas sinon ;
- concurrence on_tick × polling sur la même position → 1 seul amend
  (preuve de la sérialisation via _amend_in_progress).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from arabesque.broker.base import FreshQuote
from arabesque.core.models import Side
from arabesque.execution.position_monitor import (
    LivePositionMonitor,
    MonitorConfig,
)


@dataclass
class _AmendResult:
    success: bool = True
    message: str = "ok"


class _MockBroker:
    """Broker minimal pour tests. Type name configurable pour simuler
    ``CTraderBroker`` ou ``TradeLockerBroker`` côté ``type(broker).__name__``
    via les sous-classes ci-dessous.
    """

    def __init__(self, fresh_quote: FreshQuote | None = None,
                 raise_on_fresh: Exception | None = None):
        self.amends: list[tuple[str, float, float | None]] = []
        self._fresh_quote = fresh_quote
        self._raise_on_fresh = raise_on_fresh
        self.fresh_quote_calls: list[tuple[str, str]] = []

    async def amend_position_sltp(self, position_id: str, stop_loss: float = None,
                                   take_profit: float = None):
        self.amends.append((position_id, stop_loss, take_profit))
        return _AmendResult(success=True, message="mock_ok")

    async def get_fresh_quote(self, symbol: str, quote_type: str):
        self.fresh_quote_calls.append((symbol, quote_type))
        if self._raise_on_fresh:
            raise self._raise_on_fresh
        return self._fresh_quote


class CTraderBroker(_MockBroker):
    """Mock dont __name__ correspond à la branche cTrader du polling
    (market_ts requis, sinon skip)."""


class TradeLockerBroker(_MockBroker):
    """Mock dont __name__ correspond à la branche TradeLocker (observed_at)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_monitor(
    *, be_polling_enabled: bool = True,
    interval_s: float = 60.0,
    freshness_s: float = 300.0,
    min_amend_interval_s: float = 0.0,
    on_audit_event=None,
    brokers: dict | None = None,
) -> LivePositionMonitor:
    cfg = MonitorConfig(
        min_amend_interval_s=min_amend_interval_s,
        tick_check_interval_s=0.0,
        be_polling_enabled=be_polling_enabled,
        be_polling_interval_s=interval_s,
        be_polling_freshness_threshold_s=freshness_s,
    )
    return LivePositionMonitor(
        brokers=brokers or {},
        config=cfg,
        on_audit_event=on_audit_event,
    )


def _register_long(mon: LivePositionMonitor, broker_id: str = "ftmo"):
    return mon.register_position(
        broker_id=broker_id, position_id="P1", symbol="TEST",
        side=Side.LONG, entry=100.0, sl=99.0, tp=102.0,
        volume=1.0, digits=2,
    )


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

def test_start_be_polling_noop_when_disabled():
    mon = _make_monitor(be_polling_enabled=False)
    asyncio.run(mon.start_be_polling())
    assert mon._be_polling_task is None


def test_start_then_stop_be_polling_clean_cancellation():
    async def _runner():
        broker = CTraderBroker(fresh_quote=None)
        mon = _make_monitor(
            be_polling_enabled=True,
            interval_s=10.0,  # long pour ne pas faire de pass pendant le test
            brokers={"ftmo": broker},
        )
        await mon.start_be_polling()
        assert mon._be_polling_task is not None
        assert not mon._be_polling_task.done()

        await mon.stop_be_polling()
        assert mon._be_polling_task is None

    asyncio.run(_runner())


# ---------------------------------------------------------------------------
# _be_polling_pass — comportement par cas
# ---------------------------------------------------------------------------

def test_pass_arms_be_with_fresh_ctrader_quote():
    """cTrader avec market_ts récent et prix >= seuil BE → armement."""
    fq = FreshQuote(
        symbol="TEST", price=100.35, quote_type="bid",
        market_ts=_now(), observed_at=_now(),
    )
    broker = CTraderBroker(fresh_quote=fq)
    audits: list[dict] = []
    mon = _make_monitor(
        brokers={"ftmo": broker},
        on_audit_event=lambda p: audits.append(p),
    )
    pos = _register_long(mon)

    checked, armed, skipped = asyncio.run(mon._be_polling_pass(freshness_threshold_s=300))

    assert checked == 1
    assert armed == 1
    assert skipped == 0
    assert pos.breakeven_set
    assert len(broker.amends) == 1
    assert broker.amends[0][1] == pytest.approx(100.20, abs=1e-9)
    assert broker.amends[0][2] == pytest.approx(102.0)
    # Audit event émis
    armed_events = [a for a in audits if a["event"] == "be_polling_armed"]
    assert len(armed_events) == 1
    ev = armed_events[0]
    assert ev["event"] == "be_polling_armed"
    assert ev["quote_source"] == "polling_backup"
    assert ev["quote_freshness_kind"] == "market_ts"
    assert ev["broker_kind"] == "CTraderBroker"
    assert ev["symbol"] == "TEST"
    assert ev["mfe_r_at_arm"] >= 0.3
    # Contrat de gate Phase 2.5 — champs obligatoires
    for key in (
        "broker_id", "quote_source", "quote_market_ts",
        "quote_observed_at", "quote_age_s", "old_sl", "new_sl",
    ):
        assert key in ev, f"champ {key} manquant dans le payload audit"
    assert ev["broker_id"] == "ftmo"
    # old_sl = SL avant amend = sl_initial (pas de trailing préalable)
    assert ev["old_sl"] == pytest.approx(99.0, abs=1e-9)
    # new_sl = SL appliqué par le BE (entry + 0.20R), confirmé côté broker
    assert ev["new_sl"] == pytest.approx(100.20, abs=1e-9)
    assert ev["old_sl"] != ev["new_sl"]
    assert ev["quote_age_s"] >= 0.0
    pass_events = [a for a in audits if a["event"] == "be_polling_pass"]
    assert len(pass_events) == 1
    assert pass_events[0]["checked"] == 1
    assert pass_events[0]["armed"] == 1
    decision_events = [a for a in audits if a["event"] == "be_polling_decision"]
    assert len(decision_events) == 1
    assert decision_events[0]["decision"] == "armed"
    assert decision_events[0]["breakeven_set_before"] is False
    assert decision_events[0]["breakeven_set_after"] is True
    assert decision_events[0]["quote_source"] == "polling_backup"


def test_pass_skips_when_ctrader_market_ts_absent():
    """cTrader sans market_ts → freshness indéterminée → skip défensif."""
    fq = FreshQuote(
        symbol="TEST", price=100.40, quote_type="bid",
        market_ts=None, observed_at=_now(),
    )
    broker = CTraderBroker(fresh_quote=fq)
    mon = _make_monitor(brokers={"ftmo": broker})
    pos = _register_long(mon)

    checked, armed, skipped = asyncio.run(mon._be_polling_pass(freshness_threshold_s=300))

    assert checked == 0
    assert armed == 0
    assert skipped == 1
    assert not pos.breakeven_set
    assert len(broker.amends) == 0


def test_pass_skips_when_quote_stale():
    """market_ts > threshold → skip."""
    stale_ts = _now() - timedelta(seconds=600)  # 10 min > 5 min
    fq = FreshQuote(
        symbol="TEST", price=100.40, quote_type="bid",
        market_ts=stale_ts, observed_at=_now(),
    )
    broker = CTraderBroker(fresh_quote=fq)
    mon = _make_monitor(brokers={"ftmo": broker})
    pos = _register_long(mon)

    checked, armed, skipped = asyncio.run(mon._be_polling_pass(freshness_threshold_s=300))

    assert skipped == 1
    assert armed == 0
    assert not pos.breakeven_set
    assert len(broker.amends) == 0


def test_pass_audit_records_skip_reasons():
    stale_ts = _now() - timedelta(seconds=600)
    fq = FreshQuote(
        symbol="TEST", price=100.40, quote_type="bid",
        market_ts=stale_ts, observed_at=_now(),
    )
    broker = CTraderBroker(fresh_quote=fq)
    audits: list[dict] = []
    mon = _make_monitor(
        brokers={"ftmo": broker},
        on_audit_event=lambda p: audits.append(p),
    )
    _register_long(mon)

    checked, armed, skipped = asyncio.run(mon._be_polling_pass(freshness_threshold_s=300))

    assert (checked, armed, skipped) == (0, 0, 1)
    assert audits[-1]["event"] == "be_polling_pass"
    assert audits[-1]["skip_reasons"] == {"quote_stale_or_clock_skew": 1}


def test_pass_audits_not_eligible_decision():
    """Quote fraîche mais MFE < trigger → décision explicite, pas d'amend."""
    fq = FreshQuote(
        symbol="TEST", price=100.20, quote_type="bid",
        market_ts=_now(), observed_at=_now(),
    )
    broker = CTraderBroker(fresh_quote=fq)
    audits: list[dict] = []
    mon = _make_monitor(
        brokers={"ftmo": broker},
        on_audit_event=lambda p: audits.append(p),
    )
    pos = _register_long(mon)

    checked, armed, skipped = asyncio.run(mon._be_polling_pass(freshness_threshold_s=300))

    assert (checked, armed, skipped) == (1, 0, 0)
    assert not pos.breakeven_set
    assert len(broker.amends) == 0
    decisions = [a for a in audits if a["event"] == "be_polling_decision"]
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "not_eligible"
    assert decisions[0]["reason"] == "mfe_below_trigger"
    assert decisions[0]["mfe_r"] == pytest.approx(0.2)


def test_pass_audits_eligible_not_armed_decision():
    """MFE >= trigger mais amend throttlé → décision exploitable post-mortem."""
    fq = FreshQuote(
        symbol="TEST", price=100.35, quote_type="bid",
        market_ts=_now(), observed_at=_now(),
    )
    broker = CTraderBroker(fresh_quote=fq)
    audits: list[dict] = []
    mon = _make_monitor(
        brokers={"ftmo": broker},
        min_amend_interval_s=60.0,
        on_audit_event=lambda p: audits.append(p),
    )
    pos = _register_long(mon)
    pos.last_amend_time = time.time()

    checked, armed, skipped = asyncio.run(mon._be_polling_pass(freshness_threshold_s=300))

    assert (checked, armed, skipped) == (1, 0, 0)
    assert not pos.breakeven_set
    assert len(broker.amends) == 0
    decisions = [a for a in audits if a["event"] == "be_polling_decision"]
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "eligible_not_armed"
    assert decisions[0]["reason"] == "min_amend_interval"
    assert decisions[0]["mfe_r"] >= 0.3


def test_pass_skips_when_fresh_quote_is_none():
    """Broker retourne None (Proto vide / pas connecté) → skip, jamais d'erreur."""
    broker = CTraderBroker(fresh_quote=None)
    mon = _make_monitor(brokers={"ftmo": broker})
    pos = _register_long(mon)

    checked, armed, skipped = asyncio.run(mon._be_polling_pass(freshness_threshold_s=300))

    assert checked == 0
    assert armed == 0
    assert skipped == 1
    assert not pos.breakeven_set


def test_pass_tradelocker_uses_transport_observed_at():
    """TradeLocker n'a pas de market_ts → freshness via observed_at,
    audit doit marquer ``transport_observed_at``."""
    fq = FreshQuote(
        symbol="TEST", price=100.35, quote_type="bid",
        market_ts=None, observed_at=_now(),
    )
    broker = TradeLockerBroker(fresh_quote=fq)
    audits: list[dict] = []
    mon = _make_monitor(
        brokers={"gft": broker},
        on_audit_event=lambda p: audits.append(p),
    )
    pos = mon.register_position(
        broker_id="gft", position_id="P1", symbol="TEST",
        side=Side.LONG, entry=100.0, sl=99.0, tp=102.0,
        volume=1.0, digits=2,
    )

    checked, armed, skipped = asyncio.run(mon._be_polling_pass(freshness_threshold_s=300))

    assert armed == 1
    assert pos.breakeven_set
    assert audits[0]["quote_freshness_kind"] == "transport_observed_at"
    assert audits[0]["broker_kind"] == "TradeLockerBroker"


def test_pass_no_audit_when_be_already_armed():
    """BE déjà armé avant le passage → no-op, pas d'event audit."""
    fq = FreshQuote(
        symbol="TEST", price=100.35, quote_type="bid",
        market_ts=_now(), observed_at=_now(),
    )
    broker = CTraderBroker(fresh_quote=fq)
    audits: list[dict] = []
    mon = _make_monitor(
        brokers={"ftmo": broker},
        on_audit_event=lambda p: audits.append(p),
    )
    pos = _register_long(mon)
    pos.breakeven_set = True  # déjà armé

    checked, armed, skipped = asyncio.run(mon._be_polling_pass(freshness_threshold_s=300))

    assert armed == 0
    assert checked == 0  # position skipped en début de boucle
    assert len(audits) == 1
    assert audits[0]["event"] == "be_polling_pass"
    assert audits[0]["checked"] == 0
    assert audits[0]["armed"] == 0
    assert audits[0]["skipped"] == 0
    assert len(broker.amends) == 0


def test_pass_swallows_broker_exception():
    """Une exception côté broker.get_fresh_quote() ne doit JAMAIS propager.
    Pas de reconcile, pas de close — juste un skip silencieux du cycle."""
    broker = CTraderBroker(raise_on_fresh=RuntimeError("simulated broker error"))
    mon = _make_monitor(brokers={"ftmo": broker})
    pos = _register_long(mon)

    # Ne doit pas raise
    checked, armed, skipped = asyncio.run(mon._be_polling_pass(freshness_threshold_s=300))

    assert checked == 0
    assert skipped == 1
    assert armed == 0
    assert not pos.breakeven_set
    assert len(broker.amends) == 0


def test_pass_broker_missing_is_skipped_not_error():
    """pos.broker_id absent du registry → skip, pas d'erreur."""
    mon = _make_monitor(brokers={})  # registry vide
    _register_long(mon, broker_id="ftmo")

    checked, armed, skipped = asyncio.run(mon._be_polling_pass(freshness_threshold_s=300))

    assert skipped == 1
    assert armed == 0


# ---------------------------------------------------------------------------
# Concurrence on_tick × polling — preuve _amend_in_progress
# ---------------------------------------------------------------------------

class _SlowCTrader(CTraderBroker):
    """Version slow pour reproduire la course on_tick × polling pendant
    qu'un amend BE est en cours."""

    def __init__(self, fresh_quote: FreshQuote, delay_s: float = 0.05):
        super().__init__(fresh_quote=fresh_quote)
        self.delay_s = delay_s

    async def amend_position_sltp(self, position_id: str, stop_loss: float = None,
                                   take_profit: float = None):
        await asyncio.sleep(self.delay_s)
        self.amends.append((position_id, stop_loss))
        return _AmendResult(success=True, message="mock_ok_slow")


def test_polling_pass_concurrent_with_on_tick_yields_single_amend():
    """Cas réel attendu en prod : tick PriceFeed et polling backup tirent
    simultanément. Le guard ``_amend_in_progress`` doit garantir 1 seul amend.
    """
    fq = FreshQuote(
        symbol="TEST", price=100.35, quote_type="bid",
        market_ts=_now(), observed_at=_now(),
    )
    broker = _SlowCTrader(fresh_quote=fq, delay_s=0.05)
    mon = _make_monitor(brokers={"ftmo": broker})
    pos = _register_long(mon)

    class _Tick:
        def __init__(self):
            self.symbol = "TEST"
            self.bid = 100.35
            self.ask = 100.37
            self.timestamp = time.time()

    async def _runner():
        return await asyncio.gather(
            mon.on_tick(_Tick()),
            mon._be_polling_pass(freshness_threshold_s=300),
        )

    asyncio.run(_runner())

    assert pos.breakeven_set
    assert len(broker.amends) == 1, (
        f"Race condition : {len(broker.amends)} amends au lieu de 1 "
        f"(_amend_in_progress n'a pas sérialisé on_tick × polling)"
    )
