# Hot Path Mode — Surveillance active des positions ouvertes

**Date d'ouverture** : 2026-05-23
**Statut global** : 🛠️ en cours (étape 1 démarrée)
**Motivation** : incident DASHUSD #53110148 du 2026-05-20→22 (cf. `docs/INCIDENT_DASHUSD_RESILIENCE_BROKER_2026-05-21.md`). Une position ouverte a été fermée broker-side sans que l'engine le sache pendant ~16h, après 19h54 de feed mort cumulées sur la période. Le MFE +1.82R n'a pas pu être protégé (BE non armable, mais surtout silence total côté assistant).

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

Note : crypto cTrader fermé en weekend = `ReconcileReq` répond quand même (broker online, feed quote fermé). On garde la veille sur canaux #2 et #3 même sans tick #1.

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
| 1 | #35 | Heartbeat ReconcileReq 60s + détection "position absente broker" → alerte URGENT | 🛠️ en cours (2026-05-23) |
| 2 | #36 | Skip weekend conditionné à 0 position dans `feed_watchdog` | ⏸ après #35 |
| 3 | #37 | SL-divergence check via ReconcileReq | ⏸ après #35 |
| 4 | #38 | Seuils feed durcis + cooldowns adaptés en mode hot | ⏸ après #35-#37 |

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
- **(à compléter au fur et à mesure)**

---

## 7. Validation post-déploiement

À remplir après chaque PR mergée et restart engine :

| Étape | Date restart | PID engine | Vérif log invariant | Notif Telegram |
|---|---|---|---|---|
| #35 | — | — | `[Monitor] 🩺 reconcile broker actif` (au start si position ouverte) ; silence sinon | — |
| #36 | — | — | `[Watchdog] weekend_guard skipped (1 open position)` ou `weekend_guard active (0 positions)` | — |
| #37 | — | — | (silence en régime normal) | — |
| #38 | — | — | `[Monitor] hot path thresholds engaged` au registre position | — |
