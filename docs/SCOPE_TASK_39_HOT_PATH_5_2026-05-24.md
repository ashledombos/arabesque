# Scope Task #39 — Hot Path #5 : Escalator restart canal trading cTrader mort

**Date** : 2026-05-24 (bilan semaine, post-vérif logs DASHUSD)
**Statut** : scope posé, implémentation à faire 2026-05-25
**Priorité** : critique (cf. mémoire `project_task39_priority_critical.md`)

## Le problème — confirmé par DASHUSD 2026-05-21

Engine vivant, BE polling backup détecte MFE>=0.3R, lance amend SL. Canal trading cTrader (`_connected=False` côté Protobuf), feed quote toujours vivant. 3256 lignes `amend ABANDONED [Not connected]` entre 21-05 01:42 et 07:48 UTC. SL physique broker jamais déplacé à BE. Position termine en faux -1R alors que MFE max = 1.86R.

## Ce qui est déjà en place

| Étage | Description | File:line | Statut |
|---|---|---|---|
| 0 | Notif Telegram URGENT sur chaque `ABANDONED amend` (cooldown 30min/position) | `arabesque/execution/live.py:493-517` | ✅ commit `0b5ee78` |
| 1 | `_try_reconnect_for_order()` appelé avant chaque retour `Not connected`, anti-boucle 30s cooldown + 3/60s | `arabesque/broker/ctrader.py:790-795` | ✅ commit `0b5ee78` |
| 1 bis | Compteur d'échecs reconnect `_reconnect_failures_window` + API read-only `recent_reconnect_failures_count(window_s)` | `arabesque/broker/ctrader.py:184,797-814` | ✅ pré-câblage, jamais consommé |
| 3 feed | Auto-restart `feed_stale` persistant >30min (Étage 3 watchdog) | `scripts/feed_watchdog.py:50` | ✅ (sur trigger feed, pas trading) |
| 4 | Anti-boucle 2 restarts/h max | `scripts/feed_watchdog.py:51` | ✅ |

## Ce qu'il reste à faire = Task #39

**Trigger nouveau** : auto-restart engine quand `recent_reconnect_failures_count(window_s=300) >= 2` ET ≥1 position broker ouverte.

### Difficulté de transport (2 process distincts)

Le compteur vit dans le process `arabesque-live.service` (broker cTrader). Le watchdog vit dans `arabesque-feed-watchdog.timer` (process séparé). Il faut un canal disque.

### Couche 2a — Heartbeat trading channel (engine writer)

**Nouveau fichier** : `logs/ctrader_trading_health.json` (gitignored)
**Écrit par** : `arabesque/broker/ctrader.py` — nouvelle méthode `_dump_trading_health()` appelée :
- toutes les 60s par un timer asyncio (cf. pattern `_token_refresh_loop`)
- immédiatement après chaque `_try_reconnect_for_order()` (succès ou échec)

**Format** :
```json
{
  "ts": "2026-05-25T08:00:00+00:00",
  "broker_id": "ftmo_challenge",
  "connected": true,
  "reconnect_failures_5min": 0,
  "reconnect_failures_total": 12,
  "last_amend_abandoned_ts": "2026-05-21T07:48:17+00:00"
}
```

Écriture atomique via `os.replace(tmp, final)` (cf. patch #4 task #40, déjà éprouvé).

### Couche 2b — Trigger watchdog (consumer)

**Modifier** : `scripts/feed_watchdog.py`
- Ajouter constante `TRADING_HEALTH = ROOT / "logs" / "ctrader_trading_health.json"`
- Nouveau check `_trading_channel_dead(state)` qui lit le fichier et retourne True si :
  - `reconnect_failures_5min >= 2`
  - ET `connected == False`
  - ET fichier `mtime` récent (<3min — sinon writer mort, autre problème, escalader vers Task #41)
  - ET ≥1 position dans `position_monitor_state.json`
- Si trigger : poser état `last_status="trading_channel_dead"` dans `feed_watchdog_state.json` + alerte Telegram URGENT
- Persistance ≥10min (2 cycles consécutifs) avant déclencher restart, pour éviter glitch

### Couche 3 — Restart (reuse existant)

Reuse `_perform_restart()` existant dans `feed_watchdog.py` :
- Notif Telegram pré-restart 60s grâce manuelle (déjà fait pour feed_stale)
- Anti-boucle 2 restarts/h max (déjà fait, partagé entre triggers feed_stale et trading_channel_dead)
- Cooldown 10min entre 2 restarts trading (séparé du cooldown feed)

### Tests

Nouveaux fichiers :
- `tests/test_ctrader_trading_health_writer.py` — vérifie format JSON, écriture atomique, timer 60s, écriture post-reconnect
- `tests/test_feed_watchdog_trading_channel_restart.py` — fixtures `ctrader_trading_health.json` avec différents états, vérifie déclenchement + non-déclenchement + anti-boucle partagé

### Hors scope #39 (à acter)

- **Task #41** (réinstrumenter détection LivePositionMonitor mort sans mtime) : peut **réutiliser le mécanisme heartbeat fichier** introduit par #39. À traiter dans la foulée.
- **Pas de restart en weekend guard** (vendredi 21:00 UTC → dimanche 22:00 UTC) — sinon on entre dans le pattern erratique cTrader weekend (cf. `project_ctrader_weekend_sessions.md`).
- **Pas de restart si 0 position ouverte** — si le canal trading est mort sans position, c'est gênant mais pas critique. Le restart au prochain signal de trade (re-login) suffit.

## Estimation

- Couche 2a writer : ~1h (avec tests)
- Couche 2b watchdog : ~1h30 (avec tests intégration `_perform_restart` partagé)
- Validation + commit + push : ~30min
- **Total ~3h** (fenêtre calme journée, à faire feed actif pour vérifier non-régression)

## Critère de succès

Test pytest : simuler 3 amend ABANDONED consécutifs + 1 position ouverte → 2 cycles watchdog plus tard, `subprocess.run(["systemctl", "--user", "restart", "arabesque-live.service"])` appelé. Persistance file `watchdog_restart_history.jsonl` mentionne `reason="trading_channel_dead"`.

Test live : restart manuel engine après code livré, vérifier `logs/ctrader_trading_health.json` créé et mis à jour toutes les 60s avec `connected=True, reconnect_failures_5min=0`.
