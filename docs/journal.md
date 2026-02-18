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

### Patch backtest + audit + docs (commit courant)
- `backtest/runner.py` : parité avec orchestrator sur `open_risk_cash`, `max_open_risk_pct` passé à `PropConfig`
- `backtest/runner.py` : `BacktestConfig.max_positions` 3→10, ajout `max_open_risk_pct=2.0`
- `backtest/runner.py` : écriture JSONL synthèse par run dans `logs/backtest_runs.jsonl`
- `audit.py` : ajout `print_terminal_summary()` pour affichage lisible en fin de session
- `docs/plan.md` + `docs/journal.md` créés

### Dettes techniques connues (ne pas traiter maintenant)
- Monte Carlo + Wilson IC pour les métriques backtest (ticket à ouvrir plus tard)
- `runner.py` (live) ne vide pas `open_risk_cash` au daily reset — acceptable car les positions restent ouvertes
- `audit.py` ne persiste pas les stats entre redémarrages (stats in-memory uniquement) — acceptable pour le dry-run
- Rapport terminal absent du live runner (orphelin : `print_terminal_summary` non appelé à la fin) — à brancher quand le workflow complet est validé
