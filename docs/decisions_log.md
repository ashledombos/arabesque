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
