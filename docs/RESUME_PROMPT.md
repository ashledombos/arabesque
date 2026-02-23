# PROMPT DE REPRISE — Arabesque (v3.3, TREND-ONLY, BE 0.3/0.20)

> Destiné à un modèle intermédiaire. Créé 2026-02-23.

## Lire : `HANDOFF.md` (obligatoire avant toute action)

## Tâche : VALIDATION CROISÉE — Trend Dukascopy, 2e période

Le Replay B (Avr→Jul) a montré que trend Dukascopy = WR 79%, +29.3R.
Il faut valider sur la 2e période (Oct→Jan) pour confirmer la robustesse.

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

## Métriques à comparer

| Métrique | Replay B (Avr→Jul, Dukascopy) | Cible (Oct→Jan) |
|---|---|---|
| WR | 79% | ≥ 70% |
| Expectancy | +0.128R | ≥ +0.05R |
| Total R | +29.3R | > 0R |
| Spikes | 0 | 0 |
| Score prop | 3/5 | ≥ 3/5 |

⚠️ Utiliser `analyze_replay_v2.py` (un seul fichier JSONL, pas de glob `*`).

## ⛔ NE PAS MODIFIER de fichiers code
## ⛔ NE PAS interpréter les résultats pour décider de modifier la stratégie
