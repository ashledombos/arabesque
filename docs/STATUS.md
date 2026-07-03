# Arabesque — État opérationnel courant

> **Ce fichier = snapshot de l'état live/test à un instant T.**
> Contrairement à `DECISIONS.md` (historique des pourquoi) et `HANDOFF.md` (état de session),
> ce fichier est la référence rapide pour savoir ce qui tourne, sur quel compte, avec quel paramétrage.
> **Mettre à jour à chaque changement de compte ou de configuration live.**

Derniere mise a jour : 2026-07-03 après-midi (CONCENTRATION Étape 4 : le live ne trade plus que Glissade-XAUUSD)

> **🎯 2026-07-03 — CONCENTRATION (Décision 2026-07-03 ter, go opérateur « rentabilité d'abord »)** :
> suite au banc d'essai (Étapes 1-3.5 : Extension sans edge net nulle part en régime récent,
> Glissade-BTCUSD tuée par les coûts, élargissement Glissade = échec, l'edge est spécifique à l'or),
> **le live ne trade plus que Glissade-XAUUSD** (~+0.24R net, seul survivant). Extension + Fouetté
> sont **exclues du dispatch sur les 2 brokers** (signaux toujours générés = mesure théorique
> continue, 0 ordre). Cabriole désactivée depuis mai. Coûts : GFT 3× moins cher que FTMO en
> forex/métaux (spread médian 0.015R vs 0.049R) → GFT = meilleure venue pour l'or.

> **⚠️ 2026-07-03 — Découplage du gate signal (Décision 2026-07-03 dans DECISIONS.md)** :
> FTMO est à **DD total -7,0 % = seuil de pause guard** → son gate per-broker
> (`check_account_limits`) bloque toute nouvelle entrée FTMO, et sa balance figée
> (92 999,27) ne peut plus résorber le DD sans décision opérateur. Depuis le commit
> `199f617`, ce blocage **ne gèle plus GFT** : `receive_signal` évalue les guards par
> compte et accepte si au moins un passe. **Puis (même jour, go opérateur — Décision
> 2026-07-03 bis) : garde interne DD FTMO relevée 8 % → 9 %** (`8e58d9f`), pause FTMO
> à **-8,0 %** → les DEUX comptes retradent. FTMO opère dans la fenêtre -7 → -8 à
> sizing minuscule (~11 $/trade : CAUTION ×0.50 × DD-scaling ~0.21 × rodage ×0.25) ;
> filets conservés : DANGER -8, EMERGENCY -9, limite réelle FTMO -10. Balances au
> 2026-07-03 : FTMO $92 999 (-7,0 %, CAUTION), GFT $141 962 (-5,36 %, NORMAL).

---

## Moteur live — État actuel

