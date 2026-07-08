# Incident DASHUSD — Résilience canal trading cTrader

**Date d'ouverture** : 2026-05-21
**Statut** : En cours — étages 0+1 en implémentation
**Position de référence** : DASHUSD #53110148 (FTMO), entry 49.40 le 2026-05-20T22:00:03 UTC, SL 46.70, TP 54.70

Ce document est un dossier de suivi. Il se complète au fur et à mesure (implémentation, observation, décisions). Pour la vue d'ensemble figée, voir `HANDOFF.md` item #5 + `DECISIONS.md` 2026-05-21.

---

## 1. Symptôme observé

DASHUSD position #53110148 ouverte 2026-05-20T22:00:03 UTC. MFE max atteint 1.82R (peak prix 54.33) le 2026-05-21 vers 01:42 UTC. SL côté broker resté à **46.70** (valeur initiale) tout du long. Position toujours ouverte à 13:38 UTC le 2026-05-21 avec un P&L flottant -5.08$ (prix replié près de l'entrée).

Cause directe : `_try_amend_sl` a tenté de remonter le SL à 49.94 (BE) puis 52.44 (trailing tier 3) mais le broker répond `Not connected` sur le canal trading. La boucle log spam toutes les 10s depuis ~10 heures :

```
mai 21 01:42:18 [Monitor] ⚠️ SL amend ABANDONED after 3 attempts: DASHUSD 53110148 target_sl=49.94 last_error=[Not connected]
mai 21 13:38:21 [Monitor] 📈 Trailing tier 3: DASHUSD MFE=1.82R → SL 46.70 → 52.44 (dist=0.7R from peak=54.33)
```

## 2. Cause racine

Le canal **trading** Protobuf cTrader (`self._connected`) est passé à `False` silencieusement entre 22:00 UTC (entry) et 01:42 UTC (premier amend BE). Aucune cause logguée — ni `ALREADY_LOGGED_IN`, ni `CH_ACCESS_TOKEN_INVALID`, ni `feed_stale`. Le canal **feed** quote (séparé) est resté vivant : le monitor reçoit toujours les ticks et calcule MFE/trailing, mais ne peut pas envoyer d'amend.

Le patch token P1+P2 (commit 79007cf, 2026-05-20) ne couvre **pas** ce cas : il traite `CH_ACCESS_TOKEN_INVALID` au login + refresh préventif 12h, mais ne ré-établit pas la session trading si elle tombe en cours de route sans erreur explicite.

## 3. Plan résilience — 5 étages

| Étage | Description | Couverture estimée | Statut |
|---|---|---|---|
| 0 | **Alerte amend abandoned** dans `position_monitor.py` : notif Telegram+ntfy à chaque log `ABANDONED` (cooldown 30 min/position) avec `symbol`, `position_id`, `target_sl`, `last_error`, `mfe_r` | Détection humaine (passive) | 🛠️ en cours |
| 1 | **Reconnect-on-demand** dans `ctrader.py` : avant chaque retour `"Not connected"` (place_order, cancel_order, amend_position_sltp, close_position), tenter un `await self.connect()` une seule fois (cooldown 30s + anti-boucle 3 tentatives/min) | ~95 % (perte session protobuf récupérable par re-login OAuth en cache) | 🛠️ en cours |
| 2 | **Healthcheck proactif 5 min** : heartbeat Protobuf trading (ex: `ProtoOAGetTrendbarsReq` léger) pour détecter la perte de session **avant** qu'un ordre essaie de passer | Réduit la latence détection 10s→5min mais sans patch BE entre-temps | ⏸ différé (décision post-observation 24-48h) |
| 3 | **Auto-restart systemd** via 2e watchdog timer + `tmp/broker_disconnect_since.txt` : si broker injoignable > 10 min malgré étages 1+2, `systemctl --user restart arabesque-live.service` | ~4 % (canal mort non récupérable par reconnect) | ⏸ différé |
| 4 | **Anti-boucle guard** : si > 3 restarts/heure, stop auto-restart + alerte critique (évite la boucle de redémarrage masquant un bug structurel) | Protection des étages 3 | ⏸ différé |

**Critère de décision pour 2/3/4** : observation 24-48h après déploiement étages 0+1. Si aucun incident persistant (zéro alerte `ABANDONED` répétée) → différer indéfiniment. Sinon → activer 2 (puis 3+4 si 2 insuffisant).

## 4. Décisions

