"""Non-régression : le replay live vs théorie ne doit pas écraser un exit
quand FTMO et GFT prennent le même signal (même trade_id).

Avant le fix 2026-05-19, `load_trades` keyait `entries`/`exits` par
``trade_id`` seul. Le 2e exit (ordre journal) écrasait le 1er → sur 35
paires FTMO/GFT observées, la moitié disparaissait du replay (n et meanΔR
biaisés sur la moitié des données).

Après fix : key = ``(trade_id, broker_id)``. Les deux brokers cohabitent.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "replay_live_vs_theory.py"


def _import_replay_module():
    """Importe le script comme module (non packagé)."""
    spec = importlib.util.spec_from_file_location("_replay_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_replay_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_journal(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "trade_journal.jsonl"
    with path.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return path


def _make_entry(tid: str, broker: str, ts: str, strat: str = "extension",
                inst: str = "XAUUSD") -> dict:
    return {
        "event": "entry",
        "trade_id": tid,
        "broker_id": broker,
        "strategy": strat,
        "instrument": inst,
        "side": "LONG",
        "entry_price": 2000.0,
        "sl": 1990.0,
        "ts": ts,
    }


def _make_exit(tid: str, broker: str, ts: str, result_r: float,
               strat: str = "extension", inst: str = "XAUUSD") -> dict:
    return {
        "event": "exit",
        "trade_id": tid,
        "broker_id": broker,
        "strategy": strat,
        "instrument": inst,
        "result_r": result_r,
        "mfe_r": 0.4,
        "exit_reason": "breakeven_exit" if abs(result_r) < 0.25 else "stop_loss",
        "be_set": True,
        "be_source": "broker_armed",
        "exit_price": 2000.0,
        "ts": ts,
    }


def test_paired_ftmo_gft_both_kept(monkeypatch, tmp_path):
    """Un même trade_id sur FTMO + GFT doit produire 2 trades distincts.

    REGRESSION : avant le fix, l'exit GFT écrasait FTMO (ou l'inverse,
    selon ordre) et le replay perdait 1 datapoint.
    """
    mod = _import_replay_module()
    journal = _write_journal(tmp_path, [
        _make_entry("aa-1", "ftmo_challenge", "2026-05-14T08:00:00+00:00"),
        _make_entry("aa-1", "gft_compte1",   "2026-05-14T08:00:01+00:00"),
        _make_exit("aa-1",  "gft_compte1",   "2026-05-14T08:06:00+00:00", 0.0),
        _make_exit("aa-1",  "ftmo_challenge","2026-05-14T17:24:00+00:00", -1.0),
    ])
    monkeypatch.setattr(mod, "JOURNAL", journal)

    since = datetime(2026, 5, 1, tzinfo=timezone.utc)
    until = datetime(2026, 6, 1, tzinfo=timezone.utc)
    trades = mod.load_trades(since, until, strategy=None, broker=None)

    assert len(trades) == 2, (
        f"Attendu 2 trades (FTMO+GFT même tid), vu {len(trades)}. "
        "REGRESSION : exits[tid] écrase un broker."
    )
    brokers = sorted(t["broker_id"] for t in trades)
    assert brokers == ["ftmo_challenge", "gft_compte1"]
    # Vérifier que les R_live sont bien distincts (pas tous le même)
    rs = sorted(t["exit"]["result_r"] for t in trades)
    assert rs == [-1.0, 0.0]


def test_broker_filter_keeps_only_one(monkeypatch, tmp_path):
    """Le filtre --broker continue de fonctionner avec la nouvelle clé."""
    mod = _import_replay_module()
    journal = _write_journal(tmp_path, [
        _make_entry("aa-1", "ftmo_challenge", "2026-05-14T08:00:00+00:00"),
        _make_entry("aa-1", "gft_compte1",   "2026-05-14T08:00:01+00:00"),
        _make_exit("aa-1",  "ftmo_challenge","2026-05-14T17:24:00+00:00", -1.0),
        _make_exit("aa-1",  "gft_compte1",   "2026-05-14T08:06:00+00:00", 0.0),
    ])
    monkeypatch.setattr(mod, "JOURNAL", journal)

    since = datetime(2026, 5, 1, tzinfo=timezone.utc)
    until = datetime(2026, 6, 1, tzinfo=timezone.utc)
    trades = mod.load_trades(since, until, strategy=None, broker="ftmo_challenge")
    assert len(trades) == 1
    assert trades[0]["broker_id"] == "ftmo_challenge"
    assert trades[0]["exit"]["result_r"] == -1.0


def test_single_broker_unchanged(monkeypatch, tmp_path):
    """Un trade sur un seul broker (cas le plus fréquent) reste un seul trade."""
    mod = _import_replay_module()
    journal = _write_journal(tmp_path, [
        _make_entry("solo-1", "ftmo_challenge", "2026-05-11T20:00:00+00:00",
                    inst="SOLUSD"),
        _make_exit("solo-1",  "ftmo_challenge", "2026-05-11T20:30:00+00:00",
                   -1.01, inst="SOLUSD"),
    ])
    monkeypatch.setattr(mod, "JOURNAL", journal)

    since = datetime(2026, 5, 1, tzinfo=timezone.utc)
    until = datetime(2026, 6, 1, tzinfo=timezone.utc)
    trades = mod.load_trades(since, until, strategy=None, broker=None)
    assert len(trades) == 1
    assert trades[0]["broker_id"] == "ftmo_challenge"


def test_unrelated_trades_not_merged(monkeypatch, tmp_path):
    """Deux trades distincts (tid différent) ne doivent jamais fusionner."""
    mod = _import_replay_module()
    journal = _write_journal(tmp_path, [
        _make_entry("aa-1", "ftmo_challenge", "2026-05-10T10:00:00+00:00"),
        _make_exit("aa-1",  "ftmo_challenge", "2026-05-10T10:30:00+00:00", 0.5),
        _make_entry("bb-2", "ftmo_challenge", "2026-05-11T10:00:00+00:00",
                    inst="EURUSD"),
        _make_exit("bb-2",  "ftmo_challenge", "2026-05-11T10:30:00+00:00",
                   -1.0, inst="EURUSD"),
    ])
    monkeypatch.setattr(mod, "JOURNAL", journal)

    since = datetime(2026, 5, 1, tzinfo=timezone.utc)
    until = datetime(2026, 6, 1, tzinfo=timezone.utc)
    trades = mod.load_trades(since, until, strategy=None, broker=None)
    assert len(trades) == 2
    tids = sorted(t["trade_id"] for t in trades)
    assert tids == ["aa-1", "bb-2"]


def test_missing_broker_id_does_not_drop(monkeypatch, tmp_path):
    """Un record sans broker_id (legacy) ne doit pas casser le matching :
    on utilise '?' comme broker, et entry/exit avec broker_id absent matchent.
    """
    mod = _import_replay_module()
    events = [
        {"event": "entry", "trade_id": "x-1", "strategy": "extension",
         "instrument": "EURUSD", "side": "LONG", "entry_price": 1.1,
         "sl": 1.09, "ts": "2026-05-10T10:00:00+00:00"},
        {"event": "exit", "trade_id": "x-1", "strategy": "extension",
         "instrument": "EURUSD", "result_r": 0.5,
         "exit_reason": "take_profit", "ts": "2026-05-10T11:00:00+00:00"},
    ]
    journal = _write_journal(tmp_path, events)
    monkeypatch.setattr(mod, "JOURNAL", journal)

    since = datetime(2026, 5, 1, tzinfo=timezone.utc)
    until = datetime(2026, 6, 1, tzinfo=timezone.utc)
    trades = mod.load_trades(since, until, strategy=None, broker=None)
    assert len(trades) == 1
    assert trades[0]["broker_id"] == "?"
