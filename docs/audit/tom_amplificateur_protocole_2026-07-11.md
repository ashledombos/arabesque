# Protocole pré-enregistré — Turn-of-month en amplificateur d'Adage (session-or)

**Date de gel : 2026-07-11. Ce document est commité AVANT tout calcul.**
Item 3 de la file des GO (HANDOFF). Un tir : tout échec = KILL, toute variante
(autre fenêtre, autre politique de sizing) = nouveau protocole pré-enregistré.

## Question

L'effet turn-of-month or (écrémage 07-07 : +19,1 bps/j en fenêtre J-2..J+3 vs
+8,2 hors fenêtre, t=+1,63, 4/5 sous-périodes — `sourcing_familles_2026-07-07.md`
§3) se retrouve-t-il dans les **sessions Adage** au point de justifier un sizing
différencié au jalon 5, **à l'intérieur de la bande gravée 0,20-0,30 %/session**
(dérogation DD 07-10) ?

Ce n'est PAS une nouvelle famille (le sourcing 07-07 l'a explicitement mise en
réserve d'amplificateur : même moteur que la session-or, pas un edge décorrélé).
Le verdict ne change RIEN au live courant ni aux jalons Adage (ombre données en
cours, revue n≥30) — il grave uniquement la politique de sizing proposée à
l'opérateur pour le jalon 5 micro-live.

## Données (figées)

- `logs/adage_ombre_sessions.jsonl` — 635 sessions 2024-01-29 → 2026-07-09,
  rejouées par la chaîne Orchestrator (jalon 4, backfill + ombre). Champ
  décisionnel : `result_r_net` (coûts inclus — on juge NET pour du sizing).
- Aucune autre donnée. Aucun re-fetch.

## Définitions (figées)

- **Grille de jours ouvrés** = dates UTC uniques des `fill_ts` de la série
  elle-même (fill 22:00-23:00 UTC ≈ 1 session/jour ouvré XAUUSD).
- **Fenêtre ToM** (identique à l'écrémage 07-07, J-2..J+3, 5 jours ouvrés) :
  les **2 dernières dates de la grille dans le mois M** ∪ les **3 premières
  dates de la grille dans le mois M+1**. Fenêtres partielles aux bords de la
  série acceptées telles quelles. Aucune autre fenêtre ne sera testée.
- **Politique amplifiée candidate** (unique) : risque **0,30 %/session en ToM,
  0,20 % hors ToM** — les deux bornes de la bande gravée, rien en dehors.
  Référence : uniforme 0,25 %.
- **Sous-périodes** : 5 blocs temporels d'effectif égal (~127 sessions).

## Statistiques décisionnelles et critères (gelés)

Soit Δ = Exp_net(ToM) − Exp_net(hors ToM), en R/session.

- **(a) Matérialité de l'écart** : Δ ≥ **+0,05R** ET Exp_net(hors ToM) ≥
  **−0,02R** (si la base hors fenêtre est matériellement négative, ce n'est
  plus un amplificateur mais une concentration — constat remonté à l'opérateur
  tel quel, pas de requalification unilatérale en filtre ToM-only).
- **(b) Stabilité** : Δ > 0 dans **≥ 4/5 blocs temporels**.
- **(c) Non-illusion de queue** : Δ > 0 **après retrait des 3 meilleures
  sessions ToM** de toute la série.
- **(d) Efficience de la politique** : en % d'équité,
  ratio = (Σ gains nets)/( |maxDD| ) de la politique 0,30/0,20 ≥
  **1,10 ×** le ratio de l'uniforme 0,25, **ET** maxDD de la politique ≤
  **4,05 % d'équité** (= enveloppe de la dérogation DD : 16,2R × 0,25 %).

**PASS amplificateur** = (a) ET (b) ET (c) ET (d) → proposer à l'opérateur, pour
le jalon 5 uniquement : sizing 0,30 % ToM / 0,20 % hors ToM (paramètre
opérateur, zéro code moteur). **KILL** sinon → sizing uniforme, réserve ToM
enterrée avec chiffres.

Rapportés à titre descriptif (non décisionnels) : n par groupe, t de Welch sur
Δ, WR par groupe, table par bloc, Exp brut (`result_r`) en contrôle.

## Exécution

Script un-tir `tmp/tom_amplificateur_study.py` → `tmp/tom_amplificateur_results.txt`.
Verdict annexé à ce document + EXPERIMENT_LOG + HANDOFF ; DECISIONS si décision
de sizing gravée.
