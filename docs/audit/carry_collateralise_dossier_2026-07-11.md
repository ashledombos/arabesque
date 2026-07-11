# Carry collatéralisé (P2 poche DeFi) — dossier documentaire 2026-07-11

**Mandat** : item 4 de la file des GO (« Collatéral spot Hyperliquid, recherche
documentaire, 1 session ») élargi 07-11 en P2 de la cartographie DeFi
(`defi_paysage_2026-07-11.md`) : (a) instruire la porte de réouverture n°2 du
carry PARK 07-09 — « collatéralisation spot du short sur la même venue » ;
(b) établir le benchmark passif (sUSDe / Pendle PT) que tout carry maison doit
battre. **Recherche documentaire + chiffrage sur données déjà en cache ou
read-only : 0 € risqué, pas de wallet, rien lancé.**

## 1. La porte opérationnelle est OUVERTE : portfolio margin Hyperliquid

Le « portfolio margin » HL (live depuis ~début 2026, **beta en expansion**,
limites relevées en mars 2026) est exactement le mécanisme demandé par la
clause de réouverture n°2 du PARK :

- **Compte unifié spot + perps + emprunt** : « a portfolio margin account's
  spot borrow and perps pnl offset each other for accounting ». La doc
  officielle donne le carry (1 BTC spot + short 1 BTC-perp 10x) comme
  **exemple canonique**.
- **Collatéral éligible : BTC, HYPE (LTV 0,5), USDC, USDT** — et EUX SEULS.
  Les 5 instruments du dossier PARK (AAVE 9,8 %, LINK 8,9 %…) ne sont PAS
  collatéralisables → l'univers exécutable du carry auto-collatéralisé se
  réduit à **BTC et HYPE**.
- **Économie** : capital = 1× la jambe spot (plus de marge morte m) ; la marge
  USDC du short est **empruntée automatiquement** contre le spot ; intérêt payé
  sur la marge seule (`0.05 + 4.75·max(0, util−0.8)` APY, base 5 %) ; funding
  encaissé sur le notionnel plein ; le spot oisif rapporte le taux prêteur.
- **Le pump ne liquide plus le short** (le spot le couvre) mais un nouveau
  plafond apparaît : la **liquidation borrow-lend**. Doc : à +100 % il faut
  réduire le notionnel. Calcul (borrow = marge 1/levier + perte du short X,
  capacité = LTV 0,5 × valeur spot (1+X)) : plafond ≈ **+80 % en une fenêtre de
  rebalancement à 10x** — et il se RESSERRE à levier plus bas (3x → ~+34 %,
  plus de marge empruntée dès le départ). L'inversion est contre-intuitive :
  ici le levier haut est le plus sûr.
- **Éligibilité** : valeur de compte **> 10 k$** (ou > 5 M$ de volume), < 25 M$ ;
  caps globaux/user (BTC 2 000 global / 200 user) ; si caps atteints, retour au
  comportement non-portfolio. Liquidation : ratio > 0,95, **ordre spot/perps
  non déterministe** (doc explicite).
- Jambe spot BTC sur HL = **UBTC via Unit** (TSS de guardians indépendants) —
  couche de custody supplémentaire vs natif. HYPE spot est natif HyperCore.

## 2. Économie chiffrée (funding réel HL, cache 21 mois + fetch read-only 07-11)

Convention : always-on, frais A/R 29 bps amortis sur 1 an (protocole 07-09),
marge 10 % (10x) empruntée à 5 %/an. Net = funding APR − 0,5 pt − 0,3 pt.

| Jambe | Funding plein | 12 m | 6 m | 3 m | **Net ~courant (6 m)** | Pire pump 7 j | % h nég / pire 30 j |
|---|---|---|---|---|---|---|---|
| **BTC** (21 mois) | +10,4 % | +7,0 % | +3,1 % | +4,0 % | **≈ +2,3 %/an** | +30,8 % (sûr) | 13,6 % / −0,085 % |
| **HYPE** (19 mois, depuis listing 12-2024) | +22,1 %* | +12,0 % | +8,6 % | +9,3 % | **≈ +7,8 %/an** | **+121 %** (14 fenêtres > +80 %) | 6,3 % / **+0,277 %** |

