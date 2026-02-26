# PROMPT DE REPRISE — Arabesque v3.3 (TREND-ONLY, BE 0.3/0.20, risk 0.40%)

> Destiné à un modèle intermédiaire. Mis à jour 2026-02-26.

## Lire : `HANDOFF.md` (obligatoire avant toute action)

## STRATÉGIE VALIDÉE
- 20 mois, 1998 trades, WR 75.5%, +260R, IC99 > 0
- TREND-ONLY sur 76 instruments (forex + metals + crypto)
- BE 0.3R trigger / 0.20R offset
- Risk 0.40%/trade (DD max 8.2% < FTMO 10%)

## LIVE ENGINE — État 2026-02-26
- ✅ Connexion cTrader OK (app auth + account auth)
- ✅ 83/83 instruments chargés (249 barres H1 chacun, ~90s séquentiel)
- ✅ PriceFeedManager réutilise le broker existant (plus de ALREADY_LOGGED_IN)
- ✅ Thread-safety complète (Twisted→asyncio via _resolve_future/_asyncio_loop)
- ✅ Warnings condensés (résumé au lieu de 83 lignes/30s)
- ⚠️ BUG CRITIQUE CORRIGÉ : `_symbol_id_for_name()` retournait toujours le 1er symbole (condition always-true). Toutes les souscriptions/historiques utilisaient le même symbolId.
- Prochaine étape : relancer `--dry-run` en heures de marché et vérifier la réception de ticks sur les 83 symboles

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
