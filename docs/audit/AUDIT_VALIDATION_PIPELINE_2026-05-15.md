# Audit validation backtest / parquet / live

Date: 2026-05-15
Scope: audit lecture seule du pipeline de validation. Aucun fichier applicatif modifie.

## Verdict court

La demarche visee est bonne: un generateur de signaux unique par strategie, un backtest avec fill sur la bougie suivante, puis des scripts qui comparent le live aux signaux et aux trades theoriques.

Mais l'application actuelle n'est pas encore assez verrouillee pour conclure sereinement que "le live colle a la validation". Les risques principaux ne sont pas dans les strategies elles-memes, mais dans les chemins d'audit et de replay:

1. Les scripts d'audit ne lisent pas tous le meme champ de timeframe que le live.
2. Plusieurs symboles crypto suivis en live n'ont pas de mapping parquet local et basculent vers Yahoo.
3. Le replay parquet historique utilise un ancien pipeline Orchestrator/DryRunAdapter, pas le dispatcher live moderne.
4. La suite pytest globale collecte des artefacts obsoletes et echoue.

## Commandes executees

```bash
.venv/bin/pytest -q tests
# 13 passed

.venv/bin/pytest -q
# echec collecte: test_backtest.py obsolete + tmp/cointegration/*

.venv/bin/python scripts/check_execution_invariants.py --since 2026-05-07T23:45 --per-broker
# Verdict global OK, mais seulement 9 exits

.venv/bin/python scripts/replay_live_vs_theory.py --since 2026-05-07T23:45 --no-persist
# n=7, mean DeltaR=-0.178R, divergence concentree sur 1 trade glissade XAUUSD

.venv/bin/python scripts/replay_signals_vs_live.py --since 2026-05-07T23:45 --min-missing 1
# Beaucoup de source_missing, mais resultats contamines par timeframes/fallbacks a corriger avant usage decisionnel
```

## Points solides

- Les strategies principales exposent bien `prepare(df)` et `generate_signals(df, instrument)` et sont reutilisees par backtest et live.
- Le backtest principal applique bien la convention anti-lookahead: signal sur barre `i`, fill sur open `i+1`.
- Le backtest principal peut utiliser des sous-barres M1 pour resoudre une partie de l'ambiguite intrabarre.
- Les invariants d'execution recents passent sur la fenetre Phase 4, meme si l'echantillon est trop petit pour valider.
- `replay_live_vs_theory.py` est conceptuellement utile: il compare trade live par trade live a une simulation parquet.

## Findings critiques

### 1. Timeframe live et timeframe audit divergent pour Extension crypto

Le live lit `timeframe` dans `config/instruments.yaml`:

- `arabesque/execution/live.py` utilise `inst_data.get("timeframe", "H1")`.
- Les cryptos suivies declarent `timeframe: "H4"`.

Mais certains scripts d'audit lisent `tf`:

- `scripts/replay_signals_vs_live.py` utilise `meta.get("tf") or "h1"`.
- `scripts/replay_live_vs_theory.py` utilise `meta.get("tf") or "H1"`.

Consequence: pour Extension crypto, ces scripts peuvent rejouer du H1 alors que le live tourne en H4. Exemple observe:

```text
targets 41
Counter({('extension', 'H1'): 31, ('cabriole', 'H4'): 6, ('glissade', 'H1'): 2, ('fouette', 'M1'): 2})
[('extension', 'H1', 'ADAUSD'), ('extension', 'H1', 'BTCUSD'), ('extension', 'H1', 'ETHUSD')]
```

Impact: les "signaux manquants" et les deltas live/theorie peuvent etre faux pour Extension crypto.

### 2. Plusieurs aliases crypto suivis n'ont pas de mapping parquet

Sur 31 instruments `follow: true`, 9 n'ont pas de parquet resolu via `arabesque.data.store`:

```text
AAVUSD, ALGUSD, AVAUSD, GALUSD, MANUSD, NERUSD, SANUSD, VECUSD, XTZUSD
```

Exemples:

- La config live utilise `AAVUSD`, mais `store.py` connait `AAVEUSD`.
- La config live utilise `ALGUSD`, mais `store.py` connait `ALGOUSD`.
- La config live utilise `AVAUSD`, mais `store.py` connait `AVAXUSD`.
- La config live utilise `MANUSD`, mais `store.py` connait `MANAUSD`.
- La config live utilise `NERUSD`, mais `store.py` connait `NEARUSD`.
- La config live utilise `SANUSD`, mais `store.py` connait `SANDUSD`.
- La config live utilise `VECUSD`, mais `store.py` connait `VETUSD`.
- `GALUSD` et `XTZUSD` ont des fichiers locaux, mais pas de mapping `_CCXT_MAP`.

Consequence: les backtests/audits tombent en fallback Yahoo. En environnement sans reseau, ca echoue; avec reseau, ca peut utiliser une source differente de la source de validation locale.

