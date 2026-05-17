# Backtest combine + live parquet — 2026-05-15

## Perimetre

Objectif: valider offline la chaine suivante, sans broker live:

```text
backtest combine portefeuille
→ replay live parquet des signaux
→ comparaison des ecarts
```

Strategies incluses:

- Extension
- Glissade
- Fouette en option

Strategies exclues du scenario cible:

- Cabriole

Artefacts generes dans `../arabesque_bt_audit/`:

- `07_portfolio_filter_replay.md`
- `09_backtest_vs_live_parquet.md`
- `portfolio_filter_summary.csv`
- `portfolio_filter_accepted.csv`
- `portfolio_filter_rejected.csv`
- `portfolio_filter_daily.csv`
- `backtest_vs_live_parquet_summary.csv`
- `live_parquet_signal_sessions.csv`

## 1. Backtest combine portefeuille

Le replay portefeuille prend les trades candidats issus des backtests actifs, retire Cabriole, puis applique les contraintes prop firm sur une seule timeline:

- `max_positions = 5`
- `max_open_risk_pct = 2.0`
- duplicate instrument interdit
- daily DD 3%
- pause total DD vers -7%
- risque nominal 0.45% par trade
- rodage optionnel Glissade/Fouette x0.25

Resultats principaux:

| Scenario | Rodage | Accepted | Rejected | Total | Pire jour | Max DD | Breach -3% | Max risk ouvert |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Extension + Glissade | oui | 911 | 158 | +21.03% | -1.39% | 4.82% | 0 | 1.91% |
| Extension + Glissade + Fouette | oui | 1074 | 162 | +20.67% | -1.39% | 5.17% | 0 | 1.91% |
| Extension seule | oui | 776 | 155 | +19.10% | -1.53% | 5.01% | 0 | 1.80% |
| Glissade seule | oui | 138 | 0 | +2.20% | -0.11% | 0.29% | 0 | 0.23% |
| Extension + Glissade | non | 909 | 160 | +26.98% | -1.53% | 4.78% | 0 | 1.80% |
| Extension + Glissade + Fouette | non | 1070 | 166 | +25.63% | -1.71% | 6.15% | 0 | 1.80% |

Lecture:

- Extension + Glissade est le scenario cible.
- Le portefeuille reste dans les limites prop firm dans ce replay.
- Le guard `open_risk_limit` est central: il rejette les clusters dangereux.
- Fouette augmente le nombre de trades mais degrade legerement total et DD.

## 2. Live parquet

Le replay live parquet rejoue les generateurs sur les parquets locaux strict-data, avec les targets/timeframes de config:

- Extension: tous les `follow: true`, timeframe de `instruments.yaml`
- Glissade: `strategy_assignments`
- Fouette: `strategy_assignments`

Les signaux consecutifs sont regroupes en "sessions" pour eviter de compter plusieurs fois une condition qui reste vraie plusieurs bougies.

Comparaison aggregate:

| Strategy | Targets | Live parquet sessions | Backtest trades | BT sans session live | Sessions live sans trade BT |
| --- | ---: | ---: | ---: | ---: | ---: |
| Extension | 31 | 1217 | 931 | 36 | 322 |
| Fouette | 2 | 970 | 167 | 0 | 803 |
| Glissade | 2 | 141 | 138 | 0 | 3 |

## 3. Interpretation des ecarts

### Glissade

Tres bon alignement:

- 141 sessions live parquet
- 138 trades backtest
- 0 trade backtest sans session live
- 3 sessions live sans trade backtest

Conclusion:

- pipeline coherent;
- faible frequence normale;
- bonne candidate a garder.

### Extension

Alignement global acceptable:

- 1217 sessions live parquet
- 931 trades backtest
- 36 trades backtest sans session live
- 322 sessions live sans trade backtest

Les sessions live sans trade backtest sont principalement expliquees par les guards du backtest:

- cooldown;
- duplicate instrument;
- slippage/spread/volume zero surtout sur certains H1 JPY;
- `open_risk_limit` au niveau portefeuille dans le replay combine.

Conclusion:

- Extension ne montre pas de mismatch massif de config/timeframe.
- La difference live-session vs trade est attendue: tous les signaux ne doivent pas devenir des trades.
- Les 36 trades BT sans session live meritent une verification ponctuelle si on veut durcir l'audit, mais ce n'est pas le point dominant.

### Fouette

Ecart majeur:

- 970 sessions live parquet
- 167 trades backtest
- 803 sessions live sans trade backtest

Les raisons de rejet du backtest se concentrent sur:

- `slippage_too_high`
- `spread_too_wide`

Conclusion:

- Fouette genere beaucoup de setups theoriques M1.
- La plupart ne passent pas les contraintes d'execution.
- Ce n'est pas une brique a mettre en production pleine tant que la logique M1 live, le spread et le slippage ne sont pas clarifies.

## 4. Suffisance du backtest vs live parquet

Le backtest combine repond a:

```text
La logique strategie + guards + portefeuille est-elle prop-compatible ?
```

Le live parquet repond a:

```text
La config live produit-elle les memes familles de signaux que le backtest ?
Les timeframes et assignments sont-ils coherents ?
Les ecarts viennent-ils des guards plutot que d'un mismatch de strategie ?
```

Ces deux niveaux sont necessaires mais ne remplacent pas le live broker.

Ce qui reste hors scope offline:

- vrai spread FTMO/GFT;
- latence/fill broker;
- tick manquant;
- PriceFeed stale;
- amend SL/BE reellement pose cote broker;
- pending order rempli plus tard;
- differents prix entre cTrader et TradeLocker.

## 5. Recommandations

1. Production cible: Extension + Glissade.
2. Cabriole: exclue du live.
3. Fouette: paper/rodage symbolique seulement.
4. Garder `max_open_risk_pct = 2.0`.
5. Ne pas convertir les sessions live parquet en trades automatiquement: les guards font partie du modele.
6. Prochaine implementation utile: backtest combine natif dans le code principal, produisant directement:
   - trades acceptes;
   - signaux rejetes avec raison;
   - equity daily;
   - metriques prop firm;
   - comparaison backtest/live parquet.

## 6. Message court pour l'agent codeur

```text
Audit offline fini:
- Backtest portefeuille Cabriole exclue: Extension+Glissade passe prop firm.
- Resultat avec rodage: +21.03%, pire jour -1.39%, max DD 4.82%, 0 breach -3%, max open risk 1.91%.
- Live parquet: Glissade aligne tres bien (141 sessions vs 138 trades).
- Extension aligne globalement; l'ecart vient surtout des guards/cooldown/rejets.
- Fouette genere trop de sessions M1 rejetees par slippage/spread; garder paper/rodage symbolique.
- Ne pas relacher max_open_risk_pct=2.0.
- Implementer ensuite un CombinedBacktestRunner natif avec rapports prop firm et raisons de rejet.
```
