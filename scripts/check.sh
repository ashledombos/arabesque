#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Garde-fou hygiène Arabesque — à lancer avant tout commit (humain ou agent).
#
# Couvre le scénario « un ajout/retrait casse le code » :
#   - ruff complet (config pyproject : E + F — noms non définis, redéfinitions,
#     imports/variables inutilisés) sur arabesque/, scripts/ et tests/.
#     Base assainie le 2026-07-09 (252 → 0 erreurs) : tout nouvel écart bloque.
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

echo "▶ ruff — lint complet (E + F, config pyproject)…"
"$RUFF" check arabesque/ scripts/ tests/

echo "▶ pytest — suite complète…"
"$PY" -m pytest tests/ -q

echo "✅ check OK — sûr de commiter."
