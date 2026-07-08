"""Tests pour _reconcile_missed_exits quand un broker est injoignable.

Régression du bug 2026-05-14 : pendant la réconciliation au restart,
si `broker.get_positions()` levait (auth error, TCP timeout, etc.), le
code mappait le broker à un `set()` vide. Toutes les entries orphelines
de ce broker étaient alors traitées comme "fermées" → faux exits
`reconciled_stop_loss src=estimated_fallback` pondus dans le journal,
alors que les positions étaient en réalité toujours ouvertes côté broker.

Contrat : quand un broker est injoignable, on ne SAIT PAS si la position
est close. On ne doit donc RIEN écrire dans `trade_journal.jsonl`. Le
reconcile sera retenté au prochain boot du moteur.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


from arabesque.execution.live import LiveEngine


def _make_engine():
    eng = LiveEngine.__new__(LiveEngine)
    eng._brokers = {}
    eng._live_monitor = None
    return eng


def _write_journal(path: Path, entries: list[dict]) -> None:
    with path.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _entry(broker_id="ftmo_challenge", position_id="P1",
           instrument="ETHUSD", side="SHORT"):
    return {
        "event": "entry",
        "ts": "2026-05-14T07:00:00+00:00",
        "broker_id": broker_id,
        "position_id": position_id,
        "instrument": instrument,
        "side": side,
        "entry_price": 2500.0,
        "sl": 2550.0,
        "tp": 2400.0,
        "strategy": "cabriole",
        "trade_id": "T1",
        "volume": 1.0,
        "risk_cash": 100.0,
    }


def test_broker_unreachable_defers_reconcile(tmp_path, monkeypatch, caplog):
    """Broker get_positions() lève → reconcile différé, AUCUN exit écrit."""
    journal = tmp_path / "trade_journal.jsonl"
    _write_journal(journal, [_entry()])  # 1 entry orpheline, jamais d'exit

    eng = _make_engine()
    # Broker simule une auth error (ALREADY_LOGGED_IN, cf incident 14-05)
    broker = MagicMock()
    broker.get_positions = AsyncMock(side_effect=ConnectionError("ALREADY_LOGGED_IN"))
    eng._brokers["ftmo_challenge"] = broker

    # live_monitor mocké — si _reconcile_missed_exits l'appelle, le test échoue
    eng._live_monitor = MagicMock()
    eng._live_monitor._open_trades = {}

    monkeypatch.chdir(tmp_path.parent)
    # _reconcile_missed_exits ouvre logs/trade_journal.jsonl en chemin relatif
    logs_dir = tmp_path.parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    (logs_dir / "trade_journal.jsonl").write_text(journal.read_text())

    with caplog.at_level("WARNING"):
        asyncio.run(eng._reconcile_missed_exits())

    # 1. Aucun appel à record_exit
    eng._live_monitor.record_exit.assert_not_called()
    # 2. Le journal n'a pas été modifié (toujours 1 ligne = juste l'entry)
    final = (logs_dir / "trade_journal.jsonl").read_text().strip().splitlines()
    assert len(final) == 1
    rec = json.loads(final[0])
    assert rec["event"] == "entry"  # pas de nouvel exit ajouté
    # 3. Un warning explicite "différé" a été loggé
    messages = " ".join(r.message for r in caplog.records)
    assert "différé" in messages or "Reconcile différé" in messages
    assert "injoignable" in messages


def test_broker_responds_empty_still_reconciles(tmp_path, monkeypatch):
    """Broker répond avec liste vide → reconcile normal (position vraiment fermée).

    Garde-fou : le patch broker-indispo ne doit PAS bloquer le cas légitime
    où get_positions() retourne [] (broker accessible, position close en vrai).
    """
    journal = tmp_path / "trade_journal.jsonl"
    _write_journal(journal, [_entry()])

    eng = _make_engine()
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])  # broker répond, 0 position
    broker.get_closed_position_detail = AsyncMock(return_value=None)
    eng._brokers["ftmo_challenge"] = broker

    eng._live_monitor = MagicMock()
    eng._live_monitor._open_trades = {}

    monkeypatch.chdir(tmp_path.parent)
    logs_dir = tmp_path.parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    (logs_dir / "trade_journal.jsonl").write_text(journal.read_text())

    asyncio.run(eng._reconcile_missed_exits())

    # Ici on attend que record_exit SOIT appelé (position vraiment fermée,
    # estimated_fallback car broker n'a pas de détails)
    eng._live_monitor.record_exit.assert_called_once()


def test_one_broker_unreachable_other_ok(tmp_path, monkeypatch, caplog):
    """Mix : FTMO injoignable, GFT répond → ne diffère que FTMO."""
    journal = tmp_path / "trade_journal.jsonl"
    _write_journal(journal, [
        _entry(broker_id="ftmo_challenge", position_id="P1", instrument="ETHUSD"),
        _entry(broker_id="gft_compte1", position_id="P2", instrument="XAUUSD"),
    ])

    eng = _make_engine()
    ftmo = MagicMock()
    ftmo.get_positions = AsyncMock(side_effect=TimeoutError("TCP timeout"))
    gft = MagicMock()
    gft.get_positions = AsyncMock(return_value=[])  # GFT répond, 0 position
    gft.get_closed_position_detail = AsyncMock(return_value=None)
    eng._brokers["ftmo_challenge"] = ftmo
    eng._brokers["gft_compte1"] = gft

    eng._live_monitor = MagicMock()
    eng._live_monitor._open_trades = {}

    monkeypatch.chdir(tmp_path.parent)
    logs_dir = tmp_path.parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    (logs_dir / "trade_journal.jsonl").write_text(journal.read_text())

    with caplog.at_level("WARNING"):
        asyncio.run(eng._reconcile_missed_exits())

    # FTMO différé, GFT reconcilié
    assert eng._live_monitor.record_exit.call_count == 1
    call_kwargs = eng._live_monitor.record_exit.call_args.kwargs
    assert call_kwargs["broker_id"] == "gft_compte1"
    messages = " ".join(r.message for r in caplog.records)
    assert "ftmo_challenge" in messages
    assert "différé" in messages
