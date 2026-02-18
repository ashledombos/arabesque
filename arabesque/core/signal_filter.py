"""
Arabesque — SignalFilter (Phase 1.3).

Charge la matrice d'activation sub_type × catégorie depuis
config/signal_filters.yaml et expose is_allowed() pour le runner
et le signal generator.

Usage ::

    from arabesque.core.signal_filter import SignalFilter

    sf = SignalFilter()                          # charge config/signal_filters.yaml
    sf = SignalFilter("config/signal_filters.yaml")

    sf.is_allowed("mr_deep_wide", "energy")      # True
    sf.is_allowed("mr_shallow_wide", "fx")        # False
    sf.is_allowed("unknown_type", "crypto")       # True  (pass-through si inconnu)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


_DEFAULT_PATH = Path("config/signal_filters.yaml")


class SignalFilter:
    """Filtre d'activation sub_type × catégorie d'instrument.

    Charge un fichier YAML de la forme ::

        signal_filters:
          mr_deep_wide:
            energy: true
            fx: false
            ...

    Règle de sécurité : si un sub_type ou une catégorie n'est pas
    dans le fichier, le signal est autorisé (fail-open) pour éviter
    de bloquer silencieusement de nouveaux sous-types.
    """

    def __init__(self, path: str | Path = _DEFAULT_PATH) -> None:
        self._path = Path(path)
        self._matrix: dict[str, dict[str, bool]] = {}
        self._load()

    # ── Chargement ────────────────────────────────────────────────

    def _load(self) -> None:
        """Charge (ou recharge) le fichier YAML."""
        if not self._path.exists():
            # Pas de fichier = tout autorisé (mode dégradé)
            return

        with open(self._path) as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        filters = raw.get("signal_filters", {})
        self._matrix = {
            sub_type: {cat: bool(v) for cat, v in cats.items()}
            for sub_type, cats in filters.items()
            if isinstance(cats, dict)
        }

    def reload(self) -> None:
        """Recharge le fichier YAML sans redémarrer le process."""
        self._matrix = {}
        self._load()

    # ── API publique ──────────────────────────────────────────────

    def is_allowed(self, sub_type: str, category: str) -> bool:
        """Retourne True si la combinaison sub_type × category est autorisée.

        Args:
            sub_type:  Valeur de Signal.sub_type (ex: "mr_deep_wide")
            category:  Catégorie de l'instrument (ex: "energy", "fx")
                       telle que retournée par _categorize()

        Returns:
            True si autorisé, False si filtré.
            True par défaut si sub_type ou category inconnus (fail-open).
        """
        if not sub_type or not category:
            return True

        sub_map = self._matrix.get(sub_type)
        if sub_map is None:
            # Nouveau sous-type non défini dans le YAML → pass-through
            return True

        return sub_map.get(category, True)

    def allowed_categories(self, sub_type: str) -> list[str]:
        """Retourne la liste des catégories autorisées pour un sub_type."""
        sub_map = self._matrix.get(sub_type, {})
        return [cat for cat, ok in sub_map.items() if ok]

    def allowed_subtypes(self, category: str) -> list[str]:
        """Retourne la liste des sub_types autorisés pour une catégorie."""
        return [
            sub for sub, cats in self._matrix.items()
            if cats.get(category, True)
        ]

    def summary(self) -> str:
        """Affiche la matrice d'activation sous forme lisible."""
        if not self._matrix:
            return "SignalFilter: aucune règle chargée (tout autorisé)"

        cats = sorted({c for m in self._matrix.values() for c in m})
        header = f"{'sub_type':<22}" + "".join(f"{c:>12}" for c in cats)
        sep = "-" * len(header)
        lines = ["SignalFilter matrix:", sep, header, sep]

        for sub in sorted(self._matrix):
            row = f"{sub:<22}"
            for cat in cats:
                val = self._matrix[sub].get(cat, True)
                row += f"{'✓':>12}" if val else f"{'✗':>12}"
            lines.append(row)

        lines.append(sep)
        return "\n".join(lines)

    def __repr__(self) -> str:
        n_rules = sum(len(v) for v in self._matrix.items())
        return f"SignalFilter(path={self._path!r}, sub_types={list(self._matrix)})"


# ── Singleton optionnel ───────────────────────────────────────────
# Permet un import direct dans le runner sans instanciation manuelle :
#   from arabesque.core.signal_filter import default_filter
#   if default_filter.is_allowed(signal.sub_type, category): ...

def _build_default() -> SignalFilter:
    """Construit le filtre par défaut (ne lève pas d'exception si absent)."""
    try:
        return SignalFilter(_DEFAULT_PATH)
    except Exception:
        return SignalFilter.__new__(SignalFilter)  # instance vide


default_filter: SignalFilter = _build_default()
