# PROMPT DE REPRISE — Arabesque (post v3.0)

> Ce prompt est destiné à un modèle IA de capacité intermédiaire (Sonnet, GPT-5.2, etc.)
> pour continuer les tâches légères en attendant une session Opus 4.6.
> Créé le 2026-02-21.

## Contexte

Arabesque est un système de trading algorithmique Python pour prop firms.
Il adapte la stratégie BB_RPB_TSL (Freqtrade, WR 90.8% en live) aux marchés FTMO.

**Dernière session (Opus 4.6, 2026-02-21)** : Refonte majeure v3.0 du Position Manager.
Le mécanisme clé de BB_RPB_TSL (`minimal_roi` = TP dégressif dans le temps) a été
identifié et implémenté sous forme de ROI dégressif en R-multiples.

## Fichiers à lire

```
HANDOFF.md                      ← état actuel, prochaines étapes
docs/decisions_log.md           ← historique des décisions (§0 = boussole)
docs/STABLE_vs_FRAGILE.md       ← ce qui peut casser
docs/BB_RPB_TSL_COMPARISON.md   ← écarts vs modèle cible
```

## Ce que tu PEUX faire

1. **Exécuter le replay P3a** et rapporter les résultats :
```bash
cd ~/dev/arabesque
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay.py dry_run_*.jsonl
```
Rapporter : WR, expectancy, IC95, score prop firm, breakdown par exit type.
Chercher spécifiquement combien de trades sont EXIT_ROI vs EXIT_SL vs EXIT_TP.

2. **Diagnostiquer les spikes données** (P2c) :
```bash
python3 -c "
import pandas as pd, os
root = os.path.expanduser('~/dev/barres_au_sol/data/ccxt/derived/')
for f in sorted(os.listdir(root)):
    if not f.endswith('.parquet'): continue
    df = pd.read_parquet(root + f)
    bad = df[(df['high']/df['close'] > 5) | (df['close']/df['low'] > 5)]
    if len(bad): print(f, len(bad), 'barres suspectes')
"
```

3. **Lancer `run_stats.py`** sur les instruments et collecter les résultats.

4. **Comparer mean_reversion vs combined** sur la même période.

## Ce que tu NE DOIS PAS faire

⛔ **Ne JAMAIS modifier ces fichiers :**
- `arabesque/position/manager.py` — architecture de sortie (v3.0 en validation)
- `arabesque/backtest/signal_gen*.py` — logique d'entrée
- `arabesque/guards.py` — protection prop firm
- `arabesque/models.py` — structures de données

⛔ **Ne JAMAIS proposer :**
- De réduire le WR en échange d'un avg_win plus élevé
- D'ajouter du trailing en dessous de +1.5R
- De resserrer le SL en dessous de 1.5 ATR
- D'utiliser `git push --force`

## Si tu trouves un bug

Document le dans HANDOFF.md § "Bugs trouvés en session intermédiaire"
avec : symptôme, fichier concerné, reproduction. NE PAS le corriger.
Marquer "À corriger en session Opus 4.6".

## Résultat attendu de P3a

Après le replay, rapporter ces métriques (copier/coller la sortie) :
- Win Rate (cible ≥ 70%)
- Expectancy en R + IC95
- Score prop firm (cible ≥ 3/4)
- Breakdown exit types (combien EXIT_ROI vs EXIT_SL vs EXIT_TP etc.)
- Top 5 instruments par expectancy
- Top 5 instruments par WR
