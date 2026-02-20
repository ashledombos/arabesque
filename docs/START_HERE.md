# 🚀 START HERE — Arabesque

> Premier fichier à lire pour toute nouvelle personne (ou IA) qui rejoint le projet.

---

## 1. Clone & setup

```bash
ssh raphael@hodo
cd ~/dev/arabesque
git pull origin main
source .venv/bin/activate
```

Ou depuis zéro :
```bash
git clone git@github.com:ashledombos/arabesque.git
cd arabesque
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config/secrets.yaml.example config/secrets.yaml  # puis remplir les credentials
```

---

## 2. Lire dans cet ordre

| Fichier | Contenu | Temps de lecture |
|---|---|---|
| **HANDOFF.md** | État actuel + bugs ouverts + plan P0-P8 | 5 min |
| **docs/decisions_log.md** | Pourquoi chaque décision + erreurs passées | 10 min |
| **docs/TECH_DEBT.md** | Dette technique en cours + priorités | 2 min |
| **docs/instrument_selection_philosophy.md** | Logique de sélection anti-overfitting | 5 min |
| **docs/ARCHITECTURE.md** | Architecture détaillée | optionnel |

---

## 3. Prompt à coller en début de chaque nouvelle conversation IA

```
Lis HANDOFF.md et docs/decisions_log.md dans le repo GitHub ashledombos/arabesque
(branche main) avant de répondre. Contexte : trading algo prop firms FTMO, Python.
Bug critique non corrigé : daily_dd_pct divisé par start_balance
(doit être daily_start_balance) — guards DD ne se déclenchent jamais.
Workflow : push direct main, doc à jour après chaque session, supprimer code mort.
Si tu proposes une modification de code : indique impact, risques, comment valider,
met à jour HANDOFF.md + decisions_log.md + TECH_DEBT.md si nécessaire.
```

---

## 4. Commandes de démarrage rapide

```bash
# Vérifier l'état des instruments (pipeline complet)
python scripts/run_pipeline.py -v

# Stats avancées sur les viables
python scripts/run_stats.py XAUUSD --period 730d

# Backtest d'un instrument
python scripts/backtest.py BCHUSD --strategy combined

# Replay dry-run (offline, parquets, 3 mois)
python -m arabesque.live.runner \
  --mode dry_run --source parquet \
  --start 2025-10-01 --end 2026-01-01

# Live dry-run (ticks cTrader réels, zéro ordre)
python -m arabesque.live.engine --dry-run

# Git — aligner local sur remote (jamais --force)
git fetch origin && git reset --hard origin/main
```

---

## 5. Règles non négociables

- Anti-lookahead strict : signal bougie `i`, exécution open `i+1`
- Même code backtest/replay/live (`CombinedSignalGenerator`)
- Guards toujours actifs (dry-run inclus)
- Jamais `git push --force` sur `main`
- Ne jamais connecter le bot sur le compte challenge avant validation guards DD

---

## 6. Architecture en une phrase

```
ticks cTrader/TradeLocker → BarAggregator → CombinedSignalGenerator
→ OrderDispatcher → CTraderAdapter + TradeLockerAdapter → Apprise (alertes)
```

Voir `docs/ARCHITECTURE.md` pour le détail.
