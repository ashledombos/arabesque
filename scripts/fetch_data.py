#!/usr/bin/env python3
"""CLI pour télécharger/mettre à jour les données Parquet.

Usage :
    python scripts/fetch_data.py --from 2024-01-01 --to 2026-12-31
    python scripts/fetch_data.py --instrument BTCUSD --from 2024-01-01

Wraps arabesque.data.fetch (anciennement barres_au_sol/data_orchestrator.py).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arabesque.data.fetch import main

if __name__ == "__main__":
    main()
