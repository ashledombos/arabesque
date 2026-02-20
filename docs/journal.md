# Arabesque — Journal d'évolution

---

## 2026-02-20 (matin)

### Fix `signal_gen_trend.py` (commit `e2bc0eb`)
- `Signal.__init__()` ne reconnaît pas `tv_close=` / `tv_open=` (ce sont des propriétés alias, pas des champs)
- Fix : remplacer par `close=` et `open_=` dans les deux constructeurs LONG + SHORT
- Pipeline à nouveau fonctionnel après ce fix

### Run pipeline complet (80 instruments, 763s)
- **Résultat** : S1: 77 → S2: 31 → S3: 17 viables
- Crypto : 16 instruments (AAVUSD, ALGUSD, BCHUSD, DASHUSD, GRTUSD, ICPUSD, IMXUSD, LNKUSD, NEOUSD, NERUSD, SOLUSD, UNIUSD, VECUSD, XLMUSD, XRPUSD, XTZUSD)
- Metals : 1 (XAUUSD)
- FX : 0/43 viable — confirmé non viable en 1H
- Exemple XTZUSD : IS 60.8% WR → OOS 67.7% WR, expectancy IS +0.071R → OOS +0.305R (signal structurel, pas overfitting)

### Consolidation documentation (sessions Perplexity)
- Croisement 4 conversations précédentes + session courante
- `HANDOFF.md` v5 : état complet, brokers cTrader + TradeLocker, P0-P8
- `docs/decisions_log.md` : 8 sections, bugs corrigés + ouverts, instruments, questions ouvertes
- `.github/copilot-instructions.md` : règles anti-biais, workflow, alertes
- `docs/START_HERE.md` : guide de démarrage humains + IA
- `docs/TECH_DEBT.md` : dette technique priorisée (TD-001 à TD-010)

### Dettes techniques actives (à traiter en priorité)
- **TD-001 BLOQUANT** : `daily_dd_pct` / `start_balance` → doit être `/ daily_start_balance`
- **TD-002 Haute** : `EXIT_TRAILING` jamais taggué dans `_check_sl_tp_intrabar`
- **TD-003 Moyenne** : `orchestrator.get_status()` exception silencieuse

---

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
- Runner tourne sans erreur sur 19 instruments, 41 347 événements
- Guards validés en conditions réelles : `open_risk_limit`, `duplicate_instrument`, `max_daily_trades` tous actifs
- Trailing stop fonctionne (ex : NERUSD +0.57R, AAVUSD +0.72R, VECUSD +0.77R, ALGUSD +0.71R)

### Dettes techniques identifiées (traitées dans TECH_DEBT.md)
- **Corrélation inter-instruments** : 10/10/2025, krach crypto simultané RSI <20 sur 15 instruments → TD-010
- `runner.py` (live) ne vide pas `open_risk_cash` au daily reset — acceptable (positions restent ouvertes)
- `audit.py` stats non persistées entre redémarrages — acceptable pour dry-run

---

## 2026-02-18

### Patch guards (commit `994f228`)
- Ajout `RejectReason.OPEN_RISK_LIMIT` et `MAX_DAILY_TRADES` dans `models.py`
- `guards.py` : `PropConfig.max_positions` 3→10, `PropConfig.max_open_risk_pct=2.0`, `AccountState.open_risk_cash=0.0`
- Correction bug `_daily_trades()` qui remontait `MAX_POSITIONS` au lieu de `MAX_DAILY_TRADES`
- **Dette** : `open_risk_cash` non branché dans orchestrator → guard inactif jusqu'au patch suivant

### Patch orchestrator (commit `afb062d`)
- `orchestrator.py` : `open_risk_cash += risk_cash` à l'ouverture, `-= pos.risk_cash` à la fermeture
- `config.py` : ajout `max_open_risk_pct=2.0`, `max_positions` default 10
- `runner.py` (live) : lit `ARABESQUE_MAX_OPEN_RISK_PCT`, default `ARABESQUE_MAX_POSITIONS` 10
- Guard `OPEN_RISK_LIMIT` actif en live dès ce commit
