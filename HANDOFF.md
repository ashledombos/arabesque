# ARABESQUE — Handoff Document v4
## Pour reprendre le développement dans un nouveau chat

> **Repo** : https://github.com/ashledombos/arabesque  
> **Branche principale** : `main`  
> **Dernière mise à jour** : 2026-02-20

---

## 1. Contexte : pourquoi Arabesque existe

**Raph** développe un système de trading quantitatif pour prop firms (FTMO, Goat Funded Trader).

**Envolées** (le système précédent) utilisait des breakouts Donchian sur 4H. Après diagnostic complet, tous les configs validées sont devenues négatives une fois les biais corrigés. Le Donchian breakout n’a pas d’edge exploitable sur les instruments testés.

**Raison fondamentale** : l’asymétrie d’exécution. Le breakout achète quand le prix MONTE (slippage adverse). Le mean-reversion achète quand le prix DESCEND (slippage neutre ou favorable).

**BB_RPB_TSL** (Freqtrade, crypto) a été analysé en détail : 527 jours live, 48% CAGR, 90.8% WR, 20.8% DD. Son edge vient du pipeline complet, pas d’un indicateur isolé.

**Arabesque** = extraction des principes de BB_RPB_TSL, adaptée aux prop firms avec contraintes de drawdown strict.

---

## 2. Architecture générale

Arabesque est divisé en **deux modes** qui partagent exactement le même générateur de signaux (`CombinedSignalGenerator`).

### 2a. Mode Recherche (offline)

Objectif : valider quels instruments ont un edge **avant** d’en passer un en live.

| Script | Usage | Quand l’utiliser |
|---|---|---|
| `scripts/run_pipeline.py` | Screening 80 instruments en 3 stages (signal count → IS backtest → OOS + Monte Carlo) | Point de départ, à faire tourner régulièrement |
| `scripts/backtest.py` | Backtest IS+OOS sur un instrument donné | Pour creuser un instrument spécifique |
| `scripts/run_stats.py` | Statistiques avancées (Wilson CI, bootstrap, dégradation IS→OOS) | Après pipeline, sur les instruments viables |

Le résultat du pipeline est un JSONL dans `results/`. Les instruments viables (`follow: true`) sont reportés dans `config/instruments.yaml`.

### 2b. Mode Live (online)

```
cTrader ticks
    │
    ▼
PriceFeedManager         arabesque/live/price_feed.py
    │ on_tick
    ▼
BarAggregator            arabesque/live/bar_aggregator.py
    ├─ agrège ticks → barres H1
    ├─ CombinedSignalGenerator.prepare(df)
    └─ CombinedSignalGenerator.generate_signals()
    │ Signal
    ▼
OrderDispatcher          arabesque/live/order_dispatcher.py
    ├─ Guards (DD, spread, positions, risque open)
    ├─ cTrader compte 1
    ├─ cTrader compte 2
    └─ TradeLocker
    │ résultat
_notify_order()          → Apprise (Telegram / ntfy / Discord)
```

Commande de démarrage :
```bash
python -m arabesque.live.engine --dry-run   # ticks réels, zéro ordre
python -m arabesque.live.engine             # live réel
```

### 2c. Continuité stratégique : backtest = live ?

**Oui.** Les trois scripts backtest et le moteur live s’appuient **tous** sur le même `CombinedSignalGenerator` :
- `arabesque/backtest/signal_gen.py` — mean-reversion (BB excess + RSI + régime HTF)
- `arabesque/backtest/signal_gen_trend.py` — trend (BB squeeze → expansion → breakout + ADX)
- `arabesque/backtest/signal_gen_combined.py` — fusion des deux, c’est lui que tout le monde appelle

Le `BarAggregator` live appelle `CombinedSignalGenerator.generate_signals()` sur le cache de barres H1, exactement comme le runner backtest le fait sur un DataFrame parquet. **Ce qu’on backteste est ce qui tourne en live.**

---

## 3. Briques partagées (backtest + live)

