# ARABESQUE — Handoff v11
## Pour reprendre le développement dans un nouveau chat

> **Repo** : https://github.com/ashledombos/arabesque  
> **Dernière mise à jour** : 2026-02-22, session Opus 4.6 (v3.3 final)

---

## ⭐ BOUSSOLE STRATÉGIQUE — Immuable

```
OBJECTIF : gains petits, fréquents, consistants.
           Win Rate élevé (cible : ≥ 70%, idéal ≥ 85%).
           Expectancy positive par le volume, pas par des grands mouvements rares.
```

---

## 1. Résultats cumulés (tous sur Oct 2025 → Jan 2026, combined, 17 instruments)

| Version | WR | Exp/trade | Total R | Trades | Config clé | Statut |
|---|---|---|---|---|---|---|
| v2 base | 52.0% | +0.035R | +27.5R | 786 | Original | ✅ positif |
| v3.0 | 50.6% | +0.094R | +73.9R | 786 | ROI backstop, SL 1.5 | ✅ meilleur |
| v3.1 | 63.9% | -0.004R | -2.3R | 568 | ROI court, RSI 30, SL 2.0 | ❌ |
| v3.2 | 60.6% | -0.010R | -6.4R | 622 | BE 0.25, SL 1.5 | ❌ |
| v3.3 (BE 0.5/0.25) | 60.2% | +0.034R | +33.5R | 998 | BB tp, BE 0.5/0.25, giveback 0.5 | ✅ |
| **v3.3 (BE 0.3/0.15)** | **~80%?** | **~+0.25R?** | **~+250R?** | ~998 | **BE 0.3/0.15** | **⏳ à tester** |

### Résultats des 3 replays de validation (2026-02-23)

| Replay | Config | N | WR | Exp | Total | DD | Score |
|---|---|---|---|---|---|---|---|
| 1 | Combined crypto BE0.3 | 999 | 68.6% | +0.050R | +49.9R | 14.1% | 3/5 |
| 2 | **Trend diversifié** | **429** | **70.9%** | **+0.065R** | **+27.7R** | **8.8%** | **2/5** |
| 3 | MR-only crypto | 29 | 31% | -0.374R | -10.9R | 5.8% | INVALIDE |

**Replay 3 invalide** : `BacktestSignalGenerator` ≠ MR dans `CombinedSignalGenerator` (29 vs 873 trades).

---

## 2. Leçons majeures — IMMUTABLES

### L1 : ROI court-terme + SL réel = piège mortel
BB_RPB_TSL a SL=-99% (jamais touché). Arabesque a SL=-1R (prop firm).
Avec SL=-1R : `Exp = WR × avg_win - (1-WR) × 1.0`.
ROI court → coupe les winners tôt → avg_win < seuil → Exp négative.
**Ne plus jamais utiliser de ROI court-terme.**

### L2 : Le BE est LE levier principal du WR
Le mécanisme BE crée un filtre binaire :
- MFE < trigger → SL hit → -1R
- MFE ≥ trigger → exit à offset (si comeback) ou plus (si continuation)
- **WR ≈ % des trades atteignant le trigger MFE**

### L3 : Données montrant la robustesse de BE 0.3/0.15
Testé sur 2 datasets indépendants (entrées v3.0 et v3.3) :
- **80% des trades atteignent MFE ≥ 0.3R** → WR ~80%
- 26% reviennent sous 0.15R (BE exit) / 74% continuent (avg +0.71R)
- Expectancy analytique : +0.250R
- 0.15R offset = 0.225 ATR de marge (3× plus que le 0.05R qui avait échoué)

### L4 : BB typical_price génère ~27% plus de signaux
Passage de Close → (H+L+C)/3 a fait passer de 786 → 998 trades.
Les signaux supplémentaires sont de qualité comparable.

### L5 : La diversification d'instruments est CRITIQUE
17 instruments actuels = 16 cryptos + 1 or. Corrélation très élevée.
Les pires journées voient TOUS les cryptos perdre simultanément.
Ajouter forex/indices réduirait le DD de manière significative.

### L6 : Anti-biais — les règles non négociables
- Signal bougie `i`, exécution open bougie `i+1` (jamais pendant la bougie)
- Si SL ET TP touchés sur la même bougie → SL pris (pessimiste)
- Même code backtest et live (pas de divergence)
- Le MFE ne prédit pas l'ordre intrabar (high ou low en premier inconnu)

### L7 : MR perd partout sur diversifié, Trend gagne partout
Test 46 instruments (crypto/forex/commodities/indices) — 319 trades :
- MR : 256 trades, -41.8R (perd sur TOUTES catégories, TOUS sub-types)
- Trend : 63 trades, +27.8R, WR 84% (gagne sur TOUTES catégories)
- **MR LONG spécifiquement = -46.1R** (le gouffre principal)
- Cause racine : 29% des trades MR n'atteignent jamais +0.3R MFE (mauvaise entrée)
- **La stratégie optimale sera probablement MR-crypto + Trend-tout**

