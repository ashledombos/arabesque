# Arabesque — État opérationnel courant

> **Ce fichier = snapshot de l'état live/test à un instant T.**
> Contrairement à `DECISIONS.md` (historique des pourquoi) et `HANDOFF.md` (état de session),
> ce fichier est la référence rapide pour savoir ce qui tourne, sur quel compte, avec quel paramétrage.
> **Mettre à jour à chaque changement de compte ou de configuration live.**

Dernière mise à jour : 2026-03-17 (session Sonnet 4.6)

---

## Moteur live — État actuel

| Paramètre | Valeur |
|---|---|
| **Statut** | ✅ En marche |
| **Commande** | `PYTHONUNBUFFERED=1 nohup python -m arabesque.live.engine > /tmp/arabesque_live.log 2>&1 &` |
| **Log** | `/tmp/arabesque_live.log` |
| **Compte actif** | `ftmo_swing_test` |
| **Expire** | ~2026-03-19 (dans ~2 jours) |
| **Balance** | ~$99 478 |
| **Protection active** | LiveMonitor NORMAL (aucun DD déclenché) |
| **Notifications** | Pas encore configurées (voir ci-dessous) |
| **En marche jusqu'à** | 2026-03-21 23h (pas de modification prévue) |

---

## Stratégies actives

| Stratégie | Timeframe | Instruments | Mode | Statut |
|---|---|---|---|---|
| **Extension** (trend BB) | H1 | XAUUSD, GBPJPY, AUDJPY, CHFJPY | Live plein | ✅ Actif |
| **Extension** (trend BB) | H4 | 27 crypto (BTCUSD, ETHUSD, BNBUSD, SOLUSD…) | Live plein | ✅ Actif |
| **Glissade** (RSI div) | H1 | XAUUSD, BTCUSD | Shadow (log seulement) | 👻 Shadow |

Les signaux Glissade sont **loggés mais non exécutés** (shadow mode). Accumuler ≥ 100 trades avant décision.

---

## Compte actif : ftmo_swing_test

```yaml
# config/accounts.yaml
ftmo_swing_test:
  type: ctrader
  protected: false
  is_demo: false
```

**Paramètres risk (PropConfig hardcodé dans guards.py) :**
- `risk_per_trade_pct` : 0.40%
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

## Prochaines étapes immédiates

- [ ] Configurer les notifications Telegram/ntfy dans `secrets.yaml`
- [ ] Renouveler le compte FTMO test quand il expire (~2026-03-19)
  → Au renouvellement : passer `risk_per_trade_pct` à **0.45%** dans `arabesque/core/guards.py`
    (LiveMonitor actif compense la marge réduite vs backtest sans protection)
- [ ] Observer les premiers trades Glissade en shadow pour valider le signal
- [ ] Quand ~100 trades Glissade shadow accumulés : décider activation

## Prochaines étapes structurelles

- [ ] Lire `max_daily_dd_pct` depuis `accounts.yaml` (actuellement hardcodé dans PropConfig)
  → permet d'adapter automatiquement le risk selon GFT 4% vs FTMO 5%
- [ ] Dry-run Fouetté 3 mois (WF PASS 4/4 mais fréquence trop basse en forex)
- [ ] Scanner plus d'instruments pour Fouetté (indices, crypto)
- [ ] Shadow live Fouetté 2-4 semaines quand fréquence validée

---

## Architecture snapshot (v9)

```
Live actif :
  Extension H1 → XAUUSD, GBPJPY, AUDJPY, CHFJPY
  Extension H4 → 27 crypto
  Glissade H1  → XAUUSD, BTCUSD (shadow)

WF validé, non encore déployé :
  Fouetté M1   → XAUUSD London, US100 NY, BTCUSD NY (fréquence à valider)
  Cabriole 4H  → crypto (73-95% overlap Extension, pas prioritaire)

Placeholder :
  Pas de Deux  → pairs trading (long terme)

Testé, edge insuffisant :
  Renversé H1  → sweep + FVG retrace (WR 73%, Exp +0.006R = breakeven)

WF en cours :
  Révérence H4 → NR7 expansion (DOGEUSD PASS WR83%, overlap Extension à vérifier)

Non viable :
  Révérence    → range contraction (NR4/NR7, inside bar) → expansion breakout
```
