# ARABESQUE — Handoff v21
## Pour reprendre le développement dans un nouveau chat

> **Repo** : https://github.com/ashledombos/arabesque
> **Dernière mise à jour** : 2026-03-16, session Opus 4.6 (Fouetté FVG testé, Glissade implémenté)

---

## ⭐ BOUSSOLE STRATÉGIQUE — Immuable

```
OBJECTIF : gains petits, fréquents, consistants.
           Win Rate élevé (cible ≥ 70%, idéal ≥ 85%).
           Expectancy positive par le volume.
STRATÉGIE : TREND-ONLY, basket optimisé (XAUUSD H1 + crypto H4 + JPY crosses H1).
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

---

## 2. Session 2026-03-16

### Ce qui a changé

- **Fouetté `sl_source="fvg"` testé et abandonné** : WR chute 12-21pts, DD explose à 13-17%
- **Glissade signal generator implémenté** : VWAP pullback + EMA context, premier backtest négatif
- **`positions` CLI corrigé** : fix create_broker() argument mismatch
- **Live multi-TF confirmé opérationnel** : BTCUSD H4 + BNBUSD H4 signaux corrects, BE trigger OK
- **Sub-bar replay M1** implémenté dans le backtest : résout l'ambiguïté intra-barre H1/H4
  pour le BE trigger, trailing, et l'ordre SL/TP. Activé automatiquement quand les données M1
  sont disponibles. Flag `--no-sub-bar` pour désactiver.

### Glissade — Premier backtest (2026-03-16)

| Instrument | Trades | WR | Exp | Total R | Max DD |
|---|---|---|---|---|---|
| XAUUSD | 154 | 44.8% | -0.271R | -41.7R | 16.8% |
| BTCUSD | 1080 | 53.1% | -0.032R | -34.6R | 24.4% |
| BTCUSD (wide ADX) | 868 | 53.6% | -0.024R | -20.7R | 11.5% |

35% des trades ont un MFE < 0.25R (ne dépassent jamais le BE trigger).
Le pullback detection est trop permissif — filtre trop de bruit.
Stratégie en recherche.

---

## Session 2026-03-15

### Ce qui a changé

- **Walk-forward validation implémenté** :
  - `split_walk_forward()` dans `store.py` : fenêtres glissantes IS/OOS
  - `run_walk_forward()` + `run_walk_forward_multi()` dans `backtest.py`
  - CLI : `python -m arabesque walkforward --strategy extension --universe crypto`
  - Agrégation OOS, mesure stabilité (σ WR, σ Exp), dégradation IS→OOS, verdict auto
- **Walk-forward exécuté sur tout l'univers** — voir résultats ci-dessous
- **Placeholders Glissade + Pas de Deux** créés
- **`python -m arabesque positions --account <id>`** : nouvelle sous-commande CLI
- **Fouetté — corrections techniques** (off-by-one, vectorisation, ExecConfig M1)

### Premier backtest Fouetté — XAUUSD (jan 2024 → mars 2026)

```
Mode      : fvg_multiple, range=30m, rr_tp=1.0
Trades    : 308  |  WR : 70.8%  |  Expectancy : -0.024R  ← NÉGATIF
Total R   : -7.4R  |  PF : 0.89  |  Max DD : 6.3%

Exits :
  trailing   175 @ +0.20R  ← sort au plancher BE
  sl          55 @ -1.00R
  tp          37 @ +0.69R  ← TP trop rare (12%)
  time_stop   36 @ -0.38R
