# Arabesque — Journal d’évolution

## 2026-02-18

### Patch guards (commit `994f228`)
- Ajout `RejectReason.OPEN_RISK_LIMIT` et `MAX_DAILY_TRADES` dans `models.py`
- `guards.py` : `PropConfig.max_positions` 3→10, `PropConfig.max_open_risk_pct=2.0`, `AccountState.open_risk_cash=0.0`
- Correction bug `_daily_trades()` qui remontait `MAX_POSITIONS` au lieu de `MAX_DAILY_TRADES`
- **Dette** : `open_risk_cash` non branché dans orchestrator → guard inactif jusqu’au patch suivant

### Patch orchestrator (commit `afb062d`)
- `orchestrator.py` : `open_risk_cash += risk_cash` à l’ouverture, `-= pos.risk_cash` à la fermeture
- `config.py` : ajout `max_open_risk_pct=2.0`, `max_positions` default 10
- `runner.py` (live) : lit `ARABESQUE_MAX_OPEN_RISK_PCT`, default `ARABESQUE_MAX_POSITIONS` 10
- Guard `OPEN_RISK_LIMIT` actif en live dès ce commit

## 2026-02-19

### Patch backtest + audit + docs (commit `8b9653f`)
- `backtest/runner.py` : parité avec orchestrator sur `open_risk_cash`, `max_open_risk_pct` passé à `PropConfig`
- `backtest/runner.py` : `BacktestConfig.max_positions` 3→10, ajout `max_open_risk_pct=2.0`
- `backtest/runner.py` : écriture JSONL synthèse par run dans `logs/backtest_runs.jsonl`
- `audit.py` : ajout `print_terminal_summary()` pour affichage lisible en fin de session
- `docs/plan.md` + `docs/journal.md` créés

### Fix SyntaxError orchestrator (commit `55d486e`)
- `orchestrator.py` : backslash dans f-string incompatible Python <3.12 → remplacé par constantes module-level `EMOJI_GREEN`/`EMOJI_RED` + escapes unicode

### Validation dry-run parquet (2025-10-01 → 2026-01-01)
- Runner tourne sans erreur sur 19 instruments, 41 347 événements
- Guards validés en conditions réelles : `open_risk_limit`, `duplicate_instrument`, `max_daily_trades` tous actifs et tracés dans les logs
- Trailing stop fonctionne (ex : NERUSD +0.57R, AAVUSD +0.72R, VECUSD +0.77R, ALGUSD +0.71R)

### Dettes techniques connues (ne pas traiter maintenant)
- **Corrélation inter-instruments** : le 10/10/2025, krach crypto simultané (RSI <20 sur 15 instruments en même direction sur la même bougie). Les guards limitent l’exposition mais les 5 positions qui passent SL toutes en 1 bar. À étudier : guard supplémentaire bloquant si >N instruments crypto signalent dans la même direction sur la même bougie. Non prioritaire tant que `max_open_risk_pct` contient le drawdown.
- Monte Carlo + Wilson IC pour les métriques backtest
- `runner.py` (live) ne vide pas `open_risk_cash` au daily reset — acceptable car les positions restent ouvertes
- `audit.py` ne persiste pas les stats entre redémarrages (in-memory) — acceptable pour le dry-run
- `print_terminal_summary()` non appelé en fin de session live — à brancher quand workflow complet validé
