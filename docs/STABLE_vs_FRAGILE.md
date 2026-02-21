# Arabesque — Ce qui est stable vs ce qui est fragile

> Dernière mise à jour : 2026-02-21  
> Ce document répond à : "si je touche X, qu'est-ce que je peux casser ?"  
> À mettre à jour à chaque session où quelque chose se révèle plus fragile que prévu.

---

## 🟢 Stable — Ne pas toucher sans raison forte

Ces composants ont été debuggés, testés, et leur comportement est bien compris.  
Un changement ici nécessite un replay complet + `analyze_replay.py`.

### `arabesque/models.py` — Signal, Position, Decision

Le contrat est stable :
- `Signal.__init__` prend `close=` et `open_=` (PAS `tv_close=`)
- `sig.tp_indicative` (PAS `sig.tp`)
- `sig.side` = enum `Side.LONG`/`Side.SHORT`
- `Position.result_r`, `.mfe_r`, `.current_r` sont des propriétés calculées

**Risque si modifié** : tout le pipeline casse (backtest + replay + live).

### `arabesque/position/manager.py` — v3.0 ROI dégressif + trailing ajusté

**REFONTE v3.0** (2026-02-21, session Opus 4.6) :
- ROI dégressif (4 paliers) — mécanisme clé alignement BB_RPB_TSL
- Trailing réduit à 3 paliers (>= 1.5R MFE seulement)
- BE relevé à +1.0R, time-stop étendu à 336 barres
- Bug TD-002 corrigé : `EXIT_TRAILING` correctement tagué
- Nouveau : `EXIT_ROI` pour le suivi statistique

**Statut** : MODIFIÉ, à valider sur replay P3a.

**Risque si modifié** : le ROI dégressif est maintenant le principal moteur du WR. Tout changement doit être comparé sur replay avant/après avec focus sur le WR.

**⚠️ Modifications réservées à Opus 4.6** — ne pas toucher avec un modèle moins puissant.

### `arabesque/guards.py` — Guards prop firm

Bugs TD-001 et TD-002 corrigés (2026-02-20) :
- `daily_dd_pct` divisé par `daily_start_balance` (plus `start_balance`)
- `remaining_daily` cohérent avec le diviseur corrigé

**Risque si modifié** : les guards sont la seule protection contre la disqualification prop firm. Un bug ici = compte grillé.

### `arabesque/live/parquet_clock.py` — Replay chronologique

Architecture stabilisée :
- `only_last_bar=False` + `_seen_signals` par instrument (déduplique)
- Signaux générés sur bougie `i`, exécutés au open de `i+1` (anti-lookahead)
- Période étendue de +1 jour pour capturer les fills en fin de window

**Risque si modifié** : les bugs historiques (0 signaux, doublons massifs) sont tous liés à ce module. Toute modification nécessite un replay complet + vérification du compte de trades.

### `arabesque/broker/adapters.py` — DryRunAdapter

Fixé 2026-02-21 :
- `on_trade_closed(pnl)` met à jour `_equity` et `_balance`
- `get_account_info()` retourne l'état réel (plus 100k fixes)

---

## 🟡 Fragile — Modifier avec précaution

Ces composants fonctionnent, mais ont tendance à casser quand on les touche.

### `arabesque/backtest/signal_gen_combined.py` — CombinedSignalGenerator

**Pourquoi fragile** : c'est le cœur stratégique. Beaucoup de paramètres interdépendants.  
**Historique des régressions** :
- Modification du filtre slippage → 96% des signaux rejetés
- Ajout de `only_last_bar=True` → 0 signaux en replay
- Renommage `tv_close` → `close` mal propagé → crash live

**Précautions** :
1. Avant tout changement : lancer `scripts/debug_pipeline.py` pour vérifier le contrat
2. Après : `scripts/update_and_compare.py` pour comparer avec run N-1
3. Validation finale : replay + `analyze_replay.py`

### `arabesque/backtest/data.py` — Chargement Parquet + filtre spike

