# PROMPT DE REPRISE — Arabesque v3.3 (TREND-ONLY, BE 0.3/0.20, risk 0.40%)

> Destiné à un modèle intermédiaire. Mis à jour 2026-03-01.

## Lire : `HANDOFF.md` (obligatoire avant toute action)

## STRATÉGIE VALIDÉE
- 20 mois, 1998 trades, WR 75.5%, +260R, IC99 > 0
- TREND-ONLY sur 76 instruments (forex + metals + crypto)
- BE 0.3R trigger / 0.20R offset
- Risk 0.40%/trade (DD max 8.2% < FTMO 10%)

## LIVE ENGINE — État 2026-03-01

### ✅ Validé et fonctionnel
- Connexion cTrader OK + 83/83 instruments mappés FTMO, 36/36 GFT
- PriceFeedManager avec tolérance symboles illiquides + détection weekend
- Signal generation trend-only + dispatch multi-broker
- **Cycle d'ordres complet** : place → amend SL/TP → close position
- Volume conversion : centilots (1 lot = 100)
- Symbol resolution : symbolId numérique → nom unifié
- Scripts : test_connectivity, test_order_flow, close_positions

### ✅ Position Monitor live (BE + trailing)
`arabesque/live/position_monitor.py` gère le breakeven (0.3/0.20R) et le trailing en live, câblé dans `engine.py`. Vérifié sur chaque H1 bar close, avec retry pour les amends échoués. Fix digits: `ProtoOASymbolByIdReq` pour obtenir les vrais digits (2 pour BTCUSD etc.).

### ✅ Fix diviseur de prix cTrader (2026-03-02)
Les SpotEvents et Trendbars cTrader encodent TOUS les prix en entiers avec diviseur FIXE 10^5 (100000), indépendant de digits/pipPosition. L'ancien code dérivait le diviseur de `pip_size` → changement de pip_size par `_process_symbol_details` cassait le décodage (USDJPY 100x trop grand → volumes 100x trop petits → TRADING_BAD_VOLUME).
- `_symbol_divisors` : dict séparé, jamais modifié par symbol details
- `broker/normalizer.py` : validation pré-envoi volume min/max/step
- Validation intégrée dans `place_order()` et `order_dispatcher.py`

### Notes
- TradeLocker (GFT) compte test expiré
- FTMO impose des heures de trading même sur crypto (weekend)

## Tâche A : REPLAY DE CONFIRMATION (risk 0.40%)

Vérifier que le DD diminue bien avec le nouveau risk 0.40%.

```bash
cd ~/dev/arabesque && git pull

python -m arabesque.live.engine \
  --source parquet --start 2024-07-01 --end 2026-02-20 \
  --strategy trend --balance 100000 \
  --data-root ~/dev/barres_au_sol/data \
  --instruments EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF NZDUSD \
    EURGBP EURJPY GBPJPY AUDJPY EURCAD AUDCAD GBPCAD \
    USDMXN USDZAR USDSGD XAUUSD XAGUSD \
    AUDCHF AUDNZD CADCHF CADJPY CHFJPY EURAUD EURCHF \
    EURCZK EURHUF EURNOK EURNZD EURPLN GBPAUD GBPCHF \
    GBPNZD GBPPLN NZDCAD NZDCHF NZDJPY USDCNH USDCZK \
    USDDKK USDHKD USDHUF USDILS USDNOK USDPLN USDSEK \
    USDTRY XAGAUD XAGEUR XAUAUD XAUEUR \
    BTCUSD ETHUSD SOLUSD BNBUSD XRPUSD DOGEUSD ADAUSD \
    DOTUSD AVAXUSD LINKUSD LTCUSD UNIUSD NEARUSD ICPUSD \
    FETUSD GRTUSD IMXUSD SANDUSD MANAUSD ALGOUSD VETUSD \
    XLMUSD XMRUSD ETCUSD BCHUSD DASHUSD NEOUSD GALUSD BARUSD

python scripts/analyze_replay_v2.py dry_run_XXXXXXXX_XXXXXX.jsonl --grid
```

Cibles : DD < 10%, return > +80%, WR > 70%.

## ⛔ NE PAS MODIFIER de fichiers code
## ⛔ NE PAS interpréter les résultats pour modifier la stratégie
