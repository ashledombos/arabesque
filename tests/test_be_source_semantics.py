"""Tests pour la sémantique be_source dans _reconstruct_exit_from_history.

Contexte (cf. DECISIONS.md §3 "be_source") : le champ be_set du record exit
mélangeait deux sémantiques distinctes :
  - path live (position_monitor.py:_check_breakeven) : be_set=True UNIQUEMENT
    après broker.amend_position_sltp success → état physique réel.
  - path reconcile (live.py:_reconstruct_exit_from_history) : be_set = mfe_r
    >= 0.3 → inférence post-hoc depuis bars parquet, AUCUNE preuve broker.

Incident XAUUSD 14-05 (trade_id ae845c5d-fb2, FTMO) : engine FTMO down
09:47→17:23 UTC (panne PriceFeed). Position entrée 09:01:24, fermée
17:24:45 par SL plein côté broker (exit=4666.89 = SL−0.05). Le journal
affichait be_set=True (mfe_r=0.91 reconstruit depuis parquet), faisant
croire que le BE était armé, alors que le broker n'avait jamais été
amendé (Phase 2.5 BE polling pas active à cette date).

Ce test fige la sémantique attendue (taxonomie stricte) :
  - broker_armed : amend_position_sltp success OBSERVÉ en live (path
    position_monitor._check_breakeven). Preuve directe.
  - broker_evidence : path reconcile, broker_detail confirme exit ≈
    be_target → preuve forte INDIRECTE (on déduit l'amend, on ne l'observe
    pas). N'apparaît jamais dans le path live.
  - inferred_from_mfe : MFE parquet seul, aucune preuve broker.
  - not_armed : ni preuve, ni inférence.

Les invariants traitent broker_armed et broker_evidence comme "BE armé
broker-side" car un exit ≈ be_target côté broker implique nécessairement
qu'un amend a eu lieu. La distinction sert à la traçabilité d'audit, pas
à invalider la preuve.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from arabesque.execution.live import LiveEngine


def _make_engine():
    eng = LiveEngine.__new__(LiveEngine)
    eng._brokers = {}
    return eng


def _entry(side="LONG", entry=4693.06, sl=4666.94, tp=4771.90, instrument="XAUUSD"):
    """Trade XAUUSD glissade LONG (R = 26.12) — proche du cas 14-05."""
    return {
        "instrument": instrument,
        "side": side,
        "entry_price": entry,
        "sl": sl,
        "tp": tp,
        "ts": "2026-05-14T09:01:24+00:00",
        "broker_id": "ftmo_challenge",
        "position_id": "52759859",
        "strategy": "glissade",
        "trade_id": "ae845c5d-fb2",
        "volume": 0.01,
        "risk_cash": 4.88,
    }


def _bars_with_mfe(mfe_r: float, entry=4693.06, sl=4666.94, is_long=True):
    """Construit un DataFrame OHLC avec MFE contrôlé sur la fenêtre du trade."""
    R = abs(entry - sl)
    if is_long:
        max_price = entry + mfe_r * R
        low_price = entry - 0.1 * R  # léger creux
    else:
        max_price = entry - mfe_r * R
        low_price = entry + 0.1 * R
    idx = pd.to_datetime(
        ["2026-05-14T09:01", "2026-05-14T13:00", "2026-05-14T17:24"],
        utc=True,
    )
    return pd.DataFrame(
        {"Open": [entry, entry, entry],
         "High": [max_price, max_price, max_price],
         "Low": [low_price, low_price, low_price],
         "Close": [entry, max_price, entry]},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Cas 1 (XAUUSD 14-05) : broker_detail dit SL hit, MFE parquet = 0.91R
#   → be_source DOIT être "inferred_from_mfe" (et PAS broker_armed).
#   Régression du bug de sémantique : avant ce fix, be_set=True faisait
#   croire que le BE était armé broker-side. La vérité : engine down,
#   amend_position_sltp jamais appelé, broker a hit SL plein.
# ---------------------------------------------------------------------------
def test_xauusd_14may_inferred_from_mfe_not_broker_armed():
    eng = _make_engine()
    broker = MagicMock()
    # Broker confirme SL plein touché (exit ≈ sl − 0.05, slip mineur)
    broker.get_closed_position_detail = AsyncMock(return_value={
        "exit_price": 4666.89,
        "exit_time": "2026-05-14T17:24:45+00:00",
    })
    eng._brokers["ftmo_challenge"] = broker

    bars = _bars_with_mfe(mfe_r=0.91)
    with patch("arabesque.data.store.load_ohlc", return_value=bars):
        out = asyncio.run(eng._reconstruct_exit_from_history(
            "ftmo_challenge", "52759859", _entry()
        ))

    # Champs descriptifs : MFE théorique élevé, mais SL plein touché
    assert out["mfe_r"] >= 0.85, f"MFE attendu ≈0.91R, obtenu {out['mfe_r']}"
    assert out["exit_reason"] == "reconciled_stop_loss"
    assert out["source"] == "broker_detail"
    # be_set reste True pour rétrocompat (mfe >= 0.3) — c'est pourquoi il ne
    # doit PAS être utilisé seul dans les invariants critiques.
    assert out["be_set"] is True
    # LA sémantique qui compte : be_source dit que le BE était purement
    # théorique. Le broker a confirmé exit=SL → pas amendé en pratique.
    assert out["be_source"] == "inferred_from_mfe", (
        f"be_source attendu 'inferred_from_mfe', obtenu '{out['be_source']}'. "
        f"Pattern XAUUSD 14-05 : MFE parquet 0.91R mais broker hit SL plein "
        f"→ aucune preuve broker que le SL ait été amendé."
    )


# ---------------------------------------------------------------------------
# Cas 2 : broker_detail dit exit ≈ BE target ET MFE >= 0.3R
#   → be_source = "broker_evidence" (preuve forte INDIRECTE, pas
#     "broker_armed" qui est réservé au path live amend_position_sltp).
# ---------------------------------------------------------------------------
def test_broker_evidence_from_exit_price():
    eng = _make_engine()
    entry = _entry()
    R = abs(entry["entry_price"] - entry["sl"])
    be_target = entry["entry_price"] + 0.20 * R  # LONG → entry + 0.20R
    broker = MagicMock()
    broker.get_closed_position_detail = AsyncMock(return_value={
        "exit_price": be_target,
        "exit_time": "2026-05-14T17:24:45+00:00",
    })
    eng._brokers["ftmo_challenge"] = broker

    bars = _bars_with_mfe(mfe_r=0.5)  # MFE suffisant pour BE
    with patch("arabesque.data.store.load_ohlc", return_value=bars):
        out = asyncio.run(eng._reconstruct_exit_from_history(
            "ftmo_challenge", "52759859", entry
        ))

    assert out["exit_reason"] == "reconciled_breakeven_exit"
    assert out["source"] == "broker_detail"
    assert out["be_source"] == "broker_evidence", (
        f"path reconcile + broker confirme exit ≈ be_target → "
        f"be_source='broker_evidence' (preuve indirecte). "
        f"'broker_armed' est réservé au path live amend success observé. "
        f"Obtenu : '{out['be_source']}'"
    )


# ---------------------------------------------------------------------------
# Cas 3 : pas de broker_detail (broker indispo) mais MFE >= 0.3R parquet
#   → be_source = "inferred_from_mfe" (suppose BE armé avant coupure,
#     mais c'est une INFÉRENCE depuis les bars, pas un état broker).
# ---------------------------------------------------------------------------
def test_no_broker_detail_mfe_high_inferred():
    eng = _make_engine()
    broker = MagicMock()
    broker.get_closed_position_detail = AsyncMock(return_value=None)
    eng._brokers["ftmo_challenge"] = broker

    bars = _bars_with_mfe(mfe_r=0.5)
    with patch("arabesque.data.store.load_ohlc", return_value=bars):
        out = asyncio.run(eng._reconstruct_exit_from_history(
            "ftmo_challenge", "52759859", _entry()
        ))

    assert out["source"] == "bars_reconstruction"
    assert out["be_source"] == "inferred_from_mfe", (
        "pas de broker, MFE >= 0.3R parquet → BE inféré, pas armé broker"
    )


# ---------------------------------------------------------------------------
# Cas 4 : ni broker_detail ni MFE significatif (estimated_fallback)
#   → be_source = "not_armed".
# ---------------------------------------------------------------------------
def test_estimated_fallback_not_armed():
    eng = _make_engine()
    broker = MagicMock()
    broker.get_closed_position_detail = AsyncMock(return_value=None)
    eng._brokers["ftmo_challenge"] = broker

    bars = _bars_with_mfe(mfe_r=0.1)  # MFE trop faible pour BE
    with patch("arabesque.data.store.load_ohlc", return_value=bars):
        out = asyncio.run(eng._reconstruct_exit_from_history(
            "ftmo_challenge", "52759859", _entry()
        ))

    assert out["source"] == "estimated_fallback"
    assert out["be_set"] is False
    assert out["be_source"] == "not_armed"


# ---------------------------------------------------------------------------
# Cas 5 (invariant be_inferred_but_loser) : un trade reconcilié avec
#   be_source=inferred_from_mfe + loser doit déclencher le nouvel invariant
#   dans check_execution_invariants.
# ---------------------------------------------------------------------------
def test_invariant_be_inferred_but_loser():
    """Le scénario XAUUSD 14-05 doit déclencher be_inferred_but_loser."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from check_execution_invariants import _evaluate

    exits = [{
        "event": "exit",
        "ts": "2026-05-14T17:24:45+00:00",
        "instrument": "XAUUSD",
        "strategy": "glissade",
        "result_r": -1.002,
        "mfe_r": 0.91,
        "be_set": True,
        "be_source": "inferred_from_mfe",  # ← clé : le nouveau champ
        "exit_reason": "reconciled_stop_loss",
        "broker_id": "ftmo_challenge",
    }]

    report = _evaluate(exits)
    trigger_ids = [t[0] for t in report["triggers"]]
    assert "be_inferred_but_loser" in trigger_ids, (
        f"L'invariant be_inferred_but_loser DOIT déclencher sur un trade "
        f"reconcilié inferred_from_mfe + loser. Triggers obtenus: {trigger_ids}"
    )


