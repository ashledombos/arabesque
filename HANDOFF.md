# ARABESQUE — Handoff v14
## Pour reprendre le développement dans un nouveau chat

> **Repo** : https://github.com/ashledombos/arabesque  
> **Dernière mise à jour** : 2026-02-28, session Opus 4.6 (order dispatch fix + weekend stale)

---

## ⭐ BOUSSOLE STRATÉGIQUE — Immuable

```
OBJECTIF : gains petits, fréquents, consistants.
           Win Rate élevé (cible ≥ 70%, idéal ≥ 85%).
           Expectancy positive par le volume.
STRATÉGIE : TREND-ONLY sur tout l'univers (forex + métaux + crypto).
RISK : 0.40% par trade (calibré sur DD max 20 mois).
```

---

## 1. RÉSULTAT DÉFINITIF — 20 mois, 76 instruments

```
Période   : Jul 2024 → Fév 2026 (600 jours)
Trades    : 1998
Win Rate  : 75.5%
Expectancy: +0.130R
Total R   : +260.2R
Max DD    : 20.5R (= 8.2% à 0.40%/trade)
PF        : 1.55
IC95      : +0.095R > 0 ✅
IC99      : +0.084R > 0 ✅
Score prop: 4/5 (seul échec: jours pour +10% = 58j > 45j)
```

### Par source de données
| Source | Instruments | N | WR | Exp | Total R |
|---|---|---|---|---|---|
| Dukascopy (forex+metals) | 49 (37 ✅) | 1122 | 76% | +0.103R | +115.2R |
| CCXT (crypto) | 27 (23 ✅) | 876 | 75% | +0.166R | +145.0R |

### Consistance temporelle (8 blocs de 250 trades)
Tous les 8 blocs positifs. Aucune période perdante sur 20 mois.

---

## 2. Parcours complet des replays

| # | Version | Période | Univers | N | WR | Exp | Total R |
|---|---|---|---|---|---|---|---|
| 1 | v3.0 combined | Oct→Jan | crypto 17 | 998 | 60% | +0.034 | +33.5 |
| 2 | v3.3 combined | Oct→Jan | diversifié 46 | 319 | 64% | -0.044 | -13.9 |
| 3 | v3.3 combined | Avr→Jul | crypto 17 | 169 | 65% | -0.083 | -14.1 |
| 4 | v3.3 trend | Avr→Jul | diversifié 39 | 570 | 71% | +0.037 | +21.2 |
| 5 | v3.3 trend | Oct→Jan | Dukascopy 19 | 171 | 75% | +0.109 | +18.6 |
| **6** | **v3.3 trend** | **Jul24→Fév26** | **76 inst** | **1998** | **75.5%** | **+0.130** | **+260.2** |

---

## 3. Leçons majeures — IMMUTABLES

### L1 : ROI court-terme + SL réel = piège mortel
Ne plus jamais utiliser de ROI court terme.

### L2 : Le BE est LE levier principal du WR
WR ≈ % des trades atteignant le trigger MFE (0.3R → ~75-78%).

### L3 : Mean Reversion ne fonctionne PAS (avec nos paramètres)
MR perd sur TOUTES les catégories sur les périodes testées.
Trend gagne sur TOUTES les catégories. **TREND-ONLY est définitif.**

### L4 : La conclusion "Dukascopy only" était PRÉMATURÉE
Sur 20 mois, crypto trend (+145R) SURPERFORME forex (+115R).
La période Avr-Jul était un drawdown localisé, pas structurel.
**L'univers complet (forex + métaux + crypto) est optimal.**

### L5 : Risk 0.50%/trade → DD 10.3% (DÉPASSE FTMO 10%)
Réduction à 0.40%/trade : DD 8.2%, return +104%. Marge de 1.8%.

### L6 : Anti-biais — règles non négociables
- Signal bougie `i`, exécution open bougie `i+1`
- Si SL ET TP touchés même bougie → SL pris (pessimiste)

### L7 : BE offset 0.20R (pas 0.15)
323/339 trailing exits sortaient à +0.15R exact. Offset trop serré.

### L8 : Pire streak = 9 trades perdants consécutifs
À 0.40%/trade = -3.6%. Pire fenêtre 50 trades = -16.9R = -6.8%.
Le système survit à ses pires périodes sous les limites FTMO.

---

## 4. Configuration v3.3

### Entrées (signal_gen.py)
- BB 20, std 2.0, typical_price
- RSI 14, oversold=35, overbought=65
- SL : swing 10 bars, fallback 1.5 ATR, min 1.5 ATR

