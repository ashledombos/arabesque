# Hot Path Mode — Surveillance active des positions ouvertes

**Date d'ouverture** : 2026-05-23
**Statut global** : 🛠️ en cours (étape 1 démarrée)
**Motivation** : incident DASHUSD #53110148 du 2026-05-20→22 (cf. `docs/audit/INCIDENT_DASHUSD_RESILIENCE_BROKER_2026-05-21.md`). Une position ouverte a été fermée broker-side sans que l'engine le sache pendant ~16h, après 19h54 de feed mort cumulées sur la période. Le MFE +1.82R n'a pas pu être protégé (BE non armable, mais surtout silence total côté assistant).

Ce document trace la conception, l'implémentation et la validation du **Hot Path Mode** : un mode de surveillance intensive activé dès qu'au moins une position est ouverte. Il complète mais ne remplace pas les patches déjà en place (étages 0+1 du dossier DASHUSD, tasks #32/#33).

---

## 1. Constat de départ — 3 canaux à surveiller, 1 seul l'était

| # | Canal | Aujourd'hui (mode unique) | Hot Path Mode (≥1 position) |
|---|---|---|---|
| 1 | Feed Protobuf (ticks) | `_watch_connection` détecte stale > 5 min (majeur) ou 30 min (mineur) | Seuil 120s sur l'instrument *exact* de la position |
| 2 | Trading Protobuf (amend/close) | Détection seulement quand un amend échoue (réactif) | `ReconcileReq` actif toutes les 60s (proactif) |
| 3 | État broker (positions toujours là ?) | Reconcile au démarrage + à chaque reconnect seulement | Reconcile toutes les 60s. Détection "position absente côté broker" → alerte URGENT |

Le canal **#3** est **complètement aveugle** aujourd'hui en régime normal — c'est ce qui a permis 16h de silence sur DASHUSD entre la fermeture broker-side (SL touché) et le restart engine.

---

## 2. Architecture cible — 4 mécanismes orthogonaux

### Mécanisme A — Heartbeat broker via ReconcileReq (60s)

Polling périodique `ProtoOAReconcileReq` qui sert **3 fonctions à la fois** :

1. **Test du canal trading** : si timeout 10s → canal mort, déclenche `_try_reconnect_for_order` (étage 1 existant)
2. **Position-existence check** : si position locale absente côté broker → fermée à notre insu → alerte URGENT immédiate (cooldown 0)
3. **SL-divergence check** *(étape 3)* : si SL côté serveur ≠ SL côté engine → amend silently dropped → alerte WARNING + retry amend

Coût : ~60 requêtes/h supplémentaires côté cTrader, très en deçà des limites (typiquement 5/s autorisés).

### Mécanisme B — Seuils feed durcis en mode hot

Le `feed_watchdog` durcit ses seuils quand position ouverte :
- Stale threshold : **120s** (vs 300s majeur / 1800s mineur)
- Cible : l'instrument *exact* de la position (au lieu de BTC/ETH génériques)
- Auto-restart : **T+10 min** (vs T+30 min)
- Anti-boucle : **5 restarts/h** (vs 2) — quand position ouverte, mieux vaut restart agressif

### Mécanisme C — Skip weekend conditionné

Le `feed_watchdog.py:258` skip TOUS les checks en weekend. En Hot Path, le skip est conditionné à `len(open_positions) == 0`. Une position qui traverse le weekend → surveillance normale + auto-restart possible.

Note : crypto cTrader fermé en weekend = `ReconcileReq` répond quand même (broker online, feed quote fermé). On garde la veille sur canaux #2 et #3 même sans tick #1. Sessions cTrader weekend acceptées mais erratiques (login/reconnect intermittents) → auto-restart désactivé en weekend même si feed_stale dépasse le seuil.

### Mécanisme D — Cooldowns adaptés

| Type alerte | Mode idle | Mode hot |
|---|---|---|
| `ABANDONED` (amend echoue) | 30 min/position | 5 min/position |
| `position absente broker` | n/a | **0 cooldown** (urgent immédiat) |
| `canal trading timeout > 30s` | n/a | **0 cooldown** |
| `feed_stale persistant` | 1h (cf. task #33) | 1h (inchangé) |

---

## 3. Source de vérité "y a-t-il une position ouverte ?"

`arabesque/execution/position_monitor.py` persiste déjà `logs/position_monitor_state.json` à chaque modification de `_tracked_positions`. Tous les composants (watchdog, suivi_reminder, reconcile-actif) lisent ce fichier — pas de pingue broker pour la simple question "y a-t-il une position ?".

Format attendu : liste de `TrackedPosition` sérialisées avec `broker_id`, `position_id`, `symbol`, `side`, `entry`, `sl`, `tp`, `mfe_r`. Suffisant pour les besoins du Hot Path.

---

## 4. Plan d'implémentation graduel

Chaque étape est une PR indépendante, testable séparément, déployable sans dépendre des suivantes.

| Étape | Task | Périmètre | Statut |
|---|---|---|---|
| 1 | #35 | Heartbeat ReconcileReq 60s + détection "position absente broker" → alerte URGENT | ✅ livré 2026-05-23 (activé en prod 18:12 CEST) |
| 2 | #36 | Skip weekend conditionné à 0 position dans `feed_watchdog` | ✅ livré 2026-05-23 |
| 3 | #37 | SL-divergence check via ReconcileReq | ⏸ après #35 |
| 4 | #38 | Seuils feed durcis + cooldowns adaptés en mode hot | ⏸ après #35-#37 |
| 5 | #39 | Escalator restart sur canal trading cTrader mort (≥2 reconnect échec en 5 min) | ⏸ après #37+#38 + observation #35 |

---

## 5. Étape 1 — détails d'implémentation

### Périmètre

Touche 2 fichiers :
- `arabesque/broker/ctrader.py` : exposer `list_open_positions_proto()` qui wrap `ProtoOAReconcileReq` (déjà présent ligne 1841) en méthode publique propre avec timeout 10s
- `arabesque/execution/position_monitor.py` : ajouter une task `_broker_reconcile_loop()` démarrée quand `_tracked_positions` devient non vide, arrêtée quand vide

Plus 1 fichier de tests :
- `tests/test_position_monitor_broker_reconcile.py`

### Invariants à verrouiller (tests TDD)

1. Polling **inactif** quand `_tracked_positions` est vide (pas de requête broker inutile)
2. Polling **actif** dès qu'une position est enregistrée via `register_position()`
3. Polling **s'arrête** quand la dernière position est retirée
4. Intervalle = 60s entre 2 ReconcileReq successifs
5. Timeout 10s sur ReconcileReq → log WARNING + retry au prochain cycle (pas de bloquage)
6. 3 timeouts consécutifs → déclenche `_try_reconnect_for_order(reason="reconcile_timeout")`
7. Position locale présente, broker répond avec liste où elle est **absente** → alerte URGENT immédiate (cooldown 0) avec body explicite : `position_id`, `symbol`, `entry`, `sl`, `mfe_r au moment de la disparition`
8. Position retrouvée broker → reset compteur "missing", pas de nouvelle alerte
9. Position locale absente, broker la connaît → log INFO seulement (cas rare : reconcile arrière)
10. Pas de spam : si position absente détectée → marque la position pour exit reconciliation puis retire du tracking (mécanisme `missing_cycles` déjà présent ligne 61 de `position_monitor.py`)

### Risques identifiés

- **Faux positif "position absente"** si latence ReconcileReq : mitigation = 3 cycles consécutifs (= ~3 min) avant alerte URGENT, et exploit le compteur `missing_cycles` déjà existant
- **Polling pendant amend en cours** : risque de race condition sur `_amend_in_progress`. Mitigation = le ReconcileReq est read-only côté broker, ne touche pas aux positions locales avant la fin de l'amend
- **Polling pendant reconnect** : si le canal trading est en reconnect, ReconcileReq doit échouer proprement (timeout) puis retry au cycle suivant
- **Surcoût notification** : alerte URGENT à chaque ReconcileReq raté = spam. Mitigation = cooldown 5 min sur les WARNING répétés du *même type* (mais pas 0 sur URGENT)

---

## 6. Log d'avancement

### 2026-05-23

- **09:30 UTC** — Cadre posé après revue du dossier DASHUSD. 4 tâches #35-#38 créées. Décision : commencer par #35 (Hot Path #1), car c'est le mécanisme qui aurait sauvé la visibilité sur DASHUSD entre 18h et 18:55 UTC le 22/05.
- **10:45 UTC** — Étape 1 implémentée (TDD, red→green). 12 nouveaux tests `tests/test_position_monitor_broker_reconcile.py` couvrent les 10 invariants + 2 bonus (mixed positions, no-double-alert). Modifications :
  - `arabesque/broker/ctrader.py` : nouvelle méthode publique `list_open_positions_proto(timeout_s=10.0)` qui distingue `None` (timeout/disconnect) de `[]` (broker répond, 0 position). Réutilise `ProtoOAReconcileReq` mais sans bloquer 15s.
  - `arabesque/execution/position_monitor.py` :
    - `MonitorConfig` : 4 nouveaux champs (`broker_reconcile_enabled` désactivé par défaut, `broker_reconcile_interval_s=60`, `broker_reconcile_timeout_s=10`, `broker_reconcile_missing_threshold=3`).
    - `TrackedPosition.broker_missing_cycles` (compteur dédié, distinct de `missing_cycles` consommé par `reconcile()` 120s).
    - `LivePositionMonitor.__init__` : nouveau callback `on_position_missing_broker`.
    - Nouvelles méthodes : `_maybe_start_broker_reconcile`, `stop_broker_reconcile`, `_broker_reconcile_loop`, `_broker_reconcile_pass`, `_emit_position_missing_broker`. Boucle auto-démarrée par `register_position()` (idempotent), auto-stoppée quand `_positions` redevient vide.
  - `arabesque/execution/live.py` : wiring callback `on_position_missing_broker` qui notifie Telegram+ntfy + lecture config `live.broker_reconcile_*`. Ajout de `stop_broker_reconcile()` dans `stop()`.
  - `config/settings.yaml` : section commentée `broker_reconcile_active: false` (à passer à `true` après validation manuelle 24h, comme pour le BE polling).
  - 178/178 tests verts (12 nouveaux + 166 existants).
- **Validation requise avant activation** : (a) revoir le wiring sur un fork dev pendant une session de marché ouvert ; (b) confirmer que le log INFO `[Monitor] 🩺 reconcile broker actif` apparaît bien dès une 1ʳᵉ position ; (c) confirmer que le log INFO `[Monitor] 🩺 reconcile broker arrêté (plus de positions)` tombe à la fermeture de la dernière position. Une fois ces 3 invariants observés, basculer `broker_reconcile_active: true` et restart engine.
- **15:30 UTC — Étape 2 livrée (TDD red→green)**. Skip weekend dans `feed_watchdog` désormais conditionné à `_open_positions_count() == 0`. Le watchdog lit `logs/position_monitor_state.json` (state file partagé écrit immédiatement par `LivePositionMonitor.register_position()` / `unregister_position()` — pré-requis livré aussi, 5 tests dans `tests/test_position_monitor_state_persistence.py`). Modifications :
  - `scripts/feed_watchdog.py` : nouvelle constante `POSITIONS_STATE`, helper `_open_positions_count()` fail-safe (retourne 0 si absent/corrompu), branche weekend de `main()` réécrite : 0 position → comportement historique (`weekend_guard`, skip total) ; ≥ 1 position → flag `weekend_with_positions=True`, checks BarAggregator/alertes normaux. Statut écrit `weekend_guard_with_positions:ok age=Ns open=N` quand OK.
- **16:10 UTC — Étape 2 BIS : refinement backoff (correction user)**. Initial design désactivait totalement l'auto-restart en weekend+position. User a corrigé : cTrader accepte les sessions weekend, juste erratiques. On garde le filet du restart mais avec **backoff progressif**. Modifications :
  - `scripts/feed_watchdog.py` : nouvelles constantes `WEEKEND_BACKOFF_THRESHOLDS_MIN=[30,60,120,240]`, `WEEKEND_RESTART_MAX_24H=4`, `WEEKEND_BACKOFF_WINDOW_S=86400`. Helper `_recent_weekend_restart_count(now, 24h)` filtre par tag `weekend=True` dans `RESTART_HISTORY`. `_attempt_auto_restart` et `_append_restart_history` acceptent un kwarg `weekend=False`. Branche weekend de `main()` : si N≥4 → anti-boucle URGENT distincte (outcome `skipped_weekend_backoff`) ; sinon seuil persistance = `WEEKEND_BACKOFF_THRESHOLDS_MIN[N]` min ; restart fire avec tag `weekend=True`.
  - `tests/test_feed_watchdog_weekend_backoff.py` (nouveau, 15 tests) : invariants courbe backoff, cap 24h, exclusion entrées weekday, fenêtre 24h glissante, comptage failed restarts. Test 7 du fichier `test_feed_watchdog_weekend_with_positions.py` réécrit (de "no autorestart" à "1er restart au seuil standard").
  - **Schéma final** : 30 → 60 → 120 → 240 min (cap), puis anti-boucle URGENT. Cumulé ~7h de surveillance active avant escalade humaine. Backoff calculé sur 24h glissantes → relance naturelle après ~1 jour sans incident.
  - `arabesque/execution/position_monitor.py` : persistance immédiate déjà en place via `save_state()` appelé dans `register_position()` / `unregister_position()` (sémantique "vide = absent" : fichier supprimé quand 0 position).
  - **38 tests verts cumulés** : 8 Hot Path #2 (skip conditionnel) + 15 Hot Path #2 bis (backoff weekend) + 5 persistance state file + 11 non-régression autorestart. **205/205 suite globale verte.** (Post-review neutre 18:00 UTC : +2 tests end-to-end propagation tag weekend + isolation compteur weekday → **207/207** suite globale finale ; +1 fix `_recent_restart_count` filtre `weekend=True`.)
  - **Pas de config flag** : Hot Path #2 + bis actifs immédiatement (pas de risque — comportement strictement plus surveillant que l'ancien, jamais moins ; backoff plus conservateur que la cadence weekday).
- **Validation post-déploiement** : (a) observer une position qui traverse vendredi 21:00 UTC → confirmer que le watchdog continue d'émettre `last_status=weekend_guard_with_positions:...` au lieu de `weekend_guard` ; (b) à la fermeture broker-side de la position, confirmer que le cycle suivant repasse à `weekend_guard` (sémantique fichier absent = 0 position) ; (c) si feed_stale est détecté en weekend avec position, confirmer alerte sans auto-restart.
- **18:30 UTC — Décision : pas d'auto-restart sur trigger "position absente broker"**. Question user : faut-il restart le service quand 3 cycles consécutifs ReconcileReq détectent une position absente ? **Rejeté** : (a) 3 réponses ReconcileReq reçues prouvent que le canal trading fonctionne ; (b) la position est vraiment partie côté broker, le restart ne la récupère pas ; (c) le tracking local est déjà nettoyé par `_emit_position_missing_broker` ; (d) le restart est disruptif pour les autres aggregators et positions sur l'autre broker ; (e) cas TP touché → notif "restart auto" sur succès induirait l'humain en erreur. **Acté à la place** : task #39 (Hot Path #5) — escalator restart sur `_try_reconnect_for_order` échouant ≥2 fois en 5 min (= canal trading mort). C'est là que le restart répare quelque chose. Mécanisme : marqueur dans `feed_watchdog_state.json` que le watchdog consomme à l'itération suivante, avec cooldown 10 min. **Implémentation différée** après observation 24-48h de #35 + livraison #37/#38 (cohérence avec feedback_phase4_focus : focus stabilisation).
- **18:15 UTC — Task #40 livrée : 4 patches hardening watchdog post-revue neutre**. Ordonnancée AVANT #37/#38/#39 car le patch #1 corrige une régression de sécurité vs incident DASHUSD (fail-silent → hot path skip pile quand il faut surveiller). Modifications `scripts/feed_watchdog.py` :
  - **Patch #1 — fail-loud `POSITIONS_STATE` corrompu**. Signature `_open_positions_count() -> int` devient `tuple[int, bool]` (count, corrupted). Caller (branche weekend de `main()`) bascule en hot path présumé + notif URGENT au lieu de skip silencieux. État écrit : `positions_state_corrupted=True`. 11 tests dans `tests/test_feed_watchdog_positions_state_failloud.py` (absent, valide, parse error, non-dict variants, intégration weekend + corrompu, non-régression weekend + absent). Test existant `test_open_positions_count_failsafe_on_corrupted_file` renommé/adapté en `_failloud_`.
  - **Patch #2 — timeout subprocess**. `_engine_active` (timeout=5s) et `_last_bar_age_seconds` (timeout=10s). Fail-safe sur `TimeoutExpired` : `False` resp. `None` (branches existantes sans danger : `engine_inactive` resp. `no_bar_data_in_window`). Log WARNING stderr. Évite que le watchdog se bloque indéfiniment si systemd/journalctl freezent. 6 tests dans `tests/test_feed_watchdog_subprocess_timeout.py`.
  - **Patch #3 — détection monitor mort silencieusement**. Nouveau helper `_positions_state_age_seconds(now)` + constante `POSITIONS_STATE_STALE_S=600` (10 min). En weekend avec count > 0, si mtime > seuil → flag `positions_state_stale=True` + notif URGENT. Pas de changement de comportement décisionnel (le mode hot est déjà actif via count > 0). 6 tests dans `tests/test_feed_watchdog_positions_state_mtime.py`. **⚠️ HOTFIX 22:15 UTC ci-dessous — ce patch a été retiré le soir même (spam 8 notifs en 3h).**
  - **Patch #4 — `_write_state` atomique**. Pattern `tmp file + os.replace` (atomique POSIX). Évite JSON tronqué sur SIGKILL/OOM/disque plein qui ferait perdre `last_alert_ts`, `feed_stale_since_ts`, etc. Ajout `import os`. 5 tests dans `tests/test_feed_watchdog_atomic_write.py`.
  - **28 nouveaux tests TDD + 244/244 suite globale verte**. Aucune régression. Fichier production : ~95 lignes ajoutées sur `feed_watchdog.py`.
  - **Suite #37/#38/#39** : poursuite normale, le socle est désormais robuste pour accueillir SL-divergence (#37) et seuils durcis (#38) qui vont densifier le state file et les subprocess calls.
- **22:15 UTC — Hotfix : retrait du patch #3 (mtime check) — leçon apprise**. Symptôme : 8 notifs URGENT « monitor positions fige » envoyées entre 21:11 et 00:05 UTC sur Telegram+ntfy, toutes liées à la même position XAUUSD FTMO #52759859 ouverte à 20:27 UTC (registered_at = 1779560827). Cause racine : hypothèse fausse sur la cadence de `LivePositionMonitor.save_state` — vérification du code (`arabesque/execution/position_monitor.py:316`, `:336`, `:1402`) confirme que `save_state` n'est appelé que sur `register_position`, `unregister_position` et le checkpoint de fin de `reconcile()`. En weekend avec position dormante et reconcile qui ne déclenche rien (pas d'amend, pas de changement broker-side), le fichier garde le mtime de l'ouverture. Mon seuil 10 min était structurellement battu dès la 11e minute. Modification : bloc `age_s_state = _positions_state_age_seconds(now) ...` retiré entièrement (lignes 461-483 du commit f65fbd4). `state.pop("positions_state_stale", None)` conservé pour idempotence (nettoie un ancien flag). Test correspondant (`test_weekend_stale_state_flags_and_alerts`) transformé en test de non-régression (`test_weekend_old_state_does_not_flag_after_hotfix`) qui vérifie que le flag n'est plus posé et qu'aucune notif `monitor fige`/`stale` n'est émise. 36/36 tests `feed_watchdog` verts. Task #41 créée pour ré-instrumenter la détection « monitor mort » via un mécanisme valide (heartbeat dédié, ou checkpoint inconditionnel toutes les N min côté monitor). **Leçon** : un seuil basé sur mtime ne vaut que si l'écriture est garantie périodique, **indépendamment** de l'activité tracée. C'était l'inverse ici. Bonus : test = co-régulateur — si j'avais écrit un test « count > 0, weekend, aucune activité broker pendant 2h → pas de flag », j'aurais vu le bug avant de pousser.
- **(à compléter au fur et à mesure)**

---

## 7. Validation post-déploiement

À remplir après chaque PR mergée et restart engine :

| Étape | Date restart | PID engine | Vérif log invariant | Notif Telegram |
|---|---|---|---|---|
| #35 | — | — | `[Monitor] 🩺 reconcile broker actif` (au start si position ouverte) ; silence sinon | — |
| #36 | — | — | `feed_watchdog_state.json` : `last_status=weekend_guard` quand 0 position vs `weekend_guard_with_positions:ok age=Ns open=N` quand ≥1 position ouverte | — |
| #37 | — | — | (silence en régime normal) | — |
| #38 | — | — | `[Monitor] hot path thresholds engaged` au registre position | — |
