# PROMPT DE REPRISE — Arabesque (v3.3)

> Destiné à un modèle intermédiaire. Créé 2026-02-22.

## Lire : `HANDOFF.md`

## Tâche : Replay P3a-quater

```bash
cd ~/dev/arabesque && git pull
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay.py dry_run_*.jsonl
```

**Comparer à v3.0** : WR=50.6%, exp=+0.094R, total=+73.9R, 786 trades
On attend : WR > 50.6%, exp > +0.094R, total > +73.9R.

## ⛔ NE PAS MODIFIER de fichiers code
