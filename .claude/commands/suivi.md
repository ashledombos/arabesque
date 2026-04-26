---
description: Check-up rapide du système Arabesque — health checks, watchlist, actions auto si seuils atteints. "Coucou tout va bien" si rien à faire.
argument-hint: "[verbose|silent|notify]"
---

# /suivi — check-up et optimisation continue

Mode demandé : **$ARGUMENTS** (par défaut : check normal).

## Objectif

Une commande légère, à invoquer à n'importe quelle fréquence. Sur chaque appel :
1. Mesure le délai depuis le dernier passage (sans jugement)
2. Health checks rapides (engine, brokers, protection, orphelines)
3. Évalue la **watchlist** (seuils quantifiables) — agit si trigger atteint
4. **Auto-déclenche `/bilan`** si on est dans une fenêtre bilan (cf. §4)
5. Repère les actions de la TODO `HANDOFF.md` qui peuvent être faites maintenant
6. **Envoie un résumé humain** sur Telegram+ntfy (sauf mode `silent`)
7. **Planifie le prochain passage** (`next_expected_in_hours`) — un timer systemd user (`arabesque-suivi-reminder.timer`, persistent, résiste au reboot) ping ntfy+Telegram si retard
8. Sortie courte. Si rien à faire : **🟢 Coucou, tout va bien.**

Modes :
- (vide) ou `verbose` → output complet + notif résumé
- `silent` → output minimal (1-2 lignes), pas de notif
- `notify` → force la notif même si rien (utile pour test)
- `no-bilan` → désactive l'auto-bilan pour ce passage

## Étapes

### 1. État du dernier passage

Lis la dernière ligne de `logs/maintenance_state.jsonl` (gitignored, créé si absent) :
- Calcule `delay_h = now - last_ts` (heures)
- Note si `delay_h > 168` (> 7j) — pas une faute, juste **« Dernier suivi : il y a Xj »**
- Si premier passage, dis-le.

### 2. Health checks (chacun ≤ 5s)

a. **Engine actif** : `systemctl --user is-active arabesque-live.service` doit valoir `active`.
   - Si `inactive` ou `failed` → alerte critique, ne pas auto-restart sans investigation.
b. **Engine vivant** : `journalctl --user -u arabesque-live.service --since "10 minutes ago" | grep -c "BarAggregator"` ≥ 1.
   - Si 0 ligne → engine peut être bloqué (rare).
c. **Brokers connectés + 0 orphelines** :
   - `python -m arabesque positions --account ftmo_challenge` → 0 positions inattendues
   - `python -m arabesque positions --account gft_compte1` → idem
   - Une "orpheline" = position sans `entry` correspondant dans `logs/trade_journal.jsonl` (compare position_id).
d. **Protection NORMAL** : dernière ligne par broker dans `logs/equity_snapshots.jsonl` → `protection_level == "normal"`.
e. **Pas de phantom fallback récent** : grep `phantom_fallback` ou `orphan_cleanup` dans `logs/trade_journal.jsonl` sur 24h.

### 3. Watchlist — évaluation

Pour chaque item, calcule la métrique à partir de `logs/trade_journal.jsonl` (filtre par strategy×broker depuis `2026-03-22`, événement `exit`, `result_r`). Critère "WR" inclut BE (`-0.25 ≤ result_r ≤ 0.25` compté comme half-win).

