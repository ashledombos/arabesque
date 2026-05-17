# Review historique trading — 2026-05-15

Sources lues:

- `docs/DECISIONS.md`
- `docs/EXPERIMENT_LOG.md`
- `HANDOFF.md`
- `logs/journal/2026-04.md`
- `logs/journal/2026-05.md`
- `logs/edge_audit_latest.md`
- `logs/trade_journal.jsonl`
- audit Codex dans `../arabesque_bt_audit/`

## Synthese

Le projet a evolue par couches successives. Les resultats les plus recents remplacent certaines conclusions anciennes.

La ligne courante la plus coherente est:

```text
Production candidate = Extension + Glissade
Cabriole = a couper du live
Fouette = observation / paper / rodage symbolique
Risk guard central = open_risk_limit a 2%
Objectif = regularite prop firm, pas maximisation du nombre de trades
```

## Chronologie utile

### Fevrier 2026 — selection du coeur strategique

Les tests v3.0 -> v3.3 ont montre que les mecanismes BB_RPB_TSL purs ne se transplantent pas directement:

- ROI court + SL reel tue l'avg win.
- Mean-reversion perd sur plusieurs replays et univers.
- Trend-only devient la base productive.
- BE 0.3R / offset 0.20R devient le levier principal du WR.

Decision encore valide:

- Trend/Extension reste le coeur du systeme.
- Ne pas reintroduire ROI court, TP partiel agressif ou sorties qui tuent les runners.

Point documentaire obsolescent:

- Certaines sections anciennes de `DECISIONS.md` parlent encore de mean-reversion comme edge principal. A lire comme historique remplace par les decisions trend-only plus recentes.

### Mars 2026 — execution live et protections

Incidents importants:

- TSL H1-only insuffisant: tick-level non negociable.
- Bugs TradeLocker: `order_id != position_id`, protection SL/TP, pip size.
- Faux emergency DD lie au mauvais tracking du start balance.

Decisions encore valides:

- La qualite d'execution est aussi importante que le signal.
- DD tracking persistant obligatoire.
- SL/BE doivent etre physiques cote broker des que possible.
- TradeLocker/GFT doit etre traite comme un broker distinct, pas comme equivalent FTMO.

### Avril 2026 — divergence live, GFT et Cabriole

Le journal d'avril montre un pattern net:

- GFT sous-performe fortement, surtout sur Cabriole et certaines heures creuses.
- Cabriole GFT fait 0/10 dans les journaux.
- Des positions orphelines et des entries logguees avant fill ont pollue la lecture.
- Le filtre `strategy_broker_exclusions` a ete ajoute pour bloquer Cabriole sur GFT.

Decision a renforcer:

- Cabriole n'est plus seulement "backup valide"; elle est invalidee en live tant que l'execution-realistic replay ne prouve pas le contraire.

### Mai 2026 — drift, fixes reconcile, panne PriceFeed

Le journal de mai clarifie plusieurs faux signaux:

- Une partie du drift live/backtest venait de bugs de reconstruction/reconcile.
- La panne PriceFeed du 14 mai a cause le trade Glissade XAUUSD -1R alors que le BE aurait du etre arme si le feed avait tourne.
- Le watchdog externe est donc necessaire.
- L'audit pipeline du 15 mai a corrige la coherence timeframe/config et le strict-data mode.

Decision encore valide:

- Ne pas conclure a un drift d'edge quand l'incident est infra/execution.
- Phase 4 doit etre jugee apres exclusion des incidents documentes.

## Lecture par strategie

### Extension

Statut: garder.

Arguments:

- Base historique la plus robuste.
- Backtest portefeuille sans Cabriole reste prop-compatible avec `open_risk_limit`.
- Le live recent est brouille par une phase defavorable et des bugs, mais pas invalide structurellement.

Conditions:

- Conserver `max_open_risk_pct = 2.0`.
- Ne pas augmenter le risque tant que Phase 4 n'a pas assez de trades propres.
- Continuer a monitorer live vs parquet par broker.

### Glissade

Statut: garder.

Arguments:

- Baseline et audit prop tres propres.
- Faible frequence attendue, pas un bug.
- Le trade XAUUSD du 14 mai est un incident PriceFeed, pas un signal strategique invalide.

Conditions:

- Accepter que Glissade soit une brique lente.
- Chercher eventuellement plus d'instruments Glissade offline.
- Ne pas compenser la faible frequence par un risque disproportionne.

### Fouette

Statut: observation.

Arguments:

- WF documente positif.
- Mais live/replay recent ne montre pas encore une contribution utile.
- Les signaux M1 manques restent a investiguer.

Decision:

- Paper/rodage symbolique.
- Pas de production pleine avant clarification M1 live/parquet.

### Cabriole

Statut: couper du live.

Arguments:

- GFT invalide clairement.
- FTMO recent mauvais.
- `edge_audit_latest.md`: drift structurel action requise.
- Audit portefeuille montre que le systeme est meilleur une fois Cabriole retiree.

Conditions de rehabilitation:

- uniquement offline/paper;
- revalidation broker-specific;
- preuve execution-realistic, pas seulement signal backtest.

## Incoherences et hygiene documentaire

Ne pas effacer le passe: il explique les decisions. Mais ajouter des marqueurs explicites:

- "Decision remplacee" pour mean-reversion edge principal.
- "Decision remplacee" pour Cabriole backup/live.
- "Etat courant" dans `HANDOFF.md` a mettre a jour apres action utilisateur sur Cabriole.

## Recommandations a l'agent codeur

1. Desactiver Cabriole dans la config live sans toucher aux fichiers `signal.py`.
2. Ne pas relacher `max_open_risk_pct = 2.0`.
3. Ne pas modifier la logique BE/trailing sur la base de Cabriole.
4. Construire ensuite un vrai replay combine multi-strategies dans le code principal.
5. Ajouter une sortie de rapport prop firm standard:
   - pire jour,
   - max DD,
   - max risk ouvert,
   - raisons de rejet,
   - contribution par strategie,
   - best-day share,
   - breaches daily/total.
6. Mettre a jour `HANDOFF.md` apres la desactivation Cabriole pour eviter que le prochain agent lise Cabriole comme strategie active valide.

## Conclusion

La trajectoire du projet est coherente si l'on donne plus de poids aux donnees recentes d'execution live qu'aux validations theoriques anciennes.

Le noyau defendable aujourd'hui est:

```text
Extension + Glissade
avec contraintes portefeuille strictes
sans Cabriole
Fouette en observation
```

Le prochain saut de qualite n'est pas une nouvelle strategie. C'est un replay combine natif et une discipline stricte de validation live/parquet apres chaque changement.
