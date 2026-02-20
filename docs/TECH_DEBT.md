# Arabesque — Dette Technique

> Ce fichier recense la dette technique connue : code à nettoyer, biais à corriger, refactors à faire.
> Mis à jour à chaque session. Item résolu → déplacer en section "Résolus" avec date.

---

## 🔴 Critique (bloquant avant tout déploiement live)

### TD-001 — `daily_dd_pct` divisé par le mauvais dénominateur

| Champ | Valeur |
|---|---|
| **Fichier** | `arabesque/guards.py` |
| **Symptôme** | Guards DD (journalier 3%, total 10%) ne se déclenchent jamais |
| **Cause** | `daily_dd_pct = (daily_start_balance - equity) / start_balance` — doit être `/ daily_start_balance` |
| **Impact** | Bot peut brûler le compte challenge sans jamais stopper |
| **Fix** | Remplacer `start_balance` par `daily_start_balance` dans le calcul du `daily_dd_pct` |
| **Validation** | Replay 3 mois : chercher `"rejected DAILY_DD_LIMIT"` dans les logs |
| **Priorité** | P0 — ne pas déployer en live sans ce fix |

---

## 🟠 Haute (impacte la fiabilité des stats)

### TD-002 — `EXIT_TRAILING` jamais utilisé

| Champ | Valeur |
|---|---|
| **Fichier** | `arabesque/position/manager.py` (`_check_sl_tp_intrabar`) |
| **Symptôme** | Toutes les sorties sont étiquetées `EXIT_SL`, qu'il s'agisse d'une perte (-1R) ou d'un gain trailing (+0.5R) |
| **Cause** | `DecisionType.EXIT_TRAILING` existe dans l'enum mais n'est jamais appelé |
| **Impact** | WR, PF, expectancy par type de sortie tous faux. Bloque la décision TP fixe vs TSL. |
| **Fix** | Dans `_check_sl_tp_intrabar` : `if pos.trailing_active and pos.result_r > 0 → EXIT_TRAILING` |
| **Priorité** | P1 |

### TD-003 — `orchestrator.get_status()` exception silencieuse

| Champ | Valeur |
|---|---|
| **Fichier** | `arabesque/live/` (orchestrator) |
| **Symptôme** | Le résumé final (balance, equity, nb trades) peut lever une exception silencieuse en fin de replay |
| **Cause** | Exception non capturée, pas de fallback |
| **Impact** | Résumé final non fiable, impossible de valider automatiquement un replay |
| **Fix** | Wrapper `try/except` + log explicite de l'erreur |
| **Priorité** | P2 |

### TD-004 — `tv_close` = dernier close du cache (pas le close du signal)

| Champ | Valeur |
|---|---|
| **Fichier** | `arabesque/backtest/signal_gen_combined.py` (`_generate_signals_from_cache`) |
| **Symptôme** | Sur les replays historiques longs, `tv_close` est le close de la dernière bougie du cache, pas celui de la bougie du signal |
| **Cause** | `tv_close = bars[-1]["close"]` au lieu de `df.iloc[idx]["Close"]` |
| **Impact** | RR légèrement faux (rare en live car le cache est court) |
| **Fix** | Utiliser l'index du signal pour récupérer le close |
| **Priorité** | P2 |

---

## 🟡 Moyenne (code legacy, duplication)

### TD-005 — `arabesque/live/runner.py` déprécié

| Champ | Valeur |
|---|---|
| **Fichier** | `arabesque/live/runner.py` |
| **Symptôme** | Remplacé par `arabesque/live/engine.py` mais toujours présent |
| **Impact** | Confusion sur le point d'entrée live, risque d'utiliser l'ancien |
| **Fix** | Supprimer `runner.py` après vérification qu'aucun script ne l'importe |
| **Priorité** | P7 |

### TD-006 — `arabesque/live/bar_poller.py` déprécié

