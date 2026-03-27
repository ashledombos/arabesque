#!/usr/bin/env bash
# Installe les services systemd user pour Arabesque :
#   - arabesque-live.service   : moteur de trading (persistent, auto-restart)
#   - arabesque-fetch.timer    : fetch OHLC quotidien à 06:30 UTC
#
# Usage :
#   bash scripts/install_service.sh          # installe tout
#   bash scripts/install_service.sh fetch    # fetch uniquement
#   bash scripts/install_service.sh live     # live uniquement
#   bash scripts/install_service.sh reports  # rapports quotidien + hebdo
#
# Prérequis :
#   sudo loginctl enable-linger "$USER"   # pour que les services tournent hors session
set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
WHAT="${1:-all}"

echo "Repo : $REPO_DIR"
echo "Dest : $SYSTEMD_USER_DIR"

mkdir -p "$SYSTEMD_USER_DIR"

# --- Fetch timer ---
if [[ "$WHAT" == "all" || "$WHAT" == "fetch" ]]; then
    sed "s|{{REPO_DIR}}|$REPO_DIR|g" \
        "$REPO_DIR/deploy/systemd/arabesque-fetch.service.template" \
        > "$SYSTEMD_USER_DIR/arabesque-fetch.service"

    cp "$REPO_DIR/deploy/systemd/arabesque-fetch.timer" \
       "$SYSTEMD_USER_DIR/arabesque-fetch.timer"

    systemctl --user daemon-reload
    systemctl --user enable arabesque-fetch.timer
    systemctl --user start  arabesque-fetch.timer

    echo ""
    echo "✅ Fetch timer installé et démarré."
    systemctl --user list-timers arabesque-fetch.timer
fi

# --- Live engine ---
if [[ "$WHAT" == "all" || "$WHAT" == "live" ]]; then
    sed "s|{{REPO_DIR}}|$REPO_DIR|g" \
        "$REPO_DIR/deploy/systemd/arabesque-live.service.template" \
        > "$SYSTEMD_USER_DIR/arabesque-live.service"

    systemctl --user daemon-reload
    systemctl --user enable arabesque-live.service

    echo ""
    echo "✅ Live engine installé et activé."
    echo ""
    echo "Commandes utiles :"
    echo "  systemctl --user start arabesque-live     # démarrer"
    echo "  systemctl --user stop arabesque-live      # arrêter"
    echo "  systemctl --user restart arabesque-live   # redémarrer"
    echo "  systemctl --user status arabesque-live    # statut"
    echo "  journalctl --user -u arabesque-live -f    # logs en direct"
    echo "  journalctl --user -u arabesque-live --since '1 hour ago'  # dernière heure"
fi

# --- Report timers ---
if [[ "$WHAT" == "all" || "$WHAT" == "reports" ]]; then
    for typ in daily weekly; do
        sed "s|{{REPO_DIR}}|$REPO_DIR|g" \
            "$REPO_DIR/deploy/systemd/arabesque-report-${typ}.service.template" \
            > "$SYSTEMD_USER_DIR/arabesque-report-${typ}.service"

        cp "$REPO_DIR/deploy/systemd/arabesque-report-${typ}.timer" \
           "$SYSTEMD_USER_DIR/arabesque-report-${typ}.timer"
    done

    systemctl --user daemon-reload
    systemctl --user enable arabesque-report-daily.timer arabesque-report-weekly.timer
    systemctl --user start  arabesque-report-daily.timer arabesque-report-weekly.timer

    echo ""
    echo "✅ Report timers installés et démarrés."
    systemctl --user list-timers 'arabesque-report-*'
fi

echo ""
echo "⚠️  Si les services doivent tourner hors session active :"
echo "    sudo loginctl enable-linger \$USER"
