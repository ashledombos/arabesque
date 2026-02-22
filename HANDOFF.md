# ARABESQUE — Handoff v10
## Pour reprendre le développement dans un nouveau chat

> **Repo** : https://github.com/ashledombos/arabesque  
> **Dernière mise à jour** : 2026-02-22 (session Opus 4.6 — v3.3)

---

## ⭐ BOUSSOLE STRATÉGIQUE — Immuable

```
OBJECTIF : gains petits, fréquents, consistants.
           Win Rate élevé (cible : ≥ 70%, idéal ≥ 85%).
           Expectancy positive par le volume, pas par des grands mouvements rares.
```

---

## Historique et résultats

| Version | WR | Exp/tr | Total R | Trades | Changement clé |
|---|---|---|---|---|---|
| v2 base | 52.0% | +0.035R | +27.5R | 786 | Original |
| **v3.0** | **50.6%** | **+0.094R** | **+73.9R** ✅ | 786 | ROI backstop, trailing 3t, SL 1.5 |
| v3.1 | 63.9% | -0.004R | -2.3R ❌ | 568 | BB tp, RSI 30, ROI court, BE 0.05 |
| v3.2 | 60.6% | -0.010R | -6.4R ❌ | 622 | BE 0.25, SL retour 1.5 |
| **v3.3** | **?** | **?** | **?** | ~786? | **v3.0 + BB tp + BE 0.5/0.25 + giveback 0.5** |

### LEÇON MAJEURE v3.1/v3.2

**ROI court-terme + SL réel = piège mortel pour l'expectancy.**

BB_RPB_TSL a SL = -99% (jamais touché) → peut couper les profits tôt sans risque.
Arabesque a SL = -1R (prop firm obligatoire) → chaque SL doit être compensé.

Formule : `Exp = WR × avg_win - (1-WR) × 1.0`
- WR=60% → avg_win minimum = 0.667R (v3.1 avait 0.548 → négatif)
- WR=65% → avg_win minimum = 0.538R
- WR=50% → avg_win minimum = 1.000R (v3.0 avait 1.14 → positif)

### Stratégie v3.3 : v3.0 (rentable) + 3 améliorations chirurgicales

| Paramètre | v3.0 | v3.3 | Raison |
|---|---|---|---|
| BB source | Close | **typical_price** | Alignement BB_RPB_TSL |
| BE trigger/offset | 1.0R/0.05R | **0.5R/0.25R** | 39% losers avaient MFE ≥ 0.5R |
| Giveback MFE | 1.0R | **0.5R** | Capture profits qui érodent |
| RSI | 35 | 35 | Inchangé (30 filtrait trop) |
| min_bb_width | 0.003 | 0.003 | Inchangé (0.02 filtrait trop) |
| SL | 1.5 ATR | 1.5 ATR | Inchangé |
| ROI | 3 tiers (48/120/240h) | 2 tiers (0/240h) | Simplifié, pas agressif |
| Trailing | 3 tiers (≥1.5R) | 3 tiers (≥1.5R) | Inchangé |

---

## Prochaine étape : P3a-quater — Replay v3.3

```bash
cd ~/dev/arabesque && git pull
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay.py dry_run_*.jsonl  # le plus récent
```

**Métriques attendues vs v3.0 :**
- Trades : ~786 (même RSI/bb_width que v3.0)
- WR : > 50.6% (BE convertit des losers)
- avg_win : ~1.0R (pas de ROI court)
- Exp : > +0.094R
- Total R : > +73.9R

---

## Restrictions par niveau IA

### ⛔ Réservé Opus 4.6
`manager.py`, `signal_gen*.py`, `guards.py`, `indicators.py`

### ✅ Modèle intermédiaire
Replay P3a-quater, analyse résultats. Voir `docs/RESUME_PROMPT.md`
