"""Tests lot 1 session-or : flag be_enabled + session_exit dans PositionManager.

Design validé (docs/audit/session_or_time_exit_chiffrage_2026-07-10.md) :
sortie au close de la 1re barre >= prochaine occurrence de HH:MM@tz après
l'entrée, SL intrabar prioritaire sur la barre du mur (convention pessimiste),
comportement du manager strictement inchangé si bar_ts=None ou session_exit=None.
"""

from datetime import datetime, timezone

import pytest

from arabesque.core.models import DecisionType, Position, Side
from arabesque.modules.position_manager import ManagerConfig, PositionManager


def _session_config(**overrides) -> ManagerConfig:
    """Profil session-or : AUCUN overlay (BE/trailing/ROI/giveback/deadfish/
    time-stop off), sortie au mur uniquement."""
    params = dict(
        roi_enabled=False,
        trailing_tiers=[],
        be_enabled=False,
        giveback_enabled=False,
        deadfish_enabled=False,
        time_stop_enabled=False,
        session_exit="08:00@Europe/London",
    )
    params.update(overrides)
    return ManagerConfig(**params)


def _long_position(entry: float = 100.0, sl: float = 99.0) -> Position:
    return Position(
        instrument="XAUUSD", side=Side.LONG,
        entry=entry, sl=sl, sl_initial=sl, tp=0.0,
    )


def _utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# ── Non-régression : défauts inchangés ────────────────────────────────

def test_defaut_sans_bar_ts_comportement_inchange():
    """Config par défaut, pas de bar_ts : BE s'arme comme avant, aucun
    EXIT_SESSION possible."""
    mgr = PositionManager(ManagerConfig())
    pos = _long_position()
    mgr.positions.append(pos)

    # MFE 0.5R, close 100.4 : BE doit s'armer (trigger 0.3R, offset 0.20R)
    decisions = mgr.update_position(pos, high=100.5, low=100.1, close=100.4)

    assert pos.is_open
    assert pos.breakeven_set
    assert any(d.decision_type == DecisionType.SL_BREAKEVEN for d in decisions)
    assert not any(d.decision_type == DecisionType.EXIT_SESSION for d in decisions)
    assert pos.sl == pytest.approx(100.20)


def test_defaut_avec_bar_ts_pas_de_session_exit():
    """Config par défaut (session_exit=None) : fournir bar_ts ne change rien."""
    mgr = PositionManager(ManagerConfig())
    pos = _long_position()
    mgr.positions.append(pos)

    for h in range(24):
        mgr.update_position(pos, high=100.1, low=100.0, close=100.05,
                            bar_ts=_utc(2025, 7, 8, h))
    assert pos.is_open
    assert "session_exit_deadline" not in pos.signal_data


def test_be_enabled_false_bloque_le_breakeven():
    mgr = PositionManager(_session_config(session_exit=None))
    pos = _long_position()
    mgr.positions.append(pos)

    decisions = mgr.update_position(pos, high=100.5, low=100.1, close=100.4)

    assert pos.is_open
    assert not pos.breakeven_set
    assert pos.sl == pytest.approx(99.0)
    assert not any(d.decision_type == DecisionType.SL_BREAKEVEN for d in decisions)


# ── Session exit : cas nominal (été, écart NY/Londres = 5h) ───────────

def test_session_exit_sort_a_la_premiere_barre_au_mur():
    """Entrée 2025-07-08 22:00 UTC (18:00 NY EDT = 23:00 Londres BST).
    Mur = 08:00 Londres BST le 09 = 07:00 UTC. Barres horaires : rien ne
    sort avant, EXIT_SESSION au close de la barre 07:00 UTC."""
    mgr = PositionManager(_session_config())
    pos = _long_position()
    mgr.positions.append(pos)

    bars = [_utc(2025, 7, 8, 22), _utc(2025, 7, 8, 23)] + \
           [_utc(2025, 7, 9, h) for h in range(0, 7)]
    for ts in bars:
        decisions = mgr.update_position(pos, high=100.6, low=100.2,
                                        close=100.4, bar_ts=ts)
        assert pos.is_open, f"sortie prématurée à {ts}"
        assert decisions == []

    decisions = mgr.update_position(pos, high=100.6, low=100.2, close=100.4,
                                    bar_ts=_utc(2025, 7, 9, 7))
    assert not pos.is_open
    assert len(decisions) == 1
    assert decisions[0].decision_type == DecisionType.EXIT_SESSION
    assert pos.exit_reason == "exit_session"
    assert pos.exit_price == pytest.approx(100.4)
    assert pos.result_r == pytest.approx(0.4)
    # Deadline persistée, auditables dans les journaux
    assert pos.signal_data["session_exit_deadline"] == "2025-07-09T07:00:00+00:00"


