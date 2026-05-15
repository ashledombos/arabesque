"""Refactor étape 1 Phase 2.5 — extraction `_process_pos_quote`.

Vérifie que :
- ``on_tick`` continue d'armer le BE quand MFE >= 0.3R (sanity du refactor) ;
- ``_process_pos_quote`` appelé directement (futur polling backup) arme aussi
  le BE et retourne True ;
- ``do_trailing=False`` n'enclenche pas le trailing même si MFE dépasse le
  premier palier (1.5R) ;
- Idempotence : un 2ᵉ appel quand BE déjà armé ne déclenche pas d'amend.

Pas de réseau ni de PriceFeed — mock broker en mémoire.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

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
    """Broker minimal pour test : compte les amend, ne valide pas le prix."""

    def __init__(self):
        self.amends: list[tuple[str, float]] = []

    async def amend_position_sltp(self, position_id: str, stop_loss: float = None,
                                   take_profit: float = None):
        self.amends.append((position_id, stop_loss))
        return _AmendResult(success=True, message="mock_ok")


class _Tick:
    def __init__(self, symbol: str, bid: float, ask: float):
        self.symbol = symbol
        self.bid = bid
        self.ask = ask
        self.timestamp = time.time()


def _make_monitor(min_amend_interval_s: float = 0.0,
                  tick_check_interval_s: float = 0.0) -> tuple[LivePositionMonitor, _MockBroker]:
    """Monitor avec throttles désactivés (tests synchrones rapides)."""
    broker = _MockBroker()
    cfg = MonitorConfig(
        min_amend_interval_s=min_amend_interval_s,
        tick_check_interval_s=tick_check_interval_s,
    )
    mon = LivePositionMonitor(brokers={"ftmo": broker}, config=cfg)
    return mon, broker


def _register_long(mon: LivePositionMonitor, entry: float = 100.0,
                   sl: float = 99.0, tp: float = 102.0):
    """LONG, R=1.0, BE level @ entry + 0.20R = 100.20."""
    return mon.register_position(
        broker_id="ftmo",
        position_id="P1",
        symbol="TEST",
        side=Side.LONG,
        entry=entry,
        sl=sl,
        tp=tp,
        volume=1.0,
        digits=2,
    )


def test_on_tick_arms_be_when_mfe_reaches_threshold():
    """Sanity du refactor : on_tick continue d'armer le BE comme avant."""
    mon, broker = _make_monitor()
    pos = _register_long(mon)
    # MFE 0.3R → bid >= entry + 0.3*R = 100.30
    asyncio.run(mon.on_tick(_Tick("TEST", bid=100.31, ask=100.33)))
    assert pos.breakeven_set, "BE devrait être armé à MFE = 0.3R"
    assert len(broker.amends) == 1
    assert broker.amends[0][1] == pytest.approx(100.20, abs=1e-9), (
        "SL doit être amendé à entry + 0.20R"
    )


def test_process_pos_quote_directly_arms_be_and_returns_true():
    """Le futur polling appellera _process_pos_quote directement : doit armer."""
    mon, broker = _make_monitor()
    pos = _register_long(mon)
    armed = asyncio.run(mon._process_pos_quote(pos, bid=100.31, ask=100.33,
                                                source="polling_backup"))
    assert armed is True
    assert pos.breakeven_set
    assert len(broker.amends) == 1


def test_process_pos_quote_skips_trailing_when_disabled():
    """do_trailing=False (mode polling backup BE-only en v1) ne doit pas
    déclencher de trailing même si MFE >= premier palier (1.5R)."""
    mon, broker = _make_monitor()
    pos = _register_long(mon)
    # MFE 1.6R (déclencherait le tier 1.5R / dist 0.7R en mode normal)
    asyncio.run(mon._process_pos_quote(pos, bid=101.60, ask=101.62,
                                        source="polling_backup", do_trailing=False))
    assert pos.breakeven_set, "BE doit toujours s'armer (MFE 1.6R >> 0.3R)"
    assert not pos.trailing_active, "Trailing désactivé : trailing_active doit rester False"
    # 1 seul amend (BE), pas d'amend trailing supplémentaire
    assert len(broker.amends) == 1


