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
9. [Nouvelles stratégies — pipeline d'implémentation](#nouvelles-stratégies--pipeline-dimplémentation-2026-03-15)

---

## 0. Boussole stratégique — PRIORITAIRE SUR TOUT

> **Cette section prime sur toutes les décisions de développement.**
> Si tu es une IA qui reprend ce projet : lis cette section en premier et relis-la avant chaque suggestion.
> Si quelque chose que tu t'apprêtes à proposer contredit ce qui est écrit ici : c'est ta proposition qui est fausse.

### L'objectif réel

Arabesque est un **système de trading pour prop firms**. L'objectif n'est pas d'avoir le WR le plus élevé possible — c'est de **passer le challenge et générer des profits réguliers en restant dans les limites**.

```
CONTRAINTES NON NÉGOCIABLES (définies par la prop firm) :
  Daily DD max     : FTMO 5% | GFT/TradeLocker 4%  ← adapter par compte
  Total DD max     : FTMO 10% | GFT 10%
  Consistance      : courbe d'équité régulière, pas de journée aberrante
  Gains contenus   : éviter les journées à +3-4%+ (certaines PF le signalent)
  Target atteint   : +10% en 30 jours pour FTMO

CES CONTRAINTES S'ÉVALUENT SUR L'ENSEMBLE DU SYSTÈME,
PAS INSTRUMENT PAR INSTRUMENT.
```

### Ce qu'on cherche, et pourquoi

**WR élevé (≥ 70%)** n'est pas l'objectif — c'est un **moyen efficace** pour satisfaire les contraintes :
- WR élevé → pertes rares → daily DD difficile à breach en une journée
- Gains petits et fréquents → courbe régulière sans pics suspects
- Faible variance → statistiquement mesurable sur 100 trades

Un WR de 60% avec des gains très bien calibrés peut aussi fonctionner, **tant que la variance par journée reste dans les seuils**. Ce qui est hors scope : la volatilité de la courbe, pas le WR en soi.

Un instrument individuel avec WR 55% peut coexister dans le portefeuille si :
- Il est non corrélé aux autres (diversification effective)
- Sa contribution à la variance journalière reste faible
- Son expectancy est positive et statistiquement validée

### La référence : BB_RPB_TSL

La stratégie dont Arabesque est dérivé tourne en live depuis ~527 jours :
- Win Rate : **90.8%**
- CAGR : ~48%
- Profil : petits gains fréquents, pertes bien délimitées

C'est **la preuve empirique que ce profil est atteignable** sur les altcoins crypto. Arabesque adapte ce concept aux contraintes prop firm (SL réel, guards DD, anti-lookahead strict).

### Limites par compte — adapter le risk automatiquement

Les limites DD varient selon la prop firm. Les guards doivent utiliser les paramètres
du compte actif, **pas des valeurs hardcodées**. Configuration dans `accounts.yaml` :

| Prop Firm | Broker | Daily DD | Total DD | Reset TZ |
|---|---|---|---|---|
| FTMO | cTrader | 5% | 10% | Europe/Prague minuit |
| GFT | TradeLocker | **4%** | 10% | à vérifier |

Avec daily DD 4% (GFT), le risk/trade doit être réduit par rapport à FTMO 5%.
Simulation indicative : si max_daily_dd 4% → risk/trade ≈ 0.30-0.35% au lieu de 0.40%.

**TODO** : lire `max_daily_dd_pct` et `max_total_dd_pct` depuis `accounts.yaml` plutôt
que depuis `PropConfig` hardcodé dans `guards.py`. Implémenter en Sonnet, valider en replay.

### Ce qui est hors scope même si c'est "plus rentable"

- Stratégies avec variance journalière trop élevée (même si WR bon sur 6 mois)
- Trailing SL long qui transforme des gagnants en incertains → variance ↑
- Optimisation de l'avg_win au prix de la régularité de la courbe
- Un trade unique qui représente > 0.5% de risk → trop d'impact sur le daily DD

### Signal d'alarme à déclencher

Si tu lis dans le code, les docs, ou une proposition IA :
- "WR ~52% compensé par avg_win de 2.3R" → **DÉRIVE, CORRIGER** (variance trop haute)
- "l'edge vient des grands mouvements" → **DÉRIVE, CORRIGER**
- "sensibilité aux outliers acceptée" → **DÉRIVE, CORRIGER**
- "augmenter le risk/trade pour accélérer le target" → **DÉRIVE** (breach DD en cas de série perdante)

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

**Walk-forward validation — IMPLÉMENTÉ (2026-03-15)**
CLI : `python -m arabesque walkforward --strategy extension --universe crypto`
Code : `split_walk_forward()` dans `store.py`, `run_walk_forward()` dans `backtest.py`.
Fenêtres glissantes IS→OOS, agrégation pondérée, mesure de stabilité (σ WR, σ Exp).

**Résultats walk-forward Extension (2026-03-15) :**

| Univers | Trades OOS | WR | Exp(R) | Total R | Verdict |
|---|---|---|---|---|---|
| Forex majors H1 (7) | 171 | 55% | -0.08 | -17.0 | FAIL |
| Forex crosses H1 (15) | 226 | 57% | -0.07 | -20.1 | FAIL (sauf AUDJPY +7.6, CHFJPY +3.2) |
| XAUUSD H1 | 67 | 73% | +0.176 | +11.8 | MARGINAL |
| **Crypto 4H (14)** | **158** | **65%** | **+0.18** | **+29.1** | **PASS** |

**Instruments crypto 4H positifs :** SOLUSD +7.1, ETHUSD +5.2, LINKUSD +4.2,
DOGEUSD +3.5, AAVEUSD +3.5, AVAXUSD +2.6, ADAUSD +2.4, LTCUSD +2.3, BNBUSD +2.0,
UNIUSD +0.8. Négatifs : BTCUSD -1.7, XRPUSD -1.6, NEARUSD -1.2, DOTUSD ~0.

**Conclusion stratégique :** le split IS/OOS fixe surestimait l'edge forex H1.
En walk-forward, seuls XAUUSD H1 et la crypto 4H tiennent. Le basket live devrait
refléter cette réalité : **XAUUSD H1 + crypto 4H + sélection JPY crosses H1**.

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
Note FTMO : les restrictions news ne s'appliquent PAS pendant l'évaluation
(Challenge + Vérification), mais s'appliquent en FTMO Account funded standard.
Un SL/TP déclenché dans la fenêtre restreinte peut être considéré comme violation.

**Reset daily loss à minuit CE(S)T (priorité haute)**
La perte journalière FTMO se recalcule à minuit CE(S)T (pas à l'ouverture de
session), et inclut le P&L latent (positions ouvertes), commissions et swaps.
Conséquence : une position overnight profitable qui retrace après minuit peut
créer une violation sur le nouveau jour. Le guard actuel `max_daily_dd` dans
`guards.py` ne tient pas compte de ce reset horaire. À implémenter :
- Reset du compteur P&L journalier à 00:00 CE(S)T
- Inclusion du P&L latent dans le calcul (pas seulement réalisé)
- Alerte si positions overnight avec P&L latent > 50% de la marge journalière

**Plafond de perte intraday personnel (priorité haute)**
Fixer un plafond interne plus conservateur que la limite programme :
1.5-2.5% vs 5% FTMO. Marge de sécurité pour le slippage, les coûts et les
erreurs. À paramétrer dans `PropConfig` comme ratio de la limite officielle
(ex: `personal_daily_cap_ratio: 0.40` → 2% sur 5%).

**Limite de pertes consécutives par stratégie (priorité moyenne)**
Kill switch automatique après N pertes consécutives, paramétrable par stratégie :
- Scalping (Glissade) : 3 pertes → arrêt journée
- Mean reversion : 2 pertes → arrêt journée (probable trend day)
- Breakout (Fouetté) : 4 pertes (WR plus faible, séries attendues)
- Pairs (Pas de Deux) : 2 pertes → cooldown 1 session
Ces valeurs sont des defaults de départ à calibrer par backtest.

### À faire — Analyse avancée

**Monte Carlo sur barrières (priorité haute)**
Le Monte Carlo actuel dans `pipeline.py` estime la distribution des métriques.
Ce qui manque : estimer la **probabilité d'atteindre le profit target AVANT de
toucher le DD max**, qui est LA question pour un challenge prop firm. Se modélise
comme un problème de temps d'atteinte de barrières (gambler's ruin avec drift).
Entrée : distribution des trades (R), barrières (profit target, max DD).
Sortie : P(succès), temps médian, P(ruine).
Extension : simuler les règles FTMO complètes (equity-based, reset minuit CE(S)T)
dans le Monte Carlo, pas seulement les barrières simplifiées.