| Champ | Valeur |
|---|---|
| **Fichier** | `arabesque/live/bar_poller.py` |
| **Symptôme** | Remplacé par `arabesque/live/price_feed.py` |
| **Impact** | Code mort, confusion |
| **Fix** | Supprimer après vérification |
| **Priorité** | P7 |

### TD-007 — Alias `tv_close` / `tv_open` dans `models.py`

| Champ | Valeur |
|---|---|
| **Fichier** | `arabesque/models.py` |
| **Symptôme** | `tv_close` et `tv_open` existent comme propriétés alias de `close` et `open_` |
| **Cause** | Héritage d'une architecture TradingView webhook abandonnée |
| **Impact** | Confusion sur les noms — a déjà causé le bug TD-004 |
| **Fix** | Supprimer les propriétés après grep complet (`git grep tv_close`) |
| **Priorité** | P7 |

### TD-008 — Calcul ADX dupliqué

| Champ | Valeur |
|---|---|
| **Fichiers** | `arabesque/backtest/signal_gen.py` ET `arabesque/backtest/signal_gen_trend.py` |
| **Symptôme** | ADX calculé deux fois avec potentiellement des paramètres différents |
| **Impact** | Résultats potentiellement incohérents, maintenance difficile |
| **Fix** | Extraire dans une fonction commune dans un module `arabesque/indicators.py` |
| **Priorité** | P7 |

### TD-009 — Pas de script `run_all_stats.py`

| Champ | Valeur |
|---|---|
| **Fichier** | `scripts/` |
| **Symptôme** | Lancer `run_stats.py` sur les 17 viables nécessite une boucle bash manuelle |
| **Impact** | Friction à chaque run |
| **Fix** | Créer `scripts/run_all_stats.py` qui boucle sur `config/instruments.yaml` (follow: true) |
| **Priorité** | P7 |

### TD-010 — Corrélation inter-instruments non gérée

| Champ | Valeur |
|---|---|
| **Fichier** | `arabesque/guards.py` |
| **Symptôme** | Événement 10/10/2025 : krach crypto simultané, RSI <20 sur 15 instruments en même direction. Les 5 positions ouvertes SL toutes en 1 barre. |
| **Impact** | `max_open_risk_pct` contient le DD mais un guard "direction corrélée" filtrerait mieux |
| **Fix** | Guard : si >N instruments crypto signalent dans la même direction sur la même bougie → bloquer |
| **Priorité** | P8 (non prioritaire tant que `max_open_risk_pct` est actif) |

---

## 🟢 Résolus

| ID | Description | Fix | Date |
|---|---|---|---|
| — | `sig.tp` → `AttributeError` | Renommer en `sig.tp_indicative` | 2026-02-18 |
| — | Guard slippage rejetait 96% des signaux | Comparer `fill` vs `open_next_bar` | 2026-02-18 |
| — | `np.float64` dans dict signal | Cast `float()` partout | 2026-02-18 |
| — | Colonne `"ema200"` inexistante | Essaie `"ema200"` puis `"ema_slow"` | 2026-02-18 |
| — | RR calculé sur close courant | `df.iloc[idx]["Close"]` | 2026-02-18 |
| — | `tv_close=`/`tv_open=` dans `Signal.__init__()` | `close=` / `open_=` | 2026-02-20 |
| — | 0 signaux en dry-run replay | `only_last_bar=False` + `_seen_signals` | 2026-02-18 |
| — | 55+ trades WR 25% (doublons) | Set `_seen_signals` par timestamp | 2026-02-18 |
| — | `git push --force` a écrasé un commit | Règle : jamais `--force` sur `main` | 2026-02-18 |
| — | `open_risk_cash` non branché dans orchestrator | Patch `afb062d` : branché ouverture/fermeture | 2026-02-18 |
| — | SyntaxError backslash en f-string Python <3.12 | Constantes `EMOJI_GREEN`/`EMOJI_RED` | 2026-02-19 |
