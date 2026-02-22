# PROMPT DE REPRISE — Arabesque (v3.3, BE 0.3/0.15)

> Destiné à un modèle intermédiaire. Créé 2026-02-22.

## Lire : `HANDOFF.md` (obligatoire avant toute action)

## Tâche : 3 REPLAYS DE VALIDATION

### Replay 1 : BE 0.3/0.15 sur crypto-only

```bash
cd ~/dev/arabesque && git pull
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay_v2.py dry_run_XXXXXXXX_XXXXXX.jsonl --grid
```
Comparer à v3.3 (BE 0.5/0.25): WR=60.2%, exp=+0.034R, total=+33.5R, 998 trades.
Cible: WR≥70%, exp≥+0.10R.

### Replay 2 : TREND-ONLY sur diversifié

```bash
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
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
python scripts/analyze_replay_v2.py dry_run_XXXXXXXX_XXXXXX.jsonl --grid
```
Baseline trend: 63 trades, WR=84%, exp=+0.442R.

### Replay 3 : MR-ONLY sur crypto

```bash
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy mean_reversion --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay_v2.py dry_run_XXXXXXXX_XXXXXX.jsonl --grid
```
But: confirmer que MR fonctionne sur crypto et pas ailleurs.

⚠️ **Utiliser `analyze_replay_v2.py`** — un seul fichier JSONL à la fois.

## ⛔ NE PAS MODIFIER de fichiers code
## ⛔ NE PAS interpréter les résultats pour décider de modifier la stratégie
