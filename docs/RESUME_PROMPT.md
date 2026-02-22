# PROMPT DE REPRISE — Arabesque (post v3.1)

> Destiné à un modèle intermédiaire (Sonnet, GPT-5.2, etc.)
> Créé le 2026-02-21.

## Contexte

Arabesque adapte BB_RPB_TSL (WR 90.8%) aux prop firms FTMO.
Session Opus 4.6 du 2026-02-21 : v3.1 appliquée avec BB sur typical_price,
ROI court-terme, BE abaissé, RSI resserré, SL élargi.

## Fichiers à lire : `HANDOFF.md` puis `docs/decisions_log.md`

## Ce que tu PEUX faire

1. **Replay P3a-bis** :
```bash
cd ~/dev/arabesque && git pull
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay.py dry_run_*.jsonl
```
Rapporter : WR, expectancy, IC95, exit breakdown, WR par durée, score prop firm.

2. **Diagnostic spikes** (P2c) — voir HANDOFF.md
3. **run_stats.py** — collecter résultats
4. **Comparer** mean_reversion vs combined

## ⛔ NE PAS MODIFIER
- `arabesque/position/manager.py`
- `arabesque/backtest/signal_gen*.py`
- `arabesque/indicators.py`
- `arabesque/guards.py`
- `arabesque/models.py`

## Résultats attendus P3a-bis

Comparer au v3.0 :
- v3.0 : WR=50.6%, Exp=+0.094R, EXIT_ROI=2.3%, 786 trades
- v3.1 : ? (attendu : WR ≥ 55%, EXIT_ROI >> 2.3%, moins de SL ≤3 barres)
