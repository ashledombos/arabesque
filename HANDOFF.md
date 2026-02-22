# ARABESQUE — Handoff v9
## Pour reprendre le développement dans un nouveau chat

> **Repo** : https://github.com/ashledombos/arabesque  
> **Branche principale** : `main`  
> **Dernière mise à jour** : 2026-02-22 (session Opus 4.6 — v3.2)

---

## ⭐ BOUSSOLE STRATÉGIQUE — Immuable

```
OBJECTIF : gains petits, fréquents, consistants.
           Win Rate élevé (cible : ≥ 70%, idéal ≥ 85%).
           Expectancy positive par le volume, pas par des grands mouvements rares.
```

---

## Historique des versions et résultats

| Version | WR | Exp/trade | Total R | Trades | Changement clé |
|---|---|---|---|---|---|
| v2 (baseline) | 52.0% | +0.035R | +27.5R | 786 | Original |
| v3.0 | 50.6% | +0.094R | +73.9R | 786 | ROI 48/120/240h, trailing 3 paliers, SL 1.5ATR |
| v3.1 | **63.9%** | **-0.004R** | -2.3R | 568 | BB typical_price, RSI 30, ROI 6/12/24h, BE 0.5R, SL 2.0ATR |
| v3.2 (proj.) | ~60%? | **+0.190R?** | +107R? | ~568 | BE offset 0.25R, SL retour 1.5ATR |

### Diagnostic v3.1 → v3.2

v3.1 a gagné +13 pts de WR mais perdu l'expectancy. Cause : **165 trades (29%) sortent à +0.05R** (BE exits phantômes). Le SL après BE est trop proche de l'entrée, touché par le bruit OHLC normal. En parallèle, SL 2.0 ATR rend R trop grand, comprimant tous les gains en R-multiples.

Deux corrections :
1. **BE offset 0.05R → 0.25R** : chaque BE exit donne +0.25R au lieu de +0.05R
2. **SL 2.0 → 1.5 ATR** : R plus petit → même mouvement en $ = plus de R

---

## Fichiers modifiés dans v3.2

| Fichier | Changement |
|---|---|
| `arabesque/position/manager.py` | BE offset 0.05→0.25R |
| `arabesque/backtest/signal_gen.py` | SL 2.0→1.5 ATR |

Fichiers inchangés depuis v3.1 : `indicators.py` (BB typical_price), `models.py` (EXIT_ROI).

---

## Prochaine étape : P3a-ter — Replay v3.2

```bash
cd ~/dev/arabesque && git pull
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay.py dry_run_*.jsonl
```

**Métriques clés à comparer :**

| Métrique | v3.1 | Cible v3.2 |
|---|---|---|
| WR | 63.9% | ≥ 58% (peut baisser car SL plus serré) |
| Expectancy | -0.004R | ≥ +0.10R |
| Total R | -2.3R | ≥ +50R |
| % BE exits à +0.05R | 29% (165 trades) | < 10% |
| EXIT_ROI | 8.5% (48 trades) | ≥ 8% |

**Décision post-replay :**
- Exp ≥ +0.10R ET WR ≥ 58% → succès, passer à P3b
- Exp > 0 mais WR < 55% → SL trop serré, essayer 1.7 ATR
- Exp < 0 → problème structurel, besoin analyse Opus

---

## Restrictions par niveau IA

### ⛔ Réservé Opus 4.6
- `position/manager.py`, `signal_gen*.py`, `guards.py`, `indicators.py`
- Tout changement affectant WR ou expectancy

### ✅ Modèle intermédiaire
- Replay P3a-ter, analyse résultats
- Voir `docs/RESUME_PROMPT.md`