### 3. Le CLI backtest ne reflete pas la config live pour Extension crypto

`python -m arabesque run --strategy extension --mode backtest --universe crypto` met Extension en `1h` par defaut dans `arabesque/__main__.py`.

Le live, lui, met les cryptos suivies en H4 via `config/instruments.yaml`.

Consequence: une commande de backtest "naturelle" peut valider un autre systeme que celui deploye. Il faut passer `--interval 4h` manuellement, ou rendre le CLI config-aware.

### 4. Le replay parquet offline n'est pas le live moderne

`arabesque.execution.dryrun.ParquetClock` rejoue vers `execution.orchestrator.Orchestrator`, qui utilise l'ancien flux `handle_signal(data)` / `BrokerAdapter`.

Le live actuel passe par:

```text
BarAggregator -> LiveEngine.receive_signal -> OrderDispatcher -> BaseBroker
```

Consequence: le replay parquet est utile pour detecter des biais de signaux et de position manager, mais il ne valide pas exactement les guards, pending orders, weekend guard, exclusions strategy/broker, risk multipliers, slippage-at-trigger et logique multi-broker du live moderne.

### 5. Fouette est fragile par design de BarAggregator

Le BarAggregator live garde un cache fixe de 300 barres. En M1, cela couvre 5h. Fouette est session-based, avec des sessions plus longues que 5h selon le preset.

En plus, le live ne conserve que les signaux dont `i == last_idx`. C'est correct pour une strategie "bar close", mais fragile pour une strategie qui peut confirmer une structure de session apres plusieurs barres.

Ce point est deja documente dans `HANDOFF.md`; l'audit confirme que c'est une zone a traiter avant de considerer Fouette validee live.

## Lecture des controles recents

### Invariants execution

Fenetre `2026-05-07T23:45 -> 2026-05-15`:

- FTMO: 7 exits, verdict OK, 2 reconciled, 1 mfe=0 loser.
- GFT: 2 exits, verdict OK.

Lecture: bon signe, mais pas suffisant. Le seuil Phase 4 parle de 50 trades; on en est loin.

### Replay live vs theorie

Fenetre `2026-05-07T23:45 -> 2026-05-15`:

- Global: n=7, `mean DeltaR=-0.178R`.
- Cabriole: propre (`-0.014R/trade`).
- Extension: propre (`+0.005R/trade`).
- Glissade: 1 trade XAUUSD explique presque tout (`R_live=-1.00`, `R_theo=+0.20`, BE live et theorie armes).

Lecture: ne pas conclure globalement. Il faut inspecter ce trade Glissade precisement.

### Replay signals vs live

Le script detecte beaucoup de source-missing, mais il ne doit pas encore etre utilise tel quel pour conclure:

- Extension crypto est rejouee en H1 au lieu de H4.
- Les aliases crypto sans mapping declenchent des fallbacks Yahoo.
- Il manque une distinction stricte entre "pas de donnees locales fiables" et "vrai signal manque".

## Recommandations prioritaires

1. Unifier la cle de timeframe: lire `timeframe` partout, garder `tf` seulement comme alias legacy.
2. Completer `_CCXT_MAP` avec les aliases live: `AAVUSD`, `ALGUSD`, `AVAUSD`, `GALUSD`, `MANUSD`, `NERUSD`, `SANUSD`, `VECUSD`, `XTZUSD`.
3. Ajouter un mode strict data: les scripts d'audit doivent echouer si le parquet local manque, sauf flag explicite `--allow-yahoo`.
4. Rendre le CLI backtest Extension config-aware: si instrument a `timeframe: H4`, utiliser H4 par defaut.
5. Marquer clairement le replay parquet actuel comme "legacy execution replay", ou le migrer vers le `OrderDispatcher` live.
6. Ajouter une config pytest pour limiter la collecte a `tests/`.
7. Ajouter des tests de coherence:
   - tous les `follow: true` resolvent un parquet au timeframe live;
   - `_build_targets()` retourne H4 pour les cryptos Extension;
   - `resolve_tf()` retourne H4 pour les trades Extension crypto;
   - les commandes documentees backtestent le meme timeframe que le live.

## Regle de travail conseillee avec LLM

Pour ce projet, le LLM ne doit pas "ameliorer" une strategie ou un parametre sans protocole. Une modification acceptable doit indiquer:

- surface modifiee;
- hypothese testee;
- commande de validation;
- effet attendu sur backtest;
- effet attendu sur live;
- critere d'annulation.

Zones a proteger fortement:

- `arabesque/strategies/*/signal.py`
- `arabesque/modules/position_manager.py`
- `arabesque/core/guards.py`
- `arabesque/execution/order_dispatcher.py`
- `arabesque/execution/position_monitor.py`
- `arabesque/execution/live.py`

Les scripts d'audit peuvent evoluer plus librement, mais ils doivent etre testes contre des fixtures simples pour eviter de produire de faux diagnostics.
