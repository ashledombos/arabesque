# ARABESQUE — Handoff

> **Pour reprendre le développement dans un nouveau chat.**
> État live courant → `docs/STATUS.md`. Décisions techniques → `docs/DECISIONS.md`.
>
> Dernière mise à jour : 2026-03-27

---

## État en un coup d'œil

```
Live actif (compte ftmo_challenge, account_id 45667282, démo cTrader) :
  Extension H1  → XAUUSD, GBPJPY, AUDJPY, CHFJPY (risk 0.45%)
  Extension H4  → 27 crypto (BTCUSD, ETHUSD, BNBUSD, SOLUSD…) (risk 0.55% via TF multiplier)
  Glissade H1   → XAUUSD, BTCUSD (LIVE — WF 3/3 PASS, WR 83%, Exp +0.147R)

Balance FTMO : ~94 473$ (DD -5.5%) — protection CAUTION active, risk réduit 50%
Balance GFT  : ~142 684$ (DD -4.9%) — activé 2026-03-23, 36 instruments mappés, idle (pas de signaux crypto)

WF validé, non déployé :
  Fouetté M1    → XAUUSD London, US100 NY, BTCUSD NY (fréquence insuffisante)
  Cabriole 4H   → crypto (73-95% overlap Extension, backup)

Testé, edge insuffisant :
  Renversé H1   → sweep + FVG retrace (WR 73% mais Exp +0.006R = breakeven)

WF en cours, non déployé :
  Révérence H4  → NR7 contraction → expansion (DOGEUSD PASS WR83%, overlap 14% = complémentaire, edge mince)

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
| Environnement cTrader | **Démo** (is_demo: true) | settings.yaml + accounts.yaml |

### Architecture credentials

Tokens OAuth stockés UNE SEULE FOIS dans `secrets.yaml → ctrader_oauth`.
Chaque broker référence via `oauth: ctrader_oauth` (pas de duplication).
`_resolve_secret_refs()` dans `config.py` résout les références au chargement.
`update_broker_tokens()` sauvegarde dans la section partagée.

---

## Leçons immuables

- **BE est LE levier principal du WR** : 75% des trades atteignent 0.3R MFE → convertit les losers
- **Tick-level TSL non optionnel** : +183R avec TSL vs +10.4R H1-only backtest
- **Trend-only** : mean-reversion testée sur 4 replays, 3 univers → perd systématiquement
- **Forex majors négatifs** en walk-forward ; seuls JPY crosses + XAUUSD passent en H1
- **ROI désactivé sur crypto H4** : ROI détruisait l'edge (+0.044R → +0.181R sans ROI)
- **Anti-lookahead strict** : signal bougie i → fill open bougie i+1 ; si SL+TP même bougie → SL
- **Reversals ICT/SMC non viables** : Renversé testé (142 trades), WR 73% mais Exp +0.006R = breakeven
- **Challenges FTMO = démo cTrader** : `is_demo: true` obligatoire, sinon CANT_ROUTE_REQUEST
- **TradeLocker order_id ≠ position_id** : `create_order` retourne un order_id, il faut `get_position_id_from_order_id()` pour le lier à la position réelle
- **pip_size varie entre brokers** : GFT XAUUSD = 0.0001, cTrader = 0.01. Toujours utiliser `sym_info.pip_size` du broker
- **DD tracking doit être persistant** : `start_balance` depuis accounts.yaml, `daily_start_balance` persistant entre refreshes, rollover UTC à minuit. Sinon floating P&L = faux DD → faux EMERGENCY

---

## Prochaines étapes

### Immédiat
- [x] ~~Corriger notifications Telegram~~ (fait 2026-03-27 — token manquait préfixe numérique)
- [ ] Augmenter risk à 0.80% une fois compte stabilisé

### Court terme
- [ ] Corrélation inter-positions : facteur par catégorie pour le guard open_risk
- [x] ~~Activer GFT compte1~~ (fait 2026-03-23 — enabled, protected: false, 36 instruments mappés)
- [ ] Ajouter support `--session` CLI pour Fouetté (passer london/ny/tokyo)

### Bugs connus
- GFT compte idle : tous les signaux actuels sont crypto, non disponibles sur GFT. Ajouter forex/métaux aux assignments GFT.
- Compte 46570880 et 46738849 encore visibles via API cTrader (anciens tests, ne pas utiliser)

### Bugs corrigés (2026-03-27)
- [x] Telegram notifications — token Apprise manquait préfixe `8427362376:`
- [x] Faux EMERGENCY DD -7.3% — DD calculé sur floating P&L au lieu de balance réelle (fix: persistent DD tracking)
- [x] Warning "lot sous-évalué" faux positif — utilisait yaml pip_val au lieu de broker pip_val

### Bugs corrigés (2026-03-26)
- [x] TradeLocker order_id ≠ position_id → positions orphelines sans SL/TP
- [x] TradeLocker `amend_position_sltp` manquant (`set_position_protection` n'existe pas dans TLAPI)
- [x] Sizing XAUUSD GFT 0.01L au lieu de 0.08L (pip_size broker vs yaml)

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
# Lancer le moteur live
nohup .venv/bin/python -m arabesque.live.engine > /tmp/arabesque_live.log 2>&1 &

# Surveiller
tail -f /tmp/arabesque_live.log
grep "💰\|🔒\|🛡️\|🚨\|⚠️" /tmp/arabesque_live.log | tail -20

# Backtest
python -m arabesque run --strategy extension --mode backtest XAUUSD BTCUSD
python -m arabesque walkforward --strategy extension --universe crypto
python -m arabesque run --strategy glissade --mode backtest XAUUSD BTCUSD

# Comparaison live vs backtest (~1×/semaine)
python scripts/compare_live_vs_backtest.py --last 7
python scripts/compare_live_vs_backtest.py --period this_week

# Fetch manuel (si parquets en retard)
python -m arabesque.data.fetch --start $(date -d "4 days ago" +%Y-%m-%d) --end $(date +%Y-%m-%d) --derive 1h 4h
```

---

## Service systemd — fetch OHLC quotidien

Mise à jour automatique des parquets chaque jour à 06:30 UTC.
Fichiers sources dans `deploy/systemd/`.

### Réinstaller (nouvelle machine ou après `git clone`)

```bash
bash scripts/install_service.sh

# Pour que le timer tourne hors session active (sudo requis) :
sudo loginctl enable-linger "$USER"
```

### Vérifier / opérer

```bash
systemctl --user list-timers arabesque-fetch.timer   # prochaine exécution
systemctl --user start arabesque-fetch.service        # lancer maintenant
journalctl --user -u arabesque-fetch.service -f       # logs en direct
systemctl --user status arabesque-fetch.timer         # état
```
