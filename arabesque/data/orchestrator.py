"""
Arabesque — Data Orchestrator.

Vérifie que les parquets nécessaires sont à jour avant un backtest,
et propose de les télécharger si des barres sont manquantes.

Absorbe la logique de barres_au_sol/data_orchestrator.py.
La source de vérité pour les instruments est arabesque/data/instruments.csv.

Usage CLI :
    python -m arabesque fetch --from 2024-01-01 --to 2026-12-31
    python -m arabesque fetch --instrument BTCUSD --from 2024-01-01

Usage programmatique :
    from arabesque.data.orchestrator import ensure_data_ready
    ensure_data_ready(["BTCUSD", "XAUUSD"], start="2024-01-01", end="2026-12-31")
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("arabesque.data.orchestrator")


def default_data_root() -> str:
    """Chemin par défaut vers les données Parquet.

    Priorité :
    1. Variable d'environnement ARABESQUE_DATA_ROOT
    2. ~/dev/barres_au_sol/data  (convention historique)
    3. <repo_parent>/barres_au_sol/data
    """
    env = os.environ.get("ARABESQUE_DATA_ROOT")
    if env:
        return env

    home_path = Path.home() / "dev" / "barres_au_sol" / "data"
    if home_path.exists():
        return str(home_path)

    # Fallback relatif au repo
    repo_root = Path(__file__).resolve().parent.parent.parent
    return str(repo_root.parent / "barres_au_sol" / "data")


def check_parquet_freshness(
    instrument: str,
    data_root: Optional[str] = None,
    timeframe: str = "1h",
    min_end_date: Optional[str] = None,
) -> dict:
    """Vérifie si le parquet d'un instrument est présent et à jour.

    Returns:
        {
            "exists": bool,
            "path": str | None,
            "last_bar": str | None,  # ISO date de la dernière barre
            "stale": bool,           # True si plus de 2 jours de retard
            "missing_days": int,
        }
    """
    from arabesque.data.store import _parquet_path_for

    root = data_root or default_data_root()
    path = _parquet_path_for(instrument, root, timeframe)

    if not path or not Path(path).exists():
        return {"exists": False, "path": None, "last_bar": None, "stale": True, "missing_days": 999}

    try:
        df = pd.read_parquet(path, columns=["Open"])  # lecture minimale
        last_bar = df.index[-1]
        if hasattr(last_bar, "date"):
            last_date = last_bar.date()
        else:
            last_date = pd.Timestamp(last_bar).date()

        today = datetime.utcnow().date()
        missing_days = (today - last_date).days

        target_date = None
        if min_end_date:
            target_date = pd.Timestamp(min_end_date).date()
            stale = last_date < target_date
        else:
            stale = missing_days > 2

        return {
            "exists": True,
            "path": path,
            "last_bar": str(last_date),
            "stale": stale,
            "missing_days": missing_days,
        }
    except Exception as e:
        logger.warning(f"Erreur lecture parquet {instrument}: {e}")
        return {"exists": True, "path": path, "last_bar": None, "stale": True, "missing_days": 999}


def ensure_data_ready(
    instruments: list[str],
    start: str = "2024-01-01",
    end: Optional[str] = None,
    data_root: Optional[str] = None,
    auto_fetch: bool = False,
    interactive: bool = True,
) -> list[str]:
    """Vérifie les parquets et propose de télécharger les manquants.

    Args:
        instruments   : Liste de symboles (ex: ["BTCUSD", "XAUUSD"])
        start         : Date de début attendue
        end           : Date de fin attendue (None = aujourd'hui)
        data_root     : Chemin vers les données (auto-détecté si None)
        auto_fetch    : Si True, télécharge sans demander confirmation
        interactive   : Si True, affiche un prompt pour confirmation

    Returns:
        Liste des instruments avec données à jour.
    """
    root = data_root or default_data_root()
    end_date = end or datetime.utcnow().strftime("%Y-%m-%d")

    stale = []
    missing = []

    for inst in instruments:
        status = check_parquet_freshness(inst, root, min_end_date=end_date)
        if not status["exists"]:
            missing.append(inst)
        elif status["stale"]:
            stale.append((inst, status["last_bar"], status["missing_days"]))

    if not missing and not stale:
        logger.info("✅ Toutes les données sont à jour.")
        return instruments

    if missing:
        logger.warning(f"⚠️  Données manquantes : {missing}")
    if stale:
        for inst, last, days in stale:
            logger.warning(f"⚠️  {inst} : dernière barre {last} ({days} jours de retard)")

    if not auto_fetch and interactive:
        stale_list = [s[0] for s in stale]
        all_outdated = missing + stale_list
        print(f"\n⚠️  Données manquantes ou obsolètes pour : {all_outdated}")
        print(f"   Lancer : python -m arabesque fetch --from {start} --to {end_date}")
        answer = input("   Télécharger maintenant ? [o/N] ").strip().lower()
        if answer == "o":
            auto_fetch = True

    if auto_fetch:
        _run_fetch(missing + [s[0] for s in stale], start, end_date, root)

    # Retourner les instruments avec des données valides (même si stale)
    ready = [i for i in instruments if i not in missing]
    return ready


def _run_fetch(instruments: list[str], start: str, end: str, data_root: str) -> None:
    """Lance le téléchargement via arabesque.data.fetch."""
    logger.info(f"Téléchargement de {len(instruments)} instruments ({start} → {end})...")
    try:
        from arabesque.data.fetch import main as fetch_main
        # Construire les args comme si on avait lancé en CLI
        import sys
        orig_argv = sys.argv
        filter_pattern = "|".join(f"^{inst}$" for inst in instruments)
        sys.argv = [
            "data_orchestrator",
            "--start", start,
            "--end", end,
            "--derive", "1h",
            "--filter", filter_pattern,
        ]
        try:
            fetch_main()
        finally:
            sys.argv = orig_argv
    except Exception as e:
        logger.error(f"Erreur lors du téléchargement : {e}")
        logger.info("Téléchargez manuellement : python -m arabesque fetch --help")
