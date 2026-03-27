# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Arabesque — Système de trading algorithmique pour prop firms

## Boussole stratégique — IMMUABLE

```
GAINS PETITS, FRÉQUENTS, CONSISTANTS.
PEU DE PERTES. PETITES QUAND ELLES ARRIVENT.
WIN RATE ÉLEVÉ : CIBLE ≥ 70%, IDÉAL ≥ 85%.
COURBE D'ÉQUITÉ RÉGULIÈRE ET PRÉVISIBLE.
```

**Pourquoi ce profil ?** Les prop firms (FTMO et similaires) imposent un daily
drawdown max (~5%) et un drawdown total max (~10%). Elles évaluent la
*consistance* de la courbe d'équité, pas la performance brute. Un WR élevé
avec des gains petits et réguliers passe un challenge. Une courbe en dents de
scie avec quelques trades à +10R ne passe pas, même si le P&L total est bon.

Si un changement de code contredit cette boussole, c'est le changement qui a tort.

## Paramètres de la stratégie Extension (validés sur 20 mois, 1998 trades)

Ces valeurs ont été calibrées par simulation exhaustive et validées en production.
Ne pas les modifier sans rejeu complet (20 mois, 76 instruments) + IC99 > 0.

- **BE trigger 0.3R / offset 0.20R** — ~75% des trades atteignent 0.3R MFE,
  ce qui convertit des losers en petits gains et porte le WR de ~45% à ~75%.
  L'offset 0.20R (et pas 0.15) car à 0.15, 95% des trailing exits sortaient
  au plancher exact (+0.15R) — trop serré pour le bruit OHLC.
- **Risk 0.40%/trade** — À 0.50%, le max DD atteint 10.3%, ce qui breach le
  seuil FTMO de 10%. À 0.40%, max DD = 8.2% avec 1.8% de marge.
- **Tick-level TSL non optionnel** — Backtest H1-only : +10.4R. Avec tick TSL :
  +183R. Le TSL capte le breakeven et les trailing tiers en temps réel au lieu
  d'attendre la clôture H1.
- **Trend-only** — Mean-reversion testée sur 4 replays, 2 périodes, 3 univers :
  perd sur toutes les catégories. Abandonnée définitivement.

## Brokers et prop firms

FTMO est le premier broker ciblé, mais Arabesque supporte plusieurs brokers :
- **cTrader** (FTMO) — connecteur principal, en production live
- **TradeLocker** (GFT et autres) — connecteur secondaire, en développement
- `config/accounts.yaml` — un fichier par compte, flag `protected: true` pour les comptes réels
- `arabesque/broker/factory.py` — instancie le bon connecteur selon le type déclaré

Les contraintes prop firm (daily DD, total DD, max positions) sont dans
`arabesque/core/guards.py` et s'adaptent au profil de chaque compte.

## Architecture (v9)

```
arabesque/core/          ← IMMUABLE (models, guards, audit) — Opus uniquement
arabesque/modules/       ← indicators, position_manager
arabesque/strategies/
  └── extension/         ← signal.py UNIQUE backtest+live (Opus uniquement pour modifier)
  └── fouette/           ← ORB M1, WF PASS 4/4 (London XAUUSD, NY US100/BTCUSD)
  └── glissade/          ← RSI divergence H1, WF PASS 3/3 (XAUUSD, BTCUSD)
  └── cabriole/          ← Donchian breakout 4H, WF PASS 6/6 (overlap Extension)
  └── pas_de_deux/       ← Pairs trading cointégration (placeholder, long terme)
  └── renverse/          ← Liquidity sweep + FVG retrace H1 (testé, edge insuffisant)
  └── reverence/         ← NR7 contraction → expansion H4 (DOGEUSD WF PASS, overlap à vérifier)
arabesque/execution/     ← live.py, backtest.py, dryrun.py, bar_aggregator.py
arabesque/broker/        ← cTrader, TradeLocker
arabesque/data/          ← store.py (parquet-first loader), fetch.py, backends.py
arabesque/analysis/      ← metrics.py, stats.py, pipeline.py
config/                  ← settings.yaml, instruments.yaml, accounts.yaml
barres_au_sol/           ← données Parquet (gitignored, géré par data/fetch.py)
```

## Droits de modification

| Zone | Modèle | Raison |
|---|---|---|
| `arabesque/core/*.py`, `arabesque/modules/position_manager.py` | **Opus uniquement** | Noyau immuable — une régression ici casse le live |
| `arabesque/strategies/*/signal.py` (stratégie validée en live) | **Opus uniquement** | Toute modification change l'edge validé sur 20 mois |
| Scripts, `arabesque/analysis/`, `arabesque/data/`, `__main__.py` | Sonnet suffit | Pas d'impact sur l'edge de trading |

## Commandes clés

