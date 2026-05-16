# Audit configuration cTrader — FTMO Challenge (2026-05-16)

Audit demandé après l'incident `ALREADY_LOGGED_IN` du 14/05 et le restart 16/05.
Objectif : confirmer que l'engine pointe bien sur le bon environnement, avec les bons credentials et les bons droits.

**Aucune modification de credentials. Audit lecture seule.**

## 1. Endpoint utilisé : DEMO

Source : `arabesque/broker/ctrader.py:113-115`

```python
self.is_demo = config.get("is_demo", True)
self.host = EndPoints.PROTOBUF_DEMO_HOST if self.is_demo else EndPoints.PROTOBUF_LIVE_HOST
self.port = EndPoints.PROTOBUF_PORT
```

Valeurs résolues par la lib `ctrader-open-api` :

| Constante | Valeur |
|---|---|
| `EndPoints.PROTOBUF_DEMO_HOST` | `demo.ctraderapi.com` |
| `EndPoints.PROTOBUF_LIVE_HOST` | `live.ctraderapi.com` |
| `EndPoints.PROTOBUF_PORT` | `5035` |

`config/settings.yaml` pour `ftmo_challenge` :
```yaml
brokers:
  ftmo_challenge:
    is_demo: true
```

→ **endpoint TCP utilisé : `demo.ctraderapi.com:5035`**. Confirmé par les logs systemd :
`[cTrader] Connected to demo.ctraderapi.com:5035`.

## 2. ctidTraderAccountId et isLive renvoyés par l'API

Vérification live via l'endpoint REST OAuth officiel (lecture seule, pas de session TCP) :

```
GET https://api.spotware.com/connect/tradingaccounts?oauth_token=<access_token>
```

Réponse (2026-05-16, formatée) :

```json
{
  "accountId": 45667282,
  "accountNumber": 7479813,
  "live": false,
  "brokerName": "ftmo",
  "brokerTitle": "FTMO Platform",
  "depositCurrency": "USD",
  "traderAccountType": "HEDGED",
  "leverage": 30,
  "leverageInCents": 3000,
  "balance": 9355199,
  "moneyDigits": 2,
  "accountStatus": "ACTIVE",
  "swapFree": false,
  "deleted": false
}
```

Lecture :

| Champ API | Valeur | Conséquence |
|---|---|---|
| `accountId` | `45667282` | Correspond exactement à `secrets.yaml → ftmo_challenge.account_id` |
| `live` | `false` | Compte démo côté serveur → cohérent avec `is_demo: true` côté code |
| `brokerName` | `ftmo` | Bon broker |
| `depositCurrency` | `USD` | Cohérent avec balance $93 551.99 dans les logs |
| `balance` | `9355199` (cents) | `9355199 / 100 = $93 551.99` ✅ |
| `accountStatus` | `ACTIVE` | Compte non suspendu |
| `traderAccountType` | `HEDGED` | Cohérent avec stratégie multi-positions |
| `leverageInCents` | `3000` | Levier 30:1 ✅ |

**Verdict** : l'endpoint (DEMO) et la nature du compte (`live: false`) sont alignés. Pas de risque de pointer accidentellement sur un compte réel.

## 3. accessRights du token

Source du champ : `ProtoOATraderRes.trader.accessRights`, valeurs énumérées :

| Valeur | Sens |
|---|---|
| `FULL_ACCESS = 0` | Lecture + trading complet |
| `CLOSE_ONLY = 1` | Lecture + close uniquement |
| `NO_TRADING = 2` | Lecture seule |
| `NO_LOGIN = 3` | Refus d'auth |

État actuel : **non logué explicitement** par `_process_trader_response` (`arabesque/broker/ctrader.py:1183-1206`). Seuls `balance`, `usedMargin`, `depositAssetId`, `leverageInCents` sont consommés.

**De facto** : l'engine place, amend et close des ordres avec succès (logs des 7 derniers jours), `ProtoOAExecutionEvent` reçus sans rejet d'autorisation → `accessRights = FULL_ACCESS` en pratique.

**Recommandation différée (post-Phase 4 bis)** : ajouter un log info au premier `ProtoOATraderRes` reçu après auth pour matérialiser `accessRights`, `accountType`, `brokerName`, `registrationTimestamp`. Patch trivial, mais hors scope tant qu'on observe Extension + Glissade.

## 4. Origine des client_id / client_secret

Source : `config/secrets.yaml → ctrader_oauth`.

| Champ | Valeur | Interprétation |
|---|---|---|
| `client_id` | `23710_...` | Préfixe `23710_` = ID interne d'app cTrader Open API. ID numérique attribué par Spotware lors de la création de l'app. |
| `client_secret` | 50 chars | Secret de l'app, généré par Spotware. |
| `access_token` | 44 chars | Token OAuth obtenu via flow d'autorisation. |
| `refresh_token` | 43 chars | Token long-lived, persistant tant qu'on refresh. |

**Indices d'app approuvée (pas playground/sandbox)** :

- Le refresh token fonctionne en boucle depuis février 2026 (cf. logs `[cTrader] ✅ Token refreshed successfully`).
- L'`ProtoOAApplicationAuthReq` reçoit `ProtoOAApplicationAuthRes` sans erreur (logs : `[cTrader] ✅ Application authenticated`).
- Le compte FTMO réel `45667282` est accessible (les apps non approuvées ne voient que les comptes démo "sandbox" gérés par Spotware, pas les comptes broker).
- Le statut `accountStatus: ACTIVE` retourné par l'API REST officielle confirme que l'app peut interroger Spotware production.

**Endpoint token utilisé** : `https://openapi.ctrader.com/apps/token` (`arabesque/broker/ctrader.py:212`) — c'est bien l'endpoint OAuth de production Spotware.

