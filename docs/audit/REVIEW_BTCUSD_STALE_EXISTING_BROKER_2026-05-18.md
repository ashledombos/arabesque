# Revue — bug `existing_broker._connected=True` masque feed TCP mort

**Date** : 2026-05-18
**Statut** : revue uniquement (pas de patch demandé)
**Contexte** : incident 2026-05-18 17:18:56 UTC → 19:13:27 UTC (1h54).
BTCUSD `aucun tick depuis 6766s` au moment du restart opérateur.
45 reconnect attempts, **tous loggués** `Réutilisation du broker existant` —
aucune ne refait une vraie connexion TCP.

## Coût opérationnel

- 0 entry pendant la fenêtre (logs/trade_journal.jsonl)
- 0 exit pendant la fenêtre
- 0 position ouverte au début, 0 à la fin
- `replay_signals_vs_live` 17:18 → 19:14 : 0 signal manqué
- **Coût = 0 trade** sur cet incident, mais **récidive structurelle** : 4e
  incident similaire en ~10 jours (8-9, 10-11, 12-13, 18 mai).

## Reproduction du chemin de code

Tout est dans `arabesque/execution/price_feed.py`.

### 1. `_run_loop()` lignes 253-306 — boucle de reconnexion

```python
while self._running:
    try:
        await self._connect_and_subscribe()      # bloque dans _watch_connection
        ...
    except Exception as e:
        last_err = str(e)
        logger.error(f"[PriceFeed] Erreur: {e}")

    self._connected = False                       # ← flag PriceFeed remis à False
    self._reconnect_count += 1
    ...
    await asyncio.sleep(delay)
```

**Ce qui manque** : `self._broker` n'est **jamais reset**, et `self._broker._connected`
n'est **jamais touché**. Le flag broker reste à `True` indéfiniment.

### 2. `_connect_and_subscribe()` lignes 334-426 — premier `if` toxique

```python
async def _connect_and_subscribe(self) -> None:
    already_subscribed = False
    if self._broker and getattr(self._broker, '_connected', False):
        logger.info(
            f"[PriceFeed] Réutilisation du broker existant ({self.broker_id})"
        )
        if hasattr(self._broker, '_spot_callbacks'):
            self._broker._spot_callbacks.clear()
        already_subscribed = bool(
            hasattr(self._broker, '_subscribed_symbol_ids')
            and self._broker._subscribed_symbol_ids
        )
    else:
        from arabesque.broker.ctrader import CTraderBroker
        self._broker = CTraderBroker(self.broker_id, self.broker_cfg)
        connected = await self._broker.connect()           # ← vraie reconnexion TCP
        ...

    self._connected = True
    ...
    if already_subscribed:
        # Souscriptions TCP "actives" — juste refresh callbacks Python
        ...
    else:
        # Subscribe en batch (vraie requête TCP)
        ...

    await self._watch_connection()
```

**Le piège** : `getattr(self._broker, '_connected', False)` lit un flag Python
mis à `True` par `CTraderBroker.connect()` après login OAuth. Ce flag n'a
**aucune corrélation** avec :
- l'état du socket TCP (peut être à demi-fermé, RST en cours, keepalive expiré)
- l'état des souscriptions spots côté serveur (cTrader peut avoir purgé)
- le flux de ticks effectif (ce qui nous intéresse)

Quand `_watch_connection` lève `ConnectionError("Feed stale (majeur crypto)")`,
le broker reste `_connected=True` même si le canal est zombi.

### 3. Boucle infinie observable

```
T0    : _run_loop → _connect_and_subscribe → broker neuf, OK, ticks affluent
T+Δ   : feed crypto BTCUSD coupé silencieusement côté cTrader
T+5m  : _watch_connection raise (5 min sans tick majeur)
        _run_loop catch, sleep, retry
T+5m1s: _connect_and_subscribe → broker._connected=True → "Réutilisation"
        already_subscribed=True → callbacks refresh seulement
        re-enter _watch_connection
T+5m31s: nouvelle vérif → toujours stale → raise
        boucle ×45 reconnects sans jamais retoucher au TCP
```

## Distinction avec le bug ALREADY_LOGGED_IN (patch A+B du 2026-05-18)

| Aspect | ALREADY_LOGGED_IN | existing_broker._connected stale |
|---|---|---|
| Symptôme observable | Login refusé en boucle | "Réutilisation" en boucle, 0 tick |
| État serveur | Session encore détenue par client précédent | Session/abonnement perdus côté serveur |
| État `_connected` flag | False après `_stop_client()` | **True**, jamais reset |
| Branche dans code | `else` de `_connect_and_subscribe` | `if` de `_connect_and_subscribe` |
| Fix appliqué | Patch A+B (cleanup propre avant retry) | **Aucun** (objet de cette revue) |
| Risque trade live | Session refuse pendant 7-14 min | Engine "vert" mais aveugle indéfiniment |