```

**Diagnostic** : le TP à 1×range est rarement atteint (74% MFE < 0.5R). Le BE
convertit en +0.20R mais avg_loss -0.76R creuse l'expectancy.

**Statut** : recherche. Ne pas déployer en live.

### Exploration complète — 5 variantes testées (session 2026-03-15)

| Config | Trades | WR | Expectancy | PF |
|---|---|---|---|---|
| Baseline rr_tp=1.0 | 308 | 70.8% | -0.024R | 0.89 |
| rr_tp=2.0 | 308 | 70.8% | -0.053R | 0.76 |
| **TP fixe, sans PM** | **299** | **54.8%** | **+0.011R** | **1.02** |
| range=15min | 338 | 69.5% | -0.056R | 0.80 |
| EMA actif | 285 | 69.8% | -0.030R | 0.87 |
| TP fixe + EMA actif | 279 | 55.2% | +0.002R | 1.00 |

Seul le TP fixe sans PM passe en positif, mais l'expectancy (+0.011R) est trop
fragile (négatif à 1.5× slippage). Stratégie insuffisante sur XAUUSD en l'état.

### Walk-forward Extension — Résultats (2026-03-15)

Le split IS/OOS fixe (70/30) surestimait l'edge. Walk-forward (6m IS → 2m OOS,
fenêtres glissantes) donne une image plus réaliste :

| Univers | Trades OOS | WR | Exp(R) | Total R | Verdict |
|---|---|---|---|---|---|
| Forex majors H1 (7) | 171 | 55% | -0.08 | -17.0 | FAIL |
| Forex crosses H1 (15) | 226 | 57% | -0.07 | -20.1 | FAIL |
| XAUUSD H1 | 67 | 73% | +0.176 | +11.8 | MARGINAL |
| **Crypto 4H (14)** | **158** | **65%** | **+0.18** | **+29.1** | **PASS** |

**Instruments crypto 4H positifs :** SOLUSD +7.1, ETHUSD +5.2, LINKUSD +4.2,
DOGEUSD +3.5, AAVEUSD +3.5, AVAXUSD +2.6, ADAUSD +2.4, LTCUSD +2.3, BNBUSD +2.0.
**Forex positifs :** AUDJPY +7.6, CHFJPY +3.2, GBPJPY +0.4 (JPY crosses seulement).

**Recommandation basket live :** XAUUSD H1 + crypto 4H + JPY crosses H1.
Le forex majeurs (EURUSD, GBPUSD...) ne contribue pas positivement en walk-forward.

### Fouetté sl_source="fvg" — ABANDONNÉ (2026-03-16)

Testé sur 4 instruments (XAUUSD, BTCUSD, SOLUSD, ETHUSD) :

| Instrument | sl_source | WR | Exp | Total R | Max DD |
|---|---|---|---|---|---|
| XAUUSD | range | 70.8% | -0.024R | -7.4R | 6.3% |
| XAUUSD | fvg | 49.7% | -0.284R | -42.2R | 16.9% |
| BTCUSD | range | 72.7% | +0.014R | +7.4R | 4.9% |
| BTCUSD | fvg | 58.5% | +0.024R | +12.9R | 15.4% |
| SOLUSD | range | 75.5% | +0.009R | +6.5R | 5.1% |
| SOLUSD | fvg | 59.3% | -0.028R | -19.7R | 13.4% |
| ETHUSD | range | 70.6% | -0.032R | -20.8R | 10.4% |
| ETHUSD | fvg | 55.2% | -0.107R | -37.3R | 16.1% |

FVG SL trop serré pour le bruit M1 — WR destruction annule le gain R/R.

### P0 (Fouetté) — Pistes restantes

- Autres instruments : US500, NAS100
- Mode `breakout` pur (sans FVG)

---

## 3. Session 2026-03-13 — Restructuration v9

### Ce qui a changé

**Architecture multi-stratégie déployée sur main.** Aucune logique de trading modifiée.

| Avant | Après |
|---|---|
| `arabesque/backtest/signal_gen_trend.py` | `arabesque/strategies/extension/signal.py` |
| `arabesque/live/engine.py` (logique) | `arabesque/execution/live.py` |
| `arabesque/guards.py` (logique) | `arabesque/core/guards.py` |
| `arabesque/models.py` (logique) | `arabesque/core/models.py` |
| `arabesque/indicators.py` (logique) | `arabesque/modules/indicators.py` |
| `arabesque/position/manager.py` (logique) | `arabesque/modules/position_manager.py` |

**Shims de compatibilité** : tous les anciens chemins fonctionnent encore :
```python
from arabesque.live.engine import LiveEngine     # ✅ shim
from arabesque.models import Signal              # ✅ shim
from arabesque.guards import Guards              # ✅ shim
```

**Nouveautés :**
- `config/accounts.yaml` — flag `protected: true` pour les vrais comptes
- `arabesque/strategies/extension/STRATEGY.md` — fiche complète
- `arabesque/strategies/extension/params.yaml` — presets nommés
- `docs/HYGIENE.md` — règles de contribution formalisées
- CLI unifié `python -m arabesque run/screen/fetch/analyze/check`
- 13 scripts one-off supprimés, 8 docs obsolètes supprimés

### Validé sur le serveur

```
✅ arabesque.core.models
✅ arabesque.strategies.extension.signal
✅ arabesque.live.engine (compat shim)
✅ python -m arabesque.live.engine --source parquet --dry-run
```

### Bug résiduel — fix appliqué

**Problème** : `python -m arabesque.live.engine` ne démarrait plus (sortie silencieuse).
**Cause** : le shim `arabesque/live/engine.py` n'avait pas de bloc `if __name__ == "__main__"`.
**Fix** : ajout du forward dans le shim :
```python
if __name__ == "__main__":
    from arabesque.execution.live import main
    main()
