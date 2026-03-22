# ARABESQUE — Handoff

> **Pour reprendre le développement dans un nouveau chat.**
> État live courant → `docs/STATUS.md`. Décisions techniques → `docs/DECISIONS.md`.
>
> Dernière mise à jour : 2026-03-22

---

## État en un coup d'œil

```
Live actif (compte ftmo_swing_test, renouvelé 2026-03-22) :
  Extension H1  → XAUUSD, GBPJPY, AUDJPY, CHFJPY (risk 0.45%)
  Extension H4  → 27 crypto (BTCUSD, ETHUSD, BNBUSD, SOLUSD…) (risk 0.55% via TF multiplier)
  Glissade H1   → XAUUSD, BTCUSD (LIVE — WF 3/3 PASS, WR 83%, Exp +0.147R)

WF validé, non déployé :
  Fouetté M1    → XAUUSD London, US100 NY, BTCUSD NY (fréquence insuffisante)
  Cabriole 4H   → crypto (73-95% overlap Extension, backup)

Testé, edge insuffisant :
  Renversé H1   → sweep + FVG retrace (WR 73% mais Exp +0.006R = breakeven)

WF en cours, non déployé :
  Révérence H4  → NR7 contraction → expansion (DOGEUSD PASS WR83%, overlap Extension à vérifier)

Concepts non viables :
  Pas de Deux   → pairs trading cointégration (mean-reversion, incompatible boussole)
```

---

## Résultats de référence

### Extension — 20 mois, 76 instruments

```
Période    : Jul 2024 → Fév 2026 (600 jours)
Trades     : 1998  |  WR : 75.5%  |  Exp : +0.130R
Total R    : +260.2R  |  Max DD : 8.2%  |  PF : 1.55
IC99       : +0.084R > 0 ✅
```

### Glissade RSI div — walk-forward 3/3 PASS

| Instrument | Config | OOS Trades | WR | Exp | Total R |
|---|---|---|---|---|---|
| XAUUSD H1 | RR3 +BE | 31 | 87% | +0.185R | +5.7R |
| BTCUSD H1 | RR3 +BE | 54 | 85% | +0.196R | +10.6R |

### Fouetté — walk-forward 4/4 PASS

| Instrument | Session | Config | OOS Trades | WR | Exp |
|---|---|---|---|---|---|
| XAUUSD | London | RR1.5 +BE | 63 | 76% | +0.086R |
| US100 | NY | RR2 no_BE | 147 | 44% | +0.190R |
| BTCUSD | NY | RR1.5 +BE | 280 | 76% | +0.043R |

Fouetté non déployé : fréquence trop basse sur forex seul (~14 trades/2 ans sur XAUUSD M1 NY).
Scanner indices + crypto M1 requis.

---

## Configuration active

| Paramètre | Valeur | Source |
|---|---|---|
| `risk_per_trade_pct` | **0.45%** (H1), **0.55%** (H4 via ×1.22) | accounts.yaml + settings.yaml |
| `max_daily_dd_pct` | 3.0% | accounts.yaml (FTMO limite 5%, GFT 4%) |
| `max_total_dd_pct` | 8.0% | accounts.yaml (FTMO limite 10%) |
| BE trigger / offset | 0.3R / 0.20R | position_manager.py |
| Protection active | LiveMonitor 4 paliers | execution/live_monitor.py |
| Per-TF risk multiplier | H4 → ×1.22 | settings.yaml (risk_multiplier_by_timeframe) |

---

## Leçons immuables

- **BE est LE levier principal du WR** : 75% des trades atteignent 0.3R MFE → convertit les losers
- **Tick-level TSL non optionnel** : +183R avec TSL vs +10.4R H1-only backtest
- **Trend-only** : mean-reversion testée sur 4 replays, 3 univers → perd systématiquement
- **Forex majors négatifs** en walk-forward ; seuls JPY crosses + XAUUSD passent en H1
- **ROI désactivé sur crypto H4** : ROI détruisait l'edge (+0.044R → +0.181R sans ROI)
- **Anti-lookahead strict** : signal bougie i → fill open bougie i+1 ; si SL+TP même bougie → SL
- **Reversals ICT/SMC non viables** : Renversé testé (142 trades), WR 73% mais Exp +0.006R = breakeven. Le mouvement post-sweep est trop court pour RR2

---

## Prochaines étapes

