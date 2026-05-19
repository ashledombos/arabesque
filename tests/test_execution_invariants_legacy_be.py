"""Non-régression : ``be_inferred_but_loser`` doit détecter le cas
fondateur XAUUSD 14-05 (record legacy pré-fix be_source).

Avant 2026-05-17, ``be_source`` n'était pas écrit dans le journal. Les
exits XAUUSD 14-05 (trade_id ae845c5d-fb2, FTMO, MFE 0.91R, exit -1R)
ont ``be_source=None``. L'invariant initial requérait ``be_source ==
"inferred_from_mfe"`` → faux négatif sur le cas même qui a motivé
l'invariant.

Heuristique de récupération (fallback legacy) :
    be_source ∈ {None, "unknown"}
    ET mfe_r ≥ 0.3
    ET result_r ≤ -0.5
    ET exit_reason ∈ {stop_loss, reconciled_stop_loss}
→ équivaut sémantiquement à inferred_from_mfe.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_execution_invariants.py"


def _import_invariants_module():
    spec = importlib.util.spec_from_file_location("_invariants_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_invariants_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


def _xauusd_14_05_record() -> dict:
    """Le record réel observé (FTMO côté, exit reconciled_stop_loss).

    cf. logs/trade_journal.jsonl ligne avec trade_id ae845c5d-fb2.
    """
    return {
        "event": "exit",
        "ts": "2026-05-14T17:24:45.050337+00:00",
        "trade_id": "ae845c5d-fb2",
        "instrument": "XAUUSD",
        "strategy": "glissade",
        "broker_id": "ftmo_challenge",
        "result_r": -1.002,
        "mfe_r": 0.91,
        "exit_reason": "reconciled_stop_loss",
        "be_set": True,
        "be_source": None,  # legacy : champ pas encore émis
    }


def test_xauusd_14_05_detected_via_legacy_fallback():
    """Le cas fondateur DOIT déclencher be_inferred_but_loser."""
    mod = _import_invariants_module()
    rep = mod._evaluate([_xauusd_14_05_record()])

    triggered_ids = [t[0] for t in rep["triggers"]]
    assert "be_inferred_but_loser" in triggered_ids, (
        f"REGRESSION : XAUUSD 14-05 (be_source=None legacy) n'est plus "
        f"détecté. Triggers vus : {triggered_ids}. "
        f"Détails : {rep['details'].get('be_inferred_but_loser')}"
    )
    assert rep["details"]["be_inferred_but_loser"]["count"] == 1


def test_post_fix_inferred_from_mfe_still_works():
    """Records post-fix avec be_source explicite → comportement inchangé."""
    mod = _import_invariants_module()
    rec = {
        "event": "exit",
        "ts": "2026-05-20T10:00:00+00:00",
        "trade_id": "post-fix-1",
        "instrument": "EURUSD",
        "broker_id": "ftmo_challenge",
        "result_r": -1.0,
        "mfe_r": 0.5,
        "exit_reason": "reconciled_stop_loss",
        "be_source": "inferred_from_mfe",
    }
    rep = mod._evaluate([rec])
    assert rep["details"]["be_inferred_but_loser"]["count"] == 1


def test_legacy_clean_loser_not_flagged():
    """Loser legacy SANS mfe≥0.3 ne doit PAS être flagué."""
    mod = _import_invariants_module()
    rec = {
        "event": "exit",
        "ts": "2026-05-10T10:00:00+00:00",
        "trade_id": "clean-loser",
        "instrument": "EURUSD",
        "broker_id": "ftmo_challenge",
        "result_r": -1.0,
        "mfe_r": 0.05,  # bouge à peine
        "exit_reason": "stop_loss",
        "be_source": None,
    }
    rep = mod._evaluate([rec])
    assert rep["details"]["be_inferred_but_loser"]["count"] == 0


def test_legacy_breakeven_winner_not_flagged():
    """BE exit avec mfe ≥ 0.3 n'est pas un loser → pas de flag."""
    mod = _import_invariants_module()
    rec = {
        "event": "exit",
        "ts": "2026-05-10T10:00:00+00:00",
        "trade_id": "be-winner",
        "instrument": "EURUSD",
        "broker_id": "ftmo_challenge",
        "result_r": 0.18,
        "mfe_r": 0.31,
        "exit_reason": "breakeven_exit",
        "be_source": None,
    }
    rep = mod._evaluate([rec])
    assert rep["details"]["be_inferred_but_loser"]["count"] == 0


def test_legacy_loser_with_unrelated_exit_reason_not_flagged():
    """Loser legacy avec mfe≥0.3 mais exit_reason ≠ stop_loss → pas de flag
    (le pattern XAUUSD requiert SL hit après MFE déjà à 0.3+).
    """
    mod = _import_invariants_module()
    rec = {
        "event": "exit",
        "ts": "2026-05-10T10:00:00+00:00",
        "trade_id": "ambiguous-1",
        "instrument": "EURUSD",
        "broker_id": "ftmo_challenge",
        "result_r": -1.0,
        "mfe_r": 0.5,
        "exit_reason": "reconciled_other",  # cas ambigu
        "be_source": None,
    }
    rep = mod._evaluate([rec])
    # Pas un be_inferred_but_loser (mais peut quand même trigger autres)
    assert rep["details"]["be_inferred_but_loser"]["count"] == 0


def test_be_unarmed_ratio_unchanged_by_p3():
    """Le trigger be_unarmed_ratio (ratio global) ne doit pas changer
    de comportement avec le patch P3 (P3 ne touche QUE be_inferred_but_loser).

    Loser plein avec be_source=None + mfe≥0.3 → fallback be_set=True → considéré
    armé broker → PAS dans le ratio be_unarmed.
    """
    mod = _import_invariants_module()
    # 5 losers, dont 1 avec be_set=True (pas dans be_unarmed)
    recs = []
    for i in range(4):
        recs.append({
            "event": "exit",
            "ts": f"2026-05-1{i}T10:00:00+00:00",
            "trade_id": f"l-{i}",
            "instrument": "EURUSD",
            "broker_id": "ftmo_challenge",
            "result_r": -1.0,
            "mfe_r": 0.05,
            "exit_reason": "stop_loss",
            "be_set": False,
            "be_source": None,
        })
    recs.append({
        "event": "exit",
        "ts": "2026-05-14T10:00:00+00:00",
        "trade_id": "l-be-set",
        "instrument": "EURUSD",
        "broker_id": "ftmo_challenge",
        "result_r": -1.0,
        "mfe_r": 0.4,
        "exit_reason": "stop_loss",
        "be_set": True,  # legacy "armé" via fallback
        "be_source": None,
    })
    rep = mod._evaluate(recs)
    # be_unarmed_ratio : be_set=True → considéré armé → 0/5 unarmed
    # (comportement pré-P3, P3 n'a pas touché ce code path)
    assert rep["details"]["be_unarmed"]["count"] == 0