```
Commit : `fix: shim engine.py forward __main__`

---

## 3. État courant du moteur live

**Commande de lancement :**
```bash
python -m arabesque.live.engine |& tee live.log
```

**Compte actif** : `ftmo_swing_test` (non-protected dans `config/accounts.yaml`)

**Shadow filters actifs (log only, pas bloquant) :**
- Williams %R (`👻 WR shadow`) — accumulation de données
- RSI divergence — accumulation de données

**Instruments validés (basket FTMO) :**
```
BTCUSD ETHUSD SOLUSD BNBUSD LNKUSD ICPUSD
EURUSD USDJPY GBPUSD NZDCAD XAUUSD
```

---

## 4. Architecture post-restructuration

```
arabesque/
├── core/              ← Kernel immuable (models, guards, audit)
├── modules/           ← Briques réutilisables (indicators, position_manager)
├── strategies/
│   ├── extension/     ← Trend-following H1 ✅ Validé
│   │   ├── signal.py  ← Générateur UNIQUE backtest + live
│   │   ├── params.yaml
│   │   └── STRATEGY.md
│   ├── fouette/       ← ORB M1 🔬 En développement
│   ├── glissade/      ← Scalping VWAP 📋 Placeholder
│   └── pas_de_deux/   ← Pairs trading 📋 Placeholder
├── execution/         ← Moteurs (backtest, dryrun, live, bar_aggregator…)
├── broker/            ← Adapters (cTrader, TradeLocker, DryRun)
├── data/              ← Store parquet + fetch (ex-barres_au_sol)
└── analysis/          ← Metrics, stats, pipeline de screening
```

**Règle immuable** : `strategies/extension/signal.py` est modifiable
uniquement par **Claude Opus 4.6**.

---

## 5. Prochaines étapes

### P0 : Adapter le basket live aux résultats walk-forward
Le walk-forward a montré que forex majeurs H1 ne tient pas. Le basket live
devrait être restreint à : **XAUUSD H1 + crypto 4H + JPY crosses H1**.
Nécessite de configurer le live engine pour supporter du multi-TF (H1+4H)
sur le même compte.

### P1 : Validation live continue
Le moteur tourne sur `ftmo_swing_test`. Observer la correspondance
backtest ↔ live (WR, nb trades/semaine, exit reasons).

### P2 : Décision shadow filters
Accumuler ~100 trades avec logs Williams %R et RSI div, puis décider
si activer comme filtre bloquant. Voir `docs/DECISIONS.md`.

### P3 : Nouvelles stratégies

| Stratégie | Priorité | Statut | Prochain pas |
|---|---|---|---|
| **Extension 4H crypto** | **Critique** | ✅ Walk-forward PASS +29.1R | Configurer multi-TF live (H1+4H) |
| **Fouetté** (ORB M1) | Haute | 🔬 Backtest négatif sur XAUUSD | Tester BTCUSD, US500, NAS100 |
| **Glissade** (scalp VWAP) | Haute | 🔬 Signal implémenté, backtest négatif | Affiner pullback detection (35% MFE<0.25R) |
| **Pas de Deux** (pairs) | Long terme | 📋 Placeholder créé | Définir interface multi-jambes |

### P4 : Multi-compte prop firm
`config/prop_firm_profiles.yaml` existe. Quand le compte test est
validé, étendre à GFT (TradeLocker).

### P5 : Tests unitaires
Placeholder dans `tests/`. À implémenter quand on veut garantir la
non-régression des guards et du signal generator.

---

## 6. Configuration v3.3 (inchangée)

### Entrées (signal generator)
- BB 20, std 2.0, typical_price
- Squeeze : percentile 20 sur 100 barres, mémoire 10 barres
- ADX min 20.0, rising 3 barres
- SL : 1.5 ATR

### Sorties (position manager)
- **BE** : trigger=0.3R, offset=0.20R
- **Trailing** : 3 paliers (≥1.5R:0.7R, ≥2.0R:1.0R, ≥3.0R:1.5R)
- **ROI** : backstop (0:3.0R, 240:0.15R)

### Risk (guards)
- **risk_per_trade** : 0.40%
- **max_daily_dd** : 4.0%
- **max_total_dd** : 9.0% (safety margin 1%)
- **max_open_risk** : 2.0% simultané

---

## 7. Leçons majeures — IMMUTABLES

### L1 : BE est LE levier principal du WR
WR ≈ % des trades atteignant le trigger MFE (0.3R → ~75-78%).

### L2 : Mean Reversion abandonnée définitivement
MR perd sur TOUTES les catégories. TREND-ONLY est définitif.

### L3 : Risk 0.40%/trade (pas 0.50%)
À 0.50% : DD 10.3% → dépasse FTMO 10%. Marge de 1.8% à 0.40%.

### L4 : BE offset 0.20R (pas 0.15)
323/339 trailing exits sortaient à +0.15R exact. Offset trop serré.

### L5 : L'univers complet (forex + crypto) est optimal
Crypto trend (+145R) surperforme forex (+115R) sur 20 mois.

### L6 : Anti-biais strict
Signal bougie `i`, exécution open bougie `i+1`. Si SL ET TP même bougie → SL pris.

---

## 8. Restrictions

**⛔ Opus 4.6 uniquement** :
- `arabesque/strategies/*/signal.py`
- `arabesque/core/*.py`
- `arabesque/modules/position_manager.py`
- Toute décision stratégique (paramètres, univers, règles de sortie)

**✅ Sonnet ou intermédiaire** :
- Infrastructure, scripts, broker adapters, data pipeline
- Diagnostics, analysis, documentation

---

## 9. Bugs live connus et corrigés (session 2026-02-27)

Voir HANDOFF v15 pour le détail complet des 16 bugs corrigés dans
`ctrader.py`, `price_feed.py`, `bar_aggregator.py`, `factory.py`.

Principaux :
- Race condition bougies dupliquées → `_last_closed_ts` dedup guard
- Volume units ×lotSize (pas ×100 hardcodé)
- Symbol ID resolution dans reconcile
- Price divisor 10^5 fixe (indépendant de pip_size)