**Pourquoi fragile** : interface entre les données sources (qualité variable) et le pipeline.  
**Problème récurrent** : spikes de données dans les parquets (barres avec H×10 le prix normal).  
**Fix actuel** : double filtre dans `_clean_ohlc` — ratio vs median ET ratio intrabar.  
**Limite connue** : si toute une période de données est à un niveau de prix différent (split, erreur historique), aucun filtre ne peut détecter ça automatiquement.

**Précautions** :
- Diagnostiquer les parquets sources avant chaque replay (voir commande dans HANDOFF.md)
- Après modification de `_clean_ohlc` : vérifier que les 17 instruments chargent le même nombre de barres (+/- 5 barres)

### `arabesque/webhook/orchestrator.py` — Sizing + dispatch

**Pourquoi fragile** : gère l'AccountState qui alimente les guards. Un bug ici = guards aveugles.  
**Point sensible** : `_update_account_on_close` — équilibre entre AccountState (orchestrator) et DryRunAdapter.equity.

**Précautions** : après tout changement, vérifier que `get_status()["equity"]` évolue correctement pendant un replay.

### `config/prop_firms.yaml` — 122 instruments

**Pourquoi fragile** : la sélection d'instruments est à la fois une source de biais ET d'edge.  
**Risque** : ajouter des instruments non validés = dilution de l'edge et surcharge de positions.

**Règle** : un instrument n'entre dans `follow: true` qu'après `run_stats.py` IC95 positif + pipeline Stage 3.

---

## 🔴 Non validé — Ne pas déployer en live

Ces composants sont implémentés mais n'ont pas été testés end-to-end.

### `arabesque/broker/ctrader.py` — Connexion live cTrader

Implémenté mais jamais testé en ordre réel. Voir HANDOFF.md §7 pour les étapes de validation.

### `arabesque/broker/tradelocker.py` — TradeLocker / GFT

Même statut que ctrader.py.

### Module `arabesque/live/bar_aggregator.py` + `price_feed.py`

Chemin live (ticks → barres H1). Implémenté, jamais validé en production.

---

## Leçons apprises — Régressions passées

| Session | Ce qui a cassé | Cause | Fix |
|---|---|---|---|
| 2026-02-18 | 96% signaux rejetés | Guard slippage comparait `tv_close` vs `open_next_bar` (1h d'écart = toujours > seuil) | Comparer `fill` vs `open_next_bar` |
| 2026-02-18 | 0 signaux en replay | `only_last_bar=True` incompatible avec le rebuild du cache | `only_last_bar=False` + `_seen_signals` |
| 2026-02-18 | 55+ trades WR 25% | Suppression du filtre sans tracking → doublons massifs | Set `_seen_signals` par timestamp |
| 2026-02-20 | Crash live signal | `tv_close=` dans `Signal.__init__()` n'est pas un champ | Utiliser `close=` |
| 2026-02-20 | Guards DD aveugles | `daily_dd_pct / start_balance` au lieu de `/ daily_start_balance` | Fix diviseur |
| 2026-02-21 | R=663.5 UNIUSD | Barre corrompue dans parquet (H≈57, prix normal ≈6.5) | Filtre intrabar dans `_clean_ohlc` |
| 2026-02-21 | Equity tracking faux | `DryRunAdapter.get_account_info()` retournait 100k fixes | `on_trade_closed(pnl)` |
| 2026-02-21 | WR 52% au lieu de 90% | Mécanisme `minimal_roi` de BB_RPB_TSL absent + trailing trop agressif + SL trop serré | v3.0 : ROI dégressif + trailing ajusté + SL élargi |

---

## Règle générale de modification

Avant de toucher un composant :

```
1. Lire le historique de ce composant dans docs/decisions_log.md
2. Si ROUGE ou JAUNE : identifier le test de non-régression avant de commencer
3. Modifier
4. Valider : debug_pipeline.py → backtest.py un instrument → replay → analyze_replay.py
5. Mettre à jour ce fichier si le composant s'est révélé plus/moins fragile
```