**Reporting backtest segmenté par régime (priorité moyenne)**
Actuellement, les métriques backtest sont agrégées sur toute la période.
Ajouter un reporting par régime de marché (range vs trend, via ADX ou volatilité)
pour vérifier que chaque stratégie profite du bon régime :
- Mean reversion → doit profiter majoritairement en range (ADX < 20)
- Breakout/trend → doit profiter majoritairement en trend (ADX > 25)
Si une stratégie profite du « mauvais » régime, le filtre est mal calibré.
Implémentation : tag chaque trade avec le régime dominant au moment de l'entrée,
puis reporting séparé dans `_print_backtest_synthesis`.

**Sizing par type de stratégie — référence (priorité basse)**
Plages de risque par trade calibrées pour un 100k 2-steps (FTMO recommande
0.25-1% par trade, 10 pertes consécutives à 0.5% = 5% = limite journalière) :
- Scalping (Glissade) : 0.10-0.20% (fréquence haute → sizing bas)
- Mean reversion : 0.25-0.50%
- Breakout/ORB (Fouetté) : 0.20-0.35%
- Pairs (Pas de Deux) : 0.20-0.35%
- Trend-following (Extension) : 0.40% (validé sur 20 mois)
À intégrer comme defaults dans les configs de chaque stratégie.

**Couverture instruments : indices, énergie, agri (priorité basse)**
`universes.yaml` ne contient que forex, métaux et crypto car les sources de données
M1 actuelles (Dukascopy pour forex/metals, CCXT/Binance pour crypto) ne couvrent
pas les autres classes. Indices (US500, NAS100, GER40), énergie (USOIL, NATGAS) et
matières premières agricoles (WHEAT, COCOA) existent sur FTMO/cTrader mais on
ne peut pas les backtester faute de données. Options :
1. Exporter l'historique depuis cTrader (API ou export CSV)
2. Source M1 payante (Polygon.io, FirstRate Data)
3. Accepter du H1 Yahoo Finance pour dégrossir (qualité inférieure)
Pas prioritaire car la stratégie Extension fonctionne principalement sur XAUUSD et
crypto 4H (walk-forward validé).

## Nouvelles stratégies — pipeline d'implémentation (2026-03-15)

> Stratégies identifiées par analyse croisée de la littérature prop firm,
> filtrées par la boussole stratégique (WR ≥ 70% cible, courbe régulière).
> Convention de nommage : disciplines artistiques (danse classique, GR, GAF).

### Catalogue des stratégies

| Nom de code | Logique | TF | WR brut estimé | Statut | Priorité |
|---|---|---|---|---|---|
| **Extension** | Trend-following BB squeeze → breakout | H1/H4 | ~45% → 75% (BE) | ✅ Live multi-strat | — |
| **Glissade** | RSI divergence + EMA200 | H1 | 80-85% (BE) | ✅ Live (shadow) | Haute |
| **Fouetté** | Opening Range Breakout | M1 | 65-92% | ⚠️ Freq trop basse | Moyenne |
| **Cabriole** | Donchian breakout + EMA200 | H4 | ~75% (BE) | ✅ WF 6/6, overlap Ext | Basse |
| **Pas de Deux** | Pairs trading cointégration + z-score | M15 | 50-65% | 📋 Placeholder | Long terme |

### Stratégies évaluées et rejetées

| Stratégie | WR estimé | Raison du rejet |
|---|---|---|
| Breakout Donchian (momentum) | 30-45% | WR incompatible boussole (≥70% cible). Séries de pertes trop longues. |
| Mean reversion pure (sans VWAP ni régime) | ~40% | Testée 4 replays, 2 périodes, 3 univers : perd partout. Abandonnée définitivement. |

### Glissade — Scalping intraday pullback VWAP + EMA

**Concept :** le prix recule brièvement vers le VWAP/EMA20 dans une tendance
intraday établie, puis repart. Entrée sur bougie de retournement M1.

