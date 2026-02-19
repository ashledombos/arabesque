# Arabesque v2

Système de trading quantitatif pour prop firms (FTMO, Goat Funded Trader).
Trois stratégies complémentaires (Mean-Reversion, Trend, Breakout) avec PositionManager unifié live/backtest.

## Architecture

```
TradingView (Pine 1H)          Python Live/Backtest               Brokers
┌──────────────────┐  JSON    ┌───────────────────────────┐   ┌─────────┐
│ BB excess detect │ ───────→ │ Webhook Server (Flask)    │──→│ cTrader │
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
                              │ Live Replay (ParquetClock)│ │
                              │  ├ Parquet 1H data        │ │
                              │  ├ Bar-by-bar replay      │ │
                              │  ├ Signal generation      │ │
                              │  ├ MÊME PositionMgr ──────┼─┤
                              │  └ Anti-lookahead +1 bar  │ │
                              ├───────────────────────────┤ │
                              │ Backtest Runner           │ │
                              │  ├ Yahoo Finance 1H       │ │
                              │  ├ Signal gen (Combined)  │ │
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

## Trois stratégies complémentaires

### 1. Mean-Reversion (BB Excess)
Inspirée de BB_RPB_TSL (527 jours live, 48% CAGR, 90.8% WR Freqtrade).
- **Quand** : BB large, prix sort de la bande (excès)
- **Entrée** : close < BB lower + RSI < 35 + pas bear_trend (LONG)
- **TP** : retour au BB mid (mean reversion)
- **Logique** : le prix est trop loin de la moyenne → retour probable

### 2. Trend (BB Squeeze → Expansion)
- **Quand** : BB se contracte (squeeze) puis s'étend (expansion)
- **Entrée** : close casse BB upper/lower + ADX > 20 + EMA confirme + CMF confirme
- **TP** : pas de TP fixe, le trailing gère (ride the trend)
- **Logique** : un squeeze = accumulation d'énergie → breakout probable

### 3. Breakout (Range cassé)
- **Quand** : Prix consolide dans un range puis casse avec volume
- **Entrée** : close casse résistance/support + volume > moyenne + CMF > 0.1
- **TP** : hauteur du range projetée
- **Logique** : cassure confirmée = continuation probable

**CombinedSignalGenerator** : fusionne les 3 stratégies avec priorité intelligente (max 5 positions, pas de doublons).

Les trois partagent le même PositionManager (trailing, giveback, deadfish, time-stop).

## Système Live Replay (Dry-Run Avancé)

**Problème** : TradingView webhook = signaux live uniquement, pas de backtest avec le code Python de production.

**Solution** : `ParquetClock` rejoue des données historiques H1 bar-by-bar comme si c'était du live.

### Fonctionnement

```
┌─────────────────────────────────────────────────────────┐
│ ParquetClock Replay Engine                              │
├─────────────────────────────────────────────────────────┤
│ 1. Charger parquets H1 (19 instruments × 2000+ bars)   │
│ 2. Fusionner en timeline chronologique globale         │
│ 3. Pour chaque barre i:                                 │
│    ├─ Ajouter barre i au cache (300 bars glissantes)   │
│    ├─ Exécuter signaux pending (générés sur i-1)       │
│    │  └─ Entry price = OPEN de barre i (réaliste)      │
│    ├─ update_positions(high, low, close de i)          │
│    └─ Générer nouveaux signaux sur cache complet       │
│       └─ Enregistrer dans pending queue (exec à i+1)   │
│                                                          │
│ Anti-lookahead garanti:                                 │
│   Signal sur close[i] → Entry au open[i+1]             │
│   IDENTIQUE au backtest runner                          │
└─────────────────────────────────────────────────────────┘
```

### Différences avec le backtest classique

| Aspect | Backtest Runner | ParquetClock Replay |
|--------|----------------|---------------------|
| **Code signal** | `BacktestSignalGenerator` | `CombinedSignalGenerator` (LIVE) |
| **Code exits** | MÊME `PositionManager` | MÊME `PositionManager` |
| **Orchestrator** | Backtest loop interne | VRAI `Orchestrator` (prod) |
| **Guards** | Simplifiés | TOUS actifs (prop, exec, counterfactuals) |
| **Data source** | Yahoo Finance | Parquet locaux (cTrader history) |
| **Use case** | Validation edge (R, WR, DD) | Test intégration live (guards, broker, audit) |

### Commandes

```bash
# Replay 2 semaines (résumé auto en fin)
python -m arabesque.live.runner --mode dry_run --source parquet \
  --start 2025-10-01 --end 2025-10-15 --strategy combined

