# Protocole pré-enregistré — Arbitrage de funding cross-DEX (P1 poche DeFi)

**Date de gel : 2026-07-11 (soir). Commité AVANT tout calcul de résultat** (méthode
Pas de Deux / MR-HL / momentum XS). Toute variante ultérieure (levier, seuils,
univers élargi, venue ajoutée) = **nouveau protocole, un tir**.

**Question** : l'écart de funding entre venues perps DEX (long venue au funding
bas / short venue au funding haut, delta-neutre) paie-t-il, net de coûts
pessimistes, assez pour battre le benchmark passif (Ethena sUSDe ~8 %/an) avec
une marge qui rémunère le risque de venue ×2 et le travail d'exécution ?

## Périmètre gelé

- **Symboles** : BTC, ETH, SOL (majors présents sur les 4 venues ; stress pump le
  plus doux — rappel carry PARK : les alts pumpent +51-146 % en 7 j).
- **Venues** : Hyperliquid (cache 21 mois), Lighter (mainnet, horaire), Pacifica
  (horaire, profondeur à découvrir au backfill), Aster (8 h, depuis 2025-08).
  EdgeX exclu (non sondé). 6 paires de venues possibles par symbole.
- **Fenêtre de verdict** : 2026-01-11 → 2026-07-11 (6 mois, régime récent).
  Historique antérieur = diagnostic seulement.
- **Grille** : UTC horaire ; Aster (fraction/8 h) converti en équivalent horaire
  (rate/8 sur la fenêtre de 8 h suivant le paiement) ; Lighter `rate` = %/h signé
  par `direction` ; Pacifica/HL = fraction/h. Validation d'unités bloquante :
  les 4 venues doivent donner des niveaux comparables sur les mêmes heures
  (médiane |APR| par venue dans un rapport ≤ 5× entre venues, sinon STOP et
  ré-instruction des unités avant toute analyse).

## Simulation gelée (par symbole × paire de venues)

- **Signal** : spread(t) = funding_A(t) − funding_B(t), lissé moyenne glissante
  24 h, annualisé (APR sur notionnel). Une position à la fois.
- **Entrée** : |spread lissé| > **15 % APR** → long venue basse / short venue
  haute, exécution taker 2 jambes à **t+1 h** (anti-lookahead).
- **Sortie** : |spread lissé| < **5 % APR** (ou fin de données) → fermeture taker
  2 jambes à t+1 h.
- **Accrual** : Σ du spread horaire **réalisé** pendant la détention (jamais le
  spread d'entrée).
- **Coûts par aller-retour** (gelés, pessimistes ; taker par côté : HL 4,5 bps
  mesuré / Pacifica 2,5 (base 2,0 documentée) / Aster 4,0 (base 3,5 documentée) /
  Lighter 0 (documenté)) : (taker_A + taker_B) × 2 + slippage 1 bp × 4 côtés +
  2 bps de risque de basis à la fermeture (divergence des marks).
- **Capital** = 2 × notionnel (levier 1, isolé, jamais plus). Net APR sur capital
  calculé **sur toute la fenêtre, périodes flat incluses** (le capital est engagé
  en permanence — pas d'APR gonflé par annualisation des seuls épisodes investis).
- **Sensibilité rapportée, non décisionnelle** : seuils d'entrée 10 et 20 % APR.

## Statistiques pré-enregistrées

1. **Net APR portefeuille** : équipondéré sur tous les (symbole × paire) ayant
   ≥ 1 position dans la fenêtre de verdict — pas de sélection de la meilleure
   paire (anti-cherry-pick).
2. **Persistance** (la stat qui tue — référence académique : 40 % seulement des
   meilleures opportunités restent positives après coûts) : conditionnel à un
   franchissement du seuil d'entrée, % d'épisodes dont le P&L net (accrual réalisé
   − coûts d'aller-retour) est positif. 
3. **Utilisation** : % d'heures investies par paire.
4. **Stress pump/dump** : pire variation 7 j de chaque symbole (Binance 20 mois en
   cache) vs seuil de liquidation à levier 1 isolé (≈ ±90 %) — dépassement = paire
   invalide quelle que soit sa rentabilité (leçon carry PARK : « rentable ≠ sûr »).

## Verdict (seuils gelés)

- **PASS** : net APR portefeuille ≥ **12 %/an** (≥ 1,5× benchmark passif sUSDe)
  ET persistance ≥ 50 % ET stress pump survivable. Conséquence d'un PASS : PAS de
  capital — étape suivante = 4 semaines de collecte forward de confirmation
  (l'edge vit des saisons de points, non-stationnarité présumée), puis décision
  opérateur sur le contrat de risque de la poche.
- **PARK** : net APR 8-12 % OU persistance 40-50 % — dossier gelé, porte de
  réouverture à nommer au verdict.
- **KILL** : net APR < 8 % (battu par le produit passif sans risque d'exécution)
  OU persistance < 40 % OU stress pump fatal sur les 3 symboles.

## Limites pré-connues

Fenêtres courtes (Aster ~11 mois, Pacifica inconnue) ; l'edge est probablement
dopé par les programmes de points des jeunes venues → même un PASS ne prouve
pas la pérennité, d'où la collecte forward obligatoire avant capital. Aucun
wallet, aucun connecteur, 0 € dans cette étude.

---

## VERDICT (2026-07-11 soir, après exécution — protocole ci-dessus inchangé) : **KILL**

- Net APR portefeuille (seuil verdict 15 %) : **-1,23 %/an** ; sensibilité
  10 % : -3,55 % ; 20 % : -0,64 %. Persistance : **5 %** d'épisodes nets
  positifs (5/111). Stress pump : survivable (seul critère passant).
- Mécanisme : spread 15,5 % APR à l'entrée → **7,4 % réalisé** en détention
  (non-persistance) ; brut +553 bps vs coûts 1 917 bps (3,5×).
- Le spread statique structurel HL↔Aster (~5-6 % APR notionnel) ≈ 2,5-3 % sur
  capital à levier 1 < benchmark passif sUSDe ~8 % → pas de variante passive
  à instruire.
- Détail complet : `docs/EXPERIMENT_LOG.md` § 2026-07-11. Pacifica : profondeur
  API réelle = 6 semaines (limite découverte au backfill, documentée).
- Toute variante (alts, maker-only, levier, EdgeX) = nouveau protocole, un tir.
