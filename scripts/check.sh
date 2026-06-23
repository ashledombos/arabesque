#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Garde-fou hygiène Arabesque — à lancer avant tout commit (humain ou agent).
#
# Couvre le scénario « un ajout/retrait casse le code » :
#   - ruff F821 (nom non défini = référence dangle après un retrait de module/import)
#   - ruff F811 (redéfinition)
#   - pytest  (régression de comportement)
#
# Usage : scripts/check.sh        (ou via le pre-commit hook / la CI)
# Bypass ponctuel d'un commit : git commit --no-verify
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

# Préférer le venv local, sinon retomber sur le PATH (CI, autre machine).
PY=".venv/bin/python";  [ -x "$PY" ]   || PY="python"
RUFF=".venv/bin/ruff";  [ -x "$RUFF" ] || RUFF="ruff"

echo "▶ ruff — codes de rupture (F821 undefined-name, F811 redefined)…"
"$RUFF" check arabesque/ --select F821,F811

echo "▶ pytest — suite complète…"
"$PY" -m pytest tests/ -q

echo "✅ check OK — sûr de commiter."