Les deux bugs partagent la même racine épistémologique : **un flag Python
n'est pas une preuve d'état TCP/serveur**. Le patch A+B a corrigé la branche
`else` ; la branche `if` reste fragile.

## Pistes de correction (à discuter — pas implémentées)

Trois options, ordre de complexité croissante.

### Option 1 — Forcer reset broker sur exception `_run_loop`

Le plus simple : après catch dans `_run_loop`, marquer le broker comme à jeter.

```python
except Exception as e:
    last_err = str(e)
    logger.error(f"[PriceFeed] Erreur: {e}")
    # Le flag _connected du broker n'est pas fiable après une exception feed.
    # Forcer un cleanup propre + reset pour que le prochain _connect_and_subscribe
    # passe par la branche "nouveau broker" (vraie reconnexion TCP).
    if self._broker:
        try:
            await self._broker._cleanup_for_retry()  # méthode du patch A+B
        except Exception as cleanup_err:
            logger.warning(f"[PriceFeed] cleanup broker ignoré: {cleanup_err}")
        self._broker = None
```

**Avantages** :
- Minimaliste, ~5 lignes.
- Réutilise `_cleanup_for_retry` du patch A+B (déjà testé, idempotent).
- Force le chemin "nouveau broker" → `await broker.connect()` → vraie reconnexion TCP.

**Risques** :
- Peut déclencher la cascade ALREADY_LOGGED_IN (puisque on rappelle `connect()`).
  Mais patch A+B gère désormais ce cas avec retry (60, 180, 600)s.
- Si `_run_loop` itère trop vite, on dépense des sessions cTrader pour rien.

### Option 2 — Health check explicite TCP avant "Réutilisation"

Plus chirurgical : ne forcer reset que si le broker semble vraiment mort.

```python
if self._broker and getattr(self._broker, '_connected', False):
    # Vérifier que le TCP n'est pas zombi
    broker_healthy = (
        hasattr(self._broker, '_client')
        and self._broker._client is not None
        and getattr(self._broker._client, 'transport', None) is not None
        and self._broker._client.transport.connected
    )
    if broker_healthy:
        # Réutilisation OK
        ...
    else:
        # Broker zombi → force re-connect
        await self._broker._cleanup_for_retry()
        self._broker = None
```

**Avantages** :
- Préserve la fast path "Réutilisation" quand vraiment légitime
  (cas premier boot post-restart où broker.connect() vient de réussir).
- Évite les reconnexions inutiles.

**Risques** :
- Dépend de l'API interne Twisted (`client.transport.connected`).
  Si l'API change, le check passe silencieusement.
- Plus de surface à tester.

### Option 3 — Probe applicatif périodique (pas seulement absence de tick)

Le plus robuste : ne pas se fier à "X minutes sans tick" mais sonder activement
le canal cTrader (ProtoOAVersionReq toutes les 2 min, par exemple).

**Avantages** :
- Détection rapide même sur low-tick periods (weekend forex, plages calmes).
- Distingue "marché calme" de "canal mort".

**Risques** :
- Re-développement non-trivial.
- Hors scope Phase 4 (focus stabilisation, pas de feature nouvelle).

## Recommandation pour discussion (pas implémentation)

- **Phase 4 (now)** : Option 1 si on accepte le risque ALREADY_LOGGED_IN
  (couvert par patch A+B). C'est ~5 lignes, réutilise du code testé, ferme
  une boucle qui occupe actuellement les 4 derniers incidents.
- **Post-Phase 4** : envisager Option 2 ou 3 si le pattern persiste après
  application Option 1.

À débattre :
- Combien de fois encore on accepte de restart manuel sur cette boucle
  silencieuse avant fix ?
- Option 1 est-elle vraiment "stabilisation" ou nouvelle feature ?
  (Argument stabilisation : ferme un bug d'execution actif depuis 10j,
  réutilise patch A+B, blast radius limité à `_run_loop` du PriceFeed.)

## Annexes — file:line références

- `arabesque/execution/price_feed.py:253-306` — `_run_loop()`
- `arabesque/execution/price_feed.py:334-426` — `_connect_and_subscribe()`
- `arabesque/execution/price_feed.py:337-349` — premier `if` toxique
  ("Réutilisation du broker existant")
- `arabesque/execution/price_feed.py:386-400` — `already_subscribed=True`
  (callbacks refresh sans re-subscribe TCP)
- `arabesque/execution/price_feed.py:464-602` — `_watch_connection()`
  (détection stale, lève `ConnectionError`)
- `arabesque/broker/ctrader.py` — `_cleanup_for_retry()` du patch A+B
  (réutilisable pour Option 1)
