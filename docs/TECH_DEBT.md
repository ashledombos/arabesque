# Arabesque — Dette Technique

> Ce fichier recense la dette technique connue.  
> Item résolu → déplacer en section "Résolus" avec date.  
> À lire avant de coder : peut-être que le bug est déjà connu.

---

## 🔴 Critique (bloquant pour le live)

Aucun item critique ouvert en date du 2026-02-21.  
Les guards DD sont fonctionnels. DryRunAdapter tracke l'equity correctement.

---

## 🟠 Haute (impacte la fiabilité des résultats)

### TD-014 — Spike UNIUSD : filtre intrabar à valider

| Champ | Valeur |
|---|---|
| **Fichier** | `arabesque/backtest/data.py` — `_clean_ohlc` |
| **Symptôme** | Barre UNIUSD avec H≈57$ alors que prix normal ≈6.5$ (ratio H/C ≈ 8.7×). Le filtre ratio vs median ne l'attrape pas car c'est le High seul qui est aberrant (O/C normaux). Résultat : R=663.5 fantôme en replay. |
| **Fix appliqué** | Filtre intrabar ajouté : `H/C > 3.0` ou `H/O > 3.0` → spike détecté. |
| **À valider** | Relancer le replay et vérifier UNIUSD total_R < 20R + `analyze_replay.py` 0 outlier. |
| **Diagnostic parquet** | `df = pd.read_parquet('...UNIUSD_BINANCE_1h.parquet'); print(df[df['high']/df['close'] > 5])` |
| **Priorité** | Relancer le replay avant toute conclusion sur l'edge |

### TD-015 — ICPUSD et LNKUSD outliers à diagnostiquer

| Champ | Valeur |
|---|---|
| **Fichier** | Parquets ICPUSD et LNKUSD |
| **Symptôme** | ICPUSD R=+34.9 et LNKUSD R=+33.5 en 2 barres — suspects, même mécanisme probable que UNIUSD |
| **Fix** | Même diagnostic parquet que TD-014 |
| **Priorité** | Haute — représentent ~66R sur les 103R "propres" du replay |

---

## 🟡 Moyenne (code legacy, duplication, manques)

### TD-003 — `orchestrator.get_status()` exception silencieuse

| Champ | Valeur |
|---|---|
| **Fichier** | `arabesque/webhook/orchestrator.py` |
| **Symptôme** | Le résumé final (balance, equity, nb trades) peut lever une exception silencieuse |
| **Fix** | Wrapper `try/except` + log explicite |
| **Priorité** | P3 (avant connexion cTrader live) |

### TD-004 — `tv_close` = dernier close du cache

| Champ | Valeur |
|---|---|
| **Fichier** | `arabesque/backtest/signal_gen_combined.py` |
| **Symptôme** | Sur les replays longs, `tv_close = bars[-1]["close"]` au lieu de `df.iloc[idx]["Close"]` → RR légèrement faux |
| **Impact** | Rare en live (cache court), mais biais potentiel sur replay historique long |
| **Fix** | Utiliser l'index du signal pour récupérer le close |
| **Priorité** | P5 |

### TD-009 — Pas de `run_all_stats.py`

| Champ | Valeur |
|---|---|
| **Symptôme** | Lancer `run_stats.py` sur les 17 viables = boucle bash manuelle |
| **Fix** | Créer `scripts/run_all_stats.py` qui boucle sur `config/instruments.yaml` (`follow: true`) |
| **Priorité** | P5 |

### TD-010 — Corrélation inter-instruments non gérée

| Champ | Valeur |
|---|---|
| **Fichier** | `arabesque/guards.py` |
| **Symptôme** | Krach crypto 10/10/2025 : RSI < 20 sur 15 instruments simultanément → 5 positions SL en 1 barre |
| **Fix proposé** | Guard : si > N instruments crypto signalent dans la même direction sur la même bougie → bloquer |
| **Priorité** | P8 (non prioritaire tant que `max_open_risk_pct` est actif) |

### TD-016 — Sélection d'instruments non validée statistiquement

| Champ | Valeur |
|---|---|
| **Symptôme** | Les 17 instruments "viables" sont sélectionnés par le pipeline Stage 1-3, mais sans IC95 bootstrap positif individuel. Plusieurs (NEOUSD, XTZUSD, VECUSD) sont négatifs en replay. |
| **Fix** | Ajouter une étape dans le workflow : après `run_pipeline.py`, lancer `run_stats.py` sur chaque viable et ne garder que ceux avec IC95 low > 0R |
| **Documentation** | Voir `docs/BB_RPB_TSL_COMPARISON.md` pour le contexte |
| **Priorité** | Avant prochain scan des instruments |

---

## 🟢 Résolus

| ID | Description | Fix | Date |
|---|---|---|---|
| **TD-012** | `DryRunAdapter.get_account_info()` retournait 100k fixes → guards DD aveugles en P3 | `on_trade_closed(pnl)` dans DryRunAdapter + appel dans orchestrator `_update_account_on_close` | 2026-02-21 |
| **TD-013** | Spike UNIUSD barre Low=2.0 (ratio filtre) + barre High=57 (filtre intrabar) → R=663.5 fantôme | Double filtre `_clean_ohlc` : ratio vs median_close ET ratio intrabar H/C | 2026-02-21 |
| **TD-001** | `daily_dd_pct` divisé par `start_balance` → guards DD jamais déclenchés | `/ daily_start_balance` dans `guards.py` | 2026-02-20 |
| **TD-002** | `EXIT_TRAILING` jamais tagué → tous les exits étiquetés `EXIT_SL` | Discrimination `trailing_active or breakeven_set` dans `manager.py` | 2026-02-20 |
| **TD-007** | Alias `tv_close`/`tv_open` → crash `AttributeError` en live | `signal.close` partout + `Signal.from_webhook_json` ajouté | 2026-02-20 |
| **TD-011** | Résidus `signal.tv_close` dans orchestrator, adapters, parquet_clock | Nettoyage complet + classmethod | 2026-02-20 |
| **TD-008** | Calcul ADX dupliqué dans signal_gen.py ET signal_gen_trend.py | Module `arabesque/indicators.py` unifié | 2026-02-21 |
| **TD-005** | `runner.py` déprécié (remplacé par `engine.py`) | Supprimé | 2026-02-21 |
| **TD-006** | `bar_poller.py` déprécié (remplacé par `price_feed.py`) | Supprimé | 2026-02-21 |
| — | Guard slippage rejetait 96% des signaux | Comparer `fill` vs `open_next_bar` | 2026-02-18 |
| — | `sig.tp` → AttributeError | `sig.tp_indicative` | 2026-02-18 |
| — | 0 signaux en dry-run replay | `only_last_bar=False` + `_seen_signals` | 2026-02-18 |
| — | 55+ trades WR 25% (doublons) | Set `_seen_signals` par timestamp | 2026-02-18 |
| — | `git push --force` a écrasé un commit | Règle : jamais `--force` sur main | 2026-02-18 |
