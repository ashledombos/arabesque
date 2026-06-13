# Audit tail-risk crypto corrélé — prérequis cap=7 (P2)

> Généré 2026-06-13. Script : `tmp/replay_tailrisk_crypto.py` (read-only).
> JSON : `docs/audit/tailrisk_crypto_latest.json`. Fait suite au replay cap
> (`cap_analysis_2026-06-13.md`) qui a montré que cap=5 sous-échantillonne sans
> réduire le DD séquentiel. Seul angle mort restant : N positions crypto qui
> sautent **ensemble** (gap corrélé), mal capturé par la sim séquentielle.

## Méthode

- Réutilise la génération de signaux + `simulate_pure` du replay cap (mêmes 1337
  signaux extension+glissade, 21 mois, guards live).
- Crypto = 27 instruments (tout extension H4 + BTC/ETH). XAUUSD (métal) et paires
  JPY (extension H1) exclues.
- Simulation portefeuille cap 5/7/10 ; suit l'état crypto des positions ouvertes
  à chaque instant : concurrence crypto, LONG/SHORT simultanés, open risk agrégé.
- **Stress test** : à la concurrence crypto MAX observée, gap corrélé adverse
  **-1R/-2R/-3R sur TOUTES les positions crypto ouvertes**, impact % equity
  FTMO/GFT, breach daily/total, **en partant du DD courant** (FTMO -6.92%, GFT -5.28%).

## Exposition simultanée

| cap | max crypto concur. | max LONG crypto | max SHORT crypto | open risk max %FTMO | %GFT |
|----:|-------------------:|----------------:|-----------------:|--------------------:|-----:|
| 5  | 5  | 5  | 5  | 0.56% | 0.38% |
| 7  | 7  | 7  | 7  | 0.79% | 0.53% |
| 10 | 10 | 10 | 10 | 1.13% | 0.75% |

→ Les positions crypto peuvent remplir **100% des slots** (max crypto = cap) et
**toutes dans le même sens** (max LONG = max SHORT = cap, à des instants distincts).
Le pire cas de corrélation (book 100% crypto unidirectionnel) est réel. Pire cluster
H4 même barre : **14 signaux simultanés**. L'open risk agrégé reste trivial (≤1.13%)
car le risque/trade rodage est petit → la contrainte n'est PAS l'open risk, c'est
le **drawdown corrélé**.

## Stress test — gap corrélé, départ DD courant

| cap | gap | hit %FTMO | total DD FTMO | breach FTMO | hit %GFT | total DD GFT | breach GFT |
|----:|----:|----------:|--------------:|:-----------:|---------:|-------------:|:----------:|
| 5  | -1R | 0.56% | -7.48% | ok      | 0.38% | -5.66% | ok |
| 5  | -2R | 1.13% | -8.04% | pause 8% | 0.75% | -6.03% | ok |
| 5  | -3R | 1.69% | -8.61% | pause 8% | 1.12% | -6.41% | ok |
| 7  | -1R | 0.79% | -7.71% | ok      | 0.53% | -5.81% | ok |
| 7  | -2R | 1.57% | -8.49% | pause 8% | 1.05% | -6.33% | ok |
| 7  | -3R | 2.36% | **-9.28%** | pause 8% (pas de breach, marge 0.72%) | 1.57% | -6.86% | ok |
| 10 | -1R | 1.13% | -8.04% | pause 8% | 0.75% | -6.03% | ok |
| 10 | -2R | 2.25% | -9.17% | pause 8% (thin) | 1.50% | -6.78% | ok |
| 10 | -3R | 3.38% | **-10.29%** | **🔴 BREACH** | 2.25% | -7.53% | ok |

- La contrainte mordante est le **total DD** (on part déjà de -6.92%), pas le daily
  (hit max 3.38% < seuil daily 5%). GFT ne breach dans aucun scénario.
- **cap=5** : survit même à -3R (-8.61%, pas de breach).
- **cap=7** : survit à -3R (-9.28%, pas de hard breach, marge 0.72% ; déclencherait
  la pause interne 8% — comportement protecteur voulu). Confortable à -2R (-8.49%).
- **cap=10** : **breach le hard -10% à -3R** (-10.29%) ; -2R passe de justesse (-9.17%).

## Mitigations contextuelles

- Le **weekend_crypto_guard** bloque les nouvelles positions crypto cTrader du
  vendredi cutoff au dimanche → le scénario le plus corrélé (gap de réouverture
  weekend) est partiellement neutralisé ; le tail intra-semaine reste, mais un -3R
  simultané sur tout le book crypto y est moins probable qu'un gap weekend.
- Le stress est **conditionné au DD courant -6.92%**. Sur compte plus sain (ex. DD
  ~-5%), cap=10 à -3R = -8.4% → pas de breach. Le risque cap=10 est donc surtout
  **couplé au DD élevé actuel**, pas structurel.

## Verdict

- **cap=7 : ACCEPTABLE.** Survit au pire stress (-3R corrélé → -9.28%) sans hard
  breach ; la pause interne 8% agit comme filet avant catastrophe ; -2R confortable.
  Le weekend guard couvre le scénario le plus corrélé.
- **cap=10 : DIFFÉRER (pas exclure).** Breach le -10% à -3R corrélé **au DD courant**.
  Redevient acceptable une fois FTMO remonté (~-5% ou mieux). À ré-évaluer **après**
  validation de cap=7 ET reprise du DD.
- **Seuils de rollback (confirmés)** : la garde interne -8% est le bon tripwire
  (cap=5/7 la touchent avant le hard breach). Rollback cap=7→5 si l'un de : FTMO
  total DD < -8%, worst-day live < -10R, open_risk observé > 1.5%, invariants
  alert/critique, ou **événement de perte corrélée crypto (≥3 SL crypto même jour)**.

## Conclusion opérationnelle

Le dernier verrou intellectuel avant cap=7 est levé : **cap=7 est tenable même sous
gap corrélé sévère, au DD actuel**. Passage 5→7 applicable selon la séquence DECISIONS
2026-06-13 (compte flat, aucun incident, commit config séparé, restart contrôlé,
observation 2 sem / 30 trades). **cap=10 reste différé** jusqu'à reprise du DD.
Aucun changement live appliqué dans ce run (analyse d'abord, décision ensuite).
