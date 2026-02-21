# ARABESQUE — Handoff v6
## Pour reprendre le développement dans un nouveau chat

> **Repo** : https://github.com/ashledombos/arabesque  
> **Branche principale** : `main`  
> **Dernière mise à jour** : 2026-02-21 (session après-midi)
>
> 📖 **Lire aussi** :
> - `docs/decisions_log.md` — pourquoi chaque décision a été prise (lire §0 en premier)
> - `docs/SCRIPTS.md` — carte de tous les scripts (quoi utiliser quand)
> - `docs/STABLE_vs_FRAGILE.md` — ce qui est solide vs ce qui peut casser
> - `docs/BB_RPB_TSL_COMPARISON.md` — BB_RPB_TSL comme modèle cible et état des écarts

---

## ⭐ BOUSSOLE STRATÉGIQUE — À lire avant tout le reste

> **Cette section est immuable. Elle prime sur toutes les autres.**  
> Si un changement de code contredit ce qui est écrit ici, c'est le changement qui a tort, pas ce paragraphe.

### Le profil de gains cible

```
OBJECTIF : gains petits, fréquents, consistants.
           Peu de pertes, et petites quand elles arrivent.
           Win Rate élevé (cible : ≥ 70%, idéal ≥ 85%).
           Expectancy positive par le volume, pas par des grands mouvements rares.
```

**Ce que ça signifie concrètement :**

| Dimension | ✅ Cible | ❌ À éviter |
|---|---|---|
| Win Rate | ≥ 70%, idéalement ≥ 85% | WR ~50% avec "compensation" par des gros gains |
| Variance par trade | Faible — chaque trade ressemble aux autres | Quelques trades à +10R qui "sauvent" le bilan |
| Résilience aux séries | 10 pertes consécutives : impact contenu | 5 pertes = DD menaçant la limite prop firm |
| Lisibilité prop firm | Courbe d'équité régulière, montante | Courbe en dents de scie avec pics isolés |

### Pourquoi ce profil — la référence BB_RPB_TSL

BB_RPB_TSL tourne en **live depuis ~527 jours** : CAGR ~48%, **Win Rate 90.8%**. C'est la preuve que ce profil est réalisable sur les altcoins crypto H1.

Arabesque est l'adaptation prop firm de cette stratégie. L'adaptation porte sur les **guards** (DD limits, sizing adaptatif) et les **instruments** (FTMO vs Binance spot). **Pas sur le profil de gains.**

### Signal d'alarme

Si tu lis dans ce document (ou dans du code) une phrase du type :
- "WR ~52% compensé par un avg win de 2.3R"
- "l'edge vient des grands mouvements capturés via trailing"
- "sensibilité aux outliers acceptée car l'expectancy est positive"

→ **C'est une dérive. Corriger immédiatement.**

Un WR 52% avec trailing pur implique une variance **3.8× plus élevée** par trade qu'un WR 90% avec petits gains. Sur 100 trades, le DD possible est ~16R vs ~4R. C'est incompatible avec les limites prop firm (4% daily, 9% total) sauf à réduire le risk par trade à un niveau qui rend les gains insignifiants.

---

## 1. Contexte — D'où vient l'edge

**BB_RPB_TSL** (stratégie Freqtrade, live ~527 jours) :
- CAGR ~48%, Win Rate 90.8%, sur altcoins crypto USDT
- Mean-reversion Bollinger Bands H1, rebond depuis la bande inférieure
- Asymétrie de slippage : achète la baisse (slippage favorable)
- Petits gains fréquents, SL bien défini, courbe régulière

**Arabesque** = adaptation prop firm de BB_RPB_TSL :
- Instruments FTMO (XXXUSD) au lieu de paires Binance spot
- Guards FTMO/GFT : daily DD 4%, total DD 9%, max positions 5
- Sizing adaptatif selon le DD courant
- **L'objectif est de conserver le profil WR élevé de BB_RPB_TSL**, pas de le transformer en stratégie "faible WR / grands moves"

### État de la divergence (mesuré en replay)

