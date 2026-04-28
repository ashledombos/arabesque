---
description: Bilan de trading reproductible (jour/semaine/mois) — live vs backtest, divergences inter-brokers, met à jour journal + docs, discussion ciblée.
argument-hint: "[jour|hier|semaine|semaine-derniere|mois|mois-dernier|YYYY-MM-DD|YYYY-MM-DD..YYYY-MM-DD]"
---

# /bilan — bilan de trading reproductible

Période demandée : **$ARGUMENTS** (si vide → choisis un défaut intelligent selon le jour/heure, voir plus bas).

## Objectif

Éviter de répéter à chaque fois la même liste d'étapes pour un bilan. À chaque
invocation, tu produis :
1. **Analyse chiffrée** sur la période (trades, WR, Exp, P&L par broker + par stratégie)
2. **Comparaison live vs backtest** (précision de l'edge observée)
3. **Divergences inter-brokers** (FTMO vs GFT quand un même signal part sur les deux)
4. **Détection d'anomalies** (protection switches, phantom exits, feed stale, weekend guard blocks)
5. **Mise à jour du journal** `logs/journal/YYYY-MM.md` (section pour la période)
6. **Mise à jour des docs** (HANDOFF, DECISIONS, STATUS — si ça bouge)
7. **Verdict par stratégie** : on continue / on ajuste / RAS — avec seuils explicites
8. **Discussion initiée** : tu ouvres le sujet toi-même, pas un rapport mort

Si la période est vide ou sans événement marquant, dis **RAS** clairement et passe.

## Argument de période

`args` peut contenir une période (FR ou EN accepté) :

| Arg | Signifie |
|---|---|
| `jour`, `today` | UTC 00:00 d'aujourd'hui → maintenant |
| `hier`, `yesterday` | J-1 UTC complet |
| `semaine`, `week` | Lundi 00:00 UTC → maintenant |
| `semaine-derniere`, `last-week` | Lundi à dimanche de la semaine précédente |
| `mois`, `month` | 1er du mois 00:00 UTC → maintenant |
| `mois-dernier`, `last-month` | Mois précédent complet |
| date ISO `2026-04-18` | Journée UTC de cette date |
| plage `2026-04-13..2026-04-19` | Plage UTC explicite |

### Défaut si pas d'argument
Choisis le plus pertinent selon le contexte :
- Dimanche soir (UTC 18h+) ou lundi matin → `semaine-derniere`
- 1er-2e jour du mois → `mois-dernier`
- En cours de semaine, milieu de journée → `jour`
- Sinon → `semaine`

**Annonce la période retenue en une ligne avant de commencer** (ex: `📅 Période : semaine du 2026-04-13 au 2026-04-19 UTC`).

## Étapes (dans l'ordre)

### 1. Data gathering

Lis (filtré sur la période) :
- `logs/trade_journal.jsonl` — événements `entry`, `exit`, `protection_level_change`, `emergency_close_all`
- `logs/weekend_crypto_guard.jsonl` — blocages weekend
- `logs/multi_broker_snapshots.jsonl` si présent — snapshots multi-broker
- `logs/equity_snapshots.jsonl` — courbe d'équité (pour DD observé)
- `logs/shadow_filters.jsonl` si relevant

Utilise `jq` (ou Python si plus simple) pour agréger. Ne dump pas les JSONL bruts dans la réponse.

### 2. Métriques à calculer

Par **broker** et par **(broker × stratégie)** :
- `n_exits`, `n_wins`, `n_losses`, `n_be_exits` (|R| < 0.25)
- `WR%`, `Exp (R)`, `ΣR`, `ΣP&L ($)`
- MFE moyen, MFE max
- Durée moyenne de position

#### 2.a Adéquation **live vs backtest** (le système valide-t-il toujours sa baseline ?)

C'est la question fondamentale : *les chiffres du backtest qui ont servi à choisir les stratégies tiennent-ils en live ?*

- Lance `python scripts/compare_live_vs_backtest.py --start <start> --end <end>` (ou `--last <N>` jours, ou `--period {today|yesterday|this_week|this_month|prev_month|3m|12m}`).
- Pour chaque stratégie active, le script reporte : `n_live`, `WR_live`, `Exp_live`, `ΣR_live` **vs** baseline backtest 20 mois (`WR_baseline`, `Exp_baseline`).
- Calcule **Δ_WR = WR_live − WR_baseline** et **Δ_Exp = Exp_live − Exp_baseline**.
- **Significativité** : Wilson IC95 sur `WR_live` (small-n safe). Si `IC95_low > WR_baseline + 5pp` ou `IC95_high < WR_baseline − 5pp`, le drift est significatif.
- Liste les **trades pivots** où live SL mais backtest BE/win (révèle un problème d'exécution : slippage, spread, latence, fill).
- **Notif si dérive** : `Δ_WR < −15pp ET n ≥ 30` → notifie Telegram+ntfy avec le delta et la stratégie. Le verdict §4 prendra le relais.

#### 2.b Cohérence **cross-broker** (FTMO vs GFT — même signal, exécutions différentes)

C'est la question opérationnelle : *quand un même signal part sur les 2 brokers, sortent-ils au même endroit ?*

- **Source primaire** : `logs/trade_journal.jsonl` filtré sur événements `exit`. Groupe par `(strategy, instrument, entry_ts ±5min)` pour retrouver les paires FTMO/GFT (un même signal qui part sur les 2 brokers).
- **Source secondaire** (si dispo) : `python scripts/review_broker_divergences.py --since <start>` agrège les snapshots simultanés bid/ask/spread depuis `logs/multi_broker_snapshots.jsonl`. Si « 0 paires » → snapshots non synchrones, s'appuyer uniquement sur la source primaire.
- Pour chaque paire : `Δ_entry_price`, `Δ_R`, `Δ_PnL`, écart MFE max.
- **Métriques agrégées** par stratégie : `n_paires`, `mean(|Δ_R|)`, `n_paires_avec_inversion` (un broker WIN, l'autre LOSS).
- **Notif si dérive** :
  - `mean(|Δ_R|) > 0.40` sur `n ≥ 5` paires → drift broker structurel sur cette stratégie.
  - `n_inversions ≥ 3` sur `n ≥ 10` paires → l'exécution d'un broker mange l'edge.
- **Action** : si la divergence est attribuable à un broker spécifique (spread/slippage), proposer ajout dans `strategy_broker_exclusions` (config/settings.yaml). Cf. cas Cabriole×GFT 2026-04-25.

#### 2.c Trades manquants (signaux théoriques sans entry live)

C'est la question de couverture : *toutes les stratégies actives ont-elles bien tiré sur tous leurs signaux ?*

- Lance `python scripts/replay_signals_vs_live.py --since <start> --until <end>`.
- Pour chaque stratégie active, le script reporte : `théoriques`, `live`, `blocked_weekend`, `manquants` (= ni live ni weekend ni couvert par `strategy_broker_exclusions`).
- **Si `manquants > 2` sur une stratégie** → investigue : engine aveugle sur cette plage ? stratégie dropped silencieusement ? filtre cooldown/spread/slippage trop agressif ? feed stale ?
- Inclure le résumé dans le bilan (par stratégie, ligne `manquants=N` à côté de `live=N`).

### 3. Anomalies à détecter

- **Protection switches** : CAUTION/DANGER/EMERGENCY déclenchés sur la période
- **Phantom exits** : exits avec `phantom_fallback=True` (après 3 cycles d'absence)
- **Feed stale** : events `feed_stale` répétés (> 5 par jour)
- **Weekend guard ROI** : (a) compte les blocked events sur la période depuis `logs/weekend_crypto_guard.jsonl` (event=blocked), groupés par stratégie ; (b) **évaluation contrefactuelle** : pour chaque blocked event, simule le résultat avec les bougies post-signal (parquet) en appliquant la même logique BE 0.3R / offset 0.20R / TP 2R / SL signal.sl. Reporte WR_counterfactuel, Exp_counterfactuel, ΣR_counterfactuel **vs** WR_semaine de la même stratégie sur la période. Verdict :
  - Si `WR_cf > WR_semaine + 10pp` ET `n ≥ 30` blocked → **proposer désactivation** du guard (le blocage coûte de l'edge).
  - Si `WR_cf < WR_semaine` OU `Exp_cf < 0` → **confirmer** le guard (on a bien raison de bloquer).
  - Sinon (zone grise) → noter, recheck semaine suivante.
  Inclure ce verdict dans le résumé Telegram/ntfy de `/suivi` quand le check tombe.
- **Consecutive losses** : séquences ≥ 5 pertes consécutives sur une même stratégie
- **Drift vs baseline** : WR observé s'éloigne de > 15pp de la baseline validée

### 4. Verdict par stratégie

Pour **chaque stratégie active** (même si 0 trade), produis un verdict :

| Verdict | Condition |
|---|---|
| ✅ **Continue** | WR et Exp cohérents avec baseline (IC99 se chevauche) OU trop peu de trades pour conclure |
| ⚠️ **Surveiller** | Drift 10-15pp en WR ou Exp < 0 sur n ≥ 15 trades (pas critique mais noter) |
| 🔶 **Ajuster** | Drift > 15pp **ET** n ≥ 30 trades **OU** 2 semaines rouges consécutives |
| 🛑 **Stop/pause** | DD > 5% attribuable à cette stratégie seule OU 3 semaines rouges consécutives |
| 💤 **RAS** | 0 trade sur la période (conditions de marché, pas un bug) |

**Avant de proposer Ajuster/Stop**, lis les journaux des 2 semaines précédentes pour vérifier si c'est une tendance ou un one-off.

### 5. Mise à jour du journal

Édite `logs/journal/YYYY-MM.md` (gitignored) : ajoute une section pour la période si elle n'existe pas, OU complète si partielle.

Format pour une semaine (modèle à suivre, pas à copier mot à mot) :
```markdown
## Semaine N (YYYY-MM-DD — YYYY-MM-DD)

### Bilan chiffré
- **N exits** — décomposition par stratégie
- **FTMO** : P&L net, ΣR, WR
- **GFT**  : P&L net, ΣR, WR
- **Total** : P&L net, ΣR

### Événements marquants
- Trade remarquable (winner ou loser signifiant)
- Protection switches
- Divergences broker notables

### Observations
- Drift vs baseline
- Patterns émergents
- Feed/connectivity issues

### Verdicts
- Stratégie X : ✅ Continue / ⚠️ / 🔶 / 🛑 — raison en 1 ligne

### Actions à considérer
- (liste brève, pas forcément à faire tout de suite)
```

Format pour une journée : plus court, focus sur les trades du jour + anomalies.

### 6. Mise à jour des docs

Ne touche aux docs **que si c'est nécessaire** :
- `HANDOFF.md` → date `Dernière mise à jour` + nouvelle entrée dans "Prochaines étapes" si action concrète découverte
- `docs/DECISIONS.md` → seulement si une décision a été prise (changement de seuil, nouvelle stratégie activée, etc.)
- `docs/STATUS.md` → seulement si balance/protection/stratégies actives changent
- `arabesque/strategies/<nom>/STRATEGY.md` → seulement si résultats ou statut change significativement

**Ne jamais commit automatiquement.** Laisse le user valider.

### 7. Discussion

Après les mises à jour, **initie une discussion ciblée**. Pas un résumé plat. Choisis 1-3 sujets concrets parmi :
- Une anomalie qui mérite investigation (feed stale récurrent sur X, divergence broker hors norme)
- Un verdict 🔶 Ajuster qui demande arbitrage utilisateur
- Une décision d'allocation (ex: Cabriole toujours ×0.50 ou on relève ?)
- Un pattern émergent dans les données (heures d'entrée, corrélations)

**Si tout est propre et conforme**, dis clairement **RAS — on continue comme ça** avec les chiffres qui le justifient. Pas de discussion artificielle.

## Contraintes

- Respecte la boussole (CLAUDE.md) : gains petits/fréquents, WR élevé, courbe régulière. Un conseil qui demande plus de risque ou vise +5R doit être refusé.
- Si tu proposes un changement de paramètre, il doit être **quantifiable et testable** — jamais "on pourrait essayer X" sans critère.
- Ne suggère pas de modifier `arabesque/core/*`, `arabesque/modules/position_manager.py`, ni un `strategies/*/signal.py` validé en live — ces zones sont Opus-only.
- Date courante : lis via `date -u +%Y-%m-%d` si tu as un doute.
- Tous les timestamps dans les logs sont en UTC.

## Invocations typiques

- `/bilan` → défaut intelligent
- `/bilan semaine-derniere`
- `/bilan hier`
- `/bilan mois`
- `/bilan 2026-04-17`
- `/bilan 2026-04-13..2026-04-19`