| Module | Rôle |
|---|---|
| `arabesque/models.py` | Dataclasses `Signal`, `Position`, `Decision`, `Counterfactual` |
| `arabesque/guards.py` | Guards FTMO : drawdown, risque open, sessions, spread |
| `arabesque/position/manager.py` | Trailing stop 5 paliers, breakeven, giveback, deadfish |
| `arabesque/backtest/signal_gen_combined.py` | `CombinedSignalGenerator` — cœur de la stratégie |
| `arabesque/config.py` | Chargement `settings.yaml` + `secrets.yaml` + `instruments.yaml` |
| `arabesque/audit.py` | Logger JSONL des décisions |

**Points d’attention sur `Signal`** :
- Champs natifs : `close`, `open_` (pas `tv_close`/`tv_open` dans `__init__`)
- `tv_close` et `tv_open` existent comme **propriétés** (alias de compatibilité) — ne pas les passer en argument du constructeur
- Toujours utiliser `sig.tp_indicative` (pas `sig.tp` qui n’existe pas)
- `sig.side` est un enum `Side.LONG`/`Side.SHORT`, pas une string

**Trailing paliers** :
- +0.5R → BE, +1R → 0.5R, +1.5R → 0.8R, +2R → 1.2R, +3R → 1.5R
- SL ne descend jamais (LONG) / ne monte jamais (SHORT)

---

## 4. État du code (2026-02-20)

```
arabesque/
├── models.py                   # Signal, Decision, Position, Counterfactual
├── guards.py                   # Guards prop + exec, sizing, AccountState
├── audit.py                    # JSONL logger
├── screener.py                 # Screener pipeline
├── config.py                   # Chargement YAML
├── backtest/
│   ├── data.py                 # load_ohlc() — charge parquets locaux
│   ├── signal_gen.py           # BacktestSignalGenerator (mean-reversion)
│   ├── signal_gen_trend.py     # TrendSignalGenerator (breakout)
│   ├── signal_gen_combined.py  # CombinedSignalGenerator ← utiliser celui-ci
│   ├── signal_labeler.py       # label_mr_signal, label_trend_signal
│   └── runner.py               # Backtest runner (DataFrame → trades)
├── live/
│   ├── engine.py               # LiveEngine — point d’entrée principal
│   ├── bar_aggregator.py       # Ticks → barres H1 → signaux
│   ├── price_feed.py           # Connexion cTrader, subscribe par symbole
│   ├── order_dispatcher.py     # Guards + dispatch multi-comptes
│   ├── parquet_clock.py        # Replay parquets (dry-run sans credentials)
│   ├── runner.py               # Ancien point d’entrée (déprécié, ≈ engine.py)
│   └── bar_poller.py           # BarPoller (legacy cTrader H1 stream)
├── broker/
│   └── factory.py + adapters   # create_all_brokers(), CTraderAdapter, TradeLockerAdapter
├── position/
│   └── manager.py              # PositionManager (trailing, breakeven, exits)
└── analysis/                   # Outils stats post-run

scripts/
├── run_pipeline.py             # Screener principal (3 stages)
├── backtest.py                 # Backtest simple IS+OOS
└── run_stats.py                # Stats avancées (Wilson, bootstrap)
```

---

## 5. Historique des bugs résolus

| Date | Bug | Correction |
|---|---|---|
| 2026-02-18 | `sig.tp` → AttributeError | `sig.tp_indicative` |
| 2026-02-18 | RR calculé sur close courant (faux en replay) | RR calculé sur `df.iloc[idx]["Close"]` |
| 2026-02-18 | `np.float64` dans le dict signal | Cast `float()` natif partout |
| 2026-02-18 | Colonne `"ema200"` inexistante | Essaie `"ema200"` puis `"ema_slow"` |
| 2026-02-20 | `Signal.__init__() got an unexpected keyword argument 'tv_close'` | `signal_gen_trend.py` : remplacé `tv_close=` / `tv_open=` par `close=` / `open_=` dans les deux constructeurs LONG + SHORT |

---

## 6. Comptes FTMO (situation 2026-02-18)

- **Compte live test gratuit 15j** : 100 000 USD, Hedged 1:30 — compte «Live» selon cTrader — **sans risque réel**, idéal pour tester les ordres dangereux
- **Compte challenge 100k** : 94 989 USD actuel, Hedged 1:30 — compte «Demo» selon cTrader — **argent réel payé** — max DD 10%, déjà à ~5.0% DD → marge restante ~5%

