"""
Arabesque v2 — Backtest data loader.

PRIORITÉ DES SOURCES :
  1. Parquet barres_au_sol (Dukascopy/CCXT) — données FTMO exactes
  2. Yahoo Finance — fallback si pas de Parquet

Le chemin vers les données Parquet est configurable :
  - Env var ARABESQUE_DATA_ROOT (ex: /home/user/dev/barres_au_sol/data)
  - Argument data_root dans load_ohlc()
  - Défaut : ../barres_au_sol/data (relatif au repo arabesque)

Mapping instruments :
  - Priorité 1 : config/prop_firms.yaml (arabesque) — yahoo, category, broker symbols
  - Priorité 2 : barres_au_sol/instruments.csv — résolution du chemin Parquet
  - Fallback   : heuristiques (_categorize)

Gère :
  - Gaps weekend (vendredi close → lundi open)
  - Barres manquantes (forward-fill limité)
  - Détection jours de trading pour reset daily DD
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import logging
import pandas as pd

_clean_logger = logging.getLogger("arabesque.data.clean")


# ── Configuration ────────────────────────────────────────────────────────────

def _default_data_root() -> str:
    """Chemin par défaut vers les données barres_au_sol."""
    env = os.environ.get("ARABESQUE_DATA_ROOT")
    if env:
        return env
    repo_root = Path(__file__).resolve().parent.parent.parent
    return str(repo_root.parent / "barres_au_sol" / "data")


def _instruments_csv_path() -> str:
    """Chemin vers instruments.csv de barres_au_sol."""
    env = os.environ.get("ARABESQUE_DATA_ROOT")
    if env:
        return str(Path(env).parent / "instruments.csv")
    repo_root = Path(__file__).resolve().parent.parent.parent
    return str(repo_root.parent / "barres_au_sol" / "instruments.csv")


def _prop_firms_yaml_path() -> str:
    """Chemin vers config/prop_firms.yaml d'arabesque."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    return str(repo_root / "config" / "prop_firms.yaml")


# ── Cache instruments ─────────────────────────────────────────────────────────

_INSTRUMENTS_CSV_CACHE: Optional[pd.DataFrame] = None
_PROP_FIRMS_CACHE: Optional[dict] = None


def _load_instruments_csv() -> Optional[pd.DataFrame]:
    """Charge instruments.csv de barres_au_sol (cache en mémoire)."""
    global _INSTRUMENTS_CSV_CACHE
    if _INSTRUMENTS_CSV_CACHE is not None:
        return _INSTRUMENTS_CSV_CACHE
    csv_path = _instruments_csv_path()
    if not os.path.exists(csv_path):
        return None
    df = pd.read_csv(csv_path).fillna("")
    _INSTRUMENTS_CSV_CACHE = df
    return df


def _load_prop_firms() -> dict:
    """Charge config/prop_firms.yaml (cache en mémoire).

    Retourne le dict 'instruments' du YAML, ou {} si introuvable.
    """
    global _PROP_FIRMS_CACHE
    if _PROP_FIRMS_CACHE is not None:
        return _PROP_FIRMS_CACHE
    try:
        import yaml
        path = _prop_firms_yaml_path()
        if not os.path.exists(path):
            _PROP_FIRMS_CACHE = {}
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _PROP_FIRMS_CACHE = data.get("instruments", {}) if data else {}
    except Exception:
        _PROP_FIRMS_CACHE = {}
    return _PROP_FIRMS_CACHE


def reload_prop_firms() -> None:
    """Force le rechargement de prop_firms.yaml (utile en tests)."""
    global _PROP_FIRMS_CACHE
    _PROP_FIRMS_CACHE = None


# ── Résolution des chemins Parquet ────────────────────────────────────────────

