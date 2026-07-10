# Étude sur dossier — Candidat « session-métaux » (Phase C, famille n°1)

Date : 2026-07-04 · Statut : **PASS provisoire XAUUSD (3/4 critères filtre dur) — réserves à lever avant WF/pipeline** · XAGUSD : KILL

## 1. Hypothèses (fixées depuis la littérature AVANT de toucher les données)

- **H1 — « monte en Asie, baisse à Londres/NY »** : rendements overnight positifs /
  intraday négatifs sur l'or, documenté 30+ ans (COMEX futures, spot London Fix,
  minières, ETF — « Overnight versus day returns in gold and gold related assets » ;
  Speck, moyennes minute sur 5 ans).
- **H2 — dérive baissière autour du fixing PM de Londres (~15h Londres)** :
  motif systématique documenté 2001-2013 (études LBMA/fixing).

Règle anti-fouille : seules ces 2 hypothèses sont testées. Aucun scan libre
des 24 heures, aucun tuning de paramètres.

## 2. Test empirique (Dukascopy H1, 2024-01 → 2026-07, 650 sessions)

| Hypothèse | XAUUSD | XAGUSD | Verdict |
|---|---|---|---|
| H1 session Asie (17h NY → 8h Londres) | **+7,8 bps/j, t=+2.0, 5/5 sous-périodes > 0** | **+15,5 bps/j, t=+2.1, 5/5 > 0** | ✅ CONFIRMÉE |
| H1 sessions Londres + NY | ≈ 0 (+1,1 / +1,7 bps, t<1) | ≈ 0/negatif | cohérent |
| H2 fenêtre fixing PM (13h-16h Londres) | -0,6 bps, t=-0.35, signes instables | -2,3 bps, t=-0.72, instable | ❌ RÉFUTÉE (trop petit vs coûts) |

Granularité horaire : la dérive Asie est **concentrée sur les 2 premières heures
après la réouverture** (~22h-24h UTC) : XAU h22 +2,6 bps (t=+3.7), h23 +2,9 (t=+3.3) ;
XAG h22 +5,1 (t=+4.6). Ce sont les t-stats les plus fortes de la journée.

## 3. Coûts (rapport P1 + mesure snapshots)

- Spread XAUUSD mesuré (médian, `multi_broker_snapshots`) : **GFT 0,76 bps / FTMO 0,94 bps**
  — mais **uniquement 04h-20h UTC** (échantillonnage lié aux positions ouvertes).
- ⚠️ **Trou de mesure n°1** : aucun spread mesuré sur 21h-03h UTC — pile la fenêtre
  où la dérive se concentre (réouverture = spread élargi attendu).
- ⚠️ **Trou de mesure n°2** : swap long XAU/XAG inconnu sur les 2 venues (0 hold
  overnight métaux journalisé). Estimation dossier : ~-1,4 bps/nuit (≈ -5 %/an).
- Hypothèse de coûts de la simulation : XAU 2,4 bps/session (1,0 spread + 1,4 swap),
  XAG 4,4 bps (3,0 + 1,4).

## 4. Simulation min1 (2,5 ans, design pré-déclaré, zéro tuning)

Design : LONG à l'open de la 1re barre ≥ 18h America/New_York (réouverture) →
time-exit à l'open de la 1re barre ≥ 8h Europe/London. R = 1σ des 20 dernières
sessions (causal). Script : `tmp/etude_session_metaux.py`.

| Variante | XAUUSD Exp net | ~R/mois | sous-périodes | XAGUSD |
|---|---|---|---|---|
| brut (sans SL) | +0.131R | +2.8 | 5/5 > 0 | +0.085R mais porté par 1 sous-période |
| **SL -1R** | **+0.071R** | **+1.5** | **5/5 > 0 (+0.02…+0.13, récent +0.08)** | **-0.022R → KILL** |
| SL + BE maison 0.3R/0.2R | -0.001R | ≈ 0 | — | -0.049R |

**Enseignement structurel** : l'overlay BE maison (qui fait vivre Extension/Glissade)
**détruit cet edge** — le bruit overnight retraverse sans cesse le niveau BE et
hache la dérive. Un candidat session exige un time-exit pur avec SL de sécurité,
PAS la machinerie BE/trailing existante.

