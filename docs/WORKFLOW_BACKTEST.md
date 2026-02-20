# Arabesque — Workflow Backtest → Paper → Live

> Ce document décrit le cycle complet de validation d'une stratégie :
> du backtest initial jusqu'au passage en live, en conservant les données
> de trades à chaque étape pour comparaison future.

---

## Vue d'ensemble

```
 barres_au_sol (cron quotidien)
        │
        ▼
  data/parquet/*_H1.parquet   ← données OHLC mises à jour
        │
        ▼
  [A] BACKTEST (backtest.runner)
        │  logs/backtest_runs.jsonl  ← métriques agrégées
        │  logs/trades/*.jsonl       ← trades individuels
        │
        ▼
  [B] COMPARAISON (update_and_compare.py)
        │  logs/comparisons/compare_<date>.txt
        │
        ▼
  [C] PAPER TRADING (live.engine --mode dry_run)
        │  logs/dry_run_*.jsonl
        │
        ▼
  [D] LIVE (live.engine --mode live)
        │  logs/live_*.jsonl
        │
        ▼
  [E] VALIDATION CROISÉE
        Comparer backtest vs paper vs live
        sur la même période une fois les données disponibles
```

---

## A — Backtest

### Lancer un backtest

```bash
# Un instrument
python -m arabesque.backtest.runner --strategy combined \
  --start 2025-01-01 --end 2026-01-01 \
  XRPUSD

# Plusieurs instruments
python -m arabesque.backtest.runner --strategy combined \
  --start 2025-01-01 --end 2026-01-01 \
  XRPUSD SOLUSD BNBUSD BTCUSD

# Tous les instruments (via update_and_compare.py)
python scripts/update_and_compare.py --strategy combined \
  --start 2025-01-01 --export-trades
```

### Fichiers générés

| Fichier | Contenu | Utilisation |
|---------|---------|-------------|
| `logs/backtest_runs.jsonl` | Une ligne JSON par run (métriques agrégées) | Comparaison N-1→N, suivi tendance |
| `logs/trades/<date>.jsonl` | Un trade JSON par ligne (positions fermées) | Analyse fine, comparaison paper/live |
| `logs/comparisons/compare_<date>.txt` | Rapport textuel delta run N-1→N | Revue rapide des changements |

### Format d'un run dans `backtest_runs.jsonl`

```json
{
  "ts": "2026-02-20T18:00:00+00:00",
  "instrument": "XRPUSD",
  "sample": "out_of_sample",
  "n_trades": 42,
  "win_rate": 0.571,
  "expectancy_r": 0.312,
  "profit_factor": 1.84,
  "max_dd_pct": 2.1,
  "n_disq_days": 0,
  "n_signals": 67,
  "n_rejected": 25,
  "rejection_reasons": {"cooldown": 18, "slippage_too_high": 7}
}
```

### Format d'un trade dans `logs/trades/*.jsonl`

```json
{
  "run_ts":      "2026-02-20T18:00:00+00:00",
  "strategy":    "combined",
  "period_start":"2025-01-01",
  "period_end":  "2026-01-01",
  "instrument":  "XRPUSD",
  "sample_type": "out_of_sample",
  "side":        "LONG",
  "entry":       2.677,
  "sl":          2.601,
  "result_r":    1.83,
  "risk_cash":   500.0,
  "exit_reason": "exit_trailing",
  "bars_open":   3,
  "mfe_r":       2.1,
  "ts_entry":    "2025-03-14T09:00:00+00:00",
  "ts_exit":     "2025-03-14T12:00:00+00:00"
}
```

---

## B — Comparaison automatique (après mise à jour des Parquets)

Quand `barres_au_sol` a téléchargé de nouvelles barres, relancer :

```bash
# Workflow standard : comparer + exporter les trades
python scripts/update_and_compare.py \
  --strategy combined \
  --start 2025-01-01 \
  --export-trades
```

Le script :
1. Lit le dernier run depuis `logs/backtest_runs.jsonl`
2. Relance le backtest sur les mêmes instruments
3. Compare les métriques OOS run N-1 → run N
4. Affiche les ⚠️ régressions et ✅ améliorations
5. Sauvegarde le rapport dans `logs/comparisons/`

**Règle de décision** :
- Si `expectancy_r` baisse de plus de `0.05R` sur un instrument → investiguer
- Si `max_dd_pct` monte de plus de `0.5%` → vérifier les guards
- Si un instrument régresse sur 2 runs consécutifs → l'exclure du live

---

## C — Paper Trading (dry-run)

Le paper trading rejoue les données Parquet en temps quasi-réel (bougie par bougie)
avec le **même code** que le live.

```bash
# Rejouer une période précise
python -m arabesque.live.engine --mode dry_run --source parquet \
  --start 2025-10-01 --end 2025-12-31 --strategy combined

# Stream infini (Ctrl+C pour résumé)
python -m arabesque.live.engine --mode dry_run --source parquet \
  --strategy combined
```

### Fichiers générés

`logs/dry_run_<date>.jsonl` — même format que le live JSONL (voir §7 HANDOVER.md).

### Validation croisée backtest ↔ paper

Une fois une période de paper terminée, comparer avec le backtest de la même période :

```bash
# Relancer le backtest sur exactement la même période que le paper
python -m arabesque.backtest.runner --strategy combined \
  --start 2025-10-01 --end 2025-12-31 \
  XRPUSD SOLUSD BNBUSD

# Comparer visuellement :
# - n_trades backtest vs n_trades paper
# - expectancy_r backtest vs expectancy_r paper
# - exit_reasons distribution
```

**Divergences acceptables** :
- Slippage ±5% (fill price légèrement différent)
- ±1-2 trades (timing de clôture de période)

**Divergences à investiguer** :
- Différence de `n_trades` > 10%
- Différence d'`expectancy_r` > 0.1R
- Distribution `exit_reasons` très différente

---

## D — Live

```bash
export ARABESQUE_MODE=live
python -m arabesque.live.engine --mode live --strategy combined
```

**Prérequis** : credentials cTrader dans `config/secrets.yaml`.

### Fichiers générés

`logs/live_<date>.jsonl` — format identique au dry-run.

### Validation croisée paper ↔ live

Après 2-4 semaines de live, comparer avec le paper de la même période :
- Les fills live doivent être proches des fills paper (slippage réel)
- Le win rate live doit rester dans ±5% du win rate paper
- Si divergence forte → suspendre le live et investiguer

---

## E — Validation croisée complète

Lorsque de nouvelles barres Parquet sont disponibles couvrant une période déjà tradée en live :

```bash
# Relancer le backtest sur la période live
python -m arabesque.backtest.runner --strategy combined \
  --start <date_debut_live> --end <date_fin_live> \
  XRPUSD SOLUSD BNBUSD

# Comparer :
# backtest (logs/backtest_runs.jsonl) vs live (logs/live_*.jsonl)
```

C'est la **vérification ultime de l'absence de lookahead** et de la robustesse du système.

---

## Récapitulatif des fichiers de logs

| Fichier | Généré par | Format | Usage |
|---------|-----------|--------|-------|
| `logs/backtest_runs.jsonl` | `backtest.runner` | JSONL (métriques) | Comparaison runs |
| `logs/trades/*.jsonl` | `update_and_compare.py` | JSONL (trades) | Analyse fine |
| `logs/comparisons/*.txt` | `update_and_compare.py` | Texte | Revue rapide |
| `logs/dry_run_*.jsonl` | `live.engine` dry-run | JSONL (trades) | Paper vs backtest |
| `logs/live_*.jsonl` | `live.engine` live | JSONL (trades) | Live vs paper |
