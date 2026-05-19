"""Test que check_execution_invariants filtre correctement par broker.

Garantit qu'un bug isolé sur un broker n'est pas dilué par l'agrégation
globale (cf. incident 2026-05-07 : 33% reconciled GFT vs 24% FTMO).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_execution_invariants.py"

# Charge le script comme module sans exécuter main()
spec = importlib.util.spec_from_file_location("check_inv", SCRIPT)
check_inv = importlib.util.module_from_spec(spec)
sys.modules["check_inv"] = check_inv
spec.loader.exec_module(check_inv)


def _fake_journal(tmp_path: Path) -> Path:
    """Construit un journal synthétique :
      - FTMO : 10 exits propres (pas de mfe_zero)
      - GFT  : 10 exits dont 5 mfe_zero_loser (verdict CRITIQUE attendu)
    """
    p = tmp_path / "trade_journal.jsonl"
    rows = []
    # FTMO : tous winners ou losers cohérents (mfe non nul).
    # be_source explicite "broker_armed" sur les losers : depuis le patch P3
    # (2026-05-19), l'absence de be_source + mfe>=0.3 + SL hit déclenche le
    # fallback legacy be_inferred_but_loser. Ici on simule des trades propres
    # post-fix où le BE a vraiment été armé broker-side → pas un faux positif.
    for i in range(10):
        rows.append({
            "ts": f"2026-05-08T{10+i:02d}:00:00+00:00",
            "event": "exit",
            "broker_id": "ftmo_challenge",
            "result_r": 1.0 if i % 2 == 0 else -1.0,
            "mfe_r": 1.5 if i % 2 == 0 else 0.4,
            "be_set": True,
            "be_source": "broker_armed",
            "exit_reason": "take_profit" if i % 2 == 0 else "stop_loss",
        })
    # GFT : 5 mfe_zero losers (pattern bug)
    for i in range(5):
        rows.append({
            "ts": f"2026-05-08T{10+i:02d}:30:00+00:00",
            "event": "exit",
            "broker_id": "gft_compte1",
            "result_r": -1.0,
            "mfe_r": 0.0,
            "be_set": False,
            "exit_reason": "stop_loss",
        })
    # GFT : 5 trades sains
    for i in range(5):
        rows.append({
            "ts": f"2026-05-08T{15+i:02d}:00:00+00:00",
            "event": "exit",
            "broker_id": "gft_compte1",
            "result_r": 1.0,
            "mfe_r": 1.2,
            "be_set": True,
            "exit_reason": "take_profit",
        })
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


def test_per_broker_isolates_gft_bug(tmp_path):
    """Le verdict global doit être CRITIQUE car GFT a 5 mfe_zero_loser,
    même si FTMO est propre."""
    journal = _fake_journal(tmp_path)
    import datetime as dt
    since = dt.datetime(2026, 5, 8, tzinfo=dt.timezone.utc)
    until = dt.datetime(2026, 5, 9, tzinfo=dt.timezone.utc)

    with patch.object(check_inv, "JOURNAL", journal):
        ftmo_ex = check_inv._load_exits(since, until, broker="ftmo_challenge")
        gft_ex = check_inv._load_exits(since, until, broker="gft_compte1")
        all_ex = check_inv._load_exits(since, until, broker=None)

    assert len(ftmo_ex) == 10
    assert len(gft_ex) == 10
    assert len(all_ex) == 20

    ftmo_report = check_inv._evaluate(ftmo_ex)
    gft_report = check_inv._evaluate(gft_ex)
    global_report = check_inv._evaluate(all_ex)

    # FTMO clean
    assert ftmo_report["verdict"] == "ok"
    # GFT critique : 5 mfe_zero_loser → trigger CRITIQUE
    assert gft_report["verdict"] == "critique"
    triggers = [t[0] for t in gft_report["triggers"]]
    assert "mfe_zero_loser" in triggers
    # Verdict global aussi critique (un seul mauvais broker suffit)
    assert global_report["verdict"] == "critique"


def test_load_exits_no_broker_filter_returns_all(tmp_path):
    """Sans --broker, on récupère tous les exits."""
    journal = _fake_journal(tmp_path)
    import datetime as dt
    since = dt.datetime(2026, 5, 8, tzinfo=dt.timezone.utc)
    until = dt.datetime(2026, 5, 9, tzinfo=dt.timezone.utc)

    with patch.object(check_inv, "JOURNAL", journal):
        all_ex = check_inv._load_exits(since, until)
    assert len(all_ex) == 20


def test_verdict_rank_ordering():
    """ok < alert < critique pour pouvoir prendre le max."""
    assert check_inv._verdict_rank("ok") < check_inv._verdict_rank("alert")
    assert check_inv._verdict_rank("alert") < check_inv._verdict_rank("critique")
