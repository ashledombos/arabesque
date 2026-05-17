# Audit prop firm replay — 2026-05-15

## Objectif

Verifier que le systeme reste dans le canal prop firm une fois les strategies mises en concurrence sur un seul portefeuille, et pas seulement evaluees instrument par instrument.

Perimetre audite:

- Extension
- Glissade
- Fouette en option
- Cabriole exclue du scenario cible

Artefacts source, generes hors depot dans `../arabesque_bt_audit/`:

- `07_portfolio_filter_replay.md`
- `08_decision_apres_replay_portefeuille.md`
- `portfolio_filter_summary.csv`
- `portfolio_filter_accepted.csv`
- `portfolio_filter_rejected.csv`
- `portfolio_filter_daily.csv`

## Definition: `open_risk_limit`

`open_risk_limit` est le garde-fou qui limite le risque total deja ouvert.

Il ne regarde pas le nombre de positions seulement. Il regarde la somme des pertes potentielles si toutes les positions ouvertes touchent leur SL.

Exemple:

- compte: 100 000
- `risk_per_trade_pct = 0.45%`
- un trade plein risque: 450 de perte potentielle au SL
- `max_open_risk_pct = 2.0%`
- risque ouvert maximum autorise: 2 000

Donc le systeme peut accepter environ 4 trades pleins risque simultanes:

```text
4 x 450 = 1 800  OK
5 x 450 = 2 250  bloque par open_risk_limit
```

Si certains trades sont en rodage ou tailles reduites, davantage de positions peuvent coexister tant que la somme du risque au SL reste sous 2%.

Dans le code, la logique est dans `arabesque/core/guards.py`, via `PropConfig.max_open_risk_pct` et `AccountState.open_risk_cash`.

Point important: ce guard est central pour les prop firms. Il empeche les clusters de signaux crypto/H4 de transformer plusieurs trades individuellement acceptables en journee de breach.

## Resultat du replay portefeuille

Methode:

- entree: trades candidats issus des backtests actifs;
- Cabriole retiree;
- trades remis en ordre chronologique;
- contraintes appliquees:
  - `max_positions = 5`
  - `max_open_risk_pct = 2.0`
  - duplicate instrument interdit
  - daily DD 3%
  - pause total DD vers -7%
  - risque nominal 0.45% par trade
  - rodage optionnel Glissade/Fouette x0.25

Resultats principaux:

| Scenario | Rodage | Trades acceptes | Total | Pire jour | Max DD | Jours breach -3% | Max risk ouvert |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Extension + Glissade | oui | 911 | +21.03% | -1.39% | 4.82% | 0 | 1.91% |
| Extension + Glissade + Fouette | oui | 1074 | +20.67% | -1.39% | 5.17% | 0 | 1.91% |
| Extension seule | oui | 776 | +19.10% | -1.53% | 5.01% | 0 | 1.80% |
| Glissade seule | oui | 138 | +2.20% | -0.11% | 0.29% | 0 | 0.23% |
| Extension + Glissade | non | 909 | +26.98% | -1.53% | 4.78% | 0 | 1.80% |
| Extension + Glissade + Fouette | non | 1070 | +25.63% | -1.71% | 6.15% | 0 | 1.80% |

Lecture:

- Extension filtree par univers ne suffit pas seule si tous les signaux sont additionnes naivement.
- Extension devient prop-compatible quand `open_risk_limit` met les signaux en concurrence.
- Extension + Glissade est le meilleur compromis actuel.
- Fouette ajoute du volume mais degrade legerement le resultat et le DD.
- Glissade seule est tres propre mais trop lente pour porter le systeme.

## Decision proposee

1. Couper Cabriole du live.
2. Garder Extension.
3. Garder Glissade.
4. Garder Fouette en paper, ou en rodage symbolique uniquement tant que l'edge live reste non demontre.
5. Ne pas relacher `max_open_risk_pct = 2.0`.
6. Ne pas augmenter le risque tant que Phase 4 n'a pas valide la coherence live/parquet apres les fixes PriceFeed/reconcile/BE.

## Review historique

### Extension

Historique decisions:

- Extension/Trend-only est devenu le coeur du systeme apres abandon de la mean-reversion.
- Validation de reference: 20 mois, 1998 trades, WR 75.5%, Exp +0.130R, 8/8 blocs temporels positifs.
- Les journaux avril-mai montrent une phase defavorable, pas une invalidation structurelle.