```bash
# Backtest Extension (H1)
python -m arabesque run --strategy extension --mode backtest XAUUSD BTCUSD

# Backtest avec univers prédéfini (config/universes.yaml)
python -m arabesque run --strategy extension --mode backtest --universe crypto
python -m arabesque run --strategy extension --mode backtest --universe quick

# Walk-forward validation (fenêtres glissantes IS→OOS)
python -m arabesque walkforward --strategy extension --universe crypto
python -m arabesque walkforward --strategy extension --interval 4h --universe crypto
python -m arabesque walkforward --strategy extension XAUUSD GBPJPY AUDJPY

# Backtest Fouetté (M1 automatique)
python -m arabesque run --strategy fouette --mode backtest XAUUSD

# Backtest Glissade (RSI divergence H1)
python -m arabesque run --strategy glissade --mode backtest XAUUSD BTCUSD

# Backtest Cabriole (Donchian breakout 4H)
python -m arabesque run --strategy cabriole --mode backtest --interval 4h DOGEUSD LINKUSD

# Live
python -m arabesque.live.engine

# Fetch données
python -m arabesque.data.fetch --start 2024-01-01 --end 2026-03-14 --derive 1h 5m

# Replay parquet (dry-run offline)
python -m arabesque.live.engine --source parquet --start 2025-10-01 --end 2026-01-01

# Positions et ordres en attente
python -m arabesque positions --account ftmo_swing_test
```

## Convention signal.py (interface unique backtest+live)

```python
class <Nom>SignalGenerator:
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        # df.copy() en premier. Indicateurs via arabesque.modules.indicators.
        # Colonnes OHLCV CAPITALISÉES en sortie (Open, High, Low, Close, Volume).
    def generate_signals(self, df, instrument) -> list[tuple[int, Signal]]:
        # Signal bougie i → fill open bougie i+1 (anti-lookahead strict)
```

## Nommage des stratégies

Noms de disciplines artistiques gracieuses (danse classique, GR, GAF...).
Terme français, relation imagée avec la logique de trading.
- Extension = trend-following H1/4H (BB squeeze → breakout)
- Fouetté = Opening Range Breakout M1 (NY open, rotation rapide)
- Glissade = RSI divergence H1 (retournement dans le trend, mouvement glissé)
- Cabriole = Donchian breakout 4H (saut vif au-delà du canal)
- Pas de Deux = pairs trading cointégration (danse à deux partenaires en miroir)
- Renversé = liquidity sweep + structure shift + FVG retrace H1 (bascule puis retournement brusque)
- Révérence = range contraction NR4/NR7 → expansion breakout (inclinaison puis redressement)

## Données

- Parquets dans `barres_au_sol/` à la racine du repo
- Dukascopy : forex + métaux (price_scale 1e5 forex, 1e3 métaux)
- CCXT/Binance : crypto
- Structure : `{provider}/min1/{KEY}.parquet` et `{provider}/derived/{KEY}_{tf}.parquet`
- `store.py` charge automatiquement le bon timeframe

## Workflow de validation (obligatoire avant live)

```
Backtest IS (60%) → Exp > 0, WR > 50%
    ↓
Backtest OOS (40%) → cohérent avec IS
    ↓
Wilson CI99 > 0 → significatif
    ↓
Dry-run parquet → 3 mois minimum
    ↓
Shadow filter live → 2-4 semaines
    ↓
Live réel
```

## Shadow filter (garde fantôme)

Log `👻 NOM shadow` dans order_dispatcher.py — signal passe quand même.
Accumuler ≥ 100 trades avant décision. Activer si WR↑ ET Exp↑.

## Règles de code

- Scripts temporaires dans `tmp/` (gitignored), pas dans `scripts/`
- Indicateurs dans `arabesque/modules/indicators.py`, jamais réimplémentés
- Un seul signal.py par stratégie (backtest = live, zéro divergence)
- Shims de compatibilité si un module est déplacé

## Fin de session

Toujours mettre à jour (si applicable) :
1. `HANDOFF.md` — état courant, bugs ouverts, prochaines étapes **(obligatoire)**
2. `docs/DECISIONS.md` — toute nouvelle décision technique **(si décision prise)**
3. `docs/STATUS.md` — si la config live, les balances ou les notifications ont changé
4. `docs/EXPERIMENT_LOG.md` — si des paramètres ou stratégies ont été testés
5. `arabesque/strategies/*/STRATEGY.md` — si les résultats ou le statut d'une stratégie changent
6. `git push`

## Documents de référence

- `docs/STATUS.md` — **état live courant** (compte actif, stratégies, monitoring, que faire quand le compte expire)
- `HANDOFF.md` — pour reprendre le dev (résultats de référence, config active, prochaines étapes)
- `docs/DECISIONS.md` — historique des décisions et pourquoi
- `docs/HYGIENE.md` — règles de code
