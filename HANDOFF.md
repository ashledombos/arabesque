# ARABESQUE — Handoff Document v5
## Pour reprendre le développement dans un nouveau chat

> **Repo** : https://github.com/ashledombos/arabesque  
> **Branche principale** : `main`  
> **Dernière mise à jour** : 2026-02-21 (session replay P2)

> 📖 **Lire aussi** :
> - `docs/decisions_log.md` — pourquoi chaque décision a été prise, bugs connus, ce qui a été abandonné
> - `docs/instrument_selection_philosophy.md` — logique de sélection par catégorie

---

## 1. Contexte

Arabesque est un système de trading quantitatif pour prop firms (FTMO, Goat Funded Trader).
Edge : **mean-reversion BB H1** + module trend, sur crypto alt-coins et métaux.
Justification de l’edge : asymétrie de slippage (MR achète la baisse, slippage favorable).
Référence : BB_RPB_TSL (Freqtrade, 527j live, 48% CAGR, 90.8% WR).

**Ce qui a été abandonné** : breakout Donchian 4H (projet Envolées) — edge inexistant après correction des biais. Le connecteur cTrader d’Envolées est réutilisable, pas la stratégie.

---

## 2. Architecture

### Principe fondamental
**Le même `CombinedSignalGenerator` tourne en backtest, replay parquet, et live cTrader.**  
Toute divergence entre les modes invalide les résultats du backtest.

### Mode Recherche (offline)

| Script | Usage |
|---|---|
| `scripts/run_pipeline.py` | Screening N instruments : Stage 1 (signals) → Stage 2 (IS) → Stage 3 (OOS+MC) |
| `scripts/backtest.py` | Backtest IS+OOS sur un instrument spécifique |
| `scripts/run_stats.py` | Stats avancées : Wilson CI, bootstrap 1000 iter, dégradation IS→OOS |

### Mode Live (online)

```
cTrader / TradeLocker ticks
    │
    ▼
PriceFeedManager       arabesque/live/price_feed.py
    │ on_tick
    ▼
BarAggregator          arabesque/live/bar_aggregator.py
    ├─ ticks → barres H1
    ├─ CombinedSignalGenerator.prepare(df)
    └─ CombinedSignalGenerator.generate_signals()
    │ Signal
    ▼
OrderDispatcher        arabesque/live/order_dispatcher.py
    ├─ Guards (DD, spread, positions, risque open)
    ├─ CTraderAdapter    (compte 1 et/ou 2)
    └─ TradeLockerAdapter (Goat Funded Trader)
    │
_notify_order()        → Apprise (Telegram / ntfy / Discord)
```

### Anti-lookahead (règle absolue)
- Signal généré sur bougie `i` (close confirmé)
- Stocké dans `_pending_signals`
- Exécuté au **open de bougie `i+1`**
- Toute exécution sur le close de `i` est un biais — invalide le backtest

---

## 3. Briques partagées

| Module | Rôle |
|---|---|
| `arabesque/models.py` | `Signal`, `Position`, `Decision`, `Counterfactual` |
| `arabesque/guards.py` | Guards FTMO : DD, risque open, sessions, spread |
| `arabesque/position/manager.py` | Trailing 5 paliers, breakeven, giveback, deadfish |
| `arabesque/backtest/signal_gen_combined.py` | `CombinedSignalGenerator` — cœur stratégique |
| `arabesque/broker/factory.py` | `create_all_brokers()` — crée CTraderAdapter + TradeLockerAdapter |
| `arabesque/config.py` | Chargement `settings.yaml` + `secrets.yaml` + `instruments.yaml` |
| `arabesque/audit.py` | Logger JSONL des décisions |

**Pièges sur `Signal`** :
- `close=` et `open_=` dans `__init__` (PAS `tv_close=` ni `tv_open=` qui sont des propriétés)
- `sig.tp_indicative` (PAS `sig.tp` qui n’existe pas)
- `sig.side` = enum `Side.LONG`/`Side.SHORT` (pas une string)

**Trailing** :
- +0.5R→BE, +1R→0.5R, +1.5R→0.8R, +2R→1.2R, +3R→1.5R
- SL ne descend jamais (LONG) / ne monte jamais (SHORT) — règle inviolable

