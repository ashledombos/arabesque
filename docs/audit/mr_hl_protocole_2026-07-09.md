# Mean-reversion aux coûts Hyperliquid : protocole d'étude pré-enregistré

**Date de gel : 2026-07-09, AVANT tout calcul de résultat.**
Hypothèse opérateur 07-08 : la MR, abandonnée sur CFD (4 replays négatifs,
2026-02), pourrait vivre sur DEX où la structure de coûts diffère (pas de
swap broker ; le short REÇOIT le funding en moyenne). Dernier candidat de la
séquence DEX (Extension ✗, Glissade ✗, Pas de Deux ✗, carry ⏸).

## Mécanisme testé (paramètres HISTORIQUES figés tels quels — zéro retouche)

`git show 0c15991^:arabesque/backtest/signal_gen.py` (v3.3, 2026-02), copié
à l'identique : BB(20,2) excess H1 contrarien — LONG si close < BB lower et
RSI ≤ 35 (SHORT symétrique RSI ≥ 65), filtre régime HTF (pas de contre-trend
fort), min_bb_width 0.003, SL swing 10 barres −0,2 ATR (min 1,5 ATR et
0,3 % du prix), TP indicatif BB mid, min RR 0,5.
Adaptation unique sans impact trading : appel au signal_labeler retiré
(métadonnées de classification uniquement).

## Exécution (identique au banc étape 2 pour comparabilité)

- BacktestRunner moderne, PositionManager par défaut (BE 0,3R/offset 0,20R +
  trailing — le même moteur que toutes les études), risk 0,40 %,
  spread/slippage simulés par défaut, **sub-bar M1**.
- **SignalFilter DÉSACTIVÉ** : la matrice de filtres historique était une
  couche de calibration CFD (risque de sur-ajustement) — on juge le
  mécanisme brut.
- Données : H1 Binance 20 mois, 14 instruments liquides ∩ HL.
- Coûts par trade : taker 4,5 bps × 2 / stop_pct + **funding réel horaire**
  signé sur [entry, exit] (cache 21 mois), fallback pessimiste
  (long paie la moyenne, short reçoit 0).

## Fenêtres (honnêteté de calibration)

Les paramètres v3.3 ont été calibrés sur des données ≤ 2026-02. Donc :
- **full** (20 mois) : inclut l'ère de calibration — indicatif ;
- **récent 12 m** (≥ 2025-07) : partiellement dans son échantillon ;
- **post-abandon** (≥ 2026-02-22) : OOS pur — la fenêtre qui compte le plus.

## Critères de verdict (écrits avant les résultats)

PASS si TOUS :
1. net > 0 sur full ;
2. net > 0 sur récent 12 m ;
3. net > 0 sur post-abandon (OOS pur) ;
4. brut ≥ 3× coût sur récent ;
5. débit ≥ 2 trades/mois ;
6. aucun instrument seul > 50 % du P&L net.

Sinon KILL. PASS → étape suivante = walk-forward standard + dossier complet
(jamais de retouche de paramètres sur ce dossier ; variante = nouveau
protocole, un tir).
