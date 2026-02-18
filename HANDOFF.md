# ARABESQUE — Handoff Document v3
## Pour reprendre le développement dans un nouveau chat

> **Repo** : https://github.com/ashledombos/arabesque  
> **Branche principale** : `main`  
> **Dernière mise à jour** : 2026-02-18

---

## 1. Contexte : pourquoi Arabesque existe

**Raph** développe un système de trading quantitatif pour prop firms (FTMO, Goat Funded Trader).

**Envolées** (le système précédent) utilisait des breakouts Donchian sur 4H. Après diagnostic complet, tous les configs validées sont devenues négatives une fois les biais corrigés. Le Donchian breakout n'a pas d'edge exploitable sur les instruments testés.

**Raison fondamentale** : l'asymétrie d'exécution. Le breakout achète quand le prix MONTE (slippage adverse). Le mean-reversion achète quand le prix DESCEND (slippage neutre ou favorable).

**BB_RPB_TSL** (Freqtrade, crypto) a été analysé en détail : 527 jours live, 48% CAGR, 90.8% WR, 20.8% DD. Son edge vient du pipeline complet, pas d'un indicateur isolé.

**Arabesque** = extraction des principes de BB_RPB_TSL, adaptée aux prop firms avec contraintes de drawdown strict.

---

## 2. Architecture

```
ParquetClock / cTrader H1 stream
        │
        ▼  bougie fermée
_generate_signals_from_cache(instrument, bars, sig_gen)
        │
        ├── CombinedSignalGenerator.prepare(df)          # indicateurs
        ├── CombinedSignalGenerator.generate_signals()   # → (bar_idx, Signal)
        └── _signal_to_webhook_dict(sig, ...)            # → dict pour Orchestrator
                │
                ▼
        Orchestrator.handle_signal(dict)
                │
                ├── Guards (DD, spread, slippage, duplicate, sizing)
                └── position ouverte → DryRunAdapter / CTraderAdapter

        Orchestrator.update_positions(instrument, high, low, close)
                └── trailing paliers, breakeven, giveback, deadfish, time-stop
```

