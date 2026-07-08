"""Patch P1+P2 (2026-05-19) — non-régression sur le retry CH_ACCESS_TOKEN_INVALID
et l'intervalle par défaut du refresh préventif.

Incident fondateur : 2026-05-19 22:59 CEST. Feed stale ETHUSD → ``_force_reconnect``
→ nouveau broker créé avec ``broker_cfg["access_token"]=T0`` (config initiale) ;
``_refresh_access_token`` lit ``_shared_tokens[client_id]=T1`` (token refreshé au
boot 22h plus tôt) ; adopte T1 ; T1 est invalide côté serveur cTrader →
CH_ACCESS_TOKEN_INVALID → ``connect()`` retourne False ; ``_run_loop`` re-arme
``_force_reconnect`` ; nouvelle itération ré-adopte le même T1 invalide.
11 itérations en boucle sur 23 min, résolu uniquement par restart manuel.

Le patch P1 introduit :
  A. ``_refresh_access_token(force_http=True)`` : bypass la branche d'adoption
     du sibling token, force un vrai appel HTTP /apps/token.
  B. ``on_message`` détecte ``ProtoOAErrorRes.errorCode == "CH_ACCESS_TOKEN_INVALID"``
     et purge ``_shared_tokens[client_id]`` + reset ``_token_refreshed=False``.
  C. ``connect()`` détecte ``CH_ACCESS_TOKEN_INVALID`` dans ``str(e)`` et fait
     **1 seul retry** avec ``_refresh_access_token(force_http=True)``.
     Si le refresh échoue → return False propre, pas de boucle.
     Si 2e échec après retry → return False propre.

Le patch P2 introduit :
  D. ``PriceFeedManager.token_refresh_interval_h`` default 60.0 → 12.0
     (mesure empirique 2026-05-19 : access_token effectif ~22h sur FTMO demo).

Invariants verrouillés :
  1. ``force_http=True`` bypass l'adoption sibling.
  2. CH_ACCESS_TOKEN_INVALID → 1 refresh forcé + 1 retry, pas plus.
  3. Refresh HTTP forcé échoué → return False, pas de boucle.
  4. 2e CH_ACCESS_TOKEN_INVALID après retry → return False, pas de boucle.
  5. ALREADY_LOGGED_IN reste inchangé (3 retries / 60-180-600).
  6. Default ``token_refresh_interval_h`` est 12.0.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch


from arabesque.broker.ctrader import CTraderBroker


def _build_broker_stub(client_id: str = "stub-client") -> CTraderBroker:
    """Construit un CTraderBroker minimal sans réseau."""
    broker = CTraderBroker.__new__(CTraderBroker)
    broker._client = None
    broker._connected = False
    broker._asyncio_loop = None
    broker._reactor_running = True  # bypass _ensure_reactor_running
    broker._reactor_thread = None
    broker._token_refreshed = True  # bypass token refresh initial
    broker.refresh_token = "stub-refresh"
    broker.access_token = "stub-access"
    broker.client_id = client_id
    broker.client_secret = "stub-secret"
    broker.account_id = 12345
    broker.broker_id = "stub-broker"
    broker.config = {"auto_refresh_token": False}
    broker._subscribed_symbol_ids = set()
    broker._pending_requests = {}
    return broker


# ---------------------------------------------------------------------------
# 1. force_http=True bypass l'adoption sibling
# ---------------------------------------------------------------------------

def test_force_http_bypasses_sibling_adoption():
    """``_refresh_access_token(force_http=True)`` doit IGNORER ``_shared_tokens``
    et appeler le endpoint HTTP, même si un token sibling est disponible.

    Sans ce flag (chemin nominal), un broker neuf adopte aveuglément le token
    cached — ce qui est le bug exact de l'incident 2026-05-19 (token T1 invalide
    re-adopté en boucle).
    """
    broker = _build_broker_stub(client_id="cid-A")
    # Pré-remplir le cache sibling avec un token "frais" (mais qu'on veut quand
    # même bypasser parce qu'on vient de détecter qu'il est invalide).
    CTraderBroker._shared_tokens["cid-A"] = ("T_sibling_alive", "R_sibling")
    broker.access_token = "T_initial_old"
    broker.refresh_token = "R_initial"

    http_calls = []

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"accessToken": "T_fresh_from_http", "refreshToken": "R_fresh"}

    def fake_post(url, data=None, timeout=None):
        http_calls.append((url, dict(data or {})))
        return FakeResponse()

    try:
        with patch("arabesque.broker.ctrader.requests.post", side_effect=fake_post), \
             patch.object(broker, "_save_tokens_to_config", lambda: None):
            ok = broker._refresh_access_token(force_http=True)
    finally:
        CTraderBroker._shared_tokens.pop("cid-A", None)

    assert ok is True
    assert len(http_calls) == 1, (
        "force_http=True doit appeler le endpoint /apps/token, "
        f"vu {len(http_calls)} appel(s)."
    )
    assert broker.access_token == "T_fresh_from_http", (
        "Le token doit venir du refresh HTTP, pas du sibling cached."
    )
    assert broker.access_token != "T_sibling_alive", (
        "REGRESSION P1 : force_http=True n'a PAS bypassé le sibling — "
        "le bug 2026-05-19 reviendrait."
    )


def test_default_path_still_adopts_sibling():
    """Sanity check : sans ``force_http``, le chemin nominal continue à adopter
    le sibling (sinon on casse l'optimisation anti-collision OAuth)."""
    broker = _build_broker_stub(client_id="cid-B")
    CTraderBroker._shared_tokens["cid-B"] = ("T_sibling", "R_sibling")
    broker.access_token = "T_initial"
    broker.refresh_token = "R_initial"

    http_calls = []

    def fake_post(url, data=None, timeout=None):
        http_calls.append(url)
        raise AssertionError("HTTP ne devrait PAS être appelé en chemin nominal")

    try:
        with patch("arabesque.broker.ctrader.requests.post", side_effect=fake_post):
            ok = broker._refresh_access_token()  # force_http=False (défaut)
    finally:
        CTraderBroker._shared_tokens.pop("cid-B", None)

    assert ok is True
    assert http_calls == [], "Adoption sibling doit ne PAS appeler HTTP"
    assert broker.access_token == "T_sibling"
    assert broker.refresh_token == "R_sibling"


# ---------------------------------------------------------------------------
# 2. CH_ACCESS_TOKEN_INVALID → 1 retry avec refresh forcé HTTP
# ---------------------------------------------------------------------------

def test_connect_retries_once_with_force_http_on_token_invalid():
    """Sur CH_ACCESS_TOKEN_INVALID, ``connect()`` doit :
      - appeler ``_refresh_access_token(force_http=True)`` exactement 1 fois ;
      - rejouer ``_connect_once`` exactement 1 fois ;
      - retourner le résultat du retry (True ici).
    """
    broker = _build_broker_stub()

    call_count = {"connect_once": 0, "refresh": 0}
    refresh_calls = []  # liste des appels avec leur valeur force_http

    async def fake_connect_once():
        call_count["connect_once"] += 1
        if call_count["connect_once"] == 1:
            raise Exception(
                "cTrader Error: CH_ACCESS_TOKEN_INVALID - Invalid access token"
            )
        return True  # retry réussit

    def fake_refresh(force_http=False):
        call_count["refresh"] += 1
        refresh_calls.append(force_http)
        return True

    def fake_stop():
        pass

    broker._connect_once = fake_connect_once
    broker._refresh_access_token = fake_refresh
    broker._stop_client = fake_stop

    result = asyncio.run(broker.connect())

    assert result is True
    assert call_count["connect_once"] == 2, (
        f"Attendu 2 _connect_once (1 initial + 1 retry), vu {call_count['connect_once']}"
    )
    assert call_count["refresh"] == 1, (
        f"Attendu 1 _refresh_access_token sur token invalid, vu {call_count['refresh']}"
    )
    assert refresh_calls == [True], (
        f"Refresh devait être appelé avec force_http=True, vu {refresh_calls}"
    )


# ---------------------------------------------------------------------------
# 3. CRITIQUE — refresh HTTP forcé échoue → return False, PAS de boucle
# ---------------------------------------------------------------------------

def test_connect_returns_false_when_force_refresh_fails():
    """Si ``_refresh_access_token(force_http=True)`` retourne False (refresh_token
    mort, réseau down, OAuth rejette), ``connect()`` doit retourner False
    IMMÉDIATEMENT, sans nouveau retry.

    Sans cette garantie, P1 pourrait créer une nouvelle boucle en cas de vrai
    problème OAuth — pire que l'incident original.
    """
    broker = _build_broker_stub()

    call_count = {"connect_once": 0, "refresh": 0, "stop": 0, "sleep": 0}

    async def fake_connect_once():
        call_count["connect_once"] += 1
        raise Exception(
            "cTrader Error: CH_ACCESS_TOKEN_INVALID - Invalid access token"
        )

    def fake_refresh(force_http=False):
        call_count["refresh"] += 1
        return False  # OAuth refresh échoue

    def fake_stop():
        call_count["stop"] += 1

    async def fake_sleep(d):
        call_count["sleep"] += 1

    broker._connect_once = fake_connect_once
    broker._refresh_access_token = fake_refresh
    broker._stop_client = fake_stop

    with patch("asyncio.sleep", new=fake_sleep):
        result = asyncio.run(broker.connect())

    assert result is False
    assert call_count["connect_once"] == 1, (
        f"Refresh failed → pas de retry. Attendu 1 _connect_once, "
        f"vu {call_count['connect_once']} (RISQUE BOUCLE)."
    )
    assert call_count["refresh"] == 1, (
        "Le refresh forcé doit être tenté exactement 1 fois."
    )
    assert call_count["sleep"] == 0, (
        "Pas de sleep en cas de refresh failed — abandon immédiat, pas de "
        "backoff bouclant."
    )


# ---------------------------------------------------------------------------
# 4. Si 2e CH_ACCESS_TOKEN_INVALID après le retry → return False
# ---------------------------------------------------------------------------

def test_connect_returns_false_on_repeated_token_invalid():
    """Si après le retry (avec refresh HTTP réussi) on récupère encore
    CH_ACCESS_TOKEN_INVALID, on abandonne — on ne tente pas un 2e refresh.
    Sinon : boucle infinie possible si le compte est révoqué côté broker.
    """
    broker = _build_broker_stub()

    call_count = {"connect_once": 0, "refresh": 0}

    async def fake_connect_once():
        call_count["connect_once"] += 1
        # CH_ACCESS_TOKEN_INVALID à chaque tentative
        raise Exception(
            "cTrader Error: CH_ACCESS_TOKEN_INVALID - Invalid access token"
        )

    def fake_refresh(force_http=False):
        call_count["refresh"] += 1
        return True  # le refresh HTTP "réussit" mais le serveur rejette quand même

    def fake_stop():
        pass

    broker._connect_once = fake_connect_once
    broker._refresh_access_token = fake_refresh
    broker._stop_client = fake_stop

    result = asyncio.run(broker.connect())

    assert result is False
    assert call_count["connect_once"] == 2, (
        f"Borné à 1 retry. Attendu 2 _connect_once (initial + 1 retry), "
        f"vu {call_count['connect_once']} (BOUCLE)."
    )
    assert call_count["refresh"] == 1, (
        "1 seul refresh forcé même si le serveur rejette à nouveau."
    )


# ---------------------------------------------------------------------------
# 5. Non-régression ALREADY_LOGGED_IN — chemin inchangé (3 retries 60/180/600)
# ---------------------------------------------------------------------------

def test_already_logged_in_path_unchanged():
    """ALREADY_LOGGED_IN doit conserver son comportement patch A+B :
    3 retries avec délais (60, 180, 600), ``_cleanup_for_retry`` à chaque
    itération, ``_stop_client`` 1× in fine. Le refactor while/compteurs
    séparés du patch P1 ne doit RIEN changer à ce chemin.
    """
    broker = _build_broker_stub()

    cleanup_calls = []
    sleep_calls = []
    stop_calls = []

    async def fake_connect_once():
        raise Exception(
            "cTrader Error: 2 - ALREADY_LOGGED_IN - "
            "Open API application is already authorized"
        )

    async def fake_cleanup():
        cleanup_calls.append(True)

    async def fake_sleep(d):
        sleep_calls.append(d)

    def fake_stop():
        stop_calls.append(True)

    def fake_refresh(force_http=False):
        raise AssertionError(
            "_refresh_access_token ne doit PAS être appelé sur ALREADY_LOGGED_IN"
        )

    broker._connect_once = fake_connect_once
    broker._cleanup_for_retry = fake_cleanup
    broker._stop_client = fake_stop
    broker._refresh_access_token = fake_refresh

    with patch("asyncio.sleep", new=fake_sleep):
        result = asyncio.run(broker.connect())

    assert result is False
    assert len(cleanup_calls) == 3, (
        f"3 retries ALREADY_LOGGED_IN → 3 cleanup, vu {len(cleanup_calls)}"
    )
    assert sleep_calls == [60, 180, 600], (
        f"Délais doivent être [60, 180, 600] (patch A), vu {sleep_calls}"
    )
    assert len(stop_calls) == 1, (
        f"_stop_client appelé 1× in fine, vu {len(stop_calls)}"
    )


# ---------------------------------------------------------------------------
# 6. Mix CH_ACCESS_TOKEN_INVALID + ALREADY_LOGGED_IN : compteurs indépendants
# ---------------------------------------------------------------------------

def test_token_invalid_does_not_consume_already_logged_slot():
    """Un retry token_invalid ne doit PAS consommer un slot
    ALREADY_LOGGED_IN. Si après le refresh forcé, on tombe sur ALREADY,
    on doit avoir nos 3 slots ALREADY intacts.
    """
    broker = _build_broker_stub()

    sequence = [
        "CH_ACCESS_TOKEN_INVALID",
        "ALREADY_LOGGED_IN", "ALREADY_LOGGED_IN", "ALREADY_LOGGED_IN",
    ]
    cleanup_calls = []
    sleep_calls = []

    async def fake_connect_once():
        if not sequence:
            return True
        err = sequence.pop(0)
        raise Exception(f"cTrader Error: {err} - test")

    async def fake_cleanup():
        cleanup_calls.append(True)

    async def fake_sleep(d):
        sleep_calls.append(d)

    def fake_refresh(force_http=False):
        return True

    broker._connect_once = fake_connect_once
    broker._cleanup_for_retry = fake_cleanup
    broker._stop_client = lambda: None
    broker._refresh_access_token = fake_refresh

    with patch("asyncio.sleep", new=fake_sleep):
        result = asyncio.run(broker.connect())

    # On a brûlé 3 retries ALREADY_LOGGED_IN après le retry token_invalid,
    # puis le 5e _connect_once retourne True (sequence vide)
    assert result is True
    assert len(cleanup_calls) == 3, (
        f"3 retries ALREADY_LOGGED_IN après token_invalid, "
        f"vu {len(cleanup_calls)} cleanups."
    )
    assert sleep_calls == [60, 180, 600], (
        "Les 3 slots ALREADY n'ont pas été consommés par le retry token : "
        f"vu {sleep_calls}"
    )


# ---------------------------------------------------------------------------
# 7. P2 — Default token_refresh_interval_h = 12.0
# ---------------------------------------------------------------------------

def test_price_feed_default_token_refresh_interval_is_12h():
    """Le défaut doit être 12h (P2 2026-05-19). 60h initial était trop long :
    sur FTMO demo, l'access_token a expiré côté serveur en ~22h alors que le
    refresh préventif n'avait pas encore tourné.
    """
    from arabesque.execution.price_feed import PriceFeedManager

    pf = PriceFeedManager(
        broker_id="x",
        broker_cfg={"type": "ctrader"},
        symbols=["EURUSD"],
    )
    assert pf.token_refresh_interval_h == 12.0, (
        f"Défaut attendu 12.0h (P2 2026-05-19), vu {pf.token_refresh_interval_h}h. "
        "Mesure empirique : access_token FTMO demo ~22h ; un défaut > 22h "
        "réintroduit la fenêtre d'expiration silencieuse."
    )
