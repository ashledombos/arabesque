"""Task #40 patch #4 — ``_write_state`` atomique via ``os.replace``.

Bug latent avant patch : ``STATE.write_text(json.dumps(state))`` n'est pas
atomique. Si le watchdog est tué (SIGKILL, OOM, panique kernel) ou si le
disque est plein au milieu de l'écriture, on se retrouve avec un JSON
tronqué. Au prochain ``_read_state``, ``json.loads`` lève → le ``except``
silencieux retourne ``{}``. On perd ``last_alert_ts`` (→ spam de notifs
au prochain feed_stale), ``feed_stale_since_ts`` (→ reset du tracker
persistance, l'auto-restart Étage 3 reporté de 30 min), ``positions_state_corrupted``
(perte du flag de corruption).

Patch : pattern atomique standard via ``os.replace`` (atomique sur POSIX
quand source et destination sont sur le même filesystem).

Invariants verrouillés :
  1. ``_write_state`` écrit d'abord dans un fichier temporaire puis
     ``os.replace(tmp, STATE)``.
  2. Si l'écriture du tmp échoue (ex: disque plein), ``STATE`` original
     reste intact (pas de fichier tronqué qui remplacerait le bon).
  3. Le contenu final dans ``STATE`` après un write réussi est identique
     à ``json.dumps(state, indent=2)``.
  4. Pas de fichier ``.tmp`` orphelin laissé sur disque après un succès.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def watchdog(tmp_path, monkeypatch):
    import scripts.feed_watchdog as wd
    importlib.reload(wd)
    monkeypatch.setattr(wd, "STATE", tmp_path / "feed_watchdog_state.json")
    return wd, tmp_path


# ---------------------------------------------------------------------------
# Invariant 1 — pattern tmp + os.replace
# ---------------------------------------------------------------------------

def test_write_state_uses_os_replace(watchdog):
    """``_write_state`` doit appeler ``os.replace`` (atomique POSIX) plutôt
    que ``Path.write_text`` direct sur ``STATE``."""
    wd, _ = watchdog
    called = {"os_replace": 0, "src": None, "dst": None}

    real_os_replace = wd.os.replace

    def spy_replace(src, dst):
        called["os_replace"] += 1
        called["src"] = str(src)
        called["dst"] = str(dst)
        return real_os_replace(src, dst)

    with patch.object(wd.os, "replace", spy_replace):
        wd._write_state({"foo": "bar"})

    assert called["os_replace"] == 1, "os.replace doit etre appele exactement 1 fois"
    assert called["dst"] == str(wd.STATE), "destination doit etre STATE"
    assert called["src"] != str(wd.STATE), "source doit etre un tmp file != STATE"


# ---------------------------------------------------------------------------
# Invariant 2 — pas de partial write si l'écriture du tmp échoue
# ---------------------------------------------------------------------------

def test_write_state_preserves_original_on_tmp_write_failure(watchdog):
    """Si l'écriture du tmp échoue (ex: disque plein simulé), le STATE
    original DOIT rester intact. Sans le pattern atomique, write_text direct
    sur STATE le tronquerait."""
    wd, _ = watchdog
    # State initial sain
    wd._write_state({"initial": "ok", "last_alert_ts": "2026-05-23T12:00:00+00:00"})
    original = wd.STATE.read_text()

    # Simuler échec d'écriture du tmp
    real_write_text = Path.write_text

    def fake_write_text(self, *args, **kwargs):
        if self.name.endswith(".tmp"):
            raise OSError("simulated disk full")
        return real_write_text(self, *args, **kwargs)

    with patch.object(Path, "write_text", fake_write_text):
        with pytest.raises(OSError):
            wd._write_state({"new": "data"})

    # STATE doit toujours contenir l'original (pas tronqué, pas écrasé)
    assert wd.STATE.read_text() == original, (
        "STATE original doit rester intact si l'ecriture du tmp echoue "
        "(pattern atomique = on ne touche jamais directement au fichier final)"
    )


# ---------------------------------------------------------------------------
# Invariant 3 — contenu final correct après write réussi
# ---------------------------------------------------------------------------

def test_write_state_writes_correct_json(watchdog):
    wd, _ = watchdog
    payload = {
        "last_check_ts": "2026-05-23T17:56:00+00:00",
        "last_status": "ok:age=30s",
        "open_positions_count": 2,
    }
    wd._write_state(payload)

    assert wd.STATE.exists()
    assert json.loads(wd.STATE.read_text()) == payload


# ---------------------------------------------------------------------------
# Invariant 4 — pas de tmp orphelin
# ---------------------------------------------------------------------------

def test_write_state_cleans_up_tmp_on_success(watchdog):
    """Après un write réussi, il ne doit pas y avoir de fichier ``.tmp``
    orphelin (os.replace renomme atomiquement le tmp en STATE)."""
    wd, tmp_path = watchdog
    wd._write_state({"foo": "bar"})

    tmp_files = list(wd.STATE.parent.glob("*.tmp"))
    assert tmp_files == [], (
        f"Tmp file orphelin apres write reussi : {tmp_files}"
    )


# ---------------------------------------------------------------------------
# Invariant non-régression — roundtrip read/write inchangé
# ---------------------------------------------------------------------------

def test_read_write_roundtrip(watchdog):
    wd, _ = watchdog
    payload = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
    wd._write_state(payload)
    assert wd._read_state() == payload