---

## 4. Brokers supportés

| Broker | Adapter | Prop firm | Statut |
|---|---|---|---|
| cTrader | `CTraderAdapter` | FTMO | ⚠️ Implémenté, jamais testé en live réel |
| TradeLocker | `TradeLockerAdapter` | Goat Funded Trader | ⚠️ Implémenté, jamais testé |

**Multi-comptes** : `create_all_brokers()` dans `broker/factory.py` instancie tous les adapters configurés dans `secrets.yaml`. Un même signal peut être envoyé à plusieurs comptes simultanément.

**Comptes FTMO actuels** :
- **Compte live test 15j** : 100 000 USD, `live.ctraderapi.com:5035`, account_id `17057523` — sans risque réel, idéal pour valider l’intégration
- **Compte challenge 100k** : ~94 989 USD, ~5% DD déjà consommé, ~5% de marge restante — **ne pas y connecter le bot avant validation complète des guards DD**

---

## 5. État du code (2026-02-20)

```
arabesque/
├── models.py
├── guards.py
├── audit.py
├── config.py
├── backtest/
│   ├── data.py                 # load_ohlc()
│   ├── signal_gen.py           # MeanReversionSignalGenerator
│   ├── signal_gen_trend.py     # TrendSignalGenerator
│   ├── signal_gen_combined.py  # CombinedSignalGenerator ← utiliser celui-ci
│   ├── signal_labeler.py
│   ├── pipeline.py             # Pipeline 3 stages
│   ├── runner.py               # Backtest runner
│   ├── metrics.py
│   ├── metrics_by_label.py
│   └── stats.py                # Wilson CI, bootstrap
├── live/
│   ├── engine.py               # ⭐ Point d’entrée principal (UTILISER CELUI-CI)
│   ├── bar_aggregator.py       # Ticks → barres H1 → signaux
│   ├── price_feed.py           # Connexion cTrader
│   ├── order_dispatcher.py     # Guards + dispatch multi-comptes
│   ├── parquet_clock.py        # Replay parquets (dry-run offline)
│   ├── runner.py               # ⚠️ Déprécié — remplacer par engine.py
│   └── bar_poller.py           # ⚠️ Legacy — remplacer par price_feed.py
├── broker/
│   └── factory.py              # create_all_brokers()
├── position/
│   └── manager.py              # PositionManager
└── analysis/

scripts/
├── run_pipeline.py
├── backtest.py
└── run_stats.py

docs/
├── decisions_log.md            # ⭐ Pourquoi chaque décision
├── instrument_selection_philosophy.md
├── ARCHITECTURE.md
├── ROADMAP.md
└── journal.md
```

---

## 6. Historique des bugs

### ✅ Corrigés

| Date | Bug | Fix |
|---|---|---|
| 2026-02-21 | `DryRunAdapter.get_account_info()` retournait 100 000 $ fixes → risque d'écrasement de l'AccountState en P3 (TD-012) | `on_trade_closed(pnl)` dans BrokerAdapter + DryRunAdapter track equity réelle |
| 2026-02-21 | Spike de données UNIUSD → R=663.5 fantôme (84% des profits replay) | Filtre anti-spike `_clean_ohlc` : median_close × 3.0 (TD-013) |
| 2026-02-21 | Résultat replay P2 corrigé : 999 trades, +770R bruts mais **invalides** (UNIUSD spike non filtré) | Relancer après fix |
| 2026-02-18 | `sig.tp` → AttributeError | `sig.tp_indicative` |
| 2026-02-18 | RR calculé sur close courant | `df.iloc[idx]["Close"]` |
| 2026-02-18 | `np.float64` dans le dict signal | Cast `float()` natif |
| 2026-02-18 | Colonne `"ema200"` inexistante | Essaie `"ema200"` puis `"ema_slow"` |
| 2026-02-18 | Guard slippage rejetait 96% des signaux | Comparer `fill` vs `open_next_bar` (pas `tv_close`) |
| 2026-02-18 | 0 signaux en replay | `only_last_bar=False` + `_seen_signals` |
| 2026-02-20 | `Signal.__init__()` : `tv_close`/`tv_open` argument inconnu | `close=` / `open_=` dans `signal_gen_trend.py` |
| 2026-02-20 | `daily_dd_pct` divisé par `start_balance` | `/ daily_start_balance` dans `guards.py` (TD-001) |
| 2026-02-20 | `EXIT_TRAILING` jamais tag dans `_check_sl_tp_intrabar` | Discrimination `trailing_active or breakeven_set` dans `manager.py` (TD-002) |
| 2026-02-20 | Résidus `signal.tv_close` dans `order_dispatcher.py`, `orchestrator.py`, `adapters.py`, `parquet_clock.py` + `Signal.from_webhook_json` manquant | → `signal.close` partout + classmethod ajouté (TD-011) |

