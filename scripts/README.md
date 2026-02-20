# Scripts Arabesque

Ce dossier contient tous les scripts CLI du projet. Les données Parquet sont
cherchées dans `~/dev/barres_au_sol/data` ou via la variable d'environnement
`ARABESQUE_DATA_ROOT`.

---

## Flux de travail typique

```
1. run_pipeline.py     → identifier les instruments viables
2. run_stats.py        → approfondir un instrument retenu
3. run_label_analysis.py → comprendre quels sous-types de signal fonctionnent
4. backtest.py         → rejouer un instrument précis ou un preset
5. run_json_export.py  → convertir/exporter des résultats existants
6. analyze.py          → analyser les logs d'un paper trading ou live
7. debug_pipeline.py   → diagnostiquer un problème de signal
```

---

## `run_pipeline.py` — Screening multi-instrument

Pipeline en 3 stages : signal count → IS backtest → IS+OOS+stats.
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
python scripts/run_pipeline.py --strategy combined        # stratégie (défaut: combined)
```

**Critères par mode :**

| Mode    | min_signals | min_trades_IS | min_exp_R | min_PF | max_DD |
|---------|-------------|---------------|-----------|--------|--------|
| default | 50          | 30            | -0.10     | 0.80   | 10 %   |
| strict  | 80          | 50            | -0.05     | 0.90   | 6 %    |
| wide    | 30          | 20            | -0.15     | 0.70   | 12 %   |

**Sortie :** `results/pipeline_YYYY-MM-DD_HHMM.jsonl`

---

## `backtest.py` — Backtest instrument(s) ou preset

Backtest IS+OOS sur un ou plusieurs instruments.

```bash
# Un instrument
python scripts/backtest.py BTCUSD
python scripts/backtest.py XAUUSD --strategy combined --period 730d

# Plusieurs instruments
python scripts/backtest.py BTCUSD XAUUSD GBPUSD

# Presets
python scripts/backtest.py --preset crypto_all
python scripts/backtest.py --preset fx_majors --strategy combined
python scripts/backtest.py --preset metals

# Options utiles
python scripts/backtest.py EURUSD --data-status  # disponibilité Parquet puis exit
python scripts/backtest.py EURUSD --no-parquet   # forcer Yahoo Finance
python scripts/backtest.py EURUSD --quiet        # pas de logs détaillés
python scripts/backtest.py EURUSD --risk 0.3 --balance 200000
```

**Presets disponibles :** `fx_majors`, `fx_crosses`, `fx_exotics`, `fx_all`,
`crypto_top`, `crypto_all`, `metals`, `energy`, `indices`, `commodities`,
`stocks_us`, `stocks_eu`, `stocks_all`, `all`

---

## `run_stats.py` — Statistiques approfondies post-backtest

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

## `run_label_analysis.py` — Analyse par sous-type de signal

Ventile les résultats par type de signal (`mr_deep_wide`, `trend_strong`, etc.)
et par catégorie d'instrument. Utile pour comprendre quels setups fonctionnent
sur quelle classe d'actif.

```bash
# Tous les instruments avec Parquet
python scripts/run_label_analysis.py

# Par catégorie
python scripts/run_label_analysis.py --list crypto
python scripts/run_label_analysis.py --list fx

# Instruments spécifiques
python scripts/run_label_analysis.py BTCUSD XAUUSD EURUSD

# Options
python scripts/run_label_analysis.py -v --json results/labels.json
python scripts/run_label_analysis.py --is-too  # inclure aussi IS (OOS seul par défaut)
python scripts/run_label_analysis.py --min-trades 20  # minimum trades par cellule matrice
```

**Sortie :** matrice `sub_type × catégorie` (expectancy, win-rate, PF par cellule)

---

## `run_json_export.py` — Export/conversion résultats

Exporte les résultats d'un backtest en JSON structuré (pour visualisation,
archivage ou comparaison avec une autre run).

```bash
python scripts/run_json_export.py BTCUSD
python scripts/run_json_export.py BTCUSD --output results/btcusd_export.json
```

---

## `analyze.py` — Analyse des logs paper trading / live

Lit les logs JSONL d'audit produits par le moteur live et génère des rapports
de performance, calibration des guards, timeline des événements.

```bash
# Rapport de performance complet
python scripts/analyze.py

# Filtrer les 7 derniers jours
python scripts/analyze.py --days 7

# Calibration des guards (taux de rejet, causes)
python scripts/analyze.py --guards

# Timeline chronologique des ordres
python scripts/analyze.py --timeline

# Résumé quotidien
python scripts/analyze.py --daily

# Export CSV des trades
python scripts/analyze.py --csv trades.csv

# Tout en une fois
python scripts/analyze.py --all

# Dossier de logs custom
python scripts/analyze.py --dir /path/to/logs/audit
```

---

## `debug_pipeline.py` — Diagnostic signal

Affiche le contrat exact de `CombinedSignalGenerator` sur un instrument :
colonnes produites par `prepare()`, exemples de signaux, champs du dataclass
`Signal`. Utile pour diagnostiquer un problème de signal ou inspecter un
nouvel instrument.

```bash
python scripts/debug_pipeline.py
python scripts/debug_pipeline.py --instrument BCHUSD --bars 200
python scripts/debug_pipeline.py --instrument XRPUSD --show-signals 5
```

---

## `scripts/research/`

Dossier pour les notebooks et scripts exploratoires. Non intégré au pipeline
principal.
