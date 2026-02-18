# Arabesque — Plan

## Objectif principal
Disposer d’un workflow complet et fiable :
**signal → guards → sizing → position → audit → rapport**
fonctionnel en dry-run (parquet replay) et en live (cTrader).

## Critères « workflow complet » (checklist)
- [ ] Backtest multi-instrument tourne sans erreur et produit un rapport
- [ ] ParquetClock dry-run tourne sans erreur sur 3 mois de données
- [ ] Guards actifs (DD, open_risk, max_positions) et tracés dans les logs
- [ ] Rapport terminal lisible en fin de session (audit summary)
- [ ] Rapport JSONL machine-readable écrit à chaque run backtest
- [ ] Plan & journal à jour dans le dépôt

## Non-objectifs (pour l’instant)
- Optimisation des paramètres de stratégie (WFO, grid search)
- Interface graphique ou dashboard
- Multi-broker live simultané
- Monte Carlo / Wilson IC (noté en dette technique)

## Ordre de priorité
1. Workflow complet fonctionnel (checklist ci-dessus)
2. Qualité des guards (open_risk, DD, cooldown) — en cours
3. Rapport lisible humain + JSONL machine — en cours
4. Backtest pipeline : vérifier pariteté live/backtest
5. Connexion cTrader live (quand dry-run est validé)

## Rappels architecture
- `arabesque/guards.py` : règles prop firm (PropConfig + AccountState)
- `arabesque/webhook/orchestrator.py` : pipeline live
- `arabesque/backtest/runner.py` : pipeline backtest (doit être identique)
- `arabesque/live/runner.py` : point d’entrée parquet/ctrader
- `arabesque/audit.py` : JSONL décisions + terminal summary
- `logs/backtest_runs.jsonl` : 1 ligne par run backtest (machine)
