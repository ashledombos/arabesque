#!/usr/bin/env python3
"""
Nettoyage des fichiers morts / doublons dans le repo Arabesque.

Créé 2026-02-24 par Opus 4.6 suite à l'audit codebase.
Usage: python scripts/cleanup_dead_files.py [--dry-run]

Par défaut: dry-run (affiche ce qui serait supprimé).
Ajouter --execute pour supprimer réellement.
"""

import os
import sys

# Fichiers confirmés morts (avec justification)
DEAD_FILES = {
    "HANDOVER.md": "remplacé par HANDOFF.md v13",
    "prop_firms.yaml": "doublon de config/prop_firms.yaml",
    "scripts/analyze_replay.py": "remplacé par analyze_replay_v2.py",
    "pine/arabesque_signal.pine": "ère TradingView, stratégie maintenant interne",
    "test_backtest.py": "test obsolète à la racine, plus fonctionnel",
    "test_v2.py": "test obsolète à la racine, plus fonctionnel",
    "docs/plan.md": "supplanté par HANDOFF.md",
    "docs/journal.md": "supplanté par HANDOFF.md + decisions_log.md",
    "docs/START_HERE.md": "supplanté par HANDOFF.md",
    "docs/ROADMAP.md": "supplanté par HANDOFF.md section Prochaines étapes",
}

# Fichiers à évaluer manuellement (pas supprimés automatiquement)
EVALUATE = {
    "arabesque/screener.py": "screener Yahoo — plus utilisé depuis parquet?",
    "docs/ARCHITECTURE.md": "remplacé par docs/LIVE_ARCHITECTURE.md?",
    "docs/TECH_DEBT.md": "dette technique — encore d'actualité?",
    "docs/WORKFLOW_BACKTEST.md": "workflow backtest v1 — encore utile?",
    "docs/instrument_selection_philosophy.md": "supplanté par HANDOFF.md L4?",
    "docs/analysis_categories.md": "catégories d'analyse — encore utile?",
    "config/signal_filters.yaml": "filtres signal — encore utilisé en live?",
    "scripts/run_pipeline.py": "pipeline v1 — encore utilisé?",
    "scripts/update_and_compare.py": "comparaison Yahoo — ère pré-parquet?",
    "scripts/debug_pipeline.py": "debug pipeline v1 — encore utile?",
}


def main():
    dry_run = "--execute" not in sys.argv

    if dry_run:
        print("MODE DRY-RUN (ajouter --execute pour supprimer)\n")
    else:
        print("MODE EXÉCUTION — suppression réelle\n")

    total_bytes = 0
    deleted = 0

    print("=" * 60)
    print("FICHIERS MORTS (suppression confirmée)")
    print("=" * 60)

    for filepath, reason in sorted(DEAD_FILES.items()):
        if os.path.exists(filepath):
            size = os.path.getsize(filepath)
            total_bytes += size
            if dry_run:
                print(f"  [DRY] {filepath:<45} {size:>6}B  ({reason})")
            else:
                os.remove(filepath)
                deleted += 1
                print(f"  [DEL] {filepath:<45} {size:>6}B  ({reason})")
        else:
            print(f"  [---] {filepath:<45}  (déjà absent)")

    print(f"\n  {'Supprimeraient' if dry_run else 'Supprimés'}: {deleted if not dry_run else sum(1 for f in DEAD_FILES if os.path.exists(f))} fichiers, {total_bytes/1024:.0f} KB")

    print(f"\n{'=' * 60}")
    print("FICHIERS À ÉVALUER MANUELLEMENT")
    print("=" * 60)
    for filepath, note in sorted(EVALUATE.items()):
        exists = "✓" if os.path.exists(filepath) else "✗"
        size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        print(f"  [{exists}] {filepath:<50} {size:>6}B  ({note})")

    # Check for empty __pycache__ dirs
    print(f"\n{'=' * 60}")
    print("RÉPERTOIRES __pycache__ (nettoyables avec git clean)")
    print("=" * 60)
    pycache_count = 0
    for root, dirs, files in os.walk('.'):
        if '__pycache__' in dirs:
            pycache_count += 1
    print(f"  {pycache_count} répertoires __pycache__ trouvés")
    print(f"  → git clean -fd __pycache__ pour nettoyer")

    # Clean empty dirs left after deletion
    if not dry_run:
        for dirpath in ['pine']:
            if os.path.isdir(dirpath) and not os.listdir(dirpath):
                os.rmdir(dirpath)
                print(f"\n  [DEL] Répertoire vide supprimé: {dirpath}/")


if __name__ == "__main__":
    main()
