# Arabesque — Sélection des instruments

> Ce document explique comment choisir les instruments à trader,
> comment la matrice de filtres a été construite, et comment la mettre à jour.

---

## 1. Principe général

Tous les instruments ne répondent pas de la même façon aux mêmes stratégies.
La sélection repose sur **deux niveaux** :

1. **Catégorisation** de l'instrument (crypto, fx, metals, etc.)
2. **Matrice sub_type × catégorie** (`config/signal_filters.yaml`)
   qui autorise ou bloque chaque combinaison stratégie/instrument

---

## 2. Catégories d'instruments

La catégorisation est définie dans `arabesque/backtest/data.py` (`_categorize`) :

| Catégorie | Exemples | Particularités |
|-----------|----------|----------------|
| `crypto` | XRPUSD, SOLUSD, BTCUSD, BNBUSD | Haute volatilité, pas de weekend gaps |
| `fx` | EURUSD, GBPUSD, USDJPY | Spreads serrés, volume 24h |
| `metals` | XAUUSD, XAGUSD | Spreads larges, drive macro |
| `energy` | USOIL, UKOIL, NATGAS | Très réactif aux news |
| `indices` | US500, US100, GER40 | Corrélés entre eux |
| `commodities` | COCOA, CORN, COFFEE | Saisonnalité forte |
| `stocks` | AAPL, TSLA, MSFT | Gaps earnings, horaires restreints |

---

## 3. Matrice d'activation (`signal_filters.yaml`)

Bâtie sur l'analyse OOS de **6 759 trades / 102 instruments (Phase 1.3)**.
Elle encode quelles combinaisons `sub_type` × `catégorie` sont autorisées en production.

### Lecture de la matrice

```yaml
signal_filters:
  mr_deep_wide:          # sub_type du signal
    crypto:  true        # autorisé sur crypto
    fx:      false       # bloqué sur FX
    metals:  false       # bloqué sur métaux
```

`true` = edge positif OOS validé → trade autorisé  
`false` = edge négatif ou trop peu de trades → bloqué

### Matrice complète actuelle

| sub_type | crypto | fx | metals | indices | energy | commodities |
|---|---|---|---|---|---|---|
| `mr_shallow_wide` | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| `mr_shallow_narrow` | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| `mr_deep_wide` | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ |
| `mr_deep_narrow` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `trend_strong` | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ |
| `trend_moderate` | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |

---

## 4. Pipeline de sélection d'un nouvel instrument

### Étape 1 — Données disponibles ?

```bash
# Vérifier que le fichier Parquet H1 existe
ls ~/dev/arabesque/data/parquet/NOMUSD_H1.parquet
```

Si absent : mettre à jour `barres_au_sol` d'abord (voir HANDOVER.md §5).

### Étape 2 — Backtest exploratoire (--no-filter)

```bash
# Désactiver le filtre pour voir les signaux bruts
python -m arabesque.backtest.runner --strategy combined \
  --no-filter --start 2024-01-01 --end 2025-06-01 \
  NOMUSD
```

Critères minimaux pour continuer :
- `TRADES ≥ 30` (en in-sample)
- `Expectancy > +0.10R` (OOS)
- `Max DD < 5%`
- `Win Rate ≥ 45%`

### Étape 3 — Identifier la catégorie

Ajouter l'instrument dans `arabesque/backtest/data.py` (`_categorize`) si non reconnu automatiquement.

### Étape 4 — Tester sub_type par sub_type

Relancer avec `--no-filter` puis analyser quels sub_types génèrent des signaux.
Croiser avec la matrice actuelle pour voir si la combinaison est déjà couverte.

### Étape 5 — Mettre à jour `signal_filters.yaml`

Si le nouvel instrument appartient à une catégorie non couverte **et** montre un edge positif,
ajouter une entrée dans `config/signal_filters.yaml` et documenter la décision ici.

---

## 5. Instruments supportés (150+ instruments FTMO/GFT)

