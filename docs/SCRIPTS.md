# Arabesque — Carte des scripts

> Dernière mise à jour : 2026-02-21  
> Ce fichier répond à "quel script utiliser pour quoi ?".  
> Ne pas le lire comme une doc API — lire comme un menu.

---

## TL;DR — les 4 scripts du quotidien

```bash
# 1. Valider un instrument (backtest + stats)
python scripts/run_stats.py ICPUSD --period 730d

# 2. Sélectionner de nouveaux instruments
python scripts/run_pipeline.py --list crypto -v

# 3. Replay dry-run complet
python -m arabesque.live.engine --source parquet \
  --start 2025-10-01 --end 2026-01-01 --balance 100000 \
  --data-root ~/dev/barres_au_sol/data

# 4. Analyser le replay
python scripts/analyze_replay.py dry_run_*.jsonl
```

---

## Scripts — détail

### `scripts/run_pipeline.py` ⭐ Script principal de recherche

Screening complet de N instruments : Stage 1 (volume de signaux) → Stage 2 (IS) → Stage 3 (OOS).  
**Utiliser quand** : choisir ou réviser la liste d'instruments viables.

```bash
python scripts/run_pipeline.py -v                        # Tous les instruments configurés
python scripts/run_pipeline.py --list crypto -v          # Seulement crypto
python scripts/run_pipeline.py --list fx --mode wide -v  # FX en mode permissif
python scripts/run_pipeline.py --period 730d -v          # Données 2 ans
```

**Outputs** : rapport terminal + `results/pipeline_*.json`  
**Durée** : 2-15 min selon le nombre d'instruments

---

### `scripts/backtest.py` — Backtest IS+OOS sur un instrument

Test isolé d'un instrument spécifique, avec split IS/OOS configurable.  
**Utiliser quand** : comprendre en détail comment se comporte un instrument.

```bash
python scripts/backtest.py BCHUSD --strategy combined
python scripts/backtest.py XAUUSD --strategy mean_reversion --period 730d
python scripts/backtest.py ICPUSD --split 0.7 --verbose
```

---

### `scripts/run_stats.py` ⭐ Analyse statistique avancée

Wilson CI sur le WR + Bootstrap MC sur l'expectancy + MC équity curve pour le DD.  
**Utiliser quand** : valider qu'un instrument a vraiment un edge ou si c'est de la variance.

```bash
python scripts/run_stats.py XAUUSD --period 730d
python scripts/run_stats.py ICPUSD --sims 20000 --period 730d
```

**Sorties** :
- Wilson IC95 sur le win rate (> 50% significatif ?)
- Bootstrap IC95 sur l'expectancy (IC95 low > 0 ?)
- Monte Carlo DD : P(breach daily 3%, total 8%)
- Verdict : PRÊT / PARTIEL / NON

**Règle** : ne garder dans `config/instruments.yaml` que les instruments avec IC95 low > 0R.

---

### `scripts/analyze_replay.py` ✨ Nouveau (2026-02-21)

Analyse statistique d'un fichier JSONL de replay dry-run.  
**Utiliser quand** : après chaque replay `python -m arabesque.live.engine --source parquet`.

```bash
python scripts/analyze_replay.py dry_run_20260221_141951.jsonl
python scripts/analyze_replay.py dry_run_*.jsonl --spike-threshold 15
```

**Sorties** :
1. Détection outliers |R| > seuil (spikes de données suspects)
2. Edge brut vs net (sans outliers) + bootstrap IC95
3. Concentration : P&L tient-il sans top-3/5/10 trades ?
4. Consistance temporelle : % de fenêtres glissantes de 50 trades positives
5. Par instrument : expectancy + IC95 + significativité
6. Monte Carlo DD prop firm
7. **Verdict prop firm** : score 0-4 critères

**Interprétation** :
- Score 4/4 → forward-test prop firm
- Score 3/4 → corriger le point faible
- Score ≤ 2/4 → ne pas passer au live

---

### `scripts/run_label_analysis.py` — Analyse par sous-type de signal