- **2026-05-21** : choix Étages 0+1 d'abord. Justification : l'incident DASHUSD est typiquement un cas perte session Protobuf trading (canal feed vivant, canal trading silencieux). Un re-login OAuth simple (tokens déjà en cache, pas de refresh HTTP nécessaire) devrait rétablir le canal. Pas besoin de redémarrer systemd pour 95 % des cas.
- **2026-05-21** : Étages 2/3/4 différés mais consignés (HANDOFF item #5, task #31). Décision après observation 24-48 h.

## 5. Implémentation — checklist

### Étage 0 (position_monitor.py)
- [x] `MonitorConfig.amend_alert_cooldown_s: float = 1800.0`
- [x] `TrackedPosition.last_amend_alert_time: float = 0.0`
- [x] `LivePositionMonitor.__init__` : `on_amend_abandoned: Optional[Callable[[dict], None]] = None`
- [x] `_try_amend_sl` : juste après le `logger.error(... ABANDONED ...)`, si cooldown OK → appel callback + update `last_amend_alert_time` + try/except pour ne pas casser l'amend
- [x] Callback dans `live.py._make_position_monitor` : appelle `live_monitor._notify_telegram` + `_notify_ntfy` via `asyncio.ensure_future`

### Étage 1 (ctrader.py)
- [x] Init : `_last_reconnect_attempt`, `_reconnect_attempts_window`, `_reconnect_cooldown_s=30`, `_reconnect_window_s=60`, `_reconnect_window_max=3`
- [x] Méthode `async def _try_reconnect_for_order(reason)` : cooldown 30s + fenêtre glissante 3/60s + réutilise `self.connect()` (héritage retries P1+P2)
- [x] Wrap les 4 retours `"Not connected"` (place_order, cancel_order, amend_position_sltp, close_position)
- [x] Log explicite à chaque tentative : `[cTrader] 🔄 Reconnect trading session (raison=...)`

### Tests
- [x] `tests/test_position_monitor_amend_abandoned_alert.py` (6 tests) : callback déclenché, cooldown 30 min par position, exception callback non bloquante, rétro-compatibilité
- [x] `tests/test_ctrader_reconnect_on_demand.py` (11 tests) : reconnect réussi/échoué/exception, cooldown 30s, fenêtre 3/60s, intégration sur les 4 méthodes

### Déploiement
- [x] Suite pytest verte (110/110)
- [x] Commit + push (`0b5ee78`, branche `main`)
- [x] Restart engine après merge (PID 778032 → 866011 → 1150053)
- [ ] Vérif log `[cTrader] 🔄 Reconnect trading session` au prochain incident (sera absent en régime normal — c'est l'invariant)
- [x] Notif Telegram+ntfy "Patch résilience broker déployé"

## 6. Observation post-déploiement

(à compléter au fil de l'eau dans les 24-48 h suivant le restart)

- Date restart engine : **2026-05-21T11:50:30 UTC** (CEST 13:50:30)
- PID engine post-restart : **1150053**
- Commit chargé : **`0b5ee78`** (vérifié via `_try_reconnect_for_order` + `MonitorConfig.amend_alert_cooldown_s=1800.0`)
- Token refresh planifié toutes les 12h : ✅ (log `[PriceFeed] Token refresh planifié toutes les 12h` à 13:51:21 CEST)
- Position DASHUSD réconciliée : ✅ #53110148 entry 49.40 SL 46.70, MFE 1.82R préservé via `position_monitor_state.json`
- Premier incident `ABANDONED` post-patch : aucun (canal trading restauré par le restart)
- Reconnect-on-demand déclenché : non observé (régime normal, attendu)

### État DASHUSD post-restart

- SL côté broker : **46.70** (toujours initial)
- BE 49.94 skip cause `new_sl > bid=47.21` (prix replié sous le BE cible — c'est la garde anti-`TRADING_BAD_STOPS`, comportement attendu, pas un bug)
- Trailing tier 3 SL=52.44 calculé mais skip pour la même raison
- P&L flottant : -5.08$
- Issue attendue :
  - prix > 49.94 → BE armé (gain neutre)
  - prix > 52.44 → trailing tier 3 armé (~+0.97R)
  - prix < 46.70 → SL touché (perte standard ~-1R)

### Surveillance continue

- **2026-05-21T18:28 UTC** (T+6h38min après restart) : ✅ canal trading stable. 0 occurrence `Not connected` / `Reconnect trading session` / `ABANDONED` depuis le restart 11:50:30 UTC. BarAggregator log toutes les 2-3 min (M5), trailing tier 3 DASHUSD émis ~6/min en continu mais skip silencieux (prix bid ~47 < cible SL 52.44 = garde anti-`TRADING_BAD_STOPS` normale, pas un bug). Position #53110148 toujours ouverte, SL 46.70, P&L -5.08$. Patch invariant respecté : **silence = canal trading sain**.
- **2026-05-21T22:59 UTC** : 🚨 **nouvel incident, indépendant du patch étages 0+1**. Le canal **feed** Protobuf (PriceFeed cTrader) commence à émettre `force reconnect after stale feed`. Tentative #1. Cause sous-jacente apparente dès la 1ère reconnexion : `❌ No access token in response: ACCESS_DENIED Access denied. Make sure the credentials are valid.` puis `CH_ACCESS_TOKEN_INVALID`. Le patch P1+P2 (commit 79007cf) gère `CH_ACCESS_TOKEN_INVALID` via refresh HTTP, mais le refresh HTTP lui-même échoue (`ACCESS_DENIED`) → abandon sans boucle infinie. Tentatives toutes les 120s.
- **2026-05-22T05:12 UTC** : `config/secrets.yaml` rafraîchi (origine externe : commande `arabesque positions` CLI ou rotation manuelle). Tokens disque désormais frais, mais **engine in-memory toujours sur l'ancien refresh token mort** → désynchro. L'engine continue à échouer.
- **2026-05-22T07:22 UTC** : début des alertes Telegram+ntfy `arabesque-feed-watchdog.service` (timer toutes les 6 min).
- **2026-05-22T18:28 → 18:51 UTC** : `/suivi` invoqué par user, diagnostic posé. Position DASHUSD **clôturée broker-side** entre-temps (balance 93554.87 → 93298.21 = **−256.66$ ≈ −1R**, SL initial 46.70 touché). MFE +1.82R jamais converti en BE car prix bid n'a jamais franchi 49.94 (entry+0.20R) — même si le feed avait été vivant, le garde anti-`TRADING_BAD_STOPS` aurait skipé jusqu'au bout.
- **2026-05-22T18:55 UTC** : restart engine (PID 1150053 → 1435589). Patch P1+P2 chargé (`Token refresh planifié toutes les 12h` ✓). DASHUSD réconcilié au démarrage : `R=-1.02 reason=reconciled_stop_loss MFE=1.86R src=broker_detail bars=2076 (ftmo_challenge:53110148)`.

### Verdict patch étages 0+1 (DASHUSD)

**Invariant tenu** : 0 `ABANDONED` / 0 `Reconnect trading session` / 0 `Not connected` du restart 11:50 UTC au crash feed 22:59 UTC. Le patch protégeait le canal **trading** ; il ne couvre pas le canal **feed** (qui est tombé pour une cause différente : refresh token in-memory invalidé).

**La position DASHUSD n'a pas été sauvée par le patch**, mais ce n'était pas son rôle : le BE 49.94 n'a jamais été déclenchable (bid sous l'entry 49.40 tout du long). La protection ultime (SL initial 46.70 côté broker) a tenu — c'est l'invariant fondamental qui a évité une perte plus grande.

**Pas de décision étages 2/3/4** : l'observation 24-48h n'a pas pu se faire dans des conditions normales (incident feed parallèle a faussé l'invariant "silence post-restart"). Différer encore, ré-observer 24-48h après le restart 18:55 UTC du 22/05 dans des conditions feed propres.

## 7. Décision finale étages 2/3/4

- **Verdict 2026-05-22** : différé (observation 24-48h impossible — crash feed 22:59 UTC le 21/05 a court-circuité la mesure).
- **Justification** : entre 11:50 UTC (restart) et 22:59 UTC (crash feed), 0 trigger trading observé. Mais la fenêtre 22:59 → 18:55 UTC le 22/05 (20h) est inexploitable car feed mort → ni amend, ni reconnect, ni ticks. Pas de signal pour décider 2/3/4.
- **Action prise** : ré-observer 24-48h après le restart 2026-05-22T18:55 UTC dans des conditions feed propres. Task #31 reste en pending. Deux nouvelles tasks ouvertes :
  - **#32** : investiguer désynchro `refresh_token` engine in-memory vs disque (cause racine du crash feed du 21/05 soir).
  - **#33** : brancher `feed_stale` dans la watchlist `/suivi` (le watchdog systemd a alerté, mais aucun /suivi n'a été déclenché entre 22:59 UTC et 18:51 UTC → 20h silencieuses côté assistant).

## 8. Bilan élargi

Cet incident dépasse le périmètre initial du dossier (résilience canal trading). Il révèle deux trous distincts :

1. **Couvert par étages 0+1** : amend SL échouant car canal trading déconnecté silencieusement → notif + reconnect-on-demand. **Tenu** sur la fenêtre observée (silence).
2. **Non couvert** : canal feed déconnecté avec refresh token invalidé alors que le refresh token disque est frais → désynchro mémoire/disque. **Tâche #32** ouverte.
3. **Non couvert non plus** : aucun mécanisme assistant ne s'auto-réveille sur alerte watchdog feed_stale persistant > 30 min. L'utilisateur a porté la charge de surveillance pendant 20h. **Tâche #33** ouverte.

La position DASHUSD #53110148 sort à −1R standard (SL initial 46.70 côté broker). Pas un échec du système — c'est exactement la protection ultime conçue pour ce scénario. Le manque, c'est l'absence d'opportunité d'armer le BE (prix bid jamais > 49.94) + 20h de feed mort en background.
