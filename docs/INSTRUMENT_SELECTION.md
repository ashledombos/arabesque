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
| `indices` | SP500, NAS100, GER40 | Corrélés entre eux |
| `commodities` | COCOA, CORN, COFFEE | Saisonnalité forte |

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

## 5. Instruments actuellement suivis (fév. 2026)

### Crypto (19 instruments via CCXT/Binance)

| Arabesque | Binance pair | Statut |
|-----------|--------------|--------|
| AAVUSD | AAVE_USDT | ✅ actif |
| ALGUSD | ALGO_USDT | ✅ actif |
| AVAUSD | AVAX_USDT | ✅ actif |
| BCHUSD | BCH_USDT | ✅ actif |
| BNBUSD | BNB_USDT | ✅ actif |
| BTCUSD | BTC_USDT | ✅ actif |
| DASHUSD | DASH_USDT | ✅ actif |
| ETHUSD | ETH_USDT | ✅ actif |
| GRTUSD | GRT_USDT | ✅ actif |
| ICPUSD | ICP_USDT | ✅ actif |
| IMXUSD | IMX_USDT | ✅ actif |
| LNKUSD | LINK_USDT | ✅ actif |
| NEOUSD | NEO_USDT | ✅ actif |
| NERUSD | NEAR_USDT | ✅ actif |
| SOLUSD | SOL_USDT | ✅ actif |
| UNIUSD | UNI_USDT | ✅ actif |
| VECUSD | VET_USDT | ✅ actif |
| XLMUSD | XLM_USDT | ✅ actif |
| XRPUSD | XRP_USDT | ✅ actif |
| XTZUSD | XTZ_USDT | ✅ actif |

### Métaux (via Dukascopy)

| Arabesque | Source | Statut |
|-----------|--------|--------|
| XAUUSD | Dukascopy | ✅ actif |

> ⚠️ XAUUSD a moins de barres que les crypto (pas de weekend).
> Signal filter : `mr_shallow_narrow: metals=true` et `mr_shallow_wide: metals=false`.

---

## 6. Révision périodique

Il est recommandé de **re-valider la matrice tous les 3 mois** :

```bash
# Relancer le comparatif sur tous les instruments
python scripts/update_and_compare.py \
  --strategy combined --start 2025-01-01
```

Si un instrument montre une dégradation persistante (expectancy < 0 sur 2 runs consécutifs),
envisager de le passer à `false` dans `signal_filters.yaml` pour sa catégorie.
