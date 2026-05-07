"""Tests pour LiveEngine._reconstruct_exit_from_history.

Couvre la logique de reconstruction d'exit pour positions fermées pendant
un downtime (incident 2026-05-07). Trois sources possibles :
  - broker_detail   : get_closed_position_detail() retourne le vrai prix
  - bars_reconstruction : MFE >= 0.3R observé sur min1 → BE armé probable
  - estimated_fallback : aucune info → SL plein (ancien comportement)
"""
from __future__ import annotations

import asyncio
import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from arabesque.execution.live import LiveEngine


def _make_engine():
    """Construit un LiveEngine minimal sans démarrer le moteur réel."""
    eng = LiveEngine.__new__(LiveEngine)
    eng._brokers = {}
    return eng


def _entry(side="LONG", entry=2000.0, sl=1990.0, tp=2020.0, instrument="XAUUSD"):
    """Helper : entry_record style trade_journal.jsonl."""
    return {
        "instrument": instrument,
        "side": side,
        "entry_price": entry,
        "sl": sl,
        "tp": tp,
        "ts": "2026-05-01T10:00:00+00:00",
        "broker_id": "ftmo_challenge",
        "position_id": "P1",
        "strategy": "extension",
        "trade_id": "T1",
        "volume": 1.0,
        "risk_cash": 100.0,
    }