### Sorties (manager.py)
- **BE** : trigger=0.3R, offset=0.20R
- **Giveback** : MFE≥0.5R, current<0.15R
- **Trailing** : 3 paliers (≥1.5R:0.7R, ≥2.0R:1.0R, ≥3.0R:1.5R)
- **ROI** : backstop (0:3.0R, 240:0.15R)

### Risk (guards.py)
- **risk_per_trade** : 0.40%
- **max_daily_dd** : 4.0%
- **max_total_dd** : 9.0% (safety margin 1%)
- **max_positions** : 5

---

## 5. Prochaines étapes

### P1 : MULTI-COMPTE PROP FIRM
Voir `config/prop_firm_profiles.yaml`.
- Distribution d'instruments entre comptes (pas de doublons même firm)
- Risk adapté par type de compte (FTMO Swing vs GFT vs crypto)
- Architecture: le signal generator produit, un dispatcher distribue

### P2 : MOTEUR DE RISQUE LIVE
- Daily loss limit avec kill switch automatique
- News filter (fenêtre ±2min)
- Règle de consistance (best day < 50% du profit)
- Vérification equity tracking DryRunAdapter (DD guards non fonctionnels en replay)

### P3 : FORWARD TEST
- Démo FTMO avec la config trend-only + risk 0.40%
- Monitoring de la correspondance backtest ↔ live
- Journal structuré pour audit trail

### P4 : OPTIMISATIONS
- Données 1-minute pour résolution intrabar
- Filtrage des instruments durablement perdants (USDZAR, GBPNZD, USDCNH)
- Walk-forward formel (PBO/Deflated Sharpe)

---

## 6. Scripts

| Script | Usage |
|---|---|
| `scripts/analyze_replay_v2.py FILE --grid` | Analyse complète + simulation BE/TP |
| `scripts/analyze_replay_v2.py FILE --compare FILE2` | Comparaison 2 replays |

---

## 7. Top 10 instruments (20 mois)

| Instrument | Trades | WR | Total R | Source |
|---|---|---|---|---|
| SOLUSD | 46 | 89% | +14.8 | CCXT |
| XAGUSD | 33 | 88% | +12.3 | Dukascopy |
| DOTUSD | 37 | 81% | +12.1 | CCXT |
| NEOUSD | 36 | 83% | +11.1 | CCXT |
| ALGOUSD | 40 | 80% | +11.0 | CCXT |
| USDMXN | 28 | 86% | +10.6 | Dukascopy |
| ETHUSD | 38 | 79% | +10.3 | CCXT |
| XAUEUR | 43 | 81% | +9.2 | Dukascopy |
| USDCZK | 25 | 84% | +8.8 | Dukascopy |
| MANAUSD | 32 | 69% | +8.7 | CCXT |

---

## 8. Restrictions

**⛔ Opus 4.6** : manager.py, signal_gen.py, guards.py, indicators.py, décisions stratégiques.
**✅ Intermédiaire** : replay, analyze_replay_v2.py, diagnostics, ajout instruments.

---

## 9. Session 2026-02-27 — Live Engine Stability

### Bugs corrigés