# Replay 3 mois
python -m arabesque.live.runner --mode dry_run --source parquet \
  --start 2025-10-01 --end 2026-01-01 --strategy combined

# Stream infini (Ctrl+C pour arrêter et voir résumé)
python -m arabesque.live.runner --mode dry_run --source parquet \
  --start 2025-10-01 --strategy combined
```

### Export JSONL

Chaque run génère `dry_run_YYYYMMDD_HHMMSS.jsonl` :
```json
{"type": "trade", "instrument": "NEOUSD", "side": "SHORT", "entry": 6.408, 
 "sl": 6.4647, "result_r": 4.649, "risk_cash": 96.54, "exit_reason": "exit_tp", 
 "bars_open": 1, "mfe_r": 6.732, "ts_entry": "...", "ts_exit": "..."}
...
{"type": "summary", "strategy": "CombinedSignalGenerator", 
 "period_start": "2025-10-01", "period_end": "2025-10-15", 
 "start_balance": 10000.0, "final_equity": 11971.47, "pnl_pct": 19.71, 
 "max_dd_pct": 3.76, "n_trades": 53, "win_rate": 56.6, 
 "expectancy_r": 0.3815, "total_r": 20.22}
```

Analysable avec `scripts/analyze.py` comme les logs live.

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
├── live/
│   ├── runner.py             # CLI live replay (--mode dry_run --source parquet)
│   ├── parquet_clock.py      # Moteur replay bar-by-bar
│   ├── bar_poller.py         # Live cTrader bar subscription (+ signal gen)
│   └── orchestrator.py       # Orchestrator live (alias webhook.orchestrator)
│
├── backtest/
│   ├── data.py               # Yahoo Finance + mapping 120+ tickers FTMO
│   ├── signal_gen.py         # Mean-reversion : BB excess
│   ├── signal_gen_trend.py   # Trend : squeeze → expansion → breakout
│   ├── signal_gen_breakout.py # Breakout : range cassé avec volume
│   ├── signal_gen_combined.py # Fusionne MR + Trend + Breakout
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
git clone https://github.com/ashledombos/arabesque.git
cd arabesque

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

# Optionnel (brokers live)
pip install ctrader-open-api    # FTMO
pip install tradelocker         # GFT
```

## Backtest

### CLI (recommandé)

```bash
# Un instrument, stratégie combined (défaut)
python scripts/backtest.py EURUSD

# Stratégie trend seule
python scripts/backtest.py EURUSD --strategy trend

# Mean-reversion seule
python scripts/backtest.py EURUSD --strategy mean_reversion

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

# Combined (défaut : MR + Trend + Breakout)
result_in, result_out = run_backtest("EURUSD", period="730d")

# Trend seule
run_backtest("XAUUSD", strategy="trend")

# Mean-reversion seule
run_backtest("BTC", strategy="mean_reversion")

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

## Live Replay (Dry-Run Avancé)

### Pourquoi ?

- Tester le **code de production** (Orchestrator, Guards, Broker adapters) sans risque
- Valider que les **guards** ne bloquent pas trop de signaux
- Vérifier que le **PositionManager** live se comporte comme en backtest
- Mesurer les **counterfactuals** (signaux rejetés qui auraient été gagnants/perdants)

### Prérequis : données Parquet

Le replay utilise des fichiers Parquet H1 (format Apache Arrow, rapide).

**Structure attendue** :
```
data/parquet/
├── AAVUSD.parquet
├── ALGUSD.parquet
├── BCHUSD.parquet
├── BNBUSD.parquet
├── ...
└── XTZUSD.parquet
```

**Colonnes requises** : `timestamp` (index), `Open`, `High`, `Low`, `Close`, `Volume`

**Comment générer** :
```python
import pandas as pd
from arabesque.backtest.data import load_ohlc

# Télécharger depuis Yahoo Finance
df = load_ohlc("EURUSD", start="2025-01-01", end="2026-02-01")

# Sauver en Parquet
df.to_parquet("data/parquet/EURUSD.parquet")
```

Ou récupérer depuis cTrader history (API `ProtoOAGetTrendbarsReq`).

### Commandes

```bash
# Replay 2 semaines
python -m arabesque.live.runner --mode dry_run --source parquet \
  --start 2025-10-01 --end 2025-10-15 --strategy combined

