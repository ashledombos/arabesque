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
- [ ] Commit + push
- [ ] Restart engine après merge
- [ ] Vérif log `[cTrader] 🔄 Reconnect trading session` au prochain incident (sera absent en régime normal)
- [ ] Notif Telegram+ntfy "Patch résilience broker déployé"

## 6. Observation post-déploiement

(à compléter au fil de l'eau dans les 24-48 h suivant le restart)

- Date restart engine :
- PID engine post-restart :
- Premier incident `ABANDONED` post-patch :
- Reconnect tenté ? Réussi ? Latence ?
- Position DASHUSD : SL aligné ? Sortie ?

## 7. Décision finale étages 2/3/4

(à compléter après 24-48 h d'observation)

- Verdict :
- Justification :
- Action prise :
