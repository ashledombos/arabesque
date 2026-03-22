# Arabesque — État opérationnel courant

> **Ce fichier = snapshot de l'état live/test à un instant T.**
> Contrairement à `DECISIONS.md` (historique des pourquoi) et `HANDOFF.md` (état de session),
> ce fichier est la référence rapide pour savoir ce qui tourne, sur quel compte, avec quel paramétrage.
> **Mettre à jour à chaque changement de compte ou de configuration live.**

Dernière mise à jour : 2026-03-22 (session Opus 4.6)

---

## Moteur live — État actuel

| Paramètre | Valeur |
|---|---|
| **Statut** | ✅ En marche |
| **Commande** | `PYTHONUNBUFFERED=1 nohup python -m arabesque.live.engine > /tmp/arabesque_live.log 2>&1 &` |
| **Log** | `/tmp/arabesque_live.log` |
| **Compte actif** | `ftmo_swing_test` |
| **Expire** | ~2026-04-05 (renouvelé 2026-03-22) |
| **Balance** | ~$100 000 (nouveau compte) |
| **Protection active** | LiveMonitor NORMAL (aucun DD déclenché) |
| **Notifications** | Pas encore configurées (voir ci-dessous) |
| **En marche jusqu'à** | 2026-03-21 23h (pas de modification prévue) |

---

## Stratégies actives

| Stratégie | Timeframe | Instruments | Mode | Statut |
|---|---|---|---|---|
| **Extension** (trend BB) | H1 | XAUUSD, GBPJPY, AUDJPY, CHFJPY | Live plein (0.45%) | ✅ Actif |
| **Extension** (trend BB) | H4 | 27 crypto (BTCUSD, ETHUSD, BNBUSD, SOLUSD…) | Live plein (0.55% via ×1.22) | ✅ Actif |
| **Glissade** (RSI div) | H1 | XAUUSD, BTCUSD | Live plein (0.45%) | ✅ Actif |

Glissade est maintenant **live** (WF 3/3 PASS, WR 83%, Exp +0.147R).

---

## Compte actif : ftmo_swing_test

```yaml
# config/accounts.yaml
ftmo_swing_test:
  type: ctrader
  protected: false
  is_demo: false
```

**Paramètres risk (depuis accounts.yaml) :**
- `risk_per_trade_pct` : 0.45% (H1), 0.55% effectif (H4 via ×1.22)
- `max_daily_dd_pct` : 3.0% (guard interne — FTMO limite à 5%, marge de sécurité 2%)
- `max_total_dd_pct` : 8.0% (guard interne — FTMO limite à 10%, marge 2%)
- Reset journalier : minuit Europe/Prague

---

## Quand le compte expire (~2026-03-19)