## 5. Verdict filtre dur (XAUUSD, variante SL -1R)

| Critère | Exigence | Mesuré | Verdict |
|---|---|---|---|
| Edge brut ≥ 3× coût | ≥ 3× | ~+0.10R brut vs ~0.03R coût ≈ **3,3×** | ✅ (limite) |
| Stabilité WF régime récent | 18 mois | 5/5 sous-périodes, récent +0.077R | ✅ |
| Débit ≥ 2 trades/mois | ≥ 2 | **~21,5/mois** | ✅✅ |
| Profil prop firm | WR haut, pertes bornées | pertes bornées ✓ mais **WR 58,6 % < cible 70 %** | ⚠️ NON CONFORME |

## 6. Réserves à lever AVANT d'engager le pipeline (décision opérateur ensuite)

1. **Spread 22h-24h UTC non mesuré** → étendre l'échantillonnage snapshots aux
   métaux sur cette fenêtre pendant ~1 semaine (read-only). Si le spread réel à
   la réouverture fait ×3, le ratio edge/coût tombe sous 3×.
2. **Swap GFT/FTMO non mesuré** → ✅ **FTMO CAPTÉ 2026-07-05** (`scripts/capture_swap_rates.py`,
   `logs/swap_rates.jsonl`) : XAUUSD long **-0.019 bps/nuit** (quasi nul, vs -1,4 estimé
   ici) → coût total ≈ spread seul, ratio edge/coût monte vers ~5-7× côté FTMO.
   Caveats : unité proto (pips) à valider empiriquement au 1er hold overnight
   (swap_cash à l'exit) ; GFT toujours inconnu (aucune route API TradeLocker,
   accrual empirique seulement). Sensibilité initiale conservée : swap à -3 bps/nuit
   → Exp ≈ +0.05R (survit) ; -5 bps → marginal.
3. **Profil WR 58,6 %** : en contradiction avec le filtre écrit (WR ≥ 70 %).
   Points pour : pertes bornées -1R, trades petits et nombreux → courbe
   d'équité régulière par agrégation (l'esprit de la boussole). Points contre :
   la lettre du filtre. **Décision = opérateur.**
4. **Dépendance régime haussier or** : l'échantillon 2024-2026 = bull market.
   La littérature (30 ans) dit que l'anomalie précède ce régime, mais un
   retournement baissier de l'or affaiblirait cette jambe long-only.
5. **Implémentation** : time-exit à heure fixe = mécanisme absent de l'engine
   (sorties actuelles : SL/TP/BE/trailing). Toucherait position_manager (zone
   Opus). À chiffrer seulement si go.

## 7. Prochaine étape proposée

Lever les réserves 1-2 (mesures passives, ~1 semaine, coût nul) pendant que
l'ombre Renversé tourne. Puis go/no-go opérateur sur le profil (réserve 3).
Si go : WF rigoureux IS/OOS + implémentation time-exit + pipeline standard.

## 8. Addendum 2026-07-10 — Réserve n°1 LEVÉE (sondeur spread nocturne, verdict J-1)

7 nuits de collecte (`logs/metals_night_spread.jsonl`, 532 quotes GFT, 04→10 juil.) :

| Fenêtre | XAUUSD médian / p90 | XAGUSD |
|---|---|---|
| Jour 04-20h UTC | 0,86 / 0,97 bps | 9,85 / 11,18 |
| **Nuit 21-03h UTC (fenêtre de la dérive)** | **0,98 / 1,05 bps** | 10,90 / 12,30 |

- **Pas d'élargissement significatif à la réouverture** (h22 = 0,98, h23 = 0,94) :
  l'hypothèse de coût de la simulation (1,0 bps) est confirmée au centième.
- Avec le swap FTMO quasi nul (réserve n°2, -0,019 bps/nuit), le coût total
  réel ≈ 2 bps/session aller-retour → **ratio edge/coût ~3,3-5×** confirmé.
- XAGUSD : 10-11 bps la nuit — le KILL argent est re-confirmé par les coûts.

**État des réserves** : 1 ✅ levée · 2 ✅ quasi levée (FTMO ; GFT empirique au
1er hold) · 3 ⚠️ WR 58,6 % = décision opérateur (LE point du go/no-go) ·
4 = risque de régime assumé · 5 = implémentation time-exit à chiffrer si go.
