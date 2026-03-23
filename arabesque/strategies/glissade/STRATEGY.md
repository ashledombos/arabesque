# Glissade — RSI Divergence H1

> **Nom de code** : Glissade
> **Famille** : Danse classique — *glissade*
> **Mouvement** : En danse, la glissade est un pas glissé latéral qui prépare un saut ou un changement de direction. En trading, Glissade détecte une divergence RSI — le prix continue dans une direction mais la force du mouvement s'épuise — et entre dans la direction du retournement qui suit.

---

## Description

**Glissade** est une stratégie de **retournement dans le trend** sur timeframe H1.

Elle n'est PAS du mean-reversion (contre-trend) — elle entre dans la direction du trend principal (EMA200) mais attend que la dynamique du sous-mouvement soit épuisée (divergence RSI), ce qui donne un timing d'entrée plus favorable et un SL plus serré.

**Logique :**
1. Identifier la **direction du trend** : prix au-dessus/en-dessous de l'EMA200
2. Détecter une **divergence RSI bearish/bullish** : le prix fait un nouveau sommet/creux mais le RSI ne confirme pas
3. Attendre la **confirmation** : le RSI croise son signal (ou revient dans la zone neutre)
4. Entrer dans la **direction du trend** (pas contra-trend — le retournement doit être aligné avec EMA200)
5. **SL** : ATR fixe. **TP** : RR2 ou RR3 (selon preset)

---

## Paramètres clés

| Paramètre | Valeur défaut | Description |
|---|---|---|
| `rsi_period` | 14 | Période du RSI |
| `rsi_signal_period` | 3 | Lissage du signal RSI |
| `divergence_lookback` | 5 | Fenêtre de recherche des pivots RSI |
| `ema_period` | 200 | EMA trend filter |
| `sl_atr_mult` | 1.5 | Multiplicateur ATR pour le SL |
| `rr_tp` | 3.0 | RR du TP (RR3 = config retenue) |
| `pw_bars` | 3 | Pivot window pour la détection des divergences |

---

## Résultats validés

**Walk-forward 3/3 PASS** (config +BE pw3 RR2 et RR3)

| Instrument | Config | OOS Trades | WR | Exp | Total R | PF | MaxDD |
|---|---|---|---|---|---|---|---|
| XAUUSD H1 | pw3 RR2 +BE | 31 | 87% | +0.185R | +5.7R | 2.43 | 0.4% |
| XAUUSD H1 | pw3 RR3 +BE | 31 | 80% | +0.132R | +7.9R | 1.66 | 1.5% |
| BTCUSD H1 | pw3 RR2 +BE | 54 | 85% | +0.196R | +10.6R | 2.32 | 1.3% |
| BTCUSD H1 | pw3 RR3 +BE | 54 | 85% | +0.157R | +14.3R | 2.02 | 1.3% |

**Config retenue : RR3 +BE** — légèrement meilleure sur XAUUSD, quasi-identique sur BTCUSD. WR 80-87% conforme à la boussole FTMO.

---

## Mode abandonné

**VWAP pullback M1** (GlissadeSignalGenerator legacy) : scalping intraday sur pullback VWAP + EMA. Structurellement négatif sur tous les tests. Conservé dans le code pour référence, ne pas déployer.

---

## Gestion des positions

Identique à Extension : BE trigger 0.3R / offset 0.20R (TSL via position_monitor.py).
Le TP indicatif est à RR3, mais le TSL peut capter des moves plus grands.

---

## Conditions de marché favorables / défavorables

**Favorables :**
- Trend H1 clair (EMA200 bien inclinée)
- Oscillations régulières du RSI (marché "respirant")
- Faible corrélation intraday avec les autres positions Extension

**Défavorables :**
- Marché en consolidation plate (faux divergences)
- News majeure imminente (NFP, FOMC, CPI) — pas de filtre actualité
- Crypto weekend (gaps qui invalident les niveaux RSI)

---

## Overlap avec Extension

Glissade et Extension peuvent ouvrir simultanément sur XAUUSD ou BTCUSD.
Les guards `max_open_risk_pct` limitent l'exposition cumulée — aucun doublement de position non intentionnel n'est possible.

---

## Décisions immuables

1. **Avec BE uniquement** — sans BE, les variantes no_BE (WR 35%) sont hors boussole
2. **Aligné sur EMA200** — entrée seulement dans la direction du trend, jamais contra-trend
3. **XAUUSD + BTCUSD** — les deux seuls instruments WF PASS ; ne pas étendre sans nouveau WF

---

## Statut

| Phase | État |
|---|---|
| Backtest IS/OOS | ✅ Validé |
| Walk-forward 3/3 | ✅ PASS |
| Wilson CI99 | ✅ > 0 |
| Shadow live | ✅ Terminé |
| Live réel | ✅ **Actif** depuis 2026-03-22 (XAUUSD + BTCUSD H1) |