1. **Activer le nouveau compte FTMO test** sur le site FTMO
2. Récupérer le nouvel `account_id` cTrader (dans l'interface FTMO → cTrader)
3. Mettre à jour `config/accounts.yaml` :
   ```yaml
   ftmo_swing_test:
     account_id: <NOUVEL_ID>   # ← seul champ qui change
   ```
4. Mettre à jour les tokens dans `config/secrets.yaml` si changés :
   ```yaml
   ftmo_swing_test:
     access_token: <NOUVEAU>
     refresh_token: <NOUVEAU>
   ```
5. Tuer et relancer le moteur :
   ```bash
   kill $(pgrep -f arabesque.live.engine)
   PYTHONUNBUFFERED=1 nohup python -m arabesque.live.engine > /tmp/arabesque_live.log 2>&1 &
   tail -f /tmp/arabesque_live.log
   ```
6. Vérifier dans les logs : `💰 balance=100000.00 equity=100000.00`

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

## Notifications — à configurer

Ajouter dans `config/secrets.yaml` (non versionné) :
```yaml
notifications:
  channels:
    - "tgram://BOTTOKEN/CHATID"      # Telegram — alertes détaillées
    - "ntfys://arabesque-urgent"      # ntfy — push urgent (DANGER/EMERGENCY)
```

Sans cette config, les alertes sont loggées localement mais pas pushées.

---

## Comptes connus

| Compte | Broker | Type | Daily DD | Total DD | Protected | Statut |
|---|---|---|---|---|---|---|
| `ftmo_swing_test` | cTrader (FTMO) | Test gratuit 15j | 5% | 10% | false | ✅ Actif (expire ~19 mars) |
| `ftmo_challenge` | cTrader (FTMO) | Challenge 100k | 5% | 10% | **true** | 🔒 Jamais en auto |
| `gft_compte2` | TradeLocker (GFT) | Funded | **4%** | 10% | false | 📋 Connecteur en dev |

⚠️ **GFT a un daily DD de 4%** (pas 5% comme FTMO). Avant d'activer `gft_compte2` en live,
réduire le risk/trade à ~0.30% et adapter `max_daily_dd_pct` dans les guards pour ce compte.

---

## Fait (2026-03-22)

- [x] Per-timeframe risk : H4 → ×1.22 (0.55% effectif), validé par backtest
- [x] Glissade activé en live (WF 3/3 PASS, WR 83%)
- [x] Per-account risk overrides (accounts.yaml) — session 2026-03-21
- [x] Guard "Best Day" (métrique backtest)
- [x] Monte Carlo barrières : Challenge 0.80% → P(+10%)=82%, P(breach)=4.5%
- [x] Scan Fouetté crypto M1 → seuls BTCUSD+BNBUSD viables, impact marginal

## Tester les notifications

```bash
# Test ntfy (push immédiat sur téléphone)
python -c "
import asyncio, apprise
async def t():
    a = apprise.Apprise()
    a.add('ntfys://arabesque_alertes_7x9k2m')
    ok = await a.async_notify(body='Test Arabesque', title='TEST')
    print('ntfy:', '✅' if ok else '❌')
asyncio.run(t())
"

# Test Telegram (⚠️ token potentiellement invalide — vérifier bot + chat_id)
python -c "
import asyncio, apprise
async def t():
    a = apprise.Apprise()
    a.add('tgram://TON_BOT_TOKEN@TON_CHAT_ID')
    ok = await a.async_notify(body='Test Arabesque', title='TEST')
    print('Telegram:', '✅' if ok else '❌')
asyncio.run(t())
"
```

**Statut 2026-03-22** : ntfy ✅ fonctionne, Telegram ❌ échec (vérifier le bot token).
Pour corriger Telegram : créer un bot via @BotFather, récupérer le token, et mettre
`tgram://BOT_TOKEN@CHAT_ID` dans `config/secrets.yaml → notifications.channels`.

## Trade journal (base de données des trades)

`logs/trade_journal.jsonl` — un fichier JSONL avec chaque entrée/sortie live.
Champs clés : `instrument`, `strategy`, `side`, `entry_price`, `exit_price`,
`result_r`, `pnl_cash`, `mfe_r`, `be_set`, `trailing_tier`, `exit_reason`.

**Comparer backtest vs live** :
```bash
# Relancer un backtest sur la même période que le live
python -m arabesque run --strategy extension --mode backtest \
    --start 2026-03-16 --end 2026-03-22 XAUUSD

# Puis comparer result_r du journal vs result_r du backtest
# Le trade_journal JSONL peut être chargé dans pandas :
python -c "
import pandas as pd
df = pd.read_json('logs/trade_journal.jsonl', lines=True)
trades = df[df.event == 'exit']
print(trades[['instrument', 'strategy', 'result_r', 'mfe_r', 'exit_reason']])
"
```

**Bug corrigé** (2026-03-22) : `risk_cash` et `volume` étaient hardcodés à 0/0.01
dans le journal → fixé dans `live.py` et `order_dispatcher.py`. Le pnl_cash sera
maintenant calculé correctement pour les nouveaux trades.

## Prochaines étapes immédiates

- [ ] Configurer les notifications Telegram/ntfy dans `secrets.yaml`
- [ ] Vérifier le moteur live avec la nouvelle config après le weekend

## Prochaines étapes structurelles

- [ ] Monte Carlo avec barrières : P(+10% avant DD 10%) pour le mode challenge
- [ ] Scanner indices + crypto M1 pour Fouetté (augmenter la fréquence de signaux)
- [ ] Guard "Best Day" en live (alerter si la journée en cours dépasse le seuil)
- [ ] Corrélation inter-positions : facteur par catégorie pour open_risk guard

---

## Architecture snapshot (v9)

```
Live actif :
  Extension H1 → XAUUSD, GBPJPY, AUDJPY, CHFJPY (risk 0.45%)
  Extension H4 → 27 crypto (risk 0.55% via ×1.22 TF multiplier)
  Glissade H1  → XAUUSD, BTCUSD (risk 0.45%) ← activé 2026-03-22

WF validé, non encore déployé :
  Fouetté M1   → XAUUSD London, US100 NY, BTCUSD NY (fréquence à valider)
  Cabriole 4H  → crypto (73-95% overlap Extension, pas prioritaire)

Testé, edge insuffisant :
  Renversé H1  → sweep + FVG retrace (WR 73%, Exp +0.006R = breakeven)
  Révérence H4 → NR7 expansion (DOGEUSD PASS WR83%, edge mince +0.059R)

Non viable :
  Pas de Deux  → pairs trading (mean-reversion, incompatible boussole)
```