\* plein gonflé par l'euphorie du listing (déc-2024). Données :
`tmp/hl_funding_BTC.parquet` (cache P1) + `tmp/hl_funding_HYPE.parquet`
(fetch API info 07-11) ; pumps sur closes 4h HL (candleSnapshot).

## 3. Benchmark passif : CORRECTION du chiffre cartographié

L'API officielle Ethena (08-07-2026) donne **sUSDe ≈ 3,7 %/an courant**
(30 j : 3,70 / 90 j : 3,72 / depuis l'origine : 10,97) — pas les ~8 % «
haut de single-digit » repris dans la cartographie 07-11 (sources
secondaires datées). Cohérent : sUSDe *est* le carry BTC/ETH institutionnel,
il vit le même régime compressé que notre moniteur `dormant`. PT-sUSDe fixe
≈ 4 % (snapshot Pendle). **La vraie barre passive aujourd'hui = lending
USDC majeur ~5-6 %** (Aave v3 mainnet), pas sUSDe.

## 4. Verdict : PARK MAINTENU — mais la condition de réouverture se simplifie

- **BTC auto-collatéralisé : mort au régime courant.** Net ≈ +2,3 %/an, sous
  le lending USDC (5-6 %) et sous sUSDe historique — pour du risque venue +
  custody Unit + beta en plus. Opérationnellement sûr (pire pump 7 j +30,8 %
  ≪ plafond +80 %), mais rien à payer.
- **HYPE auto-collatéralisé : ne paie pas sa prime de risque.** Net ≈ +7,8 %
  ≈ +2-3 pts au-dessus du lending — pour un empilement de risques maximal :
  **collatéral = le token de la venue elle-même** (motif FTT/FTX : le
  delta-neutre ne protège pas d'une mort de venue qui emporte collatéral ET
  custody en même temps), pumps 7 j > +80 % observés 14× (rebalancement
  QUOTIDIEN requis, pas hebdo), portfolio margin en beta, liquidation non
  déterministe. Le critère gelé du protocole 07-09 (≥ 3 instruments ≥ 8 %
  net, prime ≥ 3 pts) n'est pas atteint : 1 seul instrument, à 7,8 %.
- **Ce que la session change** : la clause de réouverture n°2 (opérationnelle)
  est **LEVÉE** — la structure existe, elle est documentée ici, prête à
  exécuter. La réouverture du carry ne dépend plus QUE de la clause n°1 :
  **régime de funding** (moniteur `hl_funding_regime`, inchangé dans ses
  seuils : ≥ 3 instruments > 20 %/an soutenu ≥ 7 j).
- **Action prise** : HYPE ajouté aux instruments du moniteur (couverture —
  c'est désormais la jambe la plus exécutable ; seuils de réveil INCHANGÉS).

## 5. Playbook pré-écrit pour le réveil (à protocoler alors, pas maintenant)

Si `wake_confirmed` : nouveau protocole conditionnel un-tir, structure =
portfolio margin HL, jambe BTC (sûre, custody Unit) et/ou HYPE (rendement,
rebalancement quotidien obligatoire, risque venue²), 10x sur la marge
empruntée (plafond pump le plus haut), compte ≥ 10 k$, always-on avec
sortie si funding 7 j < seuil. À comparer ALORS au sUSDe du moment (le
benchmark bouge avec le même régime — en euphorie il montera aussi, l'écart
à battre est la prime d'exécution en propre, ~3 pts minimum).

## Sources

- [Hyperliquid docs — Portfolio margin](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/portfolio-margin)
- [CoinDesk 2026-03-10 — upgrade portfolio margin](https://www.coindesk.com/markets/2026/03/10/hyperliquid-s-new-upgrade-to-let-traders-take-bigger-bets-with-less-capital)
- [Bitget News — Portfolio Margin beta expands](https://www.bitget.com/amp/news/detail/12560605476967)
- [Unit docs — custody TSS guardians](https://docs.hyperunit.xyz/)
- [API Ethena — yields officiels](https://ethena.fi/api/yields/protocol-and-staking-yield) (3,75 % courant au 08-07-2026)
- [Pendle app — marchés PT](https://app.pendle.finance/)