Constat audit:

- Le panier Extension reste positif dans le replay portefeuille.
- Le guard `open_risk_limit` rejette environ 155 trades Extension dans le scenario Extension seule.
- Ces rejets sont souhaitables: ils transforment un panier potentiellement trop charge en portefeuille prop-compatible.

Decision:

- Continuer Extension.
- Ne pas confondre "filtrage d'univers" et "controle de risque portefeuille".
- Le controle portefeuille est aussi important que le choix des instruments.

### Glissade

Historique decisions:

- Walk-forward 3/3 PASS.
- Baseline XAUUSD/BTCUSD H1: WR eleve, Exp autour de +0.18R/+0.20R.
- Journaux live: peu de trades, ce qui est coherent avec la nature RSI divergence.

Constat audit:

- Backtest actif: 138 trades sur environ 22 mois pour 2 instruments.
- Profil tres propre en prop metrics.
- Glissade seule est trop lente, mais c'est une bonne brique de diversification.

Decision:

- Garder Glissade.
- Ne pas juger sa faible frequence comme un bug.
- Pour augmenter sa contribution, chercher une extension d'univers Glissade en recherche offline, pas augmenter brutalement son risque.

### Fouette

Historique decisions:

- WF 4/4 PASS dans les docs.
- Mais le live a produit 0 trade ou tres peu de trades selon les periodes.
- Des signaux M1 manques le 2026-05-13 restent a investiguer.

Constat audit:

- Dans le replay actif recent, Fouette est legerement negatif.
- Dans le replay portefeuille, l'ajouter a Extension + Glissade degrade legerement le total et augmente le DD.

Decision:

- Ne pas en faire une brique de production pleine.
- Le garder en paper/rodage symbolique tant que les problemes live M1 et la valeur marginale ne sont pas clarifies.

### Cabriole

Historique decisions:

- Les docs anciennes classent Cabriole comme WF PASS / backup.
- Les journaux avril-mai invalident cette lecture live:
  - GFT: 0/10 pertes sur Cabriole, probleme d'execution broker etabli.
  - FTMO: Cabriole reste tres mauvais dans le journal live.
  - `edge_audit_latest.md` au 2026-05-10: Cabriole live n=39, Exp -0.664R, verdict drift structurel.

Constat audit:

- Replay actif: Cabriole positif seulement marginalement en aggregate, mais SOLUSD et BTCUSD degradent fortement.
- Live recent: signal clairement mauvais.
- La divergence n'est plus un simple small-n.

Decision:

- Couper Cabriole du live.
- Conserver seulement en recherche offline/paper.
- Ne pas la reactiver sans preuve explicite:
  - backtest execution-realistic,
  - broker-specific,
  - Phase live paper positive,
  - pas seulement WF theorique.

## Points de coherence documentaire

Les documents historiques contiennent des decisions devenues obsoletes:

- `DECISIONS.md` contient encore des passages anciens indiquant que mean-reversion est l'edge principal, alors que les sections plus recentes disent trend-only.
- `HANDOFF.md` mentionne encore un etat "LIVE SUSPENDU 2026-05-07" dans l'en-tete de section, alors que la Phase 4 a ete reprise ensuite.
- Cabriole est encore decrite comme backup valide dans certaines sections, mais le journal live avril-mai justifie maintenant une desactivation.

Recommandation pour l'agent codeur:

- Ne pas supprimer l'historique.
- Ajouter une section "Etat courant / decisions remplacees" plutot que reecrire le passe.
- Faire primer les entrees datees les plus recentes, en particulier:
  - incident PriceFeed 2026-05-14/15,
  - audit validation pipeline 2026-05-15,
  - present audit prop firm replay 2026-05-15.

## Prochaine etape technique utile

Implementer un vrai replay combine multi-strategies dans le code principal.

Ce replay devrait:

- generer tous les signaux Extension/Glissade/Fouette dans une seule timeline;
- appliquer les vrais guards du live;
- modeliser `open_risk_cash`, daily reset, DD, duplicate instrument;
- produire une seule equity curve;
- exporter les raisons de rejet;
- permettre `--disable-strategy cabriole`;
- permettre `--scenario extension+glissade`.

Le replay d'audit actuel est suffisant pour la decision de portefeuille, mais un replay combine natif deviendrait l'outil de validation recurrent avant tout changement de strategie ou de risk.
