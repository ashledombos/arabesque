# Arabesque

Système de trading algorithmique pour prop firms (FTMO, GFT).
Stratégie trend-following H1 sur Bandes de Bollinger — **validée sur 20 mois**.

> **Résultat de référence** — 20 mois (Jul 2024 → Fév 2026), 76 instruments
> N=1998 | WR=75.5% | Exp=+0.130R | Total R=+260.2R | Max DD=8.2% | IC99>0 ✅

**→ Commencer ici : [docs/START_HERE.md](docs/START_HERE.md)**

---

## Démarrage rapide

```bash
git clone git@github.com:ashledombos/arabesque.git && cd arabesque
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Dry-run replay parquet (3 mois)
python -m arabesque run --strategy extension --mode dryrun \
  --from 2025-10-01 --to 2026-01-01

# Backtest IS+OOS
python -m arabesque run --strategy extension --mode backtest BTCUSD XAUUSD

# Moteur live (compte test)
python -m arabesque run --strategy extension --mode live --account ftmo_swing_test

# Ancien point d'entrée (toujours fonctionnel)
python -m arabesque.live.engine --dry-run
```

---

## Architecture

```
arabesque/
├── core/              ← Kernel immuable (models, guards, audit)
├── modules/           ← Briques réutilisables (indicators, position_manager)
├── strategies/
│   └── extension/     ← Trend-following H1 — stratégie validée
│       ├── signal.py  ← Générateur unique backtest + live
│       ├── params.yaml← Presets nommés
│       └── STRATEGY.md← Fiche complète
├── execution/         ← Moteurs (backtest, dryrun, live, bar_aggregator)
├── broker/            ← Adapters (cTrader, TradeLocker, DryRun)
├── data/              ← Store parquet + fetch (ex-barres_au_sol)
└── analysis/          ← Métriques, stats, pipeline de screening
```

Les anciens chemins d'import (`arabesque.models`, `arabesque.live.engine`, etc.)
fonctionnent toujours via des shims de compatibilité.

---

## Documentation

| Document | Contenu |
|---|---|
| [docs/START_HERE.md](docs/START_HERE.md) | **Porte d'entrée — lire en premier** |
| [HANDOFF.md](HANDOFF.md) | État actuel + plan de développement |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Décisions techniques et historique |
| [arabesque/strategies/extension/STRATEGY.md](arabesque/strategies/extension/STRATEGY.md) | Fiche de la stratégie active |
| [docs/HYGIENE.md](docs/HYGIENE.md) | Règles de contribution |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture détaillée |
