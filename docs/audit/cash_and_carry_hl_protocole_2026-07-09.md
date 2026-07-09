# Cash-and-carry funding Hyperliquid : protocole d'étude pré-enregistré

**Date de gel : 2026-07-09, AVANT tout calcul de résultat.**
Candidat DEX restant après les kills Extension/Glissade/Pas-de-Deux (07-09).
Edge **structurel** (les longs paient les shorts : +9,3 %/an de notionnel
mesuré sur 21 mois réels) — pas statistique, donc pas de risque hyperopt ;
la question n'est pas « l'edge existe-t-il » mais « que reste-t-il sur le
CAPITAL après structure de marge, frais et épisodes de funding négatif,
et le risque opérationnel est-il borné ».

## Structure évaluée

Delta-neutre 1:1 : long 1 unité d'actif (spot, hors HL) + short 1 unité perp
HL. Capital immobilisé = 1 (jambe spot) + m (marge du short).
Deux niveaux de marge évalués : **m = 0,5** (levier 2× jambe short) et
**m = 1,0** (levier 1×).

## Simulation (sur funding réel en cache, 2024-10 → 2026-07, 14 instruments)

Deux variantes FIGÉES (aucune autre ne sera testée sur ce dossier) :
- **(a) always-on** : short permanent, funding encaissé chaque heure ;
- **(b) filtre simple** : actif seulement si la moyenne glissante 7 j du
  funding annualisé > 5 %/an (sinon flat) ; ré-entrée payée à chaque
  réactivation.

Coûts : perp taker 4,5 bps/côté ; spot 10 bps/côté (pessimiste) →
aller-retour complet ≈ 29 bps de notionnel, amorti sur la durée de tenue
effective ; variante (b) paie l'aller-retour à chaque cycle on/off.

Métriques par instrument : rendement annualisé net **sur capital** (aux deux
marges), pire fenêtre glissante 30 j du flux de funding, % d'heures
négatives, plus longue séquence négative.

Stress de liquidation : pire hausse sur 7 j des 21 mois (closes 4h Binance)
par instrument, comparée à la marge m (rebalancement supposé hebdomadaire ;
liquidation approximée à un runup de 0,8×m — pessimiste).

## Critères de verdict (écrits avant les résultats)

Benchmark : ~4-5 %/an sans risque de venue (USDC lending majeur). PASS si :
1. **≥ 3 instruments** avec rendement net sur capital **≥ 8 %/an** à m = 0,5
   (prime ≥ 3 points au-dessus du benchmark, pour payer le risque venue) ;
2. pire fenêtre 30 j du funding > **-0,5 %** de notionnel (épisodes négatifs
   bornés) sur ces instruments ;
3. stress : la pire hausse 7 j historique < 0,8×m (pas de liquidation avec
   rebalancement hebdo) sur ces instruments.

PASS → dossier opérationnel (venue de la jambe spot, wallet, sizing poche,
procédure de rebalancement) puis décision opérateur. Sinon KILL ou PARK
(consigné EXPERIMENT_LOG).