# Replay 3 mois
python -m arabesque.live.runner --mode dry_run --source parquet \
  --start 2025-10-01 --end 2026-01-01 --strategy combined

# Stream infini (Ctrl+C = résumé)
python -m arabesque.live.runner --mode dry_run --source parquet \
  --start 2025-10-01 --strategy combined
```

### Résumé

```
============================================================
DRY-RUN SUMMARY — CombinedSignalGenerator
2025-10-01 → 2025-10-15
============================================================
Balance start  :     10,000
Equity final   :  11,971.47  (+19.71%)
P&L cash       :  +1,971.47
Max DD         :        3.8%
============================================================
Trades         : 53
Win rate       : 56.6%
Avg win        : +1.317R
Avg loss       : -0.839R
Expectancy     : +0.3815R
Total R        : +20.22R
============================================================
P&L par instrument :
  GRTUSD      +5.38R  (4 trades)
  NEOUSD      +4.50R  (2 trades)
  XRPUSD      +4.08R  (2 trades)
  ...
============================================================
Estimation +10%   : ~3 jours (extrapolation linéaire)
Positions ouvertes : 2 non clôturées au 2025-10-15
============================================================
Export JSONL    : dry_run_20260219_151229.jsonl
============================================================
```

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
- **Breakout** : SL = ATR sous le range, TP = hauteur du range × 1.5. R:R typique 1.5-2.5.

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

**Anti-lookahead garanti** : Signal sur close[i] → Entry au open[i+1] (backtest ET replay).

## Changelog

### v2.2 — Live Replay System (19 Feb 2026)

**Ajout — Système Live Replay (ParquetClock)** : Rejoue des données historiques H1 bar-by-bar avec le code de production (Orchestrator, Guards, PositionManager). Usage : `python -m arabesque.live.runner --mode dry_run --source parquet --start 2025-10-01 --end 2025-10-15 --strategy combined`. Export JSONL analysable avec `scripts/analyze.py`.

**Fix — Anti-lookahead strict** : Signal généré sur barre i, exécuté au OPEN de barre i+1. Queue pending entre les barres. Tracker de signaux vus (par timestamp) pour éviter doublons. Paramètre `only_last_bar` pour distinguer live (BarPoller) et replay (ParquetClock).

**Fix — Générations répétées de signaux** : Le cache glissant (300 bars) générait les mêmes signaux à chaque itération en replay. Fix : `_seen_signals` set (timestamp) + `only_last_bar=False` en replay (tous signaux générés, queue filtre), `only_last_bar=True` en live (uniquement nouvelle barre).

**Amélioration — Documentation** : README détaillé (architecture, live replay, interprétation rapports). Guide HANDOVER.md pour passer la main (archi, bugs connus, roadmap).

### v2.1 — Bugfixes backtest (14 Feb 2026)

**Bug 1 fixé — slippage_too_high tuait 96% des signaux** : Le guard comparait `|open[bar+1] - close[bar]|` comme du slippage. Sur 1H, le gap entre close et open suivant = 1h de mouvement normal. Fix : le guard compare maintenant au open de la barre d'exécution.

**Bug 2 fixé — volume_zero tuait XAUUSD, BTC, SP500** : Le sizing utilisait 100K (lot FX) pour TOUT. Fix : contract sizes instrument-aware (gold=100oz, BTC=1 coin, indices=$1/point, etc.).

**Bug 3 fixé — R minuscules (2-5 pips)** : Le swing low SL sur 10 barres donnait des SL absurdement proches. Fix : minimum SL = 0.8×ATR enforcé. Résultat : R ≈ 12-20 pips sur EURUSD.

**Ajout — Module Trend** : Détection BB squeeze → expansion → breakout avec ADX + EMA + CMF. Complémentaire au mean-reversion. Usage : `--strategy trend` ou `--strategy combined`.

**Ajout — Module Breakout** : Détection range cassé avec volume. Complémentaire aux deux autres. Usage : `--strategy combined`.

**Ajout — Analyse des logs** : `scripts/analyze.py` pour parser les logs JSONL du paper trading. Performance report, calibration guards, daily summary, export CSV.

## License

Proprietary — Raphaël Dombos (@ashledombos)
