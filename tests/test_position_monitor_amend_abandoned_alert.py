"""Étage 0 résilience broker (2026-05-21) — notification Telegram+ntfy quand
un amend SL est abandonné après ``max_amend_retries`` échecs consécutifs.

Incident fondateur : 2026-05-20T22:00 → 2026-05-21T01:42 UTC. DASHUSD #53110148
amend BE abandonné silencieusement. Aucune alerte malgré 10h de log
``[Monitor] ⚠️ SL amend ABANDONED after 3 attempts: ...``. Le patch Étage 0
ajoute un callback ``on_amend_abandoned`` déclenché à chaque ABANDONED, avec
un cooldown 30 min par position pour éviter le spam quand le canal reste mort.

Invariants verrouillés :
  1. Amend échoué (broker retourne success=False) → callback déclenché.
  2. Payload contient symbol, position_id, broker_id, target_sl, last_error,
     amend_failures, mfe_r, breakeven_set, trailing_tier.
  3. Cooldown : 2e ABANDONED < 30 min après la 1ère notif → pas de 2e notif.
  4. Cooldown levé après 30 min → nouvelle notif possible.
  5. ``last_amend_alert_time`` est par position (DASHUSD spam ne bloque pas
     les notifs pour BTCUSD).
  6. Exception dans la callback ne casse pas ``_try_amend_sl``.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from arabesque.broker.base import OrderResult
from arabesque.core.models import Side
from arabesque.execution.position_monitor import (
    LivePositionMonitor,
    MonitorConfig,
    TrackedPosition,
)


@dataclass
class _FakeBroker:
    """Broker stub qui retourne toujours OrderResult(success=False)."""
    error_message: str = "Not connected"
    amend_calls: list = None

    def __post_init__(self):
        if self.amend_calls is None:
            self.amend_calls = []

    async def amend_position_sltp(self, position_id, stop_loss=None, take_profit=None):
        self.amend_calls.append((position_id, stop_loss, take_profit))
        return OrderResult(success=False, message=self.error_message)

    async def get_quote(self, symbol):
        return None

    async def get_closed_position_detail(self, position_id):
        return None


def _build_pos(position_id="53110148", symbol="DASHUSD") -> TrackedPosition:
    return TrackedPosition(
        broker_id="ftmo_challenge",
        position_id=position_id,
        symbol=symbol,
        side=Side.LONG,
        entry=49.40,
        sl=46.70,
        sl_initial=46.70,
        tp=54.70,
        volume=0.0009,
        digits=2,
        max_favorable_price=54.33,  # MFE 1.82R
        breakeven_set=False,
        trailing_tier=3,
    )


def _build_monitor(callback, error_message="Not connected", max_retries=3):
    broker = _FakeBroker(error_message=error_message)
    cfg = MonitorConfig(
        max_amend_retries=max_retries,
        min_amend_interval_s=0.0,  # bypass anti-spam pour le test
        amend_alert_cooldown_s=1800.0,  # 30 min comme prod
    )
    monitor = LivePositionMonitor(
        brokers={"ftmo_challenge": broker},
        config=cfg,
        on_amend_abandoned=callback,
    )
    return monitor, broker


# ---------------------------------------------------------------------------
# 1. Amend échoué → callback déclenché avec payload complet
# ---------------------------------------------------------------------------

def test_amend_abandoned_triggers_callback():
    received = []

    def on_abandoned(payload):
        received.append(payload)

    monitor, broker = _build_monitor(on_abandoned)
    pos = _build_pos()
    monitor._positions[f"{pos.broker_id}:{pos.position_id}"] = pos

    # asyncio.sleep dans le backoff retry → on patch pour aller vite
    async def fast_sleep(_):
        pass

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "sleep", fast_sleep)
        result = asyncio.run(monitor._try_amend_sl(pos, new_sl=49.94, current_price=50.10))

    assert result is False
    # 3 attempts internes → max_retries atteint → ABANDONED
    assert len(broker.amend_calls) == 3
    assert len(received) == 1, f"Callback doit être appelé 1 fois, vu {len(received)}"

    payload = received[0]
    assert payload["symbol"] == "DASHUSD"
    assert payload["position_id"] == "53110148"
    assert payload["broker_id"] == "ftmo_challenge"
    assert payload["target_sl"] == 49.94
    assert payload["current_sl"] == 46.70
    assert payload["last_error"] == "Not connected"
    assert payload["amend_failures"] == 3
    assert payload["breakeven_set"] is False
    assert payload["trailing_tier"] == 3
    # MFE = (54.33 - 49.40) / (49.40 - 46.70) = 4.93 / 2.70 ≈ 1.826
    assert abs(payload["mfe_r"] - 1.826) < 0.01


# ---------------------------------------------------------------------------
# 2. Cooldown 30 min : 2e ABANDONED < 30 min ne déclenche pas la notif
# ---------------------------------------------------------------------------

def test_cooldown_blocks_second_alert_within_30min():
    received = []

    def on_abandoned(payload):
        received.append(payload)

    monitor, broker = _build_monitor(on_abandoned)
    pos = _build_pos()
    monitor._positions[f"{pos.broker_id}:{pos.position_id}"] = pos

    async def fast_sleep(_):
        pass

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "sleep", fast_sleep)
        # 1er ABANDONED
        asyncio.run(monitor._try_amend_sl(pos, new_sl=49.94, current_price=50.10))
        assert len(received) == 1

        # 2e ABANDONED juste après (même position)
        asyncio.run(monitor._try_amend_sl(pos, new_sl=52.44, current_price=54.00))

    # Cooldown : pas de 2e notif
    assert len(received) == 1, (
        f"Cooldown 30 min violé : 2e notif envoyée, vu {len(received)} notifs"
    )


def test_cooldown_released_after_30min():
    received = []

    def on_abandoned(payload):
        received.append(payload)

    monitor, broker = _build_monitor(on_abandoned)
    pos = _build_pos()
    monitor._positions[f"{pos.broker_id}:{pos.position_id}"] = pos

    async def fast_sleep(_):
        pass

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "sleep", fast_sleep)
        # 1er ABANDONED
        asyncio.run(monitor._try_amend_sl(pos, new_sl=49.94, current_price=50.10))
        assert len(received) == 1

        # Simuler 31 min plus tard
        pos.last_amend_alert_time = time.time() - 1860  # 31 min

        # 2e ABANDONED
        asyncio.run(monitor._try_amend_sl(pos, new_sl=52.44, current_price=54.00))

    assert len(received) == 2, (
        f"Cooldown levé → 2e notif doit partir, vu {len(received)} notifs"
    )


# ---------------------------------------------------------------------------
# 3. Cooldown indépendant par position
# ---------------------------------------------------------------------------

def test_cooldown_is_per_position():
    """DASHUSD spam ne doit pas bloquer une notif sur BTCUSD."""
    received = []

    def on_abandoned(payload):
        received.append(payload)

    monitor, broker = _build_monitor(on_abandoned)
    pos_dash = _build_pos(position_id="53110148", symbol="DASHUSD")
    pos_btc = _build_pos(position_id="53110200", symbol="BTCUSD")
    monitor._positions[f"{pos_dash.broker_id}:{pos_dash.position_id}"] = pos_dash
    monitor._positions[f"{pos_btc.broker_id}:{pos_btc.position_id}"] = pos_btc

    async def fast_sleep(_):
        pass

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "sleep", fast_sleep)
        # DASHUSD ABANDONED
        asyncio.run(monitor._try_amend_sl(pos_dash, new_sl=49.94, current_price=50.10))
        # BTCUSD ABANDONED juste après
        asyncio.run(monitor._try_amend_sl(pos_btc, new_sl=50.0, current_price=55.0))

    assert len(received) == 2, (
        f"Cooldown doit être par position, vu {len(received)} notifs (attendu 2)"
    )
    symbols = sorted(p["symbol"] for p in received)
    assert symbols == ["BTCUSD", "DASHUSD"]


# ---------------------------------------------------------------------------
# 4. Exception dans le callback ne casse pas _try_amend_sl
# ---------------------------------------------------------------------------

def test_callback_exception_does_not_break_amend():
    def broken_callback(payload):
        raise RuntimeError("notif service down")

    monitor, broker = _build_monitor(broken_callback)
    pos = _build_pos()
    monitor._positions[f"{pos.broker_id}:{pos.position_id}"] = pos

    async def fast_sleep(_):
        pass

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "sleep", fast_sleep)
        # Ne doit pas lever
        result = asyncio.run(monitor._try_amend_sl(pos, new_sl=49.94, current_price=50.10))

    assert result is False
    # last_amend_alert_time mis à jour AVANT l'appel (sinon cooldown ne marcherait
    # pas si le callback échoue). On vérifie que l'amend s'est terminé proprement.
    assert pos._amend_in_progress is False


# ---------------------------------------------------------------------------
# 5. Pas de callback → pas de crash (rétro-compatibilité)
# ---------------------------------------------------------------------------

def test_no_callback_does_not_crash():
    """Si on_amend_abandoned=None (cas legacy / tests), pas de crash."""
    broker = _FakeBroker()
    cfg = MonitorConfig(max_amend_retries=3, min_amend_interval_s=0.0)
    monitor = LivePositionMonitor(brokers={"ftmo_challenge": broker}, config=cfg)
    pos = _build_pos()
    monitor._positions[f"{pos.broker_id}:{pos.position_id}"] = pos

    async def fast_sleep(_):
        pass

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "sleep", fast_sleep)
        result = asyncio.run(monitor._try_amend_sl(pos, new_sl=49.94, current_price=50.10))

    assert result is False  # amend a bien échoué
    # Pas d'erreur levée — c'est l'invariant principal
