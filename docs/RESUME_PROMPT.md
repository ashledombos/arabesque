# PROMPT DE REPRISE — Arabesque (v3.3, BE 0.3/0.15)

> Destiné à un modèle intermédiaire. Créé 2026-02-22.

## Lire : `HANDOFF.md` (obligatoire avant toute action)

## Tâche : Replay v3.3 (BE 0.3/0.15)

```bash
cd ~/dev/arabesque && git pull

# Replay (un seul run)
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data

# Analyse — UTILISER LE FICHIER LE PLUS RÉCENT, PAS LE GLOB *
python scripts/analyze_replay_v2.py dry_run_20XXXXXX_XXXXXX.jsonl --grid
```

⚠️ **`analyze_replay.py` est cassé.** Utiliser **`analyze_replay_v2.py`**.
⚠️ Passer **un seul fichier JSONL**, pas `dry_run_*.jsonl`.

## Métriques à comparer

| Métrique | v3.3 (BE 0.5/0.25) | Cible (BE 0.3/0.15) |
|---|---|---|
| WR | 60.2% | ≥ 70% |
| Expectancy | +0.034R | ≥ +0.10R |
| Total R | +33.5R | ≥ +100R |
| Trades | 998 | ~998 |
| Score prop | ? | ≥ 3/5 |

## ⛔ NE PAS MODIFIER de fichiers code
## ⛔ NE PAS interpréter les résultats pour décider de modifier la stratégie