Le module trailing SL 5 paliers a fait chuter le WR de ~90% à ~52%. C'est la dérive principale à corriger. Voir `docs/BB_RPB_TSL_COMPARISON.md` pour le diagnostic complet.

---

## 2. État du code (2026-02-21)

### Structure principale

```
arabesque/
├── models.py              # Signal, Position, Decision (cœur immuable)
├── guards.py              # Guards prop firm (DD, risque ouvert)
├── audit.py               # Logger JSONL
├── config.py              # Chargement settings.yaml + prop_firms.yaml
├── indicators.py          # ✨ ADX/RSI/ATR/BB/CMF/WR unifiés
├── backtest/
│   ├── data.py            # load_ohlc() — Parquet → Yahoo fallback
│   ├── signal_gen.py      # MeanReversionSignalGenerator
│   ├── signal_gen_trend.py
│   ├── signal_gen_combined.py  # ← UTILISER CELUI-CI en production
│   ├── pipeline.py        # Pipeline 3 stages
│   ├── runner.py          # Backtest runner
│   ├── metrics.py
│   ├── metrics_by_label.py
│   └── stats.py           # Wilson CI, bootstrap, Monte Carlo DD
├── live/
│   ├── engine.py          # ⭐ Point d'entrée CLI principal
│   ├── parquet_clock.py   # Replay Parquet bougie-par-bougie
│   ├── bar_aggregator.py  # Ticks → barres H1 → signaux (live cTrader)
│   └── price_feed.py      # Connexion cTrader
├── broker/
│   ├── adapters.py        # DryRunAdapter (equity tracking réel ✨)
│   ├── factory.py
│   ├── ctrader.py
│   └── tradelocker.py
├── position/
│   └── manager.py         # Trailing 5 paliers, breakeven, deadfish
└── webhook/
    └── orchestrator.py    # Guards + sizing + dispatch

scripts/
├── run_pipeline.py        # ⭐ Pipeline de sélection instruments
├── backtest.py            # Backtest IS+OOS sur un instrument
├── run_stats.py           # Stats avancées (Wilson, bootstrap, MC DD)
├── analyze_replay.py      # ✨ Analyse JSONL replay dry-run
├── run_label_analysis.py  # Analyse par sous-type de signal
├── run_json_export.py     # Export backtest → JSONL
├── analyze.py             # Analyse logs paper/live
├── debug_pipeline.py      # Debug contrat CombinedSignalGenerator
├── update_and_compare.py  # Relance + compare avec run précédent
└── research/              # Explorations non validées — jamais en prod

docs/
├── START_HERE.md
├── SCRIPTS.md             # ✨ Carte des scripts
├── decisions_log.md       # Pourquoi chaque décision (§0 = boussole)
├── TECH_DEBT.md
├── STABLE_vs_FRAGILE.md   # ✨ Ce qui peut casser
├── BB_RPB_TSL_COMPARISON.md  # ✨ Écarts vs modèle cible + plan de retour
├── ARCHITECTURE.md
├── ROADMAP.md
└── instrument_selection_philosophy.md
```

### Pièges sur `Signal`

- `close=` et `open_=` dans `__init__` (PAS `tv_close=` ni `tv_open=`)
- `sig.tp_indicative` (PAS `sig.tp`)
- `sig.side` = enum `Side.LONG`/`Side.SHORT`

---

## 3. Résultats — Replay P2b (2026-02-21, données Oct 2025 → Jan 2026)

### Résultats bruts (INVALIDES — spike de données non filtré)

Equity finale +377% à cause d'un trade UNIUSD à R=663.5 (barre corrompue H≈57$, prix réel ≈6.5$).

### Résultats nets (sans outliers |R| > 20)

| Métrique | Valeur | Interprétation |
|---|---|---|
| Win Rate | 52.0% | ❌ Cible : ≥ 70% |
| Expectancy | +0.035R | ❌ IC95 = [-0.056, +0.124] — non significatif |
| Consistance 50-trade windows | 53% | ❌ Seuil prop firm : ≥ 65% |
| Tient sans top-3 trades | Non | ❌ Exp → -0.008R |
| Score prop firm | 0/4 | ❌ Pas prêt |

