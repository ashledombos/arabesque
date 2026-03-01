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

### ⚠️ Gap critique : PositionManager pas câblé en live
Le breakeven (0.3/0.20R) et le trailing existent dans `position/manager.py` mais ne sont **PAS exécutés** par le live engine. Les ordres ont le SL/TP initial, pas de gestion dynamique. → Le WR live sera inférieur au backtest tant que non implémenté. C'est le P0.

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