def _parquet_path_for(instrument: str, data_root: str, timeframe: str = "1h") -> Optional[str]:
    """Trouve le chemin Parquet pour un instrument FTMO via instruments.csv.

    instruments.csv (barres_au_sol) contient :
      ftmo_symbol | source (dukascopy/ccxt) | data_symbol | exchange

    Le chemin résolu est :
      <data_root>/<source>/derived/<KEY>_<timeframe>.parquet

    Où KEY est :
      - dukascopy : data_symbol.upper()
      - ccxt      : data_symbol.replace("/","").upper() + "_" + exchange.upper()
    """
    csv = _load_instruments_csv()
    if csv is None:
        return None

    matches = csv[csv["ftmo_symbol"].str.upper() == instrument.upper()]
    if matches.empty:
        return None

    row = matches.iloc[0]
    source = str(row["source"]).strip().lower()
    data_symbol = str(row["data_symbol"]).strip()
    exchange = str(row.get("exchange", "")).strip() or "binance"

    if source == "dukascopy":
        key = data_symbol.upper()
    elif source == "ccxt":
        key = data_symbol.replace("/", "").upper() + "_" + exchange.upper()
    else:
        return None

    path = os.path.join(data_root, source, "derived", f"{key}_{timeframe}.parquet")
    return path if os.path.exists(path) else None


# ── Métadonnées source ────────────────────────────────────────────────────────

class DataSourceInfo:
    """Métadonnées sur la source de données utilisée."""
    def __init__(self, source: str, path_or_symbol: str, bars: int):
        self.source = source        # "parquet" ou "yahoo"
        self.path_or_symbol = path_or_symbol
        self.bars = bars

    def __repr__(self):
        return f"[{self.source}] {self.path_or_symbol} ({self.bars} bars)"


_last_source_info: Optional[DataSourceInfo] = None


def get_last_source_info() -> Optional[DataSourceInfo]:
    """Retourne les infos de la dernière source de données chargée."""
    return _last_source_info


# ── Chargement principal ──────────────────────────────────────────────────────

def load_ohlc(
    symbol_or_instrument: str = "",
    period: str = "2y",
    interval: str = "1h",
    start: str | None = None,
    end: str | None = None,
    data_root: str | None = None,
    prefer_parquet: bool = True,
    *,
    instrument: str | None = None,
) -> pd.DataFrame:
    """Charge les données OHLC.

    PRIORITÉ :
      1. Si prefer_parquet=True → cherche dans barres_au_sol (Parquet)
      2. Si pas trouvé (ou prefer_parquet=False) → Yahoo Finance

    Args:
        symbol_or_instrument : Nom FTMO (ex: "XRPUSD") ou Yahoo (ex: "XRP-USD")
        period               : Durée Yahoo (ex: "2y", "730d"). Ignoré si start/end.
        interval             : Intervalle cible ("1h" pour Arabesque)
        start / end          : Dates optionnelles "YYYY-MM-DD"
        data_root            : Chemin vers <barres_au_sol>/data (défaut: auto)
        prefer_parquet       : True → Parquet d'abord (défaut)
        instrument           : kwarg alternatif pour le symbole (rétrocompat)

    Returns:
        DataFrame avec colonnes [Open, High, Low, Close, Volume],
        index DatetimeIndex UTC.
    """
    global _last_source_info

    effective_instrument = instrument or symbol_or_instrument

    if data_root is None:
        data_root = _default_data_root()

    # 1. Tentative Parquet
    if prefer_parquet:
        normalized = _normalize_instrument(effective_instrument)
        pq_path = _parquet_path_for(normalized, data_root, timeframe=interval)
        if pq_path:
            try:
                df = _load_from_parquet(pq_path, start=start, end=end)
                if df is not None and len(df) > 100:
                    _last_source_info = DataSourceInfo("parquet", pq_path, len(df))
                    return df
            except Exception:
                pass  # fallback Yahoo

    # 2. Fallback Yahoo Finance
    yahoo_sym = yahoo_symbol(effective_instrument)
    df = _load_from_yahoo(yahoo_sym, period=period, interval=interval, start=start, end=end)
    _last_source_info = DataSourceInfo("yahoo", yahoo_sym, len(df))
    return df


