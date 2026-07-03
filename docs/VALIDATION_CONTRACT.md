# Arabesque - Contrat de validation live

Ce document est la source d'autorite lisible par un humain ou un agent pour
decider si Arabesque peut continuer a collecter, doit rester en risque reduit,
ou peut envisager une hausse de risque. Il ne remplace pas les guards live.
Les seuils structurables sont recopies dans `config/validation_policy.yaml`.

## Objectif systeme — ETOILE POLAIRE (2026-07-03, decision operateur)

**LE SYSTEME DOIT PRODUIRE >= 1,5R NET/MOIS SOUS CONTRAINTES PROP FIRM.**
Toute strategie est un moyen jetable. L'objectif n'est PAS qu'une strategie
donnee marche ; c'est que le portefeuille soit rentable net-de-couts dans les
conditions prop firm (daily DD, total DD, courbe reguliere). Jalon
intermediaire : >= 0,8R/mois (Glissade-XAUUSD + Renverse-metaux valides).
Metrique tracee a chaque `/bilan` : R net/mois du portefeuille valide.

## Perimetre courant

- Phase en cours : `portefeuille` (remplace `phase4_bis`, close 2026-07-03
  par la concentration — cf. DECISIONS.md 2026-07-03 ter).
- Live actif : `glissade` sur XAUUSD uniquement (seul edge net valide,
  ~+0.24R, n=16 WF — mince, sous surveillance edge_audit).
- Exclues du dispatch mais generant leurs signaux (mesure passive gratuite) :
  `extension`, `fouette` (les 2 brokers). `cabriole` desactivee (2026-05-16).
- Candidat en instruction : `renverse` sur metaux (XAG +0.262R n=21,
  XAU +0.130R n=9, net estime ~+0.17R) — pipeline obligatoire avant live.
- Brokers a lire separement : `ftmo_challenge` (micro-taille, fenetre DD
  -7/-8, venue de mesure), `gft_compte1` (venue principale metaux — spread
  3x moins cher, mesure 2026-07-03).
- Niveau actuel : collecte prudente sous guards/rodage/protection ; aucune
  hausse de risque n'est autorisee sur le seul motif que le service tourne.

## Filtre dur d'acceptation (tout candidat, avant tout live)

1. **Edge BRUT >= 3x le cout mesure du marche vise** (table de couts P1 ;
   ordres de grandeur mesures : metaux GFT ~0.015R, forex ~0.02-0.05R,
   crypto cTrader ~0.05-0.12R — crypto de fait inaccessible aux edges fins).
2. **Stable inter-fenetres** en walk-forward sur regime RECENT (18 mois
   glissants), pas seulement en moyenne longue.
3. **Debit >= ~2 trades/mois** cumules sur le perimetre du candidat.
4. **Profil compatible prop firm** : WR eleve, pertes bornees, gains
   plafonnes — la boussole protege le PROFIL, pas une strategie donnee.

Pipeline apres filtre : Wilson CI sur OOS cumule -> dry-run parquet 3 mois ->
shadow live 2-4 semaines -> go live operateur. Etude de COUTS SUR DOSSIER
avant toute ligne de code (lecon Extension : l'edge brut sans modele de couts
est une illusion).

## Enterrement et rehabilitation

- Un enterrement est **definitif sur la periode mesuree** (le fait ne se
  retournera pas), pas eternel (un regime peut revenir).
- Rehabilitation d'une strategie exclue = WF recent net-de-couts positif
  + go operateur. Les exclues generent toujours leurs signaux : la preuve
  s'accumule passivement. Re-walk-forward trimestriel des enterrees.
