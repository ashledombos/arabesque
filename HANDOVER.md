# Arabesque — Guide de passation (Handover)

> **À lire en premier si tu reprends le projet.**
> Ce guide explique l’architecture, l’état actuel, les décisions de design,
> et comment faire tourner le système de A à Z.

---

## Table des matières

1. [Contexte et objectif](#1-contexte-et-objectif)
2. [État actuel (fév. 2026)](#2-état-actuel-fév-2026)
3. [Architecture globale](#3-architecture-globale)
4. [Pipeline complet d’un trade](#4-pipeline-complet-dun-trade)
5. [Installation et setup](#5-installation-et-setup)
6. [Commandes essentielles](#6-commandes-essentielles)
7. [Comprendre les résultats](#7-comprendre-les-résultats)
8. [Fichiers clés à connaître](#8-fichiers-clés-à-connaître)
9. [Décisions de design importantes](#9-décisions-de-design-importantes)
10. [Bugs connus et solutions](#10-bugs-connus-et-solutions)
11. [Documentation complémentaire](#11-documentation-complémentaire)
12. [Roadmap](#12-roadmap)

---

## 1. Contexte et objectif

**Arabesque** est un système de trading algorithmique conçu pour prop firms (FTMO, Goat Funded Trader).
Il trade sur ~20 instruments crypto (H1) en utilisant 3 stratégies complémentaires basées sur les Bandes de Bollinger.

**Objectif** : générer un edge positif (+10-20% mensuel) en respectant les règles des prop firms
(max drawdown 8% global, 3% daily, pas de trading interdit).

**Langage** : Python 3.10+  
**Broker** : cTrader (API Open API)  
**Source de données live** : cTrader (barres H1 en temps réel)  
**Source de données backtest/replay** : fichiers Parquet locaux (`~/dev/arabesque/data/parquet/`)  
**Gestionnaire de données** : projet [barres_au_sol](https://github.com/ashledombos/barres_au_sol) (dépôt séparé)

---

## 2. État actuel (fév. 2026)

### ✅ Validé et fonctionnel

| Composant | État | Notes |
|-----------|------|-------|
| `backtest.runner` (CLI backtest) | ✅ Fonctionnel | **Point d’entrée principal actuel** |
| `CombinedSignalGenerator` | ✅ Validé | 3 stratégies actives |
| `PositionManager` (trailing) | ✅ Validé | Même code live/backtest |
| Guards prop + exec | ✅ Actifs | DD, max positions, cooldown |
| `SignalFilter` | ✅ Actif | Matrice sub_type × catégorie |
| `scripts/update_and_compare.py` | ✅ Nouveau | Comparaison runs N-1→N |

### ⚠️ Non encore validé / en attente

| Composant | État | Notes |
|-----------|------|-------|
| `arabesque.live.engine` | ⚠️ Non testé | Remplace `runner.py` (déprécié) |
| `arabesque.live.runner` | ❌ Déprécié | Ne plus utiliser |
| Connexion cTrader réelle | ⚠️ Non testé | Credentials réels nécessaires |
| Paper trading continu | ⚠️ Non lancé | Utiliser `live.engine --mode dry_run` |

### 📊 Résultats de référence (backtest.runner, BTCUSD, déc. 2025)

```
Instrument     :  BTCUSD
Période        :  2025-12-01 → 2025-12-22 (in-sample)
Strategie      :  COMBINED, --no-filter
Trades         :  12
Win rate       :  33.3%
Expectancy     :  -0.429R   (période trop courte — insuffisant)
Max DD         :   3.2%
```

> ⚠️ 12 trades = INSUFFISANT (min 30 requis pour valider). À relancer sur 6+ mois.

---

## 3. Architecture globale

```
┌───────────────────────────────────────────────────────────────┐
│                  MODE BACKTEST                               │
│                                                              │
│  Fichiers Parquet ──► BacktestRunner ──► métriques + JSONL    │
│   (data/parquet/)   (backtest.runner)  (logs/)              │
│         ↑                   │                               │
│  barres_au_sol         SignalGenerator                       │
│  (dépôt séparé)   (Combined/MR/Trend)                       │
│                          │                                  │
│                     Guards + Sizing                          │
│                     PositionManager                          │
│                     (trailing 5 paliers)                     │
└───────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────┐
│               MODE PAPER / LIVE (futur)                     │
│                                                              │
│  Parquet/cTrader ─► ParquetClock ─► SignalGenerator           │
│  (réel ou replay)   (bougie/bougie)  (même code)            │
│                          │                                  │
│                     Orchestrator (live.engine)               │
│                     DryRunAdapter (paper)                    │
│                     cTraderAdapter (live)                    │
└───────────────────────────────────────────────────────────────┘
```

**Principe fondamental** : le `CombinedSignalGenerator` et le `PositionManager`
sont **strictement identiques** entre backtest, paper et live. Zéro divergence.

---

## 4. Pipeline complet d’un trade

```
Bougie H1 fermée (ex: XRPUSD, 2025-10-10 17:00)
          │
          ▼
CombinedSignalGenerator.generate_signals(df, "XRPUSD")
  ├── MeanReversionStrategy : RSI < 35 + close < BB_lower ?
  ├── TrendStrategy         : BB squeeze + breakout + ADX + CMF ?
  └── BreakoutStrategy      : cassure de range récent ?
          │
          ▼ (si signal détecté)
SignalFilter.is_allowed(sub_type, category)  ← matrice YAML
          │
          ▼ (bougie SUIVANTE)
Guards.check_all(signal, account)
  ├── Guard: cooldown (5 barres depuis dernier signal)
  ├── Guard: position déjà ouverte sur instrument
  ├── Guard: max_positions (10) atteint ?
  ├── Guard: DD daily > 3% ?
  └── Guard: slippage (open suivant vs close signal) > seuil ?
          │
          ▼ (fill = open bougie i+1)
PositionManager.open_position()
PositionManager.update_position() (barres suivantes)
  ├── Trailing SL 5 paliers (0.5R, 1R, 1.5R, 2R, 3R)
  ├── SL touché ? → exit_sl
  ├── TP fixé ? → exit_tp
  ├── Giveback > 50% MFE ? → exit_giveback
  ├── Deadfish (stagnation) ? → exit_deadfish
  └── Time-stop (>48 barres) ? → exit_time_stop
```

**Point critique anti-lookahead** :
- Signal généré sur bougie `i` (close confirmé)
- Exécution simulée au **open de bougie `i+1`**
- Update positions avec high/low/close de `i+1`

---

## 5. Installation et setup

### Prérequis

```bash
python --version  # 3.10+ requis
git --version
```

### Clone et install

```bash
cd ~/dev
git clone git@github.com:ashledombos/arabesque.git
cd arabesque

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
# Si pas de requirements.txt :
pip install pandas numpy pyarrow flask pyyaml yfinance requests
# Optionnel (broker live) :
pip install ctrader-open-api
```

### Données Parquet — via `barres_au_sol`

Les fichiers Parquet H1 sont dans `~/dev/arabesque/data/parquet/`.
Ils sont **produits par le projet [barres_au_sol](https://github.com/ashledombos/barres_au_sol)**.

Voir `HANDOVER.md` §5 (section originale) pour le détail du setup barres_au_sol.

---

## 6. Commandes essentielles

> ⚠️ `arabesque.live.runner` est **déprécié et cassé** (TD-005).
> Utiliser `arabesque.backtest.runner` pour les backtests
> et `arabesque.live.engine` pour le paper/live.

### Backtest (commande principale)

```bash
# Un instrument, période explicite
python -m arabesque.backtest.runner --strategy combined \
  --start 2025-01-01 --end 2026-01-01 \
  XRPUSD

# Plusieurs instruments
python -m arabesque.backtest.runner --strategy combined \
  --start 2025-01-01 --end 2026-01-01 \
  XRPUSD SOLUSD BNBUSD BTCUSD

# Sans filtre de signaux (exploration)
python -m arabesque.backtest.runner --strategy combined \
  --no-filter --start 2025-01-01 \
  XRPUSD
```

### Comparer avec le run précédent

```bash
# Après mise à jour des Parquets via barres_au_sol :
python scripts/update_and_compare.py \
  --strategy combined --start 2025-01-01 --export-trades
```

### Paper trading (dry-run)

```bash
# Rejouer une période précise
python -m arabesque.live.engine --mode dry_run --source parquet \
  --start 2025-10-01 --end 2025-12-31 --strategy combined

# Stream infini (Ctrl+C pour résumé)
python -m arabesque.live.engine --mode dry_run --source parquet \
  --strategy combined
```

### Live (quand credentials cTrader disponibles)

```bash
# 1. Configurer config/secrets.yaml avec les credentials
# 2. Lancer
export ARABESQUE_MODE=live
python -m arabesque.live.engine --mode live --strategy combined
```

---

## 7. Comprendre les résultats

### Métriques du rapport backtest

```
Trades         :  42          ← minimum 30 pour être statistiquement valide
Win Rate       :  57.1%
Avg win        :  +1.32R
Avg loss       :  -0.84R
Expectancy     :  +0.38R      ← la métrique la plus importante
Total R        :  +16.0R
Profit Factor  :  1.84
Max DD         :   2.1%       ← doit rester < 8% (règle FTMO)
Disqual Days   :   0          ← jours où DD daily > 3%
```

**Expectancy** : la métrique clé.
- `> +0.15R` = acceptable
- `> +0.30R` = bon
- `< 0` = ne pas trader cet instrument/stratégie

### Fichiers de logs générés

| Fichier | Contenu |
|---------|----------|
| `logs/backtest_runs.jsonl` | Métriques agrégées par run |
| `logs/trades/*.jsonl` | Trades individuels (via `update_and_compare.py --export-trades`) |
| `logs/comparisons/*.txt` | Rapports delta run N-1→N |
| `logs/dry_run_*.jsonl` | Trades paper trading |
| `logs/live_*.jsonl` | Trades live |

Voir `docs/WORKFLOW_BACKTEST.md` pour le détail des formats.

---

## 8. Fichiers clés à connaître

| Fichier | Rôle | À modifier si... |
|---------|------|-----------------|
| `arabesque/backtest/runner.py` | **Point d’entrée CLI backtest** | Ajout options CLI |
| `arabesque/live/engine.py` | **Point d’entrée CLI paper/live** | Problème de replay/live |
| `arabesque/backtest/signal_gen_combined.py` | Logique des 3 stratégies | Modifier les stratégies |
| `arabesque/backtest/signal_gen.py` | Stratégie mean-reversion | Modifier les conditions MR |
| `arabesque/backtest/signal_gen_trend.py` | Stratégie trend | Modifier les conditions Trend |
| `arabesque/position/manager.py` | Trailing 5 paliers + exits | Modifier le trailing |
| `arabesque/guards.py` | Guards prop (DD, max pos...) | Modifier les limites prop |
| `arabesque/core/signal_filter.py` | Lecture de signal_filters.yaml | Problème de filtrage |
| `config/signal_filters.yaml` | Matrice sub_type × catégorie | Ajouter/modifier des filtres |
| `config/settings.yaml` | Configuration broker, risque | Setup initial |
| `scripts/update_and_compare.py` | Comparaison runs N-1→N | Personnaliser le workflow |
| *(barres_au_sol)* `instruments.csv` | Mapping symboles FTMO ↔ sources | Ajouter/modifier des instruments |

### Fichiers dépréciés (à ne pas utiliser)

| Fichier | Remplacé par | Dette |
|---------|-------------|-------|
| `arabesque/live/runner.py` | `arabesque/live/engine.py` | TD-005 |
| `arabesque/live/bar_poller.py` | `arabesque/live/price_feed.py` | TD-006 |

---

## 9. Décisions de design importantes

### Séparation arabesque / barres_au_sol (intentionnelle)

`barres_au_sol` est un **data lake générique** réutilisable par n’importe quel système.
Il tourne 1×/jour en cron. Arabesque lit les Parquets qu’il produit.

### Anti-lookahead (critique)

Signal généré sur bougie `i` → exécuté au **open de bougie `i+1`**.
C’est la garantie fondamentale que le backtest ne triche pas.

### `--strategy combined` vs stratégies isolées

**Ne jamais utiliser `mean_reversion` seule** en production.
Elle est trop permissive (WR 25% sur crypto volatile sans filtre de tendance).
`combined` utilise les 3 stratégies + `SignalFilter` + cooldown.

### Gestion du risque (sizing)

Par défaut : `risk_pct = 0.5%` du capital par trade (`risk_cash = 500$` sur un compte 100k$).
Le volume est calculé automatiquement depuis le SL et le `contract_size` de l’instrument.

### Persistance des trades

Chaque run écrit dans `logs/backtest_runs.jsonl` (métriques) ET peut exporter
les trades individuels via `update_and_compare.py --export-trades`.
Ces fichiers permettent la **comparaison backtest ↔ paper ↔ live** sur la même période.

---

## 10. Bugs connus et solutions

### Erreur `AttributeError: 'Signal' object has no attribute 'tv_close'` — RÉSOLU

**Cause** : alias `tv_close`/`tv_open` hérités de TradingView non supprimés partout.  
**Fix** : TD-007 — commits `2aa9487` / `cbbb114` / `ac5936f` (2026-02-20).

### DD guards ne se déclenchaient jamais — RÉSOLU

**Cause** : division par `start_balance` au lieu de `daily_start_balance`.  
**Fix** : TD-001 — commit `0cb70ec` (2026-02-20).

### 0 signaux alors que les données sont chargées

**Causes possibles** :
1. `SignalFilter` bloque tous les signaux pour cet instrument/stratégie → tester avec `--no-filter`
2. Période trop courte (< 200 barres) → les indicateurs EMA200 ne sont pas initialisés
3. Fichier Parquet absent ou mal nommé → vérifier `data/parquet/<INSTRUMENT>_H1.parquet`

### XAUUSD a moins de barres (normal)

L’or ne trade pas le weekend et a des horaires restreints. Pas un bug.

---

## 11. Documentation complémentaire

| Document | Contenu |
|----------|---------|
| `docs/WORKFLOW_BACKTEST.md` | Cycle complet backtest → paper → live, formats des logs |
| `docs/INSTRUMENT_SELECTION.md` | Sélection instruments, matrice signal_filters, pipeline d’ajout |
| `docs/TECH_DEBT.md` | Dette technique connue, items résolus/ouverts |
| `HANDOFF.md` | Notes de passation courtes (supplémentaire) |

---

## 12. Roadmap

### Court terme (prioritaire)

- [ ] Lancer backtest sur 6+ mois pour valider (min 30 trades par instrument)
- [ ] Mettre en place le cron `barres_au_sol` + `update_and_compare.py` automatique
- [ ] Tester `arabesque.live.engine` en paper (dry-run) sur 2-4 semaines
- [ ] Valider TD-002 (`EXIT_TRAILING` jamais déclenché) avant le live

### Moyen terme

- [ ] Connexion cTrader avec credentials réels (compte démo d’abord)
- [ ] Dashboard web simple (Flask) pour positions en temps réel
- [ ] Alertes Telegram/email sur trades ouverts/fermés
- [ ] Tests unitaires sur `PositionManager` et `CombinedSignalGenerator`
- [ ] CI/CD (GitHub Actions) pour backtests automatiques

### Long terme (si edge validé en live)

- [ ] Intra-bar simulator (heuristique High/Low pour SL vs TP)
- [ ] Données M15 pour améliorer la précision
- [ ] Support TradeLocker (Goat Funded Trader)
- [ ] Gestion multi-compte

---

## Contact et contexte

Ce projet a été développé et maintenu par **Raphael** avec l’aide de Perplexity AI.
Historique complet dans les commits GitHub (`ashledombos/arabesque`).

```bash
git log --oneline
```
