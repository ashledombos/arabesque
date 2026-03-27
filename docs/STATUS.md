# Arabesque — État opérationnel courant

> **Ce fichier = snapshot de l'état live/test à un instant T.**
> Contrairement à `DECISIONS.md` (historique des pourquoi) et `HANDOFF.md` (état de session),
> ce fichier est la référence rapide pour savoir ce qui tourne, sur quel compte, avec quel paramétrage.
> **Mettre à jour à chaque changement de compte ou de configuration live.**

Dernière mise à jour : 2026-03-27 (session Opus 4.6)

---

## Moteur live — État actuel

| Paramètre | Valeur |
|---|---|
| **Statut** | ✅ En marche |
| **Commande** | `nohup .venv/bin/python -m arabesque.live.engine > /tmp/arabesque_live.log 2>&1 &` |
| **Log** | `/tmp/arabesque_live.log` |
| **Compte actif** | `ftmo_challenge` (account_id: 45667282) |
| **Type** | Challenge Phase 1 (2-step, 100k USD) |
| **Environnement cTrader** | **Démo** (`is_demo: true` — les challenges FTMO utilisent l'endpoint démo) |
| **Balance** | ~$94 473 (DD -5.5%) |
| **Protection active** | LiveMonitor **CAUTION** (risk × 0.5) |
| **Notifications** | ntfy ✅, Telegram ✅ (corrigé 2026-03-27) |

---

## Stratégies actives

| Stratégie | Timeframe | Instruments | Mode | Statut |
|---|---|---|---|---|
| **Extension** (trend BB) | H1 | XAUUSD, GBPJPY, AUDJPY, CHFJPY | Live plein (0.45%) | ✅ Actif |
| **Extension** (trend BB) | H4 | 27 crypto (BTCUSD, ETHUSD, BNBUSD, SOLUSD…) | Live plein (0.55% via ×1.22) | ✅ Actif |
| **Glissade** (RSI div) | H1 | XAUUSD, BTCUSD | Live plein (0.45%) | ✅ Actif |

Glissade est **live** (WF 3/3 PASS, WR 83%, Exp +0.147R).

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
Le compte challenge référence `oauth: ctrader_oauth` (pas de duplication de tokens).

**Réduction linéaire de risque** : le compte est en DD -5.01%. Le `compute_sizing` réduit
automatiquement le risque : ratio ≈ 0.33 → risque effectif ~0.15% au lieu de 0.45%.
→ Passer à 0.80% une fois le compte stabilisé et la config validée.

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
ps aux | grep arabesque | grep -v grep

# Derniers logs
tail -50 /tmp/arabesque_live.log

# Equity + protection level (toutes les 2 min)
grep "💰\|🔒\|🛡️\|🚨\|⚠️" /tmp/arabesque_live.log | tail -20
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
| EMERGENCY | ≤ -3.5% | ≤ -8.0% | **Stop total + freeze** |

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

**Comparer backtest vs live** :
```bash
python tmp/compare_live_vs_backtest.py                # toute la période du journal
python tmp/compare_live_vs_backtest.py --last 7       # derniers 7 jours
```

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
- [ ] Exécuter `python tmp/compare_live_vs_backtest.py` (~1×/semaine)
- [ ] Augmenter le risk à 0.80% une fois le compte stabilisé

## Prochaines étapes structurelles

- [ ] Corrélation inter-positions : facteur par catégorie pour open_risk guard
- [x] ~~Activer GFT compte1~~ (fait 2026-03-23)

---

## Architecture snapshot (v9)

```
Live actif (compte ftmo_challenge, 45667282, démo cTrader) :
  Extension H1 → XAUUSD, GBPJPY, AUDJPY, CHFJPY (risk 0.45%)
  Extension H4 → 27 crypto (risk 0.55% via ×1.22 TF multiplier)
  Glissade H1  → XAUUSD, BTCUSD (risk 0.45%) ← activé 2026-03-22

WF validé, non encore déployé :
  Fouetté M1   → XAUUSD London, US100 NY, BTCUSD NY (fréquence à valider)
  Cabriole 4H  → crypto (73-95% overlap Extension, pas prioritaire)

Testé, edge insuffisant :
  Renversé H1  → sweep + FVG retrace (WR 73%, Exp +0.006R = breakeven)
  Révérence H4 → NR7 expansion (DOGEUSD PASS WR83%, overlap 14% = complémentaire, edge mince)

Non viable :
  Pas de Deux  → pairs trading (mean-reversion, incompatible boussole)
```