**Principes clés** :
- Même `CombinedSignalGenerator` en backtest, replay parquet et live cTrader
- Tout en R/ATR (invariant d'instrument)
- SL ne descend JAMAIS (LONG) / ne monte JAMAIS (SHORT)
- Trailing paliers : +0.5R→BE, +1R→0.5R, +1.5R→0.8R, +2R→1.2R, +3R→1.5R
- Guards toujours actifs
- Un seul trade simultané par instrument (`duplicate_instrument`)

---

## 3. État du code (2026-02-18)

### Fichiers principaux

```
arabesque/
├── models.py                  # Signal, Decision, Position, Counterfactual
├── guards.py                  # Guards prop + exec, sizing, AccountState
├── audit.py                   # JSONL logger + counterfactual tracking
├── orchestrator.py            # handle_signal() + update_positions()
├── broker/
│   └── adapters.py            # CTraderAdapter, DryRunAdapter
├── backtest/
│   ├── data.py                # load_ohlc() — charge parquets locaux
│   ├── signal_gen_combined.py # CombinedSignalGenerator (prepare + generate_signals)
│   └── runner.py              # Backtest pass 2
├── live/
│   ├── bar_poller.py          # BarPoller (cTrader H1 stream)
│   │                          # + _signal_to_webhook_dict()
│   │                          # + _generate_signals_from_cache()  ← partagé
│   ├── parquet_clock.py       # Replay parquets locaux (dry-run sans credentials)
│   └── runner.py              # CLI : --mode dry_run/live --source parquet/ctrader
scripts/
└── debug_pipeline.py          # Inspecte l'interface du sig gen (voir §5)
```

### Interface CombinedSignalGenerator

**`prepare(df)`** prend un DataFrame OHLCV et retourne 27 colonnes :
```
Open, High, Low, Close, Volume, date,
bb_mid, bb_lower, bb_upper, bb_width,
ema_fast, ema_slow,          ← ema_slow = EMA200 LTF (pas "ema200" !)
rsi, cmf, atr, wr_14,
swing_low, swing_high, adx, regime,
htf_ema_fast_val, htf_ema_slow_val, htf_adx,
squeeze, recent_squeeze, bb_expanding, adx_rising
```
⚠️ Les premières barres ont des NaN (période de chauffe) — normal.

**`generate_signals(df, instrument)`** → `list[(bar_index, Signal)]`

**Champs Signal utilisés** :
- `sl`, `tp_indicative` (pas `tp` !), `atr`, `rsi`, `cmf`
- `bb_lower/mid/upper/width`, `ema200_ltf`, `rr`
- `strategy_type` : `"mean_reversion"` ou `"trend"`
- `side` : `Side.LONG` ou `Side.SHORT` (enum, pas string)
- `tv_close` : close au moment du signal (souvent 0.0 — non renseigné par le sig gen → utiliser `df.iloc[idx]["Close"]`)

### Bugs corrigés en session 2026-02-18

| Bug | Correction |
|-----|------------|
| `sig.tp` → AttributeError | `sig.tp_indicative` |
| RR calculé sur close courant | RR calculé sur `sig.tv_close` ou `df.iloc[idx]["Close"]` |
| `np.float64` dans le dict signal | Cast `float()` natif partout |
| Colonne `"ema200"` inexistante | Essaie `"ema200"` puis `"ema_slow"` |
| `sig.tp` dans `_signal_to_webhook_dict` | `sig.tp_indicative` |

### Bug connu restant (à corriger)

**`tv_close` dans `_generate_signals_from_cache`** :
Actuellement `close = bars[-1]["close"]` (dernière bougie du cache).  
Doit être `close = float(df.iloc[idx]["Close"])` (close à l'index du signal).  
Impact : RR légèrement faux si le signal n'est pas sur la dernière bougie (rare en live, fréquent en replay historique long).

```python
# Dans _generate_signals_from_cache(), remplacer :
close  = bars[-1]["close"]
# par :
for idx, sig in last_signals:
    sig_close = float(df.iloc[idx]["Close"])
    ...
```

---

## 4. Résultats du replay dry-run (2026-02-18)

Commande :
```bash
python -m arabesque.live.runner \
  --mode dry_run --source parquet \
  --start 2025-06-01 \
  --instruments ALGUSD XTZUSD BCHUSD SOLUSD
```

**Observations** :
- ✅ Signaux générés et acceptés (`→ accepted pos_xxx`)
- ✅ Positions ouvertes avec fill, SL, volume, risk cash
- ✅ Trailing actif (`exit_trailing +0.37R`)
- ✅ Guard `duplicate_instrument` fonctionne (rejette un 2e signal sur BCHUSD déjà ouvert)
- ✅ Notifications NOTIFY avec émoji (✅ ouverture, 🔴 SL, 🟢 TP/trailing)
- ⚠️ Beaucoup de `exit_sl -1.00R` avec MFE faible → stratégie trend sur mauvaise période ou filtre régime trop permissif
- ⚠️ Risk cash décroît à chaque trade (compound correct mais à vérifier : Risk: $100 → $90 → $81 → ...)

**Guards observés actifs** :
- `duplicate_instrument` ✅
- DD guards : pas encore déclenchés sur l'échantillon visible

---

## 5. Comptes FTMO (situation 2026-02-18)

- **Compte live test gratuit 15j** : 100 000 USD, Hedged 1:30 — compte "Live" selon cTrader — **sans risque réel**, idéal pour tester les ordres dangereux
- **Compte challenge 100k** : 94 989 USD actuel, Hedged 1:30 — compte "Demo" selon cTrader — **argent réel payé** — max DD 10%, déjà à ~5.0% DD → marge restante ~5%

⚠️ **Ne pas connecter le bot live sur le compte challenge sans validation complète des Guards DD.**

---

## 6. Prochaines étapes (par priorité)

### P0 — Corriger `tv_close` dans `_generate_signals_from_cache` (5 min)
Voir §3 "Bug connu restant".

### P1 — Valider les Guards DD sur replay complet
```bash
# Replay sur 19 instruments, 3 mois
python -m arabesque.live.runner \
  --mode dry_run --source parquet \
  --start 2025-10-01 --end 2026-01-01
# Chercher dans les logs :
# - "rejected DAILY_DD_LIMIT"
# - "rejected MAX_DD_LIMIT"
# - Résumé final : balance, equity, open_positions
```

### P2 — Vérifier le résumé final du replay
Actuellement `orchestrator.get_status()` est appelé en fin de `parquet_clock.py` mais peut lever une exception silencieuse. Vérifier qu'il affiche bien balance/equity/nb trades.

### P3 — Connecter le compte test FTMO (live gratuit 15j)
```bash
# Copier les credentials dans .env
CTRADER_CLIENT_ID=...
CTRADER_CLIENT_SECRET=...
CTRADER_ACCOUNT_ID=17057523   # compte test live
CTRADER_HOST=live.ctraderapi.com
CTRADER_PORT=5035

python -m arabesque.live.runner --mode dry_run --source ctrader
# dry_run + source ctrader = vrais barres, DryRunAdapter (pas d'ordres réels)
```

### P4 — Premier ordre réel sur compte test
```bash
python -m arabesque.live.runner --mode live --source ctrader
# Vérifier dans cTrader que l'ordre apparaît, avec le bon SL/volume
```

### P5 — Analyse statistique du replay
- Expectancy (R), Profit Factor, Max DD equity curve
- Jours disqualifiants (DD_daily ≥ 3%)
- Taux de déclenchement Guards par type

---

## 7. Commandes utiles

```bash
# Debug interface signal gen
python scripts/debug_pipeline.py --instrument BCHUSD
python scripts/debug_pipeline.py --instrument XRPUSD --bars 500 --show-signals 5

# Replay rapide (4 instruments)
python -m arabesque.live.runner \
  --mode dry_run --source parquet \
  --start 2025-06-01 --instruments ALGUSD XTZUSD BCHUSD SOLUSD

# Replay lent observable
python -m arabesque.live.runner \
  --mode dry_run --source parquet \
  --start 2025-10-01 --end 2026-01-01 --speed 0.05

# Git : aligner local sur remote
git fetch origin && git reset --hard origin/main

# Git : pousser sans PR (workflow actuel)
git add . && git commit -m "..." && git push origin main
```

---

## 8. Infra

- Serveur : hodo, user `raphael`, `/home/raphael/dev/arabesque/`
- Parquets H1 : présents localement, chargés via `load_ohlc(instrument, prefer_parquet=True)`
- Alertes : Telegram + ntfy (configurés dans l'Orchestrator)
- Python : `.venv` dans le repo

---

## 9. Pour reprendre dans un nouveau chat

```
Lis le fichier HANDOFF.md dans le repo GitHub ashledombos/arabesque (branche main).
Contexte : système de trading algo Python pour prop firms FTMO.
Dernière session : pipeline live validé en dry-run parquet, signaux générés,
positions ouvertes, trailing actif, guard duplicate_instrument OK.
Prochain objectif : [voir §6 Prochaines étapes]
```