### ⚠️ Non corrigés (bloquants en premier)

| Priorité | Bug | Impact |
|---|---|---|
| 🟡 Moyenne | `tv_close = bars[-1]["close"]` (cache) au lieu de `df.iloc[idx]["Close"]` | RR légèrement faux en replay historique long (TD-004) |
| 🟡 Moyenne | `orchestrator.get_status()` exception silencieuse en fin de replay | Résumé final non fiable (TD-003) |

---

## 7. Résultats du pipeline (2026-02-20, 07h45)

```
80 testés → S1:77 → S2:31 → S3:17 viables  (763s)

Viables :
  Crypto (16) : AAVUSD, ALGUSD, BCHUSD, DASHUSD, GRTUSD, ICPUSD, IMXUSD,
                LNKUSD, NEOUSD, NERUSD, SOLUSD, UNIUSD, VECUSD, XLMUSD,
                XRPUSD, XTZUSD
  Metals  (1) : XAUUSD

FX : 0/43 viables (susp : BB width faible + régime USD trending 2024-25)
```

**Comparaison IS vs OOS sur l’exemple XTZUSD** :
| Métrique | IS | OOS | Delta |
|---|---|---|---|
| Trades | 212 | 93 | |
| Win Rate | 60.8% | 67.7% | +6.9% |
| Expectancy | +0.071R | +0.305R | +0.234R |
| Profit Factor | 1.18 | 2.00 | +0.82 |
| Max DD | 6.2% | 3.6% | -2.6% |

OOS meilleur qu’IS = signal structurel (pas overfitting sur la période IS).

**Prochaine étape immédiate** : lancer `run_stats` sur les 17 viables (voir §8).

---

## 8. Prochaines étapes (par priorité)

### P0 — ✅ FAIT — Corriger TD-001, TD-002, TD-011

TD-001 (`daily_dd_pct`) et TD-002 (`EXIT_TRAILING`) corrigés dans le code.  
TD-011 : résidus `signal.tv_close` dans le chemin live supprimés, `Signal.from_webhook_json` ajouté.

### P1 — Run stats avancées sur les 17 viables

```bash
for inst in AAVUSD ALGUSD BCHUSD DASHUSD GRTUSD ICPUSD IMXUSD LNKUSD \
            NEOUSD NERUSD SOLUSD UNIUSD VECUSD XAUUSD XLMUSD XRPUSD XTZUSD; do
    python scripts/run_stats.py $inst --period 730d
done
# Garder si bootstrap 95% CI borne basse > 0R
# Reporter dans config/instruments.yaml (follow: true)
```

### P0 — ✅ FAIT — Corriger TD-012 et TD-013 (session 2026-02-21)

- `DryRunAdapter` : equity tracking réel via `on_trade_closed(pnl)`
- `_clean_ohlc` : filtre anti-spike (median × 3.0)
- Cause du R=663.5 UNIUSD identifiée : bougie corrompue High ~56 (prix réel ~6.5)

### P2b — Relancer le replay avec les correctifs

```bash
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy combined --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
# Vérifier : UNIUSD total_r devrait être ~5-20R (pas 653R)
# Vérifier : guards DAILY_DD et MAX_DD déclenchés dans les logs si applicable
```

### P2 — Valider les guards DD sur replay parquet (dry-run offline)

> Aucun credentials nécessaire — utilise les fichiers Parquet locaux.

