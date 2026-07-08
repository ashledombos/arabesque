# Scripts Arabesque

Ce dossier contient les scripts CLI du projet. Les données Parquet sont
cherchées dans `barres_au_sol/` ou via la variable d'environnement
`ARABESQUE_DATA_ROOT`.

> Le point d'entrée principal du projet est `python -m arabesque`
> (`run`, `walkforward`, `screen`, `positions`…) — voir CLAUDE.md.
> Les scripts ci-dessous couvrent le screening legacy, l'analyse des logs
> et l'exploitation (audits, monitoring, rappels).

---

## Screening et statistiques

### `run_pipeline.py` — Screening multi-instrument

Pipeline en 3 stages : signal count → IS backtest → IS+OOS+stats.
Screening **Extension uniquement** (mean-reversion supprimée — code
historique récupérable via `git show 0c15991^`).
Lit les données Parquet disponibles, exporte un JSONL horodaté dans `results/`.

```bash
# Auto-détection (tous les instruments avec Parquet disponible)
python scripts/run_pipeline.py

# Par catégorie
python scripts/run_pipeline.py --list crypto
python scripts/run_pipeline.py --list fx
python scripts/run_pipeline.py --list metals
python scripts/run_pipeline.py --list all

# Instruments spécifiques
python scripts/run_pipeline.py BTCUSD XAUUSD GBPUSD

# Modes de filtrage
python scripts/run_pipeline.py --mode strict   # critères stricts (prop firm)
python scripts/run_pipeline.py --mode wide     # critères larges (exploration)
python scripts/run_pipeline.py --mode default  # défaut

# Options
python scripts/run_pipeline.py -v                         # verbose (détail éliminations)
python scripts/run_pipeline.py --no-json                  # pas d'export JSONL
python scripts/run_pipeline.py --data-root /path/to/data  # Parquet custom
python scripts/run_pipeline.py --period 365d              # période custom
```

**Critères par mode :**

| Mode    | min_signals | min_trades_IS | min_exp_R | min_PF | max_DD |
|---------|-------------|---------------|-----------|--------|--------|
| default | 50          | 30            | -0.10     | 0.80   | 10 %   |
| strict  | 80          | 50            | -0.05     | 0.90   | 6 %    |
| wide    | 30          | 20            | -0.15     | 0.70   | 12 %   |

**Sortie :** `results/pipeline_YYYY-MM-DD_HHMM.jsonl`

### `run_stats.py` — Statistiques approfondies post-backtest

Après avoir identifié un instrument viable avec le pipeline, approfondit
l'analyse statistique : Wilson CI, bootstrap expectancy, Monte Carlo drawdown,
compatibilité FTMO.

```bash
python scripts/run_stats.py BTCUSD
python scripts/run_stats.py XAUUSD --sims 20000 --period 730d
python scripts/run_stats.py GBPUSD --risk 0.5 --balance 100000 --quiet
```

**Sorties :**
- Intervalle de confiance du win-rate (Wilson 95%)
- Espérance IS et OOS avec bootstrap CI95
- Simulation Monte Carlo drawdown (p95, compatibilité FTMO)
- Dégradation IS→OOS (signal d'overfitting si > 70%)

---

## Analyse des logs live

### `analyze.py` — Analyse des logs paper trading / live

Lit les logs JSONL d'audit produits par le moteur live et génère des rapports
de performance, calibration des guards, timeline des événements.

```bash
python scripts/analyze.py            # rapport de performance complet
python scripts/analyze.py --days 7   # filtrer les 7 derniers jours
python scripts/analyze.py --guards   # calibration des guards (taux de rejet)
python scripts/analyze.py --timeline # timeline chronologique des ordres
python scripts/analyze.py --daily    # résumé quotidien
python scripts/analyze.py --csv trades.csv
python scripts/analyze.py --all
python scripts/analyze.py --dir /path/to/logs/audit
```

---

## Exploitation (audits, monitoring, rappels)

Les autres scripts sont opérationnels et documentés dans leur en-tête
(docstring) ; les principaux :

- `audit_edge_live_vs_backtest.py` — écart edge live vs backtest (→ `logs/edge_audit_latest.md`)
- `check_execution_invariants.py` — invariants d'exécution (BE armé, reconcile, MFE)
- `audit_execution_integrity.py` — intégrité d'exécution forward-looking
- `selection_coverage.py` — couverture des signaux théoriques par le live
- `feed_watchdog.py` — surveillance feed/canal trading + auto-repair
- `health_check.py`, `daily_report.py` — santé système et rapport quotidien
- `capture_swap_rates.py` — capture des swaps overnight (cTrader API)
- `check.sh` — garde-fou pré-commit (ruff + pytest), cf. CLAUDE.md
