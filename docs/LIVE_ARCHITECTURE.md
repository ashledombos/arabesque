# Arabesque — Évaluation architecture live et plan d'implémentation

> Auteur: Opus 4.6 — 2026-02-24
> Contexte: stratégie validée (1998 trades, 20 mois, IC99>0), passage au live.

---

## Verdict sur chaque point proposé

### 1. Multi-comptes / config YAML par compte — ✅ VALIDE, PRIORITAIRE

**Ce qui existe déjà** : `config/prop_firm_profiles.yaml` (créé cette session),
`PropConfig` dans guards.py, `OrderDispatcher` multi-broker.

**Ce qui manque** : le lien entre un profil YAML et un `AccountState` runtime.
Actuellement un seul `PropConfig` est instancié, pas un par compte.

**Recommandation** :
- Un fichier YAML par compte (pas un fichier monolithique)
- Séparation config statique / état runtime = OUI, c'est déjà le design
  (`PropConfig` = statique, `AccountState` = runtime)
- Commencer avec un compte par firm = OUI (sage pour réduire le risque)
- Les fichiers seraient : `config/accounts/ftmo_swing_01.yaml`,
  `config/accounts/gft_01.yaml`, etc.

**Effort** : moyen. Le dispatcher existe, il faut juste boucler sur N configs
au lieu d'une seule.

---

### 2. Règles prop firm machine-exécutables — ✅ CRITIQUE, TIER 1

**Ce qui existe déjà** :
- `daily_dd_pct` et `total_dd_pct` dans `AccountState` ✅
- `new_day()` pour le reset journalier ✅
- Guard DD daily et total dans `Guards.check_all()` ✅
- Safety margin (pause avant le seuil fatal) ✅

