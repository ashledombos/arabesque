# Fouetté — Opening Range Breakout (ORB)

## Étymologie & métaphore

**Fouetté** (danse classique) : coup de jambe bref, décisif, directionnel.
La danseuse fouette sa jambe pour générer de la rotation ou de l'élan.

**Métaphore trading** : le prix "fouette" hors du range d'ouverture NY.
Mouvement bref, impulsif, porté par le flux institutionnel à 9h30.
Le range concentre la pression. Le breakout libère l'élan.

---

## Logique de la stratégie

### Principe fondamental

À l'ouverture de New York (9h30 EST), les acteurs institutionnels
injectent du volume et définissent la direction de la journée.
Les X premières minutes (5, 15 ou 30) forment un **range de tension**.
Le premier breakout hors de ce range est statistiquement directionnel.

### Trois modes croissants en qualité

```
breakout        → entrée dès la clôture hors range
    PF ~1.14 / WR ~53%

fvg             → attend une FVG (Fair Value Gap) puis son retest
    PF ~1.65 / WR ~60%

fvg_multiple    → jusqu'à N tentatives de FVG successives [DÉFAUT]
    PF ~1.75 / WR ~65%
    + EMA filter → PF ~2.43 / WR ~37%
```

### Qu'est-ce qu'une FVG (Fair Value Gap) ?

Un FVG est un "trou" entre 3 bougies consécutives :

```
Bullish FVG : bougie[n-2].high < bougie[n].low
              ↑ gap non rempli entre ces deux niveaux

Bearish FVG : bougie[n-2].low > bougie[n].high
              ↓ gap non rempli
```

**Logique** : une FVG indique une impulsion forte (ordres institutionnels).
Le retest de la FVG = les "latecomers" repassent sur la zone.
La confirmation (close hors FVG dans la direction du breakout) = le momentum reprend.

### Séquence complète (mode fvg_multiple)

```
1. Calculer le range (high/low des N premières minutes)
2. Détecter le premier breakout (close hors range)
3. Chercher une FVG formée lors du mouvement de breakout
4. Attendre que le prix retouche la FVG (mèche ou corps)
5. Confirmer sur la bougie qui close DANS la direction du breakout
6. Entrée au OPEN de la bougie suivante (anti-lookahead strict)
7. Si la FVG est nullifiée (close à travers elle) → tenter une nouvelle FVG
   jusqu'à max_attempts fois
```

### EMA filter (shadow par défaut)

L'EMA filtre les trades à contre-courant de la tendance M1 :
- LONG seulement si close > EMA au moment du signal
- SHORT seulement si close < EMA

En mode shadow (`ema_filter_active: false`), le filtre **logue** sans bloquer :
```
[Fouette] 👻 EMA shadow: XAUUSD BUY close=2345.50 ema=2347.20 → AURAIT été filtré
```
→ Accumuler 100+ occurrences avant de décider d'activer.

---

## Paramètres clés

| Paramètre | Valeur défaut | Description |
|---|---|---|
| `mode` | `fvg_multiple` | breakout / fvg / fvg_multiple |
| `range_minutes` | 30 | Durée du range d'ouverture |
| `fvg_max_attempts` | 3 | Tentatives max de FVG |
| `rr_tp` | 1.0 | TP = rr_tp × taille du range |
| `sl_source` | range | SL au bord opposé du range (ou de la FVG) |
| `sl_buffer_factor` | 0.05 | 5% du range en buffer SL |
| `ema_filter_active` | False | False = shadow uniquement |
| `ema_period` | 20 | Période EMA filter |
| `auto_dst` | True | Ajustement automatique heure été/hiver NY |

---

## Gestion des positions

La stratégie fixe un TP indicatif à `rr_tp × or_range`.
Le **tick-level TSL** (position_monitor.py) prend le relais :
- BE trigger : 0.3R (non modifiable)
- Trailing tiers : 1.5R / 2.0R / 3.0R

Pour les presets `high_rr` et `ema_active`, le TP indicatif est intentionnellement
grand — le TSL trailing capturera des mouvements de 5–10R sur les bonnes sessions.

---

## Timeframe et données

| | Valeur |
|---|---|
| Timeframe d'exécution | **M1** (barres 1 minute) |
| Timeframe de signal | M1 (idem — pas d'HTF dans cette stratégie) |
| Instruments validés | XAUUSD (or), indices (NAS100, US30), crypto |
| Instruments à éviter | Forex majeurs (spread trop large relatif au range) |

### Adaptation bar_aggregator

Cette stratégie nécessite des barres M1, pas H1 comme Extension.
Ajouter dans `execution/bar_aggregator.py` :
```python
elif signal_strategy == "fouette":
    from arabesque.strategies.fouette.signal import FouetteSignalGenerator, FouetteConfig
    sig_gen = FouetteSignalGenerator(FouetteConfig())
    # + configurer aggregation_period = "1min"
```

---

## Gestion DST

NY est en Eastern Time :
- **EST (UTC-5)** : novembre → mars → `session_open_hour_utc = 14`
- **EDT (UTC-4)** : mars → novembre → `session_open_hour_utc = 13`

Avec `auto_dst: true` (défaut), la détection est automatique (approximation par mois).
Pour les transitions exactes, configurer manuellement.

---

## Workflow de validation

```
[ ] Backtest IS (60%) XAUUSD M1 ≥ 100 trades, Exp > 0
[ ] Backtest OOS (40%) cohérent
[ ] Wilson CI99 lower > 0 (scripts/run_stats.py)
[ ] Dry-run parquet M1 3 mois
[ ] Shadow 2-4 semaines live (EMA shadow actif)
[ ] Décision activation EMA filter
[ ] Live réel
```

---

## Résultats de référence (transcript)

Source : backtests sur ~200 jours, XAUUSD M1, auteur YouTube (mars 2026).

| Preset | PF | WR | Trades | Notes |
|---|---|---|---|---|
| breakout (15m, RR2) | 1.14 | 53% | 139 | Baseline |
| fvg (30m, RR1) | 1.65 | ~60% | 82 | +EMA → 1.90 |
| fvg_multiple (30m, RR1) | 1.75 | ~65% | ~90 | Meilleur sans EMA |
| fvg_multiple + EMA | 2.43 | 37% | 114 | TP2 = 6R |
| fvg_multiple + EMA | 3.0 | ~35% | ~110 | TP2 = 8–10R + TSL |

*Ces résultats sont à reproduire en interne avant toute décision live.*

---

## Statut

| | |
|---|---|
| Statut | 🟡 En développement (signal.py créé, backtest à lancer) |
| Stratégie live | Extension (ne pas interférer) |
| Données requises | M1 XAUUSD Dukascopy (dispo dans data/min1/) |
| Prochain jalon | Backtest IS/OOS + Wilson CI99 |
