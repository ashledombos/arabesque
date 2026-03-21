# Renversé — Liquidity Sweep + Structure Shift + FVG Retrace

> **Nom de code** : Renversé
> **Famille** : Danse classique — *renversé*
> **Mouvement** : En ballet, le renversé est un mouvement où le corps bascule dans une direction puis se retourne brusquement. En trading, Renversé détecte un sweep de liquidité (le prix bascule au-delà des stops) puis un retournement de structure confirmé par un Fair Value Gap.

---

## Description

**Renversé** est une stratégie de **reversal post-sweep** sur H1, avec biais directionnel H4.

Elle s'inspire des concepts ICT/SMC mais les implémente de façon entièrement mécanique et déterministe. Aucune zone subjective, aucun "institutional order flow" non vérifiable — uniquement des règles objectivables sur OHLC.

---

## Séquence de setup

```
1. COMPRESSION (contexte)
   BB width < 25e percentile roulant sur 100 barres
   → Le marché est calme, la liquidité s'accumule aux extrêmes

2. SWEEP DE LIQUIDITÉ
   Le prix casse au-delà du swing high/low récent
   mais REJETTE (mèche significative, close réintègre le range)
   → Les stop-loss ont été balayés, le "smart money" a absorbé

3. STRUCTURE SHIFT (CHOCH — Change of Character)
   Après le sweep, le prix casse le swing OPPOSÉ
   → Confirmation que la direction a changé

4. FVG (Fair Value Gap) — imbalance
   Un gap 3-barres se forme dans le mouvement post-CHOCH
   → Zone d'imbalance institutionnelle

5. RETRACE dans le FVG
   Le prix revient tester la zone du FVG
   → Entrée sur le pullback, SL au-delà du sweep extreme
```

---

## Exemple concret (setup bullish / long)

```
         ╭─── Sweep low : mèche sous swing_low, close au-dessus
         │                    ╭─── CHOCH : close au-dessus swing_high
         │                    │      ╭─── FVG bullish (gap 3 barres)
         │                    │      │    ╭─── Retrace dans le FVG → ENTRÉE LONG
    ─────┤                    │      │    │
         │    ┌─┐             │   ┌─┤    │
    ─────┤    │ │    ┌─┐   ┌─┤   │ │    ▼
         │ ┌──┤ │    │ │   │ │   │ │  ┌─┐
         └─┤  │ │ ┌──┤ │ ┌─┤ │ ┌─┤ │  │ │ ← entrée ici
           │  └─┘ │  └─┘ │ └─┘ │ └─┘  │ │
     SL ←  ▼      └──────┘     └──────┘ └─┘
    (sous le sweep extreme)
```

---

## Biais HTF (H4)

Le signal n'est valide que si le close H1 est aligné avec l'EMA200 H4 :
- LONG seulement si close > H4 EMA200
- SHORT seulement si close < H4 EMA200

L'EMA H4 est calculée par resample interne (H1 → H4), pas par un feed séparé.

---

## Paramètres

| Paramètre | Valeur défaut | Description |
|---|---|---|
| `bb_period` | 20 | Période BB pour la compression |
| `bb_std` | 2.0 | Écart-type BB |
| `squeeze_percentile` | 25 | BB width < ce percentile = squeeze |
| `squeeze_lookback` | 100 | Fenêtre roulante du percentile |
| `swing_period` | 20 | Lookback pour swing high/low |
| `min_wick_ratio` | 0.5 | Mèche de sweep ≥ ratio × candle range |
| `choch_max_bars` | 15 | Max barres après sweep pour CHOCH |
| `min_fvg_atr` | 0.2 | Taille FVG ≥ ratio × ATR |
| `fvg_max_bars` | 15 | Max barres après CHOCH pour FVG |
| `retrace_max_bars` | 20 | Max barres pour attendre le retrace |
| `htf_ema_period` | 200 | Période EMA pour le biais H4 |
| `sl_buffer_atr` | 0.1 | Buffer SL = ratio × ATR au-delà du sweep |
| `rr_tp` | 2.0 | TP = RR × distance SL |
| `max_sl_atr` | 3.0 | Rejeter si SL > max × ATR |

