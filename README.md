# Arabesque

Système de trading algorithmique pour prop firms (FTMO, Goat Funded Trader).
Trois stratégies complémentaires sur Bandes de Bollinger, même `PositionManager` pour le live et le dry-run.

> **Résultat de référence (dry-run `combined`, oct 2025, 2 semaines)**
> +19.71% | Max DD 3.8% | WR 56.6% | Expectancy +0.38R | 53 trades

---

## Démarrage rapide

```bash
git clone git@github.com:ashledombos/arabesque.git && cd arabesque
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Dry-run replay depuis les fichiers Parquet locaux
python -m arabesque.live.runner --mode dry_run --source parquet \
  --start 2025-10-01 --end 2025-10-15 --strategy combined
```

Pour passer la main ou comprendre l'architecture en détail : lire [HANDOVER.md](HANDOVER.md).

---

## Architecture

```
 arabesque/
 ├── live/
 │   ├── runner.py            ← Point d'entrée CLI (--mode, --source, --strategy)
 │   ├── parquet_clock.py     ← Replay bougie-par-bougie depuis fichiers Parquet
 │   └── bar_poller.py        ← Stream live cTrader (production future)
 │
 ├── backtest/
 │   ├── signal_gen_combined.py  ← Stratégie recommandée (MR + Trend + Breakout)
 │   ├── signal_gen.py           ← Mean-Reversion seule (BB excess + RSI)
 │   ├── signal_gen_trend.py     ← Trend seule (BB squeeze → expansion)
 │   ├── data.py                 ← Chargement OHLC (Parquet ou Yahoo Finance)
 │   ├── metrics.py              ← Calcul expectancy, PF, DD, slippage
 │   └── runner.py               ← Backtest classique bar-by-bar
 │
 ├── webhook/
 │   ├── orchestrator.py      ← Guards + sizing + dispatch broker
 │   └── server.py            ← API Flask (webhooks TradingView)
 │
 ├── position/
 │   └── manager.py           ← Trailing 5 paliers, giveback, deadfish, time-stop
 │
 ├── broker/
 │   ├── adapters.py          ← Interface abstraite + DryRunAdapter
 │   ├── ctrader.py           ← cTrader Open API
 │   └── factory.py
 │
 ├── guards.py                ← Limites prop (DD, max positions, cooldown…)
 ├── models.py                ← Signal, Decision, Position, Counterfactual
 ├── config.py                ← ArabesqueConfig (YAML + env vars)
 └── audit.py                 ← Logger JSONL

scripts/
 ├── backtest.py              ← CLI backtest multi-instruments
 └── analyze.py               ← Parse les exports JSONL

docs/
 └── ARCHITECTURE.md          ← Architecture détaillée et décisions de design

data/
 └── parquet/                 ← Fichiers XRPUSD_H1.parquet, SOLUSD_H1.parquet…

config/
 └── settings.yaml            ← Configuration broker, risque, limites
```

**Principe fondamental** : le `PositionManager` et le `CombinedSignalGenerator`
sont **strictement identiques** entre dry-run et live. Zéro divergence.

---

## Trois modes d'exécution

| Mode | Source | Ordres réels | Usage |
|------|--------|-------------|-------|
| `--mode dry_run --source parquet` | Fichiers Parquet locaux | Non | **Validation de stratégie** (recommandé) |
| `--mode dry_run --source ctrader` | Stream cTrader démo | Non | Test de connexion broker |
| `--mode live --source ctrader` | Stream cTrader live | **Oui** | Production |

---

## Commandes

### Dry-run replay (Parquet)

```bash
# Stratégie combinée sur une période fixe
python -m arabesque.live.runner --mode dry_run --source parquet \
  --start 2025-10-01 --end 2025-10-15 --strategy combined

# Stream infini (Ctrl+C pour arrêter et afficher le résumé)
python -m arabesque.live.runner --mode dry_run --source parquet \
  --strategy combined

# Période étendue (3 mois)
python -m arabesque.live.runner --mode dry_run --source parquet \
  --start 2025-10-01 --end 2026-01-01 --strategy combined

# Vitesse ralentie pour observer (1s entre chaque bougie)
python -m arabesque.live.runner --mode dry_run --source parquet \
  --start 2025-10-01 --end 2025-10-15 --strategy combined --speed 1
```

### Backtest classique (Yahoo Finance)

```bash
python scripts/backtest.py --preset crypto_all --strategy combined
python scripts/backtest.py XRPUSD SOLUSD BNBUSD --period 365d --strategy combined
```

### Analyse des résultats JSONL

