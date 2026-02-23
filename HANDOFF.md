# ARABESQUE — Handoff v12
## Pour reprendre le développement dans un nouveau chat

> **Repo** : https://github.com/ashledombos/arabesque  
> **Dernière mise à jour** : 2026-02-23, session Opus 4.6 (4 replays, pivot TREND-ONLY)

---

## ⭐ BOUSSOLE STRATÉGIQUE — Immuable

```
OBJECTIF : gains petits, fréquents, consistants.
           Win Rate élevé (cible ≥ 70%, idéal ≥ 85%).
           Expectancy positive par le volume, pas par des grands mouvements rares.
STRATÉGIE : TREND-ONLY sur instruments Dukascopy (forex + métaux).
```

---

## 1. Résultats cumulés — 4 replays, 2 périodes

| Version | Période | Univers | N | WR | Exp | Total R | Score |
|---|---|---|---|---|---|---|---|
| v3.3 combined (BE 0.5/0.25) | Oct→Jan | crypto 17 | 998 | 60% | +0.034 | +33.5 | ? |
| v3.3 combined (BE 0.3) | Oct→Jan | diversifié 46 | 319 | 64% | -0.044 | -13.9 | 1/5 |
| Replay A: combined crypto | Avr→Jul | crypto 17 | 169 | 65% | -0.083 | -14.1 | 1/5 |
| **Replay B: TREND diversifié** | **Avr→Jul** | **diversifié 39** | **570** | **71%** | **+0.037** | **+21.2** | **3/5** |

**Replay B décomposé par source de données** (la découverte clé) :

| Source | Instruments | N | WR | Exp | Total R | Spikes |
|---|---|---|---|---|---|---|
| **Dukascopy (forex+metals)** | **19** | **229** | **79%** | **+0.128** | **+29.3** | **0** |
| CCXT (crypto) | 12 | 223 | 67% | -0.050 | -11.0 | 2 |
| Sans 1min (indices/energy/agri) | 8 | 118 | 66% | +0.025 | +3.0 | 5 |

---

## 2. Leçons majeures — IMMUTABLES

### L1 : ROI court-terme + SL réel = piège mortel
BB_RPB_TSL a SL=-99% (jamais touché). Arabesque a SL=-1R.
ROI court → avg_win effondré → Exp négative. **Ne plus jamais utiliser de ROI CT.**

### L2 : Le BE est LE levier principal du WR
WR ≈ % des trades atteignant le trigger MFE.
~75% des trades trend atteignent MFE ≥ 0.3R → WR ~75%.

### L3 : Mean Reversion ne fonctionne pas avec nos paramètres
MR perd sur TOUTES les catégories (crypto -35R, forex -7R, commo -2R)
et TOUS les sub-types. Cause : 29% des signaux MR n'atteignent jamais
+0.3R MFE (le rebond Bollinger ne se produit pas).

### L4 : Trend gagne partout — mais surtout sur Dukascopy
Trend forex Dukascopy : WR 79%, Exp +0.128R, 0 spikes.
Trend crypto CCXT : WR 67%, Exp -0.050R (net négatif).
**La qualité des données est aussi importante que la stratégie.**

### L5 : Les spikes de données corrompent les résultats
TOUS les spikes (MFE > 10R sur 1 barre) viennent d'instruments sans
données 1-minute fiables (indices, energy, agri via Yahoo).

### L6 : Anti-biais — les règles non négociables
- Signal bougie `i`, exécution open bougie `i+1`
- Si SL ET TP touchés sur même bougie → SL pris (pessimiste)
- Le MFE ne prédit pas l'ordre intrabar

### L7 : BE offset 0.15R est trop serré
323/339 trailing exits étaient des BE à exactement +0.15R.
Offset 0.20R donne +0.05R par trade × 323 = +16R net supplémentaire.

### L8 : Le rapport deep-research confirme notre direction
Trend/swing following = stratégie la plus compatible prop firms (overnight).
Points d'action restants : daily loss limit, kill switch, news filter.

---

## 3. Configuration v3.3 détaillée

### Entrées (signal_gen.py)
- BB période 20, std 2.0, source typical_price (H+L+C)/3
- RSI 14, oversold=35, overbought=65
- SL : swing 10 bars, fallback 1.5 ATR, min 1.5 ATR

### Sorties (manager.py)
- **BE** : trigger=0.3R, offset=0.20R
- **Giveback** : MFE≥0.5R, current<0.15R, RSI<46, CMF<0
- **Trailing** : 3 paliers (≥1.5R:0.7R, ≥2.0R:1.0R, ≥3.0R:1.5R)
- **ROI** : backstop (0:3.0R, 240:0.15R)

---

## 4. Prochaines étapes

### P1 : TREND-ONLY Dukascopy, Oct→Jan (VALIDATION CROISÉE) — PRIORITÉ

```bash
cd ~/dev/arabesque && git pull
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy trend --balance 100000 \
  --data-root ~/dev/barres_au_sol/data \
  --instruments EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF NZDUSD \
    EURGBP EURJPY GBPJPY AUDJPY EURCAD AUDCAD GBPCAD \
    USDMXN USDZAR USDSGD XAUUSD XAGUSD
python scripts/analyze_replay_v2.py dry_run_XXXXXXXX_XXXXXX.jsonl --grid
```

Cibles : WR ≥ 70%, Exp ≥ +0.05R, score prop ≥ 3/5

### P2 : Données 1-minute pour lever le biais pessimiste intrabar
### P3 : Moteur de risque prop firm (daily loss limit, kill switch, news filter)
### P4 : Walk-forward structuré (4+ fenêtres glissantes)

---

## 5. Restrictions

**⛔ Opus 4.6** : manager.py, signal_gen.py, guards.py, indicators.py, décisions stratégiques.
**✅ Intermédiaire** : exécuter replay, analyze_replay_v2.py, diagnostics data.
