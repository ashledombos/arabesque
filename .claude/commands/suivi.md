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
f. **Engine sain ≠ engine actif** : `systemctl is-active=active` ne garantit rien sur la fidélité d'exécution. Pour le sanity check, lance `python scripts/check_execution_invariants.py --since J-7` (≤ 2s). Si verdict ≠ `ok`, escalader directement vers le trigger `execution_invariants` de la watchlist (§3). Incident fondateur : 2026-05-07 — engine vert pendant 6 semaines avec MFE tracking cassé, drift uniforme 30-60pp WR. La leçon est dans la skill : un trigger d'invariant ne s'explique jamais par le régime de marché.

g. **Feed FTMO vivant** : le feed est vivant si **des barres se ferment**. `journalctl --user -u arabesque-live.service --since "35 minutes ago" | grep -c "BarAggregator.*Résumé"` ≥ 1 (la ligne `✅ Résumé: N barre(s) fermée(s)` ne nomme PAS les instruments — ne PAS grepper `BarAggregator.*BTCUSD`, ça rend toujours 0 = faux négatif). Optionnel, lire l'état des symboles : `grep "PriceFeed].*actifs" | tail -1` → `📊 N/31 actifs, …` (en weekend les cryptos passent en `dormants`, normal). **Signature de feed MORT** (vérifier précisément, sinon faux positif) : `journalctl --user -u arabesque-live.service --since "35 minutes ago" | grep -E "(ALREADY_LOGGED_IN|TimeoutError.*PriceFeed|Feed stale \(majeur|Feed stale global|force reconnect after stale feed)"` — si ≥ 1 match → escalader vers `feed_stale` (§3). **⚠️ NE PAS utiliser le motif `feed.*stale`** : il matche le nom du logger `…price_feed:` suivi de la ligne de statut `… N stale majeurs` → 7 faux positifs le 2026-06-20 (weekend). Incident fondateur : 2026-05-12 23:01 → 2026-05-13 07:54 — feed FTMO bloqué `ALREADY_LOGGED_IN` 8h, engine `active`, /suivi disait « tout va bien ». La règle : `is-active=active` + **0 barre fermée** ≠ "tout va bien".

### 3. Watchlist — évaluation

Pour chaque item, calcule la métrique à partir de `logs/trade_journal.jsonl` (filtre par strategy×broker depuis `2026-03-22`, événement `exit`, `result_r`). Critère "WR" inclut BE (`-0.25 ≤ result_r ≤ 0.25` compté comme half-win).

