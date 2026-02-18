# Arabesque — ROADMAP

> Version : 2026-02-18  
> Horizon : 4 semaines (S1 → S4)

---

## Baseline v1 — Résultats après filtres Phase 1.3

Analyse OOS sur **6 759 trades / 102 instruments** (split 70/30, 730 jours de données).

| Catégorie   | Sub-type gagnant      | Total R OOS | Statut   |
|-------------|-----------------------|-------------|----------|
| energy      | mr_deep_wide          | positif     | ✅ stable |
| commodities | mr_deep_wide          | positif     | ✅ stable |
| crypto      | trend_strong          | positif     | ✅ stable |
| indices     | trend_strong          | positif     | ✅ stable |
| metals      | mr_shallow_narrow     | à confirmer | 🔶 watch  |
| fx          | tous sub-types        | −60.2 R     | ❌ filtré |

**Filtres actifs** (`config/signal_filters.yaml`) :
- `mr_shallow_wide` désactivé sur FX, crypto, indices, metals
- `trend_strong` et `trend_moderate` désactivés sur metals
- FX entièrement hors prod jusqu'à validation 4H (cf. S3)

**Métriques baseline à battre** (seuils de référence) :
- Expectancy globale OOS : > 0.10 R/trade
- Profit Factor : > 1.20
- Max Drawdown : < 15 % du capital

---

## Plan 4 semaines

### S1 — Filtres + EXIT_TRAILING

**Objectif** : stabiliser la baseline, mesurer l'impact du trailing stop.

- [ ] Intégrer `SignalFilter.is_allowed()` dans `BacktestRunner` et `CombinedSignalGenerator`
- [ ] Relancer `run_label_analysis.py` avec filtres actifs → confirmer gain OOS
- [ ] Analyser la distribution des sorties `EXIT_TRAILING` vs `EXIT_SL` vs `EXIT_TP`
- [ ] Tester variantes des paliers trailing : `[1.0R, 1.5R, 2.0R]` vs `[0.75R, 1.25R, 2.0R]`
- [ ] Sauvegarder résultats dans `results/stable/s1_baseline_filtered.json`

### S2 — Scorecard par instrument

**Objectif** : identifier les instruments individuellement profitables.

- [ ] Créer `scripts/run_scorecard.py` : rank instruments par Sharpe OOS
- [ ] Définir seuil d'inclusion : expectancy > 0.08R ET n_trades ≥ 30 OOS
- [ ] Générer `config/stable/instruments_approved.yaml` (liste filtrée)
- [ ] Comparer scorecard IS vs OOS → détecter overfitting par instrument
- [ ] Sauvegarder dans `results/stable/s2_scorecard.json`

### S3 — FX en 4H

**Objectif** : tester si FX redevient profitable sur timeframe supérieur.

- [ ] Lancer `scripts/research/explore_fx_4h.py`
- [ ] Comparer FX 1H vs FX 4H sur même période OOS
- [ ] Critère de validation : Total R OOS > 0 sur ≥ 3 sub-types
- [ ] Si validé → créer `config/research/fx_4h_settings.yaml`
- [ ] Sauvegarder dans `results/research/s3_fx_4h.json`

### S4 — TP fixe vs Trailing

**Objectif** : optimiser la stratégie de sortie sur les sub-types avec AvgW > 1.0R.

- [ ] Lancer `scripts/research/explore_tp_vs_tsl.py`
- [ ] Cibles : TP fixe à 1.5R, 2.0R, 2.5R vs trailing actuel
- [ ] Filtrer sur sub-types où AvgWin > 1.0R (energy × mr_deep_wide, etc.)
- [ ] Critère : TP fixe retenu si Sharpe ≥ trailing ET max DD ≤ trailing
- [ ] Sauvegarder dans `results/research/s4_tp_vs_tsl.json`

---

## Architecture deux branches

```
config/
├── settings.yaml             # Config globale (broker, risk, mode)
├── signal_filters.yaml       # Matrice activation sub_type × catégorie
├── stable/                   # Configs validées OOS — ne pas modifier sans test
│   └── instruments_approved.yaml
└── research/                 # Configs expérimentales — jamais en prod
    └── fx_4h_settings.yaml

results/
├── stable/                   # Résultats backtests branch stable
│   ├── s1_baseline_filtered.json
│   └── s2_scorecard.json
└── research/                 # Résultats expérimentaux
    ├── s3_fx_4h.json
    └── s4_tp_vs_tsl.json

scripts/
├── run_label_analysis.py     # Pipeline Phase 1.3 (existant)
├── run_pipeline.py           # Pipeline principal (existant)
└── research/                 # Scripts d'exploration (jamais importés en prod)
    ├── explore_fx_4h.py
    └── explore_tp_vs_tsl.py
```

**Règle de gouvernance** :
- Un fichier passe de `research/` → `stable/` uniquement après validation OOS positive
- Les scripts `research/` ne sont jamais importés par le runner de prod
- Chaque merge vers `main` doit inclure le fichier `results/stable/` correspondant

---

## Décisions ouvertes

| # | Question | Owner | Deadline |
|---|----------|-------|----------|
| 1 | Intégrer SignalFilter dans le webhook live ou seulement backtest ? | — | S1 |
| 2 | FX 4H : utiliser Yahoo Finance ou Parquet FTMO ? | — | S3 |
| 3 | Seuil AvgWin minimal pour activer TP fixe ? | — | S4 |
| 4 | `mr_deep_narrow` : activer sur metals après S1 si OOS > 0 ? | — | S2 |
