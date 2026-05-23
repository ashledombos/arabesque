"""Réduction du bruit weekend dans `_run_loop` du PriceFeedManager (task #34).

Incident fondateur : 2026-05-22 21:00 UTC → 2026-05-23 (en cours), 4200+ lignes
WARNING/ERROR produites par la cascade de retry weekend après que ETHUSD est
devenu stale à 21:00 UTC vendredi (juste avant la fermeture officielle 22:00
UTC). À chaque tentative de reconnexion (toutes les 120s en steady-state),
3 lignes hurlent : `force reconnect after stale feed`, `Erreur: ...`,
`Reconnexion dans 120s`. Ce bruit masque les vrais signaux dans `journalctl`
(ex: les 8h d'`ALREADY_LOGGED_IN` du 13/05 auraient été noyées).

Approche minimale (pas de changement du comportement métier) :
  1. Helper statique `_is_market_likely_closed(now)` — élargit la fenêtre
     weekend à vendredi 21:00 UTC (au lieu de 22:00) pour couvrir la
     transition crypto/forex avant fermeture officielle.
  2. Pendant cette fenêtre : délai fixe long (300s au lieu d'exponentiel
     5→120s) ET logs INFO au lieu de WARNING/ERROR.
  3. Hors fenêtre : comportement inchangé (régression test).

Invariants verrouillés :
  - Fenêtre stricte : vendredi 21:00 UTC → dimanche 22:00 UTC.
  - Délai weekend ≥ 300s, jamais exponentiel.
  - Logs niveau INFO en weekend (n'apparaissent pas en grep "WARNING").
  - Hors weekend : exponentiel 5→120s préservé.
"""
from __future__ import annotations

import datetime as dt
import logging

import pytest

from arabesque.execution.price_feed import PriceFeedManager


# ---------------------------------------------------------------------------
# _is_market_likely_closed — fenêtre vendredi 21:00 UTC → dimanche 22:00 UTC
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("now_iso,expected", [
    # Vendredi (weekday=4) — fermeture progressive crypto/forex
    ("2026-05-22T20:59:00+00:00", False),  # juste avant fenêtre
    ("2026-05-22T21:00:00+00:00", True),   # début fenêtre élargie
    ("2026-05-22T21:30:00+00:00", True),
    ("2026-05-22T22:00:00+00:00", True),
    ("2026-05-22T23:59:00+00:00", True),
    # Samedi (weekday=5) — toute la journée
    ("2026-05-23T00:00:00+00:00", True),
    ("2026-05-23T12:00:00+00:00", True),
    ("2026-05-23T23:59:00+00:00", True),
    # Dimanche (weekday=6) — jusqu'à 22:00 UTC (réouverture)
    ("2026-05-24T00:00:00+00:00", True),
    ("2026-05-24T21:59:00+00:00", True),
    ("2026-05-24T22:00:00+00:00", False),  # marché rouvert
    ("2026-05-24T23:00:00+00:00", False),
    # Lundi → jeudi : jamais fermé
    ("2026-05-25T03:00:00+00:00", False),
    ("2026-05-25T12:00:00+00:00", False),
    ("2026-05-28T20:00:00+00:00", False),
    # Vendredi matin/après-midi : avant fenêtre
    ("2026-05-22T08:00:00+00:00", False),
    ("2026-05-22T20:00:00+00:00", False),
])
def test_is_market_likely_closed(now_iso, expected):
    now = dt.datetime.fromisoformat(now_iso)
    assert PriceFeedManager._is_market_likely_closed(now) is expected


# ---------------------------------------------------------------------------
# Délai de reconnexion : weekend → fixe 300s, sinon exponentiel
# ---------------------------------------------------------------------------

def test_next_reconnect_delay_weekend_clamps_to_floor():
    """Pendant la fenêtre weekend, le délai est forcé à ≥ 300s.

    Reason : pas la peine de retry toutes les 5s/10s/120s pendant 47h quand
    cTrader refuse les nouvelles connexions. 300s = 12 tentatives/h.
    """
    # current_delay petit (initial 5s) → clamped à 300s en weekend
    assert PriceFeedManager._next_reconnect_delay(
        current_delay=5.0, is_weekend=True
    ) == 300.0
    # current_delay déjà grand → reste à 300s (pas plus haut)
    assert PriceFeedManager._next_reconnect_delay(
        current_delay=120.0, is_weekend=True
    ) == 300.0
    # Pas d'escalade au-delà de 300s en weekend
    assert PriceFeedManager._next_reconnect_delay(
        current_delay=300.0, is_weekend=True
    ) == 300.0


def test_next_reconnect_delay_normal_uses_exponential_backoff():
    """Hors weekend, le backoff exponentiel doit être préservé (5 → 120s)."""
    assert PriceFeedManager._next_reconnect_delay(
        current_delay=5.0, is_weekend=False
    ) == 10.0
    assert PriceFeedManager._next_reconnect_delay(
        current_delay=10.0, is_weekend=False
    ) == 20.0
    assert PriceFeedManager._next_reconnect_delay(
        current_delay=60.0, is_weekend=False
    ) == 120.0
    # Cap à 120s
    assert PriceFeedManager._next_reconnect_delay(
        current_delay=120.0, is_weekend=False
    ) == 120.0


# ---------------------------------------------------------------------------
# Niveau de log : weekend → INFO, sinon WARNING (pour _run_loop & force reconnect)
# ---------------------------------------------------------------------------

def test_log_level_for_reconnect_is_info_during_weekend():
    """En weekend, les retry logs doivent passer en INFO pour ne pas polluer
    journalctl en grep WARNING/ERROR (incident 4200+ lignes 22-23/05/2026).
    """
    assert PriceFeedManager._reconnect_log_level(is_weekend=True) == logging.INFO


def test_log_level_for_reconnect_is_warning_outside_weekend():
    """Hors weekend, on garde WARNING pour que les vrais incidents soient
    captés par les outils de monitoring qui filtrent sur WARNING+.
    """
    assert PriceFeedManager._reconnect_log_level(is_weekend=False) == logging.WARNING


# ---------------------------------------------------------------------------
# Bug guard : `_is_market_likely_closed` accepte naive ou aware datetime
# ---------------------------------------------------------------------------

def test_is_market_likely_closed_accepts_naive_datetime():
    """L'helper doit fonctionner aussi avec un datetime naive (le weekday/hour
    sont les mêmes peu importe la tzinfo). Évite un crash si l'appelant
    oublie tzinfo=UTC.
    """
    naive_saturday = dt.datetime(2026, 5, 23, 12, 0, 0)
    assert PriceFeedManager._is_market_likely_closed(naive_saturday) is True
