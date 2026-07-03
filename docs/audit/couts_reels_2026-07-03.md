# P1 — Coûts réels par broker × marché (2026-07-03)

**Caveat d'échantillon** : n=25 exits avec champs coûts (`net_pnl_cash`, depuis 2026-06-07)
sur un seuil spec de 30. Produit quand même : la concentration du 07-03 (live =
Glissade-XAUUSD seul) rend n≥30 inatteignable avant des mois, et la revue était datée
2026-07-04. Compléter opportunistement quand les données arrivent.

## 1. Table de référence des coûts (entrée n°1 du filtre dur)

| Marché × venue | Coût explicite (comm+swap) | Spread (médian à l'entrée) | Total indicatif | Source |
|---|---|---|---|---|
| **Métaux GFT** | 0.000R (pricing spread-only) | **0.015R** | **~0.015R + swap ?** | 5 exits + 12 quotes |
| **Forex FTMO** | 0.032R | 0.049R (n=9, XAU-heavy) | ~0.05-0.08R | 4 exits |
| **Forex GFT** | 0.000R | 0.015R | ~0.015R + swap ? | quotes |
| **Crypto liquide FTMO** (BTC…) | 0.031R | 0.014R | ~0.045R | 3 exits |
| **Crypto illiquide FTMO** (GRT…) | 0.06-0.24R | variable | **jusqu'à 0.24R+** | 7 exits |
| **Crypto GFT** | 0.000R | **0.068R** | ~0.068R+ | 6 exits + quotes |

**Trou restant** : le **swap GFT** n'est pas journalisé (0.000 partout, suspect pour des
holds multi-nuits — pricing spread-only ne dispense pas du swap). À capter côté
TradeLocker avant de valider un hold multi-jours (bloquant pour Pas de Deux).

## 2. Réconciliation avec l'Étape 3 du banc d'essai (~0.10-0.12R crypto)

Le chiffre Étape 3 **confondait liquide et illiquide** : BTCUSD réel = 0.031R de
commission (+0.014R spread) ≈ **0.045R**, tandis que GRTUSD = 0.167R commission
+ 0.071R swap ≈ **0.24R**. Les deux sont vrais ; la moyenne était dominée par les
outliers illiquides (déjà purgés par la restriction d'univers du 06-24, puis par la
mort du crypto). **Conséquence** : le verdict « crypto tuée » reste valide mais par
l'edge (brut ≈ +0.05R < coût ~0.05-0.07R, marge nulle), pas par un coût 0.12R uniforme.

## 3. Découverte : distorsion de sizing (min-lot) — lecture des résultats cash

`pnl_cash_gap` (= P&L réel broker − théorique) montre : FTMO ≈ cohérent
(gap ±0.08-0.16R), **GFT gap +0.43R/trade (crypto) et +0.73R/trade (forex/métaux)** —
sur des micro-trades, le min-lot force une position réelle plus grosse que le risque
visé : gains ET pertes réels amplifiés ~1,4-1,7×. Implications :
- Les résultats **cash** GFT ne sont pas comparables à la théorie sans normalisation
  par le risque réel exécuté (`risk_integrity_check` existant) ;
- La fenêtre FTMO -7→-8 (sizing ~11$/trade) subira la même distorsion ;
- À la montée en risque (rodage ×0.5, ×1.0), la distorsion se résorbe mécaniquement
  (volumes plus gros → arrondi relatif plus petit).

## 4. Recommandations

1. **Venue métaux = GFT** (0.015R vs ~0.05R FTMO) — déjà acté (Décision 2026-07-03 ter).
2. **Capter le swap GFT** (TradeLocker API) — prérequis Pas de Deux et tout hold
   multi-nuits. C'est l'action n°1 restante du dossier coûts.
3. **Aucun instrument crypto à réactiver** sur aucune venue (edge < coût partout).
4. **Ne pas juger l'edge sur le cash GFT micro** — utiliser `result_r` (théorique,
   quantifié) pour l'edge et le cash pour les coûts, jamais l'inverse.
5. Méthodo : les Δ-paires FTMO/GFT sur `result_r`/`pnl_cash` sont **aveugles par
   construction** (sorties quantifiées → Δ=0.000 sur 45 paires) — seul `net_pnl_cash`
   porte le réel.
