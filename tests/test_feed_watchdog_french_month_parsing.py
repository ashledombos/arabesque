"""Régression 2026-06-01 — parsing journalctl mois français multi-lettres.

Bug : `BAR_PATTERN` et `PRICEFEED_SUMMARY_PATTERN` utilisaient `(\\w{3})`
qui ne matche que 3 caractères avant l'espace. Or les sorties `journalctl`
sur locale fr_FR utilisent `juin`/`juil`/`août`/`mars`/`sept` (4 lettres),
qui ne matchaient pas. Côté `MONTH_MAP`, le `month_str.lower()[:3]` faisait
collisionner `juin` et `juil` sur la même clé `jui` → mois faux pour juillet.

Symptôme prod (incident 2026-06-01 ~05:25 UTC) : `_last_bar_age_seconds`
retourne `None` (aucune ligne `BarAggregator` parsée pile au passage en
juin) → faux positif `no_bar_data_in_window` → notif Telegram « pas de
barres » alors que l'engine est sain et émet 5 barres par minute.

Invariants verrouillés :
1. `BAR_PATTERN` matche tous les noms de mois français (3 ou 4 lettres).
2. `MONTH_MAP` mappe correctement `juin` ≠ `juil` (pas de collision).
3. `_last_bar_age_seconds` retourne un âge cohérent pour un échantillon
   `juin 01 ...` (régression directe).
"""
from __future__ import annotations

import datetime as dt
import importlib
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def watchdog(tmp_path, monkeypatch):
    import scripts.feed_watchdog as wd
    importlib.reload(wd)
    monkeypatch.setattr(wd, "STATE", tmp_path / "feed_watchdog_state.json")
    monkeypatch.setattr(wd, "RESTART_HISTORY", tmp_path / "restart_history.jsonl")
    monkeypatch.setattr(wd, "SECRETS", tmp_path / "secrets.yaml")
    monkeypatch.setattr(wd, "POSITIONS_STATE", tmp_path / "position_monitor_state.json")
    return wd


@pytest.mark.parametrize("month_str,expected_month", [
    ("jan", 1), ("janv", 1),
    ("fév", 2), ("févr", 2),
    ("mar", 3), ("mars", 3),
    ("avr", 4), ("avri", 4),
    ("mai", 5),
    ("jun", 6), ("juin", 6),
    ("jul", 7), ("juil", 7),
    ("aoû", 8), ("août", 8),
    ("sep", 9), ("sept", 9),
    ("oct", 10),
    ("nov", 11),
    ("déc", 12),
])
def test_month_map_distinguishes_juin_and_juil(watchdog, month_str, expected_month):
    """Pas de collision juin/juil ni régression sur les mois 3 lettres."""
    assert watchdog.MONTH_MAP.get(month_str.lower()) == expected_month


@pytest.mark.parametrize("month_str", ["mai", "juin", "juil", "août", "sept"])
def test_bar_pattern_matches_french_months(watchdog, month_str):
    """`BarAggregator Résumé` doit matcher quel que soit le mois français."""
    line = (
        f"{month_str} 01 07:25:00 host python[123]: "
        "2026-06-01 07:25:00 [INFO] arabesque.live.bar_aggregator: "
        "[BarAggregator] ✅ Résumé: 4 barre(s) fermée(s), 0 signal(s) émis"
    )
    m = watchdog.BAR_PATTERN.match(line)
    assert m is not None, f"BAR_PATTERN ne matche pas mois={month_str!r}"
    assert m.group(1).lower() == month_str.lower()


def test_last_bar_age_parses_juin_lines(watchdog):
    """Régression directe incident 2026-06-01 : 1 ligne juin → âge ≈ 60s.

    Le code interprète ``juin 01 HH:MM:SS`` comme heure locale du host
    (cf. ``_last_bar_age_seconds`` ligne 226). On construit donc le `now`
    en local, on ajoute 60s, puis on repasse en UTC pour appeler le helper.
    """
    wd = watchdog
    bar_local = dt.datetime(2026, 6, 1, 7, 25, 0).astimezone()  # tz local
    now_local = bar_local + dt.timedelta(seconds=60)
    fake_now_utc = now_local.astimezone(dt.timezone.utc)

    journal_output = (
        "juin 01 07:25:00 host python[123]: "
        "2026-06-01 07:25:00 [INFO] arabesque.live.bar_aggregator: "
        "[BarAggregator] ✅ Résumé: 4 barre(s) fermée(s), 0 signal(s) émis\n"
    )

    def fake_run(*args, **kwargs):
        r = MagicMock()
        r.stdout = journal_output
        r.returncode = 0
        return r

    with patch.object(wd.subprocess, "run", fake_run):
        age = wd._last_bar_age_seconds(fake_now_utc)

    assert age is not None, (
        "_last_bar_age_seconds doit parser 'juin ...' (regression 2026-06-01)"
    )
    assert 50 <= age <= 70, f"âge attendu ~60s, obtenu {age}s"