def test_process_pos_quote_idempotent_when_be_already_armed():
    """2ᵉ appel avec BE déjà armé : pas de nouvel amend, retourne False."""
    mon, broker = _make_monitor()
    pos = _register_long(mon)
    # 1er appel : arme
    armed1 = asyncio.run(mon._process_pos_quote(pos, bid=100.31, ask=100.33))
    assert armed1 is True
    n_after_first = len(broker.amends)
    # 2e appel même prix : BE déjà armé, pas d'amend supplémentaire (sauf trailing
    # éventuel — mais 0.3R n'atteint aucun tier donc rien ne bouge)
    armed2 = asyncio.run(mon._process_pos_quote(pos, bid=100.31, ask=100.33))
    assert armed2 is False, "be_just_armed doit être False au 2e appel"
    assert len(broker.amends) == n_after_first, "Aucun amend supplémentaire attendu"


def test_process_pos_from_price_arms_be_with_single_price():
    """Phase 2.5 étape 2 — point d'entrée bas niveau pour le polling broker.

    Le polling n'a qu'un seul côté (bid LONG ou ask SHORT), pas besoin
    de fabriquer un faux PriceTick avec bid=ask.
    """
    mon, broker = _make_monitor()
    pos = _register_long(mon)
    armed = asyncio.run(mon._process_pos_from_price(
        pos, price=100.31, source="polling_backup", do_trailing=False
    ))
    assert armed is True
    assert pos.breakeven_set
    assert not pos.trailing_active  # do_trailing=False
    assert len(broker.amends) == 1


def test_process_pos_quote_zero_price_is_noop():
    """price <= 0 (quote dégradée) : ne touche pas la position."""
    mon, broker = _make_monitor()
    pos = _register_long(mon)
    armed = asyncio.run(mon._process_pos_quote(pos, bid=0, ask=0))
    assert armed is False
    assert not pos.breakeven_set
    assert len(broker.amends) == 0


class _SlowMockBroker(_MockBroker):
    """Mock broker dont amend_position_sltp prend N secondes — pour tester
    qu'un second appel concurrent voit ``_amend_in_progress=True`` et abandonne."""

    def __init__(self, delay_s: float = 0.05):
        super().__init__()
        self.delay_s = delay_s

    async def amend_position_sltp(self, position_id: str, stop_loss: float = None,
                                   take_profit: float = None):
        await asyncio.sleep(self.delay_s)
        self.amends.append((position_id, stop_loss))
        return _AmendResult(success=True, message="mock_ok_slow")


def test_concurrent_process_pos_quote_yields_single_amend():
    """Preuve d'async-safety : 2 appels gather() sur la même position ne
    doivent produire qu'un seul amend broker (point 7 user).

    Reproduit le cas où ``on_tick`` (PriceFeed) et ``_be_polling_loop`` (futur)
    tirent simultanément sur la même position pendant la fenêtre d'amend.
    """
    cfg = MonitorConfig(min_amend_interval_s=0.0, tick_check_interval_s=0.0)
    broker = _SlowMockBroker(delay_s=0.05)
    mon = LivePositionMonitor(brokers={"ftmo": broker}, config=cfg)
    pos = mon.register_position(
        broker_id="ftmo", position_id="P_CONC", symbol="TEST",
        side=Side.LONG, entry=100.0, sl=99.0, tp=102.0,
        volume=1.0, digits=2,
    )

    async def _runner():
        # Deux appels concurrents avec quote suffisante pour armer le BE
        results = await asyncio.gather(
            mon._process_pos_quote(pos, bid=100.31, ask=100.33, source="tick"),
            mon._process_pos_quote(pos, bid=100.31, ask=100.33, source="polling_backup"),
        )
        return results

    results = asyncio.run(_runner())
    # Un seul amend broker doit être enregistré
    assert len(broker.amends) == 1, (
        f"Race condition : {len(broker.amends)} amends au lieu de 1 "
        f"(_amend_in_progress n'a pas sérialisé les appels)"
    )
    assert pos.breakeven_set
    # Un seul des deux appels doit avoir vu be_just_armed=True
    # (l'autre voit soit _amend_in_progress, soit BE déjà set)
    assert sum(bool(r) for r in results) <= 1, (
        f"Plusieurs appels rapportent be_just_armed=True : {results}"
    )
