"""Task #40 patch #2 — timeout sur ``subprocess.run`` pour ``_engine_active``
et ``_last_bar_age_seconds``.

Bug avant patch : les deux helpers lançaient ``systemctl`` / ``journalctl``
sans ``timeout=``. Si systemd (cas OOM, journal corrompu, dbus bloqué) ou
journalctl (journaux énormes, mmap lent) freezent, le cycle watchdog se bloque
indéfiniment → plus aucune surveillance, plus aucune notif. Le timer
systemd qui relance le watchdog toutes les 60s s'empile en parallèle.

Patch : ajouter ``timeout=5`` sur ``_engine_active`` (systemctl is-active
doit répondre quasi-instantanément) et ``timeout=10`` sur
``_last_bar_age_seconds`` (journalctl --since "30 minutes ago" peut prendre
quelques secondes sur un journal chargé).

En cas de ``subprocess.TimeoutExpired`` : log WARNING (stderr) + fallback
sûr : ``_engine_active → False`` (sera traité comme engine_inactive, branche
qui ne fait rien de catastrophique), ``_last_bar_age_seconds → None`` (sera
traité comme ``no_bar_data_in_window``, notif normale sans auto-restart).

Invariants verrouillés :
  1. ``_engine_active`` passe ``timeout=`` à subprocess.run.
  2. ``_engine_active`` retourne ``False`` si TimeoutExpired (et n'explose pas).
  3. ``_last_bar_age_seconds`` passe ``timeout=`` à subprocess.run.
  4. ``_last_bar_age_seconds`` retourne ``None`` si TimeoutExpired.
"""
from __future__ import annotations

import datetime as dt
import importlib
import subprocess
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def watchdog(tmp_path, monkeypatch):
    import scripts.feed_watchdog as wd
    importlib.reload(wd)
    monkeypatch.setattr(wd, "STATE", tmp_path / "feed_watchdog_state.json")
    monkeypatch.setattr(wd, "RESTART_HISTORY", tmp_path / "restart_history.jsonl")
    monkeypatch.setattr(wd, "SECRETS", tmp_path / "secrets.yaml")
    monkeypatch.setattr(wd, "POSITIONS_STATE", tmp_path / "position_monitor_state.json")
    return wd, tmp_path


# ---------------------------------------------------------------------------
# Invariant 1 — _engine_active passe timeout=
# ---------------------------------------------------------------------------

def test_engine_active_passes_timeout_to_subprocess(watchdog):
    wd, _ = watchdog
    captured = {}

    def fake_run(cmd, capture_output=False, text=False, timeout=None, **kwargs):
        captured["timeout"] = timeout
        captured["cmd"] = cmd
        r = MagicMock()
        r.stdout = "active"
        r.returncode = 0
        return r

    with patch.object(wd.subprocess, "run", fake_run):
        wd._engine_active()

    assert captured["timeout"] is not None, (
        "_engine_active doit passer timeout= a subprocess.run"
    )
    assert captured["timeout"] <= 10, (
        f"timeout {captured['timeout']}s trop large pour systemctl is-active"
    )


# ---------------------------------------------------------------------------
# Invariant 2 — _engine_active fail-safe sur TimeoutExpired
# ---------------------------------------------------------------------------

def test_engine_active_returns_false_on_timeout(watchdog):
    wd, _ = watchdog

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=5)

    with patch.object(wd.subprocess, "run", fake_run):
        result = wd._engine_active()

    assert result is False, (
        "_engine_active doit retourner False sur TimeoutExpired (fail-safe : "
        "traite comme engine_inactive, branche sans danger)"
    )


def test_engine_active_does_not_raise_on_timeout(watchdog):
    """Sanity : TimeoutExpired ne doit jamais remonter au caller (sinon le
    cycle watchdog crash et plus aucune surveillance jusqu'au prochain timer)."""
    wd, _ = watchdog

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=5)

    with patch.object(wd.subprocess, "run", fake_run):
        # Ne doit pas lever
        wd._engine_active()


# ---------------------------------------------------------------------------
# Invariant 3 — _last_bar_age_seconds passe timeout=
# ---------------------------------------------------------------------------

def test_last_bar_age_passes_timeout_to_subprocess(watchdog):
    wd, _ = watchdog
    captured = {}

    def fake_run(cmd, capture_output=False, text=False, timeout=None, **kwargs):
        captured["timeout"] = timeout
        r = MagicMock()
        r.stdout = ""
        r.returncode = 0
        return r

    now = dt.datetime.now(dt.timezone.utc)
    with patch.object(wd.subprocess, "run", fake_run):
        wd._last_bar_age_seconds(now)

    assert captured["timeout"] is not None, (
        "_last_bar_age_seconds doit passer timeout= a subprocess.run"
    )
    assert captured["timeout"] <= 30, (
        f"timeout {captured['timeout']}s trop large (journalctl 30min "
        "devrait repondre en quelques secondes)"
    )


# ---------------------------------------------------------------------------
# Invariant 4 — _last_bar_age_seconds fail-safe sur TimeoutExpired
# ---------------------------------------------------------------------------

def test_last_bar_age_returns_none_on_timeout(watchdog):
    wd, _ = watchdog

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=10)

    now = dt.datetime.now(dt.timezone.utc)
    with patch.object(wd.subprocess, "run", fake_run):
        result = wd._last_bar_age_seconds(now)

    assert result is None, (
        "_last_bar_age_seconds doit retourner None sur TimeoutExpired "
        "(fail-safe : traite comme no_bar_data_in_window, notif normale "
        "sans auto-restart)"
    )


def test_last_bar_age_does_not_raise_on_timeout(watchdog):
    wd, _ = watchdog

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=10)

    now = dt.datetime.now(dt.timezone.utc)
    with patch.object(wd.subprocess, "run", fake_run):
        # Ne doit pas lever
        wd._last_bar_age_seconds(now)
