# Extension — Fiche Stratégie

> **Nom de code** : Extension
> **Famille** : Danse classique — *extension de jambe*
> **Mouvement** : En danse, l'extension est l'allongement d'un membre dans la
> continuité du corps. En trading, l'extension est le mouvement de prix qui
> s'étend hors des bandes de Bollinger après une période de compression.

---

## Description

**Extension** est une stratégie de **trend-following** sur timeframe H1.

Elle exploite un cycle naturel de la volatilité : les marchés alternent entre
périodes de compression (faible volatilité, prix dans un range) et périodes
d'expansion (momentum directionnel). Extension entre au moment précis où cette
expansion commence, après un squeeze de Bollinger.

**Logique :**
1. Identifier un **squeeze** : les Bandes de Bollinger se contractent en dessous
   du 20e percentile de leur largeur historique
2. Attendre une **expansion** : la largeur BB recommence à croître
3. Confirmer un **breakout** : le prix casse au-dessus (LONG) ou en dessous (SHORT)
   de la BB, avec ADX en hausse et CMF du même côté
4. **Entrer** au open de la bougie suivante (anti-lookahead strict)
5. Laisser le **TSL** travailler : breakeven à +0.3R, puis trailing par paliers

---

## Paramètres

Voir `params.yaml` pour les jeux de paramètres validés.

| Paramètre | Valeur par défaut | Rôle |
|---|---|---|
| `bb_period` | 20 | Période des Bandes de Bollinger |
| `bb_std` | 2.0 | Multiplicateur d'écart-type |
| `squeeze_pctile` | 20.0 | Seuil squeeze (20e percentile) |
| `squeeze_memory` | 10 | Barres de mémoire du squeeze |
| `adx_trend_min` | 20.0 | ADX minimum pour confirmer la tendance |
| `sl_atr_mult` | 1.5 | Multiplicateur ATR pour le SL |
| `be_trigger_r` | 0.30 | MFE pour activer le breakeven |
| `be_offset_r` | 0.20 | Offset du breakeven (SL à entry + offset) |
| `risk_pct` | 0.40 | % du capital risqué par trade |

---

## Résultats validés

**Run de référence — 20 mois (Juil 2024 → Fév 2026)**

| Métrique | Valeur |
|---|---|
| Période | Jul 2024 → Fév 2026 (600 jours) |
| Univers | 76 instruments (forex + métaux + crypto) |
| Trades | 1998 |
| Win Rate | 75.5% |
| Expectancy | +0.130R |
| Total R | +260.2R |
| Max DD | 8.2% (à 0.40%/trade) |
| IC95 | +0.095R > 0 ✅ |
| IC99 | +0.084R > 0 ✅ |
| Score FTMO | 4/5 |

**Consistance** : tous les 8 blocs de 250 trades sont positifs. Aucune période
perdante sur 20 mois.

---

## Edge réel

L'edge de cette stratégie repose sur **deux mécanismes indépendants** :

1. **Breakeven (WR)** : ~75% des trades atteignent +0.3R (trigger BE) et se
   ferment au minimum à +0.20R. Sans BE, le WR serait ~40-50%.
   *Le BE est LE levier principal du win rate — ne pas le modifier sans rejeu
   complet sur 20 mois.*

2. **Fat tails (R total)** : ~14% des trades atteignent le TP (moy. +1.41R) ou
   sont emportés par un move fort (+3R et plus). Ces trades font l'essentiel
   du P&L en R.

---

## Limites connues

- **Crypto weekend gaps** : Les gaps de week-end sur les cryptos peuvent faire
  déclencher le SL au-dessous du niveau prévu. Risque structural accepté.
- **Corrélation crypto** : En krach, 15 instruments crypto peuvent signaler
  simultanément. Le guard `max_open_risk_pct` limite l'exposition cumulée.
- **ADX UNIUSD/ICPUSD** : Barres de données corrompues détectées (spikes).
  Filtre intrabar actif dans `data/store.py` (`_clean_ohlc`).

---

## Conditions de marché défavorables

- Consolidations longues sans trend net (BB très large + ADX faible)
- Marchés hypercorrélés (krach simultané)
- News macro imprévisibles (NFP, FOMC, etc.) — pas de filtre d'actualité

---

## Décisions immuables

1. **TREND-ONLY** — La mean-reversion a été abandonnée après 4 replays : elle
   perd sur toutes les catégories sur les deux périodes testées.
2. **Offset BE = 0.20R** (pas 0.15) — 323/339 trailing exits étaient à +0.15R
   exact avec offset 0.15. Chaque BE exit rapporte +0.05R de plus avec 0.20.
3. **Risk 0.40%/trade** — À 0.50%, le max DD dépasse 10% (FTMO). Marge de 1.8%.
4. **Univers complet forex + métaux + crypto** — La crypto surperforme le forex
   sur 20 mois (+145R vs +115R). Exclure la crypto est une erreur.

---

## Statut

| Phase | État |
|---|---|
| Backtest IS/OOS | ✅ Validé (IC99 > 0) |
| Replay parquet 20 mois | ✅ Validé |
| Live dry-run cTrader | ✅ En cours |
| Live FTMO | 🟡 En phase de validation live |

---

## Référence

Cette stratégie est dérivée de **BB_RPB_TSL** (Bollinger Band Reversion Pivot
Breakout with Trailing Stop Loss), stratégie Pine Script open-source tournant
en live depuis 527+ jours avec WR 90.8%. Extension adapte ce concept aux
contraintes prop firm (SL réel, guards DD, anti-lookahead strict).
