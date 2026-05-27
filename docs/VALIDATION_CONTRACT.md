# Arabesque - Contrat de validation live

Ce document est la source d'autorite lisible par un humain ou un agent pour
decider si Arabesque peut continuer a collecter, doit rester en risque reduit,
ou peut envisager une hausse de risque. Il ne remplace pas les guards live.
Les seuils structurables sont recopies dans `config/validation_policy.yaml`.

## Perimetre courant

- Phase en cours : `phase4_bis`.
- Debut de fenetre : `2026-05-16T08:44:00Z`.
- Strategies du verdict : `extension`, `glissade`.
- `cabriole` et tout alias `trend` historique restent visibles dans
  l'historique global mais sont exclus du verdict Phase 4 bis.
- Brokers a lire separement : `ftmo_challenge`, `gft_compte1`.
- Niveau actuel : collecte prudente sous guards/rodage/protection ; aucune
  hausse de risque n'est autorisee sur le seul motif que le service tourne.

## Regles de decision

Une hausse de risque n'est envisageable que si toutes les conditions suivantes
sont satisfaites :

1. Au moins `30` exits propres Phase 4 bis ; `50` est la cible de decision.
2. Absence d'invariant d'execution nouveau en alerte/critique, par broker.
3. `mean_delta_r >= -0.10R` sur le scope actif, avec lecture par broker et
   agregat.
4. Aucun incident ouvert d'integrite de position : protection broker non
   confirmee, exit fantome, position non suivie, fill anormal non traite.
5. Les trades micro-dimensionnes sont mesures ; un echantillon domine par des
   tailles non representatives ne valide pas l'edge.

Actions interdites sans nouvelle decision documentee :

- reactiver `cabriole` ;
- augmenter le risque pendant qu'un incident d'execution est ouvert ;
- conclure a un drift strategique sur la seule base d'un probleme feed/broker.

## Attribution d'un ecart live / theorie

| Constat | Lecture a verifier |
| --- | --- |
| Theorie et live perdent de facon comparable | regime ou edge strategique |
| Theorie gagne, live perd | execution, protection, spread/slippage ou journal |
| Signal theorique absent des deux brokers | feed/moteur/donnees source |
| Un broker tire, l'autre manque | connecteur, guard ou instrument broker |
| Protection non confirmee apres fill | incident de securite, pas statistique |

## Briques existantes obligatoires

- `scripts/replay_signals_vs_live.py` : couverture des signaux theoriques ;
  lire Extension sur sessions dedupees, jamais sur bougies brutes.
- `scripts/replay_live_vs_theory.py` : `delta_r` des trades executes.
- `scripts/audit_edge_live_vs_backtest.py` : edge live contre baseline.
- `scripts/check_execution_invariants.py --per-broker` : bugs de tracking.
- `scripts/audit_sizing_distortion.py` : representativite des volumes live.
- `logs/trade_journal.jsonl` : preuve primaire des fills et sorties.
- `logs/broker_guard_rejects.jsonl` : rejets par broker, dont pre-vol GFT et
  quarantaine ; `replay_signals_vs_live.py` les classe comme blocages connus.

## Travail a construire sans modifier l'edge

Un `shadow_reference` permanent manque encore. Il devra :

- utiliser les generateurs et parametres de reference sans envoyer d'ordre ;
- journaliser `signal_generated`, decision shadow, decision par broker, fill,
  protection et exit avec un identifiant stable ;
- conserver les barres live ayant produit le signal, ou leur empreinte, afin
  de distinguer une divergence de donnees d'une divergence d'execution ;
- produire un verdict automatisable, sans changer signal, sizing ou guards
  avant validation separee.

Ce chantier est requis avant une conclusion forte de rentabilite, mais il ne
prime pas sur une correction de securite touchant une position reelle.

## Integrite GFT

Avant toute nouvelle entree GFT, le code doit :

- relire une quote REST TradeLocker ; absence de quote = refus ;
- refuser une derive defavorable superieure a `0.25R` ou au seuil ATR live ;
- apres fill, confirmer SL/TP serveur via les ordres lies ; tenter un amend
  unique si necessaire ;
- mettre en quarantaine les nouvelles entrees GFT si la protection ne peut
  etre confirmee, tout en continuant a monitorer la position existante.

## Maintenance de ce contrat

Toute modification des strategies actives, de la fenetre Phase 4 bis, des
seuils de decision ou du modele shadow doit modifier ensemble :

- ce document ;
- `config/validation_policy.yaml` ;
- `HANDOFF.md` et `docs/DECISIONS.md` ;
- les instructions agent (`CLAUDE.md` / `.claude/commands/suivi.md`) si leur
  comportement de suivi est concerne.
