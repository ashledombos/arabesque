# Analyse Pipeline par Catégorie — 17 fév 2026

## Vue d'ensemble

```
                        ◄── LOSING ──│── WINNING ──►
  crypto               ·············│█████████·····  exp=+0.047R  2979t  18/24 positifs
  fx_exotics           ··········███│··············  exp=-0.015R   400t   4/8  positifs
  fx_crosses           ······██████│··············  exp=-0.063R   288t   2/11 positifs
  metals               █████████████│··············  exp=-0.090R   276t   1/3  positifs
  fx_majors            █████████████│··············  exp=-0.097R    70t   1/2  positifs
```

## Constats

### Crypto : la stratégie MR fonctionne (+0.047R poolé)
- 18/24 cryptos positives en OOS — c'est un edge *catégoriel*, pas instrument-spécifique
- XTZUSD seul instrument avec edge statistiquement significatif (IC95 > 0)
- Même les cryptos "marginales" (XLMUSD +0.017R, GRTUSD +0.016R) contribuent au pool
- Volume = 2979 trades OOS → significatif en agrégé
- Les 6 négatives (DOGE, DOT, ADA, MANA, SAND, BAR) = probablement bruit, pas anomalie structurelle

### FX : la stratégie MR ne fonctionne pas (-0.041R poolé)
- 7/21 positifs seulement, et les positifs ont peu de trades (EURNZD 13t, CADCHF 21t)
- **Les majors sont mortes** : EURUSD -0.244R, GBPUSD -0.241R, USDJPY -0.091R
- **Les exotiques sont proches de zéro** (-0.015R) : la volatilité aide un peu
- Beaucoup d'INSUFFICIENT_TRADES → les signaux sont rares car BB restent étroites
- TOO_MANY_REJECTIONS sur EURCHF/EURCAD → les guards rejettent (spreads trop larges vs R)

### Métaux : borderline
- Seul XAUUSD survit (+0.039R) mais edge non significatif
- XAGUSD et XAGEUR clairement négatifs → l'argent ne mean-revert pas comme l'or

## Hypothèses à tester en Phase 1.3

1. **Trend strategy sur FX** : si le MR échoue car le FX *trend*, la strat trend devrait performer mieux
2. **Sub-labeling** : identifier quels *types* de signaux MR fonctionnent partout vs seulement sur crypto
3. **Facteurs discriminants** :
   - RSI depth au signal : est-ce que RSI < 20 donne un meilleur edge que RSI 30-35 ?
   - BB width au signal : large = mieux (confirmé par l'edge crypto vs FX)
   - Volume au signal : crypto a du volume réel, FX non (tick volume ≠ réel)

## Prochaines étapes

- [ ] Phase 1.3 : Signal sub-labeling (mr_deep_rsi, mr_wide_bb, trend_breakout, trend_continuation)
- [ ] Ventiler par sous-type et par catégorie d'instrument
- [ ] Tester la stratégie trend-only sur FX
- [ ] Ajouter indices/énergie au pipeline (données Parquet manquantes)