Section legacy `ctrader_oauth_old` dans `secrets.yaml` : credentials d'une app précédente (préfixe `19907_`), conservés mais non référencés par aucun broker. À nettoyer une fois la confiance Phase 2.5 confirmée — hors scope ici.

## 5. ProtoOAApplicationAuthReq + ProtoOAAccountAuthReq — credentials utilisés

Trace dans `arabesque/broker/ctrader.py` :

### ProtoOAApplicationAuthReq (lignes 385-390)

```python
def on_connected(client):
    print(f"[cTrader] Connected to {self.host}:{self.port}")
    req = ProtoOAApplicationAuthReq()
    req.clientId = self.client_id
    req.clientSecret = self.client_secret
    client.send(req)
```

→ `self.client_id` et `self.client_secret` sont lus à `__init__` depuis `config.get("client_id")` / `config.get("client_secret")` (`ctrader.py:105-106`). Ces clés sont injectées par le résolveur `_resolve_secret_refs` (`arabesque/config.py:193-218`) qui merge `ctrader_oauth.*` dans `ftmo_challenge.*` via le pointer `oauth: ctrader_oauth`.

→ **Credentials utilisés = ceux de la section `ctrader_oauth` (client `23710_...`)**.

### ProtoOAAccountAuthReq (lignes 406-431)

```python
if ptype == "ProtoOAApplicationAuthRes":
    ...
    if self.account_id:
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = self.account_id
        req.accessToken = self.access_token
        client.send(req)
```

→ `self.account_id = 45667282` (vérifié REST), `self.access_token = ctrader_oauth.access_token`.

→ Branche `account_id` configuré utilisée systématiquement (pas de fallback `ProtoOAGetAccountListByAccessTokenReq` à l'ouverture). La branche fallback (l415-431) ne se déclenche que si `account_id` est absent du config — ce n'est pas notre cas.

**Confirmation log** : `[cTrader] ✅ Application authenticated` puis `[cTrader] ✅ Account 45667282 authenticated` (logs systemd 2026-05-16 08:44:11). Les deux phases s'enchaînent sans erreur.

## 6. Pourquoi l'environnement DEMO pour FTMO

Décision documentée ici à des fins de traçabilité (pas une décision nouvelle — état historique).

**Toutes les phases de challenge FTMO se passent sur des comptes démo cTrader.** Ce n'est pas un raccourci côté Arabesque : c'est le modèle économique de FTMO. La phase Challenge, la phase Verification et la phase Funded (compte financé) sont toutes des comptes démo hébergés par FTMO chez cTrader. FTMO réplique en interne les positions sur ses propres comptes réels chez les LPs ; le trader n'a jamais d'accès direct à un compte live.

Conséquences pratiques :

- `is_demo: true` dans `settings.yaml` est **correct quelle que soit la phase FTMO** (Challenge / Verification / Funded).
- `live: false` dans la réponse REST est **attendu** et ne signale pas une erreur de routage.
- Le seul moment où on basculera `is_demo: false` est si l'on connecte un broker cTrader **direct** (Pepperstone, IC Markets, etc.) sur un compte argent réel — ce n'est pas le cas actuel.
- L'endpoint `demo.ctraderapi.com` héberge en réalité des comptes Challenge et Funded de toutes les prop firms partenaires de cTrader (FTMO, MyFundedFutures, etc.) — ce n'est pas un "playground" jetable.

**Documentation Spotware** : la séparation DEMO/LIVE chez cTrader concerne uniquement la nature du compte sous-jacent (argent virtuel vs argent réel chez le LP). Elle n'a aucune incidence sur la latence, le format des messages, ou la disponibilité des features API. Le code Arabesque est strictement identique entre les deux.

## 7. Configuration TLS et sécurité

- Connexion TCP : port 5035 = **TLS chiffré** (port standard cTrader Open API).
- Endpoint OAuth : `https://openapi.ctrader.com/apps/token` (HTTPS).
- Endpoint REST tradingaccounts : `https://api.spotware.com/connect/tradingaccounts` (HTTPS).
- Aucun credential transmis en clair.

## 8. Conclusion

L'environnement cTrader est correctement configuré :

- **Endpoint** : `demo.ctraderapi.com:5035`, cohérent avec `is_demo: true`.
- **Compte** : `45667282` ACTIVE, balance $93 551.99, broker `ftmo`, `live: false` — c'est bien le compte Challenge FTMO attendu.
- **Credentials** : client `23710_...`, app approuvée Spotware (refresh token et auth fonctionnels en production).
- **Flows protobuf** : `ProtoOAApplicationAuthReq` + `ProtoOAAccountAuthReq` utilisent les credentials résolus depuis `ctrader_oauth` via le pointer `oauth:` — pas de mélange entre apps.
- **Droits** : `accessRights` non logué explicitement mais FULL_ACCESS de facto (trading actif).

**Pas de modification recommandée à chaud.** Patch de log `accessRights` à considérer une fois Phase 4 bis stabilisée.

## Sources

- Code : `arabesque/broker/ctrader.py:99-176`, `arabesque/broker/ctrader.py:376-450`, `arabesque/broker/ctrader.py:1183-1206`, `arabesque/config.py:193-218`.
- Lib : `ctrader_open_api.EndPoints`, `OpenApiModelMessages_pb2.ProtoOACtidTraderAccount`, `ProtoOAAccessRights`.
- API REST : `https://api.spotware.com/connect/tradingaccounts` (2026-05-16, lecture seule).
- Logs systemd : `journalctl --user -u arabesque-live.service` (16/05 08:44 UTC).
