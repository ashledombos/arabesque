# Phase 4 bis — Checklist Extension + Glissade

Date: 2026-05-16

Objectif: reprendre une observation live propre sur le noyau `extension + glissade`, avec Cabriole désactivée séparément, sans chercher à optimiser les stratégies pendant la collecte.

Cette phase ne sert pas seulement à vérifier le WR ou l'expectancy. Elle sert surtout à vérifier que le système reste dans un couloir compatible prop firm: drawdown contenu, régularité, absence de breach, absence de dépendance à un seul gros jour, et cohérence backtest / live parquet / exécution broker.

## Périmètre

Stratégies suivies:
- `extension`: coeur principal, trade count suffisant, edge faible à modéré mais plausible.
- `glissade`: edge backtest plus net, mais faible fréquence live attendue.
- `fouette`: à garder en observation/paper ou rodage symbolique, pas dans le coeur de décision.
- `cabriole`: à exclure de la Phase 4 bis tant que le drift live n'est pas réexpliqué.

Référence d'audit:
- `docs/AUDIT_PROP_FIRM_REPLAY_2026-05-15.md`
- `docs/BACKTEST_LIVE_PARQUET_REVIEW_2026-05-15.md`
- `docs/REVIEW_HISTORIQUE_TRADING_2026-05-15.md`

## Préconditions

Avant de considérer la Phase 4 bis active:

- Cabriole désactivée en config live.
- `arabesque-live.service` actif.
- `arabesque-feed-watchdog.timer` actif.
- `arabesque-report-daily.timer` actif.
- `arabesque-suivi-reminder.timer` actif.
- Aucun service annexe en `failed`.
- Pas de position live sans SL.
- Pas d'ordre pending orphelin ou stale.
- `live.be_polling_backup` reste `false` tant que le replay 14/05 et la gate dédiée ne sont pas validés.
- GALUSD reste connu comme parquet stale jusqu'à réingestion; ne pas l'utiliser comme preuve de dérive edge.

## Surveillance Quotidienne

Chaque jour de marché, vérifier:

- Statut systemd des services live, watchdog, report daily, suivi reminder.
- Dernières alertes health check.
- Derniers exits dans `logs/trade_journal.jsonl`.
- Écart live parquet vs théorie sur les trades clos récents.
- Éventuels trades manqués par broker.
- Résumé des positions ouvertes: open risk, SL présents, BE attendu vs BE réellement armé.
- Equity snapshot récent.

Le point important n'est pas une journée négative isolée. Le vrai problème est un pattern: trade manqué, BE non armé, broker divergent, drift live parquet répété, ou daily DD qui s'approche trop vite d'une limite interne.

## Seuils Go / No-Go

Continuer si tous les points suivants restent vrais:

- Aucun breach prop firm.
- Worst day observé reste sous le seuil interne, idéalement meilleur que `-1.5%`.
- Max drawdown observé reste inférieur à `5%` sur la phase.
- Max open risk reste inférieur ou égal à `2%`.
- Pas de position sans SL.
- Pas de série d'erreurs d'exécution sur un broker.
- Écart moyen live parquet vs backtest contenu, sans drift directionnel évident.
- Pas de domination du profit par un seul jour.

Stopper et diagnostiquer si un seul de ces points apparaît:

- SL absent ou non modifié alors que le système pense qu'il l'est.
- Trade broker fermé mais non réconcilié correctement.
- `mfe_zero_loser` non expliqué.
- `reconciled_other` répété.
- BE attendu non armé pour cause infra.
- Divergence broker supérieure à `0.5R` répétée hors spread/slippage documenté.
- Daily DD interne supérieur à `2%`.
- Drawdown phase supérieur à `5%`.
- Plus de 2 trades manqués non expliqués sur 20 exits.

## Validation Statistique Minimale

Ne pas conclure avant un échantillon exploitable:

- Minimum absolu: 50 exits propres sur le noyau Extension + Glissade.
- Meilleur seuil: 100 exits propres.
- Les exits doivent être "propres": pas issus d'une panne feed, pas reconstruits post-hoc après incident, pas marqués par un bug de sizing ou un fill orphelin.

Critères de lecture:

- Extension peut avoir une expectancy faible; elle se juge surtout sur stabilité, volume, absence de breach.
- Glissade peut avoir peu de trades; elle se juge sur qualité d'exécution et cohérence backtest/live, pas sur 5 ou 10 trades.
- Fouetté ne doit pas influencer la décision tant que sa contribution live est quasi nulle.
- Cabriole ne doit pas être réintégrée sur simple amélioration de court terme.

## Risque

Règles pendant la phase:

- Pas d'augmentation de risque avant 50 exits propres.
- Pas d'augmentation de risque si un incident infra majeur a pollué l'échantillon.
- Pas d'augmentation de risque si le profit vient d'un seul très bon jour.
- Pas d'augmentation de risque si le live parquet diverge de la théorie.

Après 50 exits propres:

- Si les métriques sont conformes, conserver le risque actuel ou relever uniquement les stratégies encore en rodage d'un cran prudent.
- Après 100 exits propres, réévaluer la possibilité de sortir Glissade du rodage si l'exécution live colle au backtest.

## Backtest / Live Parquet

Le backtest seul ne suffit pas pour valider une stratégie live prop firm.

Le triptyque attendu est:

1. Backtest: edge historique et comportement de risque plausibles.
2. Live parquet: mêmes signaux, mêmes filtres, mêmes timeframes que le live.
3. Journal broker: fills, SL, BE, TP et exits cohérents avec ce que le live parquet prévoyait.

Un écart backtest/live parquet indique souvent un problème de configuration, de timeframe, de mapping symbole ou de donnée. Un écart live parquet/broker indique plutôt un problème d'exécution, de spread, de slippage, de panne feed ou de réconciliation.

## Décision Actuelle

Au vu des audits disponibles, la combinaison la plus défendable est:

`Extension + Glissade`, avec Cabriole désactivée et Fouetté non décisionnel.

Ce n'est pas une preuve définitive d'edge fort. C'est un noyau raisonnable pour continuer la validation live, parce qu'il combine:

- volume suffisant via Extension;
- profil plus sélectif via Glissade;
- risque portefeuille maîtrisable;
- absence de breach dans le replay filtré;
- meilleure lisibilité que le panier complet avec Cabriole.

La prochaine décision importante ne doit pas être une optimisation de stratégie. Elle doit être la validation ou l'invalidation de cette combinaison sur 50 à 100 exits propres.
