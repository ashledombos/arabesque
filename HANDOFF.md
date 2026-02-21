# ARABESQUE — Handoff v7
## Pour reprendre le développement dans un nouveau chat

> **Repo** : https://github.com/ashledombos/arabesque  
> **Branche principale** : `main`  
> **Dernière mise à jour** : 2026-02-21 (session Opus 4.6 — refonte position manager v3.0)
>
> 📖 **Lire aussi** :
> - `docs/decisions_log.md` — pourquoi chaque décision a été prise (lire §0 en premier)
> - `docs/SCRIPTS.md` — carte de tous les scripts (quoi utiliser quand)
> - `docs/STABLE_vs_FRAGILE.md` — ce qui est solide vs ce qui peut casser
> - `docs/BB_RPB_TSL_COMPARISON.md` — BB_RPB_TSL comme modèle cible et état des écarts

---

## ⭐ BOUSSOLE STRATÉGIQUE — À lire avant tout le reste

> **Cette section est immuable. Elle prime sur toutes les autres.**

### Le profil de gains cible

```
OBJECTIF : gains petits, fréquents, consistants.
           Peu de pertes, et petites quand elles arrivent.
           Win Rate élevé (cible : ≥ 70%, idéal ≥ 85%).
           Expectancy positive par le volume, pas par des grands mouvements rares.
```

### La référence : BB_RPB_TSL

BB_RPB_TSL tourne en **live depuis ~527 jours** : CAGR ~48%, **Win Rate 90.8%**.

**Mécanisme clé du WR identifié** (session Opus 4.6, 2026-02-21) :
- `minimal_roi` : TP dégressif dans le temps (0h→20.5%, 81h→3.8%, 292h→0.5%)
- Pas de SL serré (-99% effectif, jamais touché)
- Trailing uniquement au-dessus de +3%
- → Presque tout trade est capturé avec un petit gain

### Signal d'alarme

"WR ~52% compensé par avg_win de 2.3R" → **DÉRIVE, CORRIGER**

---

## 2. Changements v3.0 (session Opus 4.6, 2026-02-21)

### Fichiers modifiés

| Fichier | Changement | Justification |
|---|---|---|
| `arabesque/models.py` | Ajout `EXIT_ROI` dans `DecisionType` | Nouveau type de sortie |
| `arabesque/position/manager.py` | ROI dégressif, trailing ajusté, BE relevé, time-stop étendu | Alignement BB_RPB_TSL |
| `arabesque/backtest/signal_gen.py` | `min_sl_atr` 0.8 → 1.5 | Laisser respirer les trades MR |

### Détail des modifications manager.py

**ROI dégressif** (clé de la correction) :
```
bars=0   → need ≥ 3.0R   (move exceptionnel)
bars=48  → need ≥ 1.0R   (bon profit en 2j)
bars=120 → need ≥ 0.5R   (profit modéré en 5j)
bars=240 → need ≥ 0.15R  (quasi tout profit en 10j)
```

**Trailing** : réduit de 5 paliers (dès +0.5R) à 3 paliers (dès +1.5R MFE)
**Break-even** : relevé de +0.5R → +1.0R
**Time-stop** : étendu de 48 → 336 barres (backstop, pas exit actif)

### Flux de sortie `update_position()` :
```
1. SL/TP intrabar     (sécurité)
2. ROI dégressif       ← NOUVEAU
3. Break-even          (relevé à +1.0R)
4. Trailing            (seulement >= +1.5R MFE)
5. Giveback
6. Deadfish
7. Time-stop           (backstop final 336 barres)
```

---

## 3. Résultats

### AVANT v3.0 (replay Oct 2025 → Jan 2026)

| Métrique | Valeur |
|---|---|
| Win Rate | 52.0% ❌ |
| Expectancy | +0.035R (non significatif) |
| Score prop firm | 0/4 |

### APRÈS v3.0 : À MESURER (P3a)

---

## 4. Prochaines étapes

### P3a — Valider v3.0 sur replay *(priorité absolue)*

```bash
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay.py dry_run_*.jsonl
```

**Si WR ≥ 70% et score ≥ 3/4** → P3b  
**Si WR 60-70%** → ajuster seuils ROI  
**Si WR < 60%** → problème entry, pas sortie

### P2c — Diagnostiquer spikes parquets *(en parallèle)*
### P3b — Comparer mean_reversion vs combined
### P3c — `run_stats.py` 17 instruments × 2 ans
### P4 — Connexion compte test FTMO (après score ≥ 3/4)

---

## 5. Comptes FTMO

| Compte | Solde | Statut |
|---|---|---|
| Live test 15j | 100 000 USD | ✅ OK pour tests ordres |
| Challenge 100k | ~94 989 USD | ⚠️ NE PAS connecter |

---

## 6. Règles non négociables

1. Profil WR élevé en priorité
2. Anti-lookahead : signal bougie `i`, exécution open `i+1`
3. Guards toujours actifs (dry-run inclus)
4. Même `CombinedSignalGenerator` backtest / replay / live
5. Jamais `git push --force` sur `main`
6. Ne connecter le challenge qu'après WR ≥ 70%
7. Tout changement stratégique : mesurer WR d'abord

---

## 7. Restrictions de modification par niveau IA

### ⛔ Réservé Opus 4.6 (ou modèle le plus puissant)

- `position/manager.py` — architecture de sortie
- `signal_gen*.py` — logique d'entrée
- `guards.py` — protection prop firm
- Refonte pipeline, stats, métriques
- Tout changement affectant WR ou expectancy

### ✅ Accessible à des modèles moins puissants

- Exécution de replay et analyse (P3a)
- Diagnostic spikes données (P2c)
- `run_stats.py` et collecte résultats
- Mise à jour cosmétique documentation
- Comparaison résultats avant/après