**Lecture** : le profil actuel ne ressemble pas du tout à BB_RPB_TSL. L'edge mesuré est quasi nul sans les outliers, et le WR 52% génère trop de variance pour les limites prop firm.

### Spike UNIUSD persistant

Le filtre `_clean_ohlc` a été amélioré (filtre intrabar ajouté) mais **à re-valider** :

```bash
# Diagnostic parquet source
import pandas as pd
df = pd.read_parquet('~/dev/barres_au_sol/data/ccxt/derived/UNIUSD_BINANCE_1h.parquet')
print(df[df['high'] / df['close'] > 5][['open', 'high', 'low', 'close']])
```

---

## 4. Prochaines étapes (dans l'ordre)

### P2c — Diagnostiquer les spikes dans les parquets sources *(immédiat)*

```bash
python3 -c "
import pandas as pd, os
root = os.path.expanduser('~/dev/barres_au_sol/data/ccxt/derived/')
for f in sorted(os.listdir(root)):
    if not f.endswith('.parquet'): continue
    df = pd.read_parquet(root + f)
    bad = df[(df['high']/df['close'] > 5) | (df['close']/df['low'] > 5)]
    if len(bad): print(f, len(bad), 'barres suspectes')
        print(bad[['open','high','low','close']].to_string())
"
```

### P2d — Analyser la divergence WR *(priorité stratégique)*

Avant de relancer le replay, comprendre pourquoi le WR est à 52% au lieu de ~85% :

```bash
# Comparer MR pure vs Combined sur la même période
python -m arabesque.live.engine \
  --source parquet --start 2025-10-01 --end 2026-01-01 \
  --strategy mean_reversion --balance 100000 \
  --data-root ~/dev/barres_au_sol/data
python scripts/analyze_replay.py dry_run_*.jsonl
```

Questions à répondre :
- Est-ce que `mean_reversion` seule a un WR plus élevé que `combined` ?
- Est-ce que le module trend filtre des trades gagnants (faux négatifs) ?
- Quels sont les paramètres BB dans `signal_gen.py` vs BB_RPB_TSL ?

### P2e — `run_stats.py` sur les 17 instruments (2 ans)

```bash
for inst in AAVUSD ALGUSD BCHUSD DASHUSD GRTUSD ICPUSD IMXUSD LNKUSD \
            NEOUSD NERUSD SOLUSD UNIUSD VECUSD XAUUSD XLMUSD XRPUSD XTZUSD; do
  python scripts/run_stats.py $inst --period 730d
done
# Garder uniquement : IC95 low > 0R ET WR observé ≥ 60%
```

### P2f — Revisiter la configuration du trailing

Le trailing 5 paliers actuel transforme des trades gagnants (BB_RPB_TSL) en trades intermédiaires (ni TP rapide ni hold long). Tester :
- TP fixe à 1.0R ou 1.5R (proche du profil BB_RPB_TSL)
- Trailing plus agressif au premier palier (BE à +0.3R au lieu de +0.5R)
- Comparaison WR et avg_win résultants

### P3 — Connexion compte test FTMO

Après validation P2c-P2f et score `analyze_replay.py` ≥ 3/4 avec WR ≥ 70%.

---

## 5. Comptes FTMO

| Compte | Solde | Statut |
|---|---|---|
| Live test 15j | 100 000 USD | ✅ OK pour tests ordres |
| Challenge 100k | ~94 989 USD | ⚠️ ~5% DD consommé — NE PAS connecter avant validation complète |

---

## 6. Règles non négociables

1. **Profil WR élevé en priorité** — voir §Boussole ci-dessus
2. Anti-lookahead : signal bougie `i`, exécution open `i+1`
3. Guards toujours actifs (dry-run inclus)
4. Même `CombinedSignalGenerator` backtest / replay / live
5. Jamais `git push --force` sur `main`
6. Ne connecter le challenge qu'après WR ≥ 70% mesuré sur replay propre
7. Tout changement de paramètre stratégique : mesurer l'impact sur le WR en premier
