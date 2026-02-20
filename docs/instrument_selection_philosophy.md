# Arabesque — Philosophie de sélection des instruments

> **Document de référence** — à lire avant de modifier `instruments.yaml` ou les critères du pipeline.
> Synthèse des réflexions menées depuis la genese du projet.

---

## 1. Principe directeur : valider une catégorie, pas un instrument

L’intuition centrale est la suivante : **si la stratégie fonctionne sur une catégorie d’instruments, c’est le signe d’un phénomène structurel**. Si elle ne fonctionne que sur 2 instruments isolés parmi 30 de la même catégorie, c’est probablement du bruit ou de l’overfitting.

### Pourquoi cette approche réduit l’overfitting

La sélection classique par instrument optimise instrument par instrument : on garde ceux qui ont eu les meilleurs chiffres sur la période testée. Le problème est que certains instruments ont été en hype, en cycle favorable, ou simplement chanceux sur la fenêtre analysée. En traitant la catégorie comme unité de validation, on s’intéresse à la question plus robuste : **« est-ce que le mécanisme de MR fonctionne structurellement sur les alt-coins crypto ? »** plutôt que « est-ce que XTZUSD a bien marché en 2025 ? »

### Ce que ça implique concrètement

- Un instrument **légèrement négatif ou neutre** sur la période testée **ne doit pas automatiquement être exclu** si sa catégorie valide globalement. Les périodes de marché varient : un instrument peut traverser un cycle de tendance forte (défavorable au MR) puis revenir en range.
- En revanche, un instrument qui **met en danger la stratégie** (DD excessif, trop de jours disqualifiants, signaux aberrants) doit être exclu indépendamment de sa catégorie.
- Les critères d’exclusion par instrument sont donc **des garde-fous de sécurité**, pas des critères de performance.

---

## 2. Les deux types de critères

### Critères de catégorie (laxistes — anti-overfitting)

Ces critères s’appliquent au **score agrégé de la catégorie**. L’idée est de valider que le mécanisme fonctionne structurellement.

| Critère | Seuil recommandé | Logique |
|---|---|---|
| % instruments avec expectancy > 0 | ≥ 50% | La majorité des instruments capte l’edge |
| Expectancy médiane de catégorie | > -0.05R | La catégorie n’est pas structurellement négative |
| Profit Factor médian | > 0.85 | Dégradation acceptable |
| Nombre d’instruments testables | ≥ 5 | Pas de conclusion sur 2-3 instruments |

Si une catégorie **passe ces critères**, on inclût **tous** ses instruments sauf ceux bloqués par les garde-fous (voir ci-dessous).

### Critères d’exclusion par instrument (garde-fous)

Ces critères servent à éliminer les instruments dangereux, pas les instruments médiocres.

| Critère | Seuil d’exclusion | Logique |
|---|---|---|
| Jours disqualifiants FTMO | > 0 sur OOS | Risque concret de perdre le compte |
| DD max (OOS) | > 8% | Dépasse la limite FTMO directement |
| Spread moyen / ATR | > 50% | Le spread écrase l’edge à l’exécution |
| Données insuffisantes | < 2000 barres H1 | Instrument trop récent pour conclure |
| Expectancy OOS | < -0.30R | Clairement négatif, pas juste neutre |

---

## 3. Historique des approches utilisées

### Approche 1 — Sélection naïve (Envolées, avant Arabesque)

**Principe** : on backteste, on garde les instruments avec la meilleure performance IS (In-Sample).

**Problème identifié** : toutes les configs validées en IS sont devenues négatives une fois les biais corrigés (anti-lookahead). La stratégie Donchian breakout n’avait pas d’edge réel — le backtest flattait les biais d’exécution.

**Leçon** : la performance IS seule est insuffisante. Un OOS propre est obligatoire.

### Approche 2 — Pipeline IS + OOS (Arabesque v1)

**Principe** : 3 stages successifs (signal count → IS backtest → OOS backtest). Un instrument ne passe que s’il survit aux 3 stages.

**Seuils (mode `default`)** :
- Stage 1 : ≥ 50 signaux sur la période
- Stage 2 IS : Profit Factor > 0.8, expectancy > -0.10R, DD < 10%
- Stage 3 OOS : mêmes seuils sur la deuxième moitié de la période

**Résultat observé (2026-02-20)** : 80 testés → 77 Stage 1 → 31 Stage 2 → 17 viables.
16 crypto + 1 metal (XAUUSD). 0 FX sur 43 testés.

**Limite** : approche par instrument, risque de sélectionner les « chanceux » de la période testée.

