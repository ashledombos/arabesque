# Drift pré-annonces macro (FOMC/NFP/CPI) : protocole d'étude pré-enregistré

**Date de gel : 2026-07-10, AVANT tout calcul de résultat.**
Candidat n°2 du sourcing Phase C 07-07 (littérature Lucca-Moench, pre-FOMC
drift sur indices US). Prérequis livré : `config/macro_calendar.csv`
(94 événements 2024-2026, heures UTC exactes, sources Fed + BLS effectives —
le trou du shutdown oct. 2025 y figure).

## Hypothèse et mécanisme (figés)

**Primaire** : les fenêtres pré-annonce portent un drift positif sur US500
(long only, l'anomalie documentée). **Fenêtre figée : [T−25h, T−1h]** où T =
heure UTC de l'annonce — entrée à l'open de la première barre H1 ≥ T−25h,
sortie au close de la dernière barre H1 se terminant ≤ T−1h (marge compliance
FTMO ±2 min largement couverte ; sortie ~1h avant l'annonce = jamais exposé
au spread d'événement).

**Le candidat = portefeuille des 3 événements** (FOMC + NFP + CPI, ~2,7
fenêtres/mois — le débit vient de la famille, pas d'un événement seul).

**Secondaire (informatif, sans verdict)** : même fenêtre sur XAUUSD (venue
GFT) ; ventilation par type d'événement.

## Données et coûts

- H1 Dukascopy en cache : `USA500IDXUSD` (proxy US500.cash FTMO), `XAUUSD`.
- Fenêtre d'étude : 2024-01 → 2026-07 (≈ 20 FOMC + 29 NFP + 29 CPI dans les
  données).
- **Coût par fenêtre US500 FTMO (mesuré)** : spread 0,56 bps médian (sonde
  07-07) × 2 ≈ 1,1 bps + commission ~1-2 bps + **swap long -2,16 bps/nuit**
  (sonde indices 07-07, 1 nuit traversée) → **coût total figé ≈ 5 bps par
  fenêtre** (pessimiste).

## Critères de verdict (écrits avant les résultats)

Sur le PORTEFEUILLE US500 (3 événements confondus, n≈78) :
1. rendement moyen par fenêtre > 0 avec **t ≥ 2** ;
2. rendement moyen ≥ **3× coût** (≥ 15 bps/fenêtre) ;
3. positif sur **≥ 4 des 5 semestres** (2024-S1 → 2026-S1) ;
4. débit ✓ par construction (~2,7/mois).

PASS = tous → étape suivante : design de trade complet (sizing, SL, gestion
du gap d'entrée) + dossier compliance par phase de compte (challenge libre ;
financé standard : sortie >2 min OK par construction ; Swing : exempté).
KILL sinon. Ventilations par événement = lecture, PAS de re-découpage du
verdict (pas de « ça marche si on ne garde que CPI » — variante = nouveau
protocole, un tir).