### Légende statut Parquet

| Symbole | Signification |
|---------|---------------|
| ✅ | Parquet H1 disponible (barres_au_sol) |
| 📦 | Disponible via Yahoo Finance (fallback) |
| ❌ | Non configuré |

### 5.1 Indices (14 instruments)

| Interne | FTMO | GFT | Statut Parquet | Yahoo |
|---------|------|-----|----------------|-------|
| US500 | US500.cash | SPX500.X | 📦 | ^GSPC |
| US100 | US100.cash | NAS100.X | 📦 | ^NDX |
| US30 | US30.cash | US30.X | 📦 | ^DJI |
| UK100 | UK100.cash | UK100.X | 📦 | ^FTSE |
| GER40 | GER40.cash | GER40.X | 📦 | ^GDAXI |
| JP225 | JP225.cash | JAP225.X | 📦 | ^N225 |
| N25 | N25.cash | — | 📦 | ^N225 |
| AUS200 | AUS200.cash | AUS200.X | 📦 | ^AXJO |
| HK50 | HK50.cash | — | 📦 | ^HSI |
| FRA40 | FRA40.cash | — | 📦 | ^FCHI |
| EU50 | EU50.cash | — | 📦 | ^STOXX50E |
| SPN35 | SPN35.cash | — | 📦 | ^IBEX |
| US2000 | US2000.cash | — | 📦 | ^RUT |
| DXY | DXY.cash | — | 📦 | DX-Y.NYB |

### 5.2 Forex (47 paires)

| Interne | FTMO | GFT | Statut Parquet | Yahoo |
|---------|------|-----|----------------|-------|
| EURUSD | EURUSD | EURUSD.X | 📦 | EURUSD=X |
| GBPUSD | GBPUSD | GBPUSD.X | 📦 | GBPUSD=X |
| USDJPY | USDJPY | USDJPY.X | 📦 | USDJPY=X |
| AUDUSD | AUDUSD | AUDUSD.X | 📦 | AUDUSD=X |
| USDCAD | USDCAD | USDCAD.X | 📦 | USDCAD=X |
| USDCHF | USDCHF | USDCHF.X | 📦 | USDCHF=X |
| NZDUSD | NZDUSD | NZDUSD.X | 📦 | NZDUSD=X |
| EURGBP | EURGBP | EURGBP.X | 📦 | EURGBP=X |
| EURJPY | EURJPY | EURJPY.X | 📦 | EURJPY=X |
| GBPJPY | GBPJPY | GBPJPY.X | 📦 | GBPJPY=X |
| AUDJPY | AUDJPY | AUDJPY.X | 📦 | AUDJPY=X |
| EURCAD | EURCAD | EURCAD.X | 📦 | EURCAD=X |
| AUDCAD | AUDCAD | AUDCAD.X | 📦 | AUDCAD=X |
| AUDCHF | AUDCHF | AUDCHF.X | 📦 | AUDCHF=X |
| GBPAUD | GBPAUD | GBPAUD.X | 📦 | GBPAUD=X |
| EURAUD | EURAUD | EURAUD.X | 📦 | EURAUD=X |
| NZDCAD | NZDCAD | NZDCAD.X | 📦 | NZDCAD=X |
| NZDCHF | NZDCHF | NZDCHF.X | 📦 | NZDCHF=X |
| CADCHF | CADCHF | CADCHF.X | 📦 | CADCHF=X |
| GBPCAD | GBPCAD | GBPCAD.X | 📦 | GBPCAD=X |
| EURNZD | EURNZD | EURNZD.X | 📦 | EURNZD=X |
| NZDJPY | NZDJPY | NZDJPY.X | 📦 | NZDJPY=X |
| GBPNZD | GBPNZD | GBPNZD.X | 📦 | GBPNZD=X |
| CADJPY | CADJPY | CADJPY.X | 📦 | CADJPY=X |
| CHFJPY | CHFJPY | CHFJPY.X | 📦 | CHFJPY=X |
| EURCHF | EURCHF | EURCHF.X | 📦 | EURCHF=X |
| GBPCHF | GBPCHF | GBPCHF.X | 📦 | GBPCHF=X |
| AUDNZD | AUDNZD | AUDNZD.X | 📦 | AUDNZD=X |
| EURCZK | EURCZK | — | 📦 | EURCZK=X |
| EURPLN | EURPLN | — | 📦 | EURPLN=X |
| EURHUF | EURHUF | — | 📦 | EURHUF=X |
| EURNOK | EURNOK | — | 📦 | EURNOK=X |
| USDPLN | USDPLN | — | 📦 | USDPLN=X |
| USDNOK | USDNOK | — | 📦 | USDNOK=X |
| USDSEK | USDSEK | — | 📦 | USDSEK=X |
| USDMXN | USDMXN | — | 📦 | USDMXN=X |
| USDHKD | USDHKD | — | 📦 | USDHKD=X |
| USDHUF | USDHUF | — | 📦 | USDHUF=X |
| USDSGD | USDSGD | — | 📦 | USDSGD=X |
| USDZAR | USDZAR | — | 📦 | USDZAR=X |
| GBPPLN | GBPPLN | — | 📦 | GBPPLN=X |
| USDCNH | USDCNH | — | 📦 | USDCNH=X |
| USDCZK | USDCZK | — | 📦 | USDCZK=X |
| USDILS | USDILS | — | 📦 | USDILS=X |
| USDDKK | USDDKK | — | 📦 | USDDKK=X |
| USDTRY | USDTRY | — | 📦 | USDTRY=X |

