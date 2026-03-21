# ARABESQUE — Handoff

> **Pour reprendre le développement dans un nouveau chat.**
> État live courant → `docs/STATUS.md`. Décisions techniques → `docs/DECISIONS.md`.
>
> Dernière mise à jour : 2026-03-20

---

## État en un coup d'œil

```
Live actif (compte ftmo_swing_test, expire ~2026-03-19) :
  Extension H1  → XAUUSD, GBPJPY, AUDJPY, CHFJPY
  Extension H4  → 27 crypto (BTCUSD, ETHUSD, BNBUSD, SOLUSD…)
  Glissade H1   → XAUUSD, BTCUSD (shadow — log seulement)

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
| `risk_per_trade_pct` | **0.45%** | `arabesque/core/guards.py` (relevé 0.40→0.45 le 2026-03-18) |
| `max_daily_dd_pct` | 3.0% | guards.py (FTMO limite 5%, GFT 4%) |
| `max_total_dd_pct` | 8.0% | guards.py (FTMO limite 10%) |
| BE trigger / offset | 0.3R / 0.20R | position_manager.py |
| Protection active | LiveMonitor 4 paliers | execution/live_monitor.py |

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

### Immédiat
- [ ] Renouveler le compte FTMO test (~2026-03-21) — voir procédure dans `docs/STATUS.md`
- [ ] Configurer notifications Telegram/ntfy dans `config/secrets.yaml`

### Court terme
- [ ] Accumuler ~100 trades Glissade shadow → décider activation live
- [ ] Scanner indices + crypto M1 pour Fouetté (augmenter la fréquence de signaux)
- [ ] Analyser overlap Révérence H4 vs Extension H4 crypto (comme Cabriole)
- [ ] Si overlap < 50% : shadow Révérence DOGEUSD, SOLUSD, ETHUSD H4

### Structurel
- [ ] Lire `max_daily_dd_pct` depuis `accounts.yaml` (GFT = 4%, FTMO = 5%)
  → actuellement hardcodé dans `PropConfig` dans `guards.py`

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