- Surveillance continue du vivant par `edge_audit` (l'outil qui a detecte la
  mort d'Extension detectera celle de Glissade si elle vient).

## Regles de decision (hausse de risque)

Une hausse de risque n'est envisageable, PAR STRATEGIE LIVE, que si toutes
les conditions suivantes sont satisfaites :

1. Au moins `30` exits propres de la strategie ; `50` est la cible de
   decision.
2. Absence d'invariant d'execution nouveau en alerte/critique, par broker.
3. `mean_delta_r >= -0.10R` sur le scope actif, avec lecture par broker et
   agregat.
4. Aucun incident ouvert d'integrite de position : protection broker non
   confirmee, risque reel post-fill trop eleve, exit fantome, position non
   suivie, fill anormal non traite.
5. Les trades micro-dimensionnes sont mesures ; un echantillon domine par des
   tailles non representatives ne valide pas l'edge.

Echelle de ramp : rodage x0.25 -> x0.50 -> x1.0, puis risque 0.45% -> 0.80%.
Chaque cran = go operateur. Achat d'un nouveau compte prop : uniquement quand
le portefeuille valide atteint ~1,5R/mois (decision operateur 2026-07-03).

Actions interdites sans nouvelle decision documentee :

- reactiver une strategie exclue (`extension`, `fouette`, `cabriole`,
  `glissade` hors XAUUSD) ;
- mettre un candidat en live sans le pipeline complet (filtre dur -> dry-run
  -> shadow -> go) ;
- augmenter le risque pendant qu'un incident d'execution est ouvert ;
- conclure a un drift strategique sur la seule base d'un probleme feed/broker ;
- relever la garde DD FTMO au-dela de 9% (pause -8%) sans nouveau go.

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
- `scripts/shadow_reference_check.py` : wrapper read-only qui lance ensemble
  signaux theoriques vs live et live vs theorie, puis persiste un verdict dans
  `logs/shadow_reference_checks.jsonl`.
- `scripts/audit_uptime_events.py` : facteur uptime depuis
  `logs/uptime_events.jsonl`, pour mesurer la frequence et la cause des
  periodes ou Arabesque ne pouvait pas trader normalement.
- `scripts/audit_edge_live_vs_backtest.py` : edge live contre baseline.
- `scripts/check_execution_invariants.py --per-broker` : bugs de tracking.
- `scripts/audit_sizing_distortion.py` : representativite des volumes live.
- `logs/trade_journal.jsonl` : preuve primaire des fills et sorties.
- `logs/broker_guard_rejects.jsonl` : rejets par broker, dont pre-vol GFT et
  quarantaine ; `replay_signals_vs_live.py` les classe comme blocages connus.

## Travail a construire sans modifier l'edge

Un `shadow_reference` permanent manque encore. Le premier palier est
`scripts/shadow_reference_check.py`, volontairement read-only et utilisable par
un humain, un timer ou un agent. Le palier permanent devra :

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
- journaliser `gft_quote_coherence_check` dans
  `logs/gft_quote_coherence.jsonl`, avec prix de reference cTrader, bid/ask
  GFT, offset en prix/R/ATR et decision `allow|block` ;
- refuser une derive defavorable superieure a `0.25R` ou au seuil ATR live ;
- apres fill, confirmer SL/TP serveur via les ordres lies ; tenter un amend
  unique si necessaire ;
- mettre en quarantaine les nouvelles entrees GFT si la protection ne peut
  etre confirmee, tout en continuant a monitorer la position existante.

## Integrite risque post-fill

Chaque position confirmee broker-side doit produire un
`risk_integrity_check` dans `logs/trade_journal.jsonl`.

- Le risque reel est estime depuis `entry`, `SL`, `volume` et les metadonnees
  broker (`pip_size`, `lot_size`) plutot que seulement depuis le sizing
  theorique.
- `risk_ratio < 0.50` : sous-risque, trade conserve, Telegram non urgent,
  calibrage a corriger pour les prochains ordres.
- `0.50 <= risk_ratio <= 1.25` : coherent.
- `1.25 < risk_ratio <= 1.50` : sur-risque, Telegram+ntfy, nouvelles entrees
  broker bloquees, position surveillee.
- `risk_ratio > 1.50` : sur-risque critique, Telegram+ntfy, blocage broker et
  demande de cloture immediate ; si la cloture echoue, la position reste
  suivie.

Une sous-exposition ne valide pas l'edge statistique si elle domine
l'echantillon ; une surexposition est un incident de securite avant d'etre une
donnee de performance.

## Integrite feed externe

Le watchdog externe ne se limite plus a "derniere barre fermee". Il lit aussi
les resumes `PriceFeed` du journal live. Si les barres continuent mais que le
flux annonce un etat partiel (`30/31 actifs`, stale majeur ou symbole jamais
recu), il journalise `pricefeed_partial` et notifie Telegram sans restart
automatique. Ce signal sert a detecter un feed degrade par symbole ; il ne doit
pas servir a declencher des ordres depuis un flux secondaire.

Chaque passage watchdog ecrit aussi un `uptime_sample` dans
`logs/uptime_events.jsonl`. L'objectif n'est pas seulement l'alerte immediate :
il faut pouvoir quantifier l'uptime effectif et attribuer les absences de
signal a `ok`, `partial_feed`, `feed_stale`, `engine_inactive`, `weekend` ou
autre cause.

## Integrite BE polling

Le filet de secours BE ne doit pas seulement armer quand tout va bien. Chaque
passage avec positions ouvertes ecrit un `be_polling_pass` via le journal
d'audit, avec `checked`, `armed`, `skipped` et `skip_reasons`. Un
`be_polling_armed` reste l'event de preuve d'un BE effectivement pose par le
polling backup. Des skips repetes (`quote_none`, `quote_stale_or_clock_skew`,
`ctrader_missing_market_ts`, etc.) sont a lire comme un probleme de
disponibilite du filet, pas comme un resultat strategique.

## Routage des notifications

- **Telegram** est le flux complet : demarrage, rapport, suivi, drift,
  `CAUTION`, rejets non urgents et alertes urgentes.
- **ntfy** est reserve aux evenements exigeant une attention rapide :
  `DANGER` / `EMERGENCY`, protection broker non confirmee, fill aberrant,
  amend SL abandonne, position absente broker-side, panne feed avec
  auto-restart/anti-boucle/echec ou health check `CRITIQUE`.
- Les rapports planifies, rappels `/suivi`, analyses de drift et retours a
  l'etat normal ne doivent pas atteindre ntfy.

## Maintenance de ce contrat

Toute modification des strategies actives, de la fenetre Phase 4 bis, des
seuils de decision ou du modele shadow doit modifier ensemble :

- ce document ;
- `config/validation_policy.yaml` ;
- `HANDOFF.md` et `docs/DECISIONS.md` ;
- les instructions agent (`CLAUDE.md` / `.claude/commands/suivi.md`) si leur
  comportement de suivi est concerne.
