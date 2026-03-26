#!/usr/bin/env bash
# Fetch incrémental des données OHLC — appelé par le timer systemd user.
# Met à jour depuis 4 jours avant aujourd'hui (overlap pour éviter les trous)
# et dérive les timeframes H1 et H4.
set -e

cd "$(dirname "$0")/.."

START=$(date -d "4 days ago" +%Y-%m-%d)
END=$(date +%Y-%m-%d)

echo "[$(date -u +%FT%TZ)] arabesque-fetch: $START → $END"
.venv/bin/python -m arabesque.data.fetch \
    --start "$START" \
    --end   "$END"   \
    --derive 1h 4h
echo "[$(date -u +%FT%TZ)] arabesque-fetch: terminé"
