"""
arabesque.data.store — Chargement des données OHLC.

PRIORITÉ DES SOURCES :
  1. Parquet dérivés de barres_au_sol (Dukascopy forex/metals, CCXT crypto)
  2. Fallback Yahoo Finance si pas de Parquet

Chemin vers les données Parquet (par ordre de priorité) :
  - Argument data_root dans load_ohlc()
  - Env var ARABESQUE_DATA_ROOT
  - Défaut : ~/dev/barres_au_sol/data

Colonnes Parquet (barres_au_sol) : lowercase (open, high, low, close, volume)
Colonnes Arabesque (sortie) :     capitalisées (Open, High, Low, Close, Volume)

Fonctions publiques :
  load_ohlc()              — charge OHLC pour un instrument
  split_in_out_sample()    — découpe IS/OOS
  yahoo_symbol()           — mapping instrument → symbole Yahoo
  get_last_source_info()   — info sur la dernière source utilisée
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════

def _default_data_root() -> str:
    """Chemin par défaut vers les données Parquet.

    Priorité :
      1. Variable d'environnement ARABESQUE_DATA_ROOT
      2. <repo>/data/  (emplacement canonique)
    """
    env = os.environ.get("ARABESQUE_DATA_ROOT")
    if env:
        return env
    repo_root = Path(__file__).resolve().parent.parent.parent
    return str(repo_root / "barres_au_sol")


# ═══════════════════════════════════════════════════════════════════════════════
# Source info tracking
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SourceInfo:
    """Info sur la dernière source de données utilisée."""
    source: str          # "parquet_dukascopy", "parquet_ccxt", "yahoo"
    path: str = ""       # chemin du fichier
    instrument: str = ""
    bars: int = 0


_last_source_info: Optional[SourceInfo] = None


def get_last_source_info() -> Optional[SourceInfo]:
    """Retourne les infos sur la dernière source de données chargée."""
    return _last_source_info


# ═══════════════════════════════════════════════════════════════════════════════
# Mapping instruments
# ═══════════════════════════════════════════════════════════════════════════════

# Mapping FTMO instrument → clé Parquet dans dukascopy/derived/
_DUKASCOPY_MAP: dict[str, str] = {
    # Forex majors
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
    "USDCHF": "USDCHF", "AUDUSD": "AUDUSD", "USDCAD": "USDCAD",
    "NZDUSD": "NZDUSD",
    # Forex crosses
    "EURGBP": "EURGBP", "EURJPY": "EURJPY", "GBPJPY": "GBPJPY",
    "EURCHF": "EURCHF", "EURAUD": "EURAUD", "EURCAD": "EURCAD",
    "GBPAUD": "GBPAUD", "GBPCAD": "GBPCAD", "GBPCHF": "GBPCHF",
    "GBPNZD": "GBPNZD", "AUDCAD": "AUDCAD", "AUDCHF": "AUDCHF",
    "AUDJPY": "AUDJPY", "AUDNZD": "AUDNZD", "CADJPY": "CADJPY",
    "CADCHF": "CADCHF", "CHFJPY": "CHFJPY", "NZDCAD": "NZDCAD",
    "NZDCHF": "NZDCHF", "NZDJPY": "NZDJPY", "EURNZD": "EURNZD",
    # Metals
    "XAUUSD": "XAUUSD", "XAGUSD": "XAGUSD",
}

# Mapping FTMO instrument → clé Parquet dans ccxt/derived/
# Format ccxt : BTCUSDT_BINANCE
_CCXT_MAP: dict[str, str] = {
    "BTCUSD": "BTCUSDT_BINANCE",
    "ETHUSD": "ETHUSDT_BINANCE",
    "SOLUSD": "SOLUSDT_BINANCE",
    "BNBUSD": "BNBUSDT_BINANCE",
    "XRPUSD": "XRPUSDT_BINANCE",
    "DOGEUSD": "DOGEUSDT_BINANCE",
    "ADAUSD": "ADAUSDT_BINANCE",
    "DOTUSD": "DOTUSDT_BINANCE",
    "AVAXUSD": "AVAXUSDT_BINANCE",
    "LNKUSD": "LINKUSDT_BINANCE",
    "LINKUSD": "LINKUSDT_BINANCE",
    "LTCUSD": "LTCUSDT_BINANCE",
    "UNIUSD": "UNIUSDT_BINANCE",
    "MATICUSD": "MATICUSDT_BINANCE",
    "ATOMUSD": "ATOMUSDT_BINANCE",
    "FILUSD": "FILUSDT_BINANCE",
    "NEARUSD": "NEARUSDT_BINANCE",
    "ARBUSD": "ARBUSDT_BINANCE",
    "OPUSD": "OPUSDT_BINANCE",
    "APTUSD": "APTUSDT_BINANCE",
    "SUIUSD": "SUIUSDT_BINANCE",
    "AAVEUSD": "AAVEUSDT_BINANCE",
    "TRXUSD": "TRXUSDT_BINANCE",
    "SHIBUSD": "SHIBUSDT_BINANCE",
    "PEPE1000USD": "1000PEPEUSDT_BINANCE",
    "PEPEUSD": "1000PEPEUSDT_BINANCE",
}

# Mapping instrument → Yahoo symbol
_YAHOO_MAP: dict[str, str] = {
    # Forex
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X",
    "USDCHF": "USDCHF=X", "AUDUSD": "AUDUSD=X", "USDCAD": "USDCAD=X",
    "NZDUSD": "NZDUSD=X", "EURGBP": "EURGBP=X", "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X", "EURCHF": "EURCHF=X", "EURAUD": "EURAUD=X",
    "EURCAD": "EURCAD=X", "GBPAUD": "GBPAUD=X", "GBPCAD": "GBPCAD=X",
    "GBPCHF": "GBPCHF=X", "GBPNZD": "GBPNZD=X", "AUDCAD": "AUDCAD=X",
    "AUDCHF": "AUDCHF=X", "AUDJPY": "AUDJPY=X", "AUDNZD": "AUDNZD=X",
    "CADJPY": "CADJPY=X", "CADCHF": "CADCHF=X", "CHFJPY": "CHFJPY=X",
    "NZDCAD": "NZDCAD=X", "NZDCHF": "NZDCHF=X", "NZDJPY": "NZDJPY=X",
    "EURNZD": "EURNZD=X",
    # Metals
    "XAUUSD": "GC=F", "XAGUSD": "SI=F",
    # Crypto
    "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD", "SOLUSD": "SOL-USD",
    "BNBUSD": "BNB-USD", "XRPUSD": "XRP-USD", "DOGEUSD": "DOGE-USD",
    "ADAUSD": "ADA-USD", "DOTUSD": "DOT-USD", "AVAXUSD": "AVAX-USD",
    "LINKUSD": "LINK-USD", "LNKUSD": "LINK-USD",
    "LTCUSD": "LTC-USD", "UNIUSD": "UNI-USD",
    "MATICUSD": "MATIC-USD", "ATOMUSD": "ATOM-USD",
    # Indices
    "NAS100": "NQ=F", "US30": "YM=F", "US500": "ES=F",
    "GER40": "^GDAXI", "UK100": "^FTSE", "JPN225": "^N225",
}

# Catégorisation
_CATEGORIES: dict[str, str] = {
    "EURUSD": "forex_major", "GBPUSD": "forex_major", "USDJPY": "forex_major",
    "USDCHF": "forex_major", "AUDUSD": "forex_major", "USDCAD": "forex_major",
    "NZDUSD": "forex_major",
    "XAUUSD": "metal", "XAGUSD": "metal",
    "NAS100": "index", "US30": "index", "US500": "index",
    "GER40": "index", "UK100": "index", "JPN225": "index",
}


def yahoo_symbol(instrument: str) -> str:
    """Convertit un instrument FTMO en symbole Yahoo Finance.

    Si l'instrument ressemble déjà à un symbole Yahoo (contient =, -, ^),
    il est retourné tel quel.
    """
    if any(c in instrument for c in ("=", "-", "^")):
        return instrument
    inst = instrument.upper().replace("/", "")
    return _YAHOO_MAP.get(inst, f"{inst}=X")


def _categorize(instrument: str) -> str:
    """Catégorise un instrument (forex_major, forex_cross, metal, crypto, index)."""
    inst = instrument.upper().replace("/", "")
    if inst in _CATEGORIES:
        return _CATEGORIES[inst]
    if inst in _CCXT_MAP or inst.endswith("USD") and len(inst) >= 6:
        return "crypto"
    return "forex_cross"


# ═══════════════════════════════════════════════════════════════════════════════
# Chargement Parquet
# ═══════════════════════════════════════════════════════════════════════════════

def _find_parquet(
    instrument: str,
    timeframe: str = "1h",
    data_root: str | None = None,
) -> Optional[Path]:
    """Cherche le fichier Parquet pour un instrument donné.

    Cherche dans l'ordre :
      Pour min1 : {provider}/min1/{KEY}.parquet
      Pour les autres : {provider}/derived/{KEY}_{tf}.parquet

    Returns: Path du fichier ou None.
    """
    root = Path(data_root or _default_data_root())
    inst = instrument.upper().replace("/", "")

    # min1 est dans un sous-dossier différent (pas de suffixe timeframe)
    is_min1 = timeframe in ("min1", "1m", "1min")

    # Dukascopy (forex, metals)
    if inst in _DUKASCOPY_MAP:
        key = _DUKASCOPY_MAP[inst]
        if is_min1:
            path = root / "dukascopy" / "min1" / f"{key}.parquet"
        else:
            path = root / "dukascopy" / "derived" / f"{key}_{timeframe}.parquet"
        if path.exists():
            return path

    # CCXT (crypto)
    if inst in _CCXT_MAP:
        key = _CCXT_MAP[inst]
        if is_min1:
            path = root / "ccxt" / "min1" / f"{key}.parquet"
        else:
            path = root / "ccxt" / "derived" / f"{key}_{timeframe}.parquet"
        if path.exists():
            return path

    # Tentative directe (si l'instrument est déjà la clé Parquet)
    for provider in ("dukascopy", "ccxt"):
        if is_min1:
            path = root / provider / "min1" / f"{inst}.parquet"
        else:
            path = root / provider / "derived" / f"{inst}_{timeframe}.parquet"
        if path.exists():
            return path

    return None


def _load_parquet(
    path: Path,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Charge un fichier Parquet et normalise les colonnes pour Arabesque.

    Entrée (barres_au_sol) : colonnes lowercase, DatetimeIndex ou colonne timestamp.
    Sortie (Arabesque) :     colonnes capitalisées (Open, High, Low, Close, Volume),
                             DatetimeIndex UTC.
    """
    df = pd.read_parquet(path)

    # ── Normaliser l'index ──
    if not isinstance(df.index, pd.DatetimeIndex):
        # Chercher une colonne timestamp
        ts_col = None
        for col in ("timestamp", "date", "datetime", "ts", "time"):
            if col in df.columns:
                ts_col = col
                break
            if col.lower() in [c.lower() for c in df.columns]:
                ts_col = [c for c in df.columns if c.lower() == col.lower()][0]
                break
        if ts_col:
            df = df.set_index(ts_col)
        else:
            # Si index numérique, essayer de l'interpréter comme epoch
            try:
                df.index = pd.to_datetime(df.index, unit="s")
            except (ValueError, TypeError):
                df.index = pd.to_datetime(df.index)

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.DatetimeIndex(df.index)

    # UTC
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    # ── Normaliser les colonnes ──
    # barres_au_sol utilise lowercase, Arabesque attend capitalisé
    col_map = {}
    for col in df.columns:
        lc = col.lower()
        if lc == "open":
            col_map[col] = "Open"
        elif lc == "high":
            col_map[col] = "High"
        elif lc == "low":
            col_map[col] = "Low"
        elif lc == "close":
            col_map[col] = "Close"
        elif lc == "volume":
            col_map[col] = "Volume"
    if col_map:
        df = df.rename(columns=col_map)

    # Garder seulement OHLCV
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    df = df[keep].copy()

    # Volume à 0 si absent
    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    # ── Filtrage temporel ──
    if start:
        start_ts = pd.Timestamp(start, tz="UTC")
        df = df[df.index >= start_ts]
    if end:
        end_ts = pd.Timestamp(end, tz="UTC")
        df = df[df.index <= end_ts]

    # ── Nettoyage ──
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    # Supprimer les barres avec prix <= 0
    mask = (df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)
    df = df[mask]

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# Chargement Yahoo Finance (fallback)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_yahoo(
    symbol: str,
    period: str = "730d",
    interval: str = "1h",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Charge les données OHLC depuis Yahoo Finance (fallback si pas de Parquet)."""
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError(
            "yfinance non installé. Installer avec : pip install yfinance\n"
            "Ou fournir des données Parquet via barres_au_sol."
        )

    ticker = yf.Ticker(symbol)

    kwargs = {"interval": interval}
    if start and end:
        kwargs["start"] = start
        kwargs["end"] = end
    else:
        kwargs["period"] = period

    df = ticker.history(**kwargs)

    if df.empty:
        raise ValueError(f"Aucune donnée Yahoo pour {symbol} avec {kwargs}")

    # Garder seulement OHLCV
    cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    df = df[cols].copy()

    # UTC
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    # Nettoyage
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# API publique
# ═══════════════════════════════════════════════════════════════════════════════

def load_ohlc(
    symbol_or_instrument: str,
    period: str = "730d",
    interval: str = "1h",
    start: str | None = None,
    end: str | None = None,
    instrument: str | None = None,
    data_root: str | None = None,
) -> pd.DataFrame:
    """Charge les données OHLC pour un instrument.

    Priorité : Parquet barres_au_sol → Yahoo Finance (fallback).

    Args:
        symbol_or_instrument: Instrument FTMO (ex: "XAUUSD") ou symbole Yahoo (ex: "GC=F").
        period: Période Yahoo (ex: "730d"). Ignoré si start/end fournis.
        interval: Intervalle (ex: "1h"). Aussi utilisé pour trouver le bon Parquet.
        start: Date début (ex: "2025-01-01"). Filtre sur le Parquet ou passé à Yahoo.
        end: Date fin.
        instrument: Instrument FTMO explicite (quand symbol_or_instrument est un Yahoo symbol).
        data_root: Chemin vers le répertoire data de barres_au_sol.

    Returns:
        DataFrame avec colonnes [Open, High, Low, Close, Volume], DatetimeIndex UTC.
    """
    global _last_source_info

    # Résoudre l'instrument FTMO
    inst = instrument or symbol_or_instrument
    inst = inst.upper().replace("/", "").replace("=X", "").replace("-USD", "USD")
    # Nettoyer les suffixes Yahoo courants
    for suffix in ("=X", "=F", "-USD"):
        if inst.endswith(suffix):
            inst = inst[: -len(suffix)]

    # Mapper les timeframes pour le Parquet
    tf_map = {"1h": "1h", "1H": "1h", "5m": "5m", "5min": "5m", "1m": "min1",
              "1min": "min1", "min1": "min1", "15m": "15m", "15min": "15m",
              "30m": "30m", "30min": "30m", "4h": "4h", "4H": "4h",
              "1d": "1d", "1D": "1d"}
    tf = tf_map.get(interval, interval)

    # ── Tentative 1 : Parquet barres_au_sol ──
    parquet_path = _find_parquet(inst, timeframe=tf, data_root=data_root)

    if parquet_path is not None:
        try:
            df = _load_parquet(parquet_path, start=start, end=end)
            if len(df) > 0:
                provider = "dukascopy" if "dukascopy" in str(parquet_path) else "ccxt"
                _last_source_info = SourceInfo(
                    source=f"parquet_{provider}",
                    path=str(parquet_path),
                    instrument=inst,
                    bars=len(df),
                )
                logger.info(
                    f"[data] {inst}: {len(df)} barres chargées depuis "
                    f"{parquet_path.name} ({provider})"
                )
                return df
            else:
                logger.warning(
                    f"[data] {inst}: Parquet trouvé ({parquet_path.name}) "
                    f"mais 0 barres après filtrage — fallback Yahoo"
                )
        except Exception as e:
            logger.warning(f"[data] {inst}: erreur lecture Parquet ({e}) — fallback Yahoo")

    # ── Tentative 2 : Yahoo Finance ──
    yahoo_sym = yahoo_symbol(inst)
    logger.info(f"[data] {inst}: pas de Parquet, tentative Yahoo ({yahoo_sym})")

    try:
        df = _load_yahoo(yahoo_sym, period=period, interval=interval, start=start, end=end)
        _last_source_info = SourceInfo(
            source="yahoo",
            path=yahoo_sym,
            instrument=inst,
            bars=len(df),
        )
        logger.info(f"[data] {inst}: {len(df)} barres chargées depuis Yahoo ({yahoo_sym})")
        return df
    except Exception as e:
        raise ValueError(
            f"Impossible de charger les données pour {inst}.\n"
            f"  Parquet: {'non trouvé' if parquet_path is None else f'erreur ({parquet_path})'}\n"
            f"  Yahoo ({yahoo_sym}): {e}\n"
            f"  Data root: {data_root or _default_data_root()}"
        ) from e


def split_in_out_sample(
    df: pd.DataFrame,
    in_sample_pct: float = 0.70,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Découpe un DataFrame en in-sample et out-of-sample.

    Args:
        df: DataFrame OHLC avec DatetimeIndex.
        in_sample_pct: Fraction pour l'in-sample (0.70 = 70%).

    Returns:
        (df_in, df_out) — deux DataFrames disjoints.
    """
    n = len(df)
    split_idx = int(n * in_sample_pct)
    return df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy()


def generate_synthetic_ohlc(
    n_bars: int = 5000,
    start_price: float = 1.08,
    volatility: float = 0.0008,
) -> pd.DataFrame:
    """Génère des données OHLC synthétiques (pour tests unitaires uniquement).

    Returns:
        DataFrame avec colonnes [Open, High, Low, Close, Volume], DatetimeIndex UTC.
    """
    rng = np.random.default_rng(42)
    prices = [start_price]
    for _ in range(n_bars - 1):
        change = rng.normal(0, volatility)
        prices.append(prices[-1] * (1 + change))

    opens = prices
    closes = [p * (1 + rng.normal(0, volatility * 0.5)) for p in prices]
    highs = [max(o, c) * (1 + abs(rng.normal(0, volatility * 0.3))) for o, c in zip(opens, closes)]
    lows = [min(o, c) * (1 - abs(rng.normal(0, volatility * 0.3))) for o, c in zip(opens, closes)]
    volumes = rng.integers(100, 10000, size=n_bars).tolist()

    idx = pd.date_range("2023-01-01", periods=n_bars, freq="h", tz="UTC")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=idx,
    )
