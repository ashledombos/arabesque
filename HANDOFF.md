# ARABESQUE — Handoff v8
## Pour reprendre le développement dans un nouveau chat

> **Repo** : https://github.com/ashledombos/arabesque  
> **Branche principale** : `main`  
> **Dernière mise à jour** : 2026-02-21 (session Opus 4.6 — v3.1 post-diagnostic replay)
>
> 📖 **Lire aussi** :
> - `docs/decisions_log.md` — pourquoi chaque décision a été prise (lire §0 en premier)
> - `docs/SCRIPTS.md` — carte de tous les scripts
> - `docs/STABLE_vs_FRAGILE.md` — ce qui est solide vs ce qui peut casser
> - `docs/BB_RPB_TSL_COMPARISON.md` — écarts vs modèle cible
> - `docs/RESUME_PROMPT.md` — prompt de reprise pour modèle intermédiaire

---

## ⭐ BOUSSOLE STRATÉGIQUE — Immuable

```
OBJECTIF : gains petits, fréquents, consistants.
           Win Rate élevé (cible : ≥ 70%, idéal ≥ 85%).
           Expectancy positive par le volume, pas par des grands mouvements rares.
```

**Référence** : BB_RPB_TSL live ~527j, CAGR ~48%, WR 90.8%  
**Signal d'alarme** : "WR ~52% compensé par avg_win" → **DÉRIVE**

---

## 2. Historique des versions

### v3.0 (2026-02-21, session 1)
- Ajout ROI dégressif dans manager.py (tiers 48/120/240 barres)
- Trailing réduit à 3 paliers (>= 1.5R MFE)
- SL élargi de 0.8 → 1.5 ATR

### v3.0 — RÉSULTATS REPLAY
| Métrique | v2 | v3.0 | Δ |
|---|---|---|---|
| Win Rate | 52.0% | **50.6%** | -1.4 pts ❌ |
| Expectancy | +0.035R | **+0.094R** | +0.059R ✅ |
| Total R | +27.5R | **+73.9R** | +46.4R ✅ |
| EXIT_ROI | 0% | **2.3%** | Quasi inutile |
| Score prop | 0/4 | ? | Non mesuré |

**Diagnostic v3.0** (5 problèmes identifiés) :
1. **42% des trades ferment en ≤3 barres, WR=34.8%** → SL touché trop vite
2. **ROI inutile (2.3%)** → tiers trop longs (48-240h) pour trades de 3h médiane
3. **BE à 1.0R trop haut** → 39% des SL-losers avaient MFE ≥ 0.5R
4. **BB calculées sur Close, pas typical_price** → BB_RPB_TSL utilise (H+L+C)/3
5. **RSI oversold=35 trop permissif** → BB_RPB_TSL utilise ~32

### v3.1 (2026-02-21, session 2) — Corrections basées sur le diagnostic

| Fichier | Changement | Justification (donnée) |
|---|---|---|
| `indicators.py` | BB sur typical_price (H+L+C)/3 | Alignement BB_RPB_TSL |
| `signal_gen.py` | RSI 35→30, min_bb_width 0.003→0.02 | Filtrer entrées faibles |
| `signal_gen.py` | SL 1.5→2.0 ATR | 72% des SL touchés en ≤5 barres |
| `manager.py` | ROI tiers courts (6/12/24/48/120h) | Médiane trade = 3h |
| `manager.py` | BE 1.0→0.5R | 39% losers avaient MFE ≥ 0.5R |
| `manager.py` | Giveback MFE 1.0→0.5R | Capturer profits qui s'érodent |

---

## 3. Prochaines étapes

### P3a-bis — Replay v3.1 *(priorité absolue)*

```bash
cd ~/dev/arabesque && git pull
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay.py dry_run_*.jsonl
```

**Métriques à rapporter :**
- WR (cible ≥ 65%, idéal ≥ 70%)
- Expectancy R + IC95
- Breakdown EXIT_ROI vs EXIT_SL vs EXIT_TP vs EXIT_TRAILING
- WR par bucket de durée (0-3h, 3-6h, 6-12h, 12-24h)
- % losers avec MFE ≥ 0.5R (doit baisser vs 39%)
- Score prop firm

**Décision :**
- WR ≥ 65% → P3b (comparer MR vs combined)
- WR 55-65% → le ROI court fonctionne, affiner les seuils
- WR < 55% → problème d'entrée (RSI/BB pas assez sélectifs)

### P2c — Spikes parquets *(en parallèle)*
### P3b — MR seule vs combined
### P4 — Connexion compte test FTMO (après score ≥ 3/4)

---

## 4. Comptes FTMO

| Compte | Solde | Statut |
|---|---|---|
| Live test 15j | 100 000 USD | ✅ OK pour tests ordres |
| Challenge 100k | ~94 989 USD | ⚠️ NE PAS connecter |

---

## 5. Règles non négociables

1. Profil WR élevé en priorité
2. Anti-lookahead : signal bougie `i`, exécution open `i+1`
3. Guards toujours actifs (dry-run inclus)
4. Même `CombinedSignalGenerator` backtest / replay / live
5. Jamais `git push --force` sur `main`
6. Ne connecter le challenge qu'après WR ≥ 70%
7. Tout changement stratégique : mesurer WR d'abord

---

## 6. Restrictions par niveau IA

### ⛔ Réservé Opus 4.6
- `position/manager.py`, `signal_gen*.py`, `guards.py`, `indicators.py`
- Tout changement affectant WR ou expectancy

### ✅ Modèle intermédiaire
- Replay P3a-bis, analyse résultats, diagnostic spikes, run_stats
- Voir `docs/RESUME_PROMPT.md`
