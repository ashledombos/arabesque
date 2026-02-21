# Arabesque — BB_RPB_TSL : modèle cible et état des écarts

> Dernière mise à jour : 2026-02-21  
> Ce document est la boussole technique.  
> BB_RPB_TSL n'est pas "une inspiration dont on s'éloigne" — c'est le **modèle à atteindre**.

---

## Pourquoi BB_RPB_TSL est la référence absolue

BB_RPB_TSL tourne en live depuis ~527 jours. C'est une preuve empirique, pas une théorie :

| Métrique | Valeur |
|---|---|
| CAGR | ~48% |
| Win Rate | **90.8%** |
| Instruments | Altcoins USDT Binance spot (H1) |
| Edge | Mean-reversion Bollinger Bands — rebond depuis bande inférieure |
| Asymétrie | Achète la baisse → slippage favorable ou neutre |

Le Win Rate 90.8% est **le chiffre clé**. Il signifie :
- 9 trades sur 10 sont gagnants
- Les 10% perdants sont bien délimités (SL défini dès l'entrée)
- La courbe d'équité est régulière et prévisible
- Les séries de pertes sont courtes (probabilité d'une série de 5 pertes : 0.092^5 ≈ 0.0006%)

C'est exactement le profil qu'une prop firm veut voir. C'est exactement le profil qu'Arabesque doit reproduire.

---

## Ce qu'Arabesque adapte (et pourquoi, et ce que ça ne change PAS)

| Dimension | BB_RPB_TSL | Arabesque | Justification |
|---|---|---|---|
| Instruments | Pairs USDT Binance | XXXUSD FTMO | Adaptation prop firm — **ne change pas le profil de gains** |
| Guards DD | Non (pas de prop firm) | Oui (4%/9%) | Nécessaire — **renforce** le profil de régularité |
| Sizing | Fixe par % portfolio | Adaptatif selon DD | Protection supplémentaire — **compatible** avec le profil |
| Spreads | Quasi-nuls (spot) | CFDs (spreads inclus) | Impact sur le WR brut — à quantifier |
| Module trend | Non | Oui (filtre HTF) | **Hypothèse à valider** — peut aider ou filtrer à tort |

**Ce qu'Arabesque ne doit PAS changer par rapport à BB_RPB_TSL :**
- Le profil WR ≥ 85%
- Les petits gains fréquents (pas de "TP à 3R")
- La logique entry : rebond depuis BB basse, pas le breakout
- La régularité de la courbe d'équité

---

## État de la divergence (mesuré en replay Oct 2025 → Jan 2026)

### v2 — AVANT correction (trailing 5 paliers, SL 0.8 ATR)

| Métrique | BB_RPB_TSL (live) | Arabesque v2 (replay net) | Écart |
|---|---|---|---|
| Win Rate | 90.8% | **52.0%** | **−38.8 pts** |
| Variance par trade | ~0.43R (std) | ~1.64R (std) | **3.8× plus volatile** |
| DD possible sur 100 trades | ~4R (±1σ) | ~16R (±1σ) | **4× plus risqué** |
| Expectancy | ~+0.36R | +0.035R (non sig.) | **Non mesurable** |
| Score prop firm | N/A (pas de prop) | **0/4** | Pas prêt |

### Cause principale identifiée : absence du `minimal_roi`

L'analyse détaillée de `BB_RPB_TSL.py` (session Opus 4.6, 2026-02-21) a révélé que le mécanisme clé du WR 90.8% n'est pas dans les entrées mais dans les sorties :

```python
# BB_RPB_TSL — le vrai moteur du WR
minimal_roi = {
    "0": 0.205,    # vend si profit ≥ 20.5%
    "81": 0.038,   # après 81h : vend si profit ≥ 3.8%
    "292": 0.005,  # après 292h : vend si profit ≥ 0.5%
}
stoploss = -0.99   # SL quasi inexistant — laisse respirer
# custom_stoploss ne trail qu'au-dessus de +3%
```

**Ce mécanisme était totalement absent dans Arabesque v2.** Les trades devaient soit toucher le TP fixe (bb_mid) soit être trailés, alors que BB_RPB_TSL capture les petits profits dès qu'ils sont disponibles.

### v3.0 — Correction appliquée (ROI dégressif)

| Dimension | Avant (v2) | Après (v3.0) | BB_RPB_TSL |
|---|---|---|---|
| TP temps-dépendant | ❌ Absent | ✅ ROI 4 paliers (0→3R, 48→1R, 120→0.5R, 240→0.15R) | ✅ `minimal_roi` 3 paliers |
| SL effectif | 0.8×ATR (serré) | 1.5×ATR (large) | -99% (quasi absent) |
| Trailing activation | +0.5R (5 paliers) | +1.5R (3 paliers) | +3% (~1.5R) |
| Break-even | +0.5R | +1.0R | Implicite via trailing |
| Time-stop | 48 barres | 336 barres | 292h via minimal_roi |

**Résultats v3.0 : À MESURER (P3a)**

---

## Plan de retour vers le profil cible

### Étape 1 — Mesurer l'impact du trailing sur le WR *(priorité)*

```bash
# Lancer backtest avec TP fixe 1.0R (profil BB_RPB_TSL)
# vs trailing actuel
# → comparer WR résultant
python scripts/backtest.py ICPUSD --tp-fixed 1.0 --verbose
python scripts/backtest.py ICPUSD --strategy combined --verbose
# → Si WR passe de 52% à 75%+ avec TP fixe : trailing est le problème
```

### Étape 2 — Mesurer l'impact du module trend

```bash
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy mean_reversion --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay.py dry_run_*.jsonl
# → Si WR monte sans le filtre trend : revoir les seuils ADX
```

### Étape 3 — Comparer les paramètres BB

Extraire les paramètres exacts de BB_RPB_TSL (longueur, std dev, source) et vérifier que `signal_gen.py` les utilise ou s'en écarte délibérément.

### Étape 4 — Valider sur replay propre

```
Critères de succès :
  WR ≥ 70% (acceptable)  /  WR ≥ 85% (cible)
  Expectancy IC95 low > 0
  Consistance 50-trade windows ≥ 65%
  Score analyze_replay.py ≥ 3/4
```

---

## Règle de développement dérivée de ce document

> Avant d'implémenter quoi que ce soit, se demander :  
> **"Est-ce que ce changement fait monter ou descendre le Win Rate ?"**  
>  
> Si la réponse est "il fait descendre le WR mais augmente l'avg_win" → c'est une dérive.  
> Si la réponse est inconnue → tester sur backtest avant de merger.
