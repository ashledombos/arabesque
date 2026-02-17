"""
Arabesque v2 — Backtest data loader.

PRIORITÉ DES SOURCES :
  1. Parquet barres_au_sol (Dukascopy/CCXT) — données FTMO exactes
  2. Yahoo Finance — fallback si pas de Parquet

Le chemin vers les données Parquet est configurable :
  - Env var ARABESQUE_DATA_ROOT (ex: /home/user/dev/barres_au_sol/data)
  - Argument data_root dans load_ohlc()
  - Défaut : ../barres_au_sol/data (relatif au repo arabesque)

Gère :
- Gaps weekend (vendredi close → lundi open)
- Barres manquantes (forward-fill limité à 3 barres)
- Détection jours de trading pour reset daily DD
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pandas as pd


# ── Configuration ────────────────────────────────────────────────

def _default_data_root() -> str:
    """Chemin par défaut vers les données barres_au_sol."""
    # Env var en priorité
    env = os.environ.get("ARABESQUE_DATA_ROOT")
    if env:
        return env
    # Sinon, relatif au repo arabesque (../barres_au_sol/data)
    repo_root = Path(__file__).resolve().parent.parent.parent
    return str(repo_root.parent / "barres_au_sol" / "data")


def _instruments_csv_path() -> str:
    """Chemin vers instruments.csv de barres_au_sol."""
    env = os.environ.get("ARABESQUE_DATA_ROOT")
    if env:
        return str(Path(env).parent / "instruments.csv")
    repo_root = Path(__file__).resolve().parent.parent.parent
    return str(repo_root.parent / "barres_au_sol" / "instruments.csv")


# ── Mapping instruments ─────────────────────────────────────────

# Cache du CSV instruments (chargé une seule fois)
_INSTRUMENTS_CACHE: Optional[pd.DataFrame] = None


def _load_instruments_csv() -> Optional[pd.DataFrame]:
    """Charge instruments.csv, retourne None si introuvable."""
    global _INSTRUMENTS_CACHE
    if _INSTRUMENTS_CACHE is not None:
        return _INSTRUMENTS_CACHE

    csv_path = _instruments_csv_path()
    if not os.path.exists(csv_path):
        return None

    df = pd.read_csv(csv_path).fillna("")
    _INSTRUMENTS_CACHE = df
    return df


def _parquet_path_for(instrument: str, data_root: str, timeframe: str = "1h") -> Optional[str]:
    """Trouve le chemin Parquet pour un instrument FTMO.

    Cherche dans instruments.csv la correspondance :
      FTMO symbol → source (dukascopy/ccxt) → data_symbol → KEY → fichier dérivé

    Returns:
        Chemin absolu vers le fichier Parquet, ou None si introuvable.
    """
    csv = _load_instruments_csv()
    if csv is None:
        return None

    # Chercher l'instrument dans le CSV (colonne ftmo_symbol)
    matches = csv[csv["ftmo_symbol"].str.upper() == instrument.upper()]
    if matches.empty:
        return None

    row = matches.iloc[0]
    source = str(row["source"]).strip().lower()
    data_symbol = str(row["data_symbol"]).strip()
    exchange = str(row.get("exchange", "")).strip() or "binance"

    # Construire la clé (même logique que data_orchestrator.py)
    if source == "dukascopy":
        key = data_symbol.upper()
    elif source == "ccxt":
        key = data_symbol.replace("/", "").upper() + "_" + exchange.upper()
    else:
        return None

    # Chemin du fichier dérivé
    path = os.path.join(data_root, source, "derived", f"{key}_{timeframe}.parquet")
    if os.path.exists(path):
        return path

    return None


# ── Chargement OHLC ─────────────────────────────────────────────

class DataSourceInfo:
    """Métadonnées sur la source de données utilisée."""
    def __init__(self, source: str, path_or_symbol: str, bars: int):
        self.source = source                # "parquet" ou "yahoo"
        self.path_or_symbol = path_or_symbol
        self.bars = bars

    def __repr__(self):
        return f"[{self.source}] {self.path_or_symbol} ({self.bars} bars)"


# Dernier source info (accessible après load_ohlc)
_last_source_info: Optional[DataSourceInfo] = None


def get_last_source_info() -> Optional[DataSourceInfo]:
    """Retourne les infos de la dernière source de données chargée."""
    return _last_source_info


def load_ohlc(
    symbol_or_instrument: str,
    period: str = "2y",
    interval: str = "1h",
    start: str | None = None,
    end: str | None = None,
    data_root: str | None = None,
    prefer_parquet: bool = True,
) -> pd.DataFrame:
    """Charge les données OHLC.

    PRIORITÉ :
      1. Si prefer_parquet=True, cherche dans barres_au_sol (Parquet)
      2. Sinon, ou si pas trouvé, charge depuis Yahoo Finance

    Args:
        symbol_or_instrument: Nom FTMO (ex: "EURUSD") ou symbole Yahoo (ex: "EURUSD=X")
        period: Période Yahoo (ex: "2y", "730d"). Ignoré si start/end.
        interval: Intervalle ("1h" pour Arabesque)
        start/end: Dates optionnelles (format "YYYY-MM-DD")
        data_root: Chemin vers les données barres_au_sol (défaut: auto-détection)
        prefer_parquet: Si True, cherche Parquet d'abord (défaut: True)

    Returns:
        DataFrame avec colonnes [Open, High, Low, Close, Volume]
        Index = DatetimeIndex UTC.
    """
    global _last_source_info

    if data_root is None:
        data_root = _default_data_root()

    # ── Tentative Parquet ──
    if prefer_parquet:
        # Extraire le nom d'instrument (supprimer =X, -USD, etc. si c'est un symbole Yahoo)
        instrument = _normalize_instrument(symbol_or_instrument)
        pq_path = _parquet_path_for(instrument, data_root, timeframe=interval)

        if pq_path:
            try:
                df = _load_from_parquet(pq_path, start=start, end=end)
                if df is not None and len(df) > 100:
                    _last_source_info = DataSourceInfo("parquet", pq_path, len(df))
                    return df
            except Exception:
                pass  # Fallback to Yahoo

    # ── Fallback Yahoo ──
    yahoo_sym = yahoo_symbol(symbol_or_instrument)
    df = _load_from_yahoo(yahoo_sym, period=period, interval=interval, start=start, end=end)
    _last_source_info = DataSourceInfo("yahoo", yahoo_sym, len(df))
    return df


def _normalize_instrument(s: str) -> str:
    """Normalise un symbole vers un nom FTMO.

    "EURUSD=X" → "EURUSD"
    "BTC-USD"  → "BTC" ou "BTCUSD"
    "GC=F"     → "XAUUSD"  (pas de correspondance directe, on tente quand même)
    """
    s = s.strip().upper()
    # Supprimer suffixes Yahoo
    for suffix in ["=X", "-USD", "=F"]:
        if s.endswith(suffix):
            s = s[:-len(suffix)]
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

    # barres_au_sol utilise des colonnes lowercase
    col_map = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    df = df.rename(columns=col_map)

    # S'assurer que les colonnes nécessaires existent
    required = ["Open", "High", "Low", "Close"]
    if not all(c in df.columns for c in required):
        return None

    # Ajouter Volume si absent
    if "Volume" not in df.columns:
        df["Volume"] = 0

    # S'assurer de l'index UTC
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    # Filtrer par dates si demandé
    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]

    # Nettoyer
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

    kwargs = {"interval": interval}
    if start and end:
        kwargs["start"] = start
        kwargs["end"] = end
    else:
        kwargs["period"] = period

    df = ticker.history(**kwargs)

    if df.empty:
        raise ValueError(f"Aucune donnée pour {symbol} avec {kwargs}")

    # Garder seulement OHLCV
    cols = ["Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in cols if c in df.columns]].copy()

    # S'assurer de l'index UTC
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    # Nettoyer
    df = _clean_ohlc(df)

    return df


def _clean_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Nettoie les données OHLC."""
    # Supprimer NaN et zéros
    mask = (
        df["Open"].notna() & df["High"].notna() &
        df["Low"].notna() & df["Close"].notna() &
        (df["Open"] > 0) & (df["Close"] > 0)
    )
    df = df[mask].copy()

    # Corriger H/L si incohérents
    df["High"] = df[["Open", "High", "Low", "Close"]].max(axis=1)
    df["Low"] = df[["Open", "High", "Low", "Close"]].min(axis=1)

    # Marquer les changements de jour pour le reset daily DD
    df["date"] = df.index.date

    return df


