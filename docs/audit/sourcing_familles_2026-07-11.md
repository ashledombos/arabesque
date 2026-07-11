# Sourcing de nouvelles familles — cycle 2 (item 5 file des GO)

Date : 2026-07-11 · Go opérateur (file des GO, « go » nu)
Méthode : identique au cycle 07-07 — familles documentées dans la littérature
UNIQUEMENT → confrontation dossier (coûts mesurés par venue, débit ≥ 2/mois,
décorrélation avec Glissade-or live / Renversé ombre / Adage ombre, profil prop
firm) → pré-écrémage sur nos données (Dukascopy + Binance 2024-01 → 2026-07-10,
30 mois) quand trivial. **Hypothèses figées avant calcul** (fenêtres imposées par
la littérature, zéro tuning, un seul passage) — en-tête du script
`tmp/sourcing_ecremage_2026-07-11.py`, sortie brute
`tmp/sourcing_ecremage_2026-07-11_results.txt`.

Seuils de verdict pré-écrits (identiques 07-07) : INTÉRÊT = |t| ≥ 2 sur 30 mois
ET signe stable ≥ 4/5 sous-périodes ET brut ≥ 3× coût venue ; RÉSERVE = 2/3
critères ; KILL sinon.

## Résultat du cycle : 0 candidat — 5 kills chiffrés

| # | Famille (littérature) | Chiffres (30 mois) | Verdict |
|---|---|---|---|
| C1 | **Turn-of-month US500** (McConnell-Xu 2008, fenêtre J-1..J+3) | fenêtre **+1,5 bps/j vs hors +8,3** (Δ −6,8, t_Welch −0,74) ; par événement +6,1 bps vs coût ~9 (4 nuits financement mesuré −2,16 + spread) → ratio 0,7× ; 2/5 sous-périodes | ❌ KILL — l'effet canonique est ABSENT (voire inversé) de nos 30 mois, cohérent avec le double kill ToM-or |
| C2 | **Fixing PM or 15:00 Londres** (Caminschi-Heaney 2014 : short pré-fix / long post-fix) | pré-fix −0,74 bps (t=−0,44, 3/5) ; post-fix +0,85 bps (t=+0,76, 2/5) ; seuil d'intérêt 4,5 bps (3× spread diurne 1,5) | ❌ KILL — signes conformes à la littérature mais amplitude ~6× sous le seuil, aucune significativité |
| C3 | **Time-of-day FX** (Breedon-Ranaldo : la monnaie se déprécie pendant ses heures locales) — USDJPY/EURUSD/GBPUSD/AUDUSD, signé sens littérature | −0,73 à +0,43 bps/j, t ∈ [−0,87 ; +0,53], au mieux 3/5 sous-périodes, aucun ratio ≥ 3× (meilleur : AUDUSD 1,1×) | ❌ KILL — l'anomalie (données ≤ 2013) ne survit pas en régime 2024-26, même brute |
| C4 | **Session-hold nocturne Brent** (analogue Adage, ≤ 21:00 UTC → ≥ 08:00 UTC J+1, week-ends exclus) | **−5,8 bps/session** (t=−1,23, 1/5 sous-périodes positives, n=524) | ❌ KILL — le drift nocturne est un fait OR (Adage), pas commodities ; négatif avant tout coût |
| C5 | **Session US crypto BTC** (ère ETF, 13:30-21:00 UTC) | heures US +0,64 bps/j (t=+0,11) ; hors US +4,29 (t=+0,71) ; 3/5 partout | ❌ KILL — pas de saisonnalité horaire exploitable ; la question venue (CFD 0,11R vs DEX) ne se pose même pas |

**Lecture transverse** : troisième cycle consécutif où le filtre « régime récent »
tue tout ce que la littérature propose (edges 2024 morts en 2025-26 : Fouetté-US100,
Extension ; anomalies académiques absentes : ToM actions, time-of-day FX, fix or).
Le seul mécanisme calendaire/sessionnel qui ait passé le filtre reste le hold
nocturne OR (Adage) — et ses extensions naturelles (Brent C4, amplificateur ToM)
sont maintenant toutes deux enterrées avec chiffres. **L'étagère ne se regarnit
pas par les anomalies simples : la matière vivante du système = le pipeline
existant (Adage ombre → jalon 5, Renversé ombre, Glissade-or live) + les
réhabilitations conditionnées d'octobre.**

## Discipline

- Un tir par famille : aucune fenêtre alternative testée après lecture des
  résultats (pas de fix AM après l'échec du fix PM, pas de ToM-DAX après l'échec
  ToM-US500 — ce serait du tuning séquentiel). Toute variante = nouveau
  protocole pré-enregistré.
- C4 avait un verdict plafonné à RÉSERVE (coût Brent non mesuré) — le brut
  négatif rend la sonde spread inutile.

## Familles écartées SANS test (déjà enterrées ou hors périmètre)

MR toutes venues, pairs/cointégration, carry CFD (swap écrémé), overnight
indices chaque-nuit, momentum intraday or, fade forex, Fouetté US100/or,
martingale/DCA, drift pré-annonce portefeuille, ToM or (×2), momentum XS
crypto, funding cross-DEX, DCA-timing majeures ; day-of-week (littérature
faible/instable, plus proche du data-mining que d'un mécanisme) ; prime de
volatilité (pas d'accès options) ; earnings/actions single-name (pas de
données ni d'instruments prop).

## Vivier restant (par ordre d'intérêt, rien de neuf à ajouter)

1. File des GO existante : **gap weekend or (item 6, ½ session)** →
   **variante annonces FOMC+CPI (item 7, un tir, espérance faible)** →
   **réhabilitations trimestrielles octobre (item 8 : Extension+filtres régime,
   Fouetté, Révérence)**.
2. PARK à clause de réveil : carry HL (moniteur funding, 8 instruments),
   MM 0-frais Lighter + XEMM (si la poche DeFi devient sérieuse), P3 trésorerie
   (sur capitalisation).
3. Fixing or sur l'ARGENT si un jour les coûts XAG baissent (inchangé 07-07).

**Conséquence file des GO : item 5 SOLDÉ — prochain go = item 6 (gap weekend or).**