### Approche 3 — Stats avancées post-pipeline (`run_stats`)

**Principe** : sur les survivants du pipeline, appliquer un filtre statistique plus rigoureux.

- **Wilson CI** sur le win rate : est-ce que le WR observé est statistiquement signé à 95% au-dessus de 50% ?
- **Bootstrap 1000 itérations** sur l’expectancy : distribution empirique, borne basse du CI 95%
- **Dégradation IS → OOS par fenêtre glissante** : est-ce que la performance est stable dans le temps ou concentrée sur une sous-période ?

**Seuil clé** : bootstrap 95% CI borne basse > 0R. Si cette borne est négative, l’edge n’est pas démontré statistiquement à ce nombre de trades.

### Approche 4 — Validation par catégorie (en cours de réflexion)

**Principe** : ne pas évaluer un instrument isolément mais valider d’abord que **la catégorie est réceptive** à la stratégie, puis inclure tous les instruments de la catégorie sauf les dangereux.

**Logique anti-overfitting** :
- Si 12/16 crypto passent le pipeline, ce n’est pas un hasard : le mécanisme BB MR est structurel sur les alt-coins.
- Exclure NERUSD parce qu’il a un WR de 52% au lieu de 60% est potentiellement de l’overfitting : il peut très bien performer sur la prochaine période.
- En revanche, exclure un instrument avec des jours disqualifiants ou un DD > 8% est une décision de sécurité légitime.

**Ce que ça changerait dans le pipeline** : ajouter un « Stage 0 » qui calcule un score de catégorie avant d’appliquer les seuils par instrument. Si la catégorie est validée, les seuils IS/OOS par instrument sont assouplis (passer en mode `wide` automatiquement).

**Non encore implémenté**.

---

## 4. Le cas FX : pourquoi zéro viable

Le run du 2026-02-20 donne 0/43 FX viables. Deux explications probables :

1. **BB width trop faible** : les paires FX majeures ont des ranges comprimés. Le filtre `bb_width > 0.003` élimine une grande fraction des barres, faisant chuter le signal count en Stage 1.
2. **Contexte de marché 2024-2025** : le FX a été en tendance forte (USD dominant). La branche MR rejette les signaux LONG en régime `bear_trend`, réduisant encore le nombre de signaux.

**Ce n’est pas une conclusion définitive.** Le FX peut être viable sur d’autres périodes. À tester :
```bash
python scripts/run_pipeline.py --list fx --mode wide --period 1825d -v
```

---

## 5. Les catégories non encore testées

| Catégorie | Instruments FTMO | Pourquoi pas encore testés | Potentiel MR |
|---|---|---|---|
| Indices | US30, US500, DE40, UK100... | Pas de parquets locaux | Moyen — souvent en tendance |
| Énergie | USOIL, UKOIL, NATGAS | Pas de parquets locaux | Bon — forte volatilité BB |
| Commodités agri | WHEAT, CORN, SUGAR... | Pas de parquets locaux | Inconnu |
| Actions | Individuel | Gaps, earnings, liquidité variable | Risqué |

Pour les tester : récupérer les parquets H1 via `barres_au_sol`, les déposer dans `data/parquet/`, relancer le pipeline.

---

## 6. Règles pratiques actuelles (2026-02-20)

```
Catégorie validée = ≥ 50% des instruments passent Stage 3

Inclure si :
  - Catégorie validée ET
  - DD OOS ≤ 8% ET
  - Jours disqualifiants OOS = 0 ET
  - Données ≥ 2000 barres

Exclure même si catégorie validée :
  - DD OOS > 8%
  - Jours disqualifiants > 0
  - Spread moyen / ATR > 50% (illiquidité rédhibitoire)

Ne pas exclure uniquement pour :
  - WR légèrement inférieur à la médiane de catégorie
  - Expectancy OOS entre -0.10R et 0R (neutre, pas dangereux)
  - Performance IS inférieure à la médiane (peut être cycle défavorable)
```

---

## 7. Statut implémentation

| Approche | Implémentée | Fichier |
|---|---|---|
| Pipeline IS + OOS 3 stages | ✅ | `arabesque/backtest/pipeline.py`, `scripts/run_pipeline.py` |
| Stats avancées (Wilson, bootstrap) | ✅ | `arabesque/backtest/stats.py`, `scripts/run_stats.py` |
| Validation par catégorie | ❌ Non | À ajouter dans `pipeline.py` (Stage 0) |
| Pipeline automatisé mensuel | ❌ Non | À implémenter avec systemd timer |
