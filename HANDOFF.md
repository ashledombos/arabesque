# ARABESQUE — Handoff v16
## Pour reprendre le développement dans un nouveau chat

> **Repo** : https://github.com/ashledombos/arabesque
> **Dernière mise à jour** : 2026-03-15, session Sonnet 4.6 (Fouetté backtest #1)

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

---

## 2. Session 2026-03-15

### Ce qui a changé

- **`CLAUDE.md` ajouté** : guide pour les futures instances Claude Code.
- **Fouetté — corrections techniques** :
  - Off-by-one `_build` : retournait `(signal_bar_idx+1)` → corrigé en `(signal_bar_idx)`
  - `_tag_or_bars` vectorisé (boucle Python O(n) → numpy, critique pour 828k barres M1)
  - `ExecConfig` M1 dédié dans `__main__.py` : `max_spread/slippage_atr=0.5` au lieu de 0.10-0.15 (calibré H1)
- **`python -m arabesque positions --account <id>`** : nouvelle sous-commande CLI (remplace `scripts/show_positions.py`)
- Nettoyage : `patch_timeframe.sh` supprimé, `scripts/show_positions.py` supprimé

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

### P0 (Fouetté) — Pistes à explorer (décision Opus)

- `rr_tp` à 1.5 ou 2.0× le range
- Sans position manager (TP fixe uniquement)
- `range_minutes=15` au lieu de 30
- Shadow EMA activé (`ema_filter_active=True`) — ~120 signaux sur 480 auraient été filtrés

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
│   └── extension/     ← Trend-following H1 ✅ Validé
│       ├── signal.py  ← Générateur UNIQUE backtest + live
│       ├── params.yaml
│       └── STRATEGY.md
├── execution/         ← Moteurs (backtest, dryrun, live, bar_aggregator…)
├── broker/            ← Adapters (cTrader, TradeLocker, DryRun)
├── data/              ← Store parquet + fetch (ex-barres_au_sol)
└── analysis/          ← Metrics, stats, pipeline de screening
```

**Règle immuable** : `strategies/extension/signal.py` est modifiable
uniquement par **Claude Opus 4.6**.

---

## 5. Prochaines étapes

### P0 : Validation live continue
Le moteur tourne sur `ftmo_swing_test`. Observer la correspondance
backtest ↔ live (WR, nb trades/semaine, exit reasons).

### P1 : Décision shadow filters
Accumuler ~100 trades avec logs Williams %R et RSI div, puis décider
si activer comme filtre bloquant. Voir `docs/DECISIONS.md`.

### P2 : Multi-compte prop firm
`config/prop_firm_profiles.yaml` existe. Quand le compte test est
validé, étendre à GFT (TradeLocker).

### P3 : Tests unitaires
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
