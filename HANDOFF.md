# ARABESQUE — Handoff

> **Pour reprendre le développement dans un nouveau chat.**
> État live courant → `docs/STATUS.md`. Décisions techniques → `docs/DECISIONS.md`.
>
> 🎯 **OBJECTIF PRINCIPAL : vérifier que l'edge backtest se reproduit en live** (la perf est secondaire).
> **Contrat de validation portable (humain/agent) : `docs/VALIDATION_CONTRACT.md` + `config/validation_policy.yaml`.** Toute proposition de hausse de risque doit lire ces deux fichiers avant les anciens comptes rendus.
> **À lire en premier dans toute session de bilan/suivi : `logs/edge_audit_latest.md`** — état actuel de l'edge par stratégie, sans refaire l'analyse. Rafraîchir si > 24h via `python scripts/audit_edge_live_vs_backtest.py`.
>
> Derniere mise a jour : 2026-05-29 (soir) - **Patch BE observabilite + equity cTrader cross-currency charge en live**. Suite a la semaine perdante, l'audit a separe regime defavorable et execution : `replay_live_vs_theory --since 2026-05-26` donne `meanDelta=-0.108R`, avec XRPUSD FTMO comme cas BE theorique non arme live. Commit `968280f` pousse puis restart controle : FTMO AUDJPY ouverte et protegee (`SL=114.321`, `TP=114.937`), GFT flat, `31/31`, moteur pret, BE polling actif. Le polling BE emet maintenant `be_polling_decision` par position evaluee (`armed`, `not_eligible`, `eligible_not_armed` + raison) ; premier event live AUDJPY = `not_eligible/mfe_below_trigger`. Equity cTrader corrigee via `pip_size`/`pip_value_per_lot` calibres de `instruments.yaml` : faux `DANGER` AUDJPY supprime (`equity` FTMO ~`93172.96`, total DD ~`-6.83%`, `NORMAL`). Patch additionnel local a committer : watchdog flat-only, lit les positions a chaque cycle et bloque l'auto-restart si position ouverte (`manual_required_open_positions`, alerte urgente seulement). Tests : `318 passed, 1 warning`.
> 2026-05-26 (soir) - **Incident GFT position-state corrige + relecture guards live**. `AUDJPY` est restee ouverte hors monitor apres un HTTP 429 traite comme liste vide puis un faux fill de sortie pris sur l'ordre d'entree ; cloture operateur @ 114.167 et exit reel reconcilie `-0.201R`. `XAUUSD` pending rempli etait invisible car TradeLocker utilise `order_id != position_id`, puis auto-ferme orphelin ; trade restaure `+1.178R` / `MFE=1.79R`. Patch position-state : erreurs `get_positions` deviennent unknown, validation stricte du close fill, resolution pending order->position, restauration SL/TP via ordres lies au restart, correction lifecycle health loop. Relecture : `health_report` melangeait niveau global/equities multi-broker ; correction codee. `cabriole` desactivee conservait 8 pertes consecutives et maintenait a tort `DANGER` ; guard rescope a Extension/Glissade/Fouette (etat attendu apres chargement : `CAUTION`, car Glissade reste a 5 pertes). `OrderDispatcher` evaluait les ordres GFT sur l'etat/config FTMO et n'activait pas `worst_case_budget` en live : gate par broker fail-closed ajoute, avec risk_cash par compte. Relecture complementaire : les pending STOP/LIMIT sont desormais reserves dans l'exposition/daily slots ; toute lecture broker/journal inconnue ou pending broker non trace invalide le gate ; expiration 24h exige confirmation broker ; les messages startup ne peuvent plus annoncer `0 position` sur lecture echouee. **Moteur volontairement arrete : redemarrage a arbitrer explicitement car charger la correction de scope Cabriole ferait passer le guard attendu de `DANGER x0.25` a `CAUTION x0.50`.** Dossier : `docs/INCIDENT_GFT_POSITION_STATE_2026-05-26.md`, decisions du 26 mai.
> 2026-05-27 - **Maintenance cloturee et reprise controlee**. Apres coupure reseau/reboot, `arabesque-live.service` avait demarre seul a 10:04 CEST sans atteindre `Moteur pret` (DNS puis cTrader `ALREADY_LOGGED_IN`/timeouts) ; arrete/desactive a 10:07 avec comptes plats. Correction startup livree (`38b7277`, cleanup timeout + grace retry cTrader), puis correction guard pertes miroir par broker (`8d3819f`). Audit sizing : GFT Glissade XAUUSD du 19/05 demandait `4.82$` mais le minimum imposait `46.01$` (`9.54x`) ; barriere pre-envoi `max_executed_risk_ratio=1.25` livree (`deb29d0`, `286 passed`). Reprise apres verification FTMO/GFT `0 position / 0 pending` : service re-enabled/start a `15:41:56 CEST`, PID `75323`, cTrader sain, `31/31`, moteur pret `15:42:59`, watchdog OK et health report `CAUTION`. FTMO individuel `NORMAL`, GFT `CAUTION` via Glissade streak=5 ; politique pire broker = risk effectif `x0.50`. L'ALERT invariant FTMO `be_inferred_but_loser` est l'incident historique XAUUSD 14/05 conserve comme preuve, pas un nouveau blocage.
> 2026-05-27 - **Incident en position ouverte : legs GFT classes comme pending inconnus + collision reconcile cTrader**. A `16:00 CEST`, Extension a ouvert `XAUUSD SHORT` sur FTMO (`31.12$`) et GFT (`72.75$`) ; lecture broker GFT confirme que la position est physiquement protegee (`SL=4458.85`, `TP=4364.54`). Le refresh risque traitait pourtant ces deux ordres lies SL/TP comme des entries inconnues et invalidait GFT toutes les 2 minutes. A `18:00 CEST`, cette invalidation a bloque le signal `BTCUSD SHORT` Glissade sur GFT tandis que FTMO l'a execute (`7.71$`) : **1 execution GFT manquee par bug**, a conserver dans l'audit live/theorie. En parallele, FTMO produisait des `cTrader pending orders reconcile timeout` car le refresh et le heartbeat utilisaient concurremment la meme cle Future `reconcile`. Fix pousse `774f7ca` : conserver `position_id`/type/prix des legs TradeLocker et les exclure des pending d'entree seulement si leur position est broker-confirmed ; serialiser les `ProtoOAReconcileReq` cTrader. Suite initiale `289 passed`.
> 2026-05-27 - **Restart controle et deux corrections complementaires charges**. La consigne initiale de ne pas restart en position a ete revisee : le runtime ancien avait deja provoque une divergence d'execution (BTC FTMO pris, GFT bloque) ; les SL/TP XAU etant confirmes serveur-side, un restart controle est le test adapte de la reprise. Pendant l'arret, l'audit FTMO a montre que le BE BTC pose a `18:21` avait efface le TP serveur (`tp=0.0`) : `amend_position_sltp` recoit desormais le TP existant lors de tout amend SL (`3b0fb49`). BTC s'est ferme pendant l'arret et a ete reconcilie a `+0.193R` / `+$1.49`. Le premier reboot a ensuite revele que les positions existantes etaient restaurees dans `PositionMonitor` mais pas dans `LiveMonitor` (`HEALTH ... 0 ouverts`) ; correctif `57f7ca4` recharge les entries ouvertes confirmees broker sans doubler le journal. Restart final `19:39:08 CEST`, PID `123084` : `31/31`, XAU FTMO+GFT proteges/restaures, logs `Entry ouverte restauree` x2, `HEALTH [caution] ... 2 ouverts`, aucun faux pending GFT ni timeout reconcile observe au cycle suivant. Suite `291 passed`.
> 2026-05-27 - **Securite ordre GFT livree et chargee en live**. Le patch `44755a8` impose une quote REST TradeLocker pre-envoi, refuse une derive defavorable `>0.25R` ou le seuil ATR, verifie les SL/TP lies immediatement apres fill, tente un amend unique puis met en quarantaine les nouvelles entrees GFT si la protection ne peut etre confirmee ; un fill extreme reste journalise/monitore au lieu d'etre abandonne. Le `protection_check` rejoint le journal ; `broker_guard_rejects.jsonl` est lu par le replay. A `23:21 CEST`, controle broker : FTMO/GFT `0 position / 0 pending`, alors que l'ancien PID journalisait `cTrader not connected while reading pending orders` et `30/31` actifs ; restart controle `stop`, attente `60s`, `start` a `23:22:59 CEST`, PID `165502`, commit `e091571` charge : `31/31`, refresh token `12h`, reconciliation sans position ouverte, moteur pret et watchdog OK. Doctrine centralisee dans `docs/VALIDATION_CONTRACT.md`; shadow reference complet reste requis avant affirmation de rentabilite/hausse de risque.
> 2026-05-27 - **Routage notification clarifie**. Telegram est le flux exhaustif ; ntfy devient exclusivement un reveil d'intervention urgente. Helper commun `arabesque/notifications.py` applique aux rapports/timers/replays/watchdog/PriceFeed : routine (`restart`, `CAUTION`, `/suivi`, rapport, drift, feed restaure ou premier stale sous auto-restart) = Telegram seul ; urgence (`DANGER`/`EMERGENCY`, protection/fill/amend broker, auto-restart feed ou echec/anti-boucle, health `CRITIQUE`) = Telegram + ntfy.
> 2026-05-28 - **Integrite execution/feed/shadow reference preparee**. Patch local a tester/committer : `risk_integrity_check` post-fill (sous-risque = Telegram + calibrage prochain ordre ; sur-risque >1.25x = quarantaine/urgent ; >1.50x = demande de cloture immediate), tolerance TradeLocker sur SL/TP arrondis, watchdog externe capable de signaler un `PriceFeed` partiel (`30/31 actifs`) sans restart, et wrapper read-only `scripts/shadow_reference_check.py` pour lancer ensemble live/theorie et signaux/theorie. Aucun changement edge/risk volontaire ; code a charger uniquement apres suite verte et restart controle.
> 2026-05-29 - **Observabilite offset/uptime/BE preparee**. Ajouts locaux : `logs/gft_quote_coherence.jsonl` mesure chaque pre-vol GFT vs reference cTrader (offset prix/R/ATR, spread, allow/block) sans correction automatique ; `logs/uptime_events.jsonl` ecrit par le watchdog + `scripts/audit_uptime_events.py` pour calculer un facteur uptime et les causes de degradation ; `be_polling_pass` journalise chaque passage du filet BE avec `checked/armed/skipped/skip_reasons`. Pas de Pas de Deux, pas de feed secondaire comme source d'ordres.
> 2026-05-22 — **Fix désynchro refresh_token (task #32 fermée)**. Patch `_refresh_access_token` cTrader : sur `ACCESS_DENIED` (HTTP 200 + errorCode ou status 400/401), relit `config/secrets.yaml` via nouveau helper `load_broker_tokens(broker_id)` ; si le refresh_token disque diffère de l'in-memory, adopte et retente l'appel HTTP une unique fois (anti-récursion via `_disk_fallback_done`, boucle bornée à 2 itérations pour éviter le deadlock du `_token_lock` non-réentrant). Tests : 12 ajoutés (`tests/test_ctrader_token_disk_fallback.py`), couvrant : ACCESS_DENIED + disque frais → adoption + retry réussit ; ACCESS_DENIED + disque identique → no-retry (token vraiment mort) ; ACCESS_DENIED + secrets.yaml absent → no-retry ; status 400/401 → fallback aussi ; erreur réseau / 500 → pas de fallback (pas un token problem) ; `_disk_fallback_done=True` court-circuite la 2e lecture. **Suite complète 122/122 verts** (110 + 12). Reste tasks #31 (étages 2/3/4) + #33 (feed_stale dans /suivi).
> 2026-05-22 — **Incident feed FTMO refresh_token (2026-05-21T22:59 → 2026-05-22T18:55 UTC, 19h54)**. PriceFeed cTrader FTMO down 19h54 cause `ACCESS_DENIED` sur refresh OAuth, alors que tokens disque rafraîchis à 05:12 UTC restaient valides (`positions` CLI fonctionnel). Désynchro mémoire/disque. **DASHUSD #53110148 clôturée broker-side** sur SL initial 46.70 (PnL -256.66$ ≈ -1R, MFE +1.86R jamais converti en BE car bid n'a jamais franchi 49.94). Reconciled au restart 18:55 UTC (PID 1150053 → 1435589). Verdict patch 0+1 : invariant tenu sur la fenêtre 11:50→22:59 UTC le 21/05 (0 trigger trading). Doc dossier vivant : `docs/INCIDENT_DASHUSD_RESILIENCE_BROKER_2026-05-21.md` (sections 6+7+8 mises à jour).
> 2026-05-21 — **Patch résilience broker étages 0+1 (incident DASHUSD)**. Incident 2026-05-20T22:00 → 2026-05-21T01:42 UTC : position DASHUSD #53110148 (FTMO BUY @ 49.40, SL 46.70, MFE peak 1.82R / 54.33) avec amend BE/TSL en boucle "Not connected" pendant ~10h. Cause : canal Protobuf trading cTrader passé silencieusement à `_connected=False` (sans erreur loguée), feed quote resté vivant. Patch token P1+P2 (`79007cf`) ne couvre pas ce cas. **Étage 0** : notif Telegram+ntfy à chaque `ABANDONED` amend SL (cooldown 30 min/position) — `position_monitor.py` + branchement `live.py`. **Étage 1** : reconnect-on-demand avant chaque retour "Not connected" sur les 4 méthodes broker (place/cancel/amend/close), réutilise `self.connect()` (héritage retries P1+P2), anti-boucle cooldown 30s + fenêtre glissante 3/60s — `ctrader.py`. Tests : 17 ajoutés (6 monitor + 11 broker), **suite complète 110/110 verts**. Étages 2/3/4 (healthcheck proactif, auto-restart systemd, anti-boucle guard) **différés après 24-48h d'observation** — décision factuelle au prochain `/suivi`. Doc dossier vivant : `docs/INCIDENT_DASHUSD_RESILIENCE_BROKER_2026-05-21.md`.
> 2026-05-20 — **Audit fréquence Extension + ventilation non-live** (diagnostic only, aucun patch). Audit fréquence : `recent_squeeze` chute 50 %→34 %→10 % des bougies de W1 à W4 ; tous les autres filtres Extension (bb_expanding, adx, EMA, CMF, breakout BB) gardent des taux de passage stables. La baisse de fréquence Extension vient du **régime**, pas d'un guard caché. Ventilation W3+W4 : sur sessions dédupées, ratio live/théo Extension = **32 % W3** (8 live / 25 sessions), aligné W1/W2 (~33 %) — pas de drift. 22 signaux non-live W3 entièrement expliqués : 64 % engine OFF (gaps journal 36h le 05-05, 80h le 08-10-05), 23 % feed dégradé / cross-strat, 9 % weekend guard, 5 % broker_specific GFT, **0 inexpliqué**. Règle de lecture actée dans DECISIONS 2026-05-20 : **ratio live/théo Extension se lit sur sessions dédupées (cible 30-35 %)**, pas sur bougies brutes. Patch cTrader token P1+P2 livré (commit `79007cf`, refresh préventif 12h, invalidation `_shared_tokens` sur `CH_ACCESS_TOKEN_INVALID`, 93/93 tests verts). Trou disponibilité engine W3 détecté en parallèle — pas patché, à monitorer via `event_silence_gap_h_p95` plus tard.
> 2026-05-19 — **Patch observabilité Phase 4** (P1 dédup multi-broker `(trade_id, broker_id)` dans `replay_live_vs_theory.py` ; P2 résilience `_account_refresh_loop` try/except WARNING par await ; P3 fallback legacy `be_inferred_but_loser` pour records pré-fix `be_source` du 2026-05-17). Tests : 15 ajoutés (5+4+6), suite complète verte. Phase 4 bis scope formalisé (Extension + Glissade only depuis 2026-05-16, cabriole/trend legacy exclus du critère ramp — cf `docs/DECISIONS.md` 2026-05-19). Recalcul après dédup : Phase 4 n=8→11 (+3), meanΔR=-0.031R→-0.061R ; Phase 4 bis n=1→2 (+1), meanΔR=-0.200R→-0.111R. Aucun changement signal / risk / execution / order.
> 2026-05-18 — **Phase 4 bis active depuis 2026-05-16** (noyau Extension + Glissade, Cabriole désactivée). Phase 2.5 BE polling broker-side activé (surveillance 24-48h). Bot Telegram interactif phase 1 (lecture seule) en service. Infra fix 2026-05-16 : services `arabesque-report-daily` + `arabesque-suivi-reminder` repassés `success` (`health_check --warn-only` exit 0 + fallback `_load_apprise()` venv). Scripts BT sensibilité + rolling baseline distribution ajoutés (cf `scripts/recalibrate_bt_sensitivity.py`, `scripts/rolling_baseline_distribution.py`). Audit cTrader FTMO Challenge environnement documenté (`docs/CTRADER_ENV_AUDIT_2026-05-16.md`). **Watchdog feed v1 actif** (détection panne silencieuse, pas d'auto-restart — explicitement différé en Phase 4, cf patch ALREADY_LOGGED_IN ci-dessous). Incident PriceFeed 2026-05-14 09:47→17:23 UTC (7h36 boucle Feed stale ETHUSD) — Glissade XAUUSD -1R imputable. **Récidive 2026-05-18 ~03:00→05:30 UTC** (cTrader bloqué ALREADY_LOGGED_IN ~2h, 0 signal manqué — marché asiatique calme). 2 signaux Fouetté M1 le 13-05 non tirés (à investiguer). **Patch be_source 2026-05-17** : nouveau champ `broker_armed` (live amend success) / `broker_evidence` (reconcile exit≈be_target) / `inferred_from_mfe` (MFE parquet seul) / `not_armed` distingue preuve directe, indirecte et inférence ; invariant `be_inferred_but_loser` ajouté (cf bug XAUUSD 14-05). **Patch ALREADY_LOGGED_IN A+B 2026-05-18** (`arabesque/broker/ctrader.py`, commit 39c26d3 push origin/main) : (A) délais retry allongés `(60, 180, 600)s` (14 min, vs 7.5 min précédents — sous TTL serveur cTrader) ; (B) nouvelle `_cleanup_for_retry()` unsubscribe + stopService + reset état avant chaque retry, équivalent au stop opérateur manuel. Auto-restart watchdog explicitement différé (papier collant + risque interruption trade). Couverture : `tests/test_ctrader_already_logged_in_retry.py` (6 tests, 61 total passent). **Incident 2026-05-18 17:18:56→19:13:27 UTC (1h54)** : BTCUSD stale 6766s, 45 reconnects tous "Réutilisation du broker existant" — bug distinct du ALREADY_LOGGED_IN, situé branche `if` de `_connect_and_subscribe` (le flag `_broker._connected=True` masque un TCP zombi). Restart opérateur 19:13:27 UTC, coût trade = 0 (0 entry/exit/position pendant la fenêtre, 0 signal manqué via replay). **Revue diagnostic** dans `docs/REVIEW_BTCUSD_STALE_EXISTING_BROKER_2026-05-18.md` (review only, pas de patch — 3 options présentées, à débattre). **Audit pipeline validation 2026-05-15 appliqué** : timeframe unifié dans scripts d'audit (#1), 9 aliases CCXT_MAP (#2), strict-data mode `--allow-yahoo` sur replay_signals_vs_live + replay_live_vs_theory (#3), tests cohérence config↔validation (#7, 7 tests), dryrun.py marqué legacy (#5a), pytest config (20 tests OK). meanΔR Phase 4 brut : -0.178R → -0.007R post-fix.

---

## État en un coup d'œil

```
🟢 LIVE ACTIF — restart controle 2026-05-27 23:22:59 CEST, PID 165502
  Validation : FTMO/GFT 0 position / 0 pending avant start ; 31/31 souscrit ; moteur pret ; watchdog OK.
  Gardes chargees : min/step broker >1.25x bloque ; pre-vol/post-fill GFT actif ; GFT CAUTION, risque systeme x0.50.
  Positions live observees au restart : aucune.
  Surveillance immediate : retour de deconnexion cTrader/flux stale ; premiers rejets ou protection_check GFT sous le nouveau code.

Stratégies préalablement actives (à reprendre après diagnostic) :
  Extension H1  → XAUUSD, GBPJPY, AUDJPY, CHFJPY (risk 0.45%)
  Extension H4  → 27 crypto (BTCUSD, ETHUSD, BNBUSD, SOLUSD…) (risk 0.55% via TF multiplier)
  Glissade H1   → XAUUSD, BTCUSD (LIVE — WF 3/3 PASS, WR 83%, Exp +0.147R)

Balance FTMO : 93 298.21$ (DD -6.70%) — aucun LiveMonitor actif (moteur arrêté)
Rodage/guard à la reprise : niveau attendu CAUTION x0.50 après correction de scope ; plancher DANGER x0.25 recommandé à arbitrer
Balance GFT  : 142 105.25$ (DD -5.26%) — aucun LiveMonitor actif (moteur arrêté)

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
  Révérence H4  → NR7 contraction → expansion. WF 2026-05-04 : XAUUSD PASS stable (WR 92%, +0.145R, 25t), EURJPY PASS stable (WR 91%, +0.064R, 11t small-n), USDJPY PASS stable (WR 89%, +0.044R, 9t small-n), DOGEUSD PASS instable. ETHUSD marginal. SOLUSD/BTCUSD/EURUSD/GBPUSD/XAGUSD/GBPJPY FAIL.
                  Overlap mesuré (±4h) : XAUUSD vs Extension H1 = 13.2%, EURJPY = 6.2%, USDJPY = 8.9%, DOGEUSD = 7.0% ; DOGEUSD vs Cabriole H4 = 34.5%. Complémentaire vs Extension partout. Modéré vs Cabriole crypto.

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

### 🛑 BLOQUANT — Investigation drift exécution live vs backtest (avant toute reprise live)

**Verdict 2026-05-07 (rejeu 23 trades, 17 FTMO + 6 GFT)** : ce n'est PAS un slippage (mean +0.009R, négligeable). C'est un bug de **state-tracking exécution** :
- 8.2% des exits sont `reconciled_*` sur 122 trades de la fenêtre live (24% FTMO, 33% GFT)
- 17 exits avec `mfe_r=0` malgré loser franc — tracker MFE défaillant
- 5 trades clés où live MFE=0 mais BT MFE 0.27-2.22R (IMXUSD raté +2.16R)
- Δr (BT−live) moyen = +0.472R/trade (BT bat live de ~0.5R systématiquement)
- Le pattern est présent sur **les deux brokers** → bug `position_manager` / boucle reconcile, pas connecteur cTrader/TradeLocker

**Phase 0 (prévention) — fait 2026-05-07** : ajout du script `scripts/check_execution_invariants.py` (4 invariants : reconciled_ratio, mfe_zero_loser, zero_winner_streak, be_unarmed_ratio). Câblé dans `/suivi` watchlist `execution_invariants` et `/bilan` §2.e. **Règle absolue** : un trigger d'invariant ne s'explique jamais par le régime de marché, c'est toujours un bug.

**Phases à exécuter avant reprise live :**

- [x] ~~**PHASE 1 (Opus, lecture seule)**~~ — Audit fait 2026-05-07. Verdict : 3 bugs identifiés.
  1. `live._reconcile_missed_exits` (live.py:798-806 ancienne version) hardcodait `mfe_r=0`, `be_set=False`, `exit_price=sl`. Cause des 10 reconciled exits + 17 mfe_zero_loser.
  2. `position_monitor.reconcile()` ne checkpointait son state qu'à SIGTERM → crash dur perdait MFE/BE/trail courants.
  3. BE est armé engine-side (in-memory), pas broker-side : un crash entre entry et MFE 0.3R perd l'armement BE.
- [x] ~~**PHASE 2 (Opus, fix)**~~ — Fix appliqué 2026-05-07.
  1. Nouvelle `LiveEngine._reconstruct_exit_from_history()` (live.py:698) : appelle `broker.get_closed_position_detail(position_id)` pour le vrai exit_price + reconstruit MFE depuis bars min1 parquet entre entry_ts et exit_ts. Classe en `reconciled_take_profit/_breakeven_exit/_stop_loss/_other`. Fallback `bars_reconstruction` (BE inferred si MFE≥0.3R) puis `estimated_fallback`.
  2. `position_monitor.reconcile()` checkpointe `save_state()` à chaque cycle (~2 min).
  3. BE physique broker-side **non implémenté** (différé Phase 2.5 — risque mineur tant que reconstruction post-mortem est fiable, mais à faire si crash dur fréquent).
- [x] ~~**PHASE 3 (validation)**~~ — Validation faite 2026-05-07.
  - 7 tests unitaires `tests/test_reconcile_exit_reconstruction.py` passent (TP/SL/BE/other/SHORT/fallback complet/régression mfe_zero_loser).
  - Reconstruction validée offline sur les 17 mfe_zero_loser historiques : 5 auraient été BE = +1R brut récupéré.
  - `check_execution_invariants.py` mis à jour pour distinguer `reconciled_other` (fallback ambigu, trigger > 2%/5%) du reste (info uptime, trigger > 30%).
  - Backtest synthétique blackouts (5min/30min/2h) **non exécuté** : la reconstruction étant déterministe (broker_detail + bars), le test unitaire couvre les cas. Si Phase 4 expose un nouveau pattern, ajouter un test à ce moment.
- [ ] **PHASE 4 bis (reprise active en CAUTION)** — Scope de verdict Extension+Glissade depuis 2026-05-16. Fix startup cTrader livre (`38b7277`), correction streak par broker (`8d3819f`) et barriere min-volume overshoot (`deb29d0`) charges au restart du 27/05. GFT Glissade justifie `CAUTION`, donc risk systeme `x0.50`. Surveiller les trades sautes par `risk overshoot` et conserver l'ALERT FTMO historique XAUUSD 14/05 comme baseline connue. Critère ramp ulterieur : reevaluer invariants en distinguant cet incident pre-fix des nouveaux exits.
  - **Mécanisme de rappel automatique** : trigger `phase4_revalidation` dans `/suivi` watchlist (compte uniquement `extension` + `glissade` depuis `2026-05-16T08:44 UTC` ; revue possible à 30 exits propres, décision cible à 50 → invariants par broker + replay live/théorie + audit sizing avant proposition). Tant que < 30, no-op de décision. Le timer `arabesque-suivi-reminder.timer` garantit le rappel périodique.
  - **Action user à la notif** : valider la ramp (×0.25 → ×0.50, puis ×1.0 sur 50 trades supplémentaires) ou demander session de diagnostic si verdict ≠ ok.
  - **⚠️ Phase 4 bis — scope restreint Extension + Glissade depuis 2026-05-16** (cf `docs/DECISIONS.md` entrée 2026-05-19) : le critère ramp (n ≥ 50) **doit filtrer `strategy ∈ {extension, glissade}` et `since=2026-05-16T08:44 UTC`** (commit `5fd9b24` désactivation Cabriole). Les exits Cabriole et tout trend legacy désactivé sont exclus du compteur et du verdict `meanΔR`. La fenêtre Phase 4 initiale (since 2026-05-07) reste informationnelle (3 stratégies) mais ne porte plus le critère ramp.
  - **Recalcul observabilité 2026-05-19** (suite patch P1 dédup multi-broker `replay_live_vs_theory.py`) : Phase 4 n=8→11 (+3 trades Cabriole/Extension écrasés par dédup tid seul), meanΔR=-0.031R→-0.061R. Phase 4 bis n=1→2 (+1), meanΔR=-0.200R→-0.111R. La mesure était sous-comptée d'environ 30 % à cause de la collision FTMO/GFT sur même `trade_id`.
- [ ] **PHASE 2.5 — Bug #3 différé : éliminer la dépendance "tick 0.3R reçu par engine actif"** — État actuel vérifié dans le code (`position_monitor._check_breakeven` ligne 338 + `_try_amend_sl` ligne 408 → appelle `broker.amend_position_sltp(position_id, stop_loss=new_sl)` ligne 457) : **dès que l'engine reçoit un tick avec MFE ≥ 0.3R, le SL est modifié physiquement côté broker** (cTrader + TradeLocker). Une fois armé, le BE est physique, pas in-memory ; un crash après ce moment ne le perd pas.
  - **Le vrai trou** : la fenêtre où l'engine est down/déconnecté pendant que le prix franchit 0.3R. Le tick n'est jamais reçu → `_check_breakeven` jamais appelé → SL plein reste physique → si retracement, le compte broker prend le SL plein. C'est ce pattern qui a alimenté les 10 reconciled exits avec MFE=0 du bug #1.
  - **Mitigation visée** : poser à l'entry un mécanisme broker-side qui n'a pas besoin de l'engine pour s'activer. Options à vérifier : (a) ordre stop-limit conditionnel "si prix ≥ entry+0.3R alors SL = entry+0.20R" — supporté par cTrader ? TradeLocker ? (b) trailing SL natif broker (mais paliers fixes, pas adaptable). (c) si aucun support natif, un watchdog broker-side externe (process minimal, pas l'engine complet, qui vit ailleurs et fait juste l'amend SL).
  - **Pré-requis** : Phase 4 PASS (50 trades revalidation verts). Investiguer le support natif AVANT de coder.
  - **Risque** : un ordre conditionnel mal posé = SL prématuré. Tester sur compte démo, comparer à l'engine-driven sur 20 trades minimum.
  - **Health-check à ajouter en parallèle** : sur position ouverte, si `now - last_tick_time > 5min` → alerte critique. Cible précisément la fenêtre où l'armement BE serait compromis. Distinct du checkpoint `save_state()` qui, lui, protège contre les crashs APRÈS armement.

**Tâches anciennes archivées ci-dessous, à reprendre si Phase 1 les invalide :**
- [ ] ~~Rejeu trade-par-trade~~ (fait 2026-05-07 — `/tmp/replay_trades.json`, conclusion ci-dessus)
- [ ] **Mesurer slippage entry/exit réel** — extraire de `trade_journal.jsonl` (event entry) le `entry_price` live, comparer au signal_open théorique (open de la bougie i+1). Distribution par stratégie/instrument. Hypothèse : slippage > 1.5× du modèle BT explique le drift Cabriole et une partie d'Extension.
- [ ] **Mesurer spread au moment des entries** — interroger `multi_broker_snapshots.jsonl` (cadence 30s) pour le spread bid/ask au moment des entry_ts. Corréler spread élevé ↔ trades perdants.
- [ ] **Audit code exécution** — vérifier `arabesque/execution/order_dispatcher.py` + `position_monitor.py` : entry/exit sur bon tick (open vs close, mid vs ask, bougie i vs i+1) ? BE 0.3R / offset 0.20R correctement appliqué tick par tick ? **Zone Opus uniquement.**
- [ ] **Sub-bar bias quantification réelle** — comparer pour les 100 derniers trades live l'ordre de résolution SL/TP (BT utilise H/L simultané, live utilise tick séquentiel). Le bias documenté ±0.05R bidirectionnel est-il sous-estimé sur des setups particuliers ?
- [ ] **Critère de reprise live** — cause racine identifiée + fix validé sur dry-run parquet + Wilson IC95 > 0 démontré sur ≥ 30 trades simulés tick-by-tick avec le fix.

### Immédiat
- [x] ~~Watchdog feed externe v1~~ (fait 2026-05-14 — `scripts/feed_watchdog.py` + `~/.config/systemd/user/arabesque-feed-watchdog.{service,timer}`. Timer toutes les 5min, `Persistent=true`. Détecte engine `is-active=active` MAIS dernière `BarAggregator Résumé` > 15min → alerte Telegram+ntfy `🚨 Feed Arabesque mort`. Cooldown 30min anti-spam. Weekend guard ven 22h UTC → dim 22h UTC. **Pas d'auto-restart** (v1 = détection seule, opérateur décide). Externe au process Python pour échapper aux bugs du PriceFeed lui-même (cf. price_feed.py bug `_alert_sent` figé). State `logs/feed_watchdog_state.json`. Validé en conditions réelles 2026-05-14 10:00 UTC, notif reçue.)
- [x] ~~**Bug `_reconcile_missed_exits` invente exit quand broker indisponible (2026-05-14)**~~ ✅ **Corrigé 2026-05-17** — Cause racine : `_reconcile_missed_exits` mappait broker injoignable à `set()` vide → les orphans étaient traités comme "fermés" → faux exits via `estimated_fallback`. Fix : `broker_open_ids: dict[str, set[str] | None]`, `None` = injoignable → reconcile **différé** au prochain boot (warning loggé, AUCUN exit écrit). Tests : `tests/test_reconcile_broker_unreachable.py` (3 tests : broker indispo diffère, broker répond vide reconcilie, mix multi-broker). Cf `docs/DECISIONS.md §3`.
- [ ] **Investiguer 2 signaux Fouetté M1 non tirés le 2026-05-13** — `python scripts/replay_signals_vs_live.py --since 2026-05-13T00:00:00 --min-missing 1` ressort : XAUUSD M1 17:44 UTC × 2 brokers, BTCUSD M1 14:35 UTC × 2 brokers, catégorie `source` (ni FTMO ni GFT n'ont tiré). Diagnostic 2026-05-14 : engine vivant à ces moments (476 résumés BarAggregator le 13-05), à 17:44 UTC précisément le BarAggregator ferme 3 barres mais émet **0 signal**. Le bug Fouetté ligne 230 (cache OR éjecté) ne s'applique pas : pour XAUUSD 17:44, c'est 4h14 après open NY 13:30 UTC (cache 5h = OK) ; pour BTCUSD 14:35, c'est 1h05 après (largement OK). Note importante : `bar_aggregator.py:417` instancie `FouetteSignalGenerator(FouetteConfig())` avec config **par défaut = session NY** pour XAUUSD ET BTCUSD, alors que la doc dit XAUUSD London (cf. memory `project_fouette_london`). Le replay utilise la même config par défaut donc cohérent. **Hypothèse principale** : différence de données entre tick-built bars M1 (live) et parquet historique 1m (replay) → signal passe en parquet mais pas en live. À investiguer en session dédiée : exporter les bars XAUUSD M1 17:30-17:50 UTC depuis BarAggregator vs parquet, comparer High/Low/Close des bars formatrices du signal.
- [x] ~~**Suspect : XAUUSD glissade reconciled -1R MFE=0.91R BE armé (2026-05-14 19:24 UTC)**~~ ✅ **Diagnostiqué + patché 2026-05-17** — Diagnostic : trade `ae845c5d-fb2` entrée 09:01:24 UTC, engine FTMO down 09:47→17:23 UTC (panne PriceFeed, cf incident 2026-05-14), reconcilié 17:24:45 par `_reconstruct_exit_from_history` qui a vu `exit=4666.89` (= SL−0.05) via `broker.get_closed_position_detail` + `MFE=0.91R` reconstruit depuis bars parquet. `be_set=True` venait de l'inférence `mfe_r >= 0.3` (path reconcile), pas du broker (Phase 2.5 BE polling pas active à cette date). Comportement broker correct : SL plein touché en physique car `amend_position_sltp` jamais appelé (tick BE jamais reçu pendant la coupure). Bug réel : sémantique mixte de `be_set` polluait les audits. **Patch be_source** (cf `docs/DECISIONS.md §3` bug 2026-05-14 `be_set`) : nouveau champ `be_source` taxonomie stricte — `broker_armed` (live amend success observé), `broker_evidence` (reconcile exit≈be_target, preuve indirecte), `inferred_from_mfe` (MFE parquet seul), `not_armed`. Nouvel invariant `be_inferred_but_loser` (ALERT≥1, CRITIQUE≥3). Couverture : `tests/test_be_source_semantics.py` (6 tests, dont régression XAUUSD 14-05).
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
- [x] ~~Snapshot multi-broker des positions ouvertes~~ (fait 2026-04-19 — `arabesque/execution/broker_snapshot.py` écrit `logs/multi_broker_snapshots.jsonl` à 30s de cadence tant qu'au moins une position est ouverte, sur tous les brokers connectés. Analyse : `python scripts/review_broker_divergences.py`. Cadence/rétention/filtres hard-codés — à rendre paramétrables si l'usage est confirmé pertinent.)
- [x] ~~Bug racine close_position TradeLocker~~ (fait 2026-04-25 — `arabesque/broker/tradelocker.py:429` appelait `close_position(int(position_id))` qui mappait sur `order_id` arg positionnel. TLAPI cherchait l'ID dans l'historique des ordres → introuvable → False systématique. Fix : keyword `position_id=`. Cause des orphelines GFT non-fermables.)
- [x] ~~Filtre strategy×broker~~ (fait 2026-04-25 — `strategy_broker_exclusions` dans config/settings.yaml + check `_place_on_broker` dans order_dispatcher. `cabriole: [gft_compte1]` actif.)
- [x] ~~Fix bug compare_live_vs_backtest~~ (fait 2026-04-25 — `NameError: 'results' is not defined` dans path no-trades, services systemd report-daily/weekly fail depuis 2026-04-19. Fix : init `drifts=[]` avant la branche, simplifie has_drifts.)
- [x] ~~Fix weekend crypto guard~~ (fait 2026-04-26 — `_is_weekend_crypto_blocked` retournait False dès `weekday()!=4`, donc samedi/dimanche bypassed. 3 trades samedi 2026-04-25 (ALGUSD/MANUSD/IMXUSD, FTMO crypto H4) ont passé — heureusement BE+ tous. Fix : bloque wd in (5,6) toujours, wd==4 si hour>=cutoff. Engine restart 11:11 UTC.)
- [x] ~~`scripts/weekend_guard_review.py`~~ (fait 2026-04-28 — counterfactual sur les blocked events, simule TP/SL/BE sur bougies parquet, compare à WR/Exp en semaine. Verdict ✅ confirme guard / ⚠️ propose désactivation / 🟡 grey zone. Câblé dans `/bilan` §3.)
- [x] ~~`scripts/replay_signals_vs_live.py`~~ (fait 2026-04-28 — rejoue les signaux théoriques (extension/cabriole/glissade/fouette) sur la période, compare aux entries live + blocked weekend. Détecte les signaux qui auraient dû être exécutés et ne l'ont pas été. Tolérance ±2h H1 / ±6h H4 / ±0.5h M1. Câblé dans `/suivi` watchlist `missing_trades_unjustified`.)
- [x] ~~Support `--session` CLI Fouetté~~ (fait 2026-04-28 — `python -m arabesque run --strategy fouette --session london XAUUSD`)
- [x] ~~Bug entry-logged-before-fill~~ (fait 2026-04-28 — `_register_position_in_monitor` distingue maintenant fill confirmé vs pending. Si la position est dans `get_positions()` après retry → `record_entry` + register_position. Sinon → `record_pending_order` + ajout à `_pending_fills` (persistant `logs/pending_fills.json`). Le `_reconcile_loop` poll les pending toutes les 2 min ; quand le STOP/LIMIT touche, on loggue `entry` à ce moment avec le vrai entry_price broker. Pending > 24h → `pending_expired`. Élimine les phantom exits Cabriole avant fill. Nouveaux events JSONL : `pending_order`, `pending_expired`.)
- [x] ~~Étendre `/bilan` skill pour couvrir GFT~~ (fait 2026-04-28 — `compare_live_vs_backtest.py --broker {ftmo_challenge|gft_compte1}` filtre par `broker_id` dans le journal, désactive la dédup cross-broker. /bilan §2.a invoque le script 2× par broker quand un même signal part sur les deux.)
- [x] ~~`scripts/audit_edge_live_vs_backtest.py`~~ (fait 2026-04-29 — **objectif principal du système : vérifier que l'edge est conservé**. Pour chaque stratégie active, panorama Live vs Backtest pleine fenêtre vs Baseline 20 mois. Verdicts : edge_intact / drift_modere / drift_structurel / regime_defavorable / small_n_inconclusif. **Persistance** : append à `logs/edge_audit.jsonl` (1 ligne/run) + écrit `logs/edge_audit_latest.md` (Markdown lisible) — ces fichiers résistent au compactage de session, reboot, coupure. Pour relire l'état : `cat logs/edge_audit_latest.md`. Câblé dans `/bilan §2.d` et `/suivi` watchlist `edge_audit_drift`.)
- [x] ~~Investiguer 8 extension manquants 27/04~~ (fait 2026-05-02 — audit ciblé sur 5 instruments les plus actifs avril : 32 théoriques, 17 matchés + 15 refus position-déjà-ouverte = **0 truly missing**. Le replay générait un signal par barre H1 tant que la condition BB squeeze→breakout restait vraie ; l'engine prend la 1ère, refuse les suivantes. Comportement correct. Le replay surestime grossièrement les manquants.)
- [x] ~~Amender `replay_signals_vs_live.py`~~ (fait 2026-05-02 — dédup par session `_dedup_sessions()` : signaux consécutifs séparés de ≤ 1.5×TF groupés en 1 session, seul le premier gardé. Remap alias `trend`→`extension`. Ajout `--min-missing N` (défaut 10) pour le seuil trigger. Extension : 201→111 théoriques/mois. Les manquants restants ont des causes légitimes : positions déjà ouvertes, engine restart (W15 DNS), max_open_positions. Trigger `missing_trades_unjustified` dans /suivi : utiliser `--since J-7 --min-missing 5` pour détecter un engine aveugle soudain, pas une analyse de couverture mensuelle.)
- [ ] **Cabriole** : audit J-30 2026-05-03 — ΔExp=-0.252R sur n=32, **2e audit consécutif `drift_modere`**. Marge avant `drift_structurel` (-0.30R) ≈ 1 trade. Action prise : rodage ×0.25 (DECISIONS.md 2026-05-03). Critère pause complète : 3e audit `drift_structurel` OU DD FTMO -7.5%.
- [ ] **Extension** : audit J-30 ΔExp=-0.088R `regime_defavorable` (live colle au backtest, attribué au régime de marché). Pas pénalisé (reste hors rodage à ×1.0). Surveiller.
- [ ] **Glissade** : audit J-30 ΔExp=-0.201R `drift_modere` n=8 (small-n, prudence — passé ×0.25 par mesure défensive globale).
- [x] ~~Fix `--broker` flag dans `compare_live_vs_backtest.py`~~ (fait 2026-05-02 — bug ligne 266 : second appel `load_journal(args.journal)` sans `broker=args.broker`, écrasait la valeur filtrée. Validé : FTMO 14t WR 79% vs GFT 6t WR 0% sur 2026-04-20 → 26.)
- [ ] **Restart engine pour appliquer `strategy.type: extension` + rodage ×0.25** — `config/settings.yaml` modifications cumulées 2026-05-02 (`trend` → `extension`) et 2026-05-03 (rodage 0.50 → 0.25). Le rodage est lu au démarrage (cf. order_dispatcher.py:197-204), donc les nouveaux trades Cabriole/Glissade/Fouette ne passeront à ×0.25 qu'**après restart**. Pas urgent (0 position ouverte actuellement = safe), mais à faire dans la journée. Commande : `systemctl --user restart arabesque-live.service`.
- [x] ~~**Bot Telegram interactif phase 1** (lecture seule)~~ (fait 2026-05-03 — `arabesque/bot/telegram_bot.py` + service systemd user `arabesque-telegram-bot.service`. Commandes : `/start` `/help` `/status` (engine + equity + protection) `/positions` (FTMO + GFT) `/edge` (cat edge_audit_latest.md) `/journal` (queue du fichier mois courant). Auth par whitelist `chat_id` parsé depuis `tgram://` URL apprise dans `config/secrets.yaml → notifications.channels`. Extra chat_ids possibles via `secrets.telegram_bot.extra_chat_ids`. Lib `python-telegram-bot 22.7`.)
- [ ] **Bot Telegram phase 2** (commandes action + graphiques) — `/equity_chart 30j` (matplotlib PNG sur snapshots), `/suivi_state` (lit dernier `maintenance_state.jsonl`), `/restart_engine` (avec confirmation 2-temps OK→OK). Pas avant que phase 1 soit éprouvée 1 semaine. Skip `/suivi` et `/bilan` car ils nécessitent Claude (pas un bot autonome).
- [ ] Augmenter risk quand data suffisante (voir critères ci-dessous)
- [ ] **Décision déploiement Révérence H4 (à valider)** — Backtest+WF 2026-05-04 ressort 4 instruments PASS stable : XAUUSD (le plus solide, n=25, WR 92%, +0.145R), EURJPY/USDJPY (small-n 9-11, edge mince mais stable), DOGEUSD (instable). Overlap vs Extension faible (6-13%) → complémentaire. Décision à prendre : (a) shadow live d'abord 2-4 semaines pour récolter live data, (b) déploiement direct XAUUSD à risk × 0.50 rodage (déjà sur instrument couvert par Extension donc pas de nouveau symbol exposure), (c) attendre fin phase défavorable. Ma reco : **(a) shadow** — pas de risk add maintenant, mais data live précieuse à récolter pendant qu'on attend que les 3 autres stratégies se stabilisent. À valider.
- [ ] **Bug Fouetté 0 trade live (diagnostic 2026-05-03→04, à valider en session dédiée)** — Investigation complète :
  - **CONFIRMATION EMPIRIQUE 2026-05-04 20:25** : `journalctl` depuis avril → 2263 logs `EMA shadow` Fouetté, 117 signaux 📈 émis par BarAggregator (tous strat=trend/extension), 0 strat=fouette. `signal.py` détecte donc bien des signaux, mais TOUS sont jetés en aval. Probable retour aux conclusions de l'hypothèse 1 (filter `last_idx`) — à reproduire en isolation avant fix.
  - Hypothèse 1 (initiale) : `bar_aggregator.py:370-372` filtre `last_idx`. **À RE-VALIDER** par test local avec un fixture M1 réelle. Le shadow log EMA s'imprime exactement à la confirmation de retest, donc `global_i = len(df)-1 = last_idx`. Le filtre `last_idx` accepte ce signal.
  - Hypothèse 2 (probable) : **cache 300 bars M1 = 5h** (`bar_aggregator.py:53 BAR_CACHE_MAX = 300`) alors que session NY dure 6h30 (14:30→21:00 UTC). Conséquence : passé ~19:30-20:00 UTC, la fenêtre OR (14:30→15:00) est éjectée du cache → `_tag_or_bars` ne trouve plus de bars OR → `len(or_pos) < 2` → `_process_session` retourne None silencieusement. Fouetté ne peut détecter un signal que sur les ~5h après ouverture de session, pas après. Pour XAUUSD London (08:00→14:00 UTC = 6h), même problème.
  - 917 logs shadow EMA constatés ≠ 0 trade : les 917 ont été émis (shadow non-bloquant), mais probablement filtrés ailleurs OU sur des bougies hors fenêtre cache OR. À vérifier : grep dans `journalctl` pour le compte de signaux Fouetté **après** EMA shadow.
  - Fix candidat : passer `BAR_CACHE_MAX` à 500 (ou rendre dépendant du timeframe : 500 pour M1, 300 pour H1+). Coût mémoire négligeable, couvre 8h sur M1.
  - Rejeu 20 mois requis avant fix : si on accepte des signaux plus tardifs dans la session, le profil de trades change. Comparer WR/Exp vs version actuelle.
  - Zone touchée : `arabesque/execution/bar_aggregator.py` (Opus possible, mais critique). Pas de modif `signal.py`. Validation user nécessaire avant déploiement.
- [ ] **Analyse "Exp par régime de marché" sur 20 mois de trades** (à planifier hors phase défavorable, pour éviter calibrage biaisé). Script Sonnet, ~1h, sans modif `signal.py`. Pour chaque trade Extension/Cabriole/Glissade backtest : calculer descripteur régime à l'entrée (ADX_14, ATR_14/ATR_90, Choppiness Index sur la bougie de signal). Bucketer trades par bucket de régime, reporter Exp / WR / n par bucket. **Verdict de viabilité d'un filtre de régime** : si séparation nette (`Exp_chop < -0.2R` vs `Exp_trend > +0.2R`) ET tient en walk-forward 3+ fenêtres → filtre crédible (passer en Opus + revalidation 20 mois). Sinon (séparation faible ou s'inverse selon la fenêtre) → confirmer "régime cyclique, pas filtrable proprement", refermer la question. Motif : actuellement Extension `regime_defavorable`, Cabriole `drift_modere` — toutes les stratégies de breakout/divergence souffrent du chop simultanément. Ne PAS calibrer pendant le creux (overfit garanti). Cf. discussion 2026-05-03.

### Watchlist `/suivi` — seuils quantifiables à surveiller

> Ces seuils sont câblés dans la skill `.claude/commands/suivi.md`. Invoquer `/suivi` pour évaluer + agir.
> Infra de rappel : `arabesque-suivi-reminder.timer` (systemd user, hourly, `Persistent=true`). Lit `logs/maintenance_state.jsonl`, ping Telegram si `next_expected_in_hours` depasse ; ajoute ntfy uniquement pour une escalade feed urgente. Survit au reboot. Cooldown 3h. Auto-bilan si fenetre (dimanche 18h+ UTC ou debut de mois).

- **`glissade_gft_block`** : si Glissade GFT WR < 30% sur n ≥ 8 trades → ajouter `glissade: [gft_compte1]` à `strategy_broker_exclusions`. Actuellement n=4 (1/4 = 25%, CI95 trop large). Levée si Glissade FTMO WR ≥ 70% sur n ≥ 15 puis test progressif sur GFT.
- **`cabriole_gft_unblock`** : si Cabriole FTMO WR ≥ 70% sur n ≥ 20 trades → retirer `cabriole: gft_compte1` de l'exclusion. Actuellement 8/14 = 57% (incl. BE). Manque 6 trades.
- **`extension_gft_drift`** : si Extension GFT WR diverge de FTMO de > 50pp sur n ≥ 10 → block. Actuellement n=3 trop petit.
- **`phantom_exit_alert`** : si nouvel `orphan_cleanup` ou phantom_fallback dans les 24h → alerte Telegram + investigation. Cause amont non corrigée.
- **`engine_uptime_drop`** : si engine restarté < 30min ou inactif > 5min → alerte (peut indiquer crash auto-restart).
- **`dd_proximity`** : si DD courant > 70% du seuil CAUTION (-7%) → alerte proactive avant guard.
- **`stale_bilan`** : si pas de `/bilan` depuis ≥ 8 jours → suggestion (pas auto).
- **`missing_trades_unjustified`** : `scripts/replay_signals_vs_live.py --since J-7` → si > 2 signaux théoriques sans entry, sans blocked, sans exclusion par broker → alerte. Cause possible : engine aveugle, stratégie dropped silencieusement, filtre trop restrictif.
- **`edge_decomposition`** : `scripts/edge_decomposition.py --since J-30` (ou Phase 4 window). Décompose ΔExp en `be_missed` / `sl_slipped` / `reconciled` / `mfe_zero_loser` / `wide_spread` + résiduel régime. Persiste `logs/edge_decomposition.jsonl`. Triggers : be_missed ≥ 10% → bug BE ; sl_slipped ≥ 15% → slip anormal ; wide_spread ≥ 10% → coût exécution ; reconciled+mfez borne sup ≥ 30% → bug tracking ; résiduel ≥ 70% ET ΔExp < -0.20 → marché. Test 2026-05-09 sur 103 exits avril-mai : ΔExp=-0.51R, be_missed=5%, résiduel régime=95% (= la dérive est régime, pas bug exécution).
- **`replay_drift_live_vs_theory`** : `scripts/replay_live_vs_theory.py --since J-30`. Pour chaque trade live, simule le trade pur en parquet (BE 0.3R / TP 2R / SL signal.sl) et reporte `Δ_R = R_live − R_theo` par stratégie. Triggers par stratégie : meanΔR < -0.20R sur n ≥ 20 → ultra-rodage ×0.10 ; < -0.10R sur n ≥ 30 → alerter ; ∈ [-0.10, +0.10] → exécution propre (drift = régime ou bias BT). Persiste `logs/replay_live_vs_theory.jsonl` + `_trades.jsonl`. Test J-30 2026-05-09 (54 trades) : Cabriole +0.02R/31t (propre), Extension -0.10R/19t (modéré), Glissade +0.23R/4t (small-n).
- **Ultra-rodage ×0.10** : levier `config/settings.yaml → rodage.strategies_ultra` (vide par défaut). Activable manuellement quand `replay_drift_live_vs_theory` ou `edge_decomposition` recommande de réduire sans stopper. Sur compte 100k$, 0.10%/trade ≈ ~$100, 10 pertes consécutives ≈ 1% du compte.

### Court terme
- [x] ~~Corrélation inter-positions~~ (fait 2026-03-27 — discount 0.70/0.50/0.35 par catégorie dans order_dispatcher)
- [x] ~~Activer GFT compte1~~ (fait 2026-03-23 — enabled, protected: false, 36 instruments mappés)
- [x] ~~Graceful SIGTERM~~ (fait 2026-03-27 — save_state/load_state position_monitor, MFE+BE+trailing persistés)
- [x] ~~Rapports automatisés~~ (fait 2026-03-27 — daily 21h UTC + weekly dim 20h UTC via systemd timers)
- [x] ~~Feuille de route BB_RPB_TSL~~ (fait 2026-03-28 — 11/13 éléments testés, tous NEUTRE ou REJETÉ. Le système actuel est le bon profil. Voir `docs/EXPERIMENT_LOG.md` section 6)
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

### État Wilson CI (calculé 2026-05-04, live cumulé depuis activation)
```
Stratégie    Broker            n    WR     CI95 high   CI99 high   Exp(R)    Palier
extension    ftmo_challenge    46   ~32%   ~64%        ~70%        -0.27R    < P0
extension    gft_compte1       13   34.6%  57.6%       64.9%       -0.305R   < P0
cabriole     ftmo_challenge    28   19.6%  39.5%       45.7%       -0.531R   < P0  (très bas)
glissade     ftmo_challenge    4    62.5%  85.0%       89.5%       +0.474R   < P0  (small-n)
fouette      —                 0    —      —           —           —         0 trade (bug à fixer)
```
Toutes stratégies live actuellement < P0. Cohérent avec phase défavorable identifiée par edge_audit (regime_defavorable extension, drift_modere cabriole). Rodage ×0.25 défensif justifié empiriquement par le Wilson. Ne RIEN relever tant qu'IC99 WR > 50% n'est pas atteint sur les stratégies (P0 minimum).

| Palier | Condition | Trades estimés | Date estimée | Action |
|---|---|---|---|---|
| **P0** | IC99 WR > 50% | ~50 | ~fin avril 2026 | Confirmation edge existe |
| **P1** | IC99 WR > 60% + Exp IC95 > 0 | ~80 | ~mi-mai 2026 | Risk plancher à $100/trade min (anti-rounding) |
| **P2** | IC99 WR > 65% + Exp IC99 > 0 | ~150 | ~juillet 2026 | Réduire agressivité DD linéaire (0.60 au lieu de 0.10 plancher) |
| **P3** | 200+ trades, WR et Exp stables 3 mois | ~200 | ~août 2026 | Risk plein 0.40%, nouveau compte si disponible |

**Règle anti-slippage** : ne jamais descendre sous $100/trade de risk.
En-dessous, le lot rounding (±$5) mange >25% du gain moyen (+0.20R).
Si DD linéaire donne <$100, appliquer un plancher à $100 ou skip le trade.

### Suggestions d'agencement (observées 2026-05-04, non urgent)

- **`BAR_CACHE_MAX = 300` hardcodé dans `bar_aggregator.py:53`** : limite Fouetté M1 à ~5h de cache, alors que session NY dure 6h30. Devrait être paramétré par timeframe (M1: 500+, H1+: 300 suffisant). Cf TODO bug Fouetté.
- **Filtre `last_idx` implicite dans `BarAggregator._generate_and_emit`** : convention non documentée, pose problème pour stratégies session-based. Suggestion : ajouter attribut `signal_emission_mode: "last_bar" | "session_window"` sur les signal generators ; le BarAggregator branche en conséquence. Permettrait à Fouetté de fonctionner sans casser Extension/Glissade.
- **Alias `trend` → `extension` non finalisé dans `trade_journal.jsonl`** : 31 events `strategy=trend` + 15 events `strategy=extension` depuis l'activation, comptés en doublon dans Wilson CI. Le rename code est fait, mais le journal historique garde l'ancien nom. Soit migrer le journal (one-shot), soit l'agrégateur doit explicitement traiter `trend` comme alias d'`extension` (déjà fait dans `compare_live_vs_backtest.py` post-2026-05-02, à vérifier dans tous les autres consommateurs).
- **`exit` events n'ont pas de `entry_ts` dédié** (juste `ts` = exit timestamp). Pour les analyses cross-strats nécessitant la corrélation entry-time, il faut joindre avec l'event `entry` correspondant via `position_id`. Pénible. Proposition : dénormaliser `entry_ts` dans `exit` pour faciliter les agrégations.
- **Pas de rotation `trade_journal.jsonl`** : croissance illimitée. À ce rythme (~50 events/semaine), pas critique avant 2027, mais à anticiper. Format suggéré : 1 fichier par mois `trade_journal_YYYY-MM.jsonl`, avec un `trade_journal_latest.jsonl` symlink.

### Sujets à traiter (prochaine session)

1. **Journal de trading global** �� Mettre en place un journal de trading structuré (local, gitignored) qui centralise les trades, les événements marquants, les décisions manuelles. Pas un trade_journal.jsonl machine, mais un journal humain lisible. À garder en local (.gitignore) car contient des données personnelles de trading. Format suggéré : Markdown mensuel dans `logs/journal/` (gitignored).

2. **Filtre news haute importance** — Récupérer les dates de news éco (NFP, FOMC, CPI…) et bloquer le trading ±5min autour. API candidates : ForexFactory RSS, Investing.com calendar, FXStreet. Implémentation : `is_high_impact_news(now, buffer_minutes=5)` dans guards.py, appelé avant le dispatch. Réduit le besoin d'un compte swing (les news sont le seul autre cas de gap intra-semaine).

3. **Stratégies non-Extension : pas de bug** — Diagnostic 2026-04-12 : Cabriole (0 breakout Donchian depuis 28/03), Glissade (0 divergence RSI), Fouetté (917 tentatives filtrées par EMA shadow). Conditions de marché défavorables, pas un bug. Surveiller via le rapport quotidien ("Inactif: cabriole: Xj").

4. **Backlog observabilité (à activer seulement si nécessaire)** — Diagnostic 2026-05-20 a identifié deux métriques utiles qui ne sont **pas urgentes** : (a) `recent_squeeze_rate` hebdomadaire dans `scripts/rolling_baseline_distribution.py` pour mesurer le régime Extension (alerter si le rate tombe sous p5 historique 20 mois) ; (b) `event_silence_gap_h_p95` sur `trade_journal.jsonl` pour mesurer la disponibilité engine (gaps W3 cumulés ≥ 30 % du temps en silence > 2h, précèdent l'incident token 19-05). À activer uniquement si un prochain `/suivi` ressort à nouveau ces deux questions. Pas de pré-implémentation. Cf DECISIONS 2026-05-20.

5. **Étages 2/3/4 résilience broker (décision toujours différée — observation 24-48 h des étages 0+1 court-circuitée par incident feed parallèle 2026-05-21 22:59 UTC → 2026-05-22 18:55 UTC)** — Incident DASHUSD initial 2026-05-20 : `_connected=False` silencieux côté canal trading cTrader pendant ~10 h, amend BE/TSL impossible, position laissée avec SL d'origine pendant tout le retracement de +1.82R → flottant négatif. Étages 0+1 déployés (alerte amend_failures + reconnect-on-demand avant les 4 lignes "Not connected" dans `ctrader.py`) couvrent ~95 % du cas. **Verdict patch 0+1** : sur la fenêtre 11:50→22:59 UTC le 21/05 (11h09 avant crash feed), **invariant tenu** (0 `ABANDONED` / 0 `Reconnect trading` / 0 `Not connected`). Étages 2/3/4 à décider après nouvelle observation 24-48h post-restart 2026-05-22T18:55 UTC dans des conditions feed propres. Cf doc dossier `docs/INCIDENT_DASHUSD_RESILIENCE_BROKER_2026-05-21.md` section 7.

6. **Fallback disque refresh_token (task #32 — FERMÉE 2026-05-22 soir)** — Patch livré : `_refresh_access_token` détecte `ACCESS_DENIED` (HTTP 200 + errorCode, ou status 400/401) et appelle `_try_disk_token_fallback` qui relit `config/secrets.yaml` via nouveau helper `arabesque.config.load_broker_tokens(broker_id)`. Si le `refresh_token` disque diffère de l'in-memory, adoption + retry HTTP unique. Anti-récursion via paramètre `_disk_fallback_done`, boucle bornée à 2 itérations max (le `_token_lock` n'est pas réentrant — récursion classique = deadlock, contrainte verrouillée par les tests). 12 tests ajoutés (`tests/test_ctrader_token_disk_fallback.py`), 122/122 suite verte. À surveiller au prochain incident OAuth : le log explicite `🔄 ACCESS_DENIED — refresh_token disque diffère, adoption + retry unique` doit apparaître.

7. **Auto-déclencheur /suivi sur feed_stale persistant (task #33, ouverte 2026-05-22)** — Le watchdog systemd `arabesque-feed-watchdog.service` a alerté ntfy+Telegram dès 07:22 UTC le 22/05, mais aucun /suivi assistant n'a été déclenché entre le crash 22:59 UTC le 21/05 et le /suivi user-initié à 18:51 UTC le 22/05 = 19h54 de fenêtre où l'utilisateur a porté seul la charge de surveillance. Option : escalade ntfy 🚨 critique distincte du reminder habituel quand feed_stale persiste > 30 min ; ou auto-trigger /suivi via wakeup. À débattre.

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
| `arabesque-feed-watchdog.timer` | timer toutes les 5min (Persistent) | Détection panne silencieuse PriceFeed. Auto-restart sur `feed_stale` persistant + anti-boucle volontaire depuis Hot Path Mode 2026-05-23 ; restaure le monitoring d'une position, ne démarre pas un moteur inactif. |

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
