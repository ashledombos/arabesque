"""Patch 2026-05-22 — fallback disque sur ``ACCESS_DENIED`` après refresh HTTP.

Incident fondateur : 2026-05-21T22:59 → 2026-05-22T18:55 UTC. L'engine
PID 1150053 a échoué le refresh OAuth pendant 19h54 (607 tentatives) avec
``ACCESS_DENIED``, alors que les tokens disque (``config/secrets.yaml``)
étaient valides — preuve : la commande CLI ``python -m arabesque positions``
a réussi sur le même fichier pendant ce temps. Le ``refresh_token`` in-memory
du process engine était désynchronisé du disque (un processus externe avait
écrit des tokens frais entre-temps via une autre branche du code).

Patch :
  - ``_refresh_access_token`` détecte ``ACCESS_DENIED`` (response 200 +
    errorCode, ou status 400/401) et appelle ``_try_disk_token_fallback`` ;
  - ``_try_disk_token_fallback`` relit ``config/secrets.yaml`` via
    ``load_broker_tokens(broker_id)``. Si le ``refresh_token`` diffère
    de l'in-memory, on adopte et on signale au caller de retenter ;
  - le caller ``_refresh_access_token`` rappelle récursivement avec
    ``force_http=True, _disk_fallback_done=True`` → 1 seul retry max.

Invariants verrouillés :
  1. ACCESS_DENIED + disque diffère → adoption + retry HTTP unique réussit.
  2. ACCESS_DENIED + disque identique → return False sans retry (token vraiment mort).
  3. ACCESS_DENIED + secrets.yaml absent → return False sans retry.
  4. Non-ACCESS_DENIED (erreur 500, network) → pas de fallback disque.
  5. ``_disk_fallback_done=True`` court-circuite tout 2e essai disque (anti-récursion).
  6. Status 400/401 (autre forme d'auth failure) → fallback aussi tenté.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from arabesque.broker.ctrader import CTraderBroker


def _build_broker_stub(client_id: str = "stub-client") -> CTraderBroker:
    broker = CTraderBroker.__new__(CTraderBroker)
    broker._client = None
    broker._connected = False
    broker._asyncio_loop = None
    broker._reactor_running = True
    broker._reactor_thread = None
    broker._token_refreshed = True
    broker.refresh_token = "R_in_memory_dead"
    broker.access_token = "T_in_memory_dead"
    broker.client_id = client_id
    broker.client_secret = "stub-secret"
    broker.account_id = 12345
    broker.broker_id = "ftmo_challenge"
    broker.config = {"auto_refresh_token": False}
    broker._subscribed_symbol_ids = set()
    broker._pending_requests = {}
    return broker


def _write_secrets(tmp_path: Path, refresh: str, access: str = "T_disk_fresh") -> Path:
    """Écrit un secrets.yaml minimal avec une section partagée ``ctrader_oauth``."""
    secrets = {
        "ctrader_oauth": {
            "client_id": "stub-client",
            "client_secret": "stub-secret",
            "access_token": access,
            "refresh_token": refresh,
        },
        "ftmo_challenge": {
            "account_id": "12345",
            "oauth": "ctrader_oauth",
        },
    }
    path = tmp_path / "secrets.yaml"
    path.write_text(yaml.safe_dump(secrets))
    return path


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


# ---------------------------------------------------------------------------
# 1. ACCESS_DENIED + disque diffère → adoption + retry réussit
# ---------------------------------------------------------------------------

def test_access_denied_with_fresh_disk_token_adopts_and_retries():
    """Le scénario exact de l'incident 2026-05-22 : l'engine a un refresh_token
    in-memory mort, mais le disque contient un refresh_token frais écrit par un
    autre processus. Le fallback doit lire le disque, adopter, et retenter."""
    broker = _build_broker_stub()
    CTraderBroker._shared_tokens.pop(broker.client_id, None)

    call_log = []

    def fake_post(url, data=None, timeout=None):
        used_refresh = (data or {}).get("refresh_token")
        call_log.append(used_refresh)
        if used_refresh == "R_in_memory_dead":
            return _FakeResponse(200, {"errorCode": "ACCESS_DENIED", "description": "Bad token"})
        return _FakeResponse(200, {
            "accessToken": "T_new_via_disk_retry",
            "refreshToken": "R_new_via_disk_retry",
        })

    with patch("arabesque.broker.ctrader.requests.post", side_effect=fake_post), \
         patch("arabesque.config.load_broker_tokens",
               lambda bid, **kw: ("T_disk_fresh", "R_disk_fresh")), \
         patch.object(broker, "_save_tokens_to_config", lambda: None):
        ok = broker._refresh_access_token()

    assert ok is True, "Le retry après adoption disque doit réussir"
    assert len(call_log) == 2, (
        f"Attendu 2 appels HTTP (1 initial ACCESS_DENIED + 1 retry), vu {len(call_log)}"
    )
    assert call_log[0] == "R_in_memory_dead", "1er appel utilise le token in-memory mort"
    assert call_log[1] == "R_disk_fresh", "2e appel utilise le refresh_token disque adopté"
    assert broker.access_token == "T_new_via_disk_retry"
    assert broker.refresh_token == "R_new_via_disk_retry"


# ---------------------------------------------------------------------------
# 2. ACCESS_DENIED + disque identique → return False sans retry
# ---------------------------------------------------------------------------

def test_access_denied_same_disk_token_no_retry(tmp_path):
    """Si le disque a le MÊME refresh_token que l'in-memory, inutile d'adopter
    et retenter — c'est juste un token vraiment mort. On évite la boucle.
    """
    broker = _build_broker_stub()
    CTraderBroker._shared_tokens.pop(broker.client_id, None)

    call_count = {"http": 0, "disk_read": 0}

    def fake_post(url, data=None, timeout=None):
        call_count["http"] += 1
        return _FakeResponse(200, {"errorCode": "ACCESS_DENIED", "description": "Bad token"})

    def fake_load(bid, **kw):
        call_count["disk_read"] += 1
        # Disque identique à l'in-memory — pas de drift
        return ("T_in_memory_dead", "R_in_memory_dead")

    with patch("arabesque.broker.ctrader.requests.post", side_effect=fake_post), \
         patch("arabesque.config.load_broker_tokens", side_effect=fake_load):
        ok = broker._refresh_access_token()

    assert ok is False
    assert call_count["http"] == 1, (
        f"Pas de retry HTTP si disque identique. Attendu 1 appel, vu {call_count['http']}"
    )
    assert call_count["disk_read"] == 1, (
        "Le disque doit être lu 1× pour vérifier — mais pas de retry HTTP derrière."
    )


# ---------------------------------------------------------------------------
# 3. ACCESS_DENIED + secrets.yaml absent → return False sans retry
# ---------------------------------------------------------------------------

def test_access_denied_no_secrets_file_no_retry():
    """Si load_broker_tokens retourne None (fichier absent / illisible), pas de retry."""
    broker = _build_broker_stub()
    CTraderBroker._shared_tokens.pop(broker.client_id, None)

    http_calls = []

    def fake_post(url, data=None, timeout=None):
        http_calls.append(url)
        return _FakeResponse(200, {"errorCode": "ACCESS_DENIED"})

    with patch("arabesque.broker.ctrader.requests.post", side_effect=fake_post), \
         patch("arabesque.config.load_broker_tokens", return_value=None):
        ok = broker._refresh_access_token()

    assert ok is False
    assert len(http_calls) == 1, (
        f"Pas de retry si load_broker_tokens=None. Attendu 1 appel HTTP, vu {len(http_calls)}"
    )


# ---------------------------------------------------------------------------
# 4. Non-ACCESS_DENIED (erreur réseau) → pas de fallback disque
# ---------------------------------------------------------------------------

def test_non_access_denied_error_no_disk_fallback():
    """Une exception réseau ne doit PAS déclencher de relecture disque
    (le token n'est pas le problème — c'est le réseau).
    """
    broker = _build_broker_stub()
    CTraderBroker._shared_tokens.pop(broker.client_id, None)

    disk_calls = []

    def fake_post(url, data=None, timeout=None):
        raise ConnectionError("Network unreachable")

    def fake_load(bid, **kw):
        disk_calls.append(bid)
        return ("X", "Y")

    with patch("arabesque.broker.ctrader.requests.post", side_effect=fake_post), \
         patch("arabesque.config.load_broker_tokens", side_effect=fake_load):
        ok = broker._refresh_access_token()

    assert ok is False
    assert disk_calls == [], (
        "Erreur réseau ≠ ACCESS_DENIED. Le disque ne doit PAS être lu."
    )


def test_500_status_no_disk_fallback():
    """Une 500 (server down) ne doit pas déclencher le fallback disque non plus."""
    broker = _build_broker_stub()
    CTraderBroker._shared_tokens.pop(broker.client_id, None)

    disk_calls = []

    def fake_post(url, data=None, timeout=None):
        return _FakeResponse(500, {})

    def fake_load(bid, **kw):
        disk_calls.append(bid)
        return ("X", "Y")

    with patch("arabesque.broker.ctrader.requests.post", side_effect=fake_post), \
         patch("arabesque.config.load_broker_tokens", side_effect=fake_load):
        ok = broker._refresh_access_token()

    assert ok is False
    assert disk_calls == [], "500 = server down, pas un token problem. Pas de fallback disque."


# ---------------------------------------------------------------------------
# 5. _disk_fallback_done=True → court-circuite tout 2e essai (anti-récursion)
# ---------------------------------------------------------------------------

def test_disk_fallback_done_short_circuits_recursion():
    """Quand on est en train d'exécuter le retry (déjà adopté le disque), un
    nouvel ACCESS_DENIED ne doit PAS re-tenter le disque (sinon boucle infinie
    si le token disque est aussi mort).
    """
    broker = _build_broker_stub()
    CTraderBroker._shared_tokens.pop(broker.client_id, None)

    disk_calls = []

    def fake_post(url, data=None, timeout=None):
        return _FakeResponse(200, {"errorCode": "ACCESS_DENIED"})

    def fake_load(bid, **kw):
        disk_calls.append(bid)
        return ("T_other", "R_other")

    with patch("arabesque.broker.ctrader.requests.post", side_effect=fake_post), \
         patch("arabesque.config.load_broker_tokens", side_effect=fake_load):
        # Appel direct avec _disk_fallback_done=True : simule le retry interne
        ok = broker._refresh_access_token(force_http=True, _disk_fallback_done=True)

    assert ok is False
    assert disk_calls == [], (
        "_disk_fallback_done=True doit court-circuiter — pas de 2e relecture disque."
    )


# ---------------------------------------------------------------------------
# 6. Status 400/401 → fallback disque tenté (autre forme d'auth failure)
# ---------------------------------------------------------------------------

def test_status_400_triggers_disk_fallback():
    """Certaines implémentations OAuth retournent 400 sur refresh_token mort
    plutôt qu'un 200 avec errorCode. Le fallback doit s'activer aussi."""
    broker = _build_broker_stub()
    CTraderBroker._shared_tokens.pop(broker.client_id, None)

    call_log = []

    def fake_post(url, data=None, timeout=None):
        used_refresh = (data or {}).get("refresh_token")
        call_log.append((used_refresh, "post"))
        if used_refresh == "R_in_memory_dead":
            return _FakeResponse(400, {"error": "invalid_grant"})
        return _FakeResponse(200, {
            "accessToken": "T_retry",
            "refreshToken": "R_retry",
        })

    with patch("arabesque.broker.ctrader.requests.post", side_effect=fake_post), \
         patch("arabesque.config.load_broker_tokens",
               lambda bid, **kw: ("T_disk", "R_disk_fresh")), \
         patch.object(broker, "_save_tokens_to_config", lambda: None):
        ok = broker._refresh_access_token()

    assert ok is True
    assert len(call_log) == 2, (
        f"Status 400 doit déclencher fallback disque + retry. Vu {len(call_log)} appels."
    )
    assert call_log[1][0] == "R_disk_fresh"


def test_status_401_triggers_disk_fallback():
    broker = _build_broker_stub()
    CTraderBroker._shared_tokens.pop(broker.client_id, None)

    call_log = []

    def fake_post(url, data=None, timeout=None):
        used_refresh = (data or {}).get("refresh_token")
        call_log.append(used_refresh)
        if used_refresh == "R_in_memory_dead":
            return _FakeResponse(401, {})
        return _FakeResponse(200, {"accessToken": "T_x", "refreshToken": "R_x"})

    with patch("arabesque.broker.ctrader.requests.post", side_effect=fake_post), \
         patch("arabesque.config.load_broker_tokens",
               lambda bid, **kw: ("T_disk", "R_disk_fresh")), \
         patch.object(broker, "_save_tokens_to_config", lambda: None):
        ok = broker._refresh_access_token()

    assert ok is True
    assert len(call_log) == 2


# ---------------------------------------------------------------------------
# 7. Test end-to-end via fichier réel (intégration load_broker_tokens)
# ---------------------------------------------------------------------------

def test_load_broker_tokens_reads_shared_oauth_section(tmp_path):
    """Vérifie que ``load_broker_tokens`` lit bien la section partagée
    référencée par ``oauth:`` (structure secrets.yaml réelle de prod)."""
    from arabesque.config import load_broker_tokens

    secrets_path = _write_secrets(tmp_path, refresh="R_real", access="T_real")
    tokens = load_broker_tokens("ftmo_challenge", secrets_path=secrets_path)

    assert tokens is not None
    assert tokens == ("T_real", "R_real")


def test_load_broker_tokens_reads_inline_section(tmp_path):
    """Structure legacy : tokens directement dans le broker (pas de section partagée)."""
    from arabesque.config import load_broker_tokens

    secrets = {
        "ftmo_challenge": {
            "client_id": "x",
            "client_secret": "y",
            "access_token": "T_inline",
            "refresh_token": "R_inline",
            "account_id": "1234",
        },
    }
    path = tmp_path / "secrets.yaml"
    path.write_text(yaml.safe_dump(secrets))

    tokens = load_broker_tokens("ftmo_challenge", secrets_path=path)
    assert tokens == ("T_inline", "R_inline")


def test_load_broker_tokens_missing_file_returns_none(tmp_path):
    from arabesque.config import load_broker_tokens

    tokens = load_broker_tokens("ftmo_challenge", secrets_path=tmp_path / "missing.yaml")
    assert tokens is None


def test_load_broker_tokens_unknown_broker_returns_none(tmp_path):
    from arabesque.config import load_broker_tokens

    secrets_path = _write_secrets(tmp_path, refresh="R")
    tokens = load_broker_tokens("inconnu", secrets_path=secrets_path)
    assert tokens is None