| ID | Trigger | Action si atteint |
|---|---|---|
| `glissade_gft_block` | Glissade GFT : `n ≥ 8` ET `WR < 30%` | Ajouter `glissade: [gft_compte1]` dans `config/settings.yaml → strategy_broker_exclusions`. Append entry DECISIONS.md. Notif Telegram. |
| `cabriole_gft_unblock` | Cabriole FTMO : `n ≥ 20` ET `WR ≥ 70%` | Retirer `cabriole: gft_compte1`. Append DECISIONS.md. Notif Telegram. |
| `extension_gft_drift` | Extension : `n_gft ≥ 10` ET `WR_ftmo - WR_gft > 50pp` | Proposer block (ne pas auto-appliquer, demander validation). |
| `phantom_exit_alert` | Nouvel `orphan_cleanup` OU `phantom_fallback=true` dans les dernières 24h | Investiguer (lire les events), composer alerte Telegram. |
| `engine_uptime_drop` | Engine uptime < 30min OU > 5min sans BarAggregator log | Alerte. Ne pas auto-restart (peut être en cours). |
| `feed_stale` | **Détection silencieuse — engine actif mais feed mort**. Critère : **0 ligne `BarAggregator.*Résumé`** (aucune barre fermée) dans les 35 dernières minutes (hors weekend vendredi 22:00 UTC → dimanche 22:00 UTC, où les barres crypto FTMO peuvent ne pas fermer) **OU** ≥ 1 occurrence des signatures PRÉCISES `ALREADY_LOGGED_IN` / `TimeoutError.*PriceFeed` / `Feed stale (majeur` / `Feed stale global` / `force reconnect after stale feed`. **⚠️ NE PAS** utiliser `feed.*stale` (matche le logger `price_feed:` + la ligne de statut `N stale majeurs` → faux positifs, cf. 2026-06-20). | 🚨 Alerte critique Telegram+ntfy + escalade en **état `🚨 Alerte`** dans l'output (jamais cas A). Ligne `Reco: vérifie l'état du PriceFeed et redémarre l'engine si nécessaire (\`systemctl --user restart arabesque-live.service\`)`. Ne **JAMAIS** auto-restart sans validation user (un restart peut interrompre un trade en cours). |
| `dd_proximity` | DD broker > 70% du seuil CAUTION (-7%) → ex: DD < -4.9% | Alerte proactive. |
| `live_vs_backtest_drift` | Lance `python scripts/compare_live_vs_backtest.py --last 30` (ou `--period 3m`). Si pour une stratégie `Δ_WR < -15pp ET n ≥ 30`, ou `Wilson IC95_high(live) < WR_baseline - 5pp` | Alerte Telegram+ntfy avec stratégie + delta. Note dans HANDOFF "Action à considérer". Pas d'auto-désactivation (zone validation user). |
| `edge_audit_drift` | **OBJECTIF PRINCIPAL** — l'edge est-il conservé ? Lance `python scripts/audit_edge_live_vs_backtest.py --since J-30 --no-persist` (ou lit la dernière entrée de `logs/edge_audit.jsonl` si datée < 24h). Pour chaque stratégie, le verdict est inscrit dans le JSONL. Triggers : `verdict_code == "drift_structurel"` (ΔExp < -0.30 sur n ≥ 30) → alerte critique, propose action ; `verdict_code == "drift_modere"` qui persiste 3 audits consécutifs → alerte modérée. Si `regime_defavorable` → ne rien faire (live colle au backtest, c'est juste le marché). | Alerte avec lecture brute du verdict + ΔExp + ΔBaseline. Note l'historique : si même verdict drift sur ≥ 3 audits récents, escalader. |
| `edge_decomposition` | **POURQUOI ça dérive** (complète `edge_audit_drift` qui dit *que* ça dérive). Lance **systématiquement** `python scripts/edge_decomposition.py --since J-30` ; pour le verdict Phase 4 bis, limite la lecture à `extension` + `glissade` depuis `2026-05-16T08:44`, Cabriole/trend restant informationnels. Catégories quantifiées : `be_missed`, `sl_slipped`, `reconciled`, `mfe_zero_loser`, `wide_spread` + résiduel régime non imputé. Triggers : (a) `be_missed` ≥ 10% de \|ΔExp\| → bug BE non armé, escalader vers `execution_invariants` ; (b) `sl_slipped` ≥ 15% → slippage SL anormal, vérifier guards ; (c) `wide_spread` ≥ 10% → coût exécution, regarder broker ; (d) `reconciled+mfe_zero` borne sup ≥ 30% de \|ΔExp\| → bug tracking probable, prioriser tâche #18/#15 ; (e) résiduel régime ≥ 70% de \|ΔExp\| ET ΔExp < -0.20 → pas un bug, c'est le marché — proposer `/bilan` régime + revue baseline. | Inclure les 2-3 plus grosses catégories dans le résumé Telegram+ntfy (ex: « ΔExp=-0.51R · be_missed=5% · résiduel régime=95% → c'est le marché, pas un bug »). Persiste auto dans `logs/edge_decomposition.jsonl`. **Ne jamais** classer un coût imputable comme régime — chaque catégorie a sa lecture. Source d'autorité : `docs/VALIDATION_CONTRACT.md`. |
| `replay_drift_live_vs_theory` | **MESURE DIRECTE de l'exécution** (sépare exécution / régime / bias BT). Pour le verdict courant, lance `python scripts/replay_live_vs_theory.py --since 2026-05-16T08:44 --strategy extension` puis la même commande avec `--strategy glissade` ; les autres stratégies restent informationnelles. Pour chaque trade : retrouve la barre signal en parquet, simule trade pur (BE 0.3R / offset 0.20R / TP 2R / SL signal.sl), reporte `Δ_R = R_live − R_theo`. Triggers **par stratégie** : (a) `meanΔR < -0.20R sur n ≥ 20 trades` → exécution mange l'edge, proposer ultra-rodage ; (b) `meanΔR < -0.10R sur n ≥ 30 trades` → alerter ; (c) `meanΔR ∈ [-0.10R, +0.10R]` → exécution cohérente. | Lecture brute par stratégie et par broker dans la notif. Persiste `logs/replay_live_vs_theory.jsonl` + `_trades.jsonl`. **Recommandation, pas auto-action**. Source d'autorite : `docs/VALIDATION_CONTRACT.md`. |
| `cross_broker_divergence` | Aggrège `logs/trade_journal.jsonl` exits par `(strategy, instrument, entry_ts ±5min)` pour paires FTMO/GFT. Si `mean(\|Δ_R\|) > 0.40` sur `n ≥ 5` paires OU `n_inversions ≥ 3` sur `n ≥ 10` paires | Alerte avec broker incriminé. Si pattern net (un broker mange l'edge), suggérer entry dans `strategy_broker_exclusions` (proposer, ne pas auto-appliquer). |
| `missing_trades_unjustified` | Lance `python scripts/replay_signals_vs_live.py --since J-7 --min-missing 5`. Le script catégorise désormais chaque manquant : **`source`** (0 broker n'a tiré → feed/engine down, root cause unique mais comptée × N brokers en coût) vs **`broker-specific`** (signal pris sur ≥1 broker mais raté sur ≥1 autre → rejet local : risk guard, position pleine, latence d'un connecteur). Triggers : (a) total > 5 par stratégie sur 7j → alerte ; (b) `source ≥ 3` cumulé tous strats → faisceau "panne à la source", croiser avec `feed_stale` et journalctl PriceFeed ; (c) `broker-specific ≥ 3` sur un même broker → enquête connecteur (`grep` exclusions, journal des rejets, état du compte broker). | Alerte Telegram+ntfy avec ventilation **source vs broker-specific** (la première mesure une panne d'infra, la seconde un drift d'exécution sur un broker). Pas d'auto-fix — diag manuel. **Note** : le script déduplique les clusters (signaux consécutifs sur 1 setup). À J-7 le seuil 5 capte les pannes ponctuelles ; ne pas abaisser sous 3. **Lecture cible** : si tout est `source`, le problème est à la source unique (PriceFeed cTrader mutualisé via `price_feed.source_broker`) ; si tout est `broker-specific`, c'est un broker précis qui mange l'edge. |
| `selection_coverage` | **LIT le cache** (la génération — lourde, ~3-4 min — est faite par `/bilan` §2.c-bis, pas ici). Lis `logs/selection_coverage_latest.md` (+ dernière ligne de `logs/selection_coverage.jsonl`). Décompose l'écart live↔backtest en COUVERTURE / VARIANCE / BIAIS (le décodeur d'un `drift_structurel` edge_audit). Si le cache date de > 8 jours → le signaler comme périmé (« relancer un /bilan »), ne pas alerter sur du vieux. Verdicts du cache : `selection_bias` (écart raté−pris > 0.30R sur n_pris≥20 → le live rate **systématiquement** les bons signaux) ; `mild_tilt` (0.15–0.30R) ; `low_coverage_variance` (≈ → sous-échantillonnage, le cap est le levier, la **population** reste la référence d'edge) ; `low_n`. | Si verdict caché `selection_bias` (et frais) → alerte Telegram+ntfy + note HANDOFF "investiguer filtre live-only (ordering cap / spread / slippage / cooldown)". Sinon inclure **couverture %** + écart dans la notif (informatif : couverture basse = résultat live dominé par la variance, pas l'edge). **Ne PAS régénérer ici** (cf. pattern `edge_audit_drift` qui lit aussi le cache). **Référence 2026-06-20 (post cap=7)** : `low_coverage_variance`, couverture **29%**, écart **+0.10R** → pas de biais. Re-mesure (par `/bilan`) à n_pris≥40. Source : `docs/VALIDATION_CONTRACT.md`. |
| `execution_invariants` | **Détection de bugs de tracking** (distinct du drift d'edge). Lance `python scripts/check_execution_invariants.py --since J-7 --per-broker`. Le mode `--per-broker` évalue séparément FTMO + GFT pour ne pas diluer un bug spécifique à un connecteur (cf. incident 2026-05-07 : 24% reconciled FTMO vs 33% GFT). Verdict possible : `ok` / `alert` / `critique` global = max des 2. Capte par broker : `reconciled_other_ratio` (>2%/5% fallback ambigu), `mfe_zero_loser` (≥3 losers avec MFE=0 = tracker cassé), `zero_winner_streak` (0 winner sur ≥20 trades), `be_unarmed_ratio` (>10% des -1R avaient MFE≥0.3R sans BE armé). | Si verdict global `critique` → 🛑 **proposer STOP live** (pas d'auto-stop, demander validation user). Si triggers concentrés sur **un seul broker**, escalader avec lecture brute par broker. Si `alert` → notif Telegram+ntfy + note dans HANDOFF "Investigation à planifier". Ne jamais classer en `regime_defavorable` un trigger d'invariant : un MFE=0 sur un loser n'est jamais explicable par le marché. Cf incident 2026-05-07 (drift uniforme 30-60pp WR + 26% reconciled = bug exécution, pas régime). |
| `phase4_revalidation` | **Critère go Phase 4 bis** : compte uniquement les exits `strategy in {extension, glissade}` depuis `2026-05-16T08:44:00+00:00`. Cabriole/trend sont exclus du verdict. Revue possible a `n ≥ 30`, decision cible a `n ≥ 50`. Lance (a) `python scripts/check_execution_invariants.py --since 2026-05-16T08:44 --per-broker` ; (b) `python scripts/replay_live_vs_theory.py --since 2026-05-16T08:44 --strategy extension` et `--strategy glissade` ; (c) audit sizing avant toute hausse. | **Verdict combiné** : invariants `ok`, aucun incident integrite ouvert, sizing representatif et `meanΔR ≥ -0.10R` seulement permettent de proposer une hausse (validation user). Sinon rester au risque courant ou reduire. Si `n < 30`, no-op de decision et simple collecte. Source d'autorite : `docs/VALIDATION_CONTRACT.md` / `config/validation_policy.yaml`. |
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