Décompose les résultats par sous-type (`mr_deep_narrow`, `trend_strong`, etc.) × catégorie d'instrument.  
**Utiliser quand** : comprendre *quel* type de signal porte l'edge dans quelle catégorie.

```bash
python scripts/run_label_analysis.py
python scripts/run_label_analysis.py --list crypto
```

**Règle d'usage** : lancer après un changement de paramètres signal pour voir si la distribution change.

---

### `scripts/run_json_export.py` — Export backtest → JSONL

Exporte les positions individuelles d'un backtest en JSONL pour analyse externe.  
**Utiliser quand** : besoin de données brutes pour un outil d'analyse tiers.

```bash
python scripts/run_json_export.py ICPUSD -o results/icpusd_trades.jsonl
python scripts/run_json_export.py ICPUSD GRTUSD IMXUSD -o results/top3.jsonl
```

**Note** : `analyze_replay.py` fait déjà l'analyse des JSONL de replay. Cet export est surtout pour les backtests classiques.

---

### `scripts/update_and_compare.py` — Relancer et comparer avec le run précédent

Automatise : relance le dernier backtest et compare les métriques avec le run N-1.  
**Utiliser quand** : vérifier si une modification de code a amélioré ou dégradé les résultats.

```bash
python scripts/update_and_compare.py
python scripts/update_and_compare.py --instruments ICPUSD GRTUSD
```

**Cas d'usage typique** : après avoir modifié un paramètre dans `signal_gen_combined.py`.

---

### `scripts/analyze.py` — Analyse logs paper/live

Analyse les logs JSONL produits pendant le trading paper ou live.  
**Utiliser quand** : le bot tourne (ou a tourné) et on veut un rapport de performance.

```bash
python scripts/analyze.py              # Rapport global
python scripts/analyze.py --days 7    # 7 derniers jours
python scripts/analyze.py --guards    # Calibration des guards
python scripts/analyze.py --timeline  # Timeline des trades
```

---

### `scripts/debug_pipeline.py` — Debug contrat CombinedSignalGenerator

Affiche ce que `CombinedSignalGenerator` produit sur des données réelles.  
**Utiliser quand** : suspicion de bug dans la génération de signaux ou les colonnes produites par `prepare()`.

```bash
python scripts/debug_pipeline.py
python scripts/debug_pipeline.py --instrument BCHUSD --bars 200
python scripts/debug_pipeline.py --instrument XRPUSD --show-signals 5
```

---

### `scripts/research/` — Scripts d'exploration

Explorations non validées. **Ne jamais utiliser en production.**

| Script | Objectif |
|---|---|
| `explore_fx_4h.py` | FX sur timeframe 4H (non testé) |
| `explore_tp_vs_tsl.py` | TP fixe vs TSL sur mr_deep_narrow energy |

---

## Point d'entrée live

```bash
# Replay Parquet (dry-run offline)
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data

# Dry-run cTrader (vrais ticks, zéro ordre)
python -m arabesque.live.engine --dry-run

# Live réel
python -m arabesque.live.engine
```

**Note** : `python -m arabesque.live.runner` est l'ancien point d'entrée (déprécié). Utiliser `engine.py`.

---

## Flux de travail recommandés

### Tester un nouvel instrument

```
1. run_pipeline.py --list <category>   → vérifie s'il passe Stage 1-3
2. run_stats.py <INST> --period 730d   → IC95 positif ?
3. analyze_replay.py <jsonl>           → consistence après replay 3 mois
```

### Après une modification de code stratégique

```
1. debug_pipeline.py                   → contrat Signal toujours correct
2. backtest.py <INST> --verbose        → résultats individuels cohérents
3. update_and_compare.py               → comparaison avec run précédent
4. run_stats.py <INST>                 → IC95 meilleur ou équivalent
5. engine.py --source parquet          → replay complet
6. analyze_replay.py <jsonl>           → verdict prop firm
```

### Audit périodique mensuel

```
1. run_pipeline.py -v --period 365d    → scanner tous les instruments
2. run_label_analysis.py               → matrice sub-type × catégorie
3. update_and_compare.py               → dérive vs mois précédent
```