def _normalize_instrument(s: str) -> str:
    """Normalise vers un nom interne FTMO (sans suffixes Yahoo)."""
    s = s.strip().upper()
    for suffix in ("=X", "-USD", "=F"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s


def _load_from_parquet(
    path: str,
    start: str | None = None,
    end: str | None = None,
) -> Optional[pd.DataFrame]:
    """Charge un fichier Parquet barres_au_sol et normalise les colonnes."""
    df = pd.read_parquet(path)
    if df is None or df.empty:
        return None

    col_map = {
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    }
    df = df.rename(columns=col_map)

    required = ["Open", "High", "Low", "Close"]
    if not all(c in df.columns for c in required):
        return None

    if "Volume" not in df.columns:
        df["Volume"] = 0

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]

    df = _clean_ohlc(df)
    return df


def _load_from_yahoo(
    symbol: str,
    period: str = "2y",
    interval: str = "1h",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Charge les données OHLC depuis Yahoo Finance."""
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    kwargs: dict = {"interval": interval}
    if start and end:
        kwargs["start"] = start
        kwargs["end"] = end
    else:
        kwargs["period"] = period

    df = ticker.history(**kwargs)

    if df.empty:
        raise ValueError(f"Aucune donnée pour {symbol} ({kwargs})")

    cols = ["Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in cols if c in df.columns]].copy()

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    return _clean_ohlc(df)


def _clean_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Nettoie les données OHLC (NaN, zéros, cohérence H/L, spikes).

    Un spike est une bougie dont le High ou Low s'écarte anormalement
    du niveau de prix courant (artefact de données : tick erroné,
    réindexation, erreur de source).

    Critère anti-spike : si High > median_close_20 × SPIKE_FACTOR
                    ou  Low  < median_close_20 / SPIKE_FACTOR
    → la bougie est retirée et un warning est loggé.

    SPIKE_FACTOR = 3.0 : conservateur mais détecte tout multiplement
    de prix impossible sur une seule bougie H1, même en altcoin volatile.
    C'est ce filtre qui aurait éliminé le spike UNIUSD du replay 2026-02-21
    (high ~56 alors que le prix était ~6.5 → R=663 fantôme).
    """
    SPIKE_FACTOR = 3.0
    SPIKE_WINDOW = 20  # barres pour le median de référence

    mask = (
        df["Open"].notna() & df["High"].notna() &
        df["Low"].notna() & df["Close"].notna() &
        (df["Open"] > 0) & (df["Close"] > 0)
    )
    df = df[mask].copy()
    df["High"] = df[["Open", "High", "Low", "Close"]].max(axis=1)
    df["Low"]  = df[["Open", "High", "Low", "Close"]].min(axis=1)

    # Filtre anti-spike — deux mécanismes complémentaires :
    # 1. Ratio High ou Low vs median glissant des Close (capte les spikes isolés)
    median_close = df["Close"].rolling(SPIKE_WINDOW, min_periods=1, center=True).median()
    spike_ratio = (
        (df["High"] > median_close * SPIKE_FACTOR) |
        (df["Low"]  < median_close / SPIKE_FACTOR)
    )
    # 2. Ratio intrabar High/Close ou Close/Low (capte les mèches aberrantes
    #    même quand le Close lui-même est déjà élevé — invisible pour le median)
    #    Seuil : wick > SPIKE_FACTOR × taille du corps → purement aberrant sur H1
    eps = 1e-10
    spike_intrabar = (
        (df["High"] / (df["Close"] + eps) > SPIKE_FACTOR) |
        (df["High"] / (df["Open"]  + eps) > SPIKE_FACTOR) |
        ((df["Open"]  + eps) / df["Low"]  > SPIKE_FACTOR) |
        ((df["Close"] + eps) / df["Low"]  > SPIKE_FACTOR)
    )
    spike_mask = spike_ratio | spike_intrabar
    n_spikes = int(spike_mask.sum())
    if n_spikes > 0:
        _clean_logger.warning(
            f"Spike filter: {n_spikes} bougie(s) retirée(s) "
            f"(H ou L dévie de >{SPIKE_FACTOR}× le median_close_{SPIKE_WINDOW})"
        )
        for ts, row in df[spike_mask].iterrows():
            _clean_logger.warning(
                f"  Spike @ {ts}: O={row['Open']:.4f} H={row['High']:.4f} "
                f"L={row['Low']:.4f} C={row['Close']:.4f} "
                f"(median_ref={median_close[ts]:.4f})"
            )
        df = df[~spike_mask].copy()

    df["date"] = df.index.date
    return df

def split_in_out_sample(
    df: pd.DataFrame,
    in_sample_pct: float = 0.70,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split en in-sample / out-of-sample par date (chronologique)."""
    n = len(df)
    split_idx = int(n * in_sample_pct)
    return df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy()


def generate_synthetic_ohlc(
    n_bars: int = 5000,
    start_price: float = 1.0800,
    volatility: float = 0.0008,
    trend: float = 0.0,
    freq: str = "1h",
    start_date: str = "2024-02-01",
    instrument: str = "SYNTH",
) -> pd.DataFrame:
    """Génère des données OHLC synthétiques réalistes pour tester le pipeline."""
    import numpy as np

    np.random.seed(42)
    dates = pd.date_range(start=start_date, periods=n_bars * 2, freq=freq, tz="UTC")
    dates = dates[dates.dayofweek < 5][:n_bars]

    prices = [start_price]
    vol = volatility
    mr_level = start_price
    mr_speed = 0.02
    for _ in range(n_bars - 1):
        vol = 0.95 * vol + 0.05 * volatility * (1 + 0.5 * abs(np.random.randn()))
        mr_pull = mr_speed * (mr_level - prices[-1])
        prices.append(prices[-1] + trend + mr_pull + np.random.randn() * vol)
        mr_level += trend * 0.5

    prices = np.array(prices)
    opens = prices.copy()
    closes = np.roll(prices, -1); closes[-1] = prices[-1]
    highs = np.maximum(opens, closes) + np.abs(np.random.randn(n_bars)) * volatility * 0.6
    lows  = np.minimum(opens, closes) - np.abs(np.random.randn(n_bars)) * volatility * 0.6
    vols  = np.random.randint(100, 10000, n_bars).astype(float)

    df = pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=dates[:n_bars],
    )
    df["date"] = df.index.date
    return df


# ── Mapping Yahoo Finance ──────────────────────────────────────────────────────

def yahoo_symbol(instrument: str) -> str:
    """Convertit un symbole FTMO en symbole Yahoo Finance.

    Source de vérité : config/prop_firms.yaml (champ 'yahoo').
    Fallback : heuristiques (FX → XXXYYY=X, crypto → XXX-USD).
    """
    key = instrument.upper().replace(".CASH", "").replace(".C", "")

    # 1. prop_firms.yaml
    pf = _load_prop_firms()
    if key in pf and pf[key] and pf[key].get("yahoo"):
        return pf[key]["yahoo"]

    # 2. Heuristiques
    #    FX 6 chars → XXXYYY=X
    if len(key) == 6 and key.isalpha():
        return f"{key}=X"
    #    Crypto avec base connue → BASE-USD
    if key.endswith("USD") and len(key) > 6:
        return f"{key[:-3]}-USD"
    if key.endswith("USDT") and len(key) > 7:
        return f"{key[:-4]}-USD"

    return instrument


# ── Listing instruments ────────────────────────────────────────────────────────

def list_available_parquet(
    data_root: str | None = None,
    timeframe: str = "1h",
) -> dict[str, str]:
    """Liste tous les instruments disponibles en Parquet.

    Source : barres_au_sol/instruments.csv (résolution chemins).

    Returns:
        Dict {symbole_ftmo: chemin_parquet}
    """
    if data_root is None:
        data_root = _default_data_root()

    csv = _load_instruments_csv()
    if csv is None:
        return {}

    available = {}
    for _, row in csv.iterrows():
        ftmo = str(row["ftmo_symbol"]).strip()
        if not ftmo:
            continue
        path = _parquet_path_for(ftmo, data_root, timeframe)
        if path:
            available[ftmo] = path
    return available


def list_all_ftmo_instruments() -> list[dict]:
    """Liste tous les instruments depuis config/prop_firms.yaml.

    Returns:
        Liste de dicts {ftmo_symbol, gft_symbol, category, yahoo}
    """
    pf = _load_prop_firms()
    result = []
    for symbol, data in pf.items():
        if not data:
            continue
        ftmo = data.get("ftmo") or symbol
        if ftmo is None:
            continue
        result.append({
            "ftmo_symbol": ftmo,
            "gft_symbol":  data.get("gft"),
            "category":    data.get("category", "other"),
            "yahoo":       data.get("yahoo", ""),
        })
    return result


# ── Catégorisation ────────────────────────────────────────────────────────────

def _categorize(instrument: str) -> str:
    """Catégorise un instrument FTMO.

    Source de vérité : config/prop_firms.yaml.
    Fallback : heuristiques (pour les instruments non référencés).
    """
    key = instrument.upper().replace(".CASH", "").replace(".C", "")

    # 1. prop_firms.yaml
    pf = _load_prop_firms()
    if key in pf and pf[key] and pf[key].get("category"):
        return pf[key]["category"]

    # 2. Heuristiques (ordre important)

    # Métaux : XAU, XAG, XPT, XPD, XCU
    if key in ("XAUUSD","XAGUSD","XAUEUR","XAGEUR","XAUAUD","XAGAUD",
               "XCUUSD","XPTUSD","XPDUSD"):
        return "metals"

    # Énergie
    if key in ("USOIL","UKOIL","NATGAS","HEATOIL"):
        return "energy"

    # Commodities
    if key in ("COCOA","COFFEE","CORN","COTTON","SOYBEAN","SUGAR","WHEAT"):
        return "commodities"

    # Indices
    _INDICES = {
        "US30","US500","US100","US2000","USTEC","DE40","GER40",
        "UK100","JP225","AU200","AUS200","EU50","FRA40","SPN35",
        "HK50","N25","DXY",
    }
    if key in _INDICES:
        return "indices"

    # Crypto : base connue + USD
    _CRYPTO_BASES = {
        "BTC","ETH","LTC","SOL","BNB","BCH","XRP","ADA","DOGE","DOT",
        "UNI","XLM","VET","VEC","MANA","MAN","SAND","SAN","XTZ","AVAX",
        "AVA","LINK","LNK","AAVE","AAV","ALGO","ALG","NEAR","NER","IMX",
        "GRT","GAL","FET","ICP","BAR","NEO","XMR","DASH","DAS","ETC",
    }
    if key.endswith("USD") and len(key) >= 6:
        base = key[:-3]
        if base in _CRYPTO_BASES:
            return "crypto"

    # FX : 6 chars, base ET quote dans devises connues
    _FX_CURRENCIES = {
        "EUR","USD","GBP","JPY","CHF","CAD","AUD","NZD",
        "CNH","CZK","HKD","HUF","ILS","MXN","NOK","PLN",
        "SEK","SGD","ZAR","TRY","DKK",
    }
    if len(key) == 6:
        base, quote = key[:3], key[3:]
        if base in _FX_CURRENCIES and quote in _FX_CURRENCIES:
            return "fx"

    return "other"
