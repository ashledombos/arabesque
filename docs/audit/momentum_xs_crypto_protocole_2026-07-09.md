# Momentum cross-sectionnel crypto long-short : protocole pré-enregistré

**Date de gel : 2026-07-09, AVANT tout calcul de résultat.**
Famille neuve (jamais instruite dans le projet), n°1 de la roadmap R&D post-DEX
(plan approuvé opérateur). Mécanisme neutre marché : long les plus forts /
short les plus faibles — répond à la préférence opérateur pour les mécanismes
indépendants de la direction. Littérature : momentum cross-sectionnel crypto
documenté (Liu-Tsyvinski-Wu et suiv.), horizon 1-4 semaines.

## Mécanisme (figé)

- Univers : les 14 crypto liquides ∩ listings Hyperliquid (mêmes que les
  études DEX : BTC ETH SOL BNB XRP DOGE ADA DOT AVAX LINK LTC UNI NEAR AAVE).
- **Rebalancement hebdomadaire** (toutes les 42 barres 4h) : classement par
  rendement passé sur le lookback → **long equal-weight top-3 (+1/3 chacun),
  short equal-weight bottom-3 (−1/3 chacun)**. Dollar-neutre (1 jambe = 1).
- **Deux variantes déclarées, les SEULES qui seront calculées** :
  lookback **7 j** (42 barres) et lookback **30 j** (180 barres).
  Aucune autre valeur ne sera testée sur ce dossier (variante future =
  nouveau protocole, un tir).

## Coûts (mesurés étapes 1-2)

- Fees taker 4,5 bps × turnover à chaque rebalancement
  (turnover = Σ|Δpoids|, entrée initiale comprise, sortie finale comprise) ;
- **Funding réel horaire** par position signé (long paie, short reçoit),
  depuis le cache 21 mois ; heures non couvertes → fallback pessimiste
  (long paie la moyenne +8,5e-6/h, short reçoit 0).
- Comptabilité : rendements en fraction du notionnel d'UNE jambe.

## Données

Closes 4h Binance (proxy validé étape 1), fenêtre complète du cache (~20 mois),
warm-up 30 j. Fenêtres rapportées : full et récent 12 m (≥ 2025-07-01).

## Critères de verdict (écrits avant les résultats)

PASS si, pour **au moins une** des deux variantes déclarées, TOUS :
1. rendement net annualisé > 0 sur full ;
2. rendement net annualisé > 0 sur récent 12 m ;
3. brut ≥ **3× coût total** (fees + funding net) sur récent ;
4. **Sharpe net annualisé ≥ 1,0** sur full (hebdo √52) ;
5. aucun instrument ne contribue > 50 % du P&L net full
   (somme des contributions w_i×r_i par instrument).

Sinon KILL. PASS → candidat poche DEX : étape suivante = dossier complet
(walk-forward, Monte Carlo ruine, contrat de risque poche) sur décision
opérateur. Rien ne s'active en live.
