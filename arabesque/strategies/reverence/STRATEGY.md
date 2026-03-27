# Révérence — Range Contraction → Expansion

> **Nom de code** : Révérence
> **Famille** : Danse classique — *révérence*
> **Mouvement** : En danse, la révérence est une inclinaison (contraction) suivie d'un redressement (expansion). En trading, Révérence détecte une contraction de range (NR4/NR7, inside bars) — la compression — puis entre sur l'expansion qui suit.

---

## Description

**Révérence** serait une stratégie de **range contraction breakout** sur H1 ou H4.

Quand le marché se comprime (bougies de plus en plus petites, ou inside bars successives), l'énergie s'accumule. Le breakout qui suit est souvent directionnel et puissant. Un candle pattern (engulfing, marubozu) confirme la direction du breakout.

**Logique envisagée :**
1. Détecter une **contraction** : NR4 (Narrow Range 4 — range de la bougie courante est le plus petit des 4 dernières) ou NR7, ou 2+ inside bars consécutives
2. Attendre le **breakout** : close au-delà du high/low de la bougie NR ou de la mère (inside bar)
3. Confirmer avec **candle pattern** : engulfing ou bougie à corps plein (marubozu) dans la direction du breakout
4. Filtre directionnel optionnel : EMA200 ou trend HTF
5. **SL** : bord opposé de la bougie NR + buffer. **TP** : RR fixe ou expansion mesurée

---

## Pourquoi cette stratégie

- NR4/NR7 et inside bars sont **100% mécaniques** — aucune subjectivité
- La compression → expansion est un phénomène physique du marché (réduction de volatilité → explosion)
- Le candle pattern (engulfing) confirme la direction : ne sert pas de signal seul mais de filtre
- **Partiellement corrélé avec Extension** (BB squeeze détecte aussi la compression) — mais Révérence utilise des bougies individuelles, pas une moyenne sur 20 barres, donc les timings diffèrent
- Timeframe H1/H4 : cohérent avec le reste du système

---

## Détection de contraction

### NR4 / NR7 (Narrow Range)
```python
candle_range = high - low
nr4 = candle_range == candle_range.rolling(4).min()  # Plus petit range des 4 dernières
nr7 = candle_range == candle_range.rolling(7).min()  # Plus petit range des 7 dernières
```

### Inside bar
```python
inside_bar = (high[i] <= high[i-1]) and (low[i] >= low[i-1])
# Série de 2+ inside bars = compression plus forte
```

### Combinaison
NR7 + inside bar = setup le plus fort (double signal de compression).

---

## Paramètres envisagés

| Paramètre | Valeur probable | Description |
|---|---|---|
| `contraction_type` | `nr7` | `nr4`, `nr7`, `inside_bar`, ou combinaison |
| `min_inside_bars` | 2 | Nombre minimum d'inside bars consécutives |
| `breakout_confirm` | `engulfing` | Pattern de confirmation du breakout |
| `ema_filter` | 200 | EMA trend filter (0 = désactivé) |
| `rr_tp` | 2.0 | RR du TP |
| `sl_source` | `nr_range` | SL = bord opposé de la bougie NR |
| `sl_buffer_atr` | 0.1 | Buffer ATR sur le SL |

---

## Différence avec Extension

| | Extension | Révérence |
|---|---|---|
| Détection compression | BB width sur 20 barres | Bougie individuelle (NR4/NR7) |
| Trigger | ADX + close hors BB | Candle engulfing hors range NR |
| Granularité | Macro (tendance multi-jours) | Micro (1–3 bougies) |
| Timing | Plus tardif (squeeze confirmé) | Plus précoce (première bougie d'expansion) |

Les deux capturent la compression → expansion mais à des échelles différentes. **L'overlap pourrait être significatif** — à mesurer par analyse des signaux comme fait pour Cabriole.

---

## Risques et questions ouvertes

- **Overlap avec Extension** : si le BB squeeze et le NR7 se déclenchent souvent ensemble, pas de diversification réelle (même problème que Cabriole)
- **Faux breakouts** : un NR7 suivi d'un breakout qui échoue est fréquent — le candle pattern suffit-il à filtrer ?
- **Fréquence** : combien de NR7 + engulfing par mois sur les instruments cibles ?
- **Candle pattern seul s'érode** : documenté dans la littérature. Ici le pattern est un filtre, pas le signal — mais à vérifier empiriquement

---

## Résultats (2026-03-21)

### Ablation

- H1 : WR 71-73%, Exp ~0 = breakeven (543-671 trades). Non viable.
- **H4** : WR 78-80%, Exp +0.024-0.034R (465-789 trades). Edge mince mais positif.
- Engulfing non contributif (réduit fréquence sans améliorer WR).
- EMA200 filtre utile (améliore WR de 3-5pp).

### Backtest H4 (meilleur config : NR7 + body ≥ 60% + EMA200, sans engulfing)

| Instrument | Trades | WR | Exp | PF | MaxDD |
|---|---|---|---|---|---|
| XAUUSD | 60 | 88% | +0.062R | 1.53 | 0.7% |
| SOLUSD | 90 | 82% | +0.069R | 1.39 | 1.6% |
| DOGEUSD | 69 | 84% | +0.097R | 1.61 | 1.6% |
| ETHUSD | 62 | 76% | +0.057R | 1.24 | 2.7% |
| BTCUSD | 78 | 68% | -0.058R | 0.82 | 3.8% |

### Walk-forward H4 (IS=2400, OOS=800, 3 fenêtres)

| Instrument | OOS Trades | WR | Exp | PF | Verdict |
|---|---|---|---|---|---|
| DOGEUSD | 30 | 83% | +0.059R | 1.35 | **PASS** |
| SOLUSD | 33 | 82% | +0.065R | 1.36 | MARGINAL |
| ETHUSD | 27 | 78% | +0.130R | 1.58 | MARGINAL |
| AVAXUSD | 32 | 72% | -0.002R | 0.99 | FAIL |
| ADAUSD | 37 | 70% | -0.109R | 0.63 | FAIL |

### Diagnostic

Edge réel mais mince sur crypto H4 (DOGEUSD WF PASS). ~1.5 trades/mois par instrument.

### Overlap avec Extension (vérifié 2026-03-27)

Analyse sur DOGEUSD H4 (2024-01 → 2026-03) :
- Extension : 286 signaux, 116 jours uniques
- Révérence : 460 signaux, 367 jours uniques
- **Overlap : 50 jours en commun = 14% des signaux Révérence**

**Conclusion** : overlap faible (14%), très différent de Cabriole (73-95%). Révérence capte
des mouvements que Extension ne voit pas. Complémentaire en théorie, mais edge trop mince
pour déploiement immédiat. À réévaluer si l'edge se confirme sur plus d'instruments.

---

## Statut

| Phase | État |
|---|---|
| Concept documenté | ✅ |
| Code signal.py | ✅ (NR7 + body ratio + EMA200) |
| Ablation | ✅ H1 breakeven, H4 edge mince |
| Backtest | ✅ 4/10 instruments positifs sur H4 |
| Walk-forward | ✅ DOGEUSD PASS, SOLUSD/ETHUSD marginal |
| Overlap check | ✅ 14% overlap = complémentaire (vérifié 2026-03-27) |
| Shadow live | ❌ |
| Live | ❌ |