### L8 : Les spikes de données sont le bottleneck n°1
Sur Replay 1, 124 SL losers (41%) avaient MFE ≥ 0.3R — TOUS sur 1 seule barre.
Ce sont des bougies H1 aberrantes (range 7R+) où la règle pessimiste prend le SL.
Impact : ~143R perdus. Avec données 1-min, on saurait si high ou low arrive en premier.
**Résoudre les spikes de données est plus important qu'optimiser les paramètres.**

### L9 : Trend standalone libère +303 signaux
En mode combined, MR occupe des slots → seulement 126 trend exécutés sur 429 possibles.
Trend seul : 429 trades, WR 71%, DD 8.8% (le meilleur DD de tous les replays).

### L10 : SHORT >> LONG sur Oct-Jan (biais à vérifier)
Constant dans les 3 replays. Peut être saisonnier (risk-off hivernal)
ou structurel (entrées LONG sur faux rebonds). **Tester sur une autre période avant d'agir.**

---

## 3. Configuration v3.3 détaillée

### Entrées (signal_gen.py)
- BB période 20, std 2.0, **source typical_price** (H+L+C)/3
- RSI 14, oversold=35, overbought=65
- SL : swing 10 bars, fallback 1.5 ATR, min 1.5 ATR
- min_bb_width=0.003, min_rr=0.5

### Sorties (manager.py)
- **BE** : trigger=0.3R, offset=0.15R (levier principal WR)
- **Giveback** : MFE≥0.5R, current<0.15R, RSI<46, CMF<0 → close
- **Trailing** : 3 paliers (MFE≥1.5R:0.7R, ≥2.0R:1.0R, ≥3.0R:1.5R)
- **ROI** : backstop seulement (0:3.0R, 240:0.15R)
- **Deadfish** : 24 bars, MFE<0.5R, BB width < 0.005
- **Time-stop** : 336 barres (14j backstop)

### Indicateurs (indicators.py)
- BB : **typical_price** (H+L+C)/3
- RSI : Wilder's smoothing (correct)
- ATR : Wilder's smoothing (correct)

---

## 4. Prochaines étapes

### P1 : Replay sur période différente — PRIORITÉ ABSOLUE
Tester la même config sur Avr-Jul 2025 ou Jul-Oct 2025.
But : vérifier si le biais SHORT est saisonnier ou structurel.
Si SHORT domine aussi en été → problème structurel LONG à investiguer.
Si LONG/SHORT équilibrés en été → biais saisonnier, ne pas filtrer.

```bash
python -m arabesque.live.engine \
  --source parquet --start 2025-04-01 --end 2025-07-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay_v2.py dry_run_XXX.jsonl --grid
```

```bash
python -m arabesque.live.engine \
  --source parquet --start 2025-04-01 --end 2025-07-01 \
  --strategy trend --balance 100000 \
  --data-root ~/dev/barres_au_sol/data \
  --instruments EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF NZDUSD \
    EURGBP EURJPY GBPJPY AUDJPY EURCAD AUDCAD GBPCAD \
    USDMXN USDZAR USDSGD \
    XAUUSD XAGUSD XPTUSD XCUUSD \
    USOIL.cash UKOIL.cash NATGAS.cash \
    WHEAT.c CORN.c COCOA.c \
    BTCUSD ETHUSD LTCUSD BNBUSD BCHUSD SOLUSD \
    XRPUSD ADAUSD AVAUSD NERUSD DOTUSD ALGUSD
python scripts/analyze_replay_v2.py dry_run_XXX.jsonl --grid
```

### P2 : Intégration données 1-minute
Les données 1-min permettraient de résoudre l'ambiguïté intrabar.
Impact estimé : 124 trades correctement classifiés → ~70-100R récupérés.
Question à poser : ces données sont-elles disponibles dans barres_au_sol ?

### P3 : Filtre spike dans le pipeline
Écarter les bougies dont le range (high-low) dépasse X × ATR_14.
À implémenter dans le replay engine (pre-processing des données).

### P4 : Connexion FTMO test (seulement si Trend score ≥ 3/5 sur 2 périodes)

---

## 5. Scripts

| Script | Usage |
|---|---|
| `scripts/analyze_replay_v2.py FILE` | Analyse complète d'un replay |
| `scripts/analyze_replay_v2.py FILE --grid` | + grille simulation BE/TP |
| `scripts/analyze_replay_v2.py FILE --compare FILE2` | Comparaison 2 replays |

---

## 6. Restrictions par niveau IA

### ⛔ Réservé Opus 4.6
- `position/manager.py`, `signal_gen*.py`, `guards.py`, `indicators.py`
- Tout changement de paramètre affectant WR ou expectancy
- Interprétation des résultats replay (décision de modifier la stratégie)

### ✅ Modèle intermédiaire
- Exécuter replay, collecter résultats
- Lancer `analyze_replay_v2.py`
- Diagnostics data (spikes parquet)
- Ajout d'instruments au pipeline `barres_au_sol`
- Voir `docs/RESUME_PROMPT.md`
