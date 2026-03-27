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
