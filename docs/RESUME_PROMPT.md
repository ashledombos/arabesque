# PROMPT DE REPRISE — Arabesque (post v3.2)

> Destiné à un modèle intermédiaire. Créé 2026-02-22.

## Lire : `HANDOFF.md` puis `docs/decisions_log.md`

## Tâche : Replay P3a-ter

```bash
cd ~/dev/arabesque && git pull
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay.py dry_run_20*.jsonl  # le plus récent seulement
```

**Rapporter** : WR, expectancy, total R, exit breakdown, % BE exits à +0.25R.

**Comparer à** : v3.1 (WR=63.9%, exp=-0.004R, -2.3R, 165 BE exits à +0.05R)

## ⛔ NE PAS MODIFIER de fichiers code
