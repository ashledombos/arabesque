"""Test du P&L réalisé broker dans le journal d'exit (additif, best-effort).

`pnl_cash` reste THÉORIQUE (result_r × risk_cash). On ajoute, quand le broker
les fournit, les coûts réels :
- gross_pnl_cash / commission_cash / swap_cash
- net_pnl_cash = gross + commission + swap (signés broker)
- pnl_cash_gap = net_pnl_cash - pnl_cash théorique

Best-effort : si le broker ne répond pas (champs None), l'exit DOIT quand même
être journalisé, avec result_r et pnl_cash intacts et les champs coûts à None.

Scénarios : FTMO (cTrader, swap présent), GFT (TradeLocker, swap None),
broker indisponible.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from arabesque.execution.live_monitor import LiveMonitor


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    journal = tmp_path / "trade_journal.jsonl"
    monkeypatch.setattr(
        "arabesque.execution.live_monitor.TRADE_JOURNAL_PATH", journal
    )
    m = LiveMonitor.__new__(LiveMonitor)
    m._open_trades = {}
    m._closed_trades = []
    m._max_closed_history = 1000
    m._perf = {}
    m._perf_by_inst = {}
    m._perf_by_broker_strategy = {}
    m._daily_pnl = {}

    from arabesque.execution.live_monitor import ProtectionLevel, MonitorConfig
    m._protection_level = ProtectionLevel.NORMAL
    m._protection_per_broker = {}
    m._cfg = MonitorConfig()
    m._notification_channels = []
    m._consecutive_loss_threshold = 5
    m._drift_threshold_pp = 15
    m._best_day_threshold_pct = 4.0
    m._journal_path = journal
    return m


def _signal(side="LONG", instrument="BTCUSD", sl=98.0):
    sig = MagicMock()
    sig.signal_id = "abc123def456"
    sig.instrument = instrument
    sig.strategy_type = "extension"
    sig.side = MagicMock()
    sig.side.value = side
    sig.sl = sl
    sig.tp_indicative = 110.0
    return sig


def _exits(path):
    rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    return [r for r in rows if r["event"] == "exit"]


def test_ftmo_ctrader_realized_pnl_with_swap(monitor, tmp_path):
    """cTrader fournit gross + commission + swap → tous journalisés + net + gap."""
    monitor.record_entry(
        signal=_signal(), broker_id="ftmo_challenge", position_id="42",
        entry_price=100.0, volume=0.5, risk_cash=400.0,
    )
    # exit LONG à 99 sur risque 2.0 → result_r = -0.5, pnl_cash théo = -200
    monitor.record_exit(
        broker_id="ftmo_challenge", position_id="42",
        exit_price=99.0, exit_reason="stop_loss", exit_price_source="real_fill",
        broker_gross_profit=-180.0, broker_commission=-4.0, broker_swap=-6.0,
    )
    e = _exits(tmp_path / "trade_journal.jsonl")[0]
    # théorique intact
    assert e["result_r"] == pytest.approx(-0.5, abs=1e-6)
    assert e["pnl_cash"] == pytest.approx(-200.0, abs=1e-6)
    # réalisé broker
    assert e["gross_pnl_cash"] == pytest.approx(-180.0)
    assert e["commission_cash"] == pytest.approx(-4.0)
    assert e["swap_cash"] == pytest.approx(-6.0)
    assert e["net_pnl_cash"] == pytest.approx(-190.0)   # -180 -4 -6
    assert e["pnl_cash_gap"] == pytest.approx(10.0)     # -190 - (-200)


def test_gft_tradelocker_swap_none_oversizing_gap(monitor, tmp_path):
    """TradeLocker sans swap : swap_cash=None, net = gross+commission, gap reflète
    le sur-sizing min-lot (perte réelle >> perte théorique)."""
    monitor.record_entry(
        signal=_signal(instrument="XAUUSD", sl=196.0), broker_id="gft_compte1",
        position_id="100", entry_price=200.0, volume=0.5, risk_cash=300.0,
    )
    # exit LONG à 198 sur risque 4.0 → result_r = -0.5, pnl_cash théo = -150
    monitor.record_exit(
        broker_id="gft_compte1", position_id="100",
        exit_price=198.0, exit_reason="stop_loss", exit_price_source="real_fill",
        broker_gross_profit=-700.0, broker_commission=-9.0, broker_swap=None,
    )
    e = _exits(tmp_path / "trade_journal.jsonl")[0]
    assert e["pnl_cash"] == pytest.approx(-150.0, abs=1e-6)   # théorique intact
    assert e["gross_pnl_cash"] == pytest.approx(-700.0)
    assert e["commission_cash"] == pytest.approx(-9.0)
    assert e["swap_cash"] is None                              # non disponible
    assert e["net_pnl_cash"] == pytest.approx(-709.0)          # swap None → 0
    assert e["pnl_cash_gap"] == pytest.approx(-559.0)          # -709 - (-150)


def test_broker_unavailable_exit_still_journaled_with_none(monitor, tmp_path):
    """Broker injoignable (aucun coût fourni) : l'exit est QUAND MÊME journalisé,
    champs coûts à None, result_r/pnl_cash théoriques intacts."""
    monitor.record_entry(
        signal=_signal(), broker_id="ftmo_challenge", position_id="77",
        entry_price=100.0, volume=0.5, risk_cash=400.0,
    )
    monitor.record_exit(
        broker_id="ftmo_challenge", position_id="77",
        exit_price=99.0, exit_reason="stop_loss", exit_price_source="estimated",
        # aucun broker_gross_profit / commission / swap → défauts None
    )
    exits = _exits(tmp_path / "trade_journal.jsonl")
    assert len(exits) == 1                                    # exit bien enregistré
    e = exits[0]
    assert e["result_r"] == pytest.approx(-0.5, abs=1e-6)
    assert e["pnl_cash"] == pytest.approx(-200.0, abs=1e-6)
    assert e["gross_pnl_cash"] is None
    assert e["commission_cash"] is None
    assert e["swap_cash"] is None
    assert e["net_pnl_cash"] is None
    assert e["pnl_cash_gap"] is None