### Flags d'ablation

| Flag | Défaut | Effet si False |
|---|---|---|
| `require_compression` | True | Ignore le contexte BB squeeze |
| `require_choch` | True | Skip la confirmation CHOCH |
| `require_fvg_retrace` | True | Entrée directe sans attendre FVG/retrace |
| `require_htf_bias` | True | Ignore le filtre EMA200 H4 |

---

## Tests d'ablation prévus

Le sub_type du signal encode les composants actifs :
- `renverse_sq_sw_ch_fvg_htf` — full (tous les filtres)
- `renverse_sw_ch_fvg_htf` — sans compression
- `renverse_sq_sw_fvg_htf` — sans CHOCH
- `renverse_sq_sw_ch_htf` — sans FVG retrace
- `renverse_sq_sw_ch_fvg` — sans biais HTF

Cela permet d'isoler la contribution de chaque brique à l'edge.

---

## Différences avec les autres stratégies

| | Extension | Glissade | Renversé |
|---|---|---|---|
| Type | Trend breakout | Divergence dans le trend | Reversal post-sweep |
| Trigger | BB squeeze → close hors bande | RSI div + EMA200 | Sweep + CHOCH + FVG retrace |
| Direction | Continuation de tendance | Continuation (après pullback) | Retournement |
| Overlap attendu | Faible (logique opposée) | Faible | — |

Renversé est **non-corrélé** avec Extension et Glissade : il entre sur un retournement après un sweep, alors que les deux autres entrent dans la continuation du trend.

---

## Limites et hypothèses v1

- **Pas d'order block** : trop ambigu en v1, prévu comme filtre optionnel en v2
- **Pas de lower timeframe** : signal et entrée sur H1 uniquement
- **Swing detection simplifiée** : rolling max/min (pas de pivot formel)
- **FVG = 3-bar gap strict** : pas de "modified FVG" ou "consequent encroachment"
- **Univers initial** : XAUUSD et BTCUSD H1 (à étendre après validation)
- **Le concept ICT/SMC n'est pas prouvé académiquement** : cette stratégie est une hypothèse quantitative testable, pas un dogme

---

## Résultats backtest (2024-01 → 2026-03, tick-level BE)

Ablation complète : compression et CHOCH trop restrictifs (tue la fréquence sans
améliorer WR). Meilleur config = sweep + FVG retrace + HTF bias (sans compression,
sans CHOCH).

| Instrument | Trades | WR | Exp | Total R | PF | Verdict |
|---|---|---|---|---|---|---|
| XAUUSD H1 | 50 | 64% | -0.128R | -6.4R | 0.58 | **Négatif** |
| BTCUSD H1 | 92 | 78% | +0.079R | +7.2R | 1.40 | Marginal |
| **Combined** | **142** | **73%** | **+0.006R** | **+0.8R** | — | **Breakeven** |

**Diagnostic** : BE convertit assez de losers pour 73% WR, mais l'avg win (+0.28R
via trailing) vs avg loss (-0.91R) rend l'edge structurellement trop mince.
La majorité des trades sortent au BE (+0.20R) — les reversals ne sont pas assez
amples pour atteindre RR2 TP. XAUUSD négatif. BTCUSD marginal.

**Verdict** : ne passe pas la boussole (edge insuffisant pour prop firm).
Concept intéressant mais pas déployable en l'état.

---

## Statut

| Phase | État |
|---|---|
| Concept documenté | ✅ |
| Code signal.py | ✅ v1 (sweep + FVG + HTF, ablation intégrée) |
| Ablation tests | ✅ Compression et CHOCH non contributifs |
| Backtest IS/OOS | ❌ Edge insuffisant — XAUUSD négatif, BTCUSD marginal |
| Walk-forward | ❌ Non justifié (edge trop mince) |
| Shadow live | ❌ |
| Live réel | ❌ |
