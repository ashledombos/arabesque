# Arabesque — ROADMAP

> Dernière mise à jour : 2026-02-20  
> Horizon : 4-6 semaines

---

## État actuel (2026-02-20)

Pipeline run sur **80 instruments** (S1: 77 → S2: 31 → S3: **17 viables**) :

| Catégorie | Testés | Viables | Statut |
|---|---|---|---|
| Crypto alt-coins | 31 | 16 | ✅ Validée |
| Metals | 6 | 1 (XAUUSD) | ⚠️ Neutre (trend exclu) |
| FX | 43 | 0 | ❌ Suspendu 1H — à explorer 4H |
| Énergie | 0 | — | 🔄 Pas de parquets H1 |
| Commodities | 0 | — | 🔄 Pas de parquets H1 |
| Indices | 0 | — | 🔄 Pas de parquets H1 |

Filtres actifs (`config/signal_filters.yaml`) :
- `mr_shallow_wide` désactivé sur FX, crypto, indices, metals
- `trend_strong` et `trend_moderate` désactivés sur metals
- FX suspendu en 1H jusqu'à validation 4H

---

## Plan — Étapes P0-P8

### P0 🔴 — Corriger `daily_dd_pct` *(BLOQUANT avant tout live)*

**Fichier** : `arabesque/guards.py`  
**Fix** : `(daily_start_balance - equity) / daily_start_balance` (pas `/start_balance`)  
**Validation** : replay 3 mois, chercher `"rejected DAILY_DD_LIMIT"` dans les logs.

### P1 🟠 — Implémenter `EXIT_TRAILING`

Dans `_check_sl_tp_intrabar` : si `pos.trailing_active and pos.result_r > 0` → `DecisionType.EXIT_TRAILING`.  
Débloque : vrai WR, PF, expectancy par sortie, et la décision TP fixe vs TSL.

### P2 — Run stats avancées sur les 17 viables

```bash
for inst in AAVUSD ALGUSD BCHUSD DASHUSD GRTUSD ICPUSD IMXUSD LNKUSD \
            NEOUSD NERUSD SOLUSD UNIUSD VECUSD XAUUSD XLMUSD XRPUSD XTZUSD; do
    python scripts/run_stats.py $inst --period 730d
done
# Garder si bootstrap 95% CI borne basse > 0R
# Reporter dans config/instruments.yaml (follow: true)
```

### P3 — Valider les guards DD sur replay 3 mois (après fix P0)

```bash
python -m arabesque.live.runner \
  --mode dry_run --source parquet \
  --start 2025-10-01 --end 2026-01-01
# Chercher : "rejected DAILY_DD_LIMIT", "rejected MAX_DD_LIMIT"
```

### P4 — Connexion compte test FTMO (dry-run cTrader)

```bash
# Remplir config/secrets.yaml avec account_id 17057523
python -m arabesque.live.engine --dry-run
# Vrais ticks, zéro ordre envoyé
```

### P5 — Premier ordre réel (compte test uniquement)

```bash
python -m arabesque.live.engine
# Vérifier dans cTrader : ordre, SL, volume corrects
```

### P6 — FX en 4H (exploration)

```bash
python scripts/run_pipeline.py --list fx --mode wide --period 1825d -v
# Tester aussi : filtre EMA200 daily + tier 0 trailing +0.25R → 0.15R
```

### P7 — Nettoyage dette technique (TD-005 à TD-009)

- Supprimer `arabesque/live/runner.py` → remplacé par `engine.py`
- Supprimer `arabesque/live/bar_poller.py` → remplacé par `price_feed.py`
- Supprimer alias `tv_close`/`tv_open` dans `models.py` après `git grep tv_close`
- Unifier ADX dans `arabesque/indicators.py`
- Créer `scripts/run_all_stats.py` (boucle sur instruments viables)

### P8 — Nouvelles catégories (énergie, commodities, indices)

```bash
# 1. Télécharger parquets H1 via barres_au_sol
# 2. Copier dans data/parquet/
python scripts/run_pipeline.py --list energy -v
python scripts/run_pipeline.py --list indices -v
```

---

## Architecture stable vs research

```
config/stable/   + results/stable/   → production validée IS/OOS + Monte Carlo
config/research/ + results/research/ → exploration (jamais déployé direct)
```

Règle : un fichier passe de `research/` → `stable/` **uniquement** après pipeline IS/OOS + Monte Carlo complet validé.

---

## Questions ouvertes

| # | Question | Bloqué par |
|---|---|---|
| 1 | FX 4H viable avec EMA200 daily + tier 0 trailing ? | P6 |
| 2 | TP fixe 1.5-2.0R vs TSL sur `mr_deep_narrow` energy ? | P1 (EXIT_TRAILING) |
| 3 | `max_positions` optimal pour le compte challenge ? | P3 (guards validés) |
| 4 | Filtre volume crypto/metals (+0.060 corrélation) utile ? | À tester après P1 |
| 5 | ROI dégressive (sortie profit minimal après N barres stagnation) ? | À tester après P1 |
| 6 | Stage 0 validation par catégorie dans pipeline ? | Voir `instrument_selection_philosophy.md` |