def _bars(df_dict):
    """Construit un DataFrame OHLC indexé UTC."""
    idx = pd.to_datetime(df_dict["ts"], utc=True)
    return pd.DataFrame(
        {"Open": df_dict["o"], "High": df_dict["h"],
         "Low": df_dict["l"], "Close": df_dict["c"]},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Cas 1 : broker_detail disponible — TP touché
# ---------------------------------------------------------------------------
def test_broker_detail_tp_hit():
    eng = _make_engine()
    broker = MagicMock()
    broker.get_closed_position_detail = AsyncMock(return_value={
        "exit_price": 2020.0,
        "exit_time": "2026-05-01T11:30:00+00:00",
    })
    eng._brokers["ftmo_challenge"] = broker

    bars = _bars({
        "ts": ["2026-05-01T10:00", "2026-05-01T11:00", "2026-05-01T11:30"],
        "o": [2000, 2010, 2020], "h": [2005, 2015, 2020],
        "l": [1995, 2005, 2015], "c": [2003, 2014, 2020],
    })
    with patch("arabesque.data.store.load_ohlc", return_value=bars):
        out = asyncio.run(eng._reconstruct_exit_from_history(
            "ftmo_challenge", "P1", _entry()
        ))

    assert out["source"] == "broker_detail"
    assert out["exit_price"] == 2020.0
    assert out["exit_reason"] == "reconciled_take_profit"
    assert out["mfe_r"] == 2.0  # (2020-2000)/10
    assert out["be_set"] is True


# ---------------------------------------------------------------------------
# Cas 2 : broker_detail disponible — SL touché, MFE faible
# ---------------------------------------------------------------------------
def test_broker_detail_sl_hit_low_mfe():
    eng = _make_engine()
    broker = MagicMock()
    broker.get_closed_position_detail = AsyncMock(return_value={
        "exit_price": 1990.0,
        "exit_time": "2026-05-01T11:30:00+00:00",
    })
    eng._brokers["ftmo_challenge"] = broker

    bars = _bars({
        "ts": ["2026-05-01T10:00", "2026-05-01T11:00", "2026-05-01T11:30"],
        "o": [2000, 1998, 1992], "h": [2001, 2000, 1995],
        "l": [1995, 1990, 1990], "c": [1998, 1992, 1990],
    })
    with patch("arabesque.data.store.load_ohlc", return_value=bars):
        out = asyncio.run(eng._reconstruct_exit_from_history(
            "ftmo_challenge", "P1", _entry()
        ))

    assert out["source"] == "broker_detail"
    assert out["exit_reason"] == "reconciled_stop_loss"
    # MFE = (2001 - 2000)/10 = 0.1R
    assert out["mfe_r"] == pytest.approx(0.1, abs=0.01)
    assert out["be_set"] is False


# ---------------------------------------------------------------------------
# Cas 3 : broker_detail disponible — exit ambigu (mid-range) → "other"
# ---------------------------------------------------------------------------
def test_broker_detail_other():
    eng = _make_engine()
    broker = MagicMock()
    broker.get_closed_position_detail = AsyncMock(return_value={
        "exit_price": 2008.0,  # ni TP, ni SL, ni BE
        "exit_time": "2026-05-01T11:30:00+00:00",
    })
    eng._brokers["ftmo_challenge"] = broker

    bars = _bars({
        "ts": ["2026-05-01T10:00", "2026-05-01T11:00", "2026-05-01T11:30"],
        "o": [2000, 2005, 2008], "h": [2002, 2008, 2009],
        "l": [1998, 2003, 2007], "c": [2001, 2006, 2008],
    })
    with patch("arabesque.data.store.load_ohlc", return_value=bars):
        out = asyncio.run(eng._reconstruct_exit_from_history(
            "ftmo_challenge", "P1", _entry()
        ))

    assert out["source"] == "broker_detail"
    assert out["exit_reason"] == "reconciled_other"
    # MFE = (2009-2000)/10 = 0.9R
    assert out["mfe_r"] == pytest.approx(0.9, abs=0.01)
    assert out["be_set"] is True


# ---------------------------------------------------------------------------
# Cas 4 : broker indispo, mais bars montrent MFE >= 0.3R → BE inferred
# ---------------------------------------------------------------------------
def test_no_broker_high_mfe_be_inferred():
    eng = _make_engine()
    broker = MagicMock()
    broker.get_closed_position_detail = AsyncMock(side_effect=Exception("API down"))
    eng._brokers["ftmo_challenge"] = broker

    bars = _bars({
        "ts": ["2026-05-01T10:00", "2026-05-01T11:00", "2026-05-01T12:00"],
        "o": [2000, 2003, 2001], "h": [2008, 2012, 2002],
        "l": [1999, 2000, 1995], "c": [2003, 2001, 1996],
    })
    with patch("arabesque.data.store.load_ohlc", return_value=bars):
        out = asyncio.run(eng._reconstruct_exit_from_history(
            "ftmo_challenge", "P1", _entry()
        ))

    # broker en erreur → real_fill=None → fallback bars
    assert out["source"] == "bars_reconstruction"
    assert out["exit_reason"] == "reconciled_breakeven_exit"
    # MFE = (2012-2000)/10 = 1.2R → BE armé
    assert out["mfe_r"] == pytest.approx(1.2, abs=0.01)
    assert out["be_set"] is True
    # BE target = entry + 0.20R = 2000 + 2 = 2002
    assert out["exit_price"] == pytest.approx(2002.0, abs=0.01)


# ---------------------------------------------------------------------------
# Cas 5 : broker indispo, bars indispo → fallback estimated_fallback
# ---------------------------------------------------------------------------
def test_full_fallback():
    eng = _make_engine()
    broker = MagicMock()
    broker.get_closed_position_detail = AsyncMock(side_effect=Exception("API down"))
    eng._brokers["ftmo_challenge"] = broker

    with patch("arabesque.data.store.load_ohlc", side_effect=Exception("no parquet")):
        out = asyncio.run(eng._reconstruct_exit_from_history(
            "ftmo_challenge", "P1", _entry()
        ))

    assert out["source"] == "estimated_fallback"
    assert out["exit_reason"] == "reconciled_stop_loss"
    assert out["exit_price"] == 1990.0  # SL
    assert out["mfe_r"] == 0.0
    assert out["be_set"] is False


# ---------------------------------------------------------------------------
# Cas 6 : SHORT — MFE inversé (entry - min_low)
# ---------------------------------------------------------------------------
def test_short_mfe_correct_direction():
    eng = _make_engine()
    broker = MagicMock()
    broker.get_closed_position_detail = AsyncMock(return_value={
        "exit_price": 1980.0,  # TP SHORT
        "exit_time": "2026-05-01T11:30:00+00:00",
    })
    eng._brokers["ftmo_challenge"] = broker

    bars = _bars({
        "ts": ["2026-05-01T10:00", "2026-05-01T11:00", "2026-05-01T11:30"],
        "o": [2000, 1995, 1985], "h": [2002, 1998, 1990],
        "l": [1990, 1980, 1980], "c": [1995, 1990, 1980],
    })
    entry = _entry(side="SHORT", entry=2000.0, sl=2010.0, tp=1980.0)
    with patch("arabesque.data.store.load_ohlc", return_value=bars):
        out = asyncio.run(eng._reconstruct_exit_from_history(
            "ftmo_challenge", "P1", entry
        ))

    assert out["exit_reason"] == "reconciled_take_profit"
    # MFE SHORT = (entry - min_low)/R = (2000-1980)/10 = 2.0
    assert out["mfe_r"] == pytest.approx(2.0, abs=0.01)
    assert out["be_set"] is True


# ---------------------------------------------------------------------------
# Cas 7 (régression incident 2026-05-07) : losers historiques mfe_zero
#   Avant fix : mfe_r=0, be_set=False, exit_price=sl (forcés)
#   Après fix : reconstruction depuis bars → MFE réel
# ---------------------------------------------------------------------------
def test_regression_old_bug_mfe_zero_loser():
    """Avant 2026-05-07, _reconcile_missed_exits hardcodait mfe_r=0/be=False.
    Si une position avait en réalité atteint 0.3R MFE puis SL, on perdait
    +0.20R BE (loser franc affiché à -1R). Ce test confirme que la
    reconstruction récupère bien le MFE depuis les bars min1."""
    eng = _make_engine()
    broker = MagicMock()
    broker.get_closed_position_detail = AsyncMock(return_value={
        "exit_price": 1990.0,
        "exit_time": "2026-05-01T13:00:00+00:00",
    })
    eng._brokers["ftmo_challenge"] = broker

    # Position LONG : a atteint 2005 (= 0.5R MFE) puis revenue à SL
    bars = _bars({
        "ts": ["2026-05-01T10:00", "2026-05-01T11:00",
               "2026-05-01T12:00", "2026-05-01T13:00"],
        "o": [2000, 2003, 2004, 1995],
        "h": [2003, 2005, 2005, 1998],
        "l": [1999, 2001, 1996, 1990],
        "c": [2002, 2004, 1998, 1990],
    })
    with patch("arabesque.data.store.load_ohlc", return_value=bars):
        out = asyncio.run(eng._reconstruct_exit_from_history(
            "ftmo_challenge", "P1", _entry()
        ))

    # Le bug historique aurait donné mfe=0, be=False, result=-1R
    # Le fix donne mfe=0.5, be=True → invariant be_unarmed_ratio respecté
    assert out["mfe_r"] == pytest.approx(0.5, abs=0.01)
    assert out["be_set"] is True
    # Note : exit_price=1990 (SL) reste ce que le broker a réellement servi.
    # Le BE n'a pas été physiquement armé sur le broker, mais le tracker
    # signale désormais correctement que le tracking aurait dû le faire.
