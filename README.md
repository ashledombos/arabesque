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

## Lire un rapport de backtest

Exemple de rapport :
```
  ARABESQUE BACKTEST — EURUSD (in_sample)
  Bars       : 12077
  Signals    : 891 generated, 750 rejected

  TRADES     : 141  (min 30 = OK)
  Win rate   : 55%
  Avg win    : +1.22R
  Avg loss   : -0.87R

  EXPECTANCY : +0.172R  (+$86 cash)
  Total R    : +24.2R  (+$12,100 cash)

  PROFIT FACTOR : 1.42

  MAX DD     : 4.8%  ($4,800 cash)

  TIMING     :
    Avg bars   : 8  (wins: 6, losses: 11)

  EXITS      :
    exit_sl            : 89
    exit_tp            :  4
    exit_giveback      : 22
    exit_deadfish      : 16
    exit_time_stop     : 10

  REJECTIONS :
    slippage_too_high  : 420
    cooldown           : 230
    bb_squeeze         :  80
    duplicate          :  20
```

### Métriques clés — ce que tu dois regarder

**Expectancy (R)** : Le plus important. C'est le profit moyen par trade en multiples du risque.
+0.15R = bon, +0.30R = très bon, -0.05R = breakeven avec frais. Si c'est négatif, ne pas trader.

**Profit Factor** : Gains bruts / pertes brutes. PF > 1.2 = tradable. PF > 1.5 = bon.

**Win Rate** : Avec le trailing adaptatif on vise 45-55%. Pas besoin de 70%+ car les wins sont plus gros que les losses (avg win > avg loss en R).

**Max DD** : Drawdown max en % du capital initial. FTMO limite à 8% total et 3% daily. Si le backtest dépasse, les guards l'auraient coupé en live.

**Trades** : Min 30 pour que les stats soient significatives. Idéalement 100+.

### Exits — comment les trades se terminent

- **exit_sl** : SL touché. Inclut DEUX cas : SL original (perte = -1R) ET trailing SL (profit, le SL a été remonté). C'est l'exit la plus fréquente.
- **exit_tp** : TP touché (BB mid pour mean-reversion, 2R pour trend).
- **exit_giveback** : Le prix a rendu trop de profit non réalisé (>50% du MFE).
- **exit_deadfish** : Le trade stagne (flat depuis trop de barres, range < 0.5R).
- **exit_time_stop** : Max barres atteint (48h pour mean-reversion).
- **exit_trailing** : Le trailing SL adaptatif a été touché (rarement distinct de exit_sl).

### Le trailing adaptatif (5 paliers)

**Oui, il est actif dans le backtest.** C'est le MÊME PositionManager que le live.

Quand le trade va dans le bon sens, le SL se resserre automatiquement :
- MFE atteint +0.5R → SL remonté à 0.3R du high (lock petit profit)
- MFE atteint +1.0R → SL remonté à 0.5R du high (protéger 0.5R)
- MFE atteint +1.5R → SL remonté à 0.8R du high
- MFE atteint +2.0R → SL remonté à 1.2R du high
- MFE atteint +3.0R → SL remonté à 1.5R du high (laisser courir)

C'est pour ça que beaucoup de "exit_sl" sont en fait des wins : le SL a été remonté au-dessus de l'entrée par le trailing.

### Rejections — pourquoi des signaux sont ignorés

- **slippage_too_high** : Le prix à l'exécution (open barre suivante) a trop bougé par rapport au signal. Normal en backtest sur données 1H.
- **cooldown** : Un trade sur le même instrument était pris il y a moins de 5 barres.
- **bb_squeeze** : BB width trop étroit (<0.3%), pas assez de volatilité.
- **duplicate** : Un trade est déjà ouvert sur cet instrument.
- **volume_zero** : Le sizing donne 0 lots (R trop petit ou contract size inadapté).

### Comparaison in-sample / out-of-sample

Le split par défaut est 70/30. L'important : les métriques out-of-sample ne doivent pas être dramatiquement pires que l'in-sample. Un léger recul est normal. Si l'expectancy passe de +0.3R à -0.1R, c'est de l'overfitting.

### R:R (Risk/Reward Ratio)

**Le R:R n'est PAS fixe à 1.** Il dépend de la stratégie :

- **Mean-reversion** : SL = 0.8×ATR minimum (swing low sinon), TP = BB mid. Le R:R dépend de la distance au BB mid. Typiquement 0.5-2.0 selon la largeur des BB.
- **Trend** : SL = 1.5×ATR, TP indicatif = 2R. Mais le trailing gère la vraie sortie, donc les gagnants peuvent courir bien au-delà de 2R.

### Instruments supportés

120+ instruments FTMO mappés pour Yahoo Finance. Si un instrument donne 0 trades, vérifier :
1. Yahoo fournit-il le volume ? (gold, indices via futures = pas toujours)
2. Le contract size est-il mappé ? (vérifier `_contract_size()` dans runner.py)

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

## Changelog

### v2.1 — Bugfixes backtest (14 Feb 2026)

**Bug 1 fixé — slippage_too_high tuait 96% des signaux** : Le guard comparait `|open[bar+1] - close[bar]|` comme du slippage. Sur 1H, le gap entre close et open suivant = 1h de mouvement normal. Fix : le guard compare maintenant au open de la barre d'exécution.

**Bug 2 fixé — volume_zero tuait XAUUSD, BTC, SP500** : Le sizing utilisait 100K (lot FX) pour TOUT. Fix : contract sizes instrument-aware (gold=100oz, BTC=1 coin, indices=$1/point, etc.).

**Bug 3 fixé — R minuscules (2-5 pips)** : Le swing low SL sur 10 barres donnait des SL absurdement proches. Fix : minimum SL = 0.8×ATR enforcé. Résultat : R ≈ 12-20 pips sur EURUSD.

**Ajout — Module Trend** : Détection BB squeeze → expansion → breakout avec ADX + EMA + CMF. Complémentaire au mean-reversion. Usage : `--strategy trend` ou `--strategy combined`.

**Ajout — Analyse des logs** : `scripts/analyze.py` pour parser les logs JSONL du paper trading. Performance report, calibration guards, daily summary, export CSV.