**Format du body** (≤ 12 lignes, lisible par un humain qui regarde son tél) :
```
📋 Suivi YYYY-MM-DD HH:MM UTC
🟢/🛠️/🚨 État global
• Engine : active (Xh uptime)
• FTMO -X.X% / GFT -Y.Y% — protection NORMAL
• Edge replay J-30 : Cabriole +0.02R/31t · Extension -0.10R/19t · Glissade +0.23R/4t
• Edge decomp : be_missed=N% · résiduel régime=M%   (si ΔExp < -0.20)
• Phase 4 : N/50 trades · verdict=…              (si Phase 4 active)
• Watchlist : N triggers (liste si > 0)
• Reco : … (action lisible si trigger : « passe Extension en strategies_ultra », « investigue be_missed », « rien »)
Prochain suivi : YYYY-MM-DD HH:MM UTC
```

**Recommandations actionnables** : quand un trigger se déclenche, la ligne `Reco` doit dire **quoi faire concrètement**, pas juste signaler le problème :
- `replay_drift_live_vs_theory < -0.20R sur stratégie X` → « `Reco: ajoute X à rodage.strategies_ultra dans config/settings.yaml puis restart engine` »
- `edge_decomposition be_missed > 10%` → « `Reco: investigue les trades be_set=False mfe≥0.3R dans logs/trade_journal.jsonl` »
- `edge_audit_drift drift_structurel` → « `Reco: réduire risk de la stratégie d'un cran (×0.50→×0.25, ou ×0.25→×0.10) ; ne pas stopper sans replay confirmant exécution dévorante` »
- `phase4_revalidation verdict=ok+meanΔR≥-0.10R` → « `Reco: tu peux ramper rodage.risk_multiplier ×0.25 → ×0.50` »
- `phase4_revalidation verdict=ok+meanΔR<-0.20R` → « `Reco: NE pas ramper. Bascule la stratégie la plus dérivante en strategies_ultra (×0.10), continue collecte 50 trades` »

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
Feed crypto FTMO : dernière barre BTCUSD/ETHUSD il y a Xmin (vivant).
Protection : NORMAL (FTMO -X%, GFT -Y%).
Watchlist : N items surveillés, 0 trigger.
```

**Pré-condition stricte du cas A** : tous les checks §2 (a-g) doivent passer. Si check `g` (feed crypto stale) échoue → **passer en cas C directement**, jamais cas A même si tout le reste est vert. La présence du log "active" du service n'est pas un indicateur de santé suffisant — cf. incident 2026-05-12.

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
