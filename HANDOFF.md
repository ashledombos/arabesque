# ARABESQUE — Handoff

> **Pour reprendre le développement dans un nouveau chat.**
> État live courant → `docs/STATUS.md`. Décisions techniques → `docs/DECISIONS.md`.
>
> Dernière mise à jour : 2026-04-19 (bilan semaine 16)

---

## État en un coup d'œil

```
Live actif (compte ftmo_challenge, account_id 45667282, démo cTrader) :
  Extension H1  → XAUUSD, GBPJPY, AUDJPY, CHFJPY (risk 0.45%)
  Extension H4  → 27 crypto (BTCUSD, ETHUSD, BNBUSD, SOLUSD…) (risk 0.55% via TF multiplier)
  Glissade H1   → XAUUSD, BTCUSD (LIVE — WF 3/3 PASS, WR 83%, Exp +0.147R)

Balance FTMO : ~94 309$ (DD -5.7%) — protection NORMAL (seuils DD relevés 2026-04-15)
Rodage Glissade : risk × 0.50 (config/settings.yaml → rodage.strategies)
Balance GFT  : ~142 742$ (DD -4.8%) — protection NORMAL

⚠️ DD THRESHOLDS RELEVÉS 2026-04-15 : CAUTION -5%→-7%, DANGER -6.5%→-8%, EMERGENCY -8%→-9%
  Ancien seuil -5% piégeait le compte en CAUTION permanente (risk ×0.50 = ~$14-80/trade
  au lieu de ~$424), recovery trop lente pour remonter. Simulation sur 31 trades live :
  max DD 4.5% à 0.45% constant → pas de breach. Weekend crypto guard + 1% de marge FTMO.

⚠️ PHANTOM EXITS FIX 2026-04-15 : position_monitor exige maintenant get_closed_position_detail()
  avant de déclarer une position fermée. Si pas de confirmation broker, compteur de cycles
  d'absence (fallback après 3 cycles ≈ 6 min). Évite les faux exits quand get_positions()
  retourne une liste incomplète (race côté broker GFT/cTrader).

⚠️ INCIDENT 2026-04-09 : reboot machine → moteur aveugle 2j (résolu 2026-04-11)
  Cause : DNS failure au boot → cTrader hors broker list → BarAggregators sans preload
  Trades manqués : ~2-3j de signaux (9-11 avril)
  Impact : aucune perte, mais opportunités manquées

⚠️ GAP WEEKEND cTrader — confirmé récurrent (ETHUSD 12/04, DASHUSD 04/04)
  cTrader ferme les CFD crypto vendredi soir (14h-23h UTC variable)
  Le marché Binance continue 24/7 → gap à la réouverture dimanche
  ETHUSD : SL 2260.70 → fill 2222.66 = -1.69R au lieu de -1.00R (-$62 extra)
  167 événements feed stale le samedi vs ~5 les autres jours
  Protection : weekend_crypto_guard activé (settings.yaml) — bloque crypto cTrader vendredi >= 15h UTC

Rodage (risk × 0.50, activé 2026-03-28) :
  Fouetté M1    → XAUUSD London, BTCUSD NY (WF 4/4 PASS)
  Cabriole 4H   → BTCUSD, ETHUSD, SOLUSD, DOGEUSD, LINKUSD, ADAUSD (WF 6/6 PASS, 73-95% overlap Extension)

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
| Protection active | LiveMonitor 4 paliers (EMERGENCY = lot min, pas close all) | execution/live_monitor.py |
| Per-TF risk multiplier | H4 → ×1.22 | settings.yaml (risk_multiplier_by_timeframe) |
| Rodage | Glissade, Fouetté, Cabriole ×0.50 | settings.yaml (rodage.strategies) |
| Corrélation | même catégorie ×0.70/0.50/0.35 | order_dispatcher.py |
| Environnement cTrader | **Démo** (is_demo: true) | settings.yaml + accounts.yaml |

### Architecture credentials

Tokens OAuth stockés UNE SEULE FOIS dans `secrets.yaml → ctrader_oauth`.
App OpenAPI "arabesque" (client_id 23710), basculé 2026-03-28 (anciens tokens gardés dans `ctrader_oauth_old`).
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
- **pip_size varie entre brokers** : TradeLocker reporte en points (0.0001), cTrader en pips (0.01). `_compute_lots_for_broker` rescale automatiquement le `pip_value_per_lot` yaml par le ratio `broker_pip_size / yaml_pip_size` pour maintenir la cohérence cross/fallback
- **DD tracking doit être persistant** : `start_balance` depuis accounts.yaml, `daily_start_balance` persistant entre refreshes, rollover UTC à minuit. Sinon floating P&L = faux DD → faux EMERGENCY
- **Position monitor state persisté** : `save_state()` sur SIGTERM, `load_state()` au restart. Sans ça, MFE/BE/trailing perdus → positions à MFE 0.5R repartent sans BE
- **EMERGENCY = protection intelligente** : pas de close all (réalise les pertes). Triage par P&L courant : positive → BE immédiat, 0/-0.5R → SL serré -0.3R, -0.5/-0.7R → fermer, < -0.7R → laisser (trop proche SL)
- **Gap weekend cTrader crypto = risque systémique** : cTrader ferme les CFD crypto le vendredi (14h-23h UTC, variable par instrument). Le marché Binance continue 24/7. Si le prix bouge pendant le weekend, le SL s'exécute au premier prix de réouverture = gap slippage. Confirmé ETHUSD (-1.69R au lieu de -1.00R) et DASHUSD (gap 29.55→30.31). Guard implémenté : pas de nouvelle position crypto cTrader le vendredi après 15h UTC. GFT (TradeLocker) non affecté (feed continu).
- **Distribution bimodale des trades** : 68% BE wins (+0.20R), 19% SL losses (-1R), 9% runners (>+1R). Les runners génèrent 159% du P&L net — toute modification qui les empêche (TP fixe, ROI agressif) détruit l'edge. La zone +0.2R à +1R est quasi vide (4%)
- **Cooldown 5 barres optimal** : testé cd5/cd2/cd0/dégressif — plus de trades sans cooldown (+27%) mais Exp chute de +0.093R à +0.043R. Les trades supplémentaires sont de mauvaise qualité
- **Sorties MR incompatibles trend-following** : BB_RPB_TSL (MR, SL=-99%) uses RSI extreme / ROI court / momentum surextensif pour couper les profits. Avec SL réel -1R, ces mêmes mécanismes tuent les runners. Testé 11/13 éléments : tous NEUTRE ou REJETÉ (2026-03-28)
- **Vol-targeting inadapté aux BB breakout** : haute vol = notre signal d'entrée → réduire le risk en haute vol tue l'edge. Testé 5 configs, toutes négatives (-10 à -12R). La normalisation ATR au sizing suffit (2026-03-28)

---

## Prochaines étapes

### Immédiat
- [x] ~~Corriger notifications Telegram~~ (fait 2026-03-27 — token manquait préfixe numérique)
- [x] ~~Fix double-comptage rapports~~ (fait 2026-04-06 — dédupliqué par trade_id dans daily_report, compare_live_vs_backtest, health_check, live_monitor._load_journal)
- [x] ~~Fix exits manquants au redémarrage~~ (fait 2026-04-06 — _reconcile_missed_exits() dans live.py scanne le journal au démarrage)
- [x] ~~Résilience reboot~~ (fait 2026-04-12 — retry 5x backoff dans _connect_brokers)
- [x] ~~Alerte moteur aveugle~~ (fait 2026-04-12 — check_engine_processing_bars dans health_check.py)
- [x] ~~Auto-close orphelins GFT~~ (fait 2026-04-12 — fermeture auto sans SL après 120s grace dans position_monitor)
- [x] ~~Notifs Telegram digestes~~ (fait 2026-04-12 — startup compact, CAUTION/NORMAL 1 ligne, drift ne notifie que si dérive, rapport quotidien compact + activité stratégies)
- [x] ~~Weekend crypto guard FTMO~~ (fait 2026-04-12 — bloque nouvelles positions crypto sur cTrader vendredi >= 15h UTC, JSONL logging dans logs/weekend_crypto_guard.jsonl)
- [x] ~~Vrai prix de sortie broker~~ (fait 2026-04-13 — position_monitor interroge broker.get_closed_position_detail() au lieu d'estimer au SL théorique. cTrader via ProtoOADealListReq, TradeLocker via get_all_orders(history=True). Fallback sur estimation si broker call échoue.)
- [x] ~~Fix phantom exits~~ (fait 2026-04-15 — reconcile() exige corroboration get_closed_position_detail avant de retirer une position. Fallback 3 cycles d'absence ≈ 6 min.)
- [x] ~~DD thresholds relevés~~ (fait 2026-04-15 — CAUTION -7%, DANGER -8%, EMERGENCY -9%. L'ancien -5% piégeait en CAUTION permanente.)
- [x] ~~Strategy rename trend→extension~~ (fait 2026-04-15 — signal.py, bar_aggregator, order_dispatcher, live_monitor baselines)
- [x] ~~cTrader reconnect retry ALREADY_LOGGED_IN~~ (fait 2026-04-15 — retry 5× backoff 30-120s pour sessions fantômes après coupure de courant)
- [ ] Augmenter risk quand data suffisante (voir critères ci-dessous)

### Court terme
- [x] ~~Corrélation inter-positions~~ (fait 2026-03-27 — discount 0.70/0.50/0.35 par catégorie dans order_dispatcher)
- [x] ~~Activer GFT compte1~~ (fait 2026-03-23 — enabled, protected: false, 36 instruments mappés)
- [x] ~~Graceful SIGTERM~~ (fait 2026-03-27 — save_state/load_state position_monitor, MFE+BE+trailing persistés)
- [x] ~~Rapports automatisés~~ (fait 2026-03-27 — daily 21h UTC + weekly dim 20h UTC via systemd timers)
- [x] ~~Feuille de route BB_RPB_TSL~~ (fait 2026-03-28 — 11/13 éléments testés, tous NEUTRE ou REJETÉ. Le système actuel est le bon profil. Voir `docs/EXPERIMENT_LOG.md` section 6)
- [ ] Ajouter support `--session` CLI pour Fouetté (passer london/ny/tokyo)
- [x] ~~Implémenter protection slippage entrée (H4)~~ (fait 2026-03-28 — `order_dispatcher.py` check ATR-normalized slippage at trigger, counterfactual + JSONL logging, seuil configurable `max_slippage_atr: 0.5`)
- [x] ~~Explorer volatility targeting~~ (fait 2026-03-28 — REJETÉ, haute vol = notre signal d'entrée, réduire risk en haute vol tue l'edge. Voir `docs/EXPERIMENT_LOG.md` section 7)
- [x] ~~Guard counterfactual tracking~~ (fait 2026-03-30 — backtest+WF logguent les counterfactuals par guard : cooldown, bb_squeeze, slippage, spread, duplicate_instrument. Affichage agrégé dans WF report et compare_live_vs_backtest)
- [x] ~~Valider cooldown optimal~~ (fait 2026-03-30 — cd5 confirmé meilleur que cd2/cd0/dégressif. Voir `docs/EXPERIMENT_LOG.md` section 8)

### Critères pour augmenter le risque (walk-forward live)

```
ÉTAT AU 2026-04-11 : 33 trades loggés (après dédupliquation)
WR observé : 54.5% (18W/15L) — drift significatif vs baseline 75%
Exp observée : -0.285R — négatif, mais petit échantillon (IC99 large)
Rythme : ~10 trades/semaine (hors incident 9-11 avril)
Note : 68% des wins sont des BE exits (+0.20R), conforme distribution bimodale
```

| Palier | Condition | Trades estimés | Date estimée | Action |
|---|---|---|---|---|
| **P0** | IC99 WR > 50% | ~50 | ~fin avril 2026 | Confirmation edge existe |
| **P1** | IC99 WR > 60% + Exp IC95 > 0 | ~80 | ~mi-mai 2026 | Risk plancher à $100/trade min (anti-rounding) |
| **P2** | IC99 WR > 65% + Exp IC99 > 0 | ~150 | ~juillet 2026 | Réduire agressivité DD linéaire (0.60 au lieu de 0.10 plancher) |
| **P3** | 200+ trades, WR et Exp stables 3 mois | ~200 | ~août 2026 | Risk plein 0.40%, nouveau compte si disponible |

**Règle anti-slippage** : ne jamais descendre sous $100/trade de risk.
En-dessous, le lot rounding (±$5) mange >25% du gain moyen (+0.20R).
Si DD linéaire donne <$100, appliquer un plancher à $100 ou skip le trade.

### Sujets à traiter (prochaine session)

1. **Journal de trading global** �� Mettre en place un journal de trading structuré (local, gitignored) qui centralise les trades, les événements marquants, les décisions manuelles. Pas un trade_journal.jsonl machine, mais un journal humain lisible. À garder en local (.gitignore) car contient des données personnelles de trading. Format suggéré : Markdown mensuel dans `logs/journal/` (gitignored).

2. **Filtre news haute importance** — Récupérer les dates de news éco (NFP, FOMC, CPI…) et bloquer le trading ±5min autour. API candidates : ForexFactory RSS, Investing.com calendar, FXStreet. Implémentation : `is_high_impact_news(now, buffer_minutes=5)` dans guards.py, appelé avant le dispatch. Réduit le besoin d'un compte swing (les news sont le seul autre cas de gap intra-semaine).

3. **Stratégies non-Extension : pas de bug** — Diagnostic 2026-04-12 : Cabriole (0 breakout Donchian depuis 28/03), Glissade (0 divergence RSI), Fouetté (917 tentatives filtrées par EMA shadow). Conditions de marché défavorables, pas un bug. Surveiller via le rapport quotidien ("Inactif: cabriole: Xj").

### Bugs connus
- GFT ne reçoit que les signaux H1 forex/métaux (XAUUSD, GBPJPY, AUDJPY, CHFJPY) — les crypto H4 ne sont pas disponibles chez GFT. Normal, pas un bug.
- Compte 46570880 et 46738849 encore visibles via API cTrader (anciens tests, ne pas utiliser)
- **BarAggregator sans preload si broker déconnecté au boot** — ATTÉNUÉ (2026-04-12) : `_connect_brokers()` retry 5x avec backoff (5-60s). Le health check détecte aussi l'absence de barres (check_engine_processing_bars). Edge case restant : si le broker est injoignable > 2min, le moteur démarre quand même sans.
- **GFT positions orphelines** — ATTÉNUÉ (2026-04-12) : auto-close des orphelins sans SL après 120s de grâce. Le mapping order_id → position_id échoue encore parfois chez TradeLocker.

### Bugs corrigés (2026-03-28)
- [x] Sizing cross pairs GFT (AUDJPY 0.010L au lieu de 0.320L) — TradeLocker pip_size en points (0.0001) vs yaml en pips (0.01) → yaml pip_value_per_lot non rescalé. Fix: ratio automatique `broker_pip_size / yaml_pip_size` sur pip_value dans les paths cross et fallback.

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
# Moteur live (service systemd — auto-restart, journald)
systemctl --user start arabesque-live
systemctl --user stop arabesque-live
systemctl --user restart arabesque-live
systemctl --user status arabesque-live

# Surveiller
journalctl --user -u arabesque-live -f
journalctl --user -u arabesque-live --since '1 hour ago'
journalctl --user -u arabesque-live | grep '💰\|🔒\|🛡️\|🚨\|⚠️' | tail -20

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

## Services systemd

Fichiers sources dans `deploy/systemd/`. Installés via `bash scripts/install_service.sh`.

| Service | Type | Rôle |
|---|---|---|
| `arabesque-live` | persistent (auto-restart) | Moteur de trading live |
| `arabesque-fetch.timer` | timer quotidien 06:30 UTC | Mise à jour des parquets |
| `arabesque-report-daily.timer` | timer quotidien 21:00 UTC | Rapport quotidien + drift check multi-stratégie + health check (13 checks) |
| `arabesque-report-weekly.timer` | timer dimanche 20:00 UTC | Rapport hebdomadaire + drift check |

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