def split_in_out_sample(
    df: pd.DataFrame,
    in_sample_pct: float = 0.70,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split en in-sample / out-of-sample par date."""
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

    prices = np.zeros(n_bars)
    prices[0] = start_price
    vol = volatility
    mr_speed = 0.02
    mr_level = start_price

    for i in range(1, n_bars):
        vol = 0.95 * vol + 0.05 * volatility * (1 + 0.5 * abs(np.random.randn()))
        mr_pull = mr_speed * (mr_level - prices[i-1])
        innovation = np.random.randn() * vol
        prices[i] = prices[i-1] + trend + mr_pull + innovation
        mr_level += trend * 0.5

    opens = prices.copy()
    closes = np.roll(prices, -1)
    closes[-1] = prices[-1]
    highs = np.maximum(opens, closes) + np.abs(np.random.randn(n_bars)) * volatility * 0.6
    lows = np.minimum(opens, closes) - np.abs(np.random.randn(n_bars)) * volatility * 0.6
    volumes = np.random.randint(100, 10000, n_bars).astype(float)

    df = pd.DataFrame({
        "Open": opens, "High": highs, "Low": lows,
        "Close": closes, "Volume": volumes,
    }, index=dates[:n_bars])
    df["date"] = df.index.date
    return df


# ── Mapping Yahoo Finance ────────────────────────────────────────

def yahoo_symbol(instrument: str) -> str:
    """Convertit un nom d'instrument FTMO en symbole Yahoo Finance.

    Utilisé comme FALLBACK quand les données Parquet ne sont pas disponibles.
    """
    mapping = {
        # ── FX Majeures ──
        "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X",
        "USDJPY": "USDJPY=X", "USDCHF": "USDCHF=X",
        "USDCAD": "USDCAD=X", "AUDUSD": "AUDUSD=X",
        "NZDUSD": "NZDUSD=X",
        # ── FX Crosses EUR ──
        "EURGBP": "EURGBP=X", "EURJPY": "EURJPY=X",
        "EURCHF": "EURCHF=X", "EURCAD": "EURCAD=X",
        "EURAUD": "EURAUD=X", "EURNZD": "EURNZD=X",
        "EURCZK": "EURCZK=X", "EURHUF": "EURHUF=X",
        "EURNOK": "EURNOK=X", "EURPLN": "EURPLN=X",
        # ── FX Crosses GBP ──
        "GBPJPY": "GBPJPY=X", "GBPCHF": "GBPCHF=X",
        "GBPCAD": "GBPCAD=X", "GBPAUD": "GBPAUD=X",
        "GBPNZD": "GBPNZD=X",
        # ── FX Crosses AUD/NZD/CAD/CHF ──
        "AUDJPY": "AUDJPY=X", "NZDJPY": "NZDJPY=X",
        "CADJPY": "CADJPY=X", "CHFJPY": "CHFJPY=X",
        "AUDCAD": "AUDCAD=X", "AUDCHF": "AUDCHF=X",
        "AUDNZD": "AUDNZD=X", "CADCHF": "CADCHF=X",
        "NZDCAD": "NZDCAD=X", "NZDCHF": "NZDCHF=X",
        # ── FX Exotiques ──
        "USDCNH": "USDCNH=X", "USDCZK": "USDCZK=X",
        "USDHKD": "USDHKD=X", "USDMXN": "USDMXN=X",
        "USDNOK": "USDNOK=X", "USDPLN": "USDPLN=X",
        "USDSEK": "USDSEK=X", "USDSGD": "USDSGD=X",
        "USDZAR": "USDZAR=X", "USDILS": "USDILS=X",
        # ── Crypto ──
        "BTC": "BTC-USD", "BTCUSD": "BTC-USD",
        "ETH": "ETH-USD", "ETHUSD": "ETH-USD",
        "LTC": "LTC-USD", "LTCUSD": "LTC-USD",
        "SOL": "SOL-USD", "SOLUSD": "SOL-USD",
        "BNB": "BNB-USD", "BNBUSD": "BNB-USD",
        "BCH": "BCH-USD", "BCHUSD": "BCH-USD",
        "XRP": "XRP-USD", "XRPUSD": "XRP-USD",
        "ADA": "ADA-USD", "ADAUSD": "ADA-USD",
        "DOGE": "DOGE-USD", "DOGEUSD": "DOGE-USD",
        "AVAX": "AVAX-USD", "AVAUSD": "AVAX-USD",
        "LINK": "LINK-USD", "LNKUSD": "LINK-USD",
        "DOT": "DOT-USD", "DOTUSD": "DOT-USD",
        # ── Métaux ──
        "XAUUSD": "GC=F", "XAGUSD": "SI=F",
        "XAUEUR": "GC=F",  # Approximation
        "XPTUSD": "PL=F", "XPDUSD": "PA=F",
        "XCUUSD": "HG=F",
        # ── Indices ──
        "US30": "^DJI", "US500": "^GSPC", "US100": "^NDX",
        "USTEC": "^NDX",
        "DE40": "^GDAXI", "GER40": "^GDAXI",
        "UK100": "^FTSE",
        "JP225": "^N225",
        "AU200": "^AXJO", "AUS200": "^AXJO",
        "EU50": "^STOXX50E",
        "FRA40": "^FCHI",
        "SPN35": "^IBEX",
        "HK50": "^HSI",
        # ── Énergie ──
        "USOIL": "CL=F", "UKOIL": "BZ=F",
        "NATGAS": "NG=F", "HEATOIL": "HO=F",
        # ── Commodities ──
        "COCOA": "CC=F", "COFFEE": "KC=F",
        "CORN": "ZC=F", "COTTON": "CT=F",
        "SOYBEAN": "ZS=F", "SUGAR": "SB=F",
        "WHEAT": "ZW=F",
    }

    key = instrument.upper().replace(".CASH", "").replace(".C", "")
    return mapping.get(key, instrument)


# ── Utilitaires ──────────────────────────────────────────────────

def list_available_parquet(data_root: str | None = None, timeframe: str = "1h") -> dict[str, str]:
    """Liste tous les instruments disponibles en Parquet.

    Returns:
        Dict {instrument_ftmo: chemin_parquet}
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
    """Liste tous les instruments FTMO depuis instruments.csv.

    Returns:
        Liste de dicts {ftmo_symbol, source, data_symbol, category}
    """
    csv = _load_instruments_csv()
    if csv is None:
        return []

    instruments = []
    for _, row in csv.iterrows():
        ftmo = str(row["ftmo_symbol"]).strip()
        source = str(row["source"]).strip().lower()
        data_sym = str(row["data_symbol"]).strip()
        if not ftmo:
            continue
        instruments.append({
            "ftmo_symbol": ftmo,
            "source": source,
            "data_symbol": data_sym,
            "category": _categorize(ftmo),
        })
    return instruments


def _categorize(instrument: str) -> str:
    """Catégorise un instrument FTMO."""
    inst = instrument.upper()
    if inst.startswith("XA") or inst.startswith("XC") or inst.startswith("XP") or inst.startswith("XAG"):
        return "metals"
    if inst.endswith("USD") and len(inst) == 6 and not inst.startswith("X"):
        # Check if it's a known crypto
        crypto_bases = {"BTC", "ETH", "LTC", "SOL", "BNB", "BCH", "XRP", "ADA",
                       "DOGE", "DOT", "UNI", "XLM", "AVAX", "LINK", "AAVE",
                       "ALGO", "NEAR", "IMX", "GRT", "GAL", "FET", "ICP",
                       "VET", "MANA", "SAND", "XTZ", "ETC", "BAR", "NEO",
                       "XMR", "DASH", "NER", "AVA", "LNK", "ALG"}
        base = inst[:3]
        if base in crypto_bases:
            return "crypto"
        return "fx"
    if any(inst.startswith(p) for p in ["US", "DE", "GE", "UK", "JP", "AU", "EU", "FR", "SP", "HK", "N2"]):
        if inst in ("USOIL", "UKOIL"):
            return "energy"
        return "indices"
    if inst in ("USOIL", "UKOIL", "NATGAS", "HEATOIL"):
        return "energy"
    if inst in ("COCOA", "COFFEE", "CORN", "COTTON", "SOYBEAN", "SUGAR", "WHEAT"):
        return "commodities"
    if inst == "DXY":
        return "indices"
    # FX exotiques et crosses
    fx_currencies = {"EUR", "USD", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
                    "CNH", "CZK", "HKD", "HUF", "ILS", "MXN", "NOK", "PLN",
                    "SEK", "SGD", "ZAR"}
    if len(inst) == 6:
        base = inst[:3]
        quote = inst[3:]
        if base in fx_currencies and quote in fx_currencies:
            return "fx"
    return "other"