### 5.3 Métaux (8 instruments)

| Interne | FTMO | GFT | Statut Parquet | Yahoo |
|---------|------|-----|----------------|-------|
| XAUUSD | XAUUSD | XAUUSD.X | ✅ | GC=F |
| XAGUSD | XAGUSD | XAGUSD.X | 📦 | SI=F |
| XAUEUR | XAUEUR | — | 📦 | GC=F (approx) |
| XAGEUR | XAGEUR | — | 📦 | SI=F (approx) |
| XAGAUD | XAGAUD | — | 📦 | SI=F (approx) |
| XPDUSD | XPDUSD | — | 📦 | PA=F |
| XPTUSD | XPTUSD | — | 📦 | PL=F |
| XCUUSD | XCUUSD | — | 📦 | HG=F |

### 5.4 Énergies (4 instruments)

| Interne | FTMO | GFT | Statut Parquet | Yahoo |
|---------|------|-----|----------------|-------|
| USOIL | USOIL.cash | WTI.X | 📦 | CL=F |
| UKOIL | UKOIL.cash | BRENT.X | 📦 | BZ=F |
| NATGAS | NATGAS.cash | — | 📦 | NG=F |
| HEATOIL | HEATOIL.c | — | 📦 | HO=F |

### 5.5 Commodities Agricoles (6 instruments)

| Interne | FTMO | GFT | Statut Parquet | Yahoo |
|---------|------|-----|----------------|-------|
| WHEAT | WHEAT.c | — | 📦 | ZW=F |
| SOYBEAN | SOYBEAN.c | — | 📦 | ZS=F |
| COTTON | COTTON.c | — | 📦 | CT=F |
| CORN | CORN.c | — | 📦 | ZC=F |
| COFFEE | COFFEE.c | — | 📦 | KC=F |
| COCOA | COCOA.c | — | 📦 | CC=F |

### 5.6 Cryptomonnaies (30+ instruments)

