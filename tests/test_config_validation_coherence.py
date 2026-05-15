"""Cohérence config live ↔ scripts de validation.

Garantit que :
- tous les instruments `follow: true` (config/instruments.yaml) résolvent un
  parquet au timeframe déclaré pour le live ;
- `_build_targets()` (replay_signals_vs_live.py) lit `timeframe` en priorité,
  `tf` en fallback legacy, sinon H1 — Extension crypto doit ressortir en H4 ;
- `resolve_tf()` (replay_live_vs_theory.py) idem pour le trade par trade ;
- tous les instruments de `strategy_assignments` (settings.yaml) résolvent un
  parquet au timeframe de la stratégie.

Référence : `docs/AUDIT_VALIDATION_PIPELINE_2026-05-15.md` finding #1.
Incident fondateur : Extension crypto déclarée `timeframe: H4` était rejouée
en H1 par les scripts d'audit car ils lisaient `tf` au lieu de `timeframe`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "config" / "settings.yaml"
INSTRUMENTS_PATH = ROOT / "config" / "instruments.yaml"

# Mapping timeframe live → suffixe parquet (cohérent avec arabesque.data.store)
_TF_TO_PARQUET = {"M1": "min1", "H1": "1h", "H4": "4h", "D1": "1d"}


def _load_script_as_module(script_name: str, module_alias: str):
    """Charge un script depuis scripts/ comme module."""
    path = ROOT / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(module_alias, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_alias] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def settings_cfg() -> dict:
    return yaml.safe_load(SETTINGS_PATH.read_text()) or {}


@pytest.fixture(scope="module")
def instruments_cfg() -> dict:
    return yaml.safe_load(INSTRUMENTS_PATH.read_text()) or {}


# ────────────────────────────────────────────────────────────────────────────
# 1. Toutes les instruments follow: true résolvent un parquet au TF live
# ────────────────────────────────────────────────────────────────────────────
def test_followed_instruments_have_parquet_at_live_timeframe(instruments_cfg):
    """Chaque follow: true doit avoir un parquet local au timeframe déclaré.

    Sans cela, l'engine charge des barres via fallback Yahoo (différent de la
    source de validation) ou échoue offline.
    """
    from arabesque.data.store import _find_parquet

    failures = []
    for inst, meta in instruments_cfg.items():
        if not isinstance(meta, dict) or not meta.get("follow"):
            continue
        tf_live = (meta.get("timeframe") or meta.get("tf") or "H1").upper()
        tf_pq = _TF_TO_PARQUET.get(tf_live, tf_live.lower())
        path = _find_parquet(inst, tf_pq)
        if path is None or not path.exists():
            failures.append(f"{inst} ({tf_live} → {tf_pq})")

    assert not failures, (
        f"{len(failures)} instrument(s) follow: true sans parquet local au "
        f"timeframe live : {failures}. Soit ajouter au _CCXT_MAP/store.py, "
        f"soit retirer follow: true tant que la donnée n'est pas disponible."
    )


# ────────────────────────────────────────────────────────────────────────────
# 2. _build_targets() lit `timeframe` (live) — pas `tf` (legacy)
# ────────────────────────────────────────────────────────────────────────────
def test_build_targets_reads_timeframe_for_extension_crypto(instruments_cfg, settings_cfg):
    """Extension sur crypto déclarée timeframe: H4 doit être rejouée en H4."""
    mod = _load_script_as_module("replay_signals_vs_live.py", "rsl_test")
    targets = mod._build_targets(settings_cfg, instruments_cfg)
    ext_targets = {(inst, tf) for strat, tf, inst in targets if strat == "extension"}
    expected_h4 = {
        inst for inst, meta in instruments_cfg.items()
        if isinstance(meta, dict) and meta.get("follow")
        and (meta.get("timeframe") or meta.get("tf") or "H1").upper() == "H4"
    }
    missing_h4 = expected_h4 - {inst for inst, tf in ext_targets if tf == "H4"}
    assert not missing_h4, (
        f"Instruments Extension déclarés H4 dans instruments.yaml mais rejoués "
        f"à un autre timeframe : {missing_h4}"
    )


def test_build_targets_tf_legacy_alias_still_works(settings_cfg):
    """Si un instrument déclare `tf:` (vieille convention), c'est utilisé en fallback."""
    mod = _load_script_as_module("replay_signals_vs_live.py", "rsl_test2")
    fake_cfg = {"FAKEUSD": {"follow": True, "tf": "H4"}}
    targets = mod._build_targets(settings_cfg, fake_cfg)
    ext = [t for t in targets if t[2] == "FAKEUSD"]
    assert ext and ext[0] == ("extension", "H4", "FAKEUSD"), (
        f"Alias legacy `tf` non honoré : {ext}"
    )


