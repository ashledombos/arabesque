#!/usr/bin/env bash
# Installe le service systemd user pour le fetch OHLC quotidien.
#
# Usage :
#   bash scripts/install_service.sh
#
# Prérequis :
#   sudo loginctl enable-linger "$USER"   # pour que le timer tourne hors session
set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "Repo : $REPO_DIR"
echo "Dest : $SYSTEMD_USER_DIR"

mkdir -p "$SYSTEMD_USER_DIR"

# Générer le .service depuis le template (substitue {{REPO_DIR}})
sed "s|{{REPO_DIR}}|$REPO_DIR|g" \
    "$REPO_DIR/deploy/systemd/arabesque-fetch.service.template" \
    > "$SYSTEMD_USER_DIR/arabesque-fetch.service"

# Copier le timer (pas de substitution nécessaire)
cp "$REPO_DIR/deploy/systemd/arabesque-fetch.timer" \
   "$SYSTEMD_USER_DIR/arabesque-fetch.timer"

systemctl --user daemon-reload
systemctl --user enable arabesque-fetch.timer
systemctl --user start  arabesque-fetch.timer

echo ""
echo "✅ Timer installé et démarré."
systemctl --user list-timers arabesque-fetch.timer
echo ""
echo "⚠️  Si le timer doit tourner hors session active :"
echo "    sudo loginctl enable-linger \$USER"
