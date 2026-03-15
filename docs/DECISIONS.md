# Arabesque — Journal des décisions et expériences

> **Source de vérité sur le POURQUOI.**  
> Ce fichier documente ce qui a été essayé, ce qui a été abandonné, et pourquoi.  
> À lire avant de modifier la stratégie, le pipeline, ou les instruments.
>
> Synthèse de 4 conversations Perplexity (fev. 2026) + session courante.
> Mis à jour à chaque décision importante.

---

## Table des matières

0. [Boussole stratégique — IMMUABLE](#0-boussole-stratégique--immuable-prioritaire-sur-tout)
1. [Fondamentaux non négociables](#1-fondamentaux-non-négociables)
2. [Stratégie : ce qui a été abandonné et pourquoi](#2-stratégie--ce-qui-a-été-abandonné-et-pourquoi)
3. [Bugs connus, corrigés, et non corrigés](#3-bugs-connus-corrigés-et-non-corrigés)
4. [Instruments et catégories](#4-instruments-et-catégories)
5. [Gestion de position](#5-gestion-de-position)
6. [Pipeline de sélection des instruments](#6-pipeline-de-sélection-des-instruments)
7. [Infrastructure et données](#7-infrastructure-et-données)
8. [Questions ouvertes](#8-questions-ouvertes)

---

## 0. Boussole stratégique — IMMUABLE, PRIORITAIRE SUR TOUT

> **Cette section prime sur toutes les décisions de développement.**  
> Si tu es une IA qui reprend ce projet : lis cette section en premier et relis-la avant chaque suggestion.  
> Si quelque chose que tu t'apprêtes à proposer contredit ce qui est écrit ici : c'est ta proposition qui est fausse.

### L'objectif en lettres de feu

```
GAINS PETITS, FRÉQUENTS, CONSISTANTS.
PEU DE PERTES. PETITES QUAND ELLES ARRIVENT.
WIN RATE ÉLEVÉ : CIBLE ≥ 70%, IDÉAL ≥ 85%.
COURBE D'ÉQUITÉ RÉGULIÈRE ET PRÉVISIBLE.
```

Arabesque est une **stratégie prop firm**. Les prop firms évaluent la **consistance**, pas la performance brute. Une courbe d'équité régulière avec WR 85% passe un challenge. Une courbe en dents de scie avec WR 52% et quelques trades à +10R ne passe pas.

### La référence : BB_RPB_TSL

La stratégie dont Arabesque est dérivé tourne en live depuis ~527 jours :
- Win Rate : **90.8%**
- CAGR : ~48%
- Profil : petits gains fréquents, pertes bien délimitées

C'est **la preuve empirique que ce profil est atteignable** sur les altcoins crypto H1. Arabesque doit reproduire ce profil sur les instruments FTMO, avec les guards prop firm en plus.

### Ce qui est hors scope même si c'est "plus rentable"

- Stratégies avec WR < 65% (même si expectancy positive) → trop de variance pour les limites DD
- Trailing SL long au détriment du WR → transforme des gagnants en perdants potentiels
- Optimisation de l'avg_win au prix du WR → profil incompatible prop firm
- TP à 2R, 3R, ou plus → WR chute mécaniquement

### Signal d'alarme à déclencher

Si tu lis dans le code, les docs, ou une proposition IA :
- "WR ~52% compensé par avg_win de 2.3R" → **DÉRIVE, CORRIGER**
- "l'edge vient des grands mouvements" → **DÉRIVE, CORRIGER**
- "sensibilité aux outliers acceptée" → **DÉRIVE, CORRIGER**
- "le module trend réduit le WR mais améliore l'expectancy" → **DÉRIVE** — le trend est un bonus optionnel, pas une raison d'accepter un WR plus bas

### Contexte historique de la dérive (2026-02-21)

Le système a dérivé vers WR 52% à cause de l'introduction d'un trailing SL long (5 paliers, premier palier BE à +0.5R). Cette configuration :
- Transforme des trades gagnants (auraient touché le TP) en trades incertains (le prix peut revenir)
- Multiplie la variance par trade par 3.8× vs le profil BB_RPB_TSL
- Rend les résultats statistiquement non mesurables sur 3 mois de données

La correction est en cours. Ne pas retomber dans cette dérive.

### Correction v3.0 — ROI dégressif (2026-02-21, session Opus 4.6)

**Analyse racine de la divergence** : lecture détaillée de `BB_RPB_TSL.py` → la pièce manquante identifiée est le `minimal_roi`, le mécanisme de TP dégressif dans le temps. C'est ce mécanisme qui produit le WR 90.8%, pas les entrées.

**BB_RPB_TSL utilise** :
- `minimal_roi = {"0": 0.205, "81": 0.038, "292": 0.005}` → prend le profit disponible
- `stoploss = -0.99` → SL quasi inexistant (laisse respirer)
- Trailing custom uniquement au-dessus de +3% → ne trail que les "bonus"
- `custom_exit` ferme les trades avec petits profits quand le momentum faiblit

**Arabesque v2 avait** :
- Pas de ROI dégressif → les trades devaient toucher le TP fixe (bb_mid) ou être trailés
- Trailing dès +0.5R → interférait avec les profits MR normaux
- SL à 0.8×ATR → trop serré pour le mean-reversion
- Time-stop à 48h → coupait les trades trop tôt (BB_RPB_TSL donne 292h)

**Arabesque v3.0 corrige** :
1. ROI dégressif en R (0→3R, 48→1R, 120→0.5R, 240→0.15R)
2. Trailing réservé aux bonus trades (>= 1.5R MFE, 3 paliers au lieu de 5)
3. SL minimum élargi (0.8 → 1.5 ATR)
4. BE relevé (+0.5R → +1.0R)
5. Time-stop étendu (48 → 336 barres)

**Valeurs ROI volontairement rondes** pour éviter la sur-optimisation. Le principe (TP dégressif dans le temps) est l'insight clé, pas les valeurs exactes.

**À valider** : replay P3a pour mesurer l'impact sur le WR.

### Correction v3.1 — Diagnostic post-replay (2026-02-21, session Opus 4.6)

**Résultats v3.0** : WR=50.6%, expectancy=+0.094R, 786 trades. ROI quasi inutile (2.3% des sorties).

**5 problèmes identifiés par analyse du JSONL v3.0** :

1. **42% des trades (328/786) ferment en ≤3 barres avec WR=34.8%**
   - SL 1.5 ATR touché trop vite
   - Solution : élargir à 2.0 ATR

2. **ROI inutile (18 trades, 2.3%)**
   - Tiers à 48/120/240 barres inadaptés : durée médiane des trades = 3h
   - Solution : tiers courts (6/12/24/48/120h) adaptés à la distribution réelle

3. **BE à 1.0R trop haut → 39% des losers avaient MFE ≥ 0.5R**
   - Ces trades passent en positif puis reviennent au SL
   - Solution : abaisser BE à 0.5R

4. **BB sur Close au lieu de typical_price**
   - BB_RPB_TSL utilise (H+L+C)/3 via qtpylib.typical_price()
   - Solution : changer compute_bollinger() pour utiliser typical_price

5. **RSI oversold=35 trop permissif**
   - Solution : resserrer à 30 + min_bb_width de 0.003→0.02

**Principe de décision** : chaque changement est justifié par une donnée mesurée dans le replay v3.0, pas par l'intuition.

**À valider** : replay P3a-bis.

### Correction v3.2 — BE offset + SL recalibration (2026-02-22, session Opus 4.6)

**Résultats v3.1** : WR=63.9% (+13 pts ✅), exp=-0.004R ❌, 568 trades

**Analyse des données v3.1 — le "BE phantom exit" :**
- 165 trades (29%) sortent à exactement +0.05R (le BE offset)
- Ces trades avaient un MFE médian de 0.78R
- Le SL à entry+0.05R est touché par le bruit OHLC normal
- Cas extrême : XAUUSD MFE=5.31R → sorti à +0.05R (1% capturé)
- 45.7% de tous les gains sont < 0.1R → avg_win effondré à 0.548R

**Deuxième problème — SL 2.0 ATR trop large :**
- R = |entry - SL| = 2.0 × ATR → très grand
- Un mouvement favorable de X$ donne X/(2×ATR) en R au lieu de X/(1.5×ATR)
- avg_win comprimé d'un facteur 1.33

**Simulations sur les données v3.1 :**
- BE offset 0.25R seul → +30.7R (exp +0.054R)
- SL 1.5 ATR seul → +63.9R (exp +0.113R)
- **Combiné → +107.9R (exp +0.190R)**

**Corrections appliquées :**
1. BE offset : 0.05R → 0.25R (manager.py)
2. SL : 2.0 → 1.5 ATR (signal_gen.py)

**À valider** : replay P3a-ter.

### Pivot v3.3 — Retour base v3.0 + chirurgie (2026-02-22, session Opus 4.6)

**Résultats v3.2** : WR=60.6%, exp=-0.010R, total=-6.4R, 622 trades

**Bilan des 4 itérations v3.0→v3.2** :
- v3.0 : seule version rentable (+73.9R, exp=+0.094R)
- v3.1/v3.2 : WR amélioré mais expectancy négative
- Chaque tentative de copier le ROI court BB_RPB_TSL a tué l'avg_win

**Insight clé — l'incompatibilité ROI court + SL réel** :
BB_RPB_TSL n'a pas de SL réel (-99%) → couper les profits tôt ne coûte rien.
Arabesque a SL = -1R → chaque SL doit être compensé par avg_win.
ROI court → avg_win chute sous le seuil → exp négative.

**Décision : ABANDONNER le ROI court-terme, revenir à v3.0**

v3.3 = v3.0 + 3 améliorations qui n'affectent PAS avg_win :
1. BB typical_price (qualité signal, n'affecte pas la durée des trades)
2. BE 0.5R/0.25R (convertit des losers en +0.25R, augmente WR)
3. Giveback 0.5R (capture les profits qui s'érodent, augmente WR)

**À valider** : replay P3a-quater.

### Modèle analytique v3.3 — BE 0.3/0.15 (2026-02-22, session Opus 4.6)

**Résultats v3.3 (BE 0.5/0.25)** : WR=60.2%, exp=+0.034R, +33.5R, 998 trades

**Grille de simulation post-hoc** testée sur les 998 trades v3.3 ET les 786 trades v3.0 :

Chaque trade a un MFE connu. Si MFE ≥ BE_trigger, le trade serait protégé.
Si MFE < BE_trigger, le trade sortirait au SL (-1R).
Si le trade revient sous BE_offset après trigger, il sort à +offset.
Si le trade continue au-dessus, il sort au résultat réel.

Résultats (robustes sur 2 datasets) :

| Config | WR v3.3 | Exp v3.3 | WR v3.0 | Exp v3.0 |
|---|---|---|---|---|
| BE 0.3/0.15 | 79.7% | +0.250R | 78.5% | +0.414R |
| BE 0.5/0.25 | 68.4% | +0.130R | 67.4% | +0.314R |
| BE 1.0/0.05 | 36.9% | -0.294R | 49.0% | +0.021R |

**Pourquoi BE 0.3 > BE 0.5** :
- 80% des trades atteignent MFE ≥ 0.3R (vs 68% pour ≥ 0.5R)
- La tranche [0.3, 0.5) = 112 trades convertis de -1R en +0.15R
- Impact net : +128.8R supplémentaires

**Risque offset 0.15R** :
- v3.1 avait échoué à 0.05R (trop serré pour le bruit OHLC)
- 0.15R = 3× plus = 0.225 ATR de marge
- 74% des trades triggés restent au-dessus de 0.15R

**Décision** : implémenter BE 0.3/0.15 dans manager.py.
**À valider** : replay avec la nouvelle config.

### Analyse diversifiée — 46 instruments, 319 trades (2026-02-22, session Opus 4.6)

**Résultat global** : WR=64.3%, Exp=-0.044R, Total=-13.9R, 319 trades

**DÉCOUVERTE MAJEURE** :
- Mean Reversion perd sur TOUTES les catégories : crypto -35.2R, forex -7.1R, commodities -2.4R
- Trend gagne sur TOUTES les catégories : crypto +19.1R, forex +2.5R, commodities +6.3R
- MR LONG spécifiquement : 141 trades, -46.1R (le gouffre principal)
- MR SHORT : 115 trades, +4.3R (marginal)

**Cause racine MR** : le problème est l'ENTRÉE, pas la SORTIE.
29% des trades MR n'atteignent jamais +0.3R MFE (le prix continue à chuter après le BB touch).
Les 71% qui atteignent 0.3R MFE ont WR 81% et Exp +0.143R → le BE fonctionne correctement.

**Tous les sub-types MR perdent** : shallow_narrow -17.8R, shallow_wide -14.1R, deep_wide -8.5R

**Trend** : 63 trades, WR 84%, Exp +0.442R, Total +27.8R
Sub-types trend_moderate (+16.7R) et trend_strong (+11.1R) tous les deux profitables.

**Spikes de données** : 9 trades avec MFE > 10R sur 1 barre (XPTUSD 60.7R, UK100.cash 40R...).
Ces instruments ont des données corrompues qui faussent les résultats.

**Plan de validation** : 3 replays isolés (crypto BE 0.3/0.15, trend-only diversifié, MR-only crypto).
L'objectif est de déterminer la config optimale : MR-crypto + Trend-tout, ou Trend-only.

### Replay A+B — Pivot TREND-ONLY + Découverte données (2026-02-23, session Opus 4.6)

**Replay A** (combined crypto, Avr→Jul): 169 trades, WR 65%, Exp -0.083R, -14.1R. Score 1/5.
MR crypto sur 2e période = négatif. Confirme que MR crypto n'est pas robuste.

**Replay B** (trend-only diversifié, Avr→Jul): 570 trades, WR 71%, Exp +0.037R, +21.2R. Score 3/5.
DD 6.9%, +8.4% equity. Seule stratégie profitable sur les 2 périodes testées.

**DÉCOUVERTE MAJEURE — Qualité des données × Performance** :
Les instruments Dukascopy (forex + métaux avec données 1min) font +29.3R sur 229 trades (WR 79%).
Les instruments CCXT (crypto) font -11.0R (net négatif en trend).
Les instruments sans données 1min (indices/energy/agri) sont responsables de TOUS les spikes.

La performance n'est pas uniforme : 14/19 instruments Dukascopy sont rentables vs 4/12 crypto.

**Décision : BE offset 0.15 → 0.20R**
323/339 trailing exits étaient des BE à exactement +0.15R (MFE moyen 0.68R).
L'offset est trop serré : 95% des trades triggés ressortent au plancher.
Simulation BE 0.3/0.20 sur Replay B: +60.1R vs +42.8R avec 0.15, vs +21.2R réel.

**Décision : TREND-ONLY comme stratégie principale**
MR abandonné après échec sur 3 configurations différentes (2 périodes, 2 univers).
Trend gagne sur forex, métaux, commodités — perd seulement sur crypto.

**Prochaine étape** : Replay TREND-ONLY sur instruments Dukascopy seulement, période Oct→Jan,
pour validation croisée sur 2e période.

### Validation 20 mois — Résultat définitif (2026-02-24, session Opus 4.6)

**Replay 20 mois** (Jul 2024 → Fév 2026, 76 instruments): 1998 trades, WR 75.5%, Exp +0.130R, +260.2R.
Max DD 20.5R = 10.3% à 0.5%/trade. PF 1.55. IC99 > 0. Score 4/5.
8/8 blocs temporels positifs — aucune période perdante.

**RÉVISION — Crypto trend est rentable sur 20 mois**
La conclusion "Dukascopy only" (session précédente) était basée sur une seule période (Avr→Jul).
Sur 20 mois : Dukascopy +115.2R (49 inst), Crypto +145.0R (27 inst).
Le crypto trend SURPERFORME le forex trend. Le drawdown Avr-Jul était localisé, pas structurel.
Les 4 blocs temporels crypto sont tous positifs et en accélération (+21→+26→+44→+53R).

**Décision : risk_per_trade 0.50% → 0.40%**
Max DD 20.5R. À 0.50%: DD=10.3% (DÉPASSE FTMO 10%). À 0.40%: DD=8.2% (marge 1.8%).
Return à 0.40%: +104% sur 20 mois. Largement au-dessus du target 10%.
Modifié dans guards.py (PropConfig.risk_per_trade_pct) et engine.py (replay default).

**Multi-compte** : profils créés dans config/prop_firm_profiles.yaml.
Distribution d'instruments entre comptes pour éviter doublons et respecter règles anti-duplication.

### Résultats des 3 replays de validation (2026-02-23, session Opus 4.6)

**Replay 1 — Combined crypto, BE 0.3/0.15** : 999 trades, WR 68.6%, Exp +0.050R, Total +49.9R, DD 14.1%, Score 3/5.
  BE 0.3/0.15 confirme l'amélioration vs BE 0.5/0.25 (+8pts WR, +16.4R).
  Écart simulation/réalité : +181R prédit vs +50R réel.
  Cause : 124 SL losers avaient MFE ≥ 0.3R mais TOUS sur 1 seule barre (spikes).
  Règle pessimiste = SL pris avant le high quand les deux sont dans la même barre.

**Replay 2 — Trend-only diversifié** : 429 trades, WR 70.9%, Exp +0.065R, Total +27.7R, DD 8.8%, Score 2/5.
  Le trend fonctionne sur crypto (+17.3R), forex (+10.3R), commodities (+0.1R).
  En mode combined, 303 signaux trend étaient bloqués par des positions MR.
  Le DD de 8.8% est le meilleur de tous les replays (prop-firm compatible).

**Replay 3 — MR-only crypto** : 29 trades, WR 31%, Total -10.9R. TEST INVALIDE.
  BacktestSignalGenerator ≠ MR component de CombinedSignalGenerator.
  29 vs 873 trades = générateurs complètement différents.

**Découverte : SHORT >> LONG dans tous les replays** (Oct 2025 → Jan 2026).
  R1: SHORT +64.3R vs LONG -14.4R
  R2: SHORT +30.9R vs LONG -3.2R
  R3: SHORT +5.1R vs LONG -16.0R
  → Biais possiblement saisonnier, à vérifier sur une autre période.

**Prochaines étapes** :
  1. Replay sur période différente (Avr-Jul 2025) pour vérifier biais SHORT
  2. Intégrer données 1-min pour résoudre ambiguïté intrabar (124 SL fantômes = ~143R)
  3. Filtre spike : écarter bougies range > X ATR

---

## 1. Fondamentaux non négociables

Ces décisions sont **définitives**. Ne pas revenir dessus sans raison forte et documentée.

### Stratégie
- **Mean-reversion est l’edge principal**, pas le breakout. Justification : asymétrie de slippage. Le MR achète quand le prix descend (slippage neutre ou favorable). Le breakout achète quand ça monte (slippage adverse).
- **`mean_reversion` est la base de production.** Le module trend (`combined`) est un bonus optionnel — utile s'il améliore le WR sans ajouter de complexité, à mettre en pause sinon. ⚠️ Le WR ~35% historique de `mean_reversion` seule date d'une période sans filtre de qualité propre (données Yahoo, paramètres non calibrés) — à re-mesurer sur données Parquet propres avant toute conclusion définitive.
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

### `mean_reversion` seule sans filtre
**Suspendue, pas définitivement abandonnée.**  
Le WR ~35% mesuré date d'une période avec données Yahoo et paramètres non calibrés. À re-mesurer sur données Parquet propres sur 2 ans avant de conclure. Le module trend (`combined`) est un bonus optionnel : à conserver s'il améliore le WR, à mettre en pause s'il ajoute de la complexité sans gain mesurable.

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

### Diagnostic replay 0.4% — 999 vs 1998 trades (2026-02-24)

Le replay à 0.40%/trade produit 999 trades au lieu de 1998. Cause identifiée:
le guard `worst_case_budget` ajouté dans `check_all()` utilise `open_risk_cash`
qui n'est pas correctement décrémenté dans le DryRunAdapter (bug connu de l'equity
tracking en replay). Résultat: 1151 trades rejetés à tort (phantom rejects), dont
Exp +0.136R et Total +156R.

Solution: `worst_case_budget` activé UNIQUEMENT en live (`Guards.live_mode=True`).
En replay, le guard est ignoré. Le guard reste fondamental pour le live car il
vérifie que le risque cumulé (positions ouvertes + nouveau trade) ne dépasse pas
le budget daily DD restant.

Impact: les 999 trades restants montrent DD 5.0%, Score 5/5. Le run complet à
0.40%/trade (sans le guard) devrait donner DD ≈ 8.2% (20.5R × 0.4%), ce qui
reste sous FTMO 10%.

### Live Engine Bugs — 2026-02-25

**Symptôme** : `python -m arabesque.live.engine --dry-run` crash avec
`google.protobuf.message.EncodeError: Message ProtoOAGetTrendbarsReq is missing required fields: fromTimestamp,toTimestamp`
suivi de timeouts sur tous les instruments.

**Cause racine** : `get_history()` dans `broker/ctrader.py` ne remplissait pas les champs `fromTimestamp` et `toTimestamp` du proto `ProtoOAGetTrendbarsReq`, alors qu'ils sont requis par l'API cTrader.

**Bugs latents découverts** :
- `_decode_trendbar()` utilisait des champs proto inexistants (`tb.open`, `tb.high`). Le proto cTrader définit `low` (absolu) + `deltaOpen/deltaHigh/deltaClose`. Jamais atteint car get_history crashait avant.
- `_process_spot_event()` utilisait un diviseur hardcodé `100000` au lieu du diviseur spécifique au symbole (incorrect pour crypto, indices, commodities).
- SpotEvents incrémentaux : le proto n'envoie que le champ modifié (bid ou ask), mais le code exigeait les deux. Corrigé avec fallback sur le dernier prix connu.

**Améliorations** :
- Chargement historique parallèle dans `bar_aggregator.py` (Semaphore(5) + gather).
- Thread-safety de `_process_trendbar_response()` via `call_soon_threadsafe`.

**Décision** : corrections pures, aucune modification de stratégie ou paramètres de trading.

### Live Engine Bugs (suite) — 2026-02-26

**Bug CRITIQUE : `_symbol_id_for_name()` retourne toujours le premier symbole**

La condition `sinfo.broker_symbol == str(sid)` est toujours vraie car `broker_symbol = str(symbol_id)` et `sid = symbol_id`. Conséquence : TOUTES les opérations (subscribe_spots, get_history, get_last_tick) utilisaient le symbolId du premier symbole du dict (probablement EURUSD/270).

Impact :
- subscribe_spots : seul EURUSD souscrit, les 82 autres symboles trouvaient 270 déjà dans `_subscribed_symbol_ids`
- get_history : les 83 instruments chargeaient les barres d'EURUSD sous des noms différents
- Aucun tick reçu pour 82/83 symboles

**Fix** : recherche par nom exact + normalisation (suppression de `/`, `.`, `-`, `_` pour matcher EUR/USD ↔ EURUSD) + recherche par ID numérique.

**Autres fixes** :
- `_process_spot_event()` crashait avec `RuntimeError: no current event loop in thread` car `asyncio.get_event_loop()` dans le thread Twisted. Remplacé par `self._asyncio_loop`.
- PriceFeedManager réutilise le broker existant (évite ALREADY_LOGGED_IN).
- `_send_no_response()` helper pour subscribe/unsubscribe (fire-and-forget avec errback).
- Chargement séquentiel (pas parallèle) car cTrader mono-TCP.
- Warnings condensés (résumé vs 83 lignes/30s).
- Lazy imports dans `live/__init__.py` (supprime RuntimeWarning).

### Live Stability Fixes — 2026-02-27

**Problème 1 : Reconnexion en boucle pour symboles illiquides**

ALGUSD, NEOUSD, XAGUSD (fermé le vendredi) déclenchaient ConnectionError dès 5 min sans tick, provoquant une reconnexion globale toutes les 2 min. Sur 83 symboles, 76+ fonctionnaient correctement.

**Fix** : Logique de stale detection à 3 niveaux :
- Majeurs (G10, XAU, BTC, ETH) : seuil 5 min → reconnexion
- Mineurs : seuil 30 min → tolérance (pas de reconnexion)
- Global : reconnexion si >50% stale
- Weekend : forex/métaux stale tolérés (vendredi 22h → dimanche 22h UTC)

**Problème 2 : ALREADY_SUBSCRIBED lors de reconnexion**

`_connect_and_subscribe()` clearait `_subscribed_symbol_ids` avant de re-souscrire. Le serveur cTrader gardait les souscriptions actives → erreur.

**Fix** : Si broker connecté + souscriptions actives : rafraîchir uniquement les callbacks Python sans requête TCP.

**Problème 3 : Fermetures de bougies invisibles**

`_on_bar_closed` en DEBUG → aucune trace dans les logs INFO. Impossible de confirmer que le système fonctionne.

**Fix** : Passé en INFO + résumé groupé : "X barres fermées, Y signaux émis".

**Problème 4 : settings.yaml mal configuré**

- Pas de section `strategy` → defaultait à "combined" (inclut MR perdante). Ajout `strategy.type: trend`.
- `risk_percent: 0.5` au lieu de 0.40 validé. Corrigé.

**Décision** : corrections opérationnelles uniquement. Aucune modification stratégique.

### 2026-02-28 : Weekend stale fix + TypeError live_mode

**Problème 5 : Reconnexion en boucle le weekend (tentative #1→#150)**

Le check global `total_stale / total_symbols > 50%` ne tenait pas compte du weekend.
52 paires forex/métaux fermées le vendredi soir = 63% des 83 symboles → seuil 50% dépassé → reconnexion toutes les 2 min, indéfiniment. Les bougies crypto se fermaient correctement malgré la boucle (pas de perte de données), mais le log était pollué et les callbacks étaient rafraîchis inutilement.

**Fix** : pendant le weekend (ven 22h→dim 22h UTC), le check global ne compte que les crypto (31 symboles). Ajout d'un set `CRYPTO_SYMBOLS` pour classifier proprement forex vs crypto. Le check majeur utilisait déjà `{"BTCUSD", "ETHUSD"}` mais le check global ignorait le weekend.

**Problème 6 : TypeError `TrendSignalGenerator.__init__() got an unexpected keyword argument 'live_mode'`**

`_make_signal_generator()` passait `live_mode=True` à `TrendSignalGenerator` qui n'accepte pas ce paramètre (seul `BacktestSignalGenerator` le supporte). Crash au démarrage.

**Fix** : retiré `live_mode=True`. Pas nécessaire car le `BarAggregator` filtre déjà la dernière bougie côté appelant (via `last_idx`).

**Décision** : corrections opérationnelles. Le moteur crypto a tourné 6h30 sans interruption ni signal (normal : ~0.15 signaux/heure sur 31 crypto). Le vrai test viendra lundi avec le forex ouvert.

### 2026-02-28 (2) : Order dispatch fix — 0 ordres placés sur 19 signaux

**Problème 7 : CRITIQUE — instruments_mapping vide dans les brokers**

Le factory `create_all_brokers()` construisait `instruments_mapping` depuis `settings["instruments"]` qui est `{}` dans settings.yaml. Les instruments réels sont dans un fichier séparé `config/instruments.yaml`, chargé par le moteur dans `self.instruments`, mais PAS passé au factory. Résultat : `broker.map_symbol("BCHUSD")` → `None` → "non disponible sur ftmo_swing_test" pour TOUS les symboles. 19 signaux générés, 11 acceptés par les filtres, mais 0 ordres placés.

**Fix** : le moteur passe maintenant `self.instruments` au factory. Log ajouté : "(N instruments mappés)" au démarrage pour vérifier.

**Problème 8 : TradeLocker stop_price**

La lib tradelocker-python exige `stop_price` (pas `price`) pour les ordres `type_='stop'`. BCHUSD et BNBUSD généraient l'erreur `Order of type_ = 'stop' specified with a price, instead of stop_price`.

**Fix** : split du bloc `if tl_type != 'market'` en deux cas : `limit` → `price`, `stop` → `stop_price`.

**Analyse des signaux du 28/02** : 19 signaux générés (tous SHORT crypto, cohérent avec le dump en cours). Symboles récurrents : MANUSD(4), XRPUSD(3), BCHUSD(3), XLMUSD(3), ALGUSD(3). Rejets principaux : slippage ATR (5), spread ATR (3). Le dispatcher fonctionnait correctement côté filtres, seul le placement échouait.

**Décision** : fix critique. Le système était fonctionnel à 95% — seul le dernier maillon (symbole mapping → ordre) était cassé.

### 2026-02-28 (3) : Architecture multi-compte, amend/close, scripts de test

**Analyse architecture cTrader OpenAPI** :
- 1 connexion TCP supporte N authentifications de comptes simultanées
- Les ticks (spots) sont souscrits via un account_id, mais les symboles disponibles dépendent du broker sous-jacent
- L'architecture actuelle (1 CTraderBroker = 1 connexion = 1 compte) fonctionne pour un seul compte cTrader
- Phase 2 future : `CTraderConnection` (singleton TCP) + `CTraderAccount` (contexte par compte)

**Architecture TradeLocker** :
- API REST (pas WebSocket), pas de connexion persistante
- 1 TLAPI instance par compte, HTTP calls à la demande
- L'architecture actuelle est déjà adaptée au multi-compte

**Ajout amend_position_sltp + close_position** :
- `base.py` : méthodes non-abstraites (default: "Not implemented")
- `ctrader.py` : implémentation via ProtoOAAmendPositionSLTPReq et ProtoOAClosePositionReq
- `_process_order_response` refactorisé : boucle de priorité sur les 4 types de requêtes
- Wrappers synchrones ajoutés dans CTraderBrokerSync

**Scripts de test** :
- `test_order_flow.py` : cycle MARKET BUY → amend SL → close position, avec confirmation utilisateur
- `test_connectivity.py` : vérifie connexions, mappings, comptes (non destructif)

**Naming instruments.yaml** : la clé YAML = nom unifié utilisé dans tout le pipeline. Doit correspondre au nom cTrader car les ticks arrivent avec ce nom. Le mapping broker-spécifique ne sert que quand le nom diffère (ex: EURUSD → EURUSD.X sur TradeLocker).

**Décision** : ajouts opérationnels et outils de diagnostic.

### 2026-03-01 : Order flow validé, gap breakeven identifié

**Bugs corrigés dans `ctrader.py` (order flow)** :
1. `timeInForce` enum `GTC` → `GOOD_TILL_CANCEL` (+ skip pour MARKET)
2. Volume multiplier `10_000_000` → `100` (centilots)
3. `close_position()` : champ volume obligatoire dans le protobuf
4. `_process_order_response` : retourne positionId pour les opérations position
5. `_resolve_symbol_name()` : résout symbolId numérique → nom lisible

**Validation complète du cycle de vie** :
- MARKET BUY → position créée ✅
- amend SL → modifié (quand SL valide par rapport au bid) ✅  
- close position → fermée ✅
- close_positions.py → nettoyage des 4 orphelines ✅

**Gap critique identifié** : le `PositionManager` (breakeven 0.3/0.20R + trailing) existe dans `position/manager.py` pour le backtest mais n'est PAS câblé dans le live engine. Concrètement, les ordres sont placés avec SL/TP initial, mais le mécanisme de breakeven qui génère 75.5% WR ne se déclenche jamais en live. Prochain chantier prioritaire (P0).

**Décision** : valider le cycle d'ordres complet avant d'attaquer le position management live.

### 2026-03-01 (session 2) : Fix digits + Live Position Monitor

**Root cause digits** : `ProtoOASymbolsListRes` retourne `ProtoOALightSymbol` qui n'a PAS de champ `digits` ni `pipPosition`. `getattr(s, "digits", 5)` retournait toujours 5 (défaut). Fix : après chargement des symboles légers, appel `ProtoOASymbolByIdReq` (par batch de 50) pour obtenir `ProtoOASymbol` complet avec digits, volumes, lot_size.

**Live Position Monitor** (`arabesque/live/position_monitor.py`) :
- `TrackedPosition` : dataclass avec entry, sl, R, mfe_r, breakeven_set, trailing state
- `LivePositionMonitor.on_bar_closed()` : vérifie BE/trailing sur chaque H1 bar close
- BE : MFE >= 0.3R → SL → entry + 0.20R (identique au backtest)
- Trailing : tiers 1.5R/2.0R/3.0R → distances 0.7R/1.0R/1.5R
- `_try_amend_sl()` : retry avec backoff (max 3 tentatives, anti-spam 5s)
- `reconcile()` : nettoie les positions fermées (boucle 2 min dans engine)

**Câblage engine.py** :
- `_make_position_monitor()` : crée le monitor après connexion brokers (sauf dry_run)
- `BarAggregator.add_bar_closed_callback()` : nouveau callback sur chaque H1 bar close
- `_register_position_in_monitor()` : query get_positions() pour fill price, puis register
- `_reconcile_loop()` : boucle asyncio 2 min

**Décision** : implémenter uniquement BE + trailing (les 2 mécanismes les plus impactants). Giveback, deadfish, time-stop sont des optimisations secondaires.

### 2026-03-02 : Fix diviseur de prix cTrader + Normalizer + Volume validation

**Root cause prix 100x gonflés** : `_process_symbol_details()` changeait `pip_size` (ex: USDJPY 0.0001→0.01), ce qui changeait le diviseur de décodage des prix cTrader de 100000 → 1000. Les SpotEvents et Trendbars cTrader encodent TOUS les prix en entiers avec une précision fixe de 10^5 (100000), indépendamment de `digits` ou `pipPosition` du symbole. Le `digits` proto ne sert qu'à l'arrondi des prix dans les ordres.

**Fix** : Séparation complète du diviseur de prix et des métadonnées symbole :
- `_symbol_divisors: Dict[int, int]` stocke le diviseur fixe (100000) par symbole
- `_get_divisor(symbol_id)` utilisé dans les 3 points de décodage (spots, trendbars, historique)
- `_process_symbol_details()` met à jour digits/volumes mais ne touche PLUS au diviseur
- Suppression de toute dérivation `pip_pos = -log10(pip_size)` pour le diviseur

**Conséquences du bug** :
- USDJPY décodé à 15683 au lieu de 156.83 → risk_distance 100x trop grand → volume 100x trop petit → TRADING_BAD_VOLUME
- BTCUSD décodé à 6714355 au lieu de 67143.55 → même problème
- FETUSD/GALUSD : problème séparé de min_volume trop élevé sur cTrader (10-100 lots minimum pour certains crypto)

**Normalizer** (`arabesque/broker/normalizer.py`) :
- `validate_order()` : validation pré-envoi (volume min/max/step, arrondi prix, cohérence SL/TP)
- `get_broker_volume_info()` : debug des contraintes volume par symbole
- Câblé dans `order_dispatcher.py` : rejet clair AVANT envoi au broker

**Volume validation dans cTrader** :
- `place_order()` vérifie maintenant volume vs min/max/step du symbole
- Rejet local avec message explicite au lieu d'attendre l'erreur cTrader
- Arrondi au step_volume automatique

**Décision** : le diviseur 10^5 est hardcodé car empiriquement correct pour tous les symboles testés (forex, JPY, crypto). Si un broker cTrader utilise un diviseur différent, il faudra le paramétrer dans settings.yaml.

### 2026-03-02 (session 2) : Volume sizing par-broker + validation post-trade

**Root cause volumes trop bas** : `_compute_lots()` dans le dispatcher utilisait un seul `pip_value_per_lot` de instruments.yaml, identique pour tous les brokers. Or les brokers ont des `contract_size` (lot_size) différents pour le même symbole :
- cTrader FTMO : BNBUSD lot_size=100 (1 lot = 100 BNB) → 0.4L × $10.09 × 100 = $400 ✓
- TradeLocker GFT : BNBUSD lot_size=1 (1 lot = 1 BNB) → 0.4L × $10.09 × 1 = $4 ✗

**Fix** : `_compute_lots_for_broker()` — calcule le volume par-broker :
- Query `broker.get_symbol_info()` pour obtenir le `lot_size` réel
- `pip_value_per_lot = lot_size × pip_size` (calculé dynamiquement)
- Fallback sur instruments.yaml si SymbolInfo indisponible
- Log détaillé : lot_size source, pip_value calculé vs yaml, contraintes broker

**Validation post-trade** :
- `_log_trade_validation()` dans le dispatcher : logge type d'ordre, slippage, SL/TP, RR
- `_register_position_in_monitor()` : retry 3× avec délai croissant (2/4/6s) pour trouver la position
- Log de fill confirmé : entry réel vs signal, slippage mesuré, SL/TP effectifs

**Position monitor** :
- Grace period de 5 minutes : `reconcile()` ne retire plus les positions < 5 min
- Log amélioré à la suppression : MFE, état BE, tier trailing

**Décision** : le lot_size du broker est la source de vérité pour le sizing. `instruments.yaml pip_value_per_lot` ne sert plus que de fallback. Pas besoin de normalizer séparé pour le sizing — chaque broker a son propre lot_size dans SymbolInfo.

### 2026-03-02 (session 3) : Fix minVolume default — faux rejet BNBUSD

**Root cause** : `_process_symbol_details()` utilisait `getattr(s, "minVolume", 100)` comme défaut. En proto2, si le serveur ne remplit pas le champ `minVolume`, le default de 100 s'applique → `100/100 = 1.0 lot` minimum au lieu de `0.01 lot`. Résultat : BNBUSD (0.4L calculé) rejeté en pré-vol parce que `0.4 < 1.0`.

**Fix** : Changement des defaults proto :
- `minVolume` : 100 → 1 (1 centilot = 0.01 lots)
- `stepVolume` : 100 → 1 (1 centilot = 0.01 lots)
- Ajout de logging pour les symboles crypto : valeurs brutes du proto à chaque chargement

**Diagnostic** : `scripts/diag_symbol_specs.py` — affiche les valeurs brutes du proto (minVolume, maxVolume, stepVolume, lotSize, digits, pipPosition) pour vérifier ce que le serveur renvoie réellement. À lancer sur FTMO pour confirmer.

**Note** : Perplexity confirme que FTMO autorise 0.01 lot minimum pour BNBUSD (1 BNB ≈ $645), pas 1 lot (100 BNB ≈ $64,500). Le min_volume=1.0L venait du mauvais default, pas du serveur.

### 2026-03-03 : Fix fondamental unités volume cTrader + barres dupliquées + logs

**Root cause volume NZDCAD 0.01L au lieu de 2.30L** :
cTrader API utilise des "cents" (1/100 de l'unité de base) pour TOUS les volumes, y compris `lotSize`, `minVolume`, `maxVolume`, `stepVolume`, et `req.volume`. Notre code multipliait par 100 (hardcodé) au lieu de multiplier par `lotSize`.

- NZDCAD: lotSize=10,000,000 (=100,000 NZD/lot). Notre 2.30L × 100 = 230 → cTrader lit 230/10M = 0.000023 lots. Devrait être 2.30L × 10M = 23M → cTrader lit 23M/10M = 2.30 lots.
- BTCUSD: lotSize=100 (=1 BTC/lot). 0.01L × 100 = 1 → cTrader lit 1/100 = 0.01 lots. Marchait PAR COINCIDENCE.

**Fix complet** :
- `_lot_size_cents[symbol_id]` : stocke le lotSize brut du proto par symbole
- `_get_lot_size_cents()` : helper pour récupérer
- `place_order()` : `req.volume = lots × lot_size_cents` au lieu de `lots × 100`
- `close_position()` : idem
- `_process_reconcile_response()` : `lots = volume / lot_size_cents` au lieu de `volume / 100`
- `_process_symbol_details()` : `min_volume = minVolume / lotSize` au lieu de `minVolume / 100`

**Fix barres dupliquées** : Race condition async dans `on_tick()`. Plusieurs ticks NZDCAD arrivant en même temps voyaient tous `bar_start_ts > current_start` avant la mise à jour → 12 fermetures de bougie → 12 BE triggers → 12 amends → timeout cascade. Fix: `_last_closed_ts` per-symbol dedup guard.

**Fix amend spam** : `_amend_in_progress` flag per-position dans TrackedPosition, vérifié dans `_try_amend_sl`. Un seul amend à la fois par position.

**Réduction logs** :
- Barres individuelles : INFO → DEBUG (invisible en production)
- Résumé barres : conservé en INFO
- PriceFeed status : INFO → DEBUG sauf s'il y a des problèmes (stale/no ticks)
- Account state : refresh toutes les heures au lieu de 5 min, DEBUG au lieu de INFO
- Exécution events DEBUG : supprimé (commenté)
- Résultat : en opération normale, seuls signaux, trades, BE/trailing, et erreurs apparaissent

### 2026-03-03 (session 2) : Fix pip_value devise cotation + BE price validation

**Bug sizing USDJPY** : `pip_value = lot_size * pip_size` = 1000 JPY, mais risk en USD.
Fix : conversion selon devise de cotation (XXX/USD=direct, USD/XXX=/price, cross=yaml).
USDJPY: 1000/157.8 = 6.34 USD/pip/lot -> 1.60L au lieu de 0.01L.

**Bug BE TRADING_BAD_STOPS** : MFE calcul sur high (0.37R), mais bid retombe a 157.718
alors que BE target = 157.886. cTrader rejette SL > bid pour BUY.
Fix : validation prix courant dans _try_amend_sl avant envoi au broker.
Si SL infaisable, log "price fell back" et retry au bar suivant.

**Log reduction** :
- Individual bar load : DEBUG (83 lignes)
- get_history print : supprime (83 lignes)  
- Premier tick : supprime (83 lignes)
- PriceFeed symbols : count au lieu de liste

### 2026-03-03 (session 2b) : Alignement backtest/live BE faisabilite

**Divergence identifiee** : le PositionManager (backtest) posait le BE meme quand
be_level > close (LONG) ou be_level < close (SHORT). Le live rejette via
TRADING_BAD_STOPS. Resultat : le backtest surestimait les wins de +0.2R sur les
trades ou le prix retombait apres avoir touche le trigger.

**Fix** : _update_breakeven et _update_trailing dans manager.py verifient maintenant
que le new_sl est faisable au prix de cloture avant de le poser.
- LONG : be_level <= close requis (sinon skip, retry bar suivant)
- SHORT : be_level >= close requis

Identique au live qui valide new_sl <= bid (LONG) dans _try_amend_sl.

**Impact attendu** : degradation de ~1-3% du win rate sur replay.
A valider par un re-replay complet sur 20 mois.

### 2026-03-04 : Analyse live, outils comparaison, bugs identifies

**Nouveaux scripts:**
- `scripts/parse_live_trades.py` : parse live.log → rapport structuré + export jsonl
- `scripts/fast_validate.sh` : backtest rapide sur basket representatif (11 instruments, 3/6/20 mois)

**Bugs identifies dans le live du 03-04/03:**
1. FILL_MISMATCH (CRITIQUE): fill LNKUSD (entry=9.192, 1L) enregistre comme SOLUSD.
   Cause probable: routage des fills asynchrones quand 3 ordres simultanes.
   Le monitor a un "SOLUSD" avec R=78.67 (absurde). Position non trackee correctement.
2. ORDER_TIMEOUT phantom: LNKUSD et BNBUSD reportes timeout mais ouverts chez le broker
   (visibles dans cTrader). Le fill arrive apres le timeout → position non trackee.
3. XAUUSD stale feed: 31 reconnections en boucle (21:55-23:11). Le callback refresh
   ne suffit pas — marche ferme, pas de ticks a recevoir.
4. ICPUSD BE jamais pose: trigger 3x (04:00, 05:00, 06:00) mais prix toujours
   en-dessous du be_level au close → skip 3x → SL hit. Perte -$410 (-1R).
   Le monitoring minute avec paliers aurait pu poser le BE pendant les mèches.

**Basket validation rapide (11 instruments):**
BTCUSD ETHUSD SOLUSD BNBUSD LNKUSD ICPUSD EURUSD USDJPY GBPUSD NZDCAD XAUUSD
Couvre: crypto USD-quoted, forex USD/XXX (conversion /price), forex cross (yaml), metal.

### 2026-03-04 (P0) : Fix fill mismatch concurrent orders

**Root cause:** _pending_requests est un dict avec une cle fixe "order_place" par type
d'operation. Quand 3 ordres partent en concurrence (3x create_task sur
_dispatch_to_all_brokers), chaque appel a place_order() ecrase la future du precedent.
Resultat: le fill SOLUSD resout la future de BNBUSD → signal/fill mismatch.
Les ordres orphelins timeout apres 30s → positions non trackees.

**Fix:** asyncio.Lock (self._order_lock) serialise place_order, amend_position_sltp,
close_position et cancel_order. Un seul appel en vol a la fois → pas d'ecrasement.

**Defense en profondeur:** validation dans _register_position_in_monitor:
si abs(fill_entry - signal_close) > 5R → FILL MISMATCH detecte, position non enregistree.
Log CRITICAL pour investigation manuelle.

**Bonus:** SL/TP du monitor utilise maintenant les valeurs du broker (pos.stop_loss,
pos.take_profit) au lieu du signal quand disponibles → plus precise apres slippage.

### 2026-03-05 : Fix dispatch séquentiel, réconciliation startup, resubscribe TCP

**Constat live 04-05/03:** Malgré le lock dans ctrader.py, les ordres sont toujours concurrents
(3 ordres avec order_id=3124148). Cause: create_task() lance des tâches parallèles qui
entrent toutes dans place_order avant que le lock ne soit évalué (même event loop tick).

**Fix 1 — Dispatch séquentiel (order_dispatcher.py):**
- Remplacé create_task(_dispatch_to_all_brokers) par une asyncio.Queue FIFO
- Worker unique traite les signaux un par un
- Garantit : UN SEUL ordre en vol à la fois, peu importe combien de signaux triggent

**Fix 2 — Réconciliation startup (engine.py):**
- _reconcile_existing_positions() au démarrage de l'engine
- Interroge tous les brokers pour les positions ouvertes
- Les enregistre dans le monitor (BE/trailing reprend automatiquement)
- Corrige: positions orphelines après restart/crash

**Fix 3 — Resubscribe TCP après échecs (price_feed.py):**
- Après 5 tentatives de reconnexion échouées: force un resubscribe TCP complet
- Clear _subscribed_symbol_ids → batch subscribe like initial connection
- Corrige: ETHUSD stale pendant 17h+ (451 tentatives) sans resubscription

**Impact attendu:**
- Fills correctement routés (chaque ordre = unique position_id)
- Positions pré-existantes trackées pour BE/trailing
- Feed stale récupéré en <10 min au lieu de jamais

### 2026-03-06 : TSL tick-level monitoring + fix stale XAUUSD

**Contexte:** Tous les trades perdent car le BE/trailing ne vérifie qu'au close H1.
BNBUSD: monté +1R en intrabar puis crash au SL. BE skippé 7x en H1 pour LNKUSD
(session précédente). Les pertes s'accumulent: -$1959 le 05-06/03.

**Fix 1 — TSL tick-level (position_monitor.py):**
- Nouveau on_tick(tick) souscrit à tous les ticks via le price feed
- Throttled à 10s par position (pas à chaque tick)
- Met à jour le MFE en continu via update_mfe_tick(price)
- Vérifie breakeven et trailing paliers sur le bid/ask réel
- Prix de référence: bid pour LONG (on vend), ask pour SHORT (on rachète)
- Faisabilité vérifiée par _try_amend_sl comme avant

**Fix 2 — Stale XAUUSD (price_feed.py):**
- Forex stale nécessite maintenant >= 2 majeurs stale pour déclencher reconnexion
- 1 seul forex stale (ex: XAUUSD maintenance 21-22h) → log DEBUG, pas de reconnexion
- Élimine les 100+ reconnections inutiles pendant le break quotidien XAUUSD

**Impact attendu:**
- BE posé en quelques secondes quand le prix franchit 0.3R (au lieu d'attendre H1 close)
- Trailing activé dès que le MFE atteint les paliers 1.5R/2.0R/3.0R
- Plus de reconnection spam pendant les maintenances XAUUSD
- Divergence backtest/live: le backtest reste sur H1, le live fait MIEUX (bonus acceptable)

**Note divergence backtest:** Le backtest (PositionManager) vérifie toujours sur H1.
Le monitoring tick est un avantage live-only. Pour backtester avec des M1, il faudra
adapter ParquetClock pour charger les barres M1 et y brancher le check BE/trailing.
Les données M1 existent dans barres_au_sol/data/{ccxt,dukascopy}/min1/.

### 2026-03-06 (P0) : Fix duplicate positions + log spam + account state

**Root cause des 21 trades/jour:** _refresh_account_state ne remplissait JAMAIS 
open_instruments ni open_positions. Toujours [] et 0. Le guard _duplicate_instrument
ne rejetait donc aucun signal → EURCHF ouvert 3x, EURCAD 2x, etc.

**Fix 1 — AccountState complet (engine.py):**
- _refresh_account_state interroge maintenant get_positions() + position_monitor
- Remplit open_positions, open_instruments, open_risk_cash
- Rafraîchi toutes les 2 minutes (au lieu de 1h)
- Rafraîchi après chaque ordre placé

**Fix 2 — Marquage immédiat (order_dispatcher.py):**
- Dès qu'un signal est accepté, son instrument est ajouté à open_instruments
- Empêche les doublons dans la même batch (5 signaux à H1 close)

**Fix 3 — Log spam (position_monitor.py):**
- Premier skip → INFO, suivants → DEBUG
- Élimine les 200+ lignes de "BE/Trail skipped" par heure

**Impact attendu:**
- ~5 trades/jour au lieu de 21 (un seul par instrument)
- Guards fonctionnels: duplicate instrument, max_positions, worst_case_budget
- Logs lisibles

**Note TSL v7:** Le TSL tick a sauvé $3,055 sur la journée (6 trades clôturés en 
profit grâce au BE). Sans le TSL, la perte aurait été -$8,079 au lieu de -$5,024.


---

## Dette technique — Infrastructure de validation (2026-03-15)

> Ce qui a été implémenté vs. ce qui reste à faire pour la méthodologie
> de validation multi-instruments progressive.

### Implémenté (session 2026-03-15)

- **Champ `strategy` + `category` dans JSONL backtest** — permet de filtrer
  l'historique des runs par stratégie et par famille d'actifs.
- **Synthèse multi-instruments dans le CLI** — tableau agrégé par catégorie +
  total après un `--mode backtest` avec plusieurs instruments.
- **`config/universes.yaml`** — univers d'instruments prédéfinis (`crypto`,
  `forex_majors`, `metals`, `quick`, `all`). Usage : `--universe crypto`.

### À faire — priorité haute

- **JSONL dédié aux shadow filters** — actuellement les logs `👻` vont dans
  stdout et sont perdus. Créer `logs/shadow_filters.jsonl` avec : timestamp,
  instrument, strategy, filter_name, signal_side, would_have_blocked, et les
  indicateurs au moment du signal. Sans ça, impossible de prendre une décision
  d'activation fondée sur des données.
- **Agrégation shadow filters** — outil CLI (`python -m arabesque shadow-report`)
  qui lit le JSONL et calcule WR/Exp avec et sans chaque filtre, sur N trades.
  Seuil de décision : ≥ 100 trades, WR↑ ET Exp↑.

### À faire — priorité moyenne

- **Registre d'étape par stratégie** — savoir où en est chaque stratégie dans
  le pipeline (backtest IS → OOS → dryrun → shadow → live), depuis quand,
  combien de trades accumulés à chaque étape. Fichier YAML ou section HANDOFF.
- **Tracking slippage réel vs. backtest** — en live, comparer le fill réel au
  fill estimé (bid/ask au moment du signal). Persister dans le JSONL de trades.
  Permet de calibrer `spread_pct` et `slippage_r` du backtest.
- **Tracking frais/commissions** — par compte, par instrument. Nécessite
  l'info post-fill du broker (variable selon compte).

### À faire — priorité basse

- **Pipeline mensuel automatisé** — systemd timer → backtest `--universe all`
  → synthèse → notification Telegram/ntfy si une stratégie dérive vs. baseline.
- **Scorecard standardisé JSON/CSV** — format unifié pour comparer les runs
  entre eux, avec colonne `vs_baseline` (delta expectancy, delta WR, etc.).
- **Isolation multi-stratégie en live** — l'orchestrateur doit séparer les
  trades par stratégie sur des comptes différents. Pas de mélange test/prod.

### À faire — Backtest accéléré par élagage de créneaux

**Problème :** le backtest M1 sur longue période est très lent (828k barres
pour XAUUSD = ~1.6 an). Le live-test Parquet est encore plus lent (plusieurs
heures). Le téléchargement des données aussi. Tout ça bloque l'itération.

**Principe :** ne traiter que les créneaux temporels pertinents au lieu d'itérer
sur chaque barre. Plusieurs versions possibles, de la plus simple à la plus
complète.

**Version 1 — Skip des périodes mortes (règle statique)**
Le plus simple. Pour Fouetté (ORB NY) : les signaux ne peuvent arriver qu'entre
14h30 et 20h00 UTC. Si aucune position n'est ouverte hors de cette fenêtre,
on skip. Gain estimé : ~70% des barres ignorées = **~3x plus rapide**.
Implémentation : filtre sur l'index du DataFrame avant de le passer au runner,
ou mode "créneaux" dans le runner qui saute les barres non pertinentes.
Limite : ne s'applique qu'aux stratégies avec fenêtre de session fixe.

**Version 2 — Passe HTF → zoom M1 sur les trades identifiés**
Plus puissant, applicable à toute stratégie. En deux passes :
1. Backtest rapide sur la HTF native (H1 pour Extension, H4, etc.) — quelques
   secondes. Identifie chaque trade : timestamps d'entrée et de sortie.
2. Pour chaque trade, charger uniquement la tranche M1 correspondante
   (entry_time - marge indicateurs → exit_time + marge) et rejouer en
   tick-level pour résoudre l'ambiguïté intrabar (SL vs TP touché en premier,
   trailing précis, ordre de visite des niveaux).

Gain estimé : ~50 tranches × ~200 barres M1 = 10k barres au lieu de 828k.
Rapport **~80x plus rapide** qu'un backtest M1 complet.
La marge avant chaque créneau est nécessaire pour stabiliser les indicateurs
(EMA, ATR) avant le signal.
Nécessite un module planificateur (ex : `arabesque/execution/planner.py`)
qui orchestre passe 1 → créneaux → passe 2.

**Version 3 — Hybride : passe HTF + skip session + cache des créneaux**
Combine v1 et v2. Le planificateur produit des créneaux via la HTF, les
restreint aux fenêtres de session si applicable, et les cache dans un fichier
pour ne pas refaire la passe 1 à chaque itération de paramètres.

```
┌────────────────┐
│ Passe 1 (HTF)  │  rapide, identifie les créneaux pertinents
│ ou règle fixe   │  (session NY, trades identifiés, etc.)
└───────┬────────┘
        │ liste de créneaux [(start, end), ...]
        ▼
┌────────────────┐
│ Passe 2 (M1)   │  charge uniquement les tranches Parquet
│ BacktestRunner  │  résout l'intrabar avec précision
└────────────────┘
```

**Points de code concernés :**
- `store.py` : charger des tranches Parquet par plage de dates (filtre index)
- `BacktestRunner` : mode créneaux ou réception de DataFrames pré-tronqués
- Nouveau module `planner.py` : orchestration passe 1 → créneaux → passe 2

**Statut :** conception. Pas de code écrit. Prioriser v1 si le gain 3x suffit,
v2 si les backtests HTF→M1 deviennent le workflow principal.

### À faire — Robustesse statistique et biais de sélection

Issus d'une analyse des bonnes pratiques en trading algo prop firm (2026-03-15).
Arabesque couvre déjà IS/OOS, Wilson CI99 et Monte Carlo basique. Ce qui manque :

**Walk-forward validation (priorité haute)**
Le split IS/OOS fixe (70/30) teste sur UNE fenêtre OOS. Le walk-forward recalibre
sur fenêtres glissantes (ex : 12 mois IS → 3 mois OOS, avance de 3 mois, répète).
Avantage : détecte les stratégies qui ne survivent pas à un changement de régime.
Le résultat agrégé sur toutes les fenêtres OOS est beaucoup plus robuste qu'un
seul split. À implémenter dans `pipeline.py` ou comme mode dans `BacktestRunner`.

**PBO — Probability of Backtest Overfitting (priorité moyenne)**
Quand on teste N variantes sur le même historique (ex : 5 variantes Fouetté),
la probabilité de sélectionner du bruit augmente avec N. PBO (Bailey & López de
Prado) estime la probabilité que la "meilleure" variante soit en réalité du
surfit. Pertinent avant de prendre une décision sur les paramètres d'une
stratégie. Implémentation : CSCV (Combinatorially Symmetric Cross-Validation)
sur les résultats de backtest.

**Deflated Sharpe Ratio (priorité basse)**
Correction du Sharpe pour le nombre de variantes testées. Même logique que PBO
mais sous forme de ratio corrigé. Utile si on industrialise le screening de
variantes (ex : grid search sur les paramètres Fouetté).

### À faire — Guards prop firm manquants

**Guard "Best Day" / consistance (priorité haute)**
FTMO impose que le meilleur jour ne représente pas plus de X% du profit total
des jours positifs. Arabesque ne vérifie pas cette contrainte. Un trade
exceptionnellement gagnant peut invalider un challenge même si le total est bon.
Implémentation : tracker le P&L par jour dans `AccountState`, vérifier en fin
de run. Peut aussi servir de guard live (alerter si la journée en cours
s'approche du seuil).

**Guard trailing drawdown (priorité moyenne)**
Certaines firms (Topstep, Apex) utilisent un DD trailing : le plancher remonte
avec chaque nouveau pic d'equity (parfois intraday, parfois en fin de journée).
Structurellement différent du DD absolu actuel dans `guards.py`. Avec un DD
trailing, un profit latent qui retrace peut être fatal même si le P&L réalisé
est positif. À ajouter comme variante dans `PropConfig` (type: "absolute" |
"trailing" | "trailing_eod"), car le choix dépend de la prop firm ciblée.

**Corrélation inter-positions (priorité moyenne)**
Le guard actuel `open_risk_cash` additionne les risques individuels. Sur 19
cryptos corrélées à >0.7, le risque réel est très supérieur au risque additionné
(un mouvement adverse touche toutes les positions simultanément). À améliorer :
soit un facteur de corrélation par catégorie (ex : crypto × 2.5, forex × 1.5),
soit une matrice de corrélation glissante. Le facteur par catégorie est plus
simple et suffisant pour une première version.

**Fenêtre news (priorité basse)**
Certaines firms (The5ers) interdisent l'exécution ±2 minutes autour des
annonces à fort impact. Pas encore implémenté dans les guards. Nécessite un
calendrier économique (source : ForexFactory, Investing.com, ou API dédiée).
Pour Fouetté (ORB NY) c'est particulièrement pertinent car la session NY
ouvre souvent sur des annonces US.

### À faire — Analyse avancée

**Monte Carlo sur barrières (priorité haute)**
Le Monte Carlo actuel dans `pipeline.py` estime la distribution des métriques.
Ce qui manque : estimer la **probabilité d'atteindre le profit target AVANT de
toucher le DD max**, qui est LA question pour un challenge prop firm. Se modélise
comme un problème de temps d'atteinte de barrières (gambler's ruin avec drift).
Entrée : distribution des trades (R), barrières (profit target, max DD).
Sortie : P(succès), temps médian, P(ruine).

---

## Session 2026-03-15 — Premier backtest Fouetté (ORB M1) sur XAUUSD

### Corrections techniques apportées

**1. Off-by-one dans `_build` (signal.py)**
`_build` retournait `(signal_bar_idx + 1, sig)` alors que la convention du runner
est : index retourné = barre signal, fill = index + 1. Résultat : fill 2 barres
après le signal au lieu d'1. Corrigé → `(signal_bar_idx, sig)`.

**2. `_tag_or_bars` vectorisé**
Boucle Python pure O(n) sur ~1M barres M1. Remplacée par opérations numpy/pandas.
La fonction helper `_ny_open_hour_utc` supprimée (devenue inutile).

**3. Guards recalibrés pour M1 (ExecConfig dédié dans `__main__.py`)**
Guards H1 (`max_spread_atr=0.15`, `max_slippage_atr=0.10`) rejetaient 97% des
signaux M1 : ATR M1 XAUUSD ~$0.80 vs ATR H1 ~$15, donc spread normal ($0.30)
représente 0.38×ATR M1 → au-dessus des deux seuils.
Décision : ExecConfig `(max_spread_atr=0.5, max_slippage_atr=0.5)` passé au
runner uniquement pour la stratégie fouetté. Filtre les moments illiquides
(spread > 50% ATR) sans rejeter les conditions M1 normales.

### Résultat backtest XAUUSD (jan 2024 → mars 2026, 828k barres)

```
Mode          : fvg_multiple, range=30m, RR=1.0
Trades        : 308  (172 rejetés : spread_too_wide)
Win Rate      : 70.8%
Expectancy    : -0.024R  ← NÉGATIF
Total R       : -7.4R
PF            : 0.89
Max DD        : 6.3%

Exits :
  exit_trailing  : 175 trades  avg +0.20R  ← sort au plancher BE
  exit_sl        :  55 trades  avg -1.00R
  exit_tp        :  37 trades  avg +0.69R  ← TP trop rare
  exit_time_stop :  36 trades  avg -0.38R  ← stagnation longue (avg 88 barres M1)

MFE : 74% des trades < 0.5R
```

### Diagnostic

La stratégie ne génère pas assez d'extension après le retest FVG sur XAUUSD.
Le TP à `rr_tp=1.0` (= 1× le range) est rarement atteint (37/308 = 12%).
Le BE convertit les trades en +0.20R mais l'avg_loss (-0.76R) creuse l'expectancy.

### Exploration systématique — 5 variantes testées

| Config | Trades | WR | Expectancy | PF | Max DD |
|---|---|---|---|---|---|
| Baseline (rr_tp=1.0, PM normal) | 308 | 70.8% | -0.024R | 0.89 | 6.3% |
| rr_tp=2.0, PM normal | 308 | 70.8% | -0.053R | 0.76 | 8.2% |
| **TP fixe, sans PM** | **299** | **54.8%** | **+0.011R** | **1.02** | **6.9%** |
| range=15min, PM normal | 338 | 69.5% | -0.056R | 0.80 | 8.8% |
| EMA actif, PM normal | 285 | 69.8% | -0.030R | 0.87 | 6.3% |
| TP fixe + EMA actif | 279 | 55.2% | +0.002R | 1.00 | 6.8% |

**Seul résultat positif : TP fixe sans position manager (+0.011R, PF 1.02).**

Structure de cette config :
- 164 exit_tp @ +0.84R vs 135 exit_sl @ -1.00R
- MFE : 50% des trades entre 0.5R et 1.0R — distribution supporte le TP à 1×range
- Sensibilité slippage élevée : à 1.5× slippage → -0.034R (négatif)

### Conclusions

**Pourquoi augmenter rr_tp aggrave :** 74% MFE < 0.5R. Le prix ne va pas chercher
2×range. La distribution MFE ne supporte pas un TP distant.

**Pourquoi range=15min échoue :** même distribution MFE, le PM ramène tout à +0.20R.
Génère 56 jours disqualifiants (DD > 8%) — incompatible prop firm.

**Pourquoi EMA actif n'aide pas :** filtre autant de bons que de mauvais setups sur
XAUUSD M1 avec période=20. Supprime 20 trades sans améliorer l'expectancy.

**Diagnostic racine :** la stratégie fvg_multiple sur XAUUSD ne génère pas assez
d'extension post-retest pour absorber les coûts de transaction réels. L'expectancy
brute (+0.011R) est trop proche de zéro pour être robuste en live.

### Pistes restantes (décision Opus requise)

- Autres instruments : indices (US500, NAS100), crypto (BTCUSD) — l'ORB NY open
  peut mieux fonctionner sur des instruments avec momentum plus fort
- `sl_source="fvg"` : SL au bord de la FVG (plus serré) → meilleur R/R intrinsèque
- Mode `breakout` pur (sans FVG) : plus de trades, moins de filtrage
- Combinaison TP fixe + `sl_source="fvg"` : R/R amélioré sans dépendre du PM

**Statut : recherche. Ne pas déployer en live. Soumettre à Opus pour la suite.**

---

## Décision 2026-03-12 — Restructuration v9 : architecture multi-stratégie

**Contexte :** Arabesque v8c est en production live. Le code présente 6 problèmes
structurels : (1) deux générateurs de signal qui peuvent diverger, (2) scripts/
en bazar, (3) trop de points d'entrée doc, (4) dépendance fragile barres_au_sol,
(5) pas de protection contre les vraies prod accounts, (6) pas de framework
multi-stratégie.

**Décision :** Restructuration complète en Phase 1/2/3, sans modifier aucune
logique de trading. Shims de compat pour tous les anciens chemins d'import.

**Nouveauté clé — Signal unique :**
`arabesque/strategies/extension/signal.py` est THE seul générateur de signal.
Utilisé à l'identique par backtest, dryrun, et live. Fusionne l'ancien
`backtest/signal_gen_trend.py` et `backtest/signal_labeler.py`.

**Convention de nommage des stratégies :**
Noms tirés des disciplines artistiques de la souplesse et de l'acrobatie
gracieuse (danse classique, GR, GAF, danse aérienne, natation artistique, etc.).
Vocabulaire français, relation imagée avec la logique de trading.
Voir `docs/HYGIENE.md` section 3 pour les détails et la liste des noms disponibles.

**Tests unitaires :** placeholder uniquement pour l'instant. Les tests seraient
des nouvelles fonctionnalités, pas de la restructuration.

**barres_au_sol :** les repos restent indépendants. La logique de fetch est
dupliquée dans `arabesque/data/fetch.py`. Pas de git submodule (trop fragile).
`parquet_clock.py` reste séparé (source de barres distincte de l'exécution).