1. **`price_feed.py` — Reconnexion en boucle pour symboles illiquides** : ALGUSD, NEOUSD, XAGUSD déclenchaient une reconnexion globale toutes les 2 min. Nouvelle logique :
   - Symboles majeurs (G10+XAU+BTC+ETH) : seuil stale 5 min
   - Symboles mineurs : seuil stale 30 min (tolérance)
   - Détection weekend : forex/métaux stale tolérés ven 22h→dim 22h UTC
   - Reconnexion globale uniquement si >50% des symboles stale
   - **FIX 2026-02-28** : le check global >50% ne tenait pas compte du weekend → 52 forex fermés = 63% > 50% → reconnexion en boucle toutes les 2 min (tentative #1→#150). Corrigé : pendant le weekend, le check global ne compte que les crypto (31 symboles). Ajout set `CRYPTO_SYMBOLS` pour classification propre.

2. **`price_feed.py` — ALREADY_SUBSCRIBED** : lors de la reconnexion, le code clearait `_subscribed_symbol_ids` puis tentait de re-souscrire → erreur côté serveur. Fix : si broker déjà connecté et souscriptions actives, ne rafraîchir que les callbacks Python sans requête TCP.

3. **`bar_aggregator.py` — Fermetures de bougies invisibles** : `_on_bar_closed` loggait en DEBUG. Passé en INFO avec résumé groupé toutes les 2 min : "X barres fermées, Y signaux émis".

4. **`settings.yaml` — Stratégie "combined" au lieu de "trend"** : pas de section `strategy`, le code defaultait à combined (inclut mean-reversion perdante). Ajout `strategy.type: trend`.

5. **`settings.yaml` — Risk 0.5% au lieu de 0.40%** : corrigé selon la validation v3.3 (DD 8.2% à 0.40%).

6. **`bar_aggregator.py` — TypeError `live_mode`** : `TrendSignalGenerator.__init__()` n'accepte pas `live_mode=True` (contrairement à `BacktestSignalGenerator`). Retiré — pas nécessaire car le BarAggregator filtre déjà la dernière bougie côté appelant.

7. **⚠️ CRITIQUE: `factory.py` + `engine.py` — instruments_mapping vide** : `create_all_brokers()` cherchait les instruments dans `settings["instruments"]` qui est `{}` (les instruments sont dans un fichier séparé `instruments.yaml`). Résultat : `map_symbol()` retournait toujours `None` → "non disponible" pour TOUS les symboles → 0 ordre placé sur 19 signaux. Fix : le moteur passe maintenant `self.instruments` au factory via un 3e paramètre. Log ajouté : `✅ broker_id connecté (N instruments mappés)`.

8. **`tradelocker.py` — stop_price pour ordres stop** : la lib TradeLocker exige `stop_price` pour les ordres `type_='stop'`, pas `price`. L'erreur `Order of type_ = 'stop' specified with a price, instead of stop_price` apparaissait pour BCHUSD et BNBUSD sur GFT. Fix : `stop_price` pour stop, `price` pour limit.

9. **`ctrader.py` + `base.py` — amend_position_sltp + close_position** : ajout des méthodes `amend_position_sltp()` (ProtoOAAmendPositionSLTPReq) et `close_position()` (ProtoOAClosePositionReq) au broker cTrader, avec wrappers synchrones. Le handler `_process_order_response` est refactorisé pour supporter les 4 types de requêtes (order_place, position_amend, position_close, order_cancel) via un système de priorité.

10. **Scripts de test** :
    - `scripts/test_order_flow.py` : test cycle complet (MARKET → amend SL → close) avec confirmation utilisateur et volume minimum (0.01 lots)
    - `scripts/test_connectivity.py` : vérifie connexions, mappings instruments.yaml vs réalité broker, infos compte, historique (non destructif)

### Observations de la session Perplexity (review)
- Confirmation que le TP est un mur absolu (broker ferme dès qu'il est touché)
- Le trailing ne peut PAS capturer au-delà du TP
- Les paliers sont : BE (0.3R→0.20R), trailing (≥1.5R:0.7R, ≥2.0R:1.0R, ≥3.0R:1.5R)
- News filter identifié comme amélioration future (P2)

### Bugs corrigés dans `broker/ctrader.py`
1. **`get_history()` — fromTimestamp/toTimestamp manquants** : champs proto obligatoires. Ajout `_TIMEFRAME_SECONDS` + calcul fenêtre temporelle.
2. **`_decode_trendbar()` — champs proto incorrects** : corrigé vers `tb.low` + deltas.
3. **`_process_spot_event()` — diviseur hardcodé** : remplacé par diviseur spécifique au symbole. Gestion SpotEvents incrémentaux.
4. **Thread-safety globale** : `_resolve_future()`/`_reject_future()` helpers pour tous les handlers. `_asyncio_loop` stocké dès `connect()`.
5. **`_process_spot_event()` — `asyncio.get_event_loop()` dans thread Twisted** : crash `RuntimeError: no current event loop`. Remplacé par `self._asyncio_loop`.
6. **⚠️ CRITIQUE: `_symbol_id_for_name()` — condition toujours vraie** : `sinfo.broker_symbol == str(sid)` retournait TOUJOURS le premier symbole du dict. **Toutes les souscriptions spots ET tous les get_history utilisaient le même symbolId** (EURUSD). Corrigé avec recherche par nom exact + normalisation (EUR/USD → EURUSD).

### Architecture
7. **PriceFeedManager réutilise le broker existant** : plus de 2e connexion TCP → plus de `ALREADY_LOGGED_IN`.
8. **`_send_no_response()`** : fire-and-forget avec errback pour éviter les Deferred 5s timeout.
9. **Chargement historique séquentiel** : cTrader mono-connexion TCP ne supporte pas les requêtes parallèles. Séquentiel avec 0.15s delay.
10. **Lazy imports dans `live/__init__.py`** : supprime le RuntimeWarning au lancement.

### Améliorations UX
11. **Warnings "aucun tick" condensés** : un résumé au lieu de 83 lignes individuelles toutes les 30s.
