# Cartographie DeFi — sourcing élargi poche décentralisée (2026-07-11)

**Mandat opérateur 2026-07-11** : la poche DEX (actée 07-09 : même repo, contrat de
risque séparé, capital jamais mélangé) est élargie à **toute la DeFi** — perps DEX,
staking, lending, LP, rendement fixe, arbitrages. Contrainte unique : **pas de CEX**
(centralisé = autant faire ETF/actions ; la crypto ne se justifie que décentralisée,
hors prop firms). Méthode inchangée : protocole pré-enregistré, 0 € risqué avant
verdict, filtre dur (edge ≥ 3× coûts, régime récent).

**Source explorée** : github.com/djienne (auteur de la chaîne FreqtradeFR, 64 dépôts).
Profil crédible — travaux 2019-2020 sur Avellaneda-Stoikov et calibration d'intensités
de Poisson, études récentes méthodologiquement propres (audit de causalité, gates OOS,
discipline de tests multiples). Biais assumé : monétisation par liens de parrainage
vers les jeunes DEX (Lighter, Pacifica, Aster, EdgeX).

## Familles cartographiées

| # | Famille | Rendement indicatif (07-2026) | Risque dominant | Verdict préliminaire |
|---|---|---|---|---|
| 1 | **Arb funding cross-DEX** (long venue basse / short venue haute, delta-neutre) | écarts 10-20 % APR récurrents ; MAIS étude nov-2025 (26 venues) : 17 % des observations ≥ 20 bps et **seulement 40 % des meilleures opportunités positives après coûts** | venue jeune ×2 (smart contract, retraits), edge dopé par saisons de points → décroissant | **✗ KILL 07-11 soir au protocole gelé** (`funding_cross_dex_protocole_2026-07-11.md`) : net -1,23 %/an, persistance 5 %, spread réalisé = ½ du spread d'entrée ; même le spread statique HL↔Aster < benchmark sUSDe |
| 2 | **Cash-and-carry productisé** (Ethena sUSDe ; Pendle PT = version taux fixe) | sUSDe : 8-18 % historique, **haut de single-digit au T2-2026** (régime compressé — cohérent avec notre moniteur `hl_funding_regime` dormant) ; PT-sUSDe fixe 6,4-9 % | depeg USDe, custodian/exchange sur la jambe short (scénario FTX), régulation | **À INSTRUIRE — P2** (benchmark du carry PARK + porte « collatéral spot » item 4 file des GO) |
| 3 | **Lending stablecoin** (Aave v3/v4, Morpho, Spark) | 3,5-9 % variable, USDC mainnet ~5-6 % | protocole (le plus audité de la DeFi), taux variable | **Socle de trésorerie** de la poche, pas un edge — instruire seulement si la poche est capitalisée |
| 4 | **Staking ETH / LST** (Lido stETH) | 2,6-3,3 % net | prix ETH (dominant), slashing marginal | ✗ en tant que *rendement* (sous le lending stable avec risque prix en plus) ; pertinent uniquement si expo ETH voulue = décision d'investissement opérateur, pas de trading |
| 5 | **Vaults contrepartie** (HLP Hyperliquid, JLP Jupiter) | HLP ~15-30 % APR historique, CAGR ~20 % T1-2026 | **short-vol structurel** : cible d'attaques répétées (JELLY 03-25, POPCAT 11-25, Fartcoin 04-26) ; TVL -55 % en 9 mois = les informés sortent pendant que le P&L affiché monte | **✗** — anti-boussole (petits gains, queue grasse non bornée), et le signal TVL est éloquent |
| 6 | **Market making DEX 0 frais** (Lighter — vol + OBI, Avellaneda/GLFT) | edge réel possible (0 frais maker change l'équation) mais non publié | inventaire/adverse selection + **autre métier** : latence, websockets 24/7, Rust, AWS Tokyo | **PARK long terme** — charge opérationnelle incompatible Phase 4, à revisiter si la poche devient sérieuse |
| 7 | **XEMM maker-taker** (maker venue illiquide, hedge taker venue liquide) | marge paramétrée `profit_rate_bps` après frais | latence (hedge à retard), même charge op que #6 | **PARK** avec #6 |
| 8 | **Volume/points farming** (airdrops) | EV = airdrop non mesurable ; coût mesurable (~20-40 $ pour 100 k$ de volume HL) | perte certaine en $, gain spéculatif | Hors protocole (rien à mesurer) — ticket loisir à l'appréciation opérateur, pas Arabesque |
| 9 | **TA directionnelle portée sur DEX** (stratégies FreqtradeFR : TRIX, Ichimoku, BB_RPB…) | — | — | **✗ par classe** — famille exacte tuée par le filtre dur 07-09 (Extension/Glissade/MR : edge brut récent mort, la venue n'est pas le problème) |
| 10 | **Grid/DCA sans stop** (Passivbot et forks Lighter) | — | queue non bornée | **✗** — rejet opérateur déjà acté 07-09 (profils martingale/DCA) |
| 11 | **DCA amélioré sur majeures** (accumulation spot BTC/ETH sans levier — ajout opérateur 07-11 soir) | bêta de l'actif + empilement DeFi (~5-6 % sur le cash en attente, +2,6-3,3 % staking sur l'ETH accumulé) | prix de l'actif (borné au capital, pas de liquidation) — ≠ famille 10 : le rejet 07-09 visait le levier sans stop | **À INSTRUIRE — P2bis** (politique d'investissement, pas trading : hors filtre dur, décision d'allocation opérateur) |

Note transverse : le mining de facteurs de djienne (403 facteurs, 15 min, gate OOS)
confirme indépendamment notre constat — seul le **mean-reversion court terme** montre
un IC (~5 %, h=1-2) en crypto ; notre MR v3.3 est morte aux coûts avec stops serrés,
mais sa version *maker à 0 frais* (Lighter) rejoint la famille #6, pas un re-test de MR.

## Priorisation proposée

1. ~~**P1 — Mesurer l'écart de funding cross-DEX**~~ **EXÉCUTÉ ET KILL 07-11 soir**
   (protocole gelé `cb110d5`, verdict dans le protocole + EXPERIMENT_LOG §
   2026-07-11). La file DeFi continue à P2/P2bis.
   C'est la seule famille qui étend le carry PARK avec un *mécanisme différent* :
   l'edge vient de l'écart **entre venues** (distorsion d'incitations des jeunes DEX),
   pas du niveau absolu du funding qui a motivé le PARK. Questions de design à
   trancher au protocole : ~~historique paginable par venue~~ **sondé 07-11 soir,
   read-only : Lighter ✅ (`/api/v1/fundings`, horaire, champs rate+direction),
   Pacifica ✅ (`/api/v1/funding_rate/history`, paginable offset/limit, ≥ 1,5 mois
   vérifié, profondeur max à établir), Aster ✅ (API Binance-like
   `fapi/v1/fundingRate`, ~11 mois = depuis lancement), HL déjà en cache 21 mois
   → backfill historique faisable, P1 = verdict en 1 session, collecte forward
   seulement en appoint** ; univers (majors ∩ venues) ; seuil de
   viabilité net (coûts 2 jambes + slippage + **prime de risque venue** à expliciter) ;
   la stat qui tue : persistance de l'écart (l'étude académique dit que 60 % des
   meilleures opportunités s'évaporent après coûts).
2. **P2 — Instruire le carry collatéralisé** : (a) porte déjà nommée au PARK 07-09
   « collatéralisation spot du short même venue » (item 4 file des GO) ; (b) la
   version *productisée* (sUSDe/PT Pendle) comme **benchmark passif** : si Ethena
   livre ~8 % sans travail ni exécution en propre, tout carry maison doit battre
   ça net de risque venue pour justifier l'infrastructure.
3. **P2bis — DCA amélioré sur majeures** (1 session, parallèle possible de P2 —
   aucune infrastructure partagée). Protocole à pré-enregistrer : DCA fixe vs
   variantes pondérées (MM200/Mayer/paliers de drawdown) vs lump-sum, historique
   max, **cohortes par date de départ** (pas une seule courbe), stress « actif qui
   stagne » (ETH 2021-2025 = cas réel), et la stat qui tue : **normalisation par
   exposition moyenne** (séparer talent de timing et simple sur-achat de bêta —
   biais pessimiste : les variantes « creux » brillent par survivance de BTC
   lui-même). Chiffrer séparément l'empilement structurel DeFi (cash en lending +
   ETH staké), amélioration quasi certaine vs timing suspect. La question amont
   (détenir du BTC/ETH tout court) reste une décision d'allocation opérateur —
   l'étude optimise l'exécution, elle ne prend pas la décision.
4. **P3 — Socle de trésorerie** (lending stablecoin / PT fixe) : seulement quand la
   poche a un capital et un contrat de risque signés.

**Principe opérateur gravé 07-11 (soir)** : tout ce qui est étudiable dans le
périmètre DeFi mérite étude — aucune fermeture a priori — mais **aucun pari
aveugle** : chaque pari (y compris « détenir du BTC », y compris « un ETF continue
sur sa lancée ») est explicitement nommé, hiérarchisé par risque, et passe par un
protocole pré-enregistré quand il est mesurable.

## Ce qui ne change pas

- Aucun wallet, aucun connecteur, 0 € risqué — inchangé tant qu'aucun protocole n'a
  produit un verdict positif ET que l'opérateur n'a pas signé le contrat de risque
  de la poche (compte + guards dédiés).
- Phase 4 prop firm reste le fil principal ; ce chantier vit dans les items 4-5 de
  la file des GO (recherche documentaire / sourcing), une session à la fois.