| Paramètre | Valeur |
|---|---|
| **Statut** | 🟢 **ACTIF** — restart controle le 2026-05-27 23:22:59 CEST, PID `165502`, code `e091571` charge |
| **Phase** | Phase 4 bis active ; scope de verdict = Extension + Glissade uniquement depuis 2026-05-16 |
| **Commande** | Surveiller exits/protections/feed et tout retour de `pending broker non trackes` / `reconcile timeout`. Restart en position uniquement en recuperation controlee apres confirmation broker-side des protections |
| **Log** | `journalctl --user -u arabesque-live -f` |
| **Comptes actifs** | `ftmo_challenge` (cTrader 45667282) + `gft_compte1` (TradeLocker) |
| **Type FTMO** | Challenge Phase 1 (2-step, 100k USD) |
| **Type GFT** | Challenge (150k USD) |
| **Environnement cTrader** | **Démo** (`is_demo: true` — les challenges FTMO utilisent l'endpoint démo) |
| **Balance FTMO** | $93 298 (DD -6.7%) |
| **Balance GFT** | $142 105 (DD -5.3%) |
| **Protection FTMO** | `NORMAL` individuel (Extension streak=1, Glissade streak=1, DD=-6.70%) |
| **Protection GFT** | `CAUTION` individuel (Glissade streak=5, DD=-5.26%) ; politique pire broker => sizing effectif systeme `CAUTION x0.50` |
| **Notifications** | Telegram ✅ flux complet ; ntfy ✅ urgent uniquement (`DANGER`/`EMERGENCY`, integrite position, panne feed necessitant intervention, health `CRITIQUE`) ; **bot Telegram interactif** (lecture seule) ✅ |
| **Watchdog feed** | ✅ Timer actif ; auto-restart `feed_stale` volontaire depuis Hot Path Mode 2026-05-23 (anti-boucle/backoff), sans démarrage possible lorsque l'engine est inactif |
| **Positions observees** | `0` position / `0` pending sur FTMO et GFT avant le restart de 23:22:59 CEST ; reconciliation post-start : aucune position ouverte |
| **Dernier incident** | 2026-05-27 — ancien PID signalait `cTrader not connected while reading pending orders` et `30/31` flux actifs alors que les comptes etaient plats. Restart controle apres chargement de `44755a8`/`e091571` : `31/31`, refresh token `12h`, moteur pret, watchdog OK. |

---

## Stratégies actives — Phase 4 bis (2026-05-16)

| Stratégie | Timeframe | Instruments | Mode | Statut |
|---|---|---|---|---|
| **Extension** (trend BB) | H1 | XAUUSD, GBPJPY, AUDJPY, CHFJPY | Phase 4 bis | LIVE |
| **Extension** (trend BB) | H4 | 27 crypto (BTCUSD, ETHUSD, BNBUSD, SOLUSD…) | Phase 4 bis | LIVE |
| **Glissade** (RSI div) | H1 | XAUUSD, BTCUSD | Phase 4 bis | LIVE |
| **Cabriole** (Donchian) | H4 | — | — | 🛑 **Désactivée** (Phase 4 bis — drift `drift_modere` répété, à réexpliquer) |
| **Fouetté** (ORB M1) | M1 | — | Observation paper seulement | 🟡 0 trade live (bug cache OR bar_aggregator, cf HANDOFF) |

Noyau Phase 4 bis : **Extension + Glissade uniquement** (cf `docs/PHASE4_BIS_CHECKLIST_2026-05-16.md`).
La reprise est active sous barriere sizing. Le guard de pertes compte les
séries par broker et non les exécutions miroir : FTMO est `NORMAL`, mais GFT est
réellement
`CAUTION` (Glissade streak=5), et la politique pire broker maintient le
sizing système à `CAUTION x0.50`. Les entries déjà collectées sont fortement
sous-dimensionnées par l'empilement DD/protection/rodage. Depuis le
2026-05-16 : `n=7` entries broker Extension+Glissade,
`6/7` sous `5%` du risque nominal de `450$` ; Glissade XAUUSD GFT=`4.82$`.
Ces entries precedent toutefois le fix sizing per-broker `ab5b81a` ; elles ne
projettent pas directement la reprise. Avec les DD actuels et le code corrige,
`CAUTION` donnerait environ Extension H1 `31$` FTMO / `73$` GFT et Glissade
rodee `7.8$` / `18.2$`. L'audit mesure `1/7` distorsion materielle :
GFT Glissade XAUUSD cible `4.82$`, executable `46.01$` au minimum (`9.54x`);
les six autres entries sont dans `0.98x..1.16x`. La correction retenue rejette
avant envoi tout volume broker depassant `1.25x` le budget cible. Code charge
au restart final du 27/05 : `31/31` souscrit, moteur pret, XAU FTMO+GFT
restaures dans les deux monitors et health report `CAUTION` avec `2 ouverts`
confirme.

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

**Etat vérifié le 2026-05-27 10:07 CEST** : FTMO balance/equity
`93298.21`, GFT balance/equity `142105.25`, `0 position / 0 pending` sur
les deux comptes. Aucun niveau de protection n'est en exécution puisque le
moteur est arrêté.

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
| `ftmo_challenge` | cTrader (FTMO) | Challenge P1 2-step | 100k (act. $93 298) | +10% | 5% | 10% | 0.45% | false | Configuré, live arrêté |
| `gft_compte1` | TradeLocker (GFT) | Challenge P1 GOAT 2-step | 150k (act. $142 105) | +8% | **4%** | 10% | 0.30% | false | Configuré, live arrêté |

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
| NORMAL | > -2.5% | > -7% et pertes actives < 5 | Rien |
| CAUTION | ≤ -2.5% | ≤ -7% ou ≥ 5 pertes consécutives (stratégies actives seulement) | Risk × 0.50 |
| DANGER | ≤ -3.0% | ≤ -8% ou ≥ 8 pertes consécutives (stratégies actives seulement) | Risk × 0.25 |
| EMERGENCY | ≤ -3.5% | ≤ -9% | Risk × 0.10 (lot min) |

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
- [x] **Charger la protection GFT pre/post-fill** — active depuis le restart controle du 2026-05-27 23:22:59 CEST (`44755a8`) : quote REST GFT obligatoire avant ordre, seuil de derive adverse `0.25R`, confirmation/amend SL/TP apres fill, quarantaine des nouvelles entrees GFT si protection non prouvee, et conservation du monitoring sur fill extreme.
- [ ] **Construire le shadow reference** — specification dans `docs/VALIDATION_CONTRACT.md`; chantier d'observabilite requis avant conclusion forte de rentabilite ou hausse de risque, sans modifier la logique de trading courante.

---

## Architecture configurée (Phase 4 bis, execution live reprise le 2026-05-27)

```
Live actif (compte ftmo_challenge, 45667282, démo cTrader) :
  Extension H1 → XAUUSD, GBPJPY, AUDJPY, CHFJPY
  Extension H4 → 27 crypto
  Glissade H1  → XAUUSD, BTCUSD

Désactivée Phase 4 bis :
  Cabriole 4H  → drift_modere répété, à réexpliquer avant réactivation
  Fouetté M1   → bug cache OR bar_aggregator (0 trade live), observation paper

Testé, edge insuffisant :
  Renversé H1  → sweep + FVG retrace (WR 73%, Exp +0.006R = breakeven)
  Révérence H4 → NR7 expansion (DOGEUSD PASS WR83%, overlap 14% = complémentaire, edge mince)

Non viable :
  Pas de Deux  → pairs trading (mean-reversion, incompatible boussole)
```

Source de decision de validation courante : `docs/VALIDATION_CONTRACT.md` et
`config/validation_policy.yaml` (Phase 4 bis = Extension + Glissade depuis
`2026-05-16T08:44:00Z`, aucune hausse de risque avant echantillon propre,
invariants OK et audit sizing).
