# Cabriole — Donchian Breakout 4H

> **Nom de code** : Cabriole
> **Famille** : Danse classique — *cabriole*
> **Mouvement** : En danse, la cabriole est un saut vif où les jambes se frappent en l'air. En trading, Cabriole saute au-delà d'un canal de prix consolidé dès que le breakout se confirme.

---

## Description

**Cabriole** est une stratégie de **breakout directionnel** sur timeframe 4H.

Elle détecte quand le prix sort du canal formé par les plus hauts et plus bas des N dernières barres (canal de Donchian), filtré par la direction EMA200 et un filtre de volatilité. L'entrée se fait sur la confirmation du breakout.

**Logique :**
1. Calculer le **canal de Donchian(20)** (high/low sur 20 barres, décalé d'une barre pour éviter le lookahead)
2. Vérifier la **direction du trend** : EMA200
3. Appliquer le **filtre volatilité** : ATR relatif < 90e percentile (filtre les breakouts dans les périodes trop volatiles)
4. Confirmer le **breakout** : close > d_high (long) ou close < d_low (short), aligné avec EMA200
5. Entrée au **open de la bougie suivante** (anti-lookahead strict)
6. **SL** : 1.5 ATR + buffer 10%. **TP** : 2R

---

## Paramètres

| Paramètre | Valeur | Description |
|---|---|---|
| `donchian_n` | 20 | Période du canal Donchian |
| `ema_period` | 200 | EMA trend filter |
| `sl_atr` | 1.5 | Multiplicateur ATR pour le SL |
| `rr_tp` | 2.0 | RR du TP |
| `buffer_atr` | 0.10 | Buffer SL (10% d'ATR en plus) |
| `vol_quantile` | 0.90 | Filtre volatilité : ATR relatif < ce percentile |
| `vol_window` | 500 | Fenêtre du percentile volatilité |

---

## Résultats validés

**Walk-forward 6/6 PASS** — résultat remarquable en apparence, mais voir "Overlap" ci-dessous.

---

## ⚠️ Overlap critique avec Extension

**73 à 95% des signaux Cabriole sont aussi des signaux Extension.**

Analyse : Cabriole (canal Donchian + EMA200) et Extension (BB squeeze + ADX + EMA) capturent le même phénomène — le breakout de tendance — avec des indicateurs différents mais sur les mêmes instruments crypto 4H.

Conséquences :
- **Pas de diversification réelle** : les deux stratégies ouvrent les mêmes trades, presque en même temps
- **Double exposure non intentionnelle** si les deux sont actifs simultanément
- Le WF PASS 6/6 de Cabriole reflète l'edge d'Extension, pas un edge indépendant

**Décision révisée 2026-03-28** : Cabriole **est déployé en live** sur 6 crypto (BTCUSD, ETHUSD, SOLUSD, DOGEUSD, LINKUSD, ADAUSD) en complément d'Extension, sous rodage ×0.50. Configuré dans `config/settings.yaml → strategy_assignments.cabriole`.

Si Extension est un jour retiré pour une raison quelconque, Cabriole peut le remplacer directement sur les mêmes instruments.

---

## Usage actuel

- **Live FTMO** : actif sur 6 crypto H4, rodage ×0.50, en parallèle d'Extension. Entries STOP au breakout Donchian.
- **Live GFT** : 🚫 **bloqué depuis 2026-04-25** via `strategy_broker_exclusions` (config/settings.yaml). Cause : 0/10 WR sur GFT (semaines 16+17), p≈4×10⁻⁷ vs baseline WR 77% — incompatibilité spread/exécution TradeLocker. Critère de levée : Cabriole FTMO WR ≥ 70% sur ≥ 20 trades.

---

## Statut

| Phase | État |
|---|---|
| Walk-forward 6/6 | ✅ PASS |
| Overlap diagnostiqué | ✅ 73-95% des signaux = Extension |
| Déployé en live FTMO | ✅ Oui (depuis 2026-03-28, rodage ×0.50) |
| Déployé en live GFT | 🚫 Bloqué (2026-04-25 — voir DECISIONS.md) |
