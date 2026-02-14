# Arabesque v2

Système de trading quantitatif pour prop firms (FTMO, Goat Funded Trader).
Deux stratégies complémentaires sur Bollinger Bands, même PositionManager pour le live et le backtest.

## Architecture

```
TradingView (Pine 1H)          Python                          Brokers
┌──────────────────┐  JSON    ┌───────────────────────────┐   ┌─────────┐
│ BB excess detect │ ───────→ │ Webhook Server            │──→│ cTrader │
│ Regime HTF (4H)  │ webhook  │   └→ Orchestrator         │   │ (FTMO)  │
│ RSI / CMF / ATR  │          │      ├ Guards (prop+exec) │   ├─────────┤
│ Anti-lookahead   │          │      ├ Broker adapter      │   │TradeLkr │
└──────────────────┘          │      ├ PositionManager ◄───┼─┐│ (GFT)   │
                              │      │  ├ Trailing 5 tiers │ │└─────────┘
                              │      │  ├ Giveback guard   │ │
                              │      │  ├ Deadfish exit    │ │ MÊME code
                              │      │  └ Time-stop        │ │
                              │      └ Audit + Contrefact. │ │
                              ├───────────────────────────┤ │
                              │ Backtest Runner           │ │
                              │  ├ Yahoo Finance 1H       │ │
                              │  ├ Signal gen (MR/Trend)  │ │
                              │  ├ MÊME PositionMgr ──────┼─┘
                              │  └ Métriques + rapport    │
                              ├───────────────────────────┤
                              │ Analysis                  │
                              │  ├ Performance report     │
                              │  ├ Guard calibration      │
                              │  ├ Daily summary          │
                              │  └ Export CSV              │
                              └───────────────────────────┘
```

## Deux stratégies complémentaires

### Mean-Reversion (BB Excess)
Inspirée de BB_RPB_TSL (527 jours live, 48% CAGR, 90.8% WR Freqtrade).
- **Quand** : BB large, prix sort de la bande (excès)
- **Entrée** : close < BB lower + RSI < 35 + pas bear_trend (LONG)
- **TP** : retour au BB mid (mean reversion)
- **Logique** : le prix est trop loin de la moyenne → retour probable

### Trend (BB Squeeze → Expansion)
- **Quand** : BB se contracte (squeeze) puis s'étend (expansion)
- **Entrée** : close casse BB upper/lower + ADX > 20 + EMA confirme + CMF confirme
- **TP** : pas de TP fixe, le trailing gère (ride the trend)
- **Logique** : un squeeze = accumulation d'énergie → breakout probable

Les deux partagent le même PositionManager (trailing, giveback, deadfish, time-stop).

## Structure des fichiers

```
arabesque/
├── models.py                 # Signal, Decision, Position, Counterfactual
├── guards.py                 # Guards prop + exec, sizing, AccountState
├── audit.py                  # JSONL logger
├── config.py                 # Chargement settings YAML + env vars
├── screener.py               # Pass 1 : MFE/MAE brut
│
├── position/
│   └── manager.py            # PositionManager — MÊME code live + backtest
│
├── broker/
│   ├── adapters.py           # Interface abstraite + DryRunAdapter
│   ├── ctrader.py            # cTrader Open API (FTMO)
│   ├── tradelocker.py        # TradeLocker REST (GFT)
│   └── factory.py            # Crée le bon adapter depuis config
│
├── webhook/
│   ├── server.py             # Flask : /webhook, /update, /status
│   └── orchestrator.py       # Signal → Guards → Broker → Manager → Audit
│
├── backtest/
│   ├── data.py               # Yahoo Finance + mapping 120+ tickers FTMO
│   ├── signal_gen.py         # Mean-reversion : BB excess
│   ├── signal_gen_trend.py   # Trend : squeeze → expansion → breakout
│   ├── signal_gen_combined.py # Fusionne MR + Trend
│   ├── metrics.py            # Expectancy, PF, DD, prop, slippage
│   └── runner.py             # Itération bar-by-bar
│
└── analysis/
    └── analyzer.py           # Parse audit JSONL, rapports, calibration guards

scripts/
├── backtest.py               # CLI backtest (presets, strategy, multi-instrument)
└── analyze.py                # CLI analyse des logs paper/live

pine/
└── arabesque_signal.pine     # Signal TradingView (anti-lookahead)

config/
└── settings.yaml             # Configuration (brokers, prop, webhook)

systemd/
└── arabesque-webhook.service # Service systemd
```

## Installation

```bash
cd /home/raphael/dev
unzip arabesque_v2_complete.zip -d arabesque
cd arabesque

python3 -m venv venv
source venv/bin/activate

pip install flask pyyaml yfinance numpy pandas requests

# Optionnel (brokers)
pip install ctrader-open-api    # FTMO
pip install tradelocker         # GFT
```

## Backtest

### CLI (recommandé)