def test_sl_prioritaire_sur_la_barre_du_mur():
    """Sur la barre du mur, SL touché intrabar => EXIT_SL (pessimiste),
    pas EXIT_SESSION."""
    mgr = PositionManager(_session_config())
    pos = _long_position()
    mgr.positions.append(pos)

    mgr.update_position(pos, high=100.6, low=100.2, close=100.4,
                        bar_ts=_utc(2025, 7, 8, 22))
    decisions = mgr.update_position(pos, high=100.5, low=98.9, close=100.0,
                                    bar_ts=_utc(2025, 7, 9, 7))
    assert not pos.is_open
    assert decisions[0].decision_type == DecisionType.EXIT_SL
    assert pos.result_r == pytest.approx(-1.0)


# ── DST : fenêtre mars où NY a basculé mais pas Londres (écart 4h) ────

def test_dst_ecart_4h_mars_le_mur_reste_08h_londres():
    """2026-03-10 : US en EDT depuis le 03-08, UK encore en GMT (bascule
    03-29). Entrée 18:00 NY = 22:00 UTC = 22:00 Londres. Mur = 08:00 GMT
    = 08:00 UTC (et PAS 07:00 UTC comme en été)."""
    mgr = PositionManager(_session_config())
    pos = _long_position()
    mgr.positions.append(pos)

    mgr.update_position(pos, high=100.6, low=100.2, close=100.4,
                        bar_ts=_utc(2026, 3, 10, 22))
    # 07:00 UTC = 07:00 Londres GMT : PAS encore le mur
    decisions = mgr.update_position(pos, high=100.6, low=100.2, close=100.4,
                                    bar_ts=_utc(2026, 3, 11, 7))
    assert pos.is_open
    assert decisions == []

    decisions = mgr.update_position(pos, high=100.6, low=100.2, close=100.4,
                                    bar_ts=_utc(2026, 3, 11, 8))
    assert not pos.is_open
    assert decisions[0].decision_type == DecisionType.EXIT_SESSION


def test_hiver_ecart_5h_standard():
    """2026-01-06 : EST/GMT. Entrée 18:00 NY = 23:00 UTC. Mur = 08:00 GMT
    = 08:00 UTC le 07."""
    mgr = PositionManager(_session_config())
    pos = _long_position()
    mgr.positions.append(pos)

    mgr.update_position(pos, high=100.6, low=100.2, close=100.4,
                        bar_ts=_utc(2026, 1, 6, 23))
    assert pos.signal_data["session_exit_deadline"] == "2026-01-07T08:00:00+00:00"


# ── Robustesse ────────────────────────────────────────────────────────

def test_bar_ts_naif_traite_comme_utc():
    mgr = PositionManager(_session_config())
    pos = _long_position()
    mgr.positions.append(pos)

    mgr.update_position(pos, high=100.6, low=100.2, close=100.4,
                        bar_ts=datetime(2025, 7, 8, 22))  # naïf
    decisions = mgr.update_position(pos, high=100.6, low=100.2, close=100.4,
                                    bar_ts=datetime(2025, 7, 9, 7))  # naïf
    assert not pos.is_open
    assert decisions[0].decision_type == DecisionType.EXIT_SESSION


def test_session_exit_config_invalide_echoue_a_l_init():
    with pytest.raises(ValueError, match="session_exit invalide"):
        PositionManager(_session_config(session_exit="8h@London"))
    with pytest.raises(ValueError, match="session_exit invalide"):
        PositionManager(_session_config(session_exit="08:00@Pas/Un_Fuseau"))