| Interne | FTMO | GFT | Statut Parquet | Yahoo |
|---------|------|-----|----------------|-------|
| BTCUSD | BTCUSD | BTCUSD.X | ✅ | BTC-USD |
| ETHUSD | ETHUSD | ETHUSD.X | ✅ | ETH-USD |
| LTCUSD | LTCUSD | LTCUSD.X | ✅ | LTC-USD |
| BNBUSD | BNBUSD | BNBUSD.X | ✅ | BNB-USD |
| BCHUSD | BCHUSD | BCHUSD.X | ✅ | BCH-USD |
| SOLUSD | SOLUSD | SOLUSD.X | ✅ | SOL-USD |
| XRPUSD | XRPUSD | — | ✅ | XRP-USD |
| ADAUSD | ADAUSD | — | ✅ | ADA-USD |
| DOGEUSD | DOGEUSD | — | 📦 | DOGE-USD |
| AVAXUSD | AVAUSD | — | ✅ | AVAX-USD |
| LINKUSD | LNKUSD | — | ✅ | LINK-USD |
| NEARUSD | NERUSD | — | ✅ | NEAR-USD |
| NEOUSD | NEOUSD | — | ✅ | NEO-USD |
| DASHUSD | DASHUSD | — | ✅ | DASH-USD |
| XMRUSD | XMRUSD | — | 📦 | XMR-USD |
| DOTUSD | DOTUSD | — | 📦 | DOT-USD |
| ALGOUSD | ALGUSD | — | ✅ | ALGO-USD |
| VECUSD | VECUSD | — | ✅ | VET-USD |
| UNIUSD | UNIUSD | — | ✅ | UNI-USD |
| XLMUSD | XLMUSD | — | ✅ | XLM-USD |
| GALUSD | GALUSD | — | 📦 | GAL-USD |
| MANUSD | MANUSD | — | 📦 | MANA-USD |
| IMXUSD | IMXUSD | — | ✅ | IMX-USD |
| GRTUSD | GRTUSD | — | ✅ | GRT-USD |
| ICPUSD | ICPUSD | — | ✅ | ICP-USD |
| FETUSD | FETUSD | — | 📦 | FET-USD |
| XTZUSD | XTZUSD | — | ✅ | XTZ-USD |

### 5.7 Actions CFD (30+ instruments)

> ⚠️ **Non priorisés en Phase 1** : comportement différent (gaps earnings, horaires restreints, dividendes).
> Nécessitent des guards spécifiques avant activation.

| Interne | FTMO | GFT | Catégorie | Yahoo |
|---------|------|-----|-----------|-------|
| AAPL | AAPL | AAPL.X | stocks | AAPL |
| TSLA | TSLA | TSLA.X | stocks | TSLA |
| MSFT | MSFT | MSFT.X | stocks | MSFT |
| AMZN | AMZN | AMZN.X | stocks | AMZN |
| META | META | META.X | stocks | META |
| NVDA | NVDA | — | stocks | NVDA |
| GOOG | GOOG | — | stocks | GOOG |
| *(25 autres)* | ... | ... | stocks | ... |

---

## 6. Extensibilité multi-prop-firms

### Architecture future-proof

Le fichier `config/prop_firms.yaml` centralise les mappings :

```yaml
instruments:
  EURUSD:
    ftmo: "EURUSD"
    gft: "EURUSD.X"
    fundednext: "EURUSD"   # futur broker
    category: "fx"
    yahoo: "EURUSD=X"
```

### Ajout d'un nouveau prop firm

1. Ajouter une colonne dans `prop_firms.yaml` (ex: `fundednext`)
2. Implémenter l'adapter broker dans `arabesque/adapters/<broker>.py`
3. L'adapter lit `prop_firms.yaml` pour mapper symbole interne → symbole broker
4. Aucun changement dans le core (`SignalGenerator`, `PositionManager`, etc.)

---

## 7. Révision périodique

Il est recommandé de **re-valider la matrice tous les 3 mois** :

```bash
# Relancer le comparatif sur tous les instruments
python scripts/update_and_compare.py \
  --strategy combined --start 2025-01-01
```

Si un instrument montre une dégradation persistante (expectancy < 0 sur 2 runs consécutifs),
envisager de le passer à `false` dans `signal_filters.yaml` pour sa catégorie.