# ---------------------------------------------------------------------------
# Cas 6 : rétrocompat — record ancien sans be_source ne casse pas l'invariant.
#   be_set=False + mfe>=0.3 + loser → be_unarmed_ratio doit fonctionner.
# ---------------------------------------------------------------------------
def test_invariant_retrocompat_no_be_source():
    """Ancien record (pré-2026-05-17) sans be_source : fallback be_set."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from check_execution_invariants import _be_was_armed_broker

    # Record ancien, be_set=True, pas de be_source → considéré armé
    old_record = {"be_set": True, "result_r": -1.0, "mfe_r": 0.5}
    assert _be_was_armed_broker(old_record) is True

    # Record ancien, be_set=False → considéré non armé
    old_record_no_be = {"be_set": False, "result_r": -1.0, "mfe_r": 0.5}
    assert _be_was_armed_broker(old_record_no_be) is False

    # Nouveau record avec be_source="inferred_from_mfe" → non armé broker
    new_record = {"be_set": True, "be_source": "inferred_from_mfe",
                  "result_r": -1.0, "mfe_r": 0.91}
    assert _be_was_armed_broker(new_record) is False, (
        "be_source=inferred_from_mfe doit primer sur be_set=True"
    )

    # Nouveau record avec be_source="broker_armed" → vraiment armé (live)
    new_armed = {"be_set": True, "be_source": "broker_armed",
                 "result_r": 0.2, "mfe_r": 0.5}
    assert _be_was_armed_broker(new_armed) is True

    # Nouveau record avec be_source="broker_evidence" → preuve indirecte
    # forte (exit ≈ be_target) → compté comme armé broker pour les invariants.
    new_evidence = {"be_set": True, "be_source": "broker_evidence",
                    "result_r": 0.2, "mfe_r": 0.5}
    assert _be_was_armed_broker(new_evidence) is True, (
        "broker_evidence (exit broker ≈ be_target) doit compter comme "
        "armé broker — un exit à be_target implique nécessairement un amend"
    )