⚠️ **Ne pas connecter le bot live sur le compte challenge sans validation complète des Guards DD.**

---

## 7. Prochaines étapes (par priorité)

### P0 — Pipeline à nouveau fonctionnel (corrigé 2026-02-20)

Bug résolu : `signal_gen_trend.py` utilisait `tv_close=` / `tv_open=` dans les constructeurs `Signal()` alors que ce sont des propriétés (pas des champs `__init__`).  
Fix : commit [`e2bc0eb`](https://github.com/ashledombos/arabesque/commit/e2bc0ebcfa52b255284b98ec7db3ab902e3869b6).

```bash
python scripts/run_pipeline.py -v
# Doit maintenant passer Stage 1 sans erreur tv_close
```

### P1 — Valider les résultats du pipeline

Après la correction, lancer le pipeline complet et examiner les instruments viables :
```bash
python scripts/run_pipeline.py --list all -v 2>&1 | tee results/pipeline_run.log
# Vérifier : combien passent Stage 1, Stage 2, Stage 3 ?
# Reporter les viables dans config/instruments.yaml (follow: true)
```

### P2 — Run stats avancées sur les viables

```bash
python scripts/run_stats.py XAUUSD
python scripts/run_stats.py BTCUSD
# Wilson CI, bootstrap, dégradation IS→OOS
# Garder uniquement si bootstrap 95% CI > 0
```

### P3 — Valider les Guards DD sur replay complet

```bash
python -m arabesque.live.runner \
  --mode dry_run --source parquet \
  --start 2025-10-01 --end 2026-01-01
# Chercher dans les logs :
# - "rejected DAILY_DD_LIMIT"
# - "rejected MAX_DD_LIMIT"
# - Résumé final : balance, equity, open_positions
```

### P4 — Connecter le compte test FTMO (live gratuit 15j)

```bash
# Copier les credentials dans config/secrets.yaml
# Tester sans ordres réels :
python -m arabesque.live.engine --dry-run
# dry_run = vrais ticks cTrader, zéro ordre envoyé
```

### P5 — Premier ordre réel sur compte test

```bash
python -m arabesque.live.engine
# Vérifier dans cTrader que l’ordre apparaît avec le bon SL/volume
```

### P6 — Nettoyage technique

- Supprimer les alias `tv_close`/`tv_open` de `models.py` une fois vérifié qu’aucun autre fichier ne les utilise encore comme argument de constructeur
- Déprécier `arabesque/live/runner.py` (remplacé par `engine.py`)
- Unifier le calcul ADX (dupliqué entre `signal_gen.py` et `signal_gen_trend.py`)

---

## 8. Commandes utiles

```bash
# Lancer le pipeline (screener)
python scripts/run_pipeline.py -v
python scripts/run_pipeline.py --list crypto -v

# Backtest d’un instrument
python scripts/backtest.py BTCUSD --strategy combined

# Stats avancées
python scripts/run_stats.py XAUUSD

# Replay dry-run rapide (4 instruments)
python -m arabesque.live.runner \
  --mode dry_run --source parquet \
  --start 2025-06-01 --instruments ALGUSD XTZUSD BCHUSD SOLUSD

# Moteur live (dry-run, ticks réels)
python -m arabesque.live.engine --dry-run

# Git : aligner local sur remote
git fetch origin && git reset --hard origin/main
```

---

## 9. Infra

- Serveur : hodo, user `raphael`, `/home/raphael/dev/arabesque/`
- Parquets H1 : présents localement, chargés via `load_ohlc(instrument, prefer_parquet=True)`
- Alertes : Telegram + ntfy (configurés dans settings.yaml)
- Python : `.venv` dans le repo
- Systemd : unit files dans `systemd/` pour démarrage automatique

---

## 10. Pour reprendre dans un nouveau chat

```
Lis le fichier HANDOFF.md dans le repo GitHub ashledombos/arabesque (branche main).
Contexte : système de trading algo Python pour prop firms FTMO.
Dernière session (2026-02-20) : bug tv_close corrigé dans signal_gen_trend.py,
pipeline de nouveau fonctionnel, architecture documentée.
Prochain objectif : [voir §7 Prochaines étapes]
```
