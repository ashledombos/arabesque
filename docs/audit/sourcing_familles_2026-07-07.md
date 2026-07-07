# Sourcing de nouvelles familles — Phase C (l'étagère est vide)

Date : 2026-07-07 · Go opérateur « lance le sourcing »
Méthode : familles documentées dans la littérature UNIQUEMENT → confrontation sur
dossier à notre structure (coûts mesurés par venue, débit ≥ 2/mois, décorrélation
avec Glissade-or/Renversé-métaux/session-or, profil prop firm) → pré-écrémage sur
nos données (2024-01→2026-07) quand trivial. Zéro tuning, hypothèses fixées avant test.

## File priorisée

### 1. 🥇 Fouetté-US100-NY — réhabilitation ciblée (PRÊT À INSTRUIRE)
- Historique : WF 4/4 PASS, **147 trades, Exp +0.190R brut, ~7 tr/mois** (EXPERIMENT_LOG §4).
- **Coûts (mesuré 07-07)** : range 30 min ouverture NY US100 médian **53,3 bps**
  (56,8 sur 12 mois) → SL ≈ 56 bps → coût 1,0-2,5 bps → 0,018-0,045R/trade →
  **ratio edge/coût 4,3-10,6×** — passe LARGEMENT (l'inverse exact du kill
  Fouetté-or : même stratégie, ranges 2,6× plus larges).
- Symbole FTMO : `US100.cash` (confirmé, 202 symboles listés). Intraday pur → zéro swap.
- Décorrélation : excellente (indices US, ORB intraday vs retournements or H1).
- ⚠️ Réserves : (a) **WR 44 %** (« RR2 no_BE ») — même tension de profil que
  session-métaux, décision opérateur ; (b) spread réel US100.cash à mesurer
  (feed non suivi → étendre sonde ou suivre le symbole) ; (c) WF récent 18 mois
  à refaire (l'edge +0.190R date de la validation initiale).
- **Prochain candidat recommandé.**

### 2. 🥈 Effets d'annonces macro (FOMC/NFP/CPI) sur or et indices
- Littérature : pre-FOMC drift = anomalie majeure documentée (Lucca-Moench, Fed NY)
  sur indices ; sur l'or, documentation surtout praticienne (drift conditionnel aux
  attentes de taux).
- Débit potentiel : ~8 FOMC + 12 NFP + 12 CPI/an ≈ 2-3 événements/mois.
- Entrée AVANT l'événement, sortie avant l'annonce = évite le spread d'événement
  (contrairement au fade forex, tué pour cette raison exacte).
- **Prérequis bloquant : dataset calendrier macro historique** (dates/heures
  FOMC/NFP/CPI 2024-2026) — à sourcer avant toute étude.

### 3. 🥉 Turn-of-month or — EN RÉSERVE (corrélé à session-or)
- Écrémage 07-07 : J-2..J+3 = **+19,1 bps/j (t=+1.63)** vs +8,2 hors fenêtre —
  effet marginal réel (~+11 bps/j × 5 j/mois) mais 4/5 sous-périodes seulement,
  et **même moteur que session-métaux** (dérive longue or) → pas un edge
  décorrélé. À revisiter comme AMPLIFICATEUR de session-métaux si celle-ci
  passe (concentrer les holds sur la fenêtre ToM), pas comme famille autonome.

### 4. ❌ Prime overnight indices US — QUASI-KILL sur dossier
- Écrémage : US500 overnight **+4,50 bps/nuit (t=+1.77, 4/5 sous-périodes > 0)**
  vs intraday +2,54 (t=0.87) — l'anomalie la plus documentée de la littérature
  se voit dans nos données. MAIS **financement CFD long mesuré 07-07 (sonde swap)
  = -2,16 bps/nuit uniforme** (US100/US500/US30, type pips) + spread ≈ 0,3-0,5 →
  **ratio brut/coût ≈ 1,7× < 3×**. Même schéma que le carry : le broker écrème
  l'anomalie. Ne pas instruire sauf découverte d'une venue au financement bas.

### 5. ❌ Momentum intrajournalier or — KILL à l'écrémage
- Littérature solide (Gao/Han/Li/Zhou JFE 2018, étendue aux commodity ETFs) MAIS
  mort sur nos données spot : signe(1re ½h NY)×(dernière ½h) = **-0,62 bps
  (t=-1.08)**, sous-période récente -3,4. Enterré avec chiffres.

## Captures au passage
- Swaps indices FTMO ajoutés à `logs/swap_rates.jsonl` (`US100/US500/US30.cash`,
  TARGETS de la sonde étendus) : long ≈ -2,2 bps/nuit, short ≈ +0,06-0,10.
- Symboles indices FTMO confirmés : `US100.cash`, `US500.cash`, `US30.cash`, etc.

## Pistes non instruites (vivier futur, par ordre d'intérêt)
- Filtres de régime pour réhabiliter Extension (re-WF trimestriel + condition de
  volatilité/tendance) — à coupler à la re-validation d'octobre.
- Gap weekend or (dimanche 22h) — micro-famille, données dispo.
- Effets de fixing PM re-testés sur l'ARGENT si un jour les coûts XAG baissent.
