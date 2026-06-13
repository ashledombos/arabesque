# Audit `max_open_positions` 5 vs 7 vs 10 — Extension + Glissade

> Généré 2026-06-13. Script : `tmp/replay_cap_analysis.py` (read-only).
> Origine : /bilan 06-13 — le cap live (5) ne prend que ~1-2 signaux sur 13 quand
> des clusters H4 crypto tirent ensemble. Question : protège-t-il ou sous-échantillonne-t-il ?

## Méthode

- Cible = set live exact (`rsl._build_targets`, extension+glissade) : **33 targets**
  (glissade H1 ×2, extension H1 ×4, extension H4 ×27 crypto).
- Signaux générés avec le **signal.py live**, dédupliqués par session (comme `replay_signals_vs_live`).
- Chaque signal simulé par `simulate_pure` (BE 0.3R / offset 0.20R / TP 2R / SL signal.sl,
  convention pessimiste SL+TP même barre → SL) → `r_theo` + `(entry_ts, exit_ts)`.
- **Simulation portefeuille événementielle** : compte partagé, cap sur positions ouvertes
  simultanées. Avant chaque entrée, on ferme les positions dont `exit_ts <= entry_ts`.
  `n_open >= cap` → REJET (cap-binding).
- Fenêtre : **2024-09-01 → 2026-06-13** (~21 mois), **1337 signaux** (876 H4, 461 H1).
- Risque/trade : FTMO 0.1125%, GFT 0.075% (0.45%/0.30% × rodage 0.25).
- `open_risk 2%` tolère ~18 positions à ce risque → **le cap borne bien avant open_risk** (vérifié).

## Résultats

| cap | acceptés | rejetés | rej. cluster H4 | max concur. | total R | mean R | max DD (R) | DD% FTMO | DD% GFT | worst day (R) | open_risk max % |
|----:|---------:|--------:|----------------:|------------:|--------:|-------:|-----------:|---------:|--------:|--------------:|----------------:|
| 5  | 1184 | 153 | 142 | 5  | +210.4 | +0.178 | -15.6 | -1.75% | -1.17% | -8.8 | 0.56% |
| 7  | 1251 | 86  | 80  | 7  | +225.0 | +0.180 | -15.6 | -1.75% | -1.17% | -8.8 | 0.79% |
| 10 | 1299 | 38  | 36  | 10 | +237.0 | +0.182 | -15.6 | -1.75% | -1.17% | -8.8 | 1.13% |

**Clusters H4** : 165 clusters même-barre (≥2 signaux), **582 signaux** = **66% des H4**.
Top clusters : 2025-06-22 = 14 signaux simultanés, 2025-11-04 = 13, 2025-01-13 = 11.

## Verdict : **cap=5 SOUS-ÉCHANTILLONNE, il ne protège pas**

1. **mean R identique** (+0.178 → +0.182) : les trades ajoutés en élargissant le cap sont
   de même qualité. Le cap ne filtre ni les winners ni les losers → cohérent avec le
   constat /bilan « sélection à forte variance, pas de biais directionnel ».
2. **max DD / worst day / breach identiques** sur 5/7/10. Élargir le cap **n'augmente pas
   le drawdown** dans ces données. Le worst day (-8.8R) provient de positions déjà prises
   sous cap=5 (parmi les 5 premières) → l'élargissement n'y ajoute rien.
3. **open_risk reste trivial** (≤ 1.13% à cap=10, vs limite 2%) : à risque rodage, même
   10 positions concurrentes ne stressent pas l'exposition.
4. **Coût du cap=5** : ~27R laissés sur la table sur 21 mois (210→237) pour **0 bénéfice
   de risque mesuré**. 93% des rejets sont des clusters H4 crypto simultanés.

## Caveats (à ne pas ignorer avant tout changement live)

- **Pas de tick-level / pas de coûts** : `simulate_pure` sort sur bougies parquet H1/H4,
  sans slippage ni swap/commission. La DD absolue (-1.75%) est donc **sous-estimée** vs le
  live (cf. HANDOFF 06-07 : ~44% du DD réel = coûts non journalisés). La comparaison
  **relative entre caps** reste valide (même approximation pour les trois).
- **Corrélation crypto** : le modèle traite chaque R indépendamment. 10 shorts crypto
  simultanés sont fortement corrélés ; un gap adverse pourrait tous les toucher ensemble —
  un risque de queue que la sim séquentielle capture mal. Le worst-day identique sur 5/7/10
  est rassurant **dans ces données**, mais ne prouve pas l'absence de tail-risk corrélé.
- **DD-pause non simulée comme arrêt** : sans objet ici (DD loin du seuil 8%).

## Recommandation (pas d'action live dans ce run)

Le cap=5 est un throttle de fréquence pur, sans gain de risque mesuré. Un élargissement
**mesuré vers 7** capterait une partie de l'edge laissé (+15R/21 mois) en gardant une marge
sur le tail-risk corrélé crypto. **Ne pas sauter à 10 directement** tant que le tail-risk
corrélé n'est pas quantifié (sim corrélée ou tick-level), et **pas pendant que FTMO est à
0.08pp de CAUTION**. Décision = opérateur. Données brutes : `docs/audit/cap_analysis_latest.json`.