| ID | Trigger | Action si atteint |
|---|---|---|
| `glissade_gft_block` | Glissade GFT : `n ≥ 8` ET `WR < 30%` | Ajouter `glissade: [gft_compte1]` dans `config/settings.yaml → strategy_broker_exclusions`. Append entry DECISIONS.md. Notif Telegram. |
| `cabriole_gft_unblock` | Cabriole FTMO : `n ≥ 20` ET `WR ≥ 70%` | Retirer `cabriole: gft_compte1`. Append DECISIONS.md. Notif Telegram. |
| `extension_gft_drift` | Extension : `n_gft ≥ 10` ET `WR_ftmo - WR_gft > 50pp` | Proposer block (ne pas auto-appliquer, demander validation). |
| `phantom_exit_alert` | Nouvel `orphan_cleanup` OU `phantom_fallback=true` dans les dernières 24h | Investiguer (lire les events), composer alerte Telegram. |
| `engine_uptime_drop` | Engine uptime < 30min OU > 5min sans BarAggregator log | Alerte. Ne pas auto-restart (peut être en cours). |
| `dd_proximity` | DD broker > 70% du seuil CAUTION (-7%) → ex: DD < -4.9% | Alerte proactive. |
| `live_vs_backtest_drift` | Lance `python scripts/compare_live_vs_backtest.py --last 30` (ou `--period 3m`). Si pour une stratégie `Δ_WR < -15pp ET n ≥ 30`, ou `Wilson IC95_high(live) < WR_baseline - 5pp` | Alerte Telegram+ntfy avec stratégie + delta. Note dans HANDOFF "Action à considérer". Pas d'auto-désactivation (zone validation user). |
| `cross_broker_divergence` | Aggrège `logs/trade_journal.jsonl` exits par `(strategy, instrument, entry_ts ±5min)` pour paires FTMO/GFT. Si `mean(\|Δ_R\|) > 0.40` sur `n ≥ 5` paires OU `n_inversions ≥ 3` sur `n ≥ 10` paires | Alerte avec broker incriminé. Si pattern net (un broker mange l'edge), suggérer entry dans `strategy_broker_exclusions` (proposer, ne pas auto-appliquer). |
| `stale_bilan` | Pas de modif `logs/journal/YYYY-MM.md` depuis ≥ 8j | Note suggestion : « envisage `/bilan` ». Pas d'action auto. |

**Avant d'auto-appliquer une action** : vérifie que le critère est strictement satisfait (n et WR). En cas de doute (≤ 1 trade de la limite), passe en mode "Proposer" plutôt que "Appliquer".

### 4. Auto-déclenchement `/bilan` (si fenêtre)

Avant de scanner la TODO, vérifie si on est dans une **fenêtre bilan** :

| Fenêtre | Condition | Action |
|---|---|---|
| Bilan semaine | dimanche ≥ 18:00 UTC OU lundi avant 12:00 UTC, ET `logs/journal/YYYY-MM.md` non modifié depuis ≥ 5 jours | Exécute les étapes `/bilan semaine-derniere` dans la même invocation |
| Bilan mois | jour 1 ou 2 du mois UTC, ET pas de section "## Bilan mois" datée du mois précédent dans `logs/journal/YYYY-MM.md` | Exécute `/bilan mois-dernier` |
| Bilan jour | demande explicite via mode `+bilan-jour` | Exécute `/bilan jour` |

Si fenêtre détectée et mode ≠ `no-bilan` : annonce-le clairement (`📅 Fenêtre bilan détectée — j'enchaîne /bilan <période>`) puis suis la skill `bilan` au complet. Le résumé ntfy/Telegram (étape 6) inclut alors le verdict du bilan.

### 5. TODO actionables maintenant

Scan `HANDOFF.md` section "Immédiat" pour les items `[ ]` :
- Filtre ceux qui sont **hors zone Opus-only** ET **n'exigent pas de validation utilisateur**.
- Pour chaque, juge la faisabilité (≤ 15min de travail, pas de modif `signal.py`/`core/`/`position_manager.py`).
- Exécute si possible. Sinon, liste-les sous "À planifier".

### 6. Notification résumé (Telegram + ntfy)

**Par défaut (sauf mode `silent`)** : envoie un résumé humain via apprise (les channels de `config/secrets.yaml → notifications.channels` couvrent **Telegram ET ntfy**).

```python
import asyncio, yaml, apprise
from pathlib import Path
secrets = yaml.safe_load(Path("config/secrets.yaml").read_text())
channels = secrets.get("notifications", {}).get("channels", [])
if channels:
    ap = apprise.Apprise()
    for ch in channels:
        if isinstance(ch, str): ap.add(ch)
    asyncio.run(ap.async_notify(body=résumé, title="Arabesque /suivi"))
```

**Format du body** (≤ 8 lignes, lisible par un humain qui regarde son tél) :
```
📋 Suivi YYYY-MM-DD HH:MM UTC
🟢/🛠️/🚨 État global
• Engine : active (Xh uptime)
• FTMO -X.X% / GFT -Y.Y% — protection NORMAL
• Watchlist : N triggers (liste si > 0)
• Actions : … (liste ou "rien")
Prochain suivi : YYYY-MM-DD HH:MM UTC
```

Modes :
- `silent` → pas de notif
- `notify` → force la notif même si tout va bien (utile pour test ponctuel)
- défaut → notif courte mais systématique pour avoir un fil d'Ariane lisible

### 7. Planifier le prochain passage

Calcule `next_expected_in_hours` selon le contexte :

| Contexte | Délai |
|---|---|
| Tout vert, marché calme, pas de fenêtre bilan proche | 24h |
| Au moins 1 trigger watchlist actif (DD proximity, drift) | 6h |
| Protection ≠ NORMAL OU phantom récent OU engine instable | 2h |
| Vendredi soir UTC après clôture | 48h (rien jusqu'à dimanche soir) |
| Veille de fenêtre bilan (samedi soir) | jusqu'à dimanche 18:00 UTC |

Le timer `arabesque-suivi-reminder.timer` (systemd user, hourly, `Persistent=true`) lit `logs/maintenance_state.jsonl` chaque heure et envoie un rappel Telegram+ntfy si `now > last_ts + next_expected_in_hours`. Il survit au reboot.

Pour activer (une seule fois) :
```bash
systemctl --user daemon-reload
systemctl --user enable --now arabesque-suivi-reminder.timer
```

### 8. Append au state log

Écris une ligne JSON dans `logs/maintenance_state.jsonl` :
```json
{
  "ts": "2026-04-25T19:00:00+00:00",
  "delay_h_since_last": 4.2,
  "engine_ok": true,
  "engine_uptime_h": 12.3,
  "protection": {"ftmo_challenge": "normal", "gft_compte1": "normal"},
  "dd_pct": {"ftmo_challenge": -5.7, "gft_compte1": -5.0},
  "orphans": 0,
  "watchlist_triggered": [],
  "todo_done": [],
  "bilan_ran": null,
  "notification_sent": true,
  "next_expected_in_hours": 24
}
```

### 9. Output utilisateur

#### Cas A : rien à faire
```
🟢 Coucou, tout va bien.
Dernier suivi : il y a Xh (… UTC). Délai normal.
Engine : active depuis Xj, 2 brokers connectés.
Protection : NORMAL (FTMO -X%, GFT -Y%).
Watchlist : N items surveillés, 0 trigger.
```

#### Cas B : actions effectuées
```
🛠️ N action(s)
Dernier suivi : il y a Xh.
• [trigger ID] : action effectuée — résultat
• [TODO item] : fait
Notif Telegram : ✅
État après : engine OK, protection NORMAL.
```

#### Cas C : alerte critique (engine down, orphan, protection ≠ NORMAL)
```
🚨 Alerte
[liste des problèmes]
Pas d'auto-fix appliqué — investigation manuelle requise.
Notif Telegram envoyée : ✅
```

## Contraintes

- **Output ≤ 10 lignes en cas A**, ≤ 25 lignes en cas B/C. Privilégie la concision.
- Ne touche pas aux zones Opus-only (`arabesque/core/*`, `position_manager.py`, `strategies/*/signal.py` validés).
- N'auto-applique pas une action sans critère quantifiable préinscrit dans la table watchlist.
- En cas de doute (signal ambigu, métrique limite) : propose, n'agis pas.
- Date courante : `date -u +%Y-%m-%d`. Tous timestamps en UTC.
- Si `logs/maintenance_state.jsonl` n'existe pas, crée-le (premier passage).

## Invocations typiques

- `/suivi` → check normal + résumé Telegram+ntfy
- `/suivi silent` → log d'état seulement, output minimal, pas de notif
- `/suivi notify` → force la notif même si tout va bien (test du canal)
- `/suivi verbose` → output complet avec métriques détaillées
- `/suivi no-bilan` → désactive l'auto-bilan pour ce passage

## Infra de rappel (résiste au reboot)

- Script : `scripts/suivi_reminder.py` (lit `logs/maintenance_state.jsonl`, ping si retard > 0)
- Service : `~/.config/systemd/user/arabesque-suivi-reminder.service`
- Timer : `~/.config/systemd/user/arabesque-suivi-reminder.timer` — `OnCalendar=hourly`, `Persistent=true` (rattrape les passages manqués au reboot)
- Cooldown : 1 rappel toutes les 3h max (anti-spam)
- Channels : ceux de `config/secrets.yaml → notifications.channels` (Telegram + ntfy)
