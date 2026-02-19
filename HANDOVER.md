# Arabesque — Guide de passation (Handover)

> **À lire en premier si tu reprends le projet.**
> Ce guide explique l'architecture, l'état actuel, les décisions de design,
> et comment faire tourner le système de A à Z.

---

## Table des matières

1. [Contexte et objectif](#1-contexte-et-objectif)
2. [État actuel (fév. 2026)](#2-état-actuel-fév-2026)
3. [Architecture globale](#3-architecture-globale)
4. [Pipeline complet d'un trade](#4-pipeline-complet-dun-trade)
5. [Installation et setup](#5-installation-et-setup)
6. [Commandes essentielles](#6-commandes-essentielles)
7. [Comprendre les résultats](#7-comprendre-les-résultats)
8. [Fichiers clés à connaître](#8-fichiers-clés-à-connaître)
9. [Décisions de design importantes](#9-décisions-de-design-importantes)
10. [Bugs connus et solutions](#10-bugs-connus-et-solutions)
11. [Roadmap](#11-roadmap)

---

## 1. Contexte et objectif

**Arabesque** est un système de trading algorithmique conçu pour prop firms (FTMO, Goat Funded Trader).
Il trade sur 19 instruments crypto (H1) en utilisant 3 stratégies complémentaires basées sur les Bandes de Bollinger.

**Objectif** : générer un edge positif (+10-20% mensuel) en respectant les règles des prop firms
(max drawdown 8% global, 3% daily, pas de trading interdit).

**Langage** : Python 3.10+
**Broker** : cTrader (API Open API)
**Source de données live** : cTrader (barres H1 en temps réel)
**Source de données backtest/replay** : fichiers Parquet locaux (`~/dev/arabesque/data/parquet/`)

---

## 2. État actuel (fév. 2026)

### ✅ Validé et fonctionnel

| Composant | État | Notes |
|-----------|------|-------|
| `CombinedSignalGenerator` | ✅ Validé | 3 stratégies actives |
| `ParquetClock` (replay H1) | ✅ Validé | Anti-lookahead corrigé |
| `PositionManager` (trailing) | ✅ Validé | Même code live/backtest |
| Dry-run replay (`--strategy combined`) | ✅ Validé | +19.7% sur oct 2025 |
| Guards prop + exec | ✅ Actifs | DD, max positions, cooldown |

### 🔧 Non encore testé en live réel

- Connexion cTrader avec credentials réels
- Slippage live (différence signal → fill)
- Performance sur période étendue (3 mois+)

### 📊 Résultats de référence

**Dry-run `combined` — 2025-10-01 → 2025-10-15** :
```
Balance start  :  10,000
Equity final   :  11,971  (+19.71%)
Max DD         :     3.8%
Trades         :      53
Win rate       :   56.6%
Expectancy     : +0.38R
```

---

## 3. Architecture globale

```
┌─────────────────────────────────────────────────────────────┐
│                    MODE REPLAY (dry-run)                     │
│                                                              │
│  Fichiers Parquet ──► ParquetClock ──► SignalGenerator       │
│   (~/data/parquet/)    (replay H1)     (Combined/MR/Trend)   │
│                              │                               │
│                              ▼                               │
│                       Orchestrator                           │
│                    ┌────────────────┐                        │
│                    │ Guards         │ ← prop limits, DD      │
│                    │ Sizing         │ ← risk_cash (100$)     │
│                    │ PositionMgr    │ ← trailing 5 paliers   │
│                    │ DryRunAdapter  │ ← pas d'ordre réel     │
│                    └────────────────┘                        │
│                              │                               │
│                              ▼                               │
│                    Résumé + Export JSONL                     │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    MODE LIVE (futur)                         │
│                                                              │
│  cTrader API ──► BarPoller ──► SignalGenerator               │
│  (H1 live)        (H1 fermée)   (même code)                  │
│                       │                                      │
│                       ▼                                      │
│                  Orchestrator (même code)                    │
│                       │                                      │
│                       ▼                                      │
│                  cTraderAdapter ──► Ordre réel               │
└─────────────────────────────────────────────────────────────┘
```

**Principe fondamental** : le `CombinedSignalGenerator` et le `PositionManager`
sont **strictement identiques** entre replay et live. Zero divergence.

---

## 4. Pipeline complet d'un trade

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
_pending_signals["XRPUSD"].append(sig_data)   ← stocké, pas encore exécuté
          │
          ▼ (bougie SUIVANTE, H1+1 = 18:00)
orchestrator.handle_signal(sig_data)
  ├── Guard: position déjà ouverte sur XRPUSD ? → reject "duplicate"
  ├── Guard: max_positions (5) déjà atteint ? → reject "maxpositions"
  ├── Guard: DD daily > 2.5% ? → reject "dd_limit"
  ├── Guard: slippage (open 18:00 vs close 17:00) > seuil ? → reject
  ├── Sizing: risk_cash = 100$ → calcul du volume
  └── DryRunAdapter.place_order() → "fill" au open de 18:00
          │
          ▼ (barres suivantes)
orchestrator.update_positions(instrument, high, low, close)
  ├── Trailing SL 5 paliers (si MFE > 0.5R, 1R, 1.5R, 2R, 3R)
  ├── TP atteint ? → close
  ├── SL atteint ? → close
  ├── Giveback > 50% MFE ? → close
  ├── Deadfish (stagnation) ? → close
  └── Time-stop (>48 barres) ? → close
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

### Données Parquet

Les fichiers Parquet H1 sont dans `~/dev/arabesque/data/parquet/`.
Format : `{INSTRUMENT}_H1.parquet` (ex: `XRPUSD_H1.parquet`).

Pour mettre à jour les données depuis Yahoo Finance :
```bash
python -m arabesque.backtest.data --update-all
```

---

## 6. Commandes essentielles

### Dry-run replay (recommandé pour valider)

```bash
# Stratégie combinée (recommandée) — période de 2 semaines
python -m arabesque.live.runner --mode dry_run --source parquet \
  --start 2025-10-01 --end 2025-10-15 --strategy combined

# Stratégie mean-reversion seule (trop agressive seule, éviter)
python -m arabesque.live.runner --mode dry_run --source parquet \
  --start 2025-10-01 --end 2025-10-15 --strategy mean_reversion

# Période étendue (3 mois)
python -m arabesque.live.runner --mode dry_run --source parquet \
  --start 2025-10-01 --end 2026-01-01 --strategy combined

# Stream infini (Ctrl+C pour arrêter + afficher résumé)
python -m arabesque.live.runner --mode dry_run --source parquet \
  --strategy combined
```

### Backtest classique (Yahoo Finance)

```bash
python scripts/backtest.py --preset crypto_all --strategy combined
python scripts/backtest.py XRPUSD SOLUSD BNBUSD --strategy combined --period 365d
```

### Analyser les résultats JSONL

```bash
# Analyse le dernier fichier dry_run_*.jsonl
python scripts/analyze.py --all
python scripts/analyze.py --days 7
python scripts/analyze.py --csv trades.csv
```

### Live (quand credentials cTrader disponibles)

```bash
# 1. Configurer config/settings.yaml avec les credentials
# 2. Lancer
export ARABESQUE_MODE=live
python -m arabesque.live.runner --mode live --strategy combined
```

---

## 7. Comprendre les résultats

### Métriques du résumé dry-run

```
Balance start  :  10,000        ← capital initial (fictif)
Equity final   :  11,971        ← capital final
Max DD         :     3.8%       ← pire creux (< 8% FTMO = OK)
Trades         :      53        ← nombre de trades fermés
Win rate       :   56.6%        ← % de trades positifs
Avg win        :  +1.32R        ← gain moyen en multiples du risque
Avg loss       :  -0.84R        ← perte moyenne
Expectancy     :  +0.38R        ← profit moyen par trade (le plus important)
Total R        :  +20.2R        ← gain total en R
```

**Expectancy** : la métrique la plus importante.
- `> +0.15R` = bon
- `> +0.30R` = très bon
- `< 0` = ne pas trader

### Comprendre le fichier JSONL exporté

Chaque ligne est un dict JSON :

```json
{
  "type": "trade",
  "instrument": "XRPUSD",
  "side": "SHORT",
  "entry": 2.677,           // prix d'entrée (open de la bougie suivante)
  "sl": 2.722,              // stop loss initial
  "result_r": 2.083,        // résultat en R (positif = gain)
  "risk_cash": 100.0,       // montant risqué en dollars
  "exit_reason": "exit_tp", // raison de sortie
  "bars_open": 1,           // durée en barres H1
  "mfe_r": 17.2,            // maximum favorable excursion (jusqu'où le trade est allé)
  "ts_entry": "...",        // timestamp entrée
  "ts_exit": "..."          // timestamp sortie
}
```

**exit_reason** :
- `exit_sl` : stop loss touché (peut être en gain si le trailing a remonté le SL)
- `exit_tp` : take profit touché
- `exit_trailing` : trailing SL adaptatif touché
- `exit_giveback` : rendu trop de profit (>50% du MFE)
- `exit_deadfish` : trade stagnant fermé
- `exit_time_stop` : durée max (48 barres) atteinte

---

## 8. Fichiers clés à connaître

| Fichier | Rôle | À modifier si... |
|---------|------|-----------------|
| `arabesque/live/runner.py` | Point d'entrée CLI | Ajout de nouveaux modes/options |
| `arabesque/live/parquet_clock.py` | Replay bougie par bougie depuis Parquet | Problème de replay/lookahead |
| `arabesque/live/bar_poller.py` | Connexion live cTrader + logique signaux | Problème de connexion live |
| `arabesque/backtest/signal_gen_combined.py` | Logique des 3 stratégies | Modifier les stratégies |
| `arabesque/backtest/signal_gen.py` | Stratégie mean-reversion | Modifier les conditions MR |
| `arabesque/backtest/signal_gen_trend.py` | Stratégie trend | Modifier les conditions Trend |
| `arabesque/webhook/orchestrator.py` | Guards + sizing + position manager | Modifier les règles de gestion |
| `arabesque/position/manager.py` | Trailing 5 paliers + exits | Modifier le trailing |
| `arabesque/guards.py` | Guards prop (DD, max pos...) | Modifier les limites prop |
| `config/settings.yaml` | Configuration broker, risque | Setup initial |

---

## 9. Décisions de design importantes

### Anti-lookahead (critique)

**Problème** : générer un signal sur le close d'une bougie ET l'exécuter sur le même close = tricher.
Le prix n'est connu qu'à la fermeture de la bougie.

**Solution** :
1. Signal généré sur bougie `i` (après sa fermeture)
2. Stocké dans `_pending_signals`
3. Exécuté au **open de bougie `i+1`**

**Code** : `parquet_clock.py` → bloc `EXÉCUTION DES SIGNAUX PENDING`

---

### `only_last_bar` dans `_generate_signals_from_cache`

**Problème rencontré** :
- `only_last_bar=True` (réglage initial) → **0 signaux** en replay car le cache change à chaque itération
- Retirer le filtre → **55 trades** avec WR 25% car tous les signaux historiques sont renvoyés à chaque bougie

**Solution** :
- `only_last_bar=False` **+ tracker `_seen_signals`** (set de timestamps)
- Chaque signal n'est traité qu'une seule fois grâce au tracking par timestamp

**Code** : `parquet_clock.py` → `_seen_signals` + `_generate_signals_from_cache(only_last_bar=False)`

---

### `--strategy combined` vs `mean_reversion`

**Ne jamais utiliser `mean_reversion` seule** en production.
Elle est trop permissive (RSI < 35 + BB lower sans filtre tendance) et donne WR 25% en crypto volatile.

`combined` utilise les 3 stratégies avec :
- `max_positions=5`
- Filtre `duplicate_instrument` (une seule position par instrument)
- Confirmation multi-critères

---

### Gestion du risque (sizing)

Par défaut : `risk_cash = 100$` par trade (1% d'un compte 10k$).
Le volume en lots est calculé automatiquement depuis le SL en pips et le contract size.

**Si le compte réel est différent** : modifier `risk_cash` dans `config/settings.yaml`
ou passer `--risk-pct 0.01` (1% du capital courant) au runner.

---

## 10. Bugs connus et solutions

### Bug historique : SL trop serré → 0 signaux

**Symptôme** : `0 signals` en dry-run alors que les données sont chargées.
**Cause** : Filtre `only_last_bar=True` dans `_generate_signals_from_cache` + cache rechargé à chaque bougie.
**Fix** : `only_last_bar=False` + `_seen_signals` dans `parquet_clock.py`.
**Commit** : `d63fe0f`

---

### Bug historique : 55+ trades avec WR 25%

**Symptôme** : Le dry-run génère des dizaines de trades perdants, le compte fond progressivement.
**Cause** : Suppression du filtre `only_last_bar` sans tracking des doublons → tous les signaux historiques réémis à chaque itération.
**Fix** : même que ci-dessus (`_seen_signals`).

---

### Avertissement : XAUUSD a moins de barres (265 vs 361)

**Normal** : l'or ne trade pas le weekend et a des horaires restreints.
Pas un bug.

---

### Positions ouvertes à la fin de la période

Le dry-run peut terminer avec 1-2 positions ouvertes (`open_positions_at_end: 2`).
C'est normal : la période s'arrête avant que ces positions soient fermées.
La période est auto-étendue de +1 jour (`end_extended`) pour capturer les fills de fin de période.

---

## 11. Roadmap

### Court terme (immédiat)

- [ ] Tester avec credentials cTrader réels (compte démo d'abord)
- [ ] Valider le slippage live (log `ts_entry` vs timestamp réel du fill)
- [ ] Lancer dry-run sur 3 mois complets (oct 2025 → jan 2026)
- [ ] Affiner les guards si trop de rejections

### Moyen terme

- [ ] Dashboard web simple (Flask) pour voir les positions en temps réel
- [ ] Alertes Telegram/email sur trades ouverts/fermés
- [ ] Tests unitaires sur `PositionManager` et `CombinedSignalGenerator`
- [ ] CI/CD (GitHub Actions) pour lancer les backtests automatiquement

### Long terme (si edge validé en live)

- [ ] Intra-bar simulator (heuristique High/Low pour résoudre l'ambiguïté SL vs TP)
- [ ] Données M15 (Polygon.io) pour améliorer la précision des backtests
- [ ] Support TradeLocker (Goat Funded Trader)
- [ ] Gestion multi-compte

---

## Contact et contexte

Ce projet a été développé et maintenu par **Raphael** avec l'aide de Perplexity AI.
Historique complet des décisions dans les commits GitHub (`ashledombos/arabesque`).

Pour toute question sur une décision de design, lire les messages de commit :
```bash
git log --oneline
```

Les décisions importantes sont documentées dans les commits avec le préfixe `fix:` ou `feat:`.