### Fait (2026-03-22)
- [x] Per-timeframe risk : H4 → ×1.22 (0.55% effectif). Backtest validé : daily DD max 0.6% à 0.60%.
- [x] Glissade activée en live (était déjà intégrée au pipeline, pas en shadow dans le code)
- [x] Per-account risk overrides (accounts.yaml) — déjà fait session 2026-03-21
- [x] Guard "Best Day" (% du profit total) ajouté dans metrics.py
- [x] Bar aggregator override le timeframe du signal (corrige le hardcode "1h" dans signal.py)
- [x] Monte Carlo avec barrières : P(+10% avant DD 10%) — `monte_carlo_barriers()` dans stats.py
  - Challenge 0.80% : P(target)=82%, P(breach)=4.5%, médiane 196 trades (~2-3 mois)
  - Funded 0.45% : P(target)=56%, P(breach)=0.3%, médiane 320 trades

### Fait (2026-03-22, session 2)
- [x] Guard "Best Day" en live (LiveMonitor) : alerte 25% (Telegram) et 30% (ntfy+Telegram)
- [x] Profil `ftmo_challenge` activé dans accounts.yaml (Phase 1, 100k, risk 0.80%, protected)
- [x] Profil `gft_compte1` ajouté (GFT GOAT Phase 1, 150k, risk 0.30%, protected)
- [x] GFT compte2 supprimé (perdu pour dépassement 30j sans trade)
- [x] Analyse instruments GFT : H1 100% couvert, H4 crypto 6/27 (les 3 top performers OK)
- [x] Script `tmp/compare_live_vs_backtest.py` pour comparaison manuelle live vs backtest

### Immédiat
- [ ] Configurer notifications Telegram (bot token invalide, ntfy OK)
- [ ] Vérifier que le moteur live tourne avec les nouvelles config (lundi après weekend)
- [ ] Exécuter comparaison live vs backtest : `python tmp/compare_live_vs_backtest.py`

### Court terme
- [ ] Corrélation inter-positions : facteur par catégorie pour le guard open_risk
- [ ] Ajouter support `--session` CLI pour Fouetté (passer london/ny/tokyo)

### Exploré et conclu (2026-03-22)
- [x] Monte Carlo barrières → Challenge 0.80% : P(target)=82%, P(breach)=4.5% (surestimé vu les guards adaptatifs)
- [x] Scan Fouetté crypto M1 → seuls BTCUSD+BNBUSD NY viables, edge faible (+0.019-0.031R)
  → Fouetté ne change pas la donne. L'accélération challenge passe par risk 0.80%
- [x] XAUUSD London Fouetté → 3 trades/800j, quasi mort

### Bugs corrigés (2026-03-22)
- [x] `risk_cash: 0.0` dans trade_journal → live.py hardcodait `volume=0.01, risk_cash=0`
  au lieu de passer les valeurs calculées par le dispatcher. Fix : `OrderResult` enrichi
  avec `risk_cash` et `volume_lots` depuis le dispatcher.
- [x] Alerte lot sous-évalué : warning si lot effectif < 50% du risque demandé
- [x] Détection positions orphelines : `reconcile()` détecte les positions broker
  non trackées par Arabesque et logge un warning `👻 Position orpheline`
- [x] ntfy testé et fonctionnel. Telegram KO (bot token à vérifier)

---

## Restrictions modèles

| Zone | Modèle requis |
|---|---|
| `arabesque/core/*.py`, `arabesque/modules/position_manager.py` | **Opus uniquement** |
| `arabesque/strategies/*/signal.py` (validée en live) | **Opus uniquement** |
| Tout le reste | Sonnet suffit |

---

## Commandes essentielles

```bash
# Lancer le moteur live (toujours avec le venv)
PYTHONUNBUFFERED=1 nohup /var/home/machine/dev/arabesque/.venv/bin/python \
  -m arabesque.live.engine > /tmp/arabesque_live.log 2>&1 &

# Surveiller
tail -f /tmp/arabesque_live.log
grep "💰\|🔒\|🛡️\|🚨\|⚠️" /tmp/arabesque_live.log | tail -20

# Backtest
python -m arabesque run --strategy extension --mode backtest XAUUSD BTCUSD
python -m arabesque walkforward --strategy extension --universe crypto
python -m arabesque run --strategy glissade --mode backtest XAUUSD BTCUSD
python -m arabesque run --strategy fouette --mode backtest XAUUSD
```