**Pourquoi ça peut marcher avec la boussole :**
- Le pullback VWAP est un setup institutionnel bien documenté (ancrage prix moyen)
- Stops courts (0.9-1.5× ATR M1) → petites pertes
- Fréquence élevée (jusqu'à 6 trades/jour) → consistance
- Le BE trigger 0.3R convertit les trades neutres en petits gains (comme Extension)
- Hypothèse : WR brut 55-70% → avec BE trigger, 70-80% est plausible

**Risques identifiés :**
- Sensibilité au slippage et aux coûts (scalping M1)
- VWAP pas encore dans indicators.py → à implémenter
- Filtre de régime ADX critique (sans lui, chop = pertes)

**Prérequis :**
1. Implémenter VWAP session dans `arabesque/modules/indicators.py`
2. Valider le chargement M1+M5 synchronisé dans `store.py`
3. Premier backtest sur pool `quick` (6 instruments)
4. Walk-forward si résultats positifs

**Fichiers :** `arabesque/strategies/glissade/signal.py` (placeholder)

### Pas de Deux — Pairs trading cointégration + z-score

**Concept :** deux instruments cointégrés s'écartent de leur relation d'équilibre
(z-score ≥ 2.0). On prend un spread hedgé (long un / short l'autre) et on attend
le retour à la moyenne.

**Pourquoi c'est intéressant :**
- Courbe d'équité structurellement plus régulière (exposition hedgée)
- Décorrélé des autres stratégies → diversification du portefeuille
- Pas de dépendance à un régime tendance/range
- P&L proportionnel à la stabilité statistique, pas à la direction du marché

**Risques identifiés :**
- Rupture de cointégration = stop brutal (risque "à saut")
- Leg risk (fill d'une jambe échoue → exposition directionnelle non voulue)
- Complexité élevée (stats + infra double-instrument)
- BacktestRunner est mono-instrument → refonte nécessaire

**Prérequis (lourds) :**
1. Extension du modèle `Signal` pour les paires (ou nouveau type `PairSignal`)
2. `BacktestRunner` multi-instrument (ou nouveau runner dédié)
3. Calcul Engle-Granger + ADF dans indicators.py (dépendance statsmodels)
4. Exécution synchronisée de 2 ordres dans le broker
5. Identifier des paires tradables sur cTrader/FTMO (indices CFD entre eux?)

**Fichiers :** `arabesque/strategies/pas_de_deux/signal.py` (placeholder)

### Éléments transverses à noter (issus de l'analyse LLM)

**Risk engine indépendant du signal (déjà partiellement dans guards.py) :**
L'analyse confirme que le moteur de risque est plus important que le signal.
Un signal moyen + risk engine strict passe plus souvent qu'un bon signal sur-levier.
Arabesque a déjà `guards.py` + `PropConfig` + `ExecConfig`, mais certains manques :
- Plafond quotidien personnel (pas juste le daily DD FTMO, mais un seuil interne
  plus conservateur, ex: 40% du DLL programme)
- Kill switch : arrêt automatique après N pertes consécutives (paramétrable par stratégie)
- Max trades/jour par stratégie (pas juste par session comme Fouetté)

**Tests de stress standardisés (à intégrer dans le pipeline) :**
- Slippage ×2 sur 10% des trades (news/ouverture)
- Spread ×1.5 permanent
- Test look-ahead : décaler indicateurs de 1 barre, vérifier que l'edge survit
- Clusters de pertes (pas seulement permutation uniforme dans Monte Carlo)

**Compliance SIM :**
Plusieurs prop firms sanctionnent les algos qui exploitent l'absence de slippage
du simulateur (brackets ultra-serrés, hyperactivité). À garder en tête pour
Glissade (scalping) : le backtest doit être pessimiste sur les fills.

### Multi-strategy live engine (2026-03-17)

**Décision :** Modifier `_start_bar_aggregator()` dans `live.py` pour créer des BarAggregators par `(timeframe, strategy)` au lieu de timeframe seul.

**Pourquoi :**
- Extension + Glissade doivent tourner en parallèle sur les mêmes instruments (XAUUSD/BTCUSD)
- Extension H4 crypto + Extension H1 forex + Glissade H1 = 3 aggregators indépendants
- Chaque aggregator a son propre signal generator
- Les signaux sont tagués par `strategy_type` pour le dispatcher

**Config :** `strategy_assignments` dans settings.yaml. Chaque entrée crée un aggregator dédié.

**Résultat live :** 3 aggregators opérationnels — H1/glissade (2 instr), H1/trend (51 instr), H4/trend (31 instr).

### Extension comprehensive universe backtest (2026-03-17)

24 instruments with sub-bar M1. 887 trades, +31.8R. 16/24 positifs.
Top: BNBUSD +11.1R PF 2.58, ETHUSD +8.1R PF 2.35, BTCUSD +7.6R PF 1.95.
Négatifs: DOTUSD, XLMUSD, NEOUSD, GBPUSD, EURUSD, USDJPY.

### Glissade RSI div detailed backtest (2026-03-17)

Sub-bar M1 confirms WF results:
- XAUUSD H1 RR2 +BE: 60 trades, WR 80%, Exp +0.121R, +7.3R
- BTCUSD H1 RR2 +BE: 91 trades, WR 84.6%, Exp +0.177R, +16.1R
RR3 slightly better than RR2 on XAUUSD (+7.9R vs +7.3R), similar on BTCUSD.

### Fouetté signal frequency issue (2026-03-17)

Multi-instrument scan reveals:
- XAUUSD M1 fvg_multiple NY: only 14 trades in 2+ years — too few for WF
- BTCUSD M1 fvg_multiple NY: 185 trades, WR 72.4%, marginal Exp +0.019R
- ETHUSD M1 fvg_multiple NY: 314 trades, near-zero Exp
- Forex (GBPJPY, EURUSD): 0 trades — range never broken

**Conclusion:** Fouetté is only viable on crypto (BTCUSD has enough signals) and possibly indices (US100 24 trades, promising but small sample). XAUUSD FVG mode rarely triggers. Breakout mode more promising (12 trades WR 91.7%) but still too few.

### CCXT mappings expanded (2026-03-17)

Added 17 missing crypto instrument mappings in `_CCXT_MAP` (store.py): BCHUSD, XLMUSD, NEOUSD, ICPUSD, XMRUSD, ETCUSD, DASHUSD, ALGOUSD, GRTUSD, IMXUSD, SANDUSD, FETUSD, VETUSD, MANAUSD, BARUSD.

---

### Extension multi-TF — Résultats 4H vs H1 (2026-03-15)

**Contexte :** Extension est validé en H1 (+0.130R, 75.5% WR, 1998 trades sur 20 mois).
Test sur 4H pour évaluer si un TF plus long est viable (positions plus longues,
potentiel pour comptes non-swing, et diversification de TF).

**Résultats Extension 4H — univers complet (37 instruments, ~2 ans) :**

| Catégorie | Trades | WR | Exp(R) | Verdict |
|---|---|---|---|---|
| **crypto** | 504 | 66% | **+0.092** | Meilleur qu'en H1 sur la plupart |
| forex_cross | 222 | 53% | -0.225 | H1 nettement meilleur |
| forex_major | 147 | 57% | -0.100 | H1 nettement meilleur |
| metal | 61 | 59% | -0.105 | H1 nettement meilleur (XAUUSD +0.122 en H1) |
| **TOTAL** | **934** | **61%** | **-0.027** | Globalement négatif |

**Détail crypto 4H (top performers sur pool quick) :**
- ETHUSD : +0.402R, 71% WR, PF 2.63 (34 trades)
- SOLUSD : +0.217R, 79% WR, PF 2.17 (38 trades)
- BTCUSD : +0.081R, 65% WR, PF 1.23 (43 trades)

**Conclusion :** Extension 4H n'est pas un remplacement universel de H1.
Mais crypto en 4H montre un edge supérieur à H1. Piste : configuration
hybride H1 forex/métaux + 4H crypto.

**Attention :** échantillon 4H plus petit (~2 ans de barres 4H vs ~2 ans H1),
certains instruments < 30 trades. Nécessite walk-forward pour confirmer.

**Infrastructure ajoutée :**
- `--interval` dans le CLI (`python -m arabesque run --interval 4h`)
- Dérivation 15m, 30m, 4h depuis M1 existant
- `tf_map` étendu dans store.py (15m, 30m, 4h)
- try/except dans la boucle backtest (plus de crash si données manquantes)

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
- ~~`sl_source="fvg"` : SL au bord de la FVG (plus serré) → meilleur R/R intrinsèque~~
  **TESTÉ 2026-03-16** sur 4 instruments (XAUUSD, BTCUSD, SOLUSD, ETHUSD) :
  WR chute de 12-21pts, max DD explose à 13-17% (breach FTMO 10%).
  Le SL FVG est trop serré pour le bruit M1 — le WR destruction annule le gain R/R.
  **ABANDONNÉ.**
- Mode `breakout` pur (sans FVG) : plus de trades, moins de filtrage
- ~~Combinaison TP fixe + `sl_source="fvg"`~~ : abandonné avec sl_source="fvg"

**Statut : recherche. Ne pas déployer en live. Soumettre à Opus pour la suite.**

---

## Décision 2026-03-16 — Sub-bar replay M1 dans le backtest

**Contexte :** le backtest H1 utilisait le H/L agrégé d'une barre pour résoudre
SL/TP/BE. Quand SL et TP étaient tous deux touchés dans la même barre → SL pris
(conservateur). Mais surtout, le BE trigger ne pouvait pas se déclencher
intra-barre : si le prix montait à 0.35R puis redescendait au SL dans la même
barre H1, le backtest prenait -1R alors que le live (tick-level TSL) aurait
déclenché le BE et sorti à +0.20R.

**Solution :** sub-bar replay — pour chaque barre H1/H4 avec positions ouvertes,
on itère les barres M1 sous-jacentes en ordre chronologique. Le position manager
reçoit chaque M1 individuellement, ce qui résout l'ambiguïté temporelle.

**Impact mesuré sur XAUUSD Extension H1 (2 ans) :**

| Métrique | Sans sub-bar | Avec sub-bar M1 |
|---|---|---|
| WR | 69.4% | 74.1% (+4.7pts) |
| Exp | +0.122R | +0.056R |
| Total R | +13.2R | +6.0R |
| SL exits | 32 | 26 (-6) |
| Max DD | 1.7% | 1.3% |

Le mode conservateur surestimait à la fois les gains (TP touché mais BE aurait
sorti avant) et les pertes (SL touché mais BE aurait protégé). Le sub-bar replay
donne une image plus proche du live.

**Détail technique :** le compteur `bars_open` (utilisé par ROI, time_stop,
deadfish) est incrémenté une fois par barre parente, pas par M1 sub-bar.
Sauvegarde/restauration autour du sub-bar replay.

**Activation :** automatique quand des données M1 sont disponibles dans
`barres_au_sol/`. Flag `--no-sub-bar` pour désactiver (backtests rapides).

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

---

## Décision 2026-03-16 — ROI désactivé pour crypto H4

**Contexte :** l'ablation framework (42 instruments, 1623 trades, sub-bar M1)
a montré que le ROI backstop détruit l'edge sur crypto H4 :
- Avec ROI : Exp +0.044R
- Sans ROI : Exp +0.181R (×4.1)

**Pourquoi le ROI nuit :** les tiers ROI (bars=0 → 3.0R, bars=240 → 0.15R)
sont calibrés pour H1 (240 barres H1 = 10 jours). Sur H4, 240 barres = 40 jours,
ce qui est plus réaliste comme backstop, mais le tier bars=0 → 3.0R coupe les
mouvements crypto forts dès qu'ils atteignent +3R. Le BE + trailing capturent
ces mouvements naturellement.

**Implémentation :**
- `manager_config_for(instrument, interval)` dans `backtest.py` retourne
  `ManagerConfig(roi_enabled=False)` quand `_categorize(instrument) == "crypto"`
  et `interval in ("4h", "H4")`.
- Utilisé automatiquement dans backtest CLI, walk-forward, et ablation.
- Le live n'est pas affecté (le `LivePositionMonitor` ne gère que BE + trailing,
  pas le ROI — qui est un mécanisme backtest/PositionManager uniquement).

**Pas touché :** `position_manager.py` inchangé (le flag `roi_enabled` existait déjà).

## Décision 2026-03-17 — Audit biais backtest H/L vs Sub-bar M1

**Contexte :** Besoin de quantifier l'écart entre le backtest H/L agrégé (rapide mais
imprécis sur l'ordre intra-barre) et le sub-bar replay M1 (lent mais résout l'ambiguïté).

**Résultats sur 6 instruments (14 mois) :**

| Instrument | TF | WR H/L | WR Sub-bar | Exp H/L | Exp Sub-bar | Biais Exp |
|---|---|---|---|---|---|---|
| XAUUSD | 1h | 70% | 77% | +0.096 | +0.051 | +0.045 |
| BTCUSD | 4h | 67% | 81% | +0.092 | +0.149 | -0.057 |
| ETHUSD | 4h | 55% | 76% | +0.231 | +0.162 | +0.069 |
| SOLUSD | 4h | 83% | 87% | +0.283 | +0.043 | +0.240 ⚠️ |
| GBPJPY | 1h | 55% | 60% | -0.008 | +0.046 | -0.054 |
| AUDJPY | 1h | 70% | 77% | +0.099 | +0.154 | -0.055 |

**Conclusions :**
1. Le biais est **bidirectionnel**, pas systématiquement optimiste.
2. Le WR H/L est systématiquement **plus bas** que sub-bar (règle conservatrice SL en cas
   d'ambiguïté abaisse le WR). Mais l'Exp H/L peut être plus haute car avg_win est plus
   élevé (les positions survivent plus longtemps quand le BE intra-barre n'est pas résolu).
3. L'écart moyen est ±0.05R, sauf SOLUSD 4h (+0.24R) qui est un outlier.
4. **Règle :** le backtest H/L est fiable pour le screening rapide (direction correcte dans
   5/6 cas). Le sub-bar replay M1 reste obligatoire pour la validation finale (walk-forward).

## Décision 2026-03-17 — Fouetté : London session XAUUSD, US100 NY, BTCUSD NY

**Contexte :** Tests exhaustifs Fouetté sur 14 mois, 3 instruments, 3 sessions, 4 configs.

**Résultats clés (14 mois, M1) :**

| Instrument | Session | Config | Trades | WR | Exp | TotR | DD |
|---|---|---|---|---|---|---|---|
| **XAUUSD** | **London** | **RR1.5 no_BE** | 63 | 62% | +0.409 | +25.7R | 1.6% |
| XAUUSD | London | RR1.5 +BE | 63 | 76% | +0.086 | +5.4R | 1.0% |
| XAUUSD | London | RR2 no_BE | 61 | 52% | +0.423 | +25.8R | 1.6% |
| XAUUSD | NY | RR2 no_BE | 189 | 37% | +0.011 | +2.1R | 7.1% |
| **US100** | **NY** | **RR2 no_BE** | 181 | 43% | +0.171 | +31.0R | 7.0% |
| US100 | NY | RR1.5 +BE | 252 | 75% | +0.000 | 0.0R | 3.5% |
| BTCUSD | NY | RR1.5 +BE | 326 | 74% | +0.023 | +7.6R | 2.4% |
| BTCUSD | NY | RR2 no_BE | 281 | 38% | +0.030 | +8.5R | 6.8% |

**Découvertes :**
1. XAUUSD London >> NY pour ORB. Hypothèse : le London open à 8h UTC crée un vrai
   breakout directionnel (flux institutionnel européen), tandis que le NY open à 14h30
   arrive dans un marché déjà formé.
2. Le mode no_BE est systématiquement meilleur en expectancy pour ORB. Le BE convertit
   des trades gagnants (+1R, +1.5R) en +0.20R, ce qui détruit l'edge.
3. US100 est le meilleur instrument pour Fouetté NY (momentum tech fort).
4. BTCUSD est le seul où +BE reste profitable (crypto a assez de momentum pour
   que les petits gains s'accumulent).

**Walk-forward Fouetté (2026-03-17) — 4/4 PASS :**

| Combo | OOS Trades | WR | Exp | PF | Total R | MaxDD |
|---|---|---|---|---|---|---|
| XAUUSD London RR1.5 no_BE | 63 | 62% | +0.409R | 2.07 | +25.7R | 1.6% |
| XAUUSD London RR1.5 +BE | 63 | 76% | +0.086R | 1.38 | +5.4R | 1.0% |
| US100 NY RR2 no_BE | 147 | 44% | +0.190R | 1.35 | +28.0R | 7.6% |
| BTCUSD NY RR1.5 +BE | 280 | 76% | +0.043R | 1.19 | +12.0R | 2.3% |

**FVG vs breakout London :** breakout >> FVG. FVG divise l'edge par 2 (XAUUSD +25.7→+12.5R).

**Prochaine étape :** Dry-run parquet 3 mois sur les 3 combos gagnantes, puis shadow filter live.

## Décision 2026-03-17 — Extension indices/energy : GER40 4H prometteur, reste marginal

**Contexte :** Premiers backtests Extension sur données indices/energy Dukascopy.

**Résultats backtest flat (20 mois) :**

| Instrument | H1 (Exp, TotR) | H4 (Exp, TotR) |
|---|---|---|
| GER40 | +0.005, +0.4R | **+0.406, +10.2R** ⭐ |
| UK100 | +0.064, +3.5R | -0.172, -2.4R |
| US100 | +0.057, +4.4R | -0.319, -3.2R |
| US500 | -0.105, -7.0R | -0.240, -2.2R |
| JP225 | +0.000, +0.0R | -0.049, -0.9R |
| UKOIL | -0.209, -11.5R | +0.118, +1.5R |

**Walk-forward GER40 4H :** PASS technique mais seulement 3 trades OOS en 6 fenêtres.
Pas assez pour être statistiquement significatif. L'instrument génère ~1 trade/2 mois
en OOS. À accumuler avant de conclure.

**Walk-forward UK100 H1 et US100 H1 (2026-03-17) :**

| Instrument | OOS Trades | WR | Exp | Total R | Verdict |
|---|---|---|---|---|---|
| UK100 H1 | 25 | 76% | +0.061R | +1.5R | MARGINAL (σ WR=20.5%) |
| US100 H1 | 29 | 72% | -0.027R | -0.8R | FAIL |

**Décision :** GER40 4H, UK100 H1, US100 H1 — tous en watchlist mais PAS au basket live.
GER40 : trop peu de trades. UK100 : instable. US100 : Exp négative en OOS.
Les indices Dukascopy ne contribuent pas positivement au basket Extension pour l'instant.

## Décision 2026-03-17 — RSI divergence : lookahead corrigé + Glissade v2

**Bug critique découvert** : `compute_rsi_divergence()` détectait les pivots locaux avec
une fenêtre centrée `[i-w, i+w]`, regardant `w` barres dans le futur. Avec `pivot_window=5`,
cela donne 5 barres de lookahead → les backtests étaient faussement optimistes (WR 89-100%).

**Fix** : Le pivot à la barre `p` n'est confirmé qu'à la barre `p+w`. La divergence est
maintenant reportée à la barre `p+w` (première barre où elle est observable sans lookahead).

**Impact sur Extension** : Le shadow filter RSI div dans Extension utilisait aussi cet
indicateur, mais comme shadow filter log-only (pas bloquant), l'impact est nul sur les
résultats de trading. La version corrigée donne des logs plus réalistes.

**Résultats Glissade v2 (RSI divergence comme signal principal, post-fix) :**

| Instrument | TF | Config | Trades | WR | Exp | TotR |
|---|---|---|---|---|---|---|
| XAUUSD | 1h | pw3 RR2 +BE | 53 | 72% | +0.137 | +7.3R |
| XAUUSD | 1h | RR3 no_BE | 25 | 36% | +0.348 | +8.7R |
| BTCUSD | 1h | pw3 RR2 +BE | 75 | 76% | +0.218 | +16.3R |
| BTCUSD | 1h | RR2 no_BE | 28 | 43% | +0.270 | +7.6R |

H1 fonctionne, H4 non. GBPJPY, ETHUSD, SOLUSD négatifs.

**Glissade VWAP pullback M1** : reste négatif avec ou sans RSI div filter (0 trades quand
activé — la conjonction VWAP pullback + RSI div pivot est trop rare en M1). Le setup VWAP
pullback M1 n'a pas d'edge mesurable. Pivoter vers RSI div H1 comme signal principal.

### Walk-forward Glissade v2 — 3/3 PASS ✅ (2026-03-17)

| Combo | OOS Trades | WR | Exp(R) | PF | Total R | MaxDD |
|---|---|---|---|---|---|---|
| XAUUSD H1 pw3 RR2 +BE | 31 | **87%** | +0.185 | 2.43 | +5.7R | 0.4% |
| BTCUSD H1 pw3 RR2 +BE | 54 | **85%** | +0.196 | 2.32 | +10.6R | 1.3% |
| XAUUSD H1 RR3 no_BE | 17 | 35% | +0.285 | 1.44 | +4.8R | 1.6% |

Les 3 combos passent (Exp > 0, ≥ 15 trades), mais seuls les variants +BE (WR 85-87%)
correspondent au profil prop firm (boussole ≥ 70%). Le RR3 no_BE (WR 35%) est rentable
mais inadapté au profil FTMO.

**Décision** : retenir XAUUSD H1 pw3 RR2 +BE et BTCUSD H1 pw3 RR2 +BE pour dry-run.
**Prochaine étape** : dry-run parquet 3 mois, puis shadow filter live.

## Décision 2026-03-17 — Cabriole (Donchian breakout) : stratégie validée

**Origine** : Adaptation de la stratégie Donchian du projet Envolées (zip `tmp/donchian.zip`).
Setup : EMA200 trend filter + Donchian(20) channel breakout + 0.10×ATR buffer + volatilité filter (ATR rel < P90).

**Scan initial (H/L mode, 18 instruments × 2 TF × 8 configs)** :
- Crypto 4H : dominant (DOGEUSD +30.7R, XAUUSD +27.0R, LINKUSD +23.6R, AVAXUSD +19.3R)
- Crypto H1 : certains positifs (AVAXUSD +38.8R, LINKUSD +30.4R) mais WR < 66%
- Forex : uniformément négatif (même pattern que Extension)

### Walk-forward Cabriole — 6/6 PASS ✅

| Combo | OOS Trades | WR | Exp(R) | PF | Total R | MaxDD |
|---|---|---|---|---|---|---|
| LINKUSD 4H SL1 +BE | 44 | 73% | +0.354 | 2.30 | +15.6R | 1.2% |
| ADAUSD 4H DC30 +BE | 37 | 70% | +0.326 | 2.20 | +12.1R | 0.8% |
| XAUUSD 4H SL2 +BE | 25 | 76% | +0.304 | 2.27 | +7.6R | 0.6% |
| DOGEUSD 4H +BE | 45 | 73% | +0.249 | 1.93 | +11.2R | 1.4% |
| ETHUSD 4H SL2 +BE | 39 | 69% | +0.214 | 1.70 | +8.4R | 1.5% |
| AVAXUSD 4H RR1.5 +BE | 51 | 73% | +0.168 | 1.61 | +8.6R | 1.5% |

**Même univers que Extension** (crypto 4H + XAUUSD). La corrélation des signaux entre
Extension (BB squeeze) et Cabriole (Donchian) reste à mesurer — possiblement la même
tendance capturée par deux filtres différents.

**Décision** : Cabriole est une stratégie validée. Implémenter `strategies/cabriole/signal.py`,
puis mesurer le recouvrement de signaux avec Extension avant de déployer les deux en parallèle.
**Signal.py implémenté** : `arabesque/strategies/cabriole/signal.py` + CLI `--strategy cabriole`.
`compute_donchian()` ajouté dans `indicators.py`.

### Recouvrement Extension vs Cabriole (crypto 4H, ±2 barres)

| Instrument | Ext sigs | Cab sigs | Overlap | Overlap% Ext→Cab |
|---|---|---|---|---|
| XAUUSD | 60 | 231 | 57 | 95% |
| DOGEUSD | 65 | 209 | 59 | 91% |
| ETHUSD | 64 | 187 | 57 | 89% |
| BTCUSD | 77 | 198 | 63 | 82% |
| LINKUSD | 62 | 226 | 45 | 73% |

73-95% des signaux Extension sont aussi des signaux Cabriole (±2 barres). Cabriole
génère 3-4× plus de signaux (Donchian est moins sélectif que BB squeeze).

**Conclusion** : même edge sous-jacent, pas de diversification réelle.
Cabriole est un **backup/enrichissement**, pas une stratégie indépendante.
Usage possible : confirmation quand les deux déclenchent, ou remplacement
sur instruments spécifiques où Cabriole surperforme Extension.

---

### Live monitoring & protection (2026-03-17)

**Problème** : le moteur live génère des logs riches (audit JSONL, shadow filters,
position monitor) mais aucune agrégation ni alerte automatique. Impossible de
détecter rapidement un drift vs backtest, un margin call imminent, ou une série
de pertes anormale sans parsing manuel des logs.

**Solution** : `LiveMonitor` (`arabesque/execution/live_monitor.py`) — module
centralisé qui :

1. **Trade journal** : persiste chaque entrée/sortie en JSONL (`logs/trade_journal.jsonl`)
   avec résultat en R, PnL cash, MFE, BE/trailing state, exit reason
2. **Equity snapshots** : enregistre balance/equity/marge toutes les 5min
   (`logs/equity_snapshots.jsonl`) pour reconstruire la courbe d'equity live
3. **Performance live** : agrège WR, Exp, TotalR, Max DD par stratégie et instrument
4. **Drift detection** : compare WR et Exp live aux baselines backtest.
   Seuils : WR drift > 15pp, Exp < -0.05R. Minimum 20 trades avant évaluation.
5. **Margin monitoring** : alerte si free_margin < 50% equity (warn) ou < 20% (critical)
6. **Consecutive losses** : alerte après 5 pertes consécutives par stratégie
7. **Health reports** : résumé horaire dans les logs

**Intégration** :
- `position_monitor.py` enrichi avec callback `on_position_closed` dans `reconcile()`.
  Quand une position disparaît du broker, estime l'exit reason (TP/SL/BE/trailing)
  et le prix de sortie, puis notifie le LiveMonitor.
- `live.py` : LiveMonitor instancié avant le position monitor, branché sur
  `_on_order_result()` (entrées), `_refresh_account_state()` (equity snapshots),
  et `_account_refresh_loop()` (health reports périodiques).

**Baselines** :
- Extension (trend) : WR 75%, Exp +0.10R (20 mois, 1998 trades)
- Glissade (RSI div) : WR 55%, Exp +0.15R (WF XAUUSD+BTCUSD)

**Protection active** (ajoutée dans la même session) :

Le monitoring passif ne suffit pas — si le live déconne pendant la nuit, il faut
des actions automatiques. 4 paliers de protection progressive :

| Palier | Trigger | Action |
|---|---|---|
| NORMAL | — | Risque plein |
| CAUTION | DD daily ≤ -2.5% OU total ≤ -5% OU 5 pertes consec. | Risque × 0.50 |
| DANGER | DD daily ≤ -3.0% OU total ≤ -6.5% OU 8 pertes consec. | Risque × 0.25, ferme positions sans BE |
| EMERGENCY | DD daily ≤ -3.5% OU total ≤ -8.0% OU marge < 10% | Ferme TOUT, freeze trading |

**Rationale des seuils** : les guards existants bloquent à -4% daily et pause à -7% total.
Les paliers de protection se déclenchent AVANT les guards pour agir graduellement au lieu
d'attendre le seuil fatal. Le EMERGENCY à -3.5% daily laisse encore 0.5% avant le guard
daily (-4%) et à -8% total laisse 1% avant le guard total (-9%).

**Risk multiplier** : injecté dans le dispatcher via `risk_multiplier_fn()`. Le dispatcher
multiplie le `risk_cash` calculé par les guards. En CAUTION, un trade de 400$ devient 200$.
En DANGER, il devient 100$. En EMERGENCY, aucun trade n'est accepté.

**Close unprotected** : en DANGER, les positions sans breakeven set sont fermées car elles
sont exposées au SL initial complet. Les positions avec BE ou trailing sont conservées car
le risque est limité (sortie au pire à +0.20R ou trailing SL).

**Emergency kill switch** : ferme toutes les positions sur tous les brokers, freeze le
trading. Nécessite `manual_unfreeze()` ou un redémarrage du moteur. Le freeze repart en
CAUTION (pas NORMAL) pour observer avant de reprendre le risque plein.

**Notifications** : Telegram pour les alertes détaillées (CAUTION, drift, health reports),
ntfy pour les alertes urgentes (DANGER, EMERGENCY). Rate limited à 30s min entre messages.
Configuration via apprise URLs dans `config/secrets.yaml`.

**Bug corrigé** : le champ `margin_free` du broker cTrader retourne 0 (pas implémenté dans
le parsing ProtoOATrader). La vérification margin est ignorée quand free_margin=0 — on se
fie uniquement aux checks DD qui sont fiables.

**Ce que ça NE fait PAS** (extensions futures) :
- Dashboard web/Grafana (logs sont en JSONL, prêts pour l'export)
- Corrélation multi-positions (agrégation risque sectoriel)
- Latence signal→fill (timestamps disponibles mais pas encore mesurés)

## Décision 2026-03-20 — Renversé (ICT/SMC reversal) : edge insuffisant

**Concept** : Liquidity sweep + structure shift (CHOCH) + FVG retrace + biais HTF EMA200 H4.
Reversal post-sweep inspiré ICT/SMC, implémenté de façon entièrement mécanique.

**Ablation** (20 mois, XAUUSD + BTCUSD, 14 configs testées) :
- Compression BB trop restrictif : tue la fréquence (2→50 trades en la désactivant)
- CHOCH trop restrictif : réduit trades sans améliorer WR
- FVG retrace et HTF bias sont les seuls filtres contributifs

**Résultats tick-level (meilleur config : sweep + FVG retrace + HTF bias)** :

| Instrument | Trades | WR | Exp | Total R | PF |
|---|---|---|---|---|---|
| XAUUSD H1 | 50 | 64% | -0.128R | -6.4R | 0.58 |
| BTCUSD H1 | 92 | 78% | +0.079R | +7.2R | 1.40 |
| Combined | 142 | 73% | +0.006R | +0.8R | — |

**Diagnostic** : BE convertit assez de losers pour 73% WR, mais avg win (+0.28R via
trailing) vs avg loss (-0.91R) → edge structurellement trop mince. La majorité des
trades sortent au BE (+0.20R). Les reversals ne sont pas assez amples pour RR2.

**Décision** : Stratégie non déployable en l'état. Code conservé pour référence.
Walk-forward non justifié (edge <0 sur XAUUSD, marginal sur BTCUSD).

**Leçon** : Les reversals ICT/SMC sont difficilement compatibles avec la boussole
prop firm (gains petits, fréquents, consistants). Le mouvement post-sweep est
typiquement trop court pour générer un R significatif — la plupart finissent au BE.

## Décision 2026-03-21 — Révérence (NR7 contraction → expansion) : H4 viable, H1 non

**Concept** : Narrow Range 7 + body ratio confirmation + EMA200 filter.
Breakout après contraction de range (NR7 = range le plus petit des 7 dernières bougies).

**Ablation** :
- Engulfing non contributif (réduit fréquence sans gain WR)
- H1 breakeven (671 trades, WR 73%, Exp +0.013R)
- **H4 edge mince mais positif** (465 trades, WR 80%, Exp +0.034R)

**Walk-forward H4** (IS=2400, OOS=800, 3 fenêtres) :

| Instrument | OOS Trades | WR | Exp | Verdict |
|---|---|---|---|---|
| DOGEUSD | 30 | 83% | +0.059R | **PASS** |
| SOLUSD | 33 | 82% | +0.065R | MARGINAL |
| ETHUSD | 27 | 78% | +0.130R | MARGINAL |
| AVAXUSD | 32 | 72% | -0.002R | FAIL |
| ADAUSD | 37 | 70% | -0.109R | FAIL |

**Décision** : Code conservé. DOGEUSD H4 passe WF mais edge mince (+0.059R) et
~1.5 trades/mois. Overlap avec Extension H4 crypto à vérifier avant déploiement.
Si overlap < 50%, envisager shadow mode.

**Comparaison avec Cabriole** : Même pattern — stratégie de breakout sur H4 crypto
qui fonctionne sur certains instruments mais overlap potentiel avec Extension.

## Décision 2026-03-21 — Per-account risk overrides dans accounts.yaml

**Problème** : Le risk_per_trade_pct était hardcodé dans PropConfig (guards.py) et
lu depuis settings.yaml (general.risk_percent). Pas de distinction challenge vs funded.

Monte Carlo (session précédente) a montré 0.80% comme optimal pour les challenges
(P(breach total DD 10%) ≈ 1-2%, P(+10% target) ≈ 38-51% sur 80-100 trades).

**Solution** : accounts.yaml supporte maintenant des overrides per-account :
```yaml
ftmo_challenge:
  risk_per_trade_pct: 0.80   # challenge mode
ftmo_funded:
  risk_per_trade_pct: 0.45   # funded mode
```

Le live engine (`_make_dispatcher` dans `execution/live.py`) lit ces overrides
et les applique en priorité sur les valeurs de settings.yaml.

Corrigé aussi : settings.yaml risk_percent 0.40 → 0.45 (cohérent avec guards.py v3.4).

## Décision 2026-03-21 — Pas de Deux (pairs trading) non viable pour prop firms

**Raisons** :
1. Mean-reversion fondamentale — la boussole dit "trend-only, MR perd systématiquement"
2. Double margin (2 positions simultanées) sur budget DD limité 5%/10%
3. Convergence peut prendre des semaines, incompatible avec rythme challenge

**Décision** : Reste en placeholder "long terme" sans investissement de temps.

## Décision 2026-03-22 — Per-timeframe risk multiplier (H4 → 0.55%)

**Problème** : H4 crypto produit ~1.2 trades/jour moyen. Le daily DD est naturellement
plafonné par la fréquence basse. Avec 0.45%/trade (même risk que H1), on sous-utilise
la capacité du timeframe à absorber plus de risque.

**Backtest** (510 trades, 14 crypto, 20 mois) :

| Risk/trade | Daily DD max | MaxDD pire instr. | Jours disq |
|---|---|---|---|
| 0.45% | 0.5% | 3.1% (AAVEUSD) | 0 |
| 0.55% | 0.6% | 3.8% | 0 |
| 0.60% | 0.6% | 4.1% | 0 |

Même à 0.60%, le daily DD max est 0.6% — très loin du guard 3%.
Pire scénario réaliste (3-4 instruments perdant le même jour) : ~1.8% daily DD.

**Solution** : `risk_multiplier_by_timeframe` dans settings.yaml, appliqué dans
le dispatcher après compute_sizing. H4 → ×1.22 (0.45% × 1.22 ≈ 0.55% effectif).
Conservateur par rapport au 0.60% testé.

**Implémentation** :
- `settings.yaml` : `general.risk_multiplier_by_timeframe.h4: 1.22`
- `order_dispatcher.py` : lookup `signal.timeframe.lower()` → multiplier
- `bar_aggregator.py` : override `signal.timeframe` avec le TF réel de l'aggregator
  (les signal.py hardcodent "1h", ce qui serait faux pour un aggregator H4)
- `live.py` : passe le dict au dispatcher

## Décision 2026-03-22 — Glissade activé en live (plus shadow)

**Constat** : Glissade était documenté comme "shadow" mais dans le code, les signaux
passaient déjà au dispatcher normalement (pas de mécanisme de shadow dans le code).
Le shadow était une intention documentaire, pas une implémentation.

**Backtest de référence** (151 trades, 0.45% risk) :

| Instrument | Trades | WR | Exp(R) | PF | MaxDD | Daily DD max |
|---|---|---|---|---|---|---|
| XAUUSD | 60 | 80% | +0.132R | 1.66 | 1.7% | 0.5% |
| BTCUSD | 91 | 85% | +0.157R | 2.02 | 1.4% | 0.5% |

WF 3/3 PASS, WR 83%, Exp +0.147R. Les guards sont sûrs (daily DD max 0.5%).

**Décision** : Glissade est maintenant officiellement live sur XAUUSD + BTCUSD H1.

## Décision 2026-03-22 — Guard "Best Day" (métrique de consistance)

**Problème** : FTMO impose que le meilleur jour ne représente pas plus de X% du
profit total des jours positifs. Un trade géant peut invalider le challenge.

**Implémentation** : `best_day_pct` ajouté dans `metrics.py`. Calcule le pourcentage
du meilleur jour positif sur le total de tous les jours positifs.

Résultats sur les stratégies actives :
- Extension H1 XAUUSD : best_day = 6.4% (excellent)
- Glissade XAUUSD : 14.9%, BTCUSD : 10.6%
- Crypto individuelles : 19-33% (normal avec peu de trades par instrument)
- En portefeuille combiné, dilué à ~5-10% (de multiples instruments contribuent)

**Statut** : métrique de backtest uniquement. Guard live (alerter si seuil approché)
en TODO pour le mode challenge.

## Décision 2026-03-22 — Scan Fouetté crypto M1 (fréquence)

**Question** : Peut-on augmenter la fréquence de trades via Fouetté sur plus de crypto ?

**Résultats** (14 crypto, session NY, 803 jours, M1) :

| Instrument | Trades | WR | Exp(R) | Verdict |
|---|---|---|---|---|
| BNBUSD | 181 | 74% | +0.031R | **Seul viable** (avec BTCUSD) |
| BTCUSD | 185 | 72.4% | +0.019R | **Viable** (WF PASS déjà connu) |
| ETHUSD | 314 | 73.2% | +0.004R | Breakeven |
| SOLUSD | 476 | 74.4% | -0.002R | Breakeven |
| NEARUSD | 586 | 73.0% | -0.000R | Breakeven |
| LTCUSD | 346 | 74.0% | -0.007R | Breakeven |
| XRPUSD | 358 | 74.0% | -0.013R | Négatif |
| AVAXUSD | 524 | 71.4% | -0.006R | Breakeven |
| Reste | - | <72% | <-0.018R | Négatif |

**Constat** : XAUUSD London = 3 trades/800j (quasi mort). XAUUSD NY = 14 trades.
Crypto : seuls BNBUSD et BTCUSD ont un edge. BTCUSD+BNBUSD = 0.46 trades/jour.

**Décision** : Fouetté ne change pas la donne. Edge trop faible (+0.019-0.031R),
fréquence insuffisante pour accélérer un challenge. Le portefeuille actuel
(Extension H1+H4 + Glissade H1) est le vrai levier. L'accélération passe par
le risk 0.80% en challenge, pas par l'ajout de stratégies marginales.

Fouetté reste en "WF validé, non déployé" pour BTCUSD NY + BNBUSD NY.
Activation possible comme source complémentaire de trades, mais impact marginal.

## Décision 2026-03-22 — Monte Carlo avec barrières (calibration challenge)

**Question** : Quelle est la probabilité d'atteindre +10% (target challenge FTMO)
avant de toucher -10% (DD max) ?

**Implémentation** : `monte_carlo_barriers()` dans `stats.py`. Tire des séquences
de trades aléatoires dans la distribution historique, s'arrête quand une barrière
est touchée (target ou DD) ou après 500 trades (timeout).

**Résultats — Portefeuille combiné** (880 trades, WR 80%, Exp +0.040R) :
Extension H1 (XAUUSD, GBPJPY, AUDJPY, CHFJPY) + Extension H4 (crypto) + Glissade H1 (XAUUSD, BTCUSD).

| Risk/trade | P(+10%) | P(DD 10%) | Timeout | Trades médians |
|---|---|---|---|---|
| 0.45% | 55.9% | 0.3% | 43.8% | 320 |
| 0.55% | 67.9% | 0.9% | 31.2% | 276 |
| 0.60% | 72.2% | 1.5% | 26.4% | 257 |
| **0.80%** | **81.8%** | **4.5%** | 13.7% | **196** |

**Interprétation** :
- À 0.80% (mode challenge) : **81.8% de succès, 4.5% de breach** → ratio 18:1
- Médiane 196 trades ≈ 2-3 mois de trading (H4 ~1.2 trades/jour + H1 ~1 trade/jour)
- Le P(breach) reste sous 5% même à 0.80% → les guards LiveMonitor (CAUTION/DANGER/EMERGENCY)
  réduiraient encore ce risque en live
- Le timeout (13.7%) correspond aux trajectoires "flat" où ni le target ni le DD ne sont touchés

**Recommandation** :
- **Funded** : 0.45% H1 + 0.55% H4 (config actuelle, P(target) 56-68%)
- **Challenge** : 0.80% uniforme (P(target) 82%, P(breach) 4.5%, ~2-3 mois)

**Note** : L'Exp H4 crypto est quasi-nulle (-0.003R) dans ce backtest sans tick TSL
optimisé. En live avec tick TSL, l'edge est plus élevé (~+0.065R par CLI).
Le portefeuille combiné est ce qui porte la performance : diversification H1+H4+Glissade.

**P(breach) surestimé** : Le Monte Carlo simule à risque constant. En live, deux
mécanismes réduisent le risque dynamiquement :
1. `compute_sizing` (guards.py) : réduction linéaire du risk entre 0% et -(max_total_dd - margin%)
2. LiveMonitor (live_monitor.py) : 4 paliers de protection
   - CAUTION (daily ≤-2.5% ou total ≤-5%) → risk ×0.50
   - DANGER (daily ≤-3.0% ou total ≤-6.5%) → risk ×0.25 + ferme positions sans BE
   - EMERGENCY (daily ≤-3.5% ou total ≤-8.0%) → ferme TOUT + freeze trading
Le P(breach) réel est donc significativement inférieur aux 4.5% simulés à 0.80%.

## Décision 2026-03-22 — Fix risk_cash/volume dans le trade journal

**Problème** : Tous les trades dans `trade_journal.jsonl` affichent `risk_cash: 0.0`
et `volume: 0.01` (hardcodé). Résultat : `pnl_cash` calculé à 0 → impossible de
mesurer la performance en $ depuis le journal.

**Cause** : `live.py:_on_order_result()` passait `volume=0.01` (hardcodé) et ne
passait pas `risk_cash` au `LiveMonitor.record_entry()`. Le `OrderResult` du broker
ne contient pas les données de sizing (calculées par le dispatcher, pas par le broker).

**Fix** :
1. `base.py:OrderResult` : ajouté `risk_cash` et `volume_lots` (enrichi par le dispatcher)
2. `order_dispatcher.py` : enrichit `result.risk_cash` et `result.volume_lots` depuis
   le `PlaceSignal` avant de passer au callback
3. `live.py:_on_order_result()` : utilise `result.fill_volume or result.volume_lots`
   et `result.risk_cash`

**Impact** : les anciens trades dans le JSONL gardent `risk_cash: 0.0` (pas corrigeable
rétroactivement). Les nouveaux trades auront les bonnes valeurs.

## Décision 2026-03-22 — Alerte lot sous-évalué + orphelins

**Lot sous-évalué** : ajouté warning dans le dispatcher si `risque_effectif < 50%
du risk_cash demandé`. Permet de détecter un problème de pip_value ou lot_size.

**Positions orphelines** : `reconcile()` dans position_monitor.py détecte maintenant
les positions broker non trackées par Arabesque. Log `👻 Position orpheline` avec
alerte si pas de SL ou pas de TP. Cas d'usage : positions ouvertes manuellement,
positions residuelles d'un crash, ou positions d'un autre système.

Pas de fermeture automatique (trop dangereux) — alerte seulement.

## Décision 2026-03-23 — Challenges FTMO = endpoint démo cTrader

**Problème** : le compte challenge 45667282 retournait `CANT_ROUTE_REQUEST` via l'API
cTrader. Le moteur tournait sur 46738849 (ancien compte test) par défaut.

**Cause** : Les challenges FTMO ont `live: false` dans l'API cTrader (ce sont des
comptes simulés/démo). Notre config avait `is_demo: false` → connexion à l'endpoint
`PROTOBUF_LIVE_HOST` au lieu de `PROTOBUF_DEMO_HOST` → le compte démo n'est pas
routable via l'endpoint live.

**Fix** : `is_demo: true` dans `settings.yaml` et `accounts.yaml` pour `ftmo_challenge`.

**Règle** : Toujours vérifier le champ `live` dans la réponse API cTrader
(`/trading/ctrader/accounts`) pour déterminer `is_demo`. Les comptes de challenge
prop firm utilisent typiquement l'environnement démo.

## Décision 2026-03-23 — OAuth centralisé (secrets.yaml)

**Problème** : les tokens cTrader étaient dupliqués par broker dans secrets.yaml.
Le refresh_token est à usage unique — si un broker le consomme, il invalide les
autres. Race condition au démarrage multi-comptes.

**Fix** : section partagée `ctrader_oauth` contenant client_id, client_secret,
access_token, refresh_token. Chaque broker référence via `oauth: ctrader_oauth`.
`_resolve_secret_refs()` dans `config.py` fusionne au chargement.
`update_broker_tokens()` détecte la référence et écrit dans la section partagée.

## Décision 2026-03-26 — 3 bugs critiques TradeLocker/GFT

### Bug 1 : order_id ≠ position_id

**Symptôme** : Positions ouvertes sur GFT sans SL/TP, détectées comme orphelines.
L'engine cherchait la position par `order_id` retourné par `create_order()`, mais
TradeLocker utilise un `position_id` distinct. Résultat : position "not found" →
enregistrée avec valeurs estimées → retirée après 6 min → position réelle orpheline.

**Fix** : `place_order()` appelle `get_position_id_from_order_id()` après placement
pour résoudre le vrai `position_id` et le retourner dans `OrderResult.order_id`.

### Bug 2 : amend_position_sltp manquant

**Symptôme** : position_monitor ne pouvait pas modifier le SL/TP sur TradeLocker
(méthode `set_position_protection` inexistante dans TLAPI).

**Fix** : Implémente `modify_position()` avec `TLAPI.modify_position(id, params_dict)`
(format `{"stopLoss": x, "stopLossType": "absolute"}`) et `amend_position_sltp()`
qui y délègue. Sync wrapper utilise `TradeLockerBroker.modify_position` explicitement
pour éviter la boucle sync→async→sync.

### Bug 3 : sizing XAUUSD 0.01L au lieu de 0.08L

**Symptôme** : Volume 100× trop petit sur GFT pour XAUUSD. Le dispatcher calculait
`pip_value = lot_size × pip_size` avec `pip_size` de instruments.yaml (0.01), mais
GFT retourne `pip_size=0.0001` (convention 4 décimales pour les métaux).

**Fix** : `_compute_lots_for_broker()` utilise `sym_info.pip_size` du broker quand
`get_symbol_info()` retourne une valeur positive, avec log du remplacement.

### Leçon

Les conventions diffèrent entre brokers (contractSize, pip_size, order_id vs
position_id). Toujours vérifier les positions orphelines dans les logs après
activation d'un nouveau broker.

---

## Décisions 2026-03-27

### DD tracking persistant (fix faux EMERGENCY)

**Problème** : `_refresh_account_state()` recréait `AccountState` avec
`start_balance=info.balance` (balance courante) à chaque refresh de 2 min.
Résultat : le DD était calculé comme `(equity - balance_courante) / balance_courante`
= le floating P&L instantané, pas le vrai drawdown depuis la balance initiale.
Un floating de -7.3% déclenchait un faux EMERGENCY alors que la perte réelle
était de -0.5% ($516 sur 100k).

**Décision** : trois variables d'instance persistantes dans `LiveEngine` :
- `_initial_balance` : depuis `accounts.yaml`, ne change jamais
- `_daily_start_balance` : balance broker au démarrage, reset à UTC midnight
- `_daily_start_date` : date du jour pour détecter le rollover

**Fichier** : `arabesque/execution/live.py` — méthodes `_init_dd_tracking()` et
`_refresh_account_state()`.

### Migration systemd (remplacement nohup)

**Problème** : le moteur live tournait via `nohup ... > /tmp/arabesque_live.log &`.
Risques : logs perdus au reboot (tmpfs), pas d'auto-restart si crash, fichier
log qui grossit indéfiniment.

**Décision** : service systemd user `arabesque-live.service` avec :
- `Restart=on-failure` + `RestartSec=30` (auto-restart)
- `StandardOutput=journal` (rotation automatique via journald)
- `WantedBy=default.target` + `enable-linger` (démarre au boot)
- Template dans `deploy/systemd/`, installé via `scripts/install_service.sh`

**Testé** : arrêt du processus nohup + démarrage systemd → réconciliation OK
(2 positions ouvertes retrouvées), Telegram OK, DD tracking correct.

### Révérence — overlap 14% avec Extension

**Analyse** : sur DOGEUSD H4 (2024-01 → 2026-03), 460 signaux Révérence vs
286 signaux Extension, 50 jours en commun = **14% d'overlap** côté Révérence.

**Décision** : Révérence est complémentaire (contrairement à Cabriole 73-95%),
mais l'edge est trop mince (Exp +0.059R, seul DOGEUSD passe WF) pour un
déploiement immédiat. À réévaluer si davantage d'instruments passent le WF.

### Telegram — token corrigé

**Cause** : URL Apprise `tgram://AAFX...YiQ/CHATID` manquait le préfixe
numérique du bot ID. Corrigé en `tgram://8427362376:AAFX...YiQ/CHATID`.
