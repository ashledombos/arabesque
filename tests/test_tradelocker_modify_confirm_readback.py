"""TradeLocker modify_position : confirmer par relecture quand le SDK ment.

Incident 2026-06-18 : le SDK TradeLocker renvoie une valeur falsy pour
``modify_position`` ALORS QUE la modification s'applique (le SL bouge au BE).
L'ancien code (``if result: ... else: échec``) concluait à l'échec → l'armement
BE n'était jamais enregistré (be_set=False, exit_reason=stop_loss), plus une
fausse alerte ``amend_abandoned``, alors que 4 trades GFT sont réellement sortis
au niveau BE (+0.20R). Preuve : SL initial loin (côté -1R), fill réel = niveau BE.

Fix : si le SDK renvoie falsy, relire le SL effectif (ordres protecteurs liés) ;
s'il matche la cible → succès. Sinon → échec réel (SL inchangé).
"""
from __future__ import annotations

import asyncio

import pandas as pd

from arabesque.broker.tradelocker import TradeLockerBroker


class _Api:
    def __init__(self, *, modify_result, orders):
        self._modify_result = modify_result
        self._orders = orders
        self.modify_calls = []

    def modify_position(self, position_id, params):
        self.modify_calls.append((position_id, params))
        return self._modify_result

    def get_all_orders(self, history=True):
        return self._orders


def _broker(api: _Api) -> TradeLockerBroker:
    b = TradeLockerBroker.__new__(TradeLockerBroker)
    b._api = api
    b._instruments_reverse_map = {}
    b.broker_id = "gft_compte1"
    return b


def _orders_with_sl(position_id: int, sl: float) -> pd.DataFrame:
    return pd.DataFrame([{
        "positionId": position_id,
        "status": "PENDING",
        "type": "stop",
        "stopPrice": sl,
        "price": sl,
    }])


def test_falsy_return_but_sl_applied_is_success():
    """SDK renvoie None MAIS le SL a bougé à la cible → succès confirmé."""
    pid = 504403158265920174
    target = 70866.5
    api = _Api(modify_result=None, orders=_orders_with_sl(pid, target))
    broker = _broker(api)

    res = asyncio.run(broker.modify_position(str(pid), stop_loss=target))
    assert res.success is True
    assert "relecture" in res.message


def test_falsy_return_and_sl_unchanged_is_failure():
    """SDK renvoie None ET le SL est resté à l'ancien niveau → échec réel."""
    pid = 504403158265920174
    target = 70866.5
    old_sl = 72043.6  # côté -1R, ~1.6% loin de la cible → hors tolérance
    api = _Api(modify_result=None, orders=_orders_with_sl(pid, old_sl))
    broker = _broker(api)

    res = asyncio.run(broker.modify_position(str(pid), stop_loss=target))
    assert res.success is False


def test_truthy_return_is_success_without_readback():
    """Chemin nominal : SDK renvoie une valeur truthy → succès immédiat."""
    api = _Api(modify_result={"ok": True}, orders=_orders_with_sl(1, 0.0))
    broker = _broker(api)

    res = asyncio.run(broker.modify_position("1", stop_loss=100.0))
    assert res.success is True
    assert "relecture" not in res.message
