# Arabesque — Journal des décisions et expériences

> **Source de vérité sur le POURQUOI.**  
> Ce fichier documente ce qui a été essayé, ce qui a été abandonné, et pourquoi.  
> À lire avant de modifier la stratégie, le pipeline, ou les instruments.
>
> Synthèse de 4 conversations Perplexity (fev. 2026) + session courante.
> Mis à jour à chaque décision importante.

---

## Table des matières

1. [Fondamentaux non négociables](#1-fondamentaux-non-négociables)
2. [Stratégie : ce qui a été abandonné et pourquoi](#2-stratégie--ce-qui-a-été-abandonné-et-pourquoi)
3. [Bugs connus, corrigés, et non corrigés](#3-bugs-connus-corrigés-et-non-corrigés)
4. [Instruments et catégories](#4-instruments-et-catégories)
5. [Gestion de position](#5-gestion-de-position)
6. [Pipeline de sélection des instruments](#6-pipeline-de-sélection-des-instruments)
7. [Infrastructure et données](#7-infrastructure-et-données)
8. [Questions ouvertes](#8-questions-ouvertes)

---

## 1. Fondamentaux non négociables

Ces décisions sont **définitives**. Ne pas revenir dessus sans raison forte et documentée.

### Stratégie
- **Mean-reversion est l’edge principal**, pas le breakout. Justification : asymétrie de slippage. Le MR achète quand le prix descend (slippage neutre ou favorable). Le breakout achète quand ça monte (slippage adverse).
- **`combined` est la seule stratégie autorisée en production.** `mean_reversion` seule donne WR ~35% sur crypto volatile (trop permissif, pas de filtre de tendance). Ne jamais la déployer seule.
- **Timeframe signal : H1.** Le LTF (M15/M5) a été évalué : gain estimé +2-5% d’expectancy max, complexité élevée (refonte `load_ohlc`, gestion gaps, 4× plus de données). Écarté tant que l’edge n’est pas validé en live.
- **Timeframe régime HTF : 4H.** Filtre directionnel sur le signal H1.

### Architecture
- **Un seul `CombinedSignalGenerator`**, partagé entre backtest, replay parquet, et live cTrader. Zéro divergence de logique entre les modes. Si le code diverge, les résultats du backtest ne s’appliquent pas au live.
- **Anti-lookahead strict** : signal généré sur bougie `i` (close confirmé), stocké dans `_pending_signals`, exécuté au **open de bougie `i+1`**. Toute exécution sur le close de la même bougie est un biais.
- **Tout en R/ATR** (invariant d’instrument) : sizing, paliers de trailing, métriques de performance. Cela permet de comparer des instruments de classes d’actifs différentes.
- **`barres_au_sol` reste un dépôt séparé.** C’est un data lake générique réutilisable. L’intégrer dans Arabesque compromettrait sa réutilisabilité et mélangerait des dépendances incompatibles.

### Guards
- **Guards toujours actifs**, y compris en dry-run et replay. Les désactiver pour "tester plus vite" invalide les résultats.
- **Un seul trade simultané par instrument** (`duplicate_instrument`). Décision ferme, ne pas revenir dessus.
- **Ne jamais connecter le bot sur le compte challenge FTMO** (94 989 USD, ~5% DD déjà consommé) avant validation complète des guards DD sur replay 3 mois.

---

## 2. Stratégie : ce qui a été abandonné et pourquoi

### Breakout Donchian 4H (projet «Envolées»)
**Abandonné définitivement.**  
Toutes les configs validées en IS (In-Sample) sont devenues négatives après correction des biais d’exécution (anti-lookahead, slippage sur gaps). La stratégie breakout Donchian n’a pas d’edge exploitable sur les instruments testés.  
**Note** : le projet Envolées peut être réutilisé pour son **connecteur cTrader** uniquement. La stratégie elle-même est à ignorer.

### `mean_reversion` seule
**Abandonnée en production.**  
WR ~35% sur crypto volatile. Trop permissif sans filtre de tendance (régime HTF). Ne jamais déployer seule.

### `mr_shallow_wide` comme signal universel
**Abandonné sauf commodities/energy.**  
Négatif sur 4 catégories sur 6. La combinaison large volatilité + rebond superficiel génère des faux rebonds sur la majorité des marchés.

### FX en 1H
**Suspendu** (pas abandonné définitivement).  
Résultat : -60.2R OOS sur 1 070 trades. Deux causes identifiées : BB width ATR trop faible pour atteindre le premier palier trailing à +0.5R ; pas de filtre directionnel daily.  
À tester en 4H avec filtre EMA200 daily et tier 0 trailing à +0.25R avant toute conclusion définitive.

### `only_last_bar=True` dans `_generate_signals_from_cache`
**Abandonné.**  
Incompatible avec le mode replay Parquet : à chaque itération le cache est reconstruit, la "dernière barre" change, et aucun signal historique n’est jamais retourné → 0 signaux.  
Fix : `only_last_bar=False` + set `_seen_signals` par instrument (déduplique par timestamp).

### `only_last_bar=False` sans deduplication
**Abandonné.**  
Tous les signaux historiques du cache étaient renvoyés à chaque bougie, générant des doublons massifs (55+ trades WR 25%).

### Source de données live : TradingView webhook
**Remplacé par cTrader H1 stream natif.**  
Dépendance externe évitée. Le `CombinedSignalGenerator` tourne directement sur les barres H1 reçues, identique au backtest.

### Simulation LTF (M15) pour la précision du backtest
**Écarté pour l’instant.**  
Gain estimé : +2-5% d’expectancy via résolution de l’ambiguïté SL vs TP intra-barre. Complexité : refonte `load_ohlc`, gestion des gaps, 4× plus de données. Non prioritaire tant que l’edge n’est pas validé en live.

---

## 3. Bugs connus, corrigés, et non corrigés

### ✅ Corrigés

| Bug | Cause | Fix |
|---|---|---|
| `sig.tp` → `AttributeError` | Le champ s’appelle `tp_indicative`, pas `tp` | Renommer partout |
| Guard slippage rejetait 96% des signaux | `tv_close` comparé à `open_next_bar` sur données 1H (1h d’écart = toujours > seuil) | Comparer `fill` vs `open_next_bar` |
| `np.float64` dans le dict signal | Pas de cast `float()` natif → erreurs sérialisation JSON / broker | Cast `float()` partout |
| Colonne `"ema200"` inexistante | `prepare()` produit `"ema_slow"` (EMA200 LTF), pas `"ema200"` | Essaie `"ema200"` puis `"ema_slow"` |
| RR = 0.337 au lieu de 1.890 | RR recalculé avec le close courant (dernière bougie) au lieu du close au moment du signal | Utiliser `df.iloc[idx]["Close"]` |
| `tv_close` / `tv_open` dans `Signal.__init__()` | Ces noms sont des propriétés (alias), pas des champs `__init__` | Remplacer par `close=` et `open_=` |
| 0 signaux en dry-run replay | `only_last_bar=True` incompatible avec le rebuild du cache | `only_last_bar=False` + `_seen_signals` |
| 55+ trades WR 25% | Suppression du filtre sans tracking → signaux doublons massifs | Set `_seen_signals` par timestamp |
| `git push --force` a écrasé un commit | Force push depuis local en retard → écrasement du remote | Ne jamais faire `--force` sur `main` |

### ⚠️ Identifiés, non encore corrigés

| Bug | Cause | Impact | Priorité |
|---|---|---|---|
| `daily_dd_pct` divisé par `start_balance` | Doit être divisé par `daily_start_balance` | Sous-estime le DD journalier, les guards DD ne se déclenchent jamais | **BLOQUANT** pour validation guards |
| `EXIT_TRAILING` jamais utilisé | `DecisionType.EXIT_TRAILING` n’est pas appelé dans `_check_sl_tp_intrabar` | Impossible de distinguer pertes réelles et gains via trailing dans les stats | Haute |
| `tv_close` = `bars[-1]["close"]` | Close de la dernière bougie du cache au lieu de `df.iloc[idx]["Close"]` | RR légèrement faux en replay historique long (rare en live) | Moyenne |
| `orchestrator.get_status()` exception silencieuse | Exception non capturée en fin de replay | Résumé final balance/equity/nb trades non fiable | Moyenne |

---

## 4. Instruments et catégories

### Statut par catégorie (run pipeline 2026-02-20, 80 instruments)

| Catégorie | Instruments testés | Viables | Statut | Meilleurs sub-types |
|---|---|---|---|---|
| **Crypto alt-coins** | 31 | 16 | ✅ Validée | `mr_deep_narrow` (+0.237R), `trend_strong` (+0.199R) |
| **Metals** | 6 | 1 (XAUUSD) | ⚠️ Neutre | `mr_shallow_narrow` uniquement — tous les signaux trend détruisent du capital |
| **FX** | 43 | 0 | ❌ Suspendu | Aucun viable en 1H, à tester en 4H |
| **Énergie** | 0 | — | 🔄 Pas de parquets | `mr_deep_narrow` (+0.946R) sur résultats historiques |
| **Commodities** | 0 | — | 🔄 Pas de parquets | Seule catégorie où `mr_shallow_wide` est positif |
| **Indices** | 0 | — | 🔄 Pas de parquets | Potentiel moyen, souvent en tendance |
| **Actions** | 0 | — | ⚠️ À éviter | Gaps, earnings, liquidité variable |

### Instruments viables (pipeline 2026-02-20)

```
Crypto (16) : AAVUSD, ALGUSD, BCHUSD, DASHUSD, GRTUSD, ICPUSD, IMXUSD,
               LNKUSD, NEOUSD, NERUSD, SOLUSD, UNIUSD, VECUSD, XLMUSD,
               XRPUSD, XTZUSD
Metals  (1) : XAUUSD
```

⚠️ XAUUSD : moins de barres que les crypto (horaires restreints, pas de weekend). Normal, pas un bug.

### Règles de filtrage par catégorie (décisions établies)

- **FX** : suspendre en 1H. `trend_strong` et `trend_moderate` détruisent de la valeur.
- **Metals** : exclure tous les sub-types trend. Mean-reversion pur uniquement.
- **Crypto** : `mr_shallow_wide` neutre à éviter. Focus `mr_deep_narrow` + trend filteré.
- **Energy** : conserver `mr_shallow_wide` (positif ici, contrairement aux autres catégories).

### Logique de sélection anti-overfitting

Voir [`docs/instrument_selection_philosophy.md`](instrument_selection_philosophy.md) pour la discussion complète.  
Principe clé : **valider la catégorie avant l’instrument**. Un instrument neutre ou légèrement négatif dans une catégorie validée ne doit pas être exclu (cycle défavorable, pas edge inexistant). Exclure uniquement sur **critères de sécurité** : DD > 8%, jours disqualifiants > 0, spread / ATR > 50%.

---

## 5. Gestion de position

### Trailing — décisions définitives

- **SL ne descend jamais** (LONG) / **ne monte jamais** (SHORT). Règle absolue, inviolable.
- **5 paliers** : +0.5R→BE, +1R→0.5R, +1.5R→0.8R, +2R→1.2R, +3R→1.5R
- **Le trailing est le vrai moteur de l’edge**, pas les TP. `AvgW` tourne autour de 0.7-0.9R alors que le RR moyen est à 3.0-3.2R — les TP sont rarement touchés.
- **Séquence de mise à jour** : (1) `update_price` → (2) `_check_sl_tp_intrabar` avec SL actuel → (3) `_update_trailing` pour la bougie suivante. Le trailing ne prend effet qu’à N+1.
- **Règle pire-cas intrabar** : si SL et TP sont touchés sur la même bougie, c’est le SL qui s’applique.

### Exits (priorité)

```
TP > SL (ou trailing SL) > Giveback (>50% MFE rendu) > Deadfish (stagnation) > Time-stop (48 barres)
```

### Sizing

- **Sizing compound** : `risk_cash = balance_courante × risk_pct`. Le risk $ décroît avec le compte — comportement voulu, confirmé correct.
- **Arrondi** : toujours vers le bas (jamais sur-risquer).
- **`remaining_daily`** : le risk par trade est plafonnd à la marge restante avant daily DD limit — déjà implémenté, logique FTMO-safe.
- **SL minimum** : `max(swing_low_7bars, close - 0.8×ATR)` pour éviter les SL trop serrés qui généraient 0 fills.

### À explorer (non décidé)

- **TP fixe à 1.5R ou 2.0R** sur les sub-types avec `AvgW > 1.0R` (notamment `mr_deep_narrow` sur energy/crypto). Bloque sur bug `EXIT_TRAILING` non corrigé (impossible de distinguer les trailing wins).
- **Tier 0 trailing à +0.25R → trail 0.15R** : à tester spécifiquement pour FX 4H où les moves sont plus courts.
- **Sortie sur stagnation** : clore en profit minimal après N barres (après 12 barres si profit > 0.2R, après 24 barres si profit > 0R) — identifié comme manquant, non testé.

---

## 6. Pipeline de sélection des instruments

### Architecture actuelle (3 stages)

```
Stage 1 : Signal count    → ≥ 50 signaux sur la période
Stage 2 : IS backtest     → PF > 0.8, expectancy > -0.10R, DD < 10%
Stage 3 : OOS backtest    → mêmes seuils sur la deuxième moitié
```

**Modes disponibles** : `default`, `strict`, `wide`.

### Configuration YAML des filtres (`config/signal_filters.yaml`)

La matrice de filtres par catégorie et sub-type est déclarative en YAML — source de vérité, lisible sans toucher au code. Ne pas coder des filtres en dur dans `pipeline.py`.

### Architecture stable vs research

```
config/stable/   + results/stable/   → production (pipeline IS/OOS + Monte Carlo validé)
config/research/ + results/research/ → exploration (jamais déployé direct)
```

Rien ne migre vers `stable/` sans pipeline IS/OOS + Monte Carlo complet.

### Stage 0 (non encore implémenté) — validation par catégorie

Idée : calculer un score agrégé de catégorie avant d’appliquer les seuils par instrument. Si ≥ 50% des instruments de la catégorie passent Stage 3, appliquer le mode `wide` automatiquement pour tous ses instruments. Seuls les garde-fous s’appliquent alors (DD, disqual days, liquidité). Instruments neutres (-0.10R à 0R) conservés.  
Voir [`docs/instrument_selection_philosophy.md`](instrument_selection_philosophy.md).

### Stats avancées post-pipeline (`run_stats`)

- **Wilson CI** sur le WR : est-ce statistiquement signé à 95% au-dessus de 50% ?
- **Bootstrap 1000 itérations** sur l’expectancy : borne basse 95% CI doit être > 0R
- **Dégradation IS→OOS par fenêtre glissante** : performance stable ou concentrée sur sous-période ?

---

## 7. Infrastructure et données

### Serveur et environnement

- Serveur : `hodo`, user `raphael`, `/home/raphael/dev/arabesque/`
- Python : `.venv` dans le repo
- Workflow Git : **push direct sur `main`**, pas de PR. **Ne jamais faire `git push --force` sur `main`.**

### Parquets H1

- Source : `barres_au_sol` (dépôt séparé, clonable indépendamment)
- Crypto : via CCXT/Binance (clé `SYMBOL_USDT_1h.parquet` → arabesque `SYMBOLUSDUSD_H1.parquet`)
- XAUUSD : via Dukascopy
- FX, indices, energy : non encore téléchargés (pas de parquets locaux → absent du pipeline auto)

### Comptes FTMO

| Compte | Solde | Type cTrader | Risque |
|---|---|---|---|
| Live test gratuit 15j | 100 000 USD | "Live" | Zéro risque réel — idéal pour tester les ordres |
| Challenge 100k | ~94 989 USD | "Demo" | Argent réel payé — ~5% DD consommé, ~5% de marge |

⚠️ Ne jamais connecter le bot sur le compte challenge avant validation complète des guards DD.

### Transmission inter-sessions

Perplexity **n’a pas accès aux conversations précédentes** d’un espace, même dans le même espace. La mémoire inter-sessions passe **uniquement par le repo GitHub**.

Fichiers à lire en début de session :
1. `HANDOFF.md` — état opérationnel actuel + prochaines étapes
2. `docs/decisions_log.md` (ce fichier) — pourquoi les décisions ont été prises
3. `docs/instrument_selection_philosophy.md` — logique de sélection

**Prompt de reprise recommandé** :
```
Lis HANDOFF.md et docs/decisions_log.md dans le repo GitHub ashledombos/arabesque (branche main)
avant de répondre à quoi que ce soit. Ces deux fichiers contiennent l’état du projet
et l’historique des décisions. Ne pas redécouvrir ce qui est déjà documenté.
```

---

## 8. Questions ouvertes

Classement par priorité pour éviter de les redécouvrir.

### Bloquantes (doivent être résolues avant le live)

1. **Bug `daily_dd_pct`** : fix identifié (`/ daily_start_balance`) mais **pas encore committé**. Les guards DD ne se déclenchent jamais avec ce bug — déployer en live = risque direct.
2. **`EXIT_TRAILING` vs `EXIT_SL`** : sans ce tag, les stats de performance (vrai WR, PF) sont fausses. Bloque aussi la décision TP fixe vs TSL.
3. **Guards DD jamais validés** : re-vérifier après fix du `daily_dd_pct`. Lancer replay 3 mois et chercher `"rejected DAILY_DD_LIMIT"` et `"rejected MAX_DD_LIMIT"` dans les logs.

### Importantes (avant scaling)

4. **FX en 4H** : est-ce que le changement de timeframe + EMA200 daily + tier 0 trailing à +0.25R rend le FX viable ? Non testé.
5. **TP fixe vs TSL sur `mr_deep_narrow` energy** : l’expectancy exceptionnelle (+0.946R) vient-elle du trailing long ou d’un TP rapide ? Nécessite `EXIT_TRAILING` tag implémenté d’abord.
6. **`max_positions`** : quelle valeur pour la prod ? 6 ou 8 pour stresser les guards DD en replay, puis réduire pour le live.
7. **Filtre volume sur crypto et metals** : corrélation volume_ratio positive mais faible (+0.060), non implémentée.

### Exploration future

8. **Énergie, commodities, indices** : récupérer les parquets H1 via `barres_au_sol`, lancer le pipeline.
9. **Actions/équities** : à traiter avec précaution (gaps, earnings, liquidité variable). Pas de décision prise sur le pipeline de données.
10. **Stage 0 validation par catégorie** dans `pipeline.py` : voir `docs/instrument_selection_philosophy.md`.
11. **Pipeline automatisé mensuel** via systemd timer + notification Telegram/ntfy du rapport.
12. **Scorecard standardisé** : format JSON/CSV avec colonne `vs_baseline` pour toutes les explorations — à créer avant les prochaines explorations.
