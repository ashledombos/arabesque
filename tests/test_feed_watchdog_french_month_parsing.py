"""Régression parsing journalctl — immunité à la locale (mois).

Historique : `BAR_PATTERN`/`PRICEFEED_SUMMARY_PATTERN` parsaient le nom de mois
français de la sortie `journalctl` par défaut. Ça a cassé DEUX fois pile au
changement de mois :
  - 2026-06-01 : `\\w{3}` ne matchait pas `juin` (4 lettres).
  - 2026-07-01 : `\\w{3,4}\\s+` ne matchait pas `juil.`/`sept.`/`déc.` (point)
    ni `avril` (5 lettres) → `_last_bar_age_seconds` retournait `None` →
    faux `no_bar_data_in_window` → spam Telegram « feed mort » alors que
    l'engine émettait 5 barres/minute.

Correctif définitif : `journalctl -o short-iso` (horodatage ISO, indépendant
de la langue) + parsing via `dt.datetime.fromisoformat`. Ces tests verrouillent
le nouveau contrat : le parser doit lire les lignes ISO **quel que soit le
mois** (y compris juin/juillet/septembre/décembre, historiquement piégeux).
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


def _iso_bar_line(iso_prefix: str) -> str:
    """Ligne `journalctl -o short-iso` d'un BarAggregator Résumé."""
    return (
        f"{iso_prefix} host python[123]: "
        "[INFO] arabesque.live.bar_aggregator: "
        "[BarAggregator] ✅ Résumé: 4 barre(s) fermée(s), 0 signal(s) émis"
    )


# Mois historiquement piégeux (juin/juil sans collision possible en ISO) +
# offsets avec et sans deux-points (journalctl peut produire les deux).
@pytest.mark.parametrize("iso_prefix", [
    "2026-01-15T07:25:00+01:00",
    "2026-06-01T07:25:00+02:00",
    "2026-07-01T00:31:02+02:00",
    "2026-07-01T00:31:02+0200",   # offset sans deux-points
    "2026-09-30T23:59:00+02:00",
    "2026-12-25T12:00:00+01:00",
])
def test_bar_pattern_matches_iso_any_month(watchdog, iso_prefix):
    """`BarAggregator Résumé` doit matcher quel que soit le mois (ISO)."""
    m = watchdog.BAR_PATTERN.match(_iso_bar_line(iso_prefix))
    assert m is not None, f"BAR_PATTERN ne matche pas ISO={iso_prefix!r}"
    assert m.group(1) == iso_prefix


def test_last_bar_age_parses_iso_lines(watchdog):
    """Régression : 1 ligne ISO récente → âge cohérent, peu importe le mois."""
    wd = watchdog
    bar_ts = dt.datetime(2026, 7, 1, 0, 31, 2, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    fake_now_utc = bar_ts.astimezone(dt.timezone.utc) + dt.timedelta(seconds=60)
    journal_output = _iso_bar_line("2026-07-01T00:31:02+02:00") + "\n"

    def fake_run(*args, **kwargs):
        r = MagicMock()
        r.stdout = journal_output
        r.returncode = 0
        return r

    with patch.object(wd.subprocess, "run", fake_run):
        age = wd._last_bar_age_seconds(fake_now_utc)

    assert age is not None, "_last_bar_age_seconds doit parser une ligne ISO"
    assert 50 <= age <= 70, f"âge attendu ~60s, obtenu {age}s"


def test_last_bar_age_takes_latest_across_month_boundary(watchdog):
    """Plusieurs lignes ISO chevauchant un changement de mois → la plus récente."""
    wd = watchdog
    fake_now_utc = dt.datetime(2026, 7, 1, 0, 0, 30, tzinfo=dt.timezone.utc)
    journal_output = (
        _iso_bar_line("2026-06-30T23:58:00+00:00") + "\n"
        + _iso_bar_line("2026-07-01T00:00:00+00:00") + "\n"
    )

    def fake_run(*args, **kwargs):
        r = MagicMock()
        r.stdout = journal_output
        r.returncode = 0
        return r

    with patch.object(wd.subprocess, "run", fake_run):
        age = wd._last_bar_age_seconds(fake_now_utc)

    assert age == 30, f"doit prendre la barre 00:00 (la plus récente), âge={age}s"


def test_journalctl_invoked_with_short_iso(watchdog):
    """Le fix repose sur `-o short-iso` : vérifier qu'il est bien passé."""
    wd = watchdog
    captured = {}

    def fake_run(args, *a, **kw):
        captured["args"] = args
        r = MagicMock()
        r.stdout = ""
        r.returncode = 0
        return r

    with patch.object(wd.subprocess, "run", fake_run):
        wd._last_bar_age_seconds(dt.datetime.now(dt.timezone.utc))

    assert "short-iso" in captured["args"], "journalctl doit être appelé en -o short-iso"
