# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Contexte

Arabesque est un système de trading algorithmique Python pour prop firms (FTMO / Goat Funded Trader). Stratégie principale : **trend-following BB H1** sur crypto alt-coins et métaux précieux. Contraintes strictes : drawdown journalier 3%, drawdown total 10%.

**Lire avant toute action :**
1. `HANDOFF.md` — état opérationnel actuel + prochaines étapes P0→P8
2. `docs/DECISIONS.md` — pourquoi chaque décision, bugs connus, ce qui a été abandonné

## Commands

```bash
# Installation
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Backtest IS+OOS
python -m arabesque run --strategy extension --mode backtest BTCUSD XAUUSD EURUSD

# Dry-run (replay Parquet, quasi-real-time)
python -m arabesque run --strategy extension --mode dryrun --from 2025-10-01 --to 2026-01-01

# Live (cTrader)
python -m arabesque run --strategy extension --mode live --account ftmo_swing_test

# Screening multi-instruments
python -m arabesque screen --strategy extension --list crypto

# Mise à jour données
python -m arabesque fetch --from 2024-01-01 --to 2026-12-31

# Analyse logs
python -m arabesque analyze --days 7

# Test connectivité broker
python -m arabesque check --account ftmo_swing_test
```

**Validation avant déploiement d'un changement :**
```bash
python -m arabesque run --strategy extension --mode dryrun  # 3 mois minimum
# Comparer métriques avec baseline dans HANDOFF.md
```

## Architecture

Architecture multi-stratégie (restructuration mars 2026) :

```
arabesque/
├── core/           # Noyau immuable : models.py, guards.py, audit.py
├── modules/        # Composants réutilisables : indicators.py, position_manager.py
├── strategies/     # Une stratégie = un dossier (ex: extension/, fouette/)
│   └── <nom>/
│       ├── signal.py      # Générateur unique — backtest + dryrun + live
│       ├── params.yaml    # Presets de paramètres
│       └── STRATEGY.md   # Fiche : résultats, décisions, limites
├── execution/      # Moteurs : backtest.py, dryrun.py, live.py
├── broker/         # Adaptateurs : ctrader.py, tradelocker.py, base.py
├── data/           # Pipeline : store.py (Parquet + Yahoo), fetch.py
├── analysis/       # Métriques : metrics.py, pipeline.py
├── live/           # Shims de compatibilité → arabesque.execution.live
└── config.py       # Chargement YAML + env vars
```

**Flux de données (règle anti-lookahead) :**
Signal généré sur bougie `i` → exécuté au open de `i+1` via `_pending_signals`. Cette règle est inviolable — la violer invalide tous les résultats de backtest.

**Code identique** backtest / dryrun / live : un seul `signal.py` par stratégie, zéro divergence.

## Règles inviolables

**Anti-biais :**
- Signal sur bougie `i`, exécution au open de `i+1` via `_pending_signals`
- Guards toujours actifs — même en dry-run. Les désactiver invalide les résultats
- Un seul trade simultané par instrument (`duplicate_instrument` guard)
- Règle pire-cas intrabar : si SL et TP touchés sur la même bougie → SL gagne
- Tout en R/ATR (invariant d'instrument) : sizing, trailing, métriques

**Zones stables (ne pas toucher sans rejeu IS/OOS complet) :**
- `arabesque/core/models.py`, `arabesque/core/guards.py`
- `arabesque/strategies/extension/signal.py` — **seul Claude Opus 4.6 peut modifier**
- `arabesque/modules/position_manager.py` — le TSL/BE est l'edge principal (75.5% WR)

**Zones stables (modifier avec précaution) :**
- `arabesque/execution/live.py`, `arabesque/execution/dryrun.py`
- `arabesque/broker/*.py`

## Git

- Push direct sur `main` autorisé (pas de PR)
- **Jamais `git push --force` sur `main`** (a déjà écrasé un commit)
- Format commits : `type(scope): description courte` — ex : `fix(guards): daily_dd_pct divisé par daily_start_balance`

## Discipline documentation

Après chaque session, mettre à jour obligatoirement :
1. `HANDOFF.md` — état, bugs ouverts, prochaines étapes
2. `docs/DECISIONS.md` — toute décision technique, hypothèse testée + résultat chiffré

## Imports canoniques (post-restructuration)

```python
# ✅ Nouveaux chemins
from arabesque.core.models import Signal, Position
from arabesque.core.guards import Guards, PropConfig
from arabesque.modules.indicators import compute_rsi
from arabesque.modules.position_manager import PositionManager
from arabesque.strategies.extension.signal import ExtensionSignalGenerator
from arabesque.data.store import load_ohlc
from arabesque.execution.live import LiveEngine

# ⚠️ Anciens chemins (shims, dépréciés)
# from arabesque.models import Signal
# from arabesque.live.engine import LiveEngine
```

## Comptes — sécurité critique

Les comptes avec fonds réels **doivent** être `protected: true` dans `config/accounts.yaml`. Le moteur live vérifie ce flag et refuse sans `--force-live`. Ne jamais connecter le bot sur le challenge sans validation complète des guards DD.

## Stratégies — nommage

Noms tirés des disciplines artistiques acrobatiques (danse classique, GR, GAF…), **en français**, avec relation imagée entre le mouvement et la logique de la stratégie. Ex : *Extension* (trend-following = allongement hors des bandes après compression), *Fouetté* (ORB M1).

## Scripts

`scripts/` : outils CLI permanents uniquement (≤ 6 fichiers). Tout code temporaire / one-off → `tmp/` (gitignored).
