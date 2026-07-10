"""Tests lot 2 session-or : fermeture à heure de mur dans LivePositionMonitor.

Le monitor est le MIROIR live du PositionManager (risque n°1 du chiffrage
07-10 = divergence). Couverture :
  - test jumeau : le mur calculé par le monitor == deadline du manager pour
    la même entrée (source unique next_session_deadline_utc) ;
  - AUCUN overlay sur une position session (bougie, tick, BE-polling) alors
    qu'une position normale du même monitor arme son BE normalement ;
  - close market au mur → exit_label, reconcile notifie exit_reason
    "session_exit" avec le vrai fill broker ;
  - broker injoignable : retry + alerte URGENT après N échecs, position
    JAMAIS retirée, JAMAIS d'exit inventé ;
  - POSITION_NOT_FOUND au mur (SL touché avant) → pas de label session ;
  - persistance save/load du mur (survie restart) ;
  - mur déjà passé au register (downtime) → close au premier cycle.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import arabesque.execution.position_monitor as pm_module
from arabesque.core.models import Position, Side
from arabesque.execution.position_monitor import (
    LivePositionMonitor,
    MonitorConfig,
)
from arabesque.modules.position_manager import ManagerConfig, PositionManager

SESSION_SPEC = "08:00@Europe/London"
# Entrée 2025-07-08 22:00 UTC = 18:00 NY EDT = 23:00 Londres BST
ENTRY_TS = datetime(2025, 7, 8, 22, 0, tzinfo=timezone.utc)
# Mur attendu : 08:00 BST le 09 = 07:00 UTC
WALL_TS = datetime(2025, 7, 9, 7, 0, tzinfo=timezone.utc)


class _Quote:
    def __init__(self, bid=100.3, ask=100.5):
        self.bid = bid
        self.ask = ask


class _Result:
    def __init__(self, success=True, message="ok"):
        self.success = success
        self.message = message


class _BrokerPos:
    def __init__(self, position_id):
        self.position_id = position_id


class _MockBroker:
    def __init__(self):
        self.close_calls: list[str] = []
        self.amends: list[tuple] = []
        self.close_result = _Result(success=True)
        self.close_raises: Exception | None = None
        self.open_position_ids: list[str] = []
        self.closed_detail: dict | None = None

    async def get_quote(self, symbol):
        return _Quote()

    async def close_position(self, position_id, volume=None):
        self.close_calls.append(position_id)
        if self.close_raises:
            raise self.close_raises
        return self.close_result

    async def amend_position_sltp(self, position_id, stop_loss=None, take_profit=None):
        self.amends.append((position_id, stop_loss, take_profit))
        return _Result(success=True)

    async def get_positions(self):
        return [_BrokerPos(pid) for pid in self.open_position_ids]

    async def get_closed_position_detail(self, position_id):
        return self.closed_detail


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    path = tmp_path / "position_monitor_state.json"
    monkeypatch.setattr(pm_module, "STATE_FILE", path)
    return path


def _make_monitor(broker, on_closed=None, on_failed=None) -> LivePositionMonitor:
    cfg = MonitorConfig(
        session_exit_by_strategy={"session_or": SESSION_SPEC},
        session_close_failures_before_alert=3,
    )
    return LivePositionMonitor(
        brokers={"gft": broker},
        config=cfg,
        on_position_closed=on_closed,
        on_session_close_failed=on_failed,
    )


def _register_session(mon, position_id="S1", entry_ts=ENTRY_TS):
    return mon.register_position(
        broker_id="gft", position_id=position_id, symbol="XAUUSD",
        side=Side.LONG, entry=100.0, sl=99.0, tp=0.0, volume=0.1,
        strategy="session_or", entry_ts=entry_ts,
    )


# ── Test jumeau manager/monitor : même mur pour la même entrée ────────

def test_mur_monitor_identique_au_deadline_manager(state_path):
    broker = _MockBroker()
    mon = _make_monitor(broker)
    tracked = _register_session(mon)

    mgr = PositionManager(ManagerConfig(session_exit=SESSION_SPEC))
    pos = Position(instrument="XAUUSD", side=Side.LONG,
                   entry=100.0, sl=99.0, sl_initial=99.0, tp=0.0)
    mgr.positions.append(pos)
    mgr.update_position(pos, high=100.1, low=100.0, close=100.05,
                        bar_ts=ENTRY_TS)
    manager_deadline = datetime.fromisoformat(
        pos.signal_data["session_exit_deadline"])

    assert tracked.session_exit_at == manager_deadline.timestamp()
    assert tracked.session_exit_at == WALL_TS.timestamp()


# ── Aucun overlay sur une position session ────────────────────────────

def test_aucun_overlay_session_mais_be_normal_sur_autre_strategie(state_path):
    broker = _MockBroker()
    mon = _make_monitor(broker)
    session_pos = _register_session(mon)
    normal_pos = mon.register_position(
        broker_id="gft", position_id="N1", symbol="BTCUSD",
        side=Side.LONG, entry=200.0, sl=198.0, tp=0.0, volume=0.1,
        strategy="glissade",
    )

    # MFE 0.5R sur les deux : la session ne doit RIEN faire, la normale
    # doit armer son BE (amend broker)
    asyncio.run(mon.on_bar_closed("XAUUSD", high=100.5, low=100.1, close=100.4))
    asyncio.run(mon.on_bar_closed("BTCUSD", high=201.0, low=200.2, close=200.8))

    assert not session_pos.breakeven_set
    assert session_pos.mfe_r == pytest.approx(0.5)  # MFE tracké (audit)
    assert normal_pos.breakeven_set
    amended_ids = [pid for pid, _, _ in broker.amends]
    assert amended_ids == ["N1"]


def test_tick_et_be_polling_skippent_la_session(state_path):
    broker = _MockBroker()
    mon = _make_monitor(broker)
    session_pos = _register_session(mon)

    armed = asyncio.run(
        mon._process_pos_from_price(session_pos, 100.6, source="tick"))
    assert armed is False
    assert broker.amends == []
    assert not session_pos.breakeven_set

    # BE polling : la position session est skippée avant même la quote
    checked, armed_n, _ = asyncio.run(mon._be_polling_pass(300.0))
    assert checked == 0
    assert armed_n == 0


# ── Close au mur + reconcile labellise session_exit ──────────────────

def test_close_au_mur_puis_reconcile_notifie_session_exit(state_path):
    closed_events = []

    def on_closed(**kwargs):
        closed_events.append(kwargs)

    broker = _MockBroker()
    mon = _make_monitor(broker, on_closed=on_closed)
    pos = _register_session(mon)

    # Avant le mur : rien
    n = asyncio.run(mon._session_close_pass(now=WALL_TS.timestamp() - 60))
    assert n == 0 and broker.close_calls == []

    # Au mur : close market accepté, label posé, position TOUJOURS trackée
    n = asyncio.run(mon._session_close_pass(now=WALL_TS.timestamp() + 5))
    assert n == 1
    assert broker.close_calls == ["S1"]
    assert pos.exit_label == "session_exit"
    assert pos.exit_price_hint == pytest.approx(100.3)  # bid (LONG)
    assert "gft:S1" in mon._positions
    assert closed_events == []  # pas d'exit fabriqué par la boucle session

    # Reconcile : broker flat + vrai fill → notification session_exit
    pos.registered_at -= 600  # sortir de la grace period 300s
    broker.open_position_ids = []
    broker.closed_detail = {"exit_price": 100.42, "gross_profit": 4.2,
                            "commission": -0.1, "swap": 0.0}
    asyncio.run(mon.reconcile())

    assert "gft:S1" not in mon._positions
    assert len(closed_events) == 1
    evt = closed_events[0]
    assert evt["exit_reason"] == "session_exit"
    assert evt["exit_price"] == pytest.approx(100.42)
    assert evt["exit_price_source"] == "real_fill"


# ── Broker injoignable : retry + alerte, jamais d'exit inventé ────────

def test_broker_injoignable_retry_alerte_position_conservee(state_path):
    alerts = []
    closed_events = []
    broker = _MockBroker()
    broker.close_raises = ConnectionError("canal trading mort")
    mon = _make_monitor(
        broker,
        on_closed=lambda **kw: closed_events.append(kw),
        on_failed=lambda p: alerts.append(p),
    )
    pos = _register_session(mon)

    now = WALL_TS.timestamp() + 5
    for i in range(3):
        # Chaque pass re-tente (l'échec ne pose pas de throttle re-issue)
        asyncio.run(mon._session_close_pass(now=now + i))

    assert len(broker.close_calls) == 3
    assert pos.session_close_failures == 3
    assert len(alerts) == 1  # alerte au seuil, pas avant
    assert alerts[0]["symbol"] == "XAUUSD"
    assert alerts[0]["last_error"] == "canal trading mort"
    # JAMAIS d'exit inventé : position trackée, aucun label, aucun event
    assert "gft:S1" in mon._positions
    assert pos.exit_label == ""
    assert closed_events == []

    # 4e échec sous cooldown → pas de 2e alerte (anti-spam)
    asyncio.run(mon._session_close_pass(now=now + 10))
    assert len(alerts) == 1


def test_position_not_found_au_mur_pas_de_label_session(state_path):
    broker = _MockBroker()
    broker.close_result = _Result(success=False,
                                  message="POSITION_NOT_FOUND: closed")
    mon = _make_monitor(broker)
    pos = _register_session(mon)

    n = asyncio.run(mon._session_close_pass(now=WALL_TS.timestamp() + 5))
    assert n == 0
    # Fermée broker-side AVANT le mur (SL touché) : reconcile estimera la
    # vraie raison, pas de session_exit forcé
    assert pos.exit_label == ""
    assert pos.session_close_failures == 0  # pas un échec broker
    assert mon._estimate_exit_reason(pos) == "stop_loss"


# ── Persistance : le mur survit à un restart ──────────────────────────

def test_save_load_state_persiste_le_mur(state_path):
    broker = _MockBroker()
    mon1 = _make_monitor(broker)
    _register_session(mon1)
    mon1.save_state()
    assert state_path.exists()

    # Restart simulé : ré-adoption SANS stratégie (pire cas — journal
    # illisible), load_state doit restaurer mur + stratégie
    mon2 = _make_monitor(broker)
    readopted = mon2.register_position(
        broker_id="gft", position_id="S1", symbol="XAUUSD",
        side=Side.LONG, entry=100.0, sl=99.0, tp=0.0, volume=0.1,
    )
    assert readopted.session_exit_at == 0.0
    restored = mon2.load_state()
    assert restored == 1
    assert readopted.session_exit_at == WALL_TS.timestamp()
    assert readopted.strategy == "session_or"


def test_mur_deja_passe_au_register_ferme_au_premier_pass(state_path):
    """Downtime pendant le mur : la ré-adoption ancre sur l'entrée journal,
    le mur est déjà passé → close au premier cycle."""
    broker = _MockBroker()
    mon = _make_monitor(broker)
    pos = _register_session(mon, entry_ts=ENTRY_TS.isoformat())  # ISO string

    now_after_wall = WALL_TS.timestamp() + 3600  # restart 1h après le mur
    assert pos.session_exit_at < now_after_wall
    n = asyncio.run(mon._session_close_pass(now=now_after_wall))
    assert n == 1
    assert pos.exit_label == "session_exit"


def test_config_session_invalide_echoue_a_l_init(state_path):
    with pytest.raises(ValueError, match="session_exit invalide"):
        LivePositionMonitor(
            brokers={},
            config=MonitorConfig(
                session_exit_by_strategy={"session_or": "8h@London"}),
        )


# ── Non-régression : monitor sans config session inchangé ────────────

def test_monitor_sans_session_config_inchange(state_path):
    broker = _MockBroker()
    mon = LivePositionMonitor(brokers={"gft": broker}, config=MonitorConfig())
    pos = mon.register_position(
        broker_id="gft", position_id="N1", symbol="BTCUSD",
        side=Side.LONG, entry=200.0, sl=198.0, tp=0.0, volume=0.1,
        strategy="extension",
    )
    assert pos.session_exit_at == 0.0
    asyncio.run(mon.on_bar_closed("BTCUSD", high=201.0, low=200.2, close=200.8))
    assert pos.breakeven_set
    n = asyncio.run(mon._session_close_pass())
    assert n == 0 and broker.close_calls == []