```bash
# Un instrument, stratégie mean-reversion (défaut)
python scripts/backtest.py EURUSD

# Stratégie trend
python scripts/backtest.py EURUSD --strategy trend

# Les deux stratégies combinées
python scripts/backtest.py EURUSD --strategy combined

# Plusieurs instruments
python scripts/backtest.py EURUSD GBPUSD XAUUSD BTC --period 730d

# Presets
python scripts/backtest.py --preset fx_majors
python scripts/backtest.py --preset crypto_top --strategy combined
python scripts/backtest.py --preset metals
python scripts/backtest.py --preset indices
python scripts/backtest.py --preset all
```

Presets disponibles : `fx_majors` (7), `fx_crosses` (21), `fx_exotics` (10),
`fx_all` (38), `crypto_top` (9), `crypto_all` (30), `metals` (5), `energy` (3),
`indices` (11), `commodities` (7), `stocks_us` (15), `stocks_eu` (8),
`stocks_all` (23), `all` (27 mix).

### Python

```python
from arabesque.backtest import run_backtest, run_multi_instrument

# Mean-reversion (défaut)
result_in, result_out = run_backtest("EURUSD", period="730d")

# Trend
run_backtest("XAUUSD", strategy="trend")

# Combined
run_backtest("BTC", strategy="combined")

# Multi-instrument
run_multi_instrument(
    ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTC"],
    strategy="combined",
)
```

### Ce que le rapport inclut

- **Expectancy** (R et cash) — un trade typique rapporte combien
- **Profit Factor** — gains bruts / pertes brutes
- **Max Drawdown** — pire creux de l'equity curve
- **Jours disqualifiants** — combien de jours cassent les limites prop
- **Slippage sensitivity** — l'edge survit-il si le slippage double/triple
- **Exits breakdown** — SL, TP, trailing, giveback, deadfish, time-stop
- **Comparaison in-sample / out-of-sample** — overfitting check

## Paper Trading

```bash
# Générer la config
python -c "from arabesque.config import generate_default_config; generate_default_config()"

# Éditer les settings
nano config/settings.yaml

# Lancer en mode dry-run
export ARABESQUE_MODE=dry_run
python -m arabesque.webhook.server

# Vérifier
curl http://localhost:5000/health
curl http://localhost:5000/status
curl http://localhost:5000/positions
```

Configurer TradingView :
1. Graphique 1H sur l'instrument
2. Ajouter "Arabesque Signal v1"
3. Alerte → "Any alert() function call" → webhook URL

### Analyser les résultats

```bash
# Après quelques jours de paper trading
python scripts/analyze.py                    # Rapport de performance
python scripts/analyze.py --guards           # Calibration des guards
python scripts/analyze.py --daily            # Résumé jour par jour
python scripts/analyze.py --timeline         # Timeline des événements
python scripts/analyze.py --csv trades.csv   # Export pour analyse externe
python scripts/analyze.py --all              # Tout
python scripts/analyze.py --days 7           # Derniers 7 jours
```

## Live Trading

```bash
# 1. Les backtests montrent un edge positif
# 2. Le paper trading confirme sur 30+ trades réels
# 3. Passer en live

# Éditer config/settings.yaml :
#   mode: live
#   brokers:
#     - type: ctrader       # FTMO
#       client_id: "..."
#       ...

export ARABESQUE_MODE=live
python -m arabesque.webhook.server

# Ou via systemd
cp systemd/arabesque-webhook.service ~/.config/systemd/user/
systemctl --user enable --now arabesque-webhook
```

## API Webhook

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/webhook` | POST | Signal TradingView (JSON) |
| `/update` | POST | Mise à jour OHLC pour positions ouvertes |
| `/status` | GET | État du système (compte, positions, audit) |
| `/positions` | GET | Liste positions ouvertes et fermées récentes |
| `/health` | GET | Healthcheck |

## Trailing paliers

| MFE atteint | SL trail depuis le high | Effet |
|-------------|-------------------------|-------|
| +0.5R | 0.3R | Lock petit profit |
| +1.0R | 0.5R | Protéger 0.5R |
| +1.5R | 0.8R | Protéger 0.7R |
| +2.0R | 1.2R | Protéger 0.8R |
| +3.0R | 1.5R | Laisser courir |

## Instruments supportés

L'univers complet FTMO est mappé pour Yahoo Finance (120+ tickers) :
42 paires FX, 30 cryptos, 5 métaux, 4 énergies, 13 indices,
7 matières premières agricoles, 23 actions US/EU.

## Principes de design

**Zéro divergence live/backtest** : Le `PositionManager` est le même code.
Si le backtest montre +0.15R d'expectancy, le live verra la même chose.

**Tout en R** : Les résultats sont en multiples du risque initial.
Un trade à +2R = 2× le risque. Indépendant de l'instrument et du sizing.

**Conservateur** : Si SL et TP touchés dans la même bougie → SL (pire cas).

**SL ne descend jamais** (LONG) / ne monte jamais (SHORT).

**Guards toujours actifs** : Spread, slippage, DD prop, positions max, duplicates.

**Counterfactuels** : chaque signal rejeté est suivi pour voir ce qui serait
arrivé → calibrer les guards (trop stricts ? pas assez ?).
