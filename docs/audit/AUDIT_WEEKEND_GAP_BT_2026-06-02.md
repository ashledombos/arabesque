# Audit rejeu BT — protection gap weekend FX/metals

**Date :** 2026-06-02
**Périmètre :** Extension + Glissade × {AUDJPY, GBPJPY, CHFJPY, USDJPY, XAUUSD} × 2024-04-01 → 2026-06-01 (20 mois). IS = 60 % premiers mois (jusqu'au 2025-08-01), OOS = reste.
**Contexte :** commit `7c7809e` a posé un `weekend_gap_guard` live conservateur (no-new-entry vendredi ≥ 15h UTC, samedi, dimanche, sur AUDJPY/GBPJPY/CHFJPY/XAUUSD, brokers cTrader + TradeLocker). Ce rejeu mesure l'impact que cette règle aurait eu en BT, compare 4 cutoffs (15h/18h/20h/21h) et 2 variantes flat-friday (20h/21h), puis confronte le live.

> **Diagnostic only — aucune modif de code applicative.** Script : `tmp/audit_weekend_gap_fx.py`. Données brutes : `tmp/weekend_gap_fx_audit.json`.

## 1. Baseline BT (sans guard)

| Périmètre | n | Σ R | ExpR | WR | Max DD (R) | Pertes >1R | Trades crossant weekend |
|---|---|---|---|---|---|---|---|
| `bt_all` | 311 | +9.96 | +0.032 | 42.6 % | 12.48 | **0** | 6 |
| `bt_is` (avant 2025-08) | 210 | +8.56 | +0.041 | 42.9 % | 10.09 | 0 | 3 |
| `bt_oos` (depuis 2025-08) | 101 | +1.40 | +0.014 | 42.1 % | 8.00 | 0 | 3 |

Lecture : **le BT pessimiste ne génère JAMAIS de perte > 1R** sur ces instruments (SL strict, pas de modélisation gap). C'est attendu, mais cela borne la portée de l'audit : *le BT ne peut pas mesurer le risque que l'on cherche à couvrir*. Il sert uniquement à mesurer le **coût en edge** d'une règle d'exclusion.

Détail par stratégie × instrument (BT full) :

| Stratégie | Instrument | n | ExpR | WR | DD (R) | Σ R |
|---|---|---|---|---|---|---|
| extension | AUDJPY | 52 | +0.107 | 46.2 % | 3.80 | +5.56 |
| extension | GBPJPY | 2 | +0.095 | 50.0 % | 0.01 | +0.19 |
| extension | CHFJPY | 8 | −0.128 | 37.5 % | 1.23 | −1.03 |
| extension | USDJPY | 23 | −0.018 | 41.3 % | 2.24 | −0.41 |
| extension | XAUUSD | 104 | +0.024 | 42.3 % | 5.00 | +2.48 |
| glissade | AUDJPY | 35 | −0.005 | 41.4 % | 4.80 | −0.19 |
| glissade | GBPJPY | 8 | −0.305 | 31.2 % | 3.04 | −2.44 |
| glissade | CHFJPY | 9 | −0.200 | 33.3 % | 2.40 | −1.80 |
| glissade | USDJPY | 8 | −0.250 | 31.2 % | 2.40 | −2.00 |
| glissade | XAUUSD | 62 | +0.155 | 46.0 % | 3.80 | +9.59 |

Le gros de l'edge BT sur ce périmètre vient de **Extension AUDJPY** (+5.56R / 52 trades) et **Glissade XAUUSD** (+9.59R / 62 trades). GBPJPY et CHFJPY sont marginaux des deux côtés. USDJPY est neutre/négatif partout.

## 2. Scénarios — impact sur `bt_all`

| Scénario | n | Σ R | Δ R | ExpR | WR | DD (R) | n forcés / exclus |
|---|---|---|---|---|---|---|---|
| baseline | 311 | +9.96 | — | +0.032 | 42.6 % | 12.48 | — |
| no_new_fri **15h** | 289 | +11.01 | **+1.04** | +0.038 | 42.9 % | 11.88 | excl 22 |
| no_new_fri 18h | 303 | +11.40 | **+1.45** | +0.038 | 42.9 % | 12.28 | excl 8 |
| no_new_fri 20h | 310 | +10.96 | +1.00 | +0.035 | 42.7 % | 12.48 | excl 1 |
| no_new_fri 21h | 310 | +10.96 | +1.00 | +0.035 | 42.7 % | 12.48 | excl 1 |
| flat_friday **20h** | 311 | +16.18 | **+6.22** | +0.052 | 42.9 % | **9.88** | forced 9 |
| flat_friday 21h | 311 | +14.75 | +4.79 | +0.047 | 42.4 % | 13.23 | forced 5 |

Lectures importantes :

- **Tous les cutoffs `no_new_entry` améliorent le total R.** L'edge perdu en excluant des trades vendredi soir est *négatif* (les trades exclus auraient perdu en moyenne). C'est cohérent avec l'intuition "fin de semaine illiquide = setups dégradés".
- Le ratio gain/coût est meilleur à **18h** (+0.18 R par trade exclu) qu'à 15h (+0.047 R par trade exclu), mais **18h ne couvre pas le cas live observé** (entrée 2026-05-29 15h00 UTC pile, voir §4).
- **flat_friday 20h** affiche un gain BT impressionnant (+6.22 R, DD réduit de 2.6 R). **Mais ce chiffre est un proxy faible** : le script utilise le Close H1 du parquet au checkpoint pour calculer un R virtuel. Ce n'est ni une vraie simulation de slippage, ni une simulation de fill réel — c'est juste "où serait le marché si on fermait à H1 close 20h UTC vs sortie naturelle". Le BT ne sait pas modéliser le gap.

## 3. Live (n=33 sur même périmètre, depuis bascule live)

| Scénario | n | Σ R | Δ R | ExpR | WR | DD (R) | n forcés / exclus |
|---|---|---|---|---|---|---|---|
| baseline | 33 | −11.18 | — | −0.339 | 31.8 % | 11.38 | — |
| no_new_fri 15h | 31 | −9.54 | **+1.64** | −0.308 | 32.3 % | 9.73 | excl 2 |
| no_new_fri 18h | 33 | −11.18 | 0.00 | −0.339 | 31.8 % | 11.38 | excl 0 |
| no_new_fri 20h/21h | 33 | −11.18 | 0.00 | −0.339 | 31.8 % | 11.38 | excl 0 |
| flat_friday 20h/21h | 33 | −10.44 | +0.74 | −0.316 | 31.8 % | 10.64 | forced 1 |

Le live agrégé est en perte (drift d'exécution déjà connu et tracké par les invariants `execution_invariants` / `replay_drift_live_vs_theory`). Sur les **6 pertes > 1R** observées :

| Instrument | Strat | Broker | Entry | Exit | R | gap_excess | Cause |
|---|---|---|---|---|---|---|---|
| **AUDJPY** | extension | ftmo_challenge | 2026-05-29 15:00 UTC | 2026-05-31 21:05 | **−1.565** | **−0.565** | **gap weekend** |
| XAUUSD | extension | ftmo_challenge | 2026-04-28 12:00 | 2026-04-28 12:22 | −1.036 | −0.036 | slippage SL intra-day |
| XAUUSD | extension | ftmo_challenge | 2026-04-29 14:00 | 2026-04-29 15:38 | −1.002 | −0.002 | slippage SL intra-day |
| XAUUSD | extension | ftmo_challenge | 2026-05-01 10:00 | 2026-05-01 12:14 | −1.008 | −0.008 | slippage SL intra-day |
| XAUUSD | extension | ftmo_challenge | 2026-05-07 15:00 | 2026-05-07 15:53 | −1.008 | −0.008 | slippage SL intra-day |
| XAUUSD | glissade | ftmo_challenge | 2026-05-14 09:01 | 2026-05-14 17:24 | −1.002 | −0.002 | slippage SL intra-day |

**Sur 33 trades live, le risque gap weekend documenté = 1 incident, surcoût −0.565R.** Les 5 autres losses >1R sont des slippages SL intra-day (<0.04R d'excess, bruit normal cTrader). Σ gap_excess_r live = −0.621R, dont 91 % concentré sur l'incident AUDJPY.

## 4. Cohérence live / BT

Limite assumée : les rejeux BT ci-dessus tournent **sans** `WeekendFilter` (pas de `--no-weekend`), alors que le live a maintenant le `weekend_gap_guard` actif depuis `7c7809e`. La comparaison sert à mesurer l'impact *qu'aurait eu* le guard sur l'historique BT et live.

- En BT : 22 / 311 trades (7,1 %) tombent en zone "vendredi ≥ 15h UTC ou weekend".
- En live : 2 / 33 trades (6,1 %). Proportion cohérente.
- L'incident `1d725a82-a82` aurait été bloqué par `no_new_fri_15` (entry pile à 15h00 UTC).

Pour fermer la boucle dans un audit futur : rejouer Extension + Glissade avec `--no-weekend` sur l'ensemble des univers (pas que ces 5 instruments) afin de mesurer l'impact croisé sur les autres FX/metals. Hors scope de ce rejeu.

## 5. Recommandation

### 5.1 Cutoff `no_new_entry`

**Conserver cutoff 15h UTC** (état actuel `7c7809e`). Justifications :

1. C'est le seul cutoff qui capte l'incident live observé (entry 2026-05-29 15h00 UTC pile).
2. Cohérence avec `weekend_crypto_guard` (15h aussi) → règle uniforme, plus simple à expliquer.
3. ΔR BT à 15h est positif (+1.04R) — pas de pénalité d'edge nette.
4. Le sur-gain BT à 18h (+0.4R vs 15h) est trop maigre face au risque de ré-ouvrir une fenêtre 15-18h UTC peu liquide vendredi (la queue gap weekend n'est pas modélisée dans ce gain BT).
5. n=22 trades exclus / 20 mois ≈ 1.1 trade/mois — coût opportunité faible.

### 5.2 Flat-Friday

**Ne PAS adopter pour l'instant.** Justifications :

1. Le proxy BT (+6.22R sur 9 trades forcés) **n'est pas une preuve fiable** — il ne modélise pas le gap, juste un re-mark to market H1 close. Plausiblement gonflé par 1-2 cas extrêmes (à confirmer par inspection trade-par-trade si la question revient).
2. Le live ne fournit qu'**1 cas** (+0.74R, surcoût évité 0.565R) — anecdotique, IC très large.
3. Coût en edge réel : couper une position en gain potentiel continuation/gap favorable. La BE armée n'offre **aucune protection contre un gap inverse** (déjà établi dans la session) — donc *en faveur* de flat-friday — mais la même logique vaut symétriquement : un gap favorable du lundi est aussi capté.
4. Le cas live unique (AUDJPY −1.565R) aurait été **déjà bloqué en amont** par le `no_new_entry` à 15h — donc le flat-friday n'aurait rien protégé en plus *sur ce cas précis*. Sa valeur ajoutée concerne uniquement les trades **entrés avant 15h UTC vendredi** qui traversent le weekend (~7 trades BT sur 20 mois sur ce périmètre — base statistique très faible).
5. Phase 4 = stabilisation, pas nouvelles fonctionnalités (cf. `feedback_phase4_focus.md`).

### 5.3 À ré-évaluer

- **Dans 3-6 mois** : si ≥ 2 nouveaux incidents `loss_gt_1r` avec `crosses_weekend=True ET entry_ts < 2026-05-29 15:00 UTC`, ré-ouvrir l'arbitrage flat-friday avec un échantillon plus solide.
- **À court terme** : étendre `weekend_gap_guard.symbols` si un nouvel instrument FX/metals devient actif en live (CADJPY, NZDJPY, EURJPY sont les candidats naturels du fait du gap JPY week-end).
- **Bruit XAUUSD 1R intra-day** (5 cas / 33 live) : c'est un sujet *slippage SL XAU*, distinct du gap weekend — à traiter dans `execution_invariants` / `replay_live_vs_theory`, pas ici.

## 6. Décision

| Question | Réponse |
|---|---|
| Conserver cutoff 15h UTC ? | **Oui** (état actuel `7c7809e`, aucune modif). |
| Déplacer cutoff à 18h/20h/21h ? | **Non** — gain BT marginal, risque non couvert sur la fenêtre rouverte. |
| Adopter flat-friday systématique ? | **Non** — preuve insuffisante, attendre n≥3 incidents post-cutoff. |
| Adopter flat-friday conditionnel MFE ? | **Non** — la BE/MFE n'offre aucune protection gap (cf. session), donc le critère n'a pas de sens physique. |
| Étendre `symbols` du guard ? | **Pas maintenant**, ré-évaluer dès qu'un nouvel instrument FX/JPY devient actif. |

**Aucune modification de code à livrer dans la foulée de cet audit.** Le guard live actuel est conservé tel quel.
