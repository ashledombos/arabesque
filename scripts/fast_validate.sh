#!/usr/bin/env bash
# fast_validate.sh — Backtest rapide sur un mini-basket représentatif.
#
# Usage:
#   ./scripts/fast_validate.sh [period] [data-root]
#
# Périodes prédéfinies:
#   3m    = Oct 2025 - Jan 2026 (3 mois, rapide)
#   6m    = Jul 2025 - Jan 2026 (6 mois, modéré)
#   full  = Jul 2024 - Mar 2026 (20 mois, long)
#
# Le basket est choisi pour représenter :
#   - Crypto USD-quoted (la majorité des trades live)
#   - Forex majors (USD/XXX pour tester la conversion devise)
#   - Forex cross (pour tester yaml-cross fallback)
#   - Métaux (diversification)

set -euo pipefail

PERIOD="${1:-3m}"
DATA_ROOT="${2:-$HOME/dev/barres_au_sol/data}"

# === BASKET REPRÉSENTATIF (10 instruments) ===
# Crypto (5) — les plus actifs en live :
#   BTCUSD, ETHUSD, SOLUSD, BNBUSD, LNKUSD
# Forex (4) — couvrent les 3 types de conversion :
#   EURUSD (XXX/USD direct), USDJPY (USD/XXX division),
#   GBPUSD (XXX/USD), NZDCAD (cross → yaml)
# Métal (1) :
#   XAUUSD
BASKET="BTCUSD ETHUSD SOLUSD BNBUSD LNKUSD ICPUSD EURUSD USDJPY GBPUSD NZDCAD XAUUSD"

case "$PERIOD" in
    3m)
        START="2025-10-01"
        END="2026-01-01"
        DESC="3 mois (Oct-Dec 2025)"
        ;;
    6m)
        START="2025-07-01"
        END="2026-01-01"
        DESC="6 mois (Jul-Dec 2025)"
        ;;
    full)
        START="2024-07-01"
        END="2026-03-03"
        DESC="20 mois (Jul 2024 - Mar 2026)"
        ;;
    *)
        echo "Période inconnue: $PERIOD (utiliser 3m, 6m ou full)"
        exit 1
        ;;
esac

echo "============================================================"
echo "VALIDATION RAPIDE — $DESC"
echo "Basket: $BASKET"
echo "Data:   $DATA_ROOT"
echo "============================================================"
echo ""

# Vérifier que les données existent
for sym in $BASKET; do
    DIR="$DATA_ROOT/$sym"
    if [ ! -d "$DIR" ]; then
        echo "⚠️  Données manquantes: $DIR"
    fi
done

echo ""
echo "Commande:"
echo "  python -m arabesque.live.engine \\"
echo "    --source parquet \\"
echo "    --start $START \\"
echo "    --end $END \\"
echo "    --strategy trend \\"
echo "    --balance 100000 \\"
echo "    --data-root $DATA_ROOT \\"
echo "    --instruments $BASKET"
echo ""

# Lancer
exec python -m arabesque.live.engine \
    --source parquet \
    --start "$START" \
    --end "$END" \
    --strategy trend \
    --balance 100000 \
    --data-root "$DATA_ROOT" \
    --instruments $BASKET
