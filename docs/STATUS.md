# Arabesque — État opérationnel courant

> **Ce fichier = snapshot de l'état live/test à un instant T.**
> Contrairement à `DECISIONS.md` (historique des pourquoi) et `HANDOFF.md` (état de session),
> ce fichier est la référence rapide pour savoir ce qui tourne, sur quel compte, avec quel paramétrage.
> **Mettre à jour à chaque changement de compte ou de configuration live.**

Dernière mise à jour : 2026-05-17 (session Opus 4.7 — cleanup docs)

---

## Moteur live — État actuel

| Paramètre | Valeur |
|---|---|
| **Statut** | ✅ En marche — **Phase 4 bis** depuis 2026-05-16 (noyau Extension + Glissade, Cabriole désactivée) |
| **Phase** | Phase 4 revalidation (≥ 50 trades à risk ×0.25 depuis 2026-05-07T23:45 UTC) + Phase 2.5 BE polling broker-side actif |
| **Commande** | `systemctl --user start arabesque-live` (service systemd, auto-restart) |
| **Log** | `journalctl --user -u arabesque-live -f` |
| **Comptes actifs** | `ftmo_challenge` (cTrader 45667282) + `gft_compte1` (TradeLocker) |
| **Type FTMO** | Challenge Phase 1 (2-step, 100k USD) |
| **Type GFT** | Challenge (150k USD) |
| **Environnement cTrader** | **Démo** (`is_demo: true` — les challenges FTMO utilisent l'endpoint démo) |
| **Balance FTMO** | ~$94 309 (DD -5.7%) |
| **Balance GFT** | ~$142 742 (DD -4.8%) |
| **Protection FTMO** | LiveMonitor **NORMAL** (seuils relevés 2026-04-15) |
| **Protection GFT** | LiveMonitor **NORMAL** |
| **Notifications** | ntfy ✅, Telegram ✅, **bot Telegram interactif** (lecture seule) ✅ |
| **Watchdog feed** | ✅ Actif (`arabesque-feed-watchdog.timer`, 5min, détection silent-fail PriceFeed) |
| **Dernier incident** | 2026-05-14 — boucle Feed stale ETHUSD 9h45→17h23 UTC (Glissade XAUUSD -1R imputable, gate Phase 2.5) |

---

## Stratégies actives — Phase 4 bis (2026-05-16)

| Stratégie | Timeframe | Instruments | Mode | Statut |
|---|---|---|---|---|
| **Extension** (trend BB) | H1 | XAUUSD, GBPJPY, AUDJPY, CHFJPY | Phase 4 revalidation (risk ×0.25) | ✅ Actif — noyau |
| **Extension** (trend BB) | H4 | 27 crypto (BTCUSD, ETHUSD, BNBUSD, SOLUSD…) | Phase 4 revalidation (risk ×0.25) | ✅ Actif — noyau |
| **Glissade** (RSI div) | H1 | XAUUSD, BTCUSD | Phase 4 revalidation (risk ×0.25) | ✅ Actif — noyau |
| **Cabriole** (Donchian) | H4 | — | — | 🛑 **Désactivée** (Phase 4 bis — drift `drift_modere` répété, à réexpliquer) |
| **Fouetté** (ORB M1) | M1 | — | Observation paper seulement | 🟡 0 trade live (bug cache OR bar_aggregator, cf HANDOFF) |

Noyau Phase 4 bis : **Extension + Glissade uniquement** (cf `docs/PHASE4_BIS_CHECKLIST_2026-05-16.md`).
Risk global ×0.25 maintenu jusqu'à ≥ 50 trades de revalidation depuis 2026-05-07T23:45 UTC.
Critère go (auto via trigger `phase4_revalidation` dans `/suivi`) : `check_execution_invariants.py --per-broker` = `global_verdict=ok` + `mfe_zero_loser=0`.

---

## Compte actif : ftmo_challenge

```yaml
# config/accounts.yaml
ftmo_challenge:
  type: ctrader
  protected: false              # OK pour live (risk conservatif 0.45%)
  is_demo: true                 # Challenge FTMO = environnement démo cTrader
  initial_balance: 100000
  profit_target_pct: 10.0       # Phase 1
  risk_per_trade_pct: 0.45      # conservatif (compte en DD -5%)
  max_daily_dd_pct: 3.0         # FTMO = 5%, garde interne 3%
  max_total_dd_pct: 8.0         # FTMO = 10%, garde interne 8%
  leverage: 30                  # 1:30 swing
```

**Credentials** : stockées UNE SEULE FOIS dans `config/secrets.yaml` → section `ctrader_oauth`.
App OpenAPI "arabesque" (client_id 23710, basculé 2026-03-28). Ancien compte (19907) gardé dans `ctrader_oauth_old`.
Le compte challenge référence `oauth: ctrader_oauth` (pas de duplication de tokens).

**Réduction linéaire de risque** : le compte FTMO est en DD -5.5%. Le `compute_sizing` réduit
automatiquement le risque. En plus, CAUTION applique ×0.50.
Le compte GFT est en DD -4.8%, protection NORMAL.

**État Wilson CI au 2026-05-04** (cumul live depuis activation, cf HANDOFF.md) :
- extension FTMO n=46 WR~32% Exp -0.27R, GFT n=13 WR 34.6% Exp -0.305R — < P0
- cabriole FTMO n=28 WR 19.6% Exp -0.531R — < P0 (très bas, désactivée Phase 4 bis)
- glissade FTMO n=4 small-n WR 62.5% Exp +0.474R — non concluant
- fouette n=0 — bug cache OR

Cohérent avec phase défavorable identifiée par `edge_audit` (extension `regime_defavorable`, cabriole `drift_modere`).
Rodage ×0.25 défensif justifié empiriquement par le Wilson. Ne RIEN relever tant qu'IC99 WR > 50% (P0) n'est pas atteint.

---

## Architecture des credentials (secrets.yaml)

```yaml
ctrader_oauth:           # Section partagée — une seule fois
  client_id: ...
  client_secret: ...
  access_token: <auto-refreshed>
  refresh_token: <auto-refreshed>

ftmo_challenge:
  account_id: '45667282'
  oauth: ctrader_oauth   # ← référence, pas duplication

gft_compte1:
  account_id: '1711519'
  auth: tradelocker_gft  # ← référence

tradelocker_gft:
  email: ...
  password: ...
```

Le token refresh sauvegarde dans la section partagée `ctrader_oauth`.
`_resolve_secret_refs()` dans `config.py` résout les références au chargement.

---

## Comptes connus

| Compte | Broker | Type | Balance | Objectif | Daily DD | Total DD | Risk/trade | Protected | Statut |
|---|---|---|---|---|---|---|---|---|---|
| `ftmo_challenge` | cTrader (FTMO) | Challenge P1 2-step | 100k (act. ~95k) | +10% | 5% | 10% | 0.45% | false | ✅ **Actif** |
| `gft_compte1` | TradeLocker (GFT) | Challenge P1 GOAT 2-step | 150k (act. ~143k) | +8% | **4%** | 10% | 0.30% | false | ✅ **Actif** |

### Architecture multi-prop firm

cTrader (FTMO) est la source unique de prix (ticks). Les signaux sont générés
une seule fois, puis `OrderDispatcher._dispatch_to_all_brokers()` route les ordres
vers **tous** les brokers activés (cTrader + TradeLocker). TradeLocker ne fournit
pas de ticks — un seul price feed suffit.

Les instruments non disponibles chez un broker sont automatiquement ignorés
(ex: GRTUSD non dispo chez GFT → skippé silencieusement, log warning).

**Pourquoi multi-prop firm** : reproduire les mêmes trades sur plusieurs comptes
pour multiplier les gains sans déclencher de copy-trading intra-broker.
Chaque compte a son propre sizing (risk_per_trade_pct, max_dd) adapté aux
règles de la prop firm.

**Validé le 2026-03-23** : connexion, place_order, cancel_order testés sur
TradeLocker (GFT). 36 instruments mappés. Premier dispatch réel : GRTUSD → FTMO OK, GFT ignoré (instrument non dispo).

### Comptes cTrader connus (via API)

| account_id | live | Balance | Statut | Usage |
|---|---|---|---|---|
| 45667282 | false (démo) | 94 989$ | ACTIVE | **Challenge actuel** |
| 46570880 | true | 99 558$ | ACTIVE | Ancien test (accès supprimé) |
| 46738849 | true | 100 264$ | ACTIVE | Ancien test (ne plus utiliser) |

⚠️ Les challenges FTMO utilisent l'environnement **démo** cTrader (`live: false`).
→ `is_demo: true` obligatoire dans settings.yaml et accounts.yaml.

### Différences clés FTMO vs GFT

| | FTMO Swing | GFT GOAT |
|---|---|---|
| **Daily DD** | 5% | **4%** (plus serré) |
| **Levier** | 1:30 | 1:100 |
| **Overnight/WE/news** | ✅ Autorisé | ✅ Autorisé |
| **Crypto H4** | 27 instruments | **6 instruments** (majeurs uniquement) |
| **H1 forex** | 4 instruments | 4 instruments (identique) |
| **Risk/trade** | 0.45% (conservatif) | 0.30% (daily DD serré) |
| **Guard interne daily** | 3.0% | 2.5% |
| **Split funded** | 80% | Variable |

⚠️ **GFT daily DD = 4%** → risk/trade réduit à 0.30% et guard interne à 2.5%.
Instruments H4 crypto limités à : BTCUSD, ETHUSD, BNBUSD, SOLUSD, LTCUSD, BCHUSD.

---

## Monitoring — que surveiller

### Vérification rapide
```bash
# Moteur vivant ?
systemctl --user status arabesque-live

# Derniers logs
journalctl --user -u arabesque-live -f

# Equity + protection level
journalctl --user -u arabesque-live | grep '💰\|🔒\|🛡️\|🚨\|⚠️' | tail -20

# Health check complet (13 vérifications)
python scripts/health_check.py
```

### Signaux d'alarme dans les logs
| Pattern | Signification | Action |
|---|---|---|
| `🔒 Signal bloqué` | Trading freezé (EMERGENCY) | Vérifier cause, appeler `manual_unfreeze()` si OK |
| `🛡️ Risk réduit` | Palier CAUTION ou DANGER actif | Surveiller, vérifier le DD |
| `⚠️ EMERGENCY` | DD extrême ou marge critique | Intervention immédiate |
| `❌` (erreur broker) | Déconnexion cTrader | Relancer le moteur |
| `Reconnexion` en boucle | Instabilité connexion | Attendre, ou relancer |

### Paliers de protection actifs (LiveMonitor)
| Palier | Daily DD | Total DD | Action automatique |
|---|---|---|---|
| NORMAL | > -2.5% | > -5% | Rien |
| CAUTION | ≤ -2.5% | ≤ -5% | Risk × 0.5 |
| DANGER | ≤ -3.0% | ≤ -6.5% | Risk × 0.25 + ferme positions sans BE |
| EMERGENCY | ≤ -3.5% | ≤ -8.0% | Risk × 0.10 (lot min) + ferme positions sans BE |

---

## Notifications

Configurées dans `config/secrets.yaml` (non versionné) :
```yaml
notifications:
  channels:
    - "tgram://BOTTOKEN/CHATID"           # Telegram — alertes détaillées
    - "ntfys://arabesque_alertes_7x9k2m"  # ntfy — push urgent
```

**Statut 2026-03-27** : ntfy ✅, Telegram ✅ (token corrigé — manquait le préfixe numérique du bot ID).
**Statut 2026-05-03** : **bot Telegram interactif phase 1** ✅ (`arabesque-telegram-bot.service`, lecture seule). Commandes : `/status` `/positions` `/edge` `/journal`. Auth whitelist `chat_id`.
**Statut 2026-05-16** : services `arabesque-report-daily` + `arabesque-suivi-reminder` repassés `success` (cf `docs/INFRA_SERVICES_FIX_2026-05-16.md` — `health_check --warn-only` exit 0, fallback `_load_apprise()`).

### Tester les notifications
```bash
python -c "
import asyncio, apprise
async def t():
    a = apprise.Apprise()
    a.add('ntfys://arabesque_alertes_7x9k2m')
    ok = await a.async_notify(body='Test Arabesque', title='TEST')
    print('ntfy:', '✅' if ok else '❌')
asyncio.run(t())
"
```

---

## Trade journal

`logs/trade_journal.jsonl` — un fichier JSONL avec chaque entrée/sortie live.

**Comparer backtest vs live** (multi-stratégie : Extension, Glissade, Fouetté, Cabriole) :
```bash
python scripts/compare_live_vs_backtest.py                # toute la période du journal
python scripts/compare_live_vs_backtest.py --last 7       # derniers 7 jours
python scripts/compare_live_vs_backtest.py --period this_week --notify  # + alerte Telegram
```
Exécuté automatiquement par le timer daily (21h UTC) et weekly (dim 20h UTC).

---

## Fait (2026-03-23)

- [x] **Compte challenge FTMO identifié** : 45667282 (is_demo: true — endpoint démo cTrader)
- [x] **OAuth centralisé** : tokens dans `ctrader_oauth`, référencés par `oauth:` depuis chaque broker
- [x] `update_broker_tokens()` sauvegarde dans la section partagée (plus de duplication)
- [x] Moteur relancé sur le bon compte challenge avec la bonne config
- [x] **GFT compte1 activé** : `enabled: true`, `protected: false`, testé (connect + place_order + cancel)
- [x] Moteur relancé avec 2 brokers : ftmo_challenge (83 instruments) + gft_compte1 (36 instruments)
- [x] Premier dispatch multi-broker : GRTUSD SHORT → FTMO OK, GFT ignoré (instrument non dispo)

## Fait (2026-03-22)

- [x] Per-timeframe risk : H4 → ×1.22 (0.55% effectif), validé par backtest
- [x] Glissade activé en live (WF 3/3 PASS, WR 83%)
- [x] Per-account risk overrides (accounts.yaml)
- [x] Guard "Best Day" (métrique backtest + live alert dans LiveMonitor)
- [x] Monte Carlo barrières : Challenge 0.80% → P(+10%)=82%, P(breach)=4.5%
- [x] Scan Fouetté crypto M1 → seuls BTCUSD+BNBUSD viables, impact marginal
- [x] Comptes challenge configurés : ftmo_challenge (100k, 0.45%) + gft_compte1 (150k, 0.30%)
- [x] GFT compte2 supprimé (perdu pour inactivité 30j)
- [x] Script comparaison live vs backtest : `python tmp/compare_live_vs_backtest.py`

## Prochaines étapes immédiates

- [x] ~~Corriger notifications Telegram~~ (fait 2026-03-27)
- [x] ~~Comparaison live vs backtest automatisée~~ (fait 2026-03-28 — timer daily + weekly, multi-stratégie)
- [x] ~~Health check automatisé~~ (fait 2026-03-28 — 13 checks, alerte si WARN/CRIT)
- [x] ~~Corrélation inter-positions~~ (fait 2026-03-27 — discount 0.70/0.50/0.35 par catégorie)
- [x] ~~Activer Fouetté + Cabriole~~ (fait 2026-03-28 — rodage ×0.50)
- [ ] Augmenter le risk à 0.80% une fois le compte stabilisé

## Prochaines étapes structurelles

- [x] ~~Activer GFT compte1~~ (fait 2026-03-23)

---

## Architecture snapshot (v9 — Phase 4 bis 2026-05-16)

```
Live actif (compte ftmo_challenge, 45667282, démo cTrader) :
  Extension H1 → XAUUSD, GBPJPY, AUDJPY, CHFJPY (Phase 4 revalidation, risk ×0.25)
  Extension H4 → 27 crypto (Phase 4 revalidation, risk ×0.25)
  Glissade H1  → XAUUSD, BTCUSD (Phase 4 revalidation, risk ×0.25)

Désactivée Phase 4 bis :
  Cabriole 4H  → drift_modere répété, à réexpliquer avant réactivation
  Fouetté M1   → bug cache OR bar_aggregator (0 trade live), observation paper

Testé, edge insuffisant :
  Renversé H1  → sweep + FVG retrace (WR 73%, Exp +0.006R = breakeven)
  Révérence H4 → NR7 expansion (DOGEUSD PASS WR83%, overlap 14% = complémentaire, edge mince)

Non viable :
  Pas de Deux  → pairs trading (mean-reversion, incompatible boussole)
```
