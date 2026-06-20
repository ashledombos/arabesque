"""be_unarmed_ratio ne doit PAS compter les aller-retours marché non-armables.

Incident NERUSD 2026-06-15 : -1R, MFE 0.32R, mais 426 polls
`price_fell_back_before_amend` → le prix a effleuré 0.3R puis s'est effondré sous
l'offset BE ; le BE était PHYSIQUEMENT non-armable (on ne pose pas un stop au-dessus
du marché). Ce n'est pas un bug d'exécution → l'invariant ne doit pas le flagger.
On ne compte que les non-armés ARMABLES (raison be_polling = exécution, ou pas de
donnée poll = conservateur).
"""
from importlib import import_module

cei = import_module("scripts.check_execution_invariants")


def _loser(pid, mfe=0.4, r=-1.0):
    return {"result_r": r, "mfe_r": mfe, "be_set": False,
            "be_source": "not_armed", "position_id": pid,
            "exit_reason": "stop_loss"}


def test_market_roundtrip_excluded_execution_kept():
    exits = [_loser("P1"), _loser("P2"), _loser("P3")]
    be_reasons = {"P1": "market", "P2": "execution"}  # P3 absent = pas de poll
    rep = cei._evaluate(exits, be_reasons)
    d = rep["details"]["be_unarmed"]
    # P1 (price_fell_back) exclu ; P2 (exécution) + P3 (conservateur) comptés.
    assert d["count"] == 2
    assert d["market_roundtrip_excluded"] == 1
    assert d["of_full_losers"] == 3


def test_all_market_roundtrips_no_bug():
    exits = [_loser("P1"), _loser("P2")]
    be_reasons = {"P1": "market", "P2": "market"}
    rep = cei._evaluate(exits, be_reasons)
    d = rep["details"]["be_unarmed"]
    assert d["count"] == 0
    assert d["market_roundtrip_excluded"] == 2


def test_no_polling_data_stays_conservative():
    """Records pré-be_polling (pas de raison) → comptés comme avant (faux négatif évité)."""
    exits = [_loser("P1"), _loser("P2")]
    rep = cei._evaluate(exits, {})  # aucune raison connue
    d = rep["details"]["be_unarmed"]
    assert d["count"] == 2
    assert d["market_roundtrip_excluded"] == 0


def test_armed_loser_not_counted():
    """Un loser dont le BE a été armé broker ne compte pas (be_source=broker_armed)."""
    e = _loser("P1")
    e["be_source"] = "broker_armed"
    rep = cei._evaluate([e], {"P1": "execution"})
    assert rep["details"]["be_unarmed"]["count"] == 0
