# Données OHLC Arabesque

Répertoire de stockage des données Parquet (gitignored sauf ce README).

Structure :
```
data/
├── dukascopy/
│   ├── min1/          ← Barres 1 minute brutes
│   └── derived/       ← Barres dérivées (5m, 1h)
├── ccxt/
│   ├── min1/
│   └── derived/
└── README.md
```

Téléchargement : `python -m arabesque fetch --from 2024-01-01`
Source : Dukascopy (forex/metals), CCXT/Binance (crypto)
