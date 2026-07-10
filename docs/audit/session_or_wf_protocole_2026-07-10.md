# Session-or — Jalon 1 : WF formel IS/OOS (protocole pré-enregistré)

**Date de gel : 2026-07-10, AVANT tout calcul.** Suite du GO opérateur
(DECISIONS 2026-07-10, pipeline par étapes, dérogation WR actée).

## Design (STRICTEMENT identique au dossier 07-04 — zéro paramètre libre)

- LONG XAUUSD : entrée à l'open de la 1re barre min1 ≥ 18:00 America/New_York ;
  sortie time-exit à l'open de la 1re barre min1 ≥ 08:00 Europe/London.
- R = 1,0 × σ des 20 derniers rendements de session (causal, shift 1).
- Variante retenue du dossier : **SL -1R** (le BE maison détruit l'edge, §4).
- Garde-fous inchangés : saut weekend (dd 1-3), session > 20h exclue,
  ≥ 60 barres min1 par session.

Un design sans paramètre ajustable ⇒ le « walk-forward » est une validation
de stabilité par fenêtres formelles (tout est OOS par construction).

## Coûts (figés)

- **Primaire (continuité dossier, pessimiste)** : 2,4 bps/session
  (1,0 spread + 1,4 swap estimé).
- **Sensibilité (mesuré 07-05/07-10)** : 2,0 bps (0,98 spread nuit mesuré +
  0,5 swap marge [FTMO mesuré ≈ 0] + 0,5 slippage réouverture).

## Fenêtres et critères de verdict (écrits avant les résultats)

Fenêtres : 5 semestres calendaires 2024-S1 → 2026-S1 (+ 2026-S2 partiel,
informatif) ; focus récent = les 3 fenêtres 2025-01 → 2026-07.

PASS (→ jalon 2 : chiffrage implémentation time-exit) si TOUS, au coût primaire :
1. Exp nette > 0 sur **chacune des 3 fenêtres récentes** (le critère qui a
   tué Fouetté-US100) ;
2. Exp nette globale ≥ **+0,05R** ;
3. rythme ≥ **+1,0R/mois** sur le récent 18 mois ;
4. max drawdown de la série des sessions < **15R** ;
5. WR(> -0,25R) ≥ **55 %** (pas de dégradation vs dossier 58,6 %).

KILL sinon (et fin du pipeline, décision gravée). Aucune variante, aucun
retuning — ce dossier ne teste QUE le design du 07-04.