def test_build_targets_default_h1_when_no_timeframe(settings_cfg):
    """Si ni timeframe ni tf, le défaut est H1 (cohérent avec live.py l.1004)."""
    mod = _load_script_as_module("replay_signals_vs_live.py", "rsl_test3")
    fake_cfg = {"FAKEUSD": {"follow": True}}
    targets = mod._build_targets(settings_cfg, fake_cfg)
    ext = [t for t in targets if t[2] == "FAKEUSD"]
    assert ext and ext[0][1] == "H1", f"Défaut attendu H1, obtenu : {ext}"


# ────────────────────────────────────────────────────────────────────────────
# 3. resolve_tf() lit `timeframe` (live) — pas `tf` (legacy)
# ────────────────────────────────────────────────────────────────────────────
def test_resolve_tf_extension_crypto_returns_h4(instruments_cfg, settings_cfg):
    """Pour un trade Extension sur instrument déclaré H4, resolve_tf doit retourner H4."""
    mod = _load_script_as_module("replay_live_vs_theory.py", "rlt_test")
    h4_cryptos = [
        inst for inst, meta in instruments_cfg.items()
        if isinstance(meta, dict) and meta.get("follow")
        and (meta.get("timeframe") or meta.get("tf") or "H1").upper() == "H4"
    ]
    assert h4_cryptos, "Test inutile : aucune crypto déclarée H4 dans instruments.yaml"
    for inst in h4_cryptos[:5]:
        tf = mod.resolve_tf("extension", inst, settings_cfg, instruments_cfg)
        assert tf == "H4", (
            f"Extension {inst} attendu H4, resolve_tf retourne {tf} — bug timeframe"
        )


def test_resolve_tf_strategy_assignment_overrides_instrument(settings_cfg, instruments_cfg):
    """Pour les stratégies non-extension, strategy_assignments prime."""
    mod = _load_script_as_module("replay_live_vs_theory.py", "rlt_test2")
    sa = settings_cfg.get("strategy_assignments", {}) or {}
    for strat, cfg in sa.items():
        if not isinstance(cfg, dict):
            continue
        tf_expected = cfg.get("timeframe", "H1").upper()
        for inst in (cfg.get("instruments") or [])[:3]:
            tf = mod.resolve_tf(strat, inst, settings_cfg, instruments_cfg)
            assert tf == tf_expected, (
                f"{strat} {inst} : strategy_assignments dit {tf_expected}, "
                f"resolve_tf retourne {tf}"
            )


# ────────────────────────────────────────────────────────────────────────────
# 4. strategy_assignments → instruments résolvent un parquet
# ────────────────────────────────────────────────────────────────────────────
def test_strategy_assignments_instruments_have_parquet(settings_cfg):
    """Tous les instruments des stratégies assignées doivent avoir un parquet."""
    from arabesque.data.store import _find_parquet

    sa = settings_cfg.get("strategy_assignments", {}) or {}
    failures = []
    for strat, cfg in sa.items():
        if not isinstance(cfg, dict):
            continue
        tf_live = cfg.get("timeframe", "H1").upper()
        tf_pq = _TF_TO_PARQUET.get(tf_live, tf_live.lower())
        for inst in cfg.get("instruments") or []:
            path = _find_parquet(inst, tf_pq)
            if path is None or not path.exists():
                failures.append(f"{strat}/{inst} ({tf_live} → {tf_pq})")
    assert not failures, (
        f"{len(failures)} instrument(s) strategy_assignments sans parquet : {failures}"
    )
