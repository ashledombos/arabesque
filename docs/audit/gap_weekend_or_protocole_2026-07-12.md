# Protocole pré-enregistré — Gap weekend or (item 6 file des GO)

**Date de gel : 2026-07-12. Commité AVANT tout calcul.**
Méthode identique aux cycles de sourcing 07-07 / 07-11 et aux protocoles gelés
(ToM amplificateur `05e45bc`, funding cross-DEX `cb110d5`) : hypothèses,
définitions, coûts et seuils figés avant de regarder les données. **Un tir,
zéro tuning.** Toute variante non déclarée ici = nouveau protocole un tir.

## Hypothèse (littérature / folk, sens figé)

Les gaps de réouverture du dimanche soir tendent à se **combler** (« gaps
fill ») : le prix revient vers le close du vendredi dans les heures qui
suivent la réouverture. Sens joué = **FADE** du gap (gap up → short,
gap down → long). Un résultat significatif dans le sens opposé
(continuation) = **KILL** au présent protocole ; une variante continuation
serait un nouveau protocole un tir.

Contexte défavorable connu et assumé : 3 cycles de sourcing consécutifs où le
régime récent a tué toutes les anomalies calendaires/sessionnelles simples.
Prior faible — c'est le « fond de tiroir » de la file des GO.

## Données

- XAUUSD Dukascopy min1, store `barres_au_sol/dukascopy/min1/XAUUSD.parquet`,
  période **2024-01-01 → 2026-07-10** (30 mois, ~131 weekends).
- ⚠️ Le store exclut structurellement sam/dim (`compute_missing_days`,
  backends.py:62) → la réouverture dimanche ~22:00 UTC est absente.
  **Complément ad hoc** : fetch des dimanches de la période via
  `_dukascopy_fetch_day` (même backend, même validation), stocké dans
  `tmp/gap_weekend_sundays.parquet` — HORS store (le pipeline reste inchangé).
- Barres « réelles » = volume > 0 (le store contient une grille complète).
- **Repli pré-écrit** si Dukascopy ne sert pas les dimanches : gap mesuré au
  1er bar réel lundi 00:00 UTC — proxy dégradé (2-3 h de cotation dimanche
  déjà absorbées, dilution documentée), verdict au mieux RÉSERVE.

## Définitions figées

- `close_ven` = close de la dernière barre réelle du vendredi
  (attendue 20:59 été / 21:59 hiver UTC).
- `reopen` = 1re barre réelle du dimanche (attendue 22:00 été / 23:00 hiver
  UTC). Weekend sans barre dimanche = exclu (compté et rapporté).
- `gap_bps` = ln(open(reopen) / close_ven) × 1e4. Gap nul exclu.
- **Entrée fade** = open de la 1re barre réelle ≥ reopen + 5 min
  (anti-lookahead : le gap est observé à la réouverture, on entre après ;
  les 5 min évitent le carnet vide de la première minute).
- **Sortie** = close de la 1re barre réelle ≥ lundi 08:00 UTC
  (London open, moment liquide ; hold ~10 h, **aucun rollover 22:00 UTC
  traversé → zéro financement**).
- `r_fade_bps` = −signe(gap) × ln(px_sortie / px_entrée) × 1e4.

## Variantes pré-déclarées (les deux jugées, aucune troisième)

- **A** : tous les weekends à gap non nul.
- **B** : |gap| ≥ 15 bps (5× le coût A/R pessimiste : un fade qui capte en
  moyenne 60 % d'un gap de 15 bps paie 3× le coût).

## Coûts

- Spread réouverture dimanche **non sondé** (le sondeur 7 nuits couvrait
  21:00-03:00 UTC en semaine : 0,98 bps médian). Réouverture réputée plus
  large → **3 bps A/R pessimiste** (≈ 3× la nuit de semaine).
- Si INTÉRÊT : sonde spread dimanche 22:00 UTC obligatoire avant tout
  protocole de validation (même schéma que la levée de réserve session-or).

## Seuils de verdict (identiques cycles 07-07 / 07-11)

- **INTÉRÊT** : |t| ≥ 2,0 ET signe stable ≥ 4/5 sous-périodes ET brut ≥ 3×
  coût (9 bps/événement) — sur la variante concernée.
- **RÉSERVE** : 2 des 3 critères.
- **KILL** : sinon — enterré avec chiffres.
- Sous-périodes = 5 blocs contigus égaux de la série des weekends.

## Descriptif rapporté SANS verdict

Distribution de |gap| (moyenne, médiane, part ≥ 15 bps), taux de comblement
(retour au close vendredi avant lundi 21:00 UTC), asymétrie gap up / gap down.

## Livrables

`tmp/gap_weekend_fetch_sundays.py`, `tmp/gap_weekend_sundays.parquet`,
`tmp/gap_weekend_study.py` → `tmp/gap_weekend_results.txt` ; verdict annexé
ici + EXPERIMENT_LOG + HANDOFF.

---

## VERDICT (annexé après calcul, 2026-07-12) — KILL 0/3 sur les deux variantes

- Données servies : 106/131 dimanches (Dukascopy ne publie pas les dimanches
  2024-01 → 2024-06-16 ; 25 weekends exclus comptés) → n=102 weekends
  instruits, série effective ~2024-06-23 → 2026-07-05.
- **Variante A (n=102)** : fade moyen **−7,93 bps/weekend**, t=−0,96,
  1/5 sous-périodes positives, ratio brut/coût **−2,6×** → KILL 0/3.
- **Variante B |gap| ≥ 15 bps (n=33)** : fade moyen **−22,13 bps**, t=−1,14,
  2/5, ratio **−7,4×** → KILL 0/3.
- Descriptif : |gap| médian 6,9 bps, 32 % ≥ 15 bps ; **comblement 86 %** avant
  lundi 21:00 — taux élevé mais espérance signée du fade négative (illusion
  du « gap fill ») ; asymétrie : fade d'un gap UP = −16,3 bps (l'or qui ouvre
  haut continue, cohérent drift nocturne Adage), gap down ≈ 0.
- La continuation (sens opposé) n'atteint pas non plus la significativité
  (|t| < 1,2) — toute variante continuation = nouveau protocole un tir,
  espérance a priori faible.

Scripts : `tmp/gap_weekend_fetch_sundays.py`, `tmp/gap_weekend_study.py`
→ `tmp/gap_weekend_results.txt`, sessions `tmp/gap_weekend_sessions.jsonl`.
