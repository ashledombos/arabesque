# ARABESQUE — Handoff Document v2
## Pour reprendre le développement dans un nouveau chat

---

## 1. Contexte : pourquoi Arabesque existe

**Raph** développe un système de trading quantitatif pour prop firms (FTMO, Goat Funded Trader).

**Envolées** (le système précédent) utilisait des breakouts Donchian sur 4H. Après des semaines de diagnostic (5 versions de diagnostic cross-instrument, correction de biais d'entrée sur gaps), **tous les configs validées sont devenues négatives ou nulles une fois les biais corrigés**. Le Donchian breakout n'a pas d'edge exploitable à 4H sur les instruments testés (NZDUSD, USDCZK, USDMXN, GBPUSD, AUDJPY, USDCNH, NZDJPY, EURNZD, USDSGD, EURCHF, GBPCHF, USDZAR, EURAUD, EURNOK, CHFJPY, EURJPY, EURHUF, USDCHF).

**Raison fondamentale** : l'asymétrie d'exécution. Le breakout achète quand le prix MONTE (slippage adverse, tout le monde voit le même niveau). Le mean-reversion achète quand le prix DESCEND (slippage neutre ou favorable, moins de concurrence).

**BB_RPB_TSL** (Freqtrade, crypto) a été analysé en détail : 527 jours live, 48% CAGR, 90.8% WR, 20.8% DD. Son edge vient du pipeline complet, pas d'un indicateur isolé : 20+ signaux mean-reversion, trailing à paliers, giveback contextuel, deadfish, slippage filter.

**Arabesque** = extraction des principes de BB_RPB_TSL, adaptée aux prop firms (FX/indices) avec contraintes de drawdown strict.

---

## 2. Architecture décidée

```
TradingView (Pine 1H)           Python (Webhook)              Brokers
┌──────────────────┐   JSON    ┌──────────────────────┐      ┌─────────┐
│ BB excess detect │ ────────→ │ Guards               │ ───→ │ cTrader │
│ Regime HTF (4H)  │  webhook  │   ├ prop (DD/sizing)  │      │ (FTMO)  │
│ RSI / CMF / ATR  │           │   ├ spread/slippage   │      ├─────────┤
│ Anti-lookahead   │           │   └ expiry/duplicate   │      │ TradeLo │
└──────────────────┘           │ Position Manager      │      │ (GFT)   │
                               │   ├ OHLC intrabar ✓   │      └─────────┘
                               │   ├ Trailing paliers  │
                               │   ├ Giveback (MFE+RSI)│
                               │   ├ Deadfish          │
                               │   └ Time-stop         │
                               │ Audit Logger          │
                               │   └ Contrefactuels    │
                               └──────────────────────┘
```

**Principes clés** :
- Pine = détection pure (signal + contexte), Python = toute la gestion
- 4H = régime (autorisation), 1H = signal (excès)
- Même PositionManager pour live ET backtest (zéro divergence)
- Tout en R/ATR (invariant d'instrument), pas en %
- SL ne descend JAMAIS (LONG) / ne monte JAMAIS (SHORT)
- Trailing paliers : +0.5R→0.3R, +1R→0.5R, +1.5R→0.8R, +2R→1.2R, +3R→1.5R
- Guards toujours actifs (broker quote obligatoire, même en dry-run)
- Audit + contrefactuels pour calibrer les guards

---

## 3. État actuel : Arabesque v2 (consolidation v0.1 + v1)

### Ce qui a été fait

**v0.1** apportait :
- Modèles propres (Signal, Decision, Counterfactual comme types séparés)
- Screener pass 1 (MFE/MAE sans simulation d'exécution)
- `recalculate_from_fill()` (SL/TP ajustés au fill broker réel)

**v1** apportait :
- Pipeline complet Pine → webhook → guards → broker → manager → audit
- Broker adapters (cTrader, TradeLocker, DryRun)
- Pine signal generator avec anti-lookahead

**v2 consolide et corrige** :

| Bug v1 | Correction v2 |
|--------|--------------|
| `update(price)` = close only → SL/TP ratés intrabar | `update_position(high, low, close)` + règle conservatrice |
| SL+TP même bougie = non géré | Pire cas = SL (standard conservateur) |
| `broker_quote = None` → guards spread/slip inactifs | Quote obligatoire (DryRunAdapter fournit un quote réaliste) |
| `AccountState` à 0 → sizing = 0 | Initialisé à 100k par défaut |
| Williams %R = `percentrank * -1` (faux) | Vrai W%R : `(HH - close) / (HH - LL) * -100` |
| `recalculate_from_fill` absent | Intégré dans Position.recalculate_from_fill() |
| Counterfactual ne gère que close | Counterfactual.update(high, low, close) + pire cas |

### Fichiers v2

```
arabesque_v2/
├── arabesque/
│   ├── models.py              # Signal, Decision, Position, Counterfactual
│   ├── guards.py              # Guards prop + exec, sizing, AccountState
│   ├── audit.py               # JSONL logger + counterfactual tracking
│   ├── screener.py            # Pass 1 : MFE/MAE sans exécution (de v0.1)
│   ├── position/
│   │   └── manager.py         # PositionManager : OHLC, trailing, giveback, deadfish
│   ├── broker/
│   │   └── adapters.py        # Interface + cTrader/TradeLocker/DryRun stubs
│   └── backtest/
│       └── (vide, à créer)    # Runner backtest pass 2
├── pine/
│   └── arabesque_signal.pine  # Signal generator TradingView (W%R corrigé)
└── test_v2.py                 # Test pipeline complet
```

### Test v2 (résultat)

```
Guards: PASS — All guards passed
Sizing: risk_cash=500.0, risk_dist=0.00150
Position opened: EURUSD LONG  Entry(fill): 1.07660  SL(recalc): 1.07510  R=0.00150

Bar  3: BE activé MFE=0.50R, SL 1.07510 → 1.07691
Bar  5: Trailing tier 3, SL → 1.07760
Bar  6: High=1.07882(MFE 1.48R) mais Low=1.07758 < SL → EXIT SL @ 1.07760 = +0.67R
```

Le trailing protège le profit réalisé. Sans OHLC intrabar, v1 aurait vu close=1.07830 et gardé la position ouverte (biais optimiste).

---

## 4. Ce qui reste à faire (par priorité)

### P0 — Backtest pass 2 (CRITIQUE, bloquant)

Le screener (pass 1) donne les distributions MFE/MAE. Mais on a besoin d'un **backtest pass 2** qui utilise le MÊME PositionManager que le live pour simuler :
- Spread + slippage réalistes
- Contraintes prop (DD daily/max, sizing, lots arrondis)
- Gestion dynamique (BE, trailing, giveback, deadfish, time-stop)

**Architecture** : `arabesque/backtest/runner.py` qui itère sur un DataFrame OHLC et appelle `PositionManager.update_position()` à chaque bougie. Même code, même machine à états.

**Données** : Yahoo Finance 1H (comme Envolées). Attention aux gaps weekend et aux barres manquantes.

**Métriques requises** :
- Expectancy (R et cash)
- Profit Factor
- Max DD (equity curve)
- **Jours disqualifiants** (DD_daily ≥ 3% ou DD_total ≥ 8%)
- Slippage sensitivity (Δ expectancy quand on 1.5x/2x/3x le slippage)

**Protocole** :
- In-sample : 70% des données (train)
- Out-of-sample : 30% (validation)
- Walk-forward optionnel (fenêtre glissante)
- Min 30 trades pour significativité

### P1 — Instruments à tester

Candidats mean-reversion pour prop firms :
- FX majeures : EURUSD, GBPUSD, USDJPY, AUDUSD (spread serré, bien documenté)
- Métaux : XAUUSD (volatilité exploitable)
- Indices : US30, NAS100, GER40 (réversion intraday)
- Crypto FTMO : BTC, ETH (si disponible)

### P2 — Brancher les vrais brokers

Les adapters cTrader et TradeLocker sont des stubs. Le code de connexion existant est dans `envolees-auto` sur le serveur de Raph :
- `envolees-auto/brokers/ctrader.py` (cTrader Open API, async + protobuf)
- `envolees-auto/brokers/tradelocker.py` (REST API + lib Python)
- `envolees-auto/webhook/server.py` (Flask)
- `envolees-auto/config/settings.yaml` (credentials)

Il faut : copier la logique de connexion dans les méthodes `connect()`, `place_order()`, `compute_volume()`, `modify_sl()` des stubs v2.

### P3 — Module trend opportuniste (optionnel)

Pas du Donchian pur. Plutôt : squeeze BB (bandes comprimées N barres) → expansion (BB width monte + ADX monte). Entrée sur confirmation de changement de régime, pas sur breakout de canal. Même Position Manager, trailing plus agressif.

### P4 — Live testing instrumenté

Paper trading avec audit + contrefactuels. Le système tourne en dry-run, reçoit les vrais signaux TV, applique les guards, et loggue ce qui se serait passé. Permet de calibrer les guards sans risquer de capital.

---

## 5. Leçons clés à ne pas oublier

### Sur le backtesting
- **Impossible sans aucun biais**, mais possible de réduire 90% des biais graves
- **Chaîne** : backtest massif → paper trading → live small size
- **Granularité** : simulation à la clôture 1H, SL/TP vérifiés sur high/low, pire cas si ambiguïté
- Le backtest est légèrement pessimiste → c'est sain

### Sur l'edge
- L'edge n'est pas un indicateur, c'est un **pipeline** (entrée + gestion + exécution)
- Mean-reversion > breakout à court terme sur instruments liquides (asymétrie d'exécution)
- BB_RPB_TSL : 100+ paramètres hyperopt = risque overfitting massif (le README le dit)
- 527 jours live en crypto bull ≠ garantie FX/indices
- Les périodes sans signal sont normales et bénéfiques

### Sur les prop firms
- 3% daily DD = game over → max 3 positions × 0.5% = 1.5% exposure max
- Sizing = start_balance × risk% (pas equity courante)
- Lot arrondis toujours vers le bas (never round up)
- Le worst-case daily (tous les SL le même jour) doit rester sous 2.5%

### Sur l'exécution
- Le fill broker ≠ tv_close → recalculer SL/TP depuis le fill réel
- Spread et slippage vérifié AVANT chaque trade (pas optional)
- Signal expiré > 5 min = rejeté
- Un instrument déjà en position = pas de doublon

---

## 6. Infra existante (serveur de Raph)

- Serveur : hodo (54.38.94.152), user `raphael`
- Python venv : `/home/raphael/dev/envolees-auto/venv/`
- Services systemd utilisateur pour webhook et cleaner
- Brokers : FTMO (cTrader, demo.ctraderapi.com:5035), GFT (TradeLocker, bsb.tradelocker.com)
- Alertes : Telegram + ntfy
- TradingView : alertes webhook vers le serveur

---

## 7. Références

- **BB_RPB_TSL code** : 1202 lignes Freqtrade, analysé en détail (signals, custom_stoploss, custom_exit, confirm_trade_entry)
- **Moskowitz et al. 2012** : trend-following académique (monthly rebalancing, 58 instruments, 25+ ans) — explique pourquoi ça marche en macro mais pas en intraday Donchian
- **Diagnostic Envolées** : 6 versions, gap fill bias discovery, tous les configs négatives post-correction
- **Transcripts complets** : dans le projet Claude "Arabesque", conversations du 11-14 février 2026

---

## 8. Pour reprendre : instructions au prochain chat

Copier-coller ce document comme premier message, puis :

1. **Si tu veux construire le backtest pass 2** :
   > "Voici le handoff Arabesque v2. J'ai le zip v2 avec le PositionManager corrigé (OHLC intrabar). Construis le runner backtest qui utilise le même manager, avec Yahoo Finance 1H, contraintes prop, et métriques (expectancy, PF, DD, jours disqualifiants, slippage sensitivity)."

2. **Si tu veux brancher les vrais brokers** :
   > "Voici le handoff Arabesque v2. J'ai le code envolees-auto sur mon serveur avec les brokers cTrader/TradeLocker fonctionnels. Aide-moi à les intégrer dans les stubs v2."

3. **Si tu veux lancer le paper trading** :
   > "Voici le handoff Arabesque v2. Le Pine est prêt, le webhook aussi. Configure le service systemd pour le dry-run et montre comment interpréter les logs d'audit."
