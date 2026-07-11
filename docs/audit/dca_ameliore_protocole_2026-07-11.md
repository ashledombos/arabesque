# Protocole pré-enregistré — DCA amélioré sur majeures (P2bis poche DeFi)

**Date de gel : 2026-07-11 (nuit). Commité AVANT tout calcul de résultat** (méthode
Pas de Deux / MR-HL / momentum XS / funding cross-DEX). Toute variante ultérieure
(autres paliers, autres signaux, autres actifs, levier) = **nouveau protocole, un tir**.

**Nature** : politique d'investissement, PAS trading — hors filtre dur (pas de
seuil 3× coûts ni ≥ 2 trades/mois). La question amont — détenir du BTC/ETH tout
court — reste une **décision d'allocation opérateur** ; cette étude optimise
l'exécution d'un flux d'épargne déjà décidé, elle ne prend pas la décision.

**Question (3 volets)** :
- **Q1** — capital déjà disponible : lump-sum vs étalement (descriptif, pas de
  verdict — c'est un choix de risque).
- **Q2** — flux régulier : les DCA « améliorés » (pondération par Mayer/MM200 ou
  par paliers de drawdown) battent-ils le DCA fixe **une fois l'exposition moyenne
  normalisée** ? Suspicion gelée : le timing « acheter les creux » brille par
  survivance de BTC lui-même (chaque creux de l'échantillon a été racheté) — le
  gain apparent serait du simple sur-achat de bêta, pas du talent de timing.
- **Q3** — empilement DeFi : que rapporte cash-en-attente en lending + ETH accumulé
  staké, chiffré séparément du timing (amélioration attendue quasi certaine).

## Données gelées

- **Actifs** : BTC, ETH (majeures uniquement, mandat opérateur).
- **Source principale** : Binance klines 1d BTCUSDT + ETHUSDT depuis 2017-08-17
  (API publique read-only), close journalier. ~9 ans, 2 cycles complets
  (bear 2018, top 2021, bear 2022, bull 2024-25).
- **Extension diagnostic (non décisionnelle)** : BTC/USD journalier Bitstamp
  (via CCXT) depuis ~2011 si l'API le sert — ajoute le bear 2014-15. Si
  indisponible : noté et ignoré.

## Mécanique commune gelée

- **Injection** : 100 $/semaine (tous les 7 jours calendaires depuis le départ de
  la cohorte) dans un buffer cash. Montant arbitraire (tout est linéaire).
- **Exécution** : achat au close du jour d'injection, frais 10 bps par achat
  (swap spot DEX, uniforme sur toutes les variantes).
- **Variantes pondérées** : dépense du jour = injection × multiplicateur,
  **plafonnée au cash disponible** (jamais de découvert — les backtests publics
  « j'achète ×3 dans le creux » supposent un cash infini) ; le cash non dépensé
  s'accumule dans le buffer. Multiplicateur 0 = on accumule.
- **Q2 : le cash dort à 0 %** (isole le timing pur). Q3 : le même run avec cash
  rémunéré + staking, pour chiffrer l'empilement seul.

## Variantes gelées

| Code | Règle |
|---|---|
| **LS** | Lump-sum : budget total de la fenêtre investi au jour 1 (référence Q1) |
| **DCA-F** | Fixe : 100 % de l'injection dépensée chaque semaine (référence Q2) |
| **DCA-M** | Mayer M = P/MM200 : M < 0,8 → ×2 ; 0,8–1,2 → ×1 ; 1,2–2,4 → ×0,5 ; ≥ 2,4 → ×0 |
| **DCA-DD** | Drawdown vs ATH courant : DD < 10 % → ×0,5 ; 10–20 % → ×1 ; 20–40 % → ×1,5 ; 40–60 % → ×2 ; ≥ 60 % → ×3 |

MM200 et ATH calculés sur l'historique disponible au jour J (anti-lookahead ;
MM200 exige ≥ 200 jours d'historique — les cohortes Binance démarrent donc au
plus tôt 2018-03).

## Cohortes gelées

- **Départ** : le 1er de chaque mois (premier jour coté ≥ le 1er), de 2018-03 à
  (fin de données − horizon).
- **Horizon principal : 3 ans** (156 injections). Sensibilité : 2 ans et 4 ans.
- Cohortes chevauchantes = autocorrélées (assumé, rapporté — pas de test de
  significativité prétendu, on lit des distributions).
- **Sous-ensemble stress gelé** : cohortes démarrant 2021-01 → 2021-11 (achat
  dans les 6 mois précédant le sommet de cycle, les deux actifs). Cas nommé
  rapporté individuellement : **ETH cohorte nov-2021** (ATH 4 878 $, sous l'eau
  ~4 ans = le cas réel « l'actif stagne »).

## Métriques gelées (par cohorte × variante, cash à 0 %)

- **MOIC** = valeur finale / total injecté.
- **PU moyen payé** ($ dépensés / coins acquis) et ratio vs DCA-F.
- **expo_moy** = moyenne journalière de la valeur mark-to-market de la poche crypto.
- **R_norm = (valeur finale − total injecté) / expo_moy** — **la stat qui tue** :
  un excédent obtenu en détenant simplement plus de bêta plus tôt donne un R_norm
  ≈ celui du DCA-F ; seul du vrai talent de timing l'élève.
- maxDD de la valeur totale (crypto + cash) ; % de jours sous l'eau
  (valeur < injecté cumulé).

## Critères de verdict gelés

- **Q2 — une variante pondérée est déclarée AMÉLIORANTE ssi les 4 conditions
  tiennent, sur BTC ET ETH séparément** (anti-cherry-pick) :
  - (a) médiane des cohortes : R_norm(variante) > R_norm(DCA-F) ;
  - (b) ≥ 60 % des cohortes ;
  - (c) vrai aussi sur le sous-ensemble stress 2021 ;
  - (d) robustesse : reste vrai avec tous les seuils de paliers décalés ×0,75
    puis ×1,25 (2 re-runs).
  Sinon → **verdict : timing non prouvé, DCA fixe** (pas de demi-mesure, pas de
  « ça marche sur BTC seulement »).
- **Q1** : lump-sum vs DCA-F rapporté en médiane / p10 / p90 des cohortes MOIC —
  descriptif, la décision (risque de séquence vs espérance) revient à l'opérateur.
- **Q3** : gain d'empilement chiffré avec lending USDC **5 %/an** sur le buffer
  cash (sensibilité 4 / 6 %) + staking **3,0 %/an** sur les coins ETH accumulés
  (sensibilité 2,6 / 3,3) ; BTC : pas de rendement natif (wrap/lending BTC =
  risque supplémentaire, hors périmètre). Retenu si net positif (attendu
  trivialement oui) ; les risques (smart contract, dépeg, custody) sont nommés
  au verdict, pas chiffrés. Benchmarks corrigés 07-11 : sUSDe réel 3,7 %/an,
  barre passive = lending USDC ~5-6 %.

## Biais assumés (pessimisme)

- **Survivance** : BTC/ETH ont survécu et chaque creux de l'échantillon a été
  racheté — c'est exactement pourquoi (c) stress et R_norm existent. Une variante
  qui ne passe que grâce aux cohortes post-creux est un mirage.
- Frais uniformes 10 bps : neutres pour le comparatif, comptés quand même.
- Pas de fiscalité, pas de slippage différencié (ordres minuscules sur majeures).

## Livrables

- Script `tmp/dca_ameliore_study.py` (fetch + simulation + tableau).
- Verdict annexé à ce document + `docs/EXPERIMENT_LOG.md` + HANDOFF.
