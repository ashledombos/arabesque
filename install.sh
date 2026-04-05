#!/usr/bin/env bash
# install.sh — Crée le venv et installe toutes les dépendances d'Arabesque
#
# Trick ctrader/tradelocker :
#   ctrader-open-api → requests==2.32.3
#   tradelocker      → requests==2.32.2  (conflit)
#   Solution : tradelocker --no-deps, puis ses deps non-conflictuelles séparément.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
PIP="$VENV/bin/pip"

echo "▶ Création du venv : $VENV"
python3 -m venv "$VENV"
"$PIP" install --upgrade pip --quiet

echo "▶ Installation des dépendances principales + ctrader-open-api (avec deps)"
"$PIP" install -e "$SCRIPT_DIR[all]"

echo "▶ Installation de tradelocker SANS ses dépendances (conflit requests)"
"$PIP" install --no-deps tradelocker

echo "▶ Installation des dépendances manquantes de tradelocker (versions exactes pincées)"
# tradelocker 0.56.2 pince == pas >=, donc on respecte ses versions exactes.
# requests==2.32.2 est déjà satisfait par requests==2.32.3 (compatible en pratique).
"$PIP" install "PyJWT==2.8.0" "joblib==1.4.2" "python-dotenv==1.0.0"

echo "▶ Installation de service_identity (TLS hostname verification pour Twisted)"
"$PIP" install service-identity

echo "▶ Vérification"
"$VENV/bin/python" -c "
import arabesque
import ctrader_open_api
import tradelocker
import ccxt
import yfinance
import apprise
print('Tous les modules OK')
"

echo ""
echo "✓ Installation terminée."
echo "  Activez l'environnement avec : source .venv/bin/activate"
