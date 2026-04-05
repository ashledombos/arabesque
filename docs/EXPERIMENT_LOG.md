# Arabesque — Journal d'expérimentations

> **Ce fichier documente TOUTES les expérimentations de stratégie et de paramètres.**
> Avant de tester une nouvelle idée, vérifier ici qu'elle n'a pas déjà été testée.
> Mis à jour à chaque expérimentation significative.

---

## Table des matières

1. [Stratégie Extension — Itérations v3.0→v3.3](#1-stratégie-extension--itérations)
2. [Paramètres de position management](#2-paramètres-de-position-management)
3. [Univers d'instruments](#3-univers-dinstruments)
4. [Stratégies alternatives](#4-stratégies-alternatives)
5. [Incidents live et enseignements](#5-incidents-live-et-enseignements)
6. [Feuille de route — Éléments BB_RPB_TSL non testés](#6-feuille-de-route--éléments-bb_rpb_tsl-non-testés)
7. [Volatility targeting](#7-volatility-targeting--rejeté-2026-03-28)
8. [Cooldown post-fill — comparaison 4 modes](#8-cooldown-post-fill--comparaison-4-modes-2026-03-30)
9. [Distribution des trades Extension — analyse R](#9-distribution-des-trades-extension--analyse-r-2026-03-30)

---

## 1. Stratégie Extension — Itérations

### v3.0 — ROI dégressif (2026-02-21)
- **Config** : ROI dégressif (0→3R, 48→1R, 120→0.5R, 240→0.15R), SL=1.5 ATR, BE 0.5R/0.25R, time-stop 336 barres
- **Résultat** : 786 trades, WR=50.6%, Exp=+0.094R, Total=+73.9R
- **Verdict** : Seule version profitable des 4 itérations ROI

### v3.1 — ROI courts + BE abaissé (2026-02-21)
- **Config** : v3.0 + ROI courts (6/12/24/48/120h), BE à 0.5R, SL=2.0 ATR, typical_price BB, RSI oversold=30
- **Résultat** : 568 trades, WR=63.9%, Exp=-0.004R ❌
- **Verdict** : WR amélioré mais expectancy négative. ROI courts tuent l'avg_win.

### v3.2 — BE offset + SL recalibration (2026-02-22)
- **Config** : v3.1 + BE offset 0.25R, SL 1.5 ATR
- **Résultat** : 622 trades, WR=60.6%, Exp=-0.010R, Total=-6.4R ❌
- **Verdict** : Toujours négatif. ROI court + SL réel = incompatible.

### v3.3 — Retour v3.0 + chirurgie (2026-02-22)
- **Config** : v3.0 + BB typical_price, BE 0.5R/0.25R, Giveback 0.5R
- **Résultat** : 998 trades, WR=60.2%, Exp=+0.034R, Total=+33.5R
- **Verdict** : Positif mais faible.

### 💡 Insight ROI court + SL réel
BB_RPB_TSL (référence) n'a pas de SL réel (-99%) → couper les profits tôt ne coûte rien.
Arabesque a SL = -1R → chaque SL doit être compensé par avg_win.
**ROI court → avg_win chute → exp négative. ABANDONNÉ DÉFINITIVEMENT.**

---

### Validation 20 mois — Config définitive (2026-02-24)
- **Config** : Trend-only, BE 0.3R/0.20R, SL dynamique, pas de ROI
- **Univers** : 76 instruments (49 Dukascopy + 27 crypto)
- **Résultat** : 1998 trades, WR=75.5%, Exp=+0.130R, Total=+260.2R, Max DD=8.2%, PF=1.55, IC99>0 ✅
- **8/8 blocs temporels positifs**
- **Verdict** : Config de production. Ne pas modifier sans rejeu complet.

---

## 2. Paramètres de position management

### BE trigger / offset — Grille exhaustive (2026-02-22)

Simulé sur 998 trades (v3.3) ET 786 trades (v3.0) :

| Config | WR v3.3 | Exp v3.3 | WR v3.0 | Exp v3.0 | Verdict |
|---|---|---|---|---|---|
| **BE 0.3/0.20** | 79.7% | +0.250R | 78.5% | +0.414R | ✅ **PRODUCTION** |
| BE 0.3/0.15 | ~80% | +0.250R | ~79% | +0.414R | ❌ Trop serré, 95% exits au plancher |
| BE 0.5/0.25 | 68.4% | +0.130R | 67.4% | +0.314R | ❌ 12pts WR de moins |
| BE 1.0/0.05 | 36.9% | -0.294R | 49.0% | +0.021R | ❌ Détruit l'edge |

**Pourquoi BE 0.3 > 0.5** : 80% des trades atteignent MFE ≥ 0.3R (vs 68% pour ≥ 0.5R).
La tranche [0.3, 0.5) = 112 trades convertis de -1R en +0.15R → +128.8R supplémentaires.

**Pourquoi offset 0.20 > 0.15** : à 0.15, 95% des trailing exits sortaient au plancher exact (+0.15R) — trop serré pour le bruit OHLC. Simulation BE 0.3/0.20 sur Replay B: +60.1R vs +42.8R avec 0.15.

### Risk per trade — Calibration (2026-02-24)

| Risk | Max DD (20 mois) | Marge FTMO (10%) | Verdict |
|---|---|---|---|
| 0.50% | 10.3% | -0.3% ❌ breach | ❌ |
| **0.45%** | ~9.2% | +0.8% | ✅ Production FTMO |
| **0.40%** | 8.2% | +1.8% | ✅ Conservateur |
| 0.80% | ~16.4% | -6.4% ❌ | ⚠️ Objectif futur si compte stabilisé |

**H4 crypto risk multiplier** : ×1.22 → risk effectif 0.55% en H4 (validé séparément).

### TSL tick-level vs H1-only (2026-03-06)

| Mode | Total R (20 mois) | Verdict |
|---|---|---|
| H1-only (close bar) | +10.4R | ❌ BE manqué trop souvent |
| **Tick-level (10s throttle)** | +183R | ✅ **NON NÉGOCIABLE** |

Le TSL capte le breakeven et les trailing tiers en temps réel. Sans tick-level, BE est posé en retard (voire jamais), les losers ne sont pas convertis.

### Trailing paliers

| Palier | MFE trigger | SL → | Rôle |
|---|---|---|---|
| BE | 0.3R | entry + 0.20R | Convertit losers en petits gains |
| Tier 1 | 1.5R | entry + 0.7R | Sécurise les profits moyens |
| Tier 2 | 2.0R | entry + 1.0R | Sécurise les bons trades |
| Tier 3 | 3.0R | entry + 1.5R | Capture les grands mouvements |

### ROI dégressif sur crypto H4

| Config | Exp | Verdict |
|---|---|---|
| ROI activé (0→3R, etc.) | +0.044R | ❌ Détruit l'edge |
| **ROI désactivé** | +0.181R | ✅ Production |

**ABANDONNÉ sur crypto H4.** Le ROI coupe les tendances crypto trop tôt.

---

## 3. Univers d'instruments

### Mean-Reversion vs Trend — Test exhaustif (2026-02-22)

46 instruments, 319 trades, 4 replays :

| Approche | Catégories | Résultat | Verdict |
|---|---|---|---|
| **Mean-Reversion** | Toutes | Perd sur TOUTES les catégories | ❌ ABANDONNÉ |
| MR LONG spécifiquement | Toutes | 141 trades, -46.1R | ❌ Gouffre principal |
| MR SHORT | Toutes | 115 trades, +4.3R | ❌ Marginal |
| **Trend-only** | Toutes | 63 trades, WR 84%, +27.8R | ✅ PRODUCTION |

**TREND-ONLY est la seule approche viable.** Testée sur 4 replays, 2 périodes, 3 univers.

### Catégories d'instruments (2026-02-22 → 02-24)

| Catégorie | H1 trend | H4 trend | Verdict |
|---|---|---|---|
| Crypto (27 inst.) | +145.0R (20 mois) | Principal driver | ✅ Live |
| Forex JPY crosses | Positif (GBPJPY, AUDJPY, CHFJPY) | Non testé | ✅ Live H1 |
| Forex majors (EUR, GBP, AUD, NZD) | ❌ Négatif en WF | Non testé | ❌ Exclu |
| XAUUSD | Positif H1 | N/A | ✅ Live H1 |
| Indices (US100, etc.) | Pas de parquets | N/A | 🔄 Non testé |
| Énergie | Pas de parquets | N/A | 🔄 Non testé |

**Insight** : la conclusion "Dukascopy only" (session 02-23) était basée sur 1 seule période.
Sur 20 mois, crypto trend SURPERFORME forex trend. Le drawdown Avr-Jul crypto était localisé, pas structurel.

### Données Dukascopy vs CCXT — Impact sur performance (2026-02-23)

Sur une seule période (Avr→Jul) :
- Dukascopy (forex + métaux, données M1) : +29.3R, WR 79%
- CCXT (crypto) : -11.0R
- **MAIS** : sur 20 mois, crypto = +145R. La conclusion initiale était trop hâtive.

### SHORT vs LONG bias (2026-02-23)

Sur période Oct 2025 → Jan 2026 :
- SHORT : systématiquement positif (+64.3R, +30.9R, +5.1R)
- LONG : systématiquement négatif (-14.4R, -3.2R, -16.0R)
- **Possiblement saisonnier** — non intégré en filtre, à surveiller.

---

## 4. Stratégies alternatives

### Extension H1/H4 — BB squeeze → breakout (PRODUCTION)
- **Statut** : ✅ Live
- **Résultats** : WR 75.5%, Exp +0.130R, 1998 trades / 20 mois
- **Instruments** : H1 (XAUUSD, GBPJPY, AUDJPY, CHFJPY), H4 (27 crypto)

### Glissade — RSI divergence H1 (PRODUCTION)
- **Statut** : ✅ Live (WF 3/3 PASS)
- **Config** : RR3 + BE
- **Résultats** :
  - XAUUSD H1 : 31 trades OOS, WR 87%, Exp +0.185R
  - BTCUSD H1 : 54 trades OOS, WR 85%, Exp +0.196R

### Fouetté — Opening Range Breakout M1 (VALIDÉ, NON DÉPLOYÉ)
- **Statut** : WF 4/4 PASS, mais fréquence insuffisante
- **Config** : Session-based (London, NY)
- **Résultats** :
  - XAUUSD London : 63 trades, WR 76%, Exp +0.086R, RR1.5 +BE
  - US100 NY : 147 trades, WR 44%, Exp +0.190R, RR2 no_BE
  - BTCUSD NY : 280 trades, WR 76%, Exp +0.043R, RR1.5 +BE
- **Raison non-déploiement** : fréquence trop basse sur forex/métaux, seule crypto viable en M1

### Cabriole — Donchian breakout 4H (VALIDÉ, BACKUP)
- **Statut** : WF 6/6 PASS, overlap 73-95% avec Extension
- **Résultats** : Positif mais presque identique à Extension sur les mêmes instruments
- **Raison non-déploiement** : overlap trop élevé, servirait de backup si Extension désactivée

### Renversé — Liquidity sweep + FVG retrace H1 (EDGE INSUFFISANT)
- **Statut** : ❌ Testé, abandonné
- **Concept** : ICT/SMC reversal — sweep de liquidité + structure shift + FVG retrace
- **Résultats** : 142 trades, WR 73%, Exp +0.006R = **breakeven**
- **Verdict** : WR correct mais expectancy nulle. Les reversals ICT/SMC ne fonctionnent pas avec un SL réel contraint.

### Révérence — NR7 contraction → expansion H4 (EN COURS)
- **Statut** : 🔄 WF en cours
- **Concept** : Range contraction (NR4/NR7) puis breakout expansion
- **Résultats partiels** : DOGEUSD PASS WR 83%
- **Question ouverte** : overlap avec Extension à vérifier

### Pas de Deux — Pairs trading cointégration (NON VIABLE)
- **Statut** : ❌ Concept abandonné
- **Concept** : Mean-reversion sur paires cointégrées
- **Raison** : Mean-reversion incompatible avec la boussole (testée et rejetée sur 4 replays)

---

## 5. Incidents live et enseignements

### 2026-03-04 — Fill mismatch concurrent orders
- **Symptôme** : Fill LNKUSD enregistré comme SOLUSD (3 ordres simultanés)
- **Cause** : `_pending_requests` écrasait les futures des ordres concurrents
- **Fix** : Dispatch séquentiel via asyncio.Queue FIFO
- **Leçon** : UN SEUL ordre en vol à la fois

### 2026-03-05 — Pertes -$1959 (BE H1-only)
- **Symptôme** : Tous les trades perdent, BE manqué ou skippé
- **Cause** : BE/trailing ne vérifie qu'au close H1 → MFE atteint en intrabar puis crash au SL
- **Fix** : TSL tick-level monitoring (position_monitor.py)
- **Leçon** : TSL tick-level NON OPTIONNEL (+183R vs +10.4R)

### 2026-03-26 — 3 bugs critiques TradeLocker/GFT
- **Bug 1** : `order_id ≠ position_id` → positions orphelines sans SL/TP
- **Bug 2** : `set_position_protection` n'existe pas → SL/TP jamais modifiés
- **Bug 3** : `pip_size` GFT XAUUSD = 0.0001 vs cTrader 0.01 → sizing 100× trop petit
- **Fix** : `get_position_id_from_order_id()`, `modify_position()`, `broker_pip_size`
- **Leçon** : Toujours utiliser `sym_info.pip_size` du broker, pas du yaml

### 2026-03-27 — Faux EMERGENCY DD -7.3%
- **Symptôme** : EMERGENCY déclenchée, 4 positions fermées, trading gelé
- **Cause** : `_refresh_account_state()` recréait `AccountState` avec `start_balance=info.balance` à chaque refresh → DD = floating P&L instantané, pas DD réel
- **Réalité** : Perte réelle ~$516 (-0.5%), pas -7.3%. DD total depuis 100k = -5.5%
- **Fix** : `_initial_balance` depuis accounts.yaml, `_daily_start_balance` persistant, rollover UTC
- **Bilan** : L'emergency a par chance sauvé ~0.6R (IMXUSD/GALUSD allaient au SL), mais a gelé 2 trades gagnants (ALGUSD +0.20R, MANUSD +0.20R)
- **Leçon** : Le calcul de DD doit être correct avant de l'utiliser pour des fermetures d'urgence

---

## Résumé des paramètres testés et rejetés

| Paramètre | Valeur testée | Résultat | Valeur retenue |
|---|---|---|---|
| ROI dégressif | Multiples configs | Tue l'edge avec SL réel | **Désactivé** |
| ROI sur crypto H4 | Activé | Exp 0.044R vs 0.181R sans | **Désactivé** |
| Mean-reversion | 4 replays, 3 univers | Perd partout | **Trend-only** |
| BE trigger 0.5R | BE 0.5/0.25 | WR 68%, -12pts vs 0.3 | **0.3R** |
| BE trigger 1.0R | BE 1.0/0.05 | WR 37%, exp négative | **0.3R** |
| BE offset 0.05R | v3.1 | 95% exits au plancher | **0.20R** |
| BE offset 0.15R | Replay B | 95% exits au plancher | **0.20R** |
| SL 2.0 ATR | v3.1 | Comprime avg_win ×1.33 | **1.5 ATR** |
| Risk 0.50% | 20 mois | DD 10.3% > FTMO 10% | **0.45%** |
| TSL H1-only | Live 03/06 | +10.4R vs +183R tick | **Tick-level** |
| Forex majors H1 | WF | Négatif | **Exclu** |
| ICT/SMC reversals | 142 trades | Exp +0.006R = breakeven | **Abandonné** |
| Pairs trading MR | Concept | Incompatible boussole | **Abandonné** |

---

## 6. Feuille de route — Éléments BB_RPB_TSL non testés

> **Source** : analyse vidéo + code BB_RPB_TSL (2026-03-28)
> **Protocole de test** : multi-TF (H1 + H4) × multi-instruments (forex, crypto, métaux).
> Fenêtre courte mais pertinente : ~3 mois H1, ~6 mois H4 (≥100 trades).
> Si un élément est inconsistant sur fenêtre courte, il est rejeté — pas besoin de 20 mois
> pour prouver qu'il ne marche pas. La consistance prime sur la performance brute.

### Priorité haute — Sorties / protection

| # | Élément | Description BB_RPB_TSL | État Arabesque | Test proposé |
|---|---|---|---|---|
| H1 | **Sortie agressive sous EMA 200** | Quand prix < EMA 200, logique de sortie plus stricte (trailing plus serré, seuils abaissés) | EMA 200 trackée (`current_ema200`) mais **jamais utilisée** dans le position_manager | Ablation : trailing tiers ×0.5 et/ou giveback trigger abaissé quand `close < ema200`. Mesurer WR/Exp delta |
| H2 | **Sortie RSI extrême + profit** | RSI > 80 + CTI > 0.95 avec profit latent → prendre le profit immédiatement | Absent. Le trailing ne s'active qu'à MFE ≥ 1.5R | Sortie conditionnelle : si RSI > 80 et current_r > 0.3R → exit. Tester seuils RSI 75/80/85 |
| H3 | **Sortie divergence momentum surextensif** | MOMDIV croise au-dessus de sa propre BB supérieure → mouvement surextensif, vendre | Giveback détecte momentum *faible*, pas *surextensif* | Implémenter MOMDIV ou proxy (RSI > 80 + RSI en baisse). Comparer avec H2 |

### Priorité haute — Entrées / filtres

| # | Élément | Description BB_RPB_TSL | État Arabesque | Test proposé |
|---|---|---|---|---|
| H4 | **Protection slippage à l'entrée** | Vérifie que le prix actuel n'a pas trop dévié du prix du signal avant exécution | `max_spread_atr=0.3` mais pas de vérification slippage signal→fill | Implémenter dans `live.py` : rejeter si `|fill_price - signal.close| > X × ATR`. Mesurer trades rejetés en replay |
| H5 | **BB 3σ — filtre mèche violente** | BB à 2σ ET 3σ ; delta entre les deux niveaux ; prix sous BB 3σ = dump violent, pas un pullback | BB 2σ uniquement | Ajouter BB 3σ dans indicators. Filtre : rejeter entrée si close > BB_upper_3σ (breakout trop violent). Mesurer faux positifs vs trades évités |

### Priorité haute — Gestion de position (observations utilisateur 2026-03-28)

| # | Élément | Observation / Hypothèse | État Arabesque | Test proposé |
|---|---|---|---|---|
| H6 | **Trailing structure-based (Dow Theory)** | Le BE à 0.3R est "aveugle" — il peut éjecter des trades dans de simples pullbacks. Hypothèse : remonter le SL au dernier swing low seulement quand un swing high est revisité/dépassé (BOS), pas quand un seuil R arbitraire est atteint. Plus cohérent avec la structure de marché. | BE/trailing en R fixe uniquement. Pivots locaux déjà implémentés dans `indicators.py` (Glissade) — réutilisables | 1) Détecter swing H/L en temps réel sur le TF du trade. 2) Trail SL → dernier swing low quand BOS confirmé (new high > previous swing high). 3) Comparer avec trailing R fixe actuel. Attention : détection swing a un lag inhérent (confirmation pivot_window barres après) |
| H7 | **Sortie perte de dynamique (giveback étendu)** | BB_RPB_TSL sort quand le profit max était ~4.5% et redescend vers 0 avec RSI < 46. Arabesque a le giveback mais les seuils sont peut-être trop conservateurs. Observation multi-TF : parfois en montant de TF le momentum a déjà disparu, parfois en descendant de TF le prix range autour de l'entrée | Giveback existant : MFE ≥ 0.5R et current ≤ 0.15R et RSI < 46 + CMF < 0 | 1) Tester giveback plus agressif : MFE ≥ 0.3R et current ≤ 0.10R. 2) Ajouter check HTF : si HTF momentum baissier → abaisser les seuils. 3) Ajouter check LTF : si prix range ±0.1R autour de l'entrée pendant N barres → sortie (variante deadfish plus précoce) |
| H8 | **Split position (fermeture partielle)** | Diviser le risque en 2 positions (A et B, chacune à 0.5R de risque). Position A : trailing serré (runner). Position B : SL en miroir de A → si A a TSL à +0.1R, B a SL à -0.1R = BE garanti net. Permet de verrouiller les gains tout en laissant courir. | **cTrader : fermeture partielle supportée** (`close_position(id, volume=X)`). TradeLocker : fermeture partielle à vérifier dans la doc API (implémentation actuelle = close total uniquement) | 1) Vérifier API TradeLocker partial close. 2) Simuler en backtest : 2 demi-positions, A=trailing serré + B=SL miroir. 3) Comparer courbe d'équité vs position unique. 4) Attention aux frais ×2 et au min_volume broker |

### Priorité moyenne — Gestion de position

| # | Élément | Description BB_RPB_TSL | État Arabesque | Test proposé |
|---|---|---|---|---|
| M1 | **Trailing continu (tightening progressif)** | Plus le profit monte, plus le stop se rapproche — comportement non linéaire, pas des paliers | 3 paliers fixes (1.5R/2.0R/3.0R) avec distances fixes (0.7/1.0/1.5R) | Formule continue : `trail_distance = max(0.3, MFE × 0.5)` ou similaire. Comparer avec paliers actuels |
| M2 | **Ablation du TP fixe** | BB_RPB_TSL n'a AUCUN TP fixe — tout sort par trailing/ROI/custom_exit | TP indicatif 1.5R pour `trend_strong` | Désactiver le TP 1.5R, laisser trailing + BE + giveback gérer seuls. Mesurer si les "runners" compensent les pertes de TP |
| M3 | **Sortie par invalidation de régime** | Sortie si conditions de marché invalidées (perte de tendance, momentum inversé) | Régime HTF calculé à l'entrée mais **jamais réévalué** pendant le trade | Réévaluer le régime à chaque barre. Si un trade LONG est ouvert et que le régime passe en `bear_trend` → sortie ou trailing immédiat |
| M4 | **Multi-TF momentum check en cours de trade** | Observation utilisateur : en montant de TF on voit parfois que le momentum n'est plus là ; en descendant, que le prix range | Aucun check multi-TF sur les positions ouvertes | 1) Check HTF (ex: 4H pour trade H1) : si RSI HTF en baisse → flag. 2) Check LTF (ex: 15M pour trade H1) : si ATR LTF < X% → ranging. Combiné avec H7 pour décision de sortie |

### Priorité faible — Indicateurs additionnels

| # | Élément | Description BB_RPB_TSL | État Arabesque | Test proposé |
|---|---|---|---|---|
| L1 | **Volume brut comme filtre d'entrée** | Volume en hausse pendant un creux = vrais acheteurs | CMF comme proxy | Filtre : `volume > SMA(volume, 20)`. Comparer avec CMF seul |
| L2 | **Pente des EMA** | Non seulement croisement EMA fast > slow, mais pente (accélération du trend) | Croisement EMA uniquement | Calculer `ema_slope = (ema - ema[5]) / ema[5]`. Filtrer si slope < seuil |
| L3 | **Multi-RSI (4, 14, 20)** | RSI rapide pour réactivité, RSI lent pour confirmation | RSI 14 uniquement | Ajouter RSI 4 et RSI 20. Tester comme filtres d'entrée et/ou de sortie |
| L4 | **Williams %R multi-période** | WR 14/32/64/96/480 pour surachat/survente multi-horizon | WR 14 uniquement | Tester WR 96 ou 480 comme filtre "ne pas acheter un trend mort" |
| L5 | **PMAX** | Signal haussier/baissier basé sur ATR + SuperTrend | Absent | Implémenter et tester comme filtre de sortie. Probablement redondant avec trailing |

### Protocole de test (rappel)

```
Pour chaque élément :
1. Backtest H1 : XAUUSD, GBPJPY, BTCUSD (3 mois récents)
2. Backtest H4 : BTCUSD, ETHUSD, SOLUSD (6 mois récents)
3. Si Exp > 0 ET WR > 50% sur les deux → étendre à l'univers complet
4. Si inconsistant (Exp < 0 sur un TF ou un instrument) → rejeter
5. Walk-forward final avant mise en production
```

### Ordre d'exécution des tests

> Principe : tester d'abord ce qui ne nécessite aucun nouveau code (ablation pure),
> puis ce qui utilise des données déjà trackées (RSI, EMA200, régime),
> puis ce qui nécessite de nouveaux indicateurs ou de l'architecture.

| Ordre | ID | Élément | Effort code | Raison de l'ordre | Résultat |
|---|---|---|---|---|---|
| 1 | M2 | Ablation TP fixe | Zéro — désactiver `tp_r_by_subtype` | Ablation pure, aucun risque | **NEUTRE** — Δ +0.002R crypto, -0.012R métal. TP 1.5R aide XAUUSD. Pas de changement. |
| 2 | H7 | Giveback seuils agressifs | Zéro — modifier params ManagerConfig | Ablation pure, params existants | **NEUTRE** — Δ -0.001R crypto H1, +0.002R H4. Seuils actuels corrects. Pas de changement. |
| 3 | H1 | Sortie agressive sous EMA 200 | Faible — EMA200 déjà dans `pos.current_ema200` | Données déjà trackées, logique simple | **NEUTRE** — Δ 0.000R partout. Le filtre régime à l'entrée empêche déjà les trades du mauvais côté de l'EMA 200. Giveback seul ne suffit pas, à combiner avec M3 (invalidation régime en cours de trade). |
| 4 | H2 | Sortie RSI extrême + profit | Faible — RSI déjà dans `pos.current_rsi` | Données déjà trackées | **REJETÉ** — Négatif partout sauf forex majors. RSI > 80 = momentum fort en trend-following, couper = perdre les runners. Logique MR incompatible avec trend-only. |
| 5 | M3 | Invalidation de régime en cours de trade | Faible — régime déjà calculé par signal.py | Réutilise le compute_htf_regime existant | **NEUTRE** — Δ 0.000R partout. Le régime HTF (EMA 50/200 + ADX sur 4H) change trop lentement vs durée trade (~14h). Le filtre d'entrée empêche déjà d'entrer dans le mauvais régime. |
| 6 | H5 | BB 3σ filtre mèche violente | Moyen — ajouter BB 3σ dans indicators | Nouvel indicateur simple | **REJETÉ** — Trades beyond 3σ ont WR 76.2% / +0.050R vs normal 74.2% / +0.005R (H1). En trend-following, les extensions 3σ sont les meilleurs breakouts. Filtrer = perdre les meilleurs trades. |
| 7 | H3 | Sortie momentum surextensif | Moyen — proxy via RSI (si H2 non concluant) | Dépend du résultat de H2 | **REJETÉ** — H2 prouve que couper sur RSI extrême tue les runners. H3 est le même concept (MOMDIV). Skip. |
| 8 | M1 | Trailing continu vs paliers | Moyen — modifier `_update_trailing` | Changement de logique existante | **NEUTRE** — 3 variantes (ratio 0.4/0.5/0.6). H1: ±0.01R max selon catégorie, gains crypto annulés par pertes metal. H4 crypto: trail_cont_tight +0.018R (397 trades, non significatif). Paliers actuels OK. |
| 9 | H6 | Trailing structure-based (Dow) | Élevé — swing detection temps réel | Concept nouveau, architecture | **NEUTRE** — Trail vers dernier swing confirmé (pivot_window=5). H1: crypto +0.005R, metal +0.028R, forex ±0. H4 crypto: -0.006R. trail_dow et trail_dow_tight identiques (offset 0.1 vs 0.05 ne change rien). Le swing detection avec lag=5 barres ne capture que rarement un niveau différent du trailing R fixe. |
| 10 | H8 | Split position (2 × 0.5R) | Élevé — simuler 2 positions dans backtest | Architecture backtest à adapter | **REJETÉ** — Post-hoc sim avec TP_A = 0.8/1.0/1.5R. WR inchangé (+0.0-0.2%), Exp toujours négatif (Δ -0.003R à -0.011R). Le BE à 0.3R fait déjà le travail du split — la majorité des wins sont déjà des petits gains au BE. TP_A n'est touché que dans 3-23% des trades (MFE rarement > 0.8R). |
| 11 | M4 | Multi-TF momentum in-trade | Élevé — charger TF additionnel pendant le trade | Données additionnelles à gérer | **NEUTRE (redondant)** — Post-hoc : ADX > 25 → WR 76.7% / +0.047R vs ADX < 25 → WR 71.9% / -0.029R. MAIS cette corrélation est déjà captée par le filtre d'entrée (régime = EMA + ADX). H4: 0 trades en régime non-aligné = 100% filtré à l'entrée. Pas d'action. |
| 12 | H4 | Protection slippage entrée | Live-only — pas de sens en backtest | Implémenter après les tests ci-dessus | À implémenter directement dans `live.py` (pas backtestable) |
| 13+ | L1-L5 | Indicateurs additionnels | Variable | Basse priorité, tester si temps | Non testés — priorité basse, après validation que l'edge de base est solide |

### Synthèse des tests BB_RPB_TSL (2026-03-28)

**11 éléments testés sur 13** (H4 = live-only, L1-L5 = basse priorité).

| Résultat | Tests | Conclusion |
|---|---|---|
| **REJETÉ** | H2, H3, H5, H8 | Logique mean-reversion incompatible avec trend-following |
| **NEUTRE** | M2, H7, H1, M3, M1, H6, M4 | Le système actuel capture déjà l'essentiel |
| **Non testé** | H4, L1-L5 | Live-only ou basse priorité |

**Leçon principale** : BB_RPB_TSL est un système mean-reversion crypto sans SL réel (-99%). Ses mécanismes de sortie (RSI extrême, ROI court, momentum surextensif) coupent les profits dans un contexte où les pertes sont virtuellement illimitées. En trend-following avec SL réel à -1R, ces mêmes mécanismes détruisent l'edge parce qu'ils tuent les runners qui compensent les losses.

Le système actuel d'Arabesque (BE 0.3R/0.20R + trailing paliers + giveback + deadfish) est le bon profil pour les prop firms : WR élevé via BE, runners via trailing, protection via giveback. Les filtres d'entrée (régime, squeeze, EMA 200) font déjà le travail que les sorties conditionnelles essaient de faire.

---

## 7. Volatility Targeting — Post-hoc simulation (2026-03-28)

**Concept** : Ajuster le risk% en fonction de la volatilité récente.
`risk_adjusted = base_risk × (target_vol / realized_vol)`, cappé entre [0.5×, cap_high×].
En haute vol → risk réduit → drawdown limité. En basse vol → risk augmenté → capture du calme.

**Motivation** : Seule technique avec consensus académique fort (revue littérature 2016-2026, `resources/gestion_trading_prop_firms.txt`).

**Méthode** : Simulation post-hoc sur 1049 trades H1 (12 instruments) et 213 trades H4 (9 instruments crypto). On recalcule le P&L effectif comme si le sizing avait été ajusté. La vol réalisée est l'écart-type des rendements log sur `vol_window` barres.

### Résultats H1 (1049 trades, 12 instruments)

| Vol window | Cap | Δ Total R | Δ MaxDD | Verdict |
|---|---|---|---|---|
| 20 | [0.5×, 1.5×] | **-10.5R** | -2.3% (pire) | REJETÉ |
| 20 | [0.5×, 2.0×] | **-11.2R** | -2.7% (pire) | REJETÉ |
| 50 | [0.5×, 1.5×] | **-12.1R** | -2.6% (pire) | REJETÉ |
| 50 | [0.5×, 2.0×] | **-10.8R** | -3.1% (pire) | REJETÉ |

### Résultats H4 crypto (213 trades, 9 instruments)

| Vol window | Cap | Δ Total R | Δ MaxDD | Verdict |
|---|---|---|---|---|
| 20 | [0.5×, 1.5×] | **+0.0R** | -0.2% | NEUTRE |

### Analyse

Le vol-targeting est conçu pour les stratégies momentum classiques (long-only equities, CTA) où la haute volatilité est un risque. Dans Extension, **haute vol = BB squeeze → breakout = notre signal d'entrée**. Réduire le risk en haute vol revient à réduire le sizing exactement quand le signal est le plus fort. Inversement, augmenter le risk en basse vol augmente l'exposition quand le marché range (pas de signal Extension).

De plus, le BE à 0.3R plafonne déjà les gains unitaires, donc l'augmentation de risk en basse vol ne produit pas de gains significativement plus grands — mais les pertes, elles, sont proportionnellement plus grandes.

**Verdict final : REJETÉ.** Le vol-targeting est inadapté aux stratégies BB squeeze/breakout. Notre normalisation ATR au sizing fait déjà le travail d'adaptation à la volatilité.

---

## 8. Cooldown post-fill — comparaison 4 modes (2026-03-30)

**Contexte** : le cooldown empêche de prendre un nouveau signal sur le même instrument pendant N barres après un fill. Valeur actuelle : 5 barres. Question : est-ce optimal ?

### Modes testés

| Mode | Description |
|---|---|
| A_cd5 | Statu quo — 5 barres cooldown post-fill |
| B_cd2 | Cooldown court — 2 barres |
| C_cd0 | Zéro cooldown |
| D_degr | Risque dégressif (pas de cooldown dur, risk ×0.25 si ≤2 bars, ×0.50 si 3-5, ×1.0 si >5) |

### Résultats agrégés (7 instruments : XAUUSD, GBPJPY, AUDJPY, CHFJPY H1 + BTCUSD, ETHUSD, SOLUSD H4)

| Mode | Trades | WR | Exp | Total R |
|---|---|---|---|---|
| A_cd5 | 445 | 74.6% | +0.093R | +41.4R |
| B_cd2 | 504 | 72.8% | +0.070R | +35.3R |
| C_cd0 | 565 | 70.6% | +0.043R | +24.3R |
| D_degr | 565 | 70.6% | +0.043R | +24.3R |

### Analyse

- Plus de trades (cd0 = +120 trades vs cd5) mais qualité dégradée : Exp passe de +0.093R à +0.043R
- Le mode dégressif D est identique à cd0 car les trades supplémentaires se déclenchent rarement dans la fenêtre 1-2 bars (le risk_mult s'applique mais les trades sont les mêmes)
- Les trades bloqués par le cooldown sont majoritairement des signaux de mauvaise qualité (post-breakout, re-test immédiat qui échoue)

**Verdict : CONFIRMÉ.** Le cooldown 5 barres est optimal. Les trades supplémentaires sans cooldown sont de moindre qualité et diluent l'expectancy.

---

## 9. Distribution des trades Extension — analyse R (2026-03-30)

**Contexte** : comprendre la structure réelle des gains/pertes pour évaluer la robustesse du système.

### Distribution bimodale

| Bucket | % trades | Contribution au P&L |
|---|---|---|
| Pertes (-1R) | 19% | -100% des pertes |
| BE wins (+0.20R) | 68% | ~54% des gains |
| Zone morte (+0.2R à +1R) | 4% | ~5% des gains |
| Runners (>+1R) | 9% | ~159% des gains nets |

### Observations clés

- Le BE à 0.3R/0.20R crée un système fondamentalement bimodal : soit le trade atteint exactement +0.20R (BE lock), soit il dépasse largement (runner)
- La zone +0.5R à +1R est quasi vide — le trailing tiers saute de BE à ~+1.5R minimum
- Les runners (9% des trades) génèrent 159% du P&L net — sans eux, le système serait breakeven
- RR 1:1 n'a pas été testé explicitement pour Extension, mais les tests apparentés (M2 TP ablation, H8 split position) montrent que fixer un TP tue les runners

**Implication** : le système est intrinsèquement dépendant des runners. Toute modification qui réduit la capacité à capturer les gros moves (TP fixe, ROI agressif, trailing trop serré) détruirait l'edge.

---

### Notes architecturales

- **Fermeture partielle cTrader** : `close_position(position_id, volume=X)` déjà supporté (volume en lots)
- **Fermeture partielle TradeLocker** : supporté par l'API (paramètre `qty` = volume à fermer, `qty=0` = close total). Non implémenté dans `tradelocker.py` — à coder si H8 validé en backtest
- **Pivots locaux** : déjà dans `indicators.py` (fonction `rsi_divergence`, pivot_window=5). Réutilisable pour H6 (trailing structure-based) mais nécessite une version temps-réel (le pivot est confirmé avec un lag de `pivot_window` barres)
- **Entrées en retard** : observation structurelle du trend-following — inhérent au modèle. Les filtres BB 3σ (H5) et slippage (H4) peuvent mitiger les cas extrêmes
