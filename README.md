# Arabesque

*Personal algorithmic trading experiment for proprietary trading firm challenges.*

*Expérimentation personnelle de trading algorithmique pour challenges de prop firms.*

Feel free to explore, fork, or adapt any part of this project for your own use.
Libre à vous d'explorer, forker ou adapter ce projet à vos besoins.

---

## What is this? / C'est quoi ?

**EN** — Arabesque is a multi-strategy, multi-broker trading system designed around prop firm constraints (daily drawdown caps, consistency requirements, strict risk management). It runs live on cTrader (FTMO) and TradeLocker (GFT), with a single price feed dispatching orders to multiple accounts simultaneously.

**FR** — Arabesque est un système de trading multi-stratégie et multi-broker conçu autour des contraintes des prop firms (plafonds de drawdown journalier, exigences de consistance, gestion stricte du risque). Il tourne en live sur cTrader (FTMO) et TradeLocker (GFT), avec un seul flux de prix qui dispatche les ordres vers plusieurs comptes simultanément.

---

## Guiding principle / Principe directeur

```
Small, frequent, consistent gains.
Few losses. Small when they happen.
High win rate: target >= 70%, ideal >= 85%.
Smooth, predictable equity curve.
```

Prop firms evaluate *consistency*, not raw performance. A 75% win rate with small regular gains passes a challenge. A jagged equity curve with occasional +10R trades does not, even if total P&L is higher.

---

## Strategies / Stratégies

Each strategy is named after a graceful dance move (ballet, rhythmic gymnastics).

| Strategy | Logic | Timeframe | Status | WF |
|---|---|---|---|---|
| **Extension** | BB squeeze → trend breakout | H1 / H4 | **Live** | 20mo, 1998 trades, WR 75%, Exp +0.130R |
| **Glissade** | RSI divergence in trend | H1 | **Live** | 3/3 PASS, WR 83%, Exp +0.147R |
| **Fouetté** | Opening Range Breakout | M1 | Validated, not deployed | 4/4 PASS (frequency too low) |
| **Cabriole** | Donchian channel breakout | H4 | Validated, backup | 6/6 PASS (73-95% overlap with Extension) |
| **Révérence** | NR7 contraction → expansion | H4 | WF partial | DOGEUSD PASS, edge thin |
| **Renversé** | Liquidity sweep + FVG retrace | H1 | Tested, abandoned | WR 73%, Exp +0.006R = breakeven |
| **Pas de Deux** | Pairs trading (cointegration) | — | Concept only | Incompatible with guiding principle |

Full documentation for each strategy: [`arabesque/strategies/*/STRATEGY.md`](arabesque/strategies/)

---

## Architecture

```
arabesque/
├── core/              ← Immutable kernel (models, guards, audit)
├── modules/           ← Reusable blocks (indicators, position_manager)
├── strategies/
│   ├── extension/     ← BB trend-following H1/H4 (live)
│   ├── glissade/      ← RSI divergence H1 (live)
│   ├── fouette/       ← ORB M1 (validated, not deployed)
│   ├── cabriole/      ← Donchian breakout H4 (backup)
│   ├── reverence/     ← NR7 expansion H4 (testing)
│   ├── renverse/      ← Sweep + FVG H1 (abandoned)
│   └── pas_de_deux/   ← Pairs trading (concept)
├── execution/         ← Engines (backtest, dryrun, live, bar_aggregator)
├── broker/            ← Adapters (cTrader, TradeLocker, DryRun)
├── data/              ← Parquet store + fetch (Dukascopy, CCXT/Binance)
└── analysis/          ← Metrics, stats, screening pipeline
```

### Multi-broker dispatch

```
cTrader ticks → BarAggregator → SignalGenerator → OrderDispatcher
                                                      ├── cTrader (FTMO)
                                                      └── TradeLocker (GFT)
```

One price feed, multiple accounts. Each account has its own risk sizing adapted to the prop firm's rules.

---

## Quick start / Démarrage rapide

```bash
git clone git@github.com:ashledombos/arabesque.git && cd arabesque
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Backtest Extension
python -m arabesque run --strategy extension --mode backtest XAUUSD BTCUSD

# Walk-forward validation
python -m arabesque walkforward --strategy extension --universe crypto

# Backtest Glissade
python -m arabesque run --strategy glissade --mode backtest XAUUSD BTCUSD

# Live engine (requires config/secrets.yaml with broker credentials)
nohup .venv/bin/python -m arabesque.live.engine > /tmp/arabesque_live.log 2>&1 &

# Fetch historical data
python -m arabesque.data.fetch --start 2024-01-01 --end 2026-03-23 --derive 1h 5m
```

---

## Configuration

| File | Purpose |
|---|---|
| `config/settings.yaml` | General settings, broker list, strategy assignments |
| `config/accounts.yaml` | Per-account risk parameters, prop firm profiles |
| `config/instruments.yaml` | Instrument definitions, broker symbol mappings |
| `config/secrets.yaml` | Credentials (gitignored) — OAuth tokens, API keys |

---

## Documentation

| Document | Content |
|---|---|
| [`docs/STATUS.md`](docs/STATUS.md) | Current live state — what's running, on which account |
| [`HANDOFF.md`](HANDOFF.md) | Development handoff — resume context for next session |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Technical decisions and rationale |
| [`docs/HYGIENE.md`](docs/HYGIENE.md) | Code conventions |
| [`arabesque/strategies/*/STRATEGY.md`](arabesque/strategies/) | Per-strategy documentation |

---

## Validation workflow / Processus de validation

```
Backtest IS (60%) → Exp > 0, WR > 50%
    ↓
Backtest OOS (40%) → consistent with IS
    ↓
Wilson CI99 > 0 → statistically significant
    ↓
Dry-run parquet → 3 months minimum
    ↓
Shadow filter live → 2-4 weeks
    ↓
Live
```

No strategy goes live without passing all stages.

---

## Disclaimer / Avertissement

**EN** — This is a personal experiment. Trading involves risk of loss. Past backtest results do not guarantee future performance. This software is provided as-is, with no warranty. Use at your own risk.

**FR** — Ceci est une expérimentation personnelle. Le trading comporte des risques de perte. Les résultats de backtest passés ne garantissent pas les performances futures. Ce logiciel est fourni tel quel, sans garantie. Utilisation à vos risques et périls.
