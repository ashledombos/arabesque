# PROMPT DE REPRISE — Arabesque (v3.3, BE 0.3/0.15)

> Destiné à un modèle intermédiaire. Créé 2026-02-23.

## Lire : `HANDOFF.md` (obligatoire avant toute action)

## Tâche : Replays sur période Avr-Jul 2025

But : vérifier si le biais SHORT (constant sur Oct-Jan) persiste ou est saisonnier.

### Replay A : Combined crypto, période différente

```bash
cd ~/dev/arabesque && git pull
python -m arabesque.live.engine \
  --source parquet --start 2025-04-01 --end 2025-07-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay_v2.py dry_run_XXXXXXXX_XXXXXX.jsonl --grid
```

### Replay B : Trend diversifié, période différente

```bash
python -m arabesque.live.engine \
  --source parquet --start 2025-04-01 --end 2025-07-01 \
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

## Métriques clés à reporter

| Métrique | Oct-Jan (ref) | Avr-Jul |
|---|---|---|
| LONG total R | -14.4R (combined) / -3.2R (trend) | ? |
| SHORT total R | +64.3R (combined) / +30.9R (trend) | ? |
| WR global | 68.6% / 70.9% | ? |
| Exp | +0.050R / +0.065R | ? |
| Total R | +49.9R / +27.7R | ? |

⚠️ **Utiliser `analyze_replay_v2.py`** — un seul fichier JSONL à la fois.

## ⛔ NE PAS MODIFIER de fichiers code