```bash
python -m arabesque.live.engine --mode dry_run --source parquet \
  --start 2025-10-01 --end 2026-01-01
# Chercher : "rejected DAILY_DD_LIMIT", "rejected MAX_DD_LIMIT"
# Vérifier aussi : EXIT_TRAILING dans les logs (doit apparaître)
```

### P3 — Connexion compte test FTMO (dry-run cTrader — vrais ticks, zéro ordre)

> Nécessite credentials dans `config/secrets.yaml` (account_id `17057523`).

```bash
python -m arabesque.live.engine --dry-run
# Vrais ticks cTrader, zéro ordre envoyé
```

### P4 — Premier ordre réel (compte test 15j seulement)

```bash
python -m arabesque.live.engine
# Vérifier dans cTrader : ordre apparaît, SL correct, volume correct
```

### P5 — Ré-analyse complète du pipeline (périodique)

Lancer le pipeline sur 100% des instruments pour validation par catégorie.
Un instrument neutre dans une catégorie validée n'est pas exclu automatiquement.

```bash
python scripts/run_pipeline.py -v  # tous les instruments
```

### P6 — FX en 4H (exploration)

```bash
python scripts/run_pipeline.py --list fx --mode wide --period 1825d -v
# Tester aussi avec filtre EMA200 daily et tier 0 trailing à +0.25R
# Voir docs/decisions_log.md §8 pour le contexte
```

### P7 — Nouvelles catégories (énergie, commodities, indices)

```bash
# 1. Télécharger les parquets via barres_au_sol
# 2. Copier dans data/parquet/
# 3. Lancer :
python scripts/run_pipeline.py --list energy -v
python scripts/run_pipeline.py --list indices -v
```
---

## 9. Commandes utiles

```bash
# Pipeline complet
python scripts/run_pipeline.py -v
python scripts/run_pipeline.py --list crypto -v
python scripts/run_pipeline.py --list fx --mode wide -v

# Stats sur un instrument
python scripts/run_stats.py XAUUSD --period 730d

# Backtest simple
python scripts/backtest.py BTCUSD --strategy combined

# Replay dry-run (offline, parquets)
python -m arabesque.live.runner \
  --mode dry_run --source parquet \
  --start 2025-10-01 --end 2026-01-01

# Live dry-run (ticks réels cTrader, zéro ordre)
python -m arabesque.live.engine --dry-run

# Live réel
python -m arabesque.live.engine

# Git : aligner local sur remote (jamais --force sur main)
git fetch origin && git reset --hard origin/main
```

---

## 10. Règles de maintenance (pour toute session future)

### Documentation
- **Mettre à jour ce fichier** à chaque session : date, bugs corrigés, résultats obtenus, nouvelles étapes.
- **Mettre à jour `docs/decisions_log.md`** à chaque décision stratégique ou bug identifié.
- **Ne pas dupliquer** : si une info est dans `decisions_log.md`, référencer sans recopier.

### Code
- **Supprimer** les fichiers dépréciés plutôt que de les garder avec un commentaire `# deprecated`.
- **Refactoriser** si une commande revient souvent sans script dédié (ex : boucle `run_stats` sur tous les viables).
- **Ne pas garder de code mort** : alias `tv_close`/`tv_open`, anciens runners, calculs ADX dupliqués.
- **`config/stable/`** pour la prod uniquement. `config/research/` pour les explorations. Rien ne migre vers stable sans pipeline IS/OOS + Monte Carlo.

### Git
- **Jamais `git push --force` sur `main`** (a déjà écrasé un commit).
- Messages de commit : `fix:`, `feat:`, `docs:`, `refactor:`, `chore:`.

---

## 11. Pour reprendre dans un nouveau chat

```
Lis HANDOFF.md et docs/decisions_log.md dans le repo GitHub
ashledombos/arabesque (branche main) avant de répondre.
Contexte : système de trading algo Python pour prop firms FTMO.
Dernière session : 2026-02-20 matin.
Bug critique non corrigé : daily_dd_pct divisé par start_balance
(doit être daily_start_balance) — guards DD ne se déclenchent jamais.
Prochain objectif : voir HANDOFF.md §8 Prochaines étapes.
```
