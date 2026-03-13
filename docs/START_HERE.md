# Arabesque — START HERE

> Ce fichier est **la seule porte d'entrée**. Lisez-le en premier.
> Il vous dit quoi lire ensuite et dans quel ordre.

---

## 1. Setup rapide

```bash
ssh raphael@hodo
cd ~/dev/arabesque
git pull origin main
source .venv/bin/activate
```

Depuis zéro :
```bash
git clone git@github.com:ashledombos/arabesque.git
cd arabesque
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config/secrets.example.yaml config/secrets.yaml  # puis remplir les credentials
```

---

## 2. Commandes essentielles

```bash
# Backtest trend sur instruments validés
python -m arabesque run --strategy extension --mode backtest BTCUSD XAUUSD EURUSD

# Dry-run replay parquet (3 mois)
python -m arabesque run --strategy extension --mode dryrun \
  --from 2025-10-01 --to 2026-01-01

# Moteur live (compte test)
python -m arabesque run --strategy extension --mode live --account ftmo_swing_test

# Moteur live direct (ancien point d'entrée — toujours fonctionnel)
python -m arabesque.live.engine --dry-run

# Screening multi-instruments
python -m arabesque screen --strategy extension --list crypto

# Mise à jour données Parquet
python -m arabesque fetch --from 2024-01-01 --to 2026-12-31

# Analyser les logs live
python -m arabesque analyze --days 7
```

---

## 3. Ordre de lecture — documentation

| # | Document | Contenu | Temps |
|---|---|---|---|
| **1** | `docs/START_HERE.md` | Ce fichier | 2 min |
| **2** | `HANDOFF.md` | État actuel, bugs ouverts, P0→P8 | 5 min |
| **3** | `docs/DECISIONS.md` | Pourquoi chaque décision, ce qui a été abandonné | 10 min |
| **4** | `arabesque/strategies/extension/STRATEGY.md` | Fiche de la stratégie active | 5 min |
| **5** | `docs/HYGIENE.md` | Règles de contribution — LIRE avant de coder | 3 min |
| **6** | `docs/ARCHITECTURE.md` | Architecture technique détaillée | optionnel |

---

## 4. Prompt de reprise pour une IA

```
Lis HANDOFF.md et docs/DECISIONS.md dans le repo GitHub ashledombos/arabesque
(branche main) avant de répondre. Contexte : trading algo prop firms FTMO,
Python asyncio, stratégie trend-following H1 en live.

Règles immuables :
- Gains petits, fréquents, consistants. WR ≥ 70%.
- La stratégie Extension (trend-only) est validée — ne pas modifier la logique
  de signal sans rejeu complet 20 mois.
- Tick-level TSL non optionnel (récupère +183R vs +10.4R H1-only).
- Seul Claude Opus 4.6 peut modifier arabesque/strategies/*/signal.py et
  arabesque/core/*.py.

Architecture post-restructuration :
- arabesque/core/          → models, guards, audit (kernel immuable)
- arabesque/modules/       → indicators, position_manager (réutilisables)
- arabesque/strategies/    → une stratégie = un dossier autonome
- arabesque/execution/     → backtest, dryrun, live (moteurs)
- arabesque/data/          → store + fetch (ex-barres_au_sol)
- arabesque/analysis/      → metrics, stats, pipeline
- Les vieux chemins d'import (arabesque.models, arabesque.live.engine, etc.)
  fonctionnent toujours via des shims de compatibilité.
```

---

## 5. Règles non négociables

1. **Jamais `git push --force` sur main**
2. **Jamais modifier la stratégie sans rejeu complet** (20 mois, 76 instruments)
3. **Tout script temporaire dans `tmp/`** (gitignored)
4. **Fin de session** : mettre à jour `HANDOFF.md` + `docs/DECISIONS.md`
5. **Comptes `protected: true`** dans `config/accounts.yaml` ne peuvent pas
   recevoir d'ordres sans `--force-live` explicite
6. **Seul Opus 4.6** peut modifier `arabesque/strategies/*/signal.py`
   et `arabesque/core/*.py`

Voir `docs/HYGIENE.md` pour les règles complètes.