```bash
python scripts/analyze.py --all
python scripts/analyze.py --days 7
python scripts/analyze.py --csv trades.csv
```

---

## Stratégies

### `combined` ← recommandé

Combine les trois stratégies avec `max_positions=5` et filtre anti-doublon par instrument.

### `mean_reversion`

- **Signal** : `close < BB_lower` + `RSI < 35` + pas de trend baissier fort
- **Entrée** : open de la bougie suivante (anti-lookahead)
- **TP** : retour au BB mid
- **SL** : swing low des 7 dernières barres (min 0.8×ATR)
- ⚠️ Trop permissive seule sur crypto : WR 35% en backtests récents

### `trend`

- **Signal** : BB squeeze (width contracté) → expansion → cassure + ADX > 20 + CMF confirme
- **Entrée** : open bougie suivante
- **TP** : 2R indicatif (le trailing gère la vraie sortie)
- **SL** : 1.5×ATR

---

## Gestion des positions — Trailing 5 paliers

| MFE atteint | SL déplacé à | Effet |
|-------------|-------------|-------|
| +0.5R | 0.3R du high/low | Lock petit gain |
| +1.0R | 0.5R | Protège 0.5R |
| +1.5R | 0.8R | Protège 0.7R |
| +2.0R | 1.2R | Protège 0.8R |
| +3.0R | 1.5R | Laisse courir |

Le SL ne peut qu'avancer dans le sens favorable (jamais reculer).

---

## Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `ARABESQUE_BALANCE` | `10000` | Capital initial simulé |
| `ARABESQUE_RISK_PCT` | `1.0` | Risque par trade (% du capital) |
| `ARABESQUE_MAX_POSITIONS` | `10` | Positions simultanées max |
| `ARABESQUE_MAX_DAILY_DD` | `5.0` | Drawdown daily max (%) |
| `ARABESQUE_MAX_TOTAL_DD` | `10.0` | Drawdown total max (%) |
| `ARABESQUE_MAX_DAILY_TRADES` | `999` (dry) / `5` (live) | Trades/jour max |
| `CTRADER_HOST` | `demo.ctraderapi.com` | Hôte cTrader |
| `CTRADER_CLIENT_ID` | — | Credentials cTrader |
| `CTRADER_ACCESS_TOKEN` | — | Token OAuth cTrader |
| `CTRADER_ACCOUNT_ID` | — | ID du compte cTrader |
| `TELEGRAM_TOKEN` | — | Notifications Telegram (optionnel) |
| `NTFY_TOPIC` | — | Notifications ntfy.sh (optionnel) |

---

## Lire le résumé dry-run

```
Balance start  :  10,000        ← capital initial
Equity final   :  11,971        ← capital final
Max DD         :     3.8%       ← pire creux (FTMO limite à 8%)
Trades         :      53        ← trades fermés
Win rate       :   56.6%        ← % trades positifs
Expectancy     :  +0.38R        ← profit moyen par trade ← métrique clé
Total R        :  +20.2R        ← gain total en R
```

**Expectancy > +0.15R = bon | > +0.30R = très bon | < 0 = ne pas trader.**

Le fichier `dry_run_YYYYMMDD_HHMMSS.jsonl` exporté contient un enregistrement
par trade et une ligne `summary`. Utiliser `scripts/analyze.py` pour l'analyser.

---

## Installation complète

```bash
# Dépendances obligatoires
pip install pandas numpy pyarrow flask pyyaml yfinance requests

# Broker live (optionnel)
pip install ctrader-open-api

# Notifications (optionnel)
pip install python-telegram-bot
```

---

## Changelog

### v2.2 — Live Runner + Parquet replay (fév. 2026)

- **Nouveau** : `arabesque.live.runner` — point d'entrée unifié pour dry-run et live
- **Nouveau** : `ParquetClock` — replay H1 depuis fichiers Parquet locaux, anti-lookahead strict
- **Fix** : `only_last_bar=False` + `_seen_signals` dans `ParquetClock` (évite doublons de signaux)
- **Fix** : Extension automatique de la période `+1 jour` pour capturer les fills de fin de période
- **Ajout** : Export JSONL automatique après chaque dry-run
- **Ajout** : Support notifications Telegram + ntfy

### v2.1 — Bugfixes backtest (14 fév. 2026)

- Fix slippage guard (comparaison open vs close inter-barres)
- Fix volume_zero sur XAUUSD, BTC, indices
- Fix SL trop serré (minimum 0.8×ATR)
- Ajout module Trend (BB squeeze → expansion → breakout)
- Ajout `scripts/analyze.py`
