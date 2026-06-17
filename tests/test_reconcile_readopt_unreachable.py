"""Re-adoption d'une position connue-ouverte quand le broker est injoignable au démarrage.

Incident fondateur 2026-06-12 : restart maintenance ~21h UTC avec le canal trading
cTrader mort. ``get_positions()`` renvoie ``[]`` (cTrader fait ``if not self._connected:
return self._positions`` → liste vide) SANS lever. L'ancien ``_reconcile_existing_positions``
faisait ``if not positions: continue`` → la position glissade BTCUSD connue-ouverte dans le
journal n'était NI ré-enregistrée pour le BE-polling NI retentée → dangling 4 jours, jusqu'à
ce qu'un restart ultérieur (broker joignable) la reconcilie. Coût : une position vivante non
surveillée par le BE/trailing pendant des jours.

Règle : un ``[]`` d'un broker injoignable n'est PAS une preuve de flat. Tant que le journal
porte une entry ouverte pour ce broker et que le broker n'a pas confirmé son état
(``is_connected()`` False ou get_positions qui lève), il faut ré-adopter la position depuis
le journal pour que le BE-polling reprenne. La boucle reconcile la fermera proprement si elle
s'avère close une fois le broker revenu (cf. feedback_reconcile_broker_unreachable : on ne
fabrique pas d'exit, on diffère — mais on ne lâche pas la surveillance non plus).
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from arabesque.execution.live import LiveEngine


def _write_open_entry(journal_path, broker_id="ftmo_challenge", position_id="POS-BTC-1"):
    journal_path.write_text(json.dumps({
        "event": "entry",
        "broker_id": broker_id,
        "position_id": position_id,
        "instrument": "BTCUSD",
        "strategy": "glissade",
        "side": "SHORT",
        "entry_price": 63662.79,
        "sl": 64299.0,
        "tp": 62390.0,
        "volume": 0.08,
    }) + "\n")


def _make_engine(broker, journal_path, monkeypatch):
    monkeypatch.setattr("arabesque.execution.live.TRADE_JOURNAL_PATH", journal_path)
    engine = LiveEngine.__new__(LiveEngine)
    engine._brokers = {"ftmo_challenge": broker}
    engine._position_monitor = MagicMock()
    engine._live_monitor = MagicMock()
    return engine


def test_readopts_journal_open_position_when_broker_returns_empty_but_disconnected(
    tmp_path, monkeypatch
):
    """Le bug 06-12 : get_positions=[] + canal mort + entry journal ouverte → ré-adoption."""
    journal = tmp_path / "trade_journal.jsonl"
    _write_open_entry(journal)

    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])          # canal trading mort
    broker.is_connected = False        # @property bool sur BrokerBase — broker injoignable
    broker.get_symbol_info = AsyncMock(return_value=None)

    engine = _make_engine(broker, journal, monkeypatch)
    asyncio.run(engine._reconcile_existing_positions())

    # La position connue-ouverte DOIT être ré-enregistrée pour que le BE-polling reprenne.
    engine._position_monitor.register_position.assert_called()
    kwargs = engine._position_monitor.register_position.call_args.kwargs
    assert kwargs["position_id"] == "POS-BTC-1"
    assert kwargs["symbol"] == "BTCUSD"
    assert kwargs["sl"] == 64299.0
    assert kwargs["volume"] == 0.08


def test_does_not_readopt_when_broker_connected_and_genuinely_flat(
    tmp_path, monkeypatch
):
    """Broker SAIN qui répond [] = vraiment flat : ne PAS ressusciter (missed-exit s'en charge)."""
    journal = tmp_path / "trade_journal.jsonl"
    _write_open_entry(journal)

    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])          # broker répond
    broker.is_connected = True         # @property bool sur BrokerBase — broker sain
    broker.get_symbol_info = AsyncMock(return_value=None)

    engine = _make_engine(broker, journal, monkeypatch)
    asyncio.run(engine._reconcile_existing_positions())

    engine._position_monitor.register_position.assert_not_called()


def test_readopts_journal_open_position_when_broker_raises(tmp_path, monkeypatch):
    """get_positions qui lève (broker injoignable) + entry journal ouverte → ré-adoption aussi."""
    journal = tmp_path / "trade_journal.jsonl"
    _write_open_entry(journal)

    broker = MagicMock()
    broker.get_positions = AsyncMock(side_effect=ConnectionError("trading channel dead"))
    broker.is_connected = False
    broker.get_symbol_info = AsyncMock(return_value=None)

    engine = _make_engine(broker, journal, monkeypatch)
    asyncio.run(engine._reconcile_existing_positions())

    engine._position_monitor.register_position.assert_called()
    kwargs = engine._position_monitor.register_position.call_args.kwargs
    assert kwargs["position_id"] == "POS-BTC-1"