**Ce qui MANQUE (et c'est critique)** :
- **Timezone du reset** : `new_day()` est appelé mais sans logique timezone.
  FTMO reset à minuit CE(S)T, Topstep à 17h CT. Si on se trompe de 1 heure
  à cause du DST → breach silencieuse possible.
- **Worst-case pre-trade** : le guard `_max_positions` vérifie `open_risk_cash`
  mais ne fait PAS le calcul "si tous les SL sont touchés + ce nouveau trade
  aussi, est-ce qu'on dépasse le daily DD?". C'est le check le plus important.
- **Equity live vs balance** : `daily_dd_pct` utilise `equity` (correct),
  mais en replay l'equity n'est pas mise à jour en temps réel (bug connu
  du DryRunAdapter). En live ça devrait fonctionner car le broker fournit
  l'equity.

**Recommandation** :
- Ajouter `reset_timezone` et `reset_hour` dans la config par compte
- Implémenter le worst-case pre-trade check dans `_max_positions` ou
  un nouveau guard `_worst_case_budget`
- Vérifier que l'equity broker est bien lue en live (pas cached)

**Effort** : faible-moyen. Ce sont des modifications de Guards, pas une réécriture.

---

### 3. Risk management et allocation — ⚠️ VALIDE MAIS SECONDAIRE

**Ce qui existe déjà** :
- Sizing progressif basé sur DD restant (`compute_sizing` dans guards.py) ✅
- Max open risk (% du capital en risque simultané) ✅

**Ce qui est proposé** :
- **Sizing adaptatif** : déjà implémenté (réduction linéaire quand DD approche).
  La version actuelle est suffisante. L'augmentation en période de profit est
  DANGEREUSE et je la déconseille fortement — c'est du procyclique qui amplifie
  les DD quand le retournement arrive.
- **Recovery mode** : conceptuellement intéressant mais en pratique, le sizing
  progressif fait déjà le travail. Un mode "recovery" explicite risque de
  créer de la complexité sans bénéfice mesurable.
- **Cluster/corrélation** : PERTINENT sur le principe mais PRÉMATURÉ.
  Avec 76 instruments et 3.3 trades/jour, on n'a rarement plus de 2-3
  positions simultanées. Le risque de corrélation se manifestera quand on
  augmentera le volume. À ce stade, le guard `max_positions=5` suffit.

**Recommandation** :
- NE PAS toucher au sizing adaptatif (il marche)
- NE PAS implémenter le recovery mode (over-engineering)
- NE PAS implémenter les clusters maintenant (prématuré)
- DOCUMENTER que c'est P3/P4

**Effort** : zéro (on ne fait rien, c'est le bon choix).

---

### 4. Guards techniques — ✅ PARTIELLEMENT EXISTANT

**Ce qui existe déjà** :
- Spread guard (`_spread`) : vérifie spread < X × ATR ✅
- Slippage guard (`_slippage`) : vérifie écart signal vs prix broker ✅
- Duplicate instrument guard ✅
- BB squeeze guard ✅

**Ce qui MANQUE** :
- **Métadonnées instruments** : tick size, lot step, min lot, contract size.
  C'est CRITIQUE pour le sizing en live. Le replay n'en a pas besoin (il
  travaille en R), mais en live, un mauvais lot size = sizing faux = breach.
  Les brokers exposent ces données via API (cTrader SymbolInfo, TradeLocker
  instrument specs).
- **Invariant SL/TP post-ouverture** : vérifier que le SL est bien posé après
  le fill. Le code broker existe (`place_order` dans ctrader.py met le SL),
  mais il n'y a PAS de vérification asynchrone que le broker l'a bien accepté.
- **Post-execution read-back** : lire la position réelle après fill pour
  vérifier volume, prix, SL/TP. Pas implémenté.

**Recommandation** :
- Ajouter un `InstrumentSpec` dataclass chargé depuis le broker au démarrage
- Ajouter une boucle `verify_position_after_fill()` (10s après fill, relire)
- Logguer les divergences comme incidents

**Effort** : moyen. L'API broker est déjà là, il faut ajouter la lecture.

---

### 5. Circuit breaker — ✅ CRITIQUE, TIER 1

**Ce qui existe** : RIEN de spécifique. Le guard DD bloque les nouveaux trades
mais ne gère pas les incidents (SL absent, sizing incohérent, etc.).

**Ce qui est proposé** : parfaitement décrit. C'est exactement ce qu'il faut.

**Recommandation** :
- Un `CircuitBreaker` classe avec 3 états : `RUNNING`, `CAUTION`, `FROZEN`
- Déclencheurs : SL absent détecté, divergence sizing > 10%, exception broker,
  DD safety margin atteint
- Actions : CAUTION = log + alerte, pas de nouveaux trades pendant 5min.
  FROZEN = fermer tout, alerte urgente, plus rien jusqu'à déblocage manuel.
- Alerte = webhook Discord/Telegram (simple, fiable)
- Journal d'incident structuré (JSON, horodaté UTC)

**Effort** : moyen. C'est un nouveau composant mais la logique est simple.

---

### 6. Supervision "account health" — ✅ VALIDE, TIER 2

**Ce qui existe** : AccountState avec equity, DD, etc. Mais pas de boucle
de supervision continue.

**Recommandation** :
- Une boucle `asyncio` qui toutes les 30s :
  1. Lit l'equity de chaque broker
  2. Met à jour AccountState
  3. Vérifie invariants (SL présent sur chaque position ouverte)
  4. Calcule daily_dd_remaining et total_dd_remaining
  5. Log le statut (ok/caution/frozen)
- C'est simple, c'est un `while True: await asyncio.sleep(30)` avec des reads.
- Les cas limites (swaps, partial fills, mapping symboles) sont réels mais
  se gèrent au cas par cas, pas en préventif.

**Effort** : faible. C'est une boucle de monitoring, pas de logique complexe.

---

### 7. Shadow trading — ✅ EXCELLENTE IDÉE, TIER 2

**Ce qui existe** : RIEN, mais le DryRunAdapter pourrait servir de base.

**Pourquoi c'est excellent** : les 4 quadrants (pris/gagnant, pris/perdant,
refusé/gagnant, refusé/perdant) permettent de MESURER la qualité des guards
en production. Sans ça, tu ne sais jamais si un guard te coûte plus qu'il
ne te protège.

**Recommandation** :
- Chaque signal passe dans un `ShadowLedger` (dict en mémoire)
- Le shadow suit le trade virtuellement (MFE, résultat)
- Log quotidien des 4 quadrants
- STRICTEMENT séparé du réel (le shadow ne modifie jamais l'état réel)

**Effort** : moyen. Le code du replay (parquet_clock) fait déjà ça,
il faut l'adapter en live.

---

### 8. Tests "scénarios tordus" — ✅ OUI, OBLIGATOIRE AVANT LIVE

C'est la question la plus importante de ton message. La réponse est OUI,
absolument. Voir la section dédiée ci-dessous.

---

## Priorisation — 3 tiers

### TIER 1 — AVANT DE METTRE 1€ EN LIVE
1. Config par compte YAML avec timezone reset
2. Worst-case pre-trade check (budget restant)
3. Circuit breaker (freeze sur incident)
4. Post-execution readback (SL/TP bien posé?)
5. Tests scénarios tordus

### TIER 2 — PREMIÈRES SEMAINES DE LIVE
6. Shadow trading (4 quadrants)
7. Account health monitoring loop
8. Instrument specs depuis broker (tick size, lot step)
9. Alertes Discord/Telegram

### TIER 3 — QUAND ÇA TOURNE STABLE
10. Multi-compte même firm
11. Cluster/corrélation limits
12. News filter
13. Recovery mode (si vraiment nécessaire)

### REJETÉ / REPORTÉ INDÉFINIMENT
- Sizing procyclique (augmentation après gains) → DANGEREUX
- Recovery mode explicite → le sizing adaptatif suffit
- Optimisation cluster sophistiquée → prématuré

---

## Nettoyage fichiers morts

### À SUPPRIMER (confirmé mort)
```
HANDOVER.md                         # remplacé par HANDOFF.md
prop_firms.yaml (racine)            # doublon de config/
scripts/analyze_replay.py           # remplacé par v2
pine/arabesque_signal.pine          # ère TradingView
test_backtest.py                    # tests obsolètes racine
test_v2.py                          # tests obsolètes racine
docs/plan.md                        # supplanté par HANDOFF.md
docs/journal.md                     # supplanté par HANDOFF.md
docs/START_HERE.md                  # supplanté par HANDOFF.md
docs/ROADMAP.md                     # supplanté par HANDOFF.md
```

### À ÉVALUER (demander à Raph)
```
arabesque/screener.py               # screener Yahoo — encore utilisé?
docs/ARCHITECTURE.md                # sera remplacé par ce document
docs/TECH_DEBT.md                   # encore d'actualité?
docs/WORKFLOW_BACKTEST.md           # encore d'actualité?
docs/instrument_selection_philosophy.md
config/signal_filters.yaml          # encore utilisé en live?
scripts/run_pipeline.py             # pipeline v1?
scripts/update_and_compare.py       # ère Yahoo?
scripts/debug_pipeline.py           # encore utile?
```
