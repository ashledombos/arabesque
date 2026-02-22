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

### P1 : Replay v3.3 (BE 0.3/0.15) — PRIORITÉ ABSOLUE

```bash
cd ~/dev/arabesque && git pull
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay_v2.py dry_run_20*.jsonl --grid
```

⚠️ **Utiliser `analyze_replay_v2.py`** (pas `analyze_replay.py`).
⚠️ Passer **un seul fichier**, pas `dry_run_*.jsonl` (le glob passe tous les fichiers).

**Cibles :**
| Métrique | v3.3 (BE 0.5/0.25) | Cible BE 0.3/0.15 |
|---|---|---|
| WR | 60.2% | **≥ 70%** |
| Exp | +0.034R | **≥ +0.10R** |
| Total R | +33.5R | **≥ +100R** |
| Score prop | ? | **≥ 3/5** |

### P2 : Diversification instruments
Ajouter au pipeline parquet avec `barres_au_sol` :
- Forex prioritaire : EURUSD, GBPUSD, USDJPY, AUDUSD
- Indices : NAS100, SP500, DAX40
- Commodités : XAGUSD (argent), USOIL
Même période Oct 2025 → Jan 2026 pour comparabilité.

### P3 : Données 1-minute (si P1 montre des résultats prometteurs)
Les données 1-min permettraient de lever l'ambiguïté intrabar :
- Savoir si high ou low est touché en premier dans chaque bougie H1
- Transformer le biais pessimiste en observation réelle
- Impact estimé : +5-15% de trades correctement classifiés

### P4 : Connexion FTMO test (seulement si score prop ≥ 3/5)

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
