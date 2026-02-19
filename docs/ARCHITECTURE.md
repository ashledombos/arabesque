# Arabesque — Architecture détaillée

> Document technique pour développeurs. Pour la passation, lire [HANDOVER.md](../HANDOVER.md) en premier.

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Module `live` — Runner et sources de barres](#2-module-live--runner-et-sources-de-barres)
3. [Module `backtest` — Générateurs de signaux](#3-module-backtest--générateurs-de-signaux)
4. [Module `position` — PositionManager](#4-module-position--positionmanager)
5. [Module `webhook` — Orchestrateur](#5-module-webhook--orchestrateur)
6. [Module `broker` — Adapters](#6-module-broker--adapters)
7. [Anti-lookahead : le contrat fondamental](#7-anti-lookahead--le-contrat-fondamental)
8. [Flux de données complet](#8-flux-de-données-complet)
9. [Format JSONL export](#9-format-jsonl-export)
10. [Configuration](#10-configuration)

---

## 1. Vue d'ensemble

```
┌─────────────────────────────────────────────────────────────┐
│  arabesque.live.runner  (point d'entrée CLI)                │
│                                                             │
│  --source parquet ──► ParquetClock                         │
│                           │                                 │
│  --source ctrader ──► BarPoller ◄── cTrader Open API        │
│                           │                                 │
│              ┌────────────▼────────────┐                    │
│              │      Orchestrator        │                    │
│              │  ┌──────────────────┐   │                    │
│              │  │  Guards          │   │  ◄── limits prop   │
│              │  │  Sizing (R-based)│   │                    │
│              │  │  PositionManager │   │  ◄── trailing 5p   │
│              │  │  Broker Adapter  │   │  ◄── DryRun/live   │
│              │  └──────────────────┘   │                    │
│              └─────────────────────────┘                    │
│                           │                                 │
│              ┌────────────▼────────────┐                    │
│              │  Export JSONL + Résumé  │                    │
│              └─────────────────────────┘                    │
└─────────────────────────────────────────────────────────────┘
```

**Invariant central** : `CombinedSignalGenerator` et `PositionManager` ont
exactement le même code quelle que soit la source de barres (Parquet ou cTrader live).
Un résultat obtenu en replay Parquet est directement comparable à un résultat live.

---

## 2. Module `live` — Runner et sources de barres

### `runner.py`

Point d'entrée CLI. Responsabilités :
- Parser les arguments (`--mode`, `--source`, `--strategy`, `--start`, `--end`, `--speed`)
- Instancier `ArabesqueConfig` depuis les args ou variables d'environnement
- Instancier le bon `SignalGenerator` selon `--strategy`
- Instancier le `BrokerAdapter` selon `--mode`
- Instancier et démarrer `ParquetClock` ou `BarPoller`

### `parquet_clock.py` — Replay Parquet

Simulateur de marché temps-réel depuis fichiers Parquet locaux.

**Algorithme principal (`_replay`)** :

```
1. Charger tous les DataFrames (period = [start, end+1jour])
2. Construire une liste d'events (ts, instrument, row) triée chronologiquement
3. Pour chaque event :
   a. Ajouter la bougie au cache (max 300 barres)
   b. EXÉCUTER les signaux pending (générés sur la bougie précédente)
      → fill au OPEN de la bougie courante (anti-lookahead)
   c. UPDATE_POSITIONS (high, low, close de la bougie courante)
   d. GÉNÉRER les signaux sur la bougie courante
      → filtrer par _seen_signals (timestamps déjà vus)
      → stocker dans _pending_signals
4. _print_summary() + export JSONL
```

**Paramètres clés** :

| Paramètre | Défaut | Rôle |
|-----------|--------|------|
| `replay_speed` | `0.0` | Délai entre bougies en secondes (0 = max speed) |
| `min_bars_for_signal` | `50` | Barres minimum avant génération de signal |
| `_seen_signals` | `{}` | Set de timestamps par instrument — évite les doublons |
| `_pending_signals` | `{}` | Signaux en attente d'exécution à la prochaine bougie |

**Extension de période** : si `--end` est fourni, la période chargée est
automatiquement étendue de +1 jour (`end_extended`) pour capturer les fills
des signaux générés sur les dernières bougies.

### `bar_poller.py` — Stream live cTrader

Poll les barres H1 fermées depuis cTrader toutes les 60 secondes.
Même interface que `ParquetClock` côté `Orchestrator`.

Fonctions exportées utilisées par `ParquetClock` :
- `_generate_signals_from_cache(instrument, bars, sig_gen, only_last_bar)` → `list[dict]`
- `DEFAULT_INSTRUMENTS` → liste des 19 instruments supportés

---

## 3. Module `backtest` — Générateurs de signaux

Tous les générateurs prennent un `DataFrame` OHLCV H1 et retournent une
liste de `Signal`. Appelés via `_generate_signals_from_cache` qui construit
le DataFrame à partir du cache de barres.

### `CombinedSignalGenerator` (recommandé)

Agrège les trois stratégies. Applique :
- Déduplication par instrument (une seule position ouverte par instrument)
- Limite `max_positions` (5 simultanées par défaut)
- Priorité : Trend > MeanReversion > Breakout si conflit

```python
class CombinedSignalGenerator:
    def generate_signals(self, df: pd.DataFrame, instrument: str) -> list[Signal]:
        signals = []
        signals += self._trend.generate_signals(df, instrument)
        signals += self._mean_rev.generate_signals(df, instrument)
        signals += self._breakout.generate_signals(df, instrument)
        return self._deduplicate(signals)
```

### `BacktestSignalGenerator` — Mean-Reversion

Basée sur [BB_RPB_TSL](https://github.com/freqtrade/freqtrade-strategies).

**Conditions LONG** :
```
close[-1] < bb_lower[-1]   # prix sous la bande basse
RSI[-1] < 35               # survente
not bear_trend              # pas de tendance baissière confirmée
bb_width > 0.003            # bandes pas trop étroites
```

**Conditions SHORT** : miroir (prix > bb_upper, RSI > 65, not bull_trend)

**SL** : `max(swing_low_7bars, close - 0.8 * ATR)` pour LONG
**TP** : BB mid (retour à la moyenne)

### `TrendSignalGenerator` — Trend

**Conditions LONG** :
```
bb_squeeze: bb_width < bb_width_ma   # compression récente
expansion:  bb_width > bb_width[-1]  # expansion en cours
cassure:    close > bb_upper          # prix casse la bande haute
ADX[-1] > 20                          # force de tendance
EMA_fast > EMA_slow                   # confirmation tendance
CMF > 0                               # flux acheteur
```

**SL** : 1.5 × ATR
**TP** : 2R indicatif (trailing gère)

---

## 4. Module `position` — PositionManager

Gère le cycle de vie de chaque position ouverte.
Appelé par `orchestrator.update_positions()` à chaque nouvelle bougie.

### Trailing 5 paliers

```python
TRAILING_TIERS = [
    # (mfe_seuil, sl_depuis_high)
    (3.0, 1.5),   # MFE > 3R → SL à 1.5R du high
    (2.0, 1.2),
    (1.5, 0.8),
    (1.0, 0.5),
    (0.5, 0.3),
]
```

Pour chaque bougie :
1. Calculer MFE courant
2. Trouver le palier applicable
3. Calculer le nouveau SL candidat
4. `sl_new = max(sl_actuel, sl_candidat)` pour LONG (le SL ne recule jamais)

### Sorties

Dans l'ordre de priorité :
1. **TP** : `low <= tp` (LONG) — exit immédiate
2. **SL** : `low <= sl` (LONG) — exit immédiate
3. **Giveback** : `(mfe - current_profit) / mfe > 0.5` — le trade a rendu >50% du MFE
4. **Deadfish** : range sur N barres < 0.5R (trade stagnant)
5. **Time-stop** : `bars_open > max_bars` (48 barres = 48h)

### Invariants

- SL LONG : ne peut que **monter** (`sl = max(sl_actuel, sl_nouveau)`)
- SL SHORT : ne peut que **descendre** (`sl = min(sl_actuel, sl_nouveau)`)
- `result_r = (exit_price - entry) / (entry - sl_initial)` pour LONG

---

## 5. Module `webhook` — Orchestrateur

### `orchestrator.py`

Point de décision central. Reçoit les signaux et décide d'ouvrir/refuser.

**`handle_signal(sig_data: dict) → dict`** :

```
1. Guard: instrument déjà en position ? → reject "duplicate"
2. Guard: max_positions atteint ?        → reject "maxpositions"
3. Guard: DD daily > max_daily_dd_pct ?  → reject "dd_limit"
4. Guard: DD total > max_total_dd_pct ?  → reject "dd_limit"
5. Guard: max_daily_trades atteint ?     → reject "daily_trades"
6. Guard: slippage (fill vs signal) ?    → reject "slippage_too_high"
7. Sizing: calcul volume (risk_cash / (entry - sl) / contract_size)
8. Guard: volume calculé = 0 ?           → reject "volume_zero"
9. broker.place_order() → fill
10. manager.open_position()
11. audit.log()
→ return {"status": "accepted", "position_id": "..."}
```

**`update_positions(instrument, high, low, close)`** :

Appelle `manager.update()` pour chaque position ouverte sur l'instrument.
Si une position est fermée, appelle `broker.close_order()` et `audit.log()`.

### `server.py` — API Flask

Utilisé uniquement en mode TradingView webhook (legacy, non utilisé en mode Parquet).

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/webhook` | POST | Signal TradingView |
| `/update` | POST | Update OHLC pour positions |
| `/status` | GET | État système |
| `/positions` | GET | Liste positions |
| `/health` | GET | Healthcheck |

---

## 6. Module `broker` — Adapters

Interface abstraite `BrokerAdapter` avec méthodes :
- `place_order(instrument, side, volume, entry, sl, tp, label) → OrderResult`
- `close_order(position_id, price) → None`
- `get_account_info() → AccountInfo`

### `DryRunAdapter`

Simule les fills sans connexion réseau. Fill au prix `sig_data["tv_close"]`
(remplacé par le OPEN de la bougie suivante dans `ParquetClock`).

### `CTraderAdapter`

Connexion via cTrader Open API (OAuth2 + WebSocket).
Variables requises : `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`,
`CTRADER_ACCESS_TOKEN`, `CTRADER_ACCOUNT_ID`.

---

## 7. Anti-lookahead : le contrat fondamental

C'est la règle la plus importante du système. Toute violation introduit un biais
optimiste dans les backtests (les résultats réels seront pires).

**Règle** : un signal ne peut utiliser que des données disponibles à la fermeture
de la bougie sur laquelle il est généré.

```
Bougie i fermée à 10:00
  → Signal généré sur df[:-1] (toutes barres jusqu'à 10:00 INCLUSE)
  → Stocké dans _pending_signals

Bougie i+1 ouvre à 11:00
  → Open de 11:00 connu (premier tick)
  → Fill simulé au Open(i+1) ← prix réaliste
  → Update positions avec High/Low/Close(i+1)
```

**Ce qui serait FAUX** (lookahead) :
- Fill au Close(i) — le close n'est connu qu'à la fermeture
- Utiliser High/Low/Close(i+1) pour décider d'ouvrir

**Implémentation dans `ParquetClock._replay()`** :
```python
# Ordre des opérations pour chaque bougie :
1. cache.append(bar)                          # ajouter bougie courante
2. execute_pending_signals(open=bar.open)     # fill au OPEN
3. orchestrator.update_positions(H, L, C)    # update avec données courantes
4. signals = generate_signals(cache)          # signal pour la PROCHAINE bougie
5. _pending_signals[instrument].extend(signals)
```

---

## 8. Flux de données complet

```
Fichier Parquet
  XRPUSD_H1.parquet
     │
     │  load_ohlc(instrument="XRPUSD", prefer_parquet=True)
     │
     ▼
  DataFrame OHLCV (colonnes : Open, High, Low, Close, Volume, index=DatetimeIndex)
     │
     │  (glissant : cache = list des 300 dernières barres)
     │
     ▼
  _generate_signals_from_cache(instrument, bars, sig_gen, only_last_bar=False)
     │
     │  CombinedSignalGenerator.generate_signals(df, "XRPUSD")
     │    ├── TrendSignalGenerator → [Signal(side="SHORT", sl=2.72, rr=2.0)]  ou []
     │    ├── MeanReversionGen     → [Signal(...)]  ou []
     │    └── BreakoutGen          → [Signal(...)]  ou []
     │
     ▼
  list[dict] sig_data = [
    {"instrument": "XRPUSD", "side": "SHORT", "tv_close": 2.677,
     "sl": 2.722, "tp": 2.34, "rr": 2.0, "ts": "2025-10-10T17:00:00"}
  ]
     │
     │  _pending_signals["XRPUSD"].append(sig_data)
     │  (bougie suivante)
     │  sig_data["tv_close"] = bar_next.open  ← fill réel
     │
     ▼
  orchestrator.handle_signal(sig_data)
     │
     │  Guards OK → DryRunAdapter.place_order()
     │            → manager.open_position(entry=2.496, sl=2.568, risk_cash=100.0)
     │
     ▼
  Position(instrument="XRPUSD", entry=2.496, sl=2.568, tp=1.867, risk_cash=100.0)
     │
     │  (bougies suivantes)
     │  orchestrator.update_positions("XRPUSD", H=2.24, L=2.15, C=2.20)
     │    → manager.update() → tp atteint ? sl atteint ? trailing ?
     │
     ▼
  Position fermée : result_r=+2.08, exit_reason="exit_tp", bars_open=1
     │
     ▼
  JSONL : {"type": "trade", "instrument": "XRPUSD", "result_r": 2.083, ...}
```

---

## 9. Format JSONL export

Fichier : `dry_run_YYYYMMDD_HHMMSS.jsonl`

### Ligne `trade`

```json
{
  "type": "trade",
  "instrument": "XRPUSD",
  "side": "SHORT",
  "entry": 2.496,
  "sl": 2.568,
  "result_r": 2.083,
  "risk_cash": 100.0,
  "exit_reason": "exit_tp",
  "bars_open": 1,
  "mfe_r": 17.2,
  "ts_entry": "2025-10-10T17:00:00+00:00",
  "ts_exit":  "2025-10-10T18:00:00+00:00"
}
```

### Ligne `summary` (dernière ligne)

```json
{
  "type": "summary",
  "strategy": "CombinedSignalGenerator",
  "period_start": "2025-10-01",
  "period_end": "2025-10-15",
  "start_balance": 10000.0,
  "final_equity": 11971.47,
  "pnl_cash": 1971.47,
  "pnl_pct": 19.7147,
  "max_dd_pct": 3.7555,
  "n_trades": 53,
  "win_rate": 56.6,
  "avg_win_r": 1.3172,
  "avg_loss_r": -0.8389,
  "expectancy_r": 0.3815,
  "total_r": 20.2195,
  "days_to_10pct": 2.9,
  "open_positions_at_end": 2,
  "pnl_by_instrument": {
    "XRPUSD": {"total_r": 4.08, "trades": 2},
    "...": "..."
  }
}
```

**exit_reason** possibles :

| Valeur | Signification |
|--------|---------------|
| `exit_sl` | SL touché (peut être en gain si trailing l'avait remonté) |
| `exit_tp` | TP touché |
| `exit_trailing` | Trailing SL touché après avoir avancé |
| `exit_giveback` | A rendu >50% du MFE |
| `exit_deadfish` | Stagnation : range < 0.5R sur N barres |
| `exit_time_stop` | Ouvert depuis plus de 48 barres |

---

## 10. Configuration

### Via variables d'environnement (prioritaire)

Voir tableau dans le README.

### Via `config/settings.yaml`

```yaml
mode: dry_run                 # dry_run | live
start_balance: 10000
risk_per_trade_pct: 1.0       # 1% du capital par trade
max_positions: 5              # max positions simultanées
max_daily_dd_pct: 5.0         # coupe le trading si DD daily > 5%
max_total_dd_pct: 10.0        # coupe le trading si DD total > 10%
max_daily_trades: 999         # 999 en dry-run, 5 en live

ctrader:
  host: demo.ctraderapi.com
  port: 5035
  client_id: ""
  client_secret: ""
  access_token: ""
  account_id: 0

notifications:
  telegram_token: ""
  telegram_chat_id: ""
  ntfy_topic: ""
```

### Instruments par défaut

Définis dans `bar_poller.py` → `DEFAULT_INSTRUMENTS` :

```python
DEFAULT_INSTRUMENTS = [
    "AAVUSD", "ALGUSD", "AVAUSD", "BCHUSD", "BNBUSD",
    "DASHUSD", "GRTUSD", "ICPUSD", "IMXUSD", "LNKUSD",
    "NEOUSD", "NERUSD", "SOLUSD", "UNIUSD", "VECUSD",
    "XAUUSD", "XLMUSD", "XRPUSD", "XTZUSD",
]
```

Pour surcharger : `--instruments XRPUSD SOLUSD BNBUSD`
