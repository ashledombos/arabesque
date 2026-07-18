# Registre des hypothèses porteuses — matière première de `/table-rase`

> Une ligne = une croyance sur laquelle des décisions reposent. Le procès
> mensuel re-teste en priorité les plus **anciennes** et les plus **lourdes**.
> Tampon = « tient toujours au JJ-MM » (posé par un run `/table-rase` ou une
> mesure documentée). 🕰️ = preuve > 6 mois sans re-vérification → à re-tester
> ou déclasser. La boussole (CLAUDE.md) n'est pas ici : c'est le cahier des
> charges, pas une hypothèse.

Poids : ❗❗ = si faux, le live ou la route entière change ; ❗ = une décision
majeure change ; ○ = local.

## Stratégique (le portefeuille repose dessus)

| ID | Hypothèse | Preuve | Fenêtre | Poids | Tampon |
|---|---|---|---|---|---|
| S1 | Glissade-XAUUSD est le seul edge net vivant du parc CFD (+0,24R net) | Banc 07-01→03 | 20 mois, n=16 live | ❗❗ | 2026-07-18 (rien ne le contredit : replay XAUUSD sans drift ; n live insuffisant pour re-juger — critère = ramp n≥30) |
| S2 | Extension n'a d'edge net NULLE PART en régime récent (brut négatif forex/métaux) | Banc 07-03, étape 3.5 | 12-20 mois | ❗❗ | 2026-07-18 (9 derniers live −0,605R, BT plat ; IC99 live [−0,45;−0,16] contre-expertise) |
| S3 | Les edges nets survivants sont MÉTAUX only (crypto liquide net ≤ 0 aux coûts réels ~0,11R) | Banc 07-01/03 | 20 mois | ❗ | 2026-07-03 |
| S4 | Le drift nocturne de l'or est réel (+0,070R net/session) et SPÉCIFIQUE à l'or (Brent −5,8 bps) | WF gelé 07-10 + écrémage 07-11 | 30 mois | ❗❗ | 2026-07-12 |
| S5 | Le creux Adage mai-juin 2026 est un creux DE la distribution, pas la mort de l'edge (dérogation DD −16,2R) | Dérogation opérateur 07-10 | — | ❗❗ | 🟡 EN TEST — série à 0,36R du tripwire (−15,84R), 90 derniers jours −9,1R, edge historique concentré sur 3 trimestres (table-rase 07-18) ; le seuil pré-enregistré décide |
| S6 | Les anomalies calendaires/sessionnelles de la littérature sont mortes en régime 2025-26 (3 cycles, 11 kills) | Sourcing 07-07/11/12 | 30 mois | ❗ | 2026-07-12 |
| S7 | Renversé : edge brut insuffisant en l'état, l'ombre tranchera | Dossier 07-04 | 20 mois | ○ | 2026-07-04 |
| S8 | Fouetté (US100/or), Révérence, Cabriole : morts ou redondants en régime récent (réhabilitations = octobre) | Bancs 07-03/07-07 | 18-20 mois | ○ | 2026-07-07 |

## Calibration (héritée de l'ère Extension — s'applique au parc actuel)

| ID | Hypothèse | Preuve | Fenêtre | Poids | Tampon |
|---|---|---|---|---|---|
| C1 | BE 0,3R / offset 0,20R porte le WR ~45→75 % (0,15 = plancher exact 95 % des sorties) | Sim exhaustive 2025 | 20 mois, 1998 trades | ❗❗ | 🕰️ preuve sur Extension (morte) — gap live = marché, pas exéc. (06-08). **Re-test proposé** (table-rase 07-18, re-test n°1, sur go) |
| C2 | Risk 0,40 %/trade : maxDD 8,2 % vs 10,3 % à 0,50 % (marge FTMO 1,8 %) | Sim 2025 | 20 mois | ❗ | 🕰️ même chantier que C1 |
| C3 | Tick-TSL non optionnel (+183R vs +10,4R en H1-only) | Backtest 2025 | 20 mois | ❗ | 🕰️ même chantier que C1 |
| C4 | Mean-reversion perd partout, toutes venues (y c. aux coûts HL) | 4 replays + MR-HL 07-09 | multi | ❗ | 2026-07-09 |
| C5 | Le gap de capture BE live↔BT (~+2R/3 mois) = marché (aller-retours), PAS exécution → ne pas toucher la calib | Audit 06-08 | 3 mois | ❗ | 2026-06-08 |
| C6 | La sous-perf live = couverture 29 % + variance, PAS biais de sélection (population = référence d'edge, +0.137R) | Clôture 06-20 | 3 mois | ❗ | 2026-06-20 (re-mesure à n_pris≥40) |
| C7 | WR backtest compte BE comme win ; le vrai écart live↔BT = taux de pertes pleines (45 vs 22 %) | Analyse 06 | 3 mois | ○ | 2026-06 |

## Coûts et venues

| ID | Hypothèse | Preuve | Fenêtre | Poids | Tampon |
|---|---|---|---|---|---|
| V1 | Coûts FTMO ~0,078R/trade (2,6× l'edge fin) = invariant en R, sizer up n'aide pas ; juger l'edge en BRUT | Mesure 06 | 3 mois | ❗ | 2026-06 |
| V2 | Coûts GFT non journalisés (trou de données assumé) | Constat 06 | — | ○ | ouvert |
| V3 | Spread nuit XAUUSD 0,98 bps médian (21-03 UTC) ; XAG 10-11 bps = kill | Sondeur 7 nuits 07-10 | 7 nuits | ❗ | 2026-07-10 |
| V4 | Binance = proxy de prix valide pour Hyperliquid (corr 1h ≥ 0,998, basis −4,5 bps stable) | Sonde 07-08 | échant. | ○ | 2026-07-08 |
| V5 | Funding HL de croisière (5-12 %/an) ne paie pas le carry ; réveil = moniteur seul (≥3 instr. > 20 % soutenu 7 j) | Protocoles 07-09/11 | 21 mois | ○ | 2026-07-17 (moniteur dormant) |
| V6 | Gap weekend : cutoff vendredi 15h UTC suffit ; fade du gap dominical or NON rentable | Rejeu 06-02 + kill 07-12 | 20-30 mois | ○ | 2026-07-12 |

## Infra et méthode

| ID | Hypothèse | Preuve | Fenêtre | Poids | Tampon |
|---|---|---|---|---|---|
| I1 | Un trigger d'invariant (reconciled, MFE=0, BE non armé) n'est JAMAIS du régime — c'est un bug | Incident 05-07 | — | ❗❗ | méthodo gravée |
| I2 | Token refresh 12h ferme la fuite CH_ACCESS_TOKEN_INVALID | Patch 79007cf 05-20 | — | ○ | 🟡 à vérifier au prochain incident |
| I3 | Ratio live/théo Extension se lit sur sessions dédupées (30-35 % normal) — bougies brutes = drift fantôme | Analyse 06 | — | ○ | 2026-06 |
| I4 | La machine partagée (builds) ne contamine pas l'exécution (drop-in systemd) — contention à vérifier AVANT de suspecter le broker | Mesure 07-04 | — | ○ | 2026-07-04 |
| I5 | Le filtre « ça doit marcher en régime récent » est LA pièce qui évite les faux edges (3 edges 2024 morts en 2025-26) | Série de kills | 24 mois | ❗❗ | 2026-07-18 (contre-expertise à froid a réinventé ce correctif indépendamment — convergence) |
