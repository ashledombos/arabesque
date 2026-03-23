# Arabesque

*Personal algorithmic trading experiment for proprietary trading firm challenges.*

*Expérimentation personnelle de trading algorithmique pour challenges de prop firms.*

Feel free to explore, fork, or adapt any part of this project for your own use.
Libre à vous d'explorer, forker ou adapter ce projet à vos besoins.

---

**EN** — A modular backtesting and live trading framework built around prop firm constraints (drawdown caps, consistency requirements). Started as a personal challenge to see if a systematic approach could pass FTMO-style evaluations. Each strategy is named after a ballet move.

**FR** — Un framework modulaire de backtest et de trading live, construit autour des contraintes des prop firms (plafonds de drawdown, consistance). Parti d'un challenge personnel pour voir si une approche systématique peut passer les évaluations de type FTMO. Chaque stratégie porte le nom d'un mouvement de ballet.

---

## Guiding principle / Principe directeur

```
Small, frequent, consistent gains.
High win rate. Smooth equity curve.
```

---

## Strategies / Stratégies

| Strategy | Logic | Timeframe | Status |
|---|---|---|---|
| **Extension** | BB squeeze → trend breakout | H1 / H4 | Live |
| **Glissade** | RSI divergence in trend | H1 | Live |
| **Fouetté** | Opening Range Breakout | M1 | Validated, not deployed |
| **Cabriole** | Donchian breakout | H4 | Backup |
| **Révérence** | NR7 contraction → expansion | H4 | Testing |
| **Renversé** | Liquidity sweep + FVG retrace | H1 | Abandoned (edge too thin) |

---

## Quick start

```bash
git clone git@github.com:ashledombos/arabesque.git && cd arabesque
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Backtest
python -m arabesque run --strategy extension --mode backtest XAUUSD BTCUSD
python -m arabesque walkforward --strategy extension --universe crypto

# Live engine
nohup .venv/bin/python -m arabesque.live.engine > /tmp/arabesque_live.log 2>&1 &
```

Requires `config/secrets.yaml` with broker credentials (not versioned).

---

## Disclaimer

Trading involves risk of loss. Past backtest results do not guarantee future performance. Personal experiment, no warranty.

*Le trading comporte des risques de perte. Les résultats passés ne garantissent pas les performances futures. Expérimentation personnelle, sans garantie.*
