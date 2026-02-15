"""
Arabesque v2 — Backtest data loader.

Charge les données OHLC avec priorité :
1. Parquet dérivés 1H de barres_au_sol (Dukascopy / CCXT)
2. Fallback Yahoo Finance si Parquet absent ou insuffisant

Gère :
- Mapping FTMO instrument → clé Parquet (via instruments.csv ou heuristique)
- Normalisation colonnes (lowercase Parquet → capitalisé Arabesque)
- Gaps weekend (vendredi close → lundi open)
- Barres manquantes (forward-fill limité à 3 barres)
- Détection jours de trading pour reset daily DD
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Configuration Parquet
# ─────────────────────────────────────────────────────────────

# Chemin par défaut vers le data root de barres_au_sol.
# Peut être overridé via :
#   - paramètre data_root dans load_ohlc()
#   - variable d'env BARRES_AU_SOL_DATA_ROOT
#   - argument CLI --data-root
DEFAULT_DATA_ROOT = os.environ.get(
    "BARRES_AU_SOL_DATA_ROOT",
    os.path.expanduser("~/dev/barres_au_sol/data"),
)

# Chemin vers instruments.csv de barres_au_sol (pour le mapping)
DEFAULT_INSTRUMENTS_CSV = os.environ.get(
    "BARRES_AU_SOL_INSTRUMENTS_CSV",
    os.path.expanduser("~/dev/barres_au_sol/instruments.csv"),
)


# ─────────────────────────────────────────────────────────────
# Mapping FTMO instrument → clé Parquet
# ─────────────────────────────────────────────────────────────

def _load_instruments_mapping(csv_path: str | None = None) -> dict[str, dict]:
    """Charge instruments.csv de barres_au_sol et construit le mapping.

    Returns:
        Dict {ftmo_symbol: {"source": str, "key": str}}
        ex: {"EURUSD": {"source": "dukascopy", "key": "EURUSD"},
             "BTC": {"source": "ccxt", "key": "BTCUSDT_BINANCE"}}
    """
    csv_path = csv_path or DEFAULT_INSTRUMENTS_CSV
    if not os.path.exists(csv_path):
        logger.debug(f"instruments.csv not found at {csv_path}")
        return {}

    try:
        df = pd.read_csv(csv_path).fillna("")
    except Exception as e:
        logger.warning(f"Failed to load instruments.csv: {e}")
        return {}

    mapping = {}
    for _, row in df.iterrows():
        ftmo = str(row.get("ftmo_symbol", "")).strip()
        source = str(row.get("source", "")).strip().lower()
        data_symbol = str(row.get("data_symbol", "")).strip()
        exchange = str(row.get("exchange", "")).strip() or "binance"

        if not ftmo or not source or not data_symbol:
            continue

        if source == "dukascopy":
            key = data_symbol.upper()
        elif source == "ccxt":
            key = data_symbol.replace("/", "").upper() + "_" + exchange.upper()
        else:
            continue

        mapping[ftmo.upper()] = {"source": source, "key": key}

    return mapping


# Cache global du mapping (chargé une fois)
_INSTRUMENTS_MAP: dict[str, dict] | None = None


def _get_instruments_map(csv_path: str | None = None) -> dict[str, dict]:
    """Retourne le mapping instruments, avec cache."""
    global _INSTRUMENTS_MAP
    if _INSTRUMENTS_MAP is None:
        _INSTRUMENTS_MAP = _load_instruments_mapping(csv_path)
    return _INSTRUMENTS_MAP


def _resolve_parquet_path(
    instrument: str,
    data_root: str,
    timeframe: str = "1h",
    csv_path: str | None = None,
) -> str | None:
    """Résout le chemin Parquet pour un instrument FTMO.

    Stratégie :
    1. Chercher dans instruments.csv (mapping exact)
    2. Heuristique : essayer les chemins courants
       - dukascopy/derived/{INSTRUMENT}_1h.parquet
       - ccxt/derived/{INSTRUMENT}USDT_BINANCE_1h.parquet

    Returns:
        Chemin absolu vers le fichier Parquet, ou None si introuvable.
    """
    inst_upper = instrument.upper()
    mapping = _get_instruments_map(csv_path)

    # 1. Mapping via instruments.csv
    if inst_upper in mapping:
        info = mapping[inst_upper]
        path = os.path.join(
            data_root, info["source"], "derived",
            f"{info['key']}_{timeframe}.parquet"
        )
        if os.path.exists(path):
            return path
        logger.debug(f"Parquet mapped but not found: {path}")

    # 2. Heuristique — essayer les chemins courants
    candidates = [
        # Dukascopy direct (FX, métaux)
        os.path.join(data_root, "dukascopy", "derived", f"{inst_upper}_{timeframe}.parquet"),
        # CCXT Binance (crypto)
        os.path.join(data_root, "ccxt", "derived", f"{inst_upper}USDT_BINANCE_{timeframe}.parquet"),
        os.path.join(data_root, "ccxt", "derived", f"{inst_upper}USD_BINANCE_{timeframe}.parquet"),
        # CCXT avec /
        os.path.join(data_root, "ccxt", "derived", f"{inst_upper}_BINANCE_{timeframe}.parquet"),
    ]

    for path in candidates:
        if os.path.exists(path):
            logger.info(f"Parquet found via heuristic: {path}")
            return path

    return None


# ─────────────────────────────────────────────────────────────
# Chargement Parquet
# ─────────────────────────────────────────────────────────────

def _load_parquet_ohlc(
    parquet_path: str,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Charge un fichier Parquet dérivé et normalise pour Arabesque.

    barres_au_sol stocke : open, high, low, close, volume (lowercase)
    Arabesque attend :    Open, High, Low, Close, Volume (capitalisé)
    """
    df = pd.read_parquet(parquet_path)

    if df.empty:
        raise ValueError(f"Parquet vide : {parquet_path}")

    # Normaliser les noms de colonnes (lowercase → capitalisé)
    col_map = {
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    }
    df = df.rename(columns=col_map)

    # S'assurer qu'on a les colonnes nécessaires
    required = ["Open", "High", "Low", "Close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Colonnes manquantes dans {parquet_path}: {missing}")

    # Ajouter Volume si absent
    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    # Garder seulement OHLCV
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()

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


# ─────────────────────────────────────────────────────────────
# Chargement Yahoo Finance (fallback)
# ─────────────────────────────────────────────────────────────

def _load_yahoo_ohlc(
    symbol: str,
    period: str = "2y",
    interval: str = "1h",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Charge les données OHLC depuis Yahoo Finance.

    Args:
        symbol: Symbole Yahoo (ex: "EURUSD=X", "GC=F" pour l'or)
        period: Période (ex: "2y", "730d"). Ignoré si start/end fournis.
        interval: Intervalle ("1h" pour Arabesque)
        start/end: Dates optionnelles (format "YYYY-MM-DD")

    Returns:
        DataFrame avec colonnes [Open, High, Low, Close, Volume]
        Index = DatetimeIndex UTC.
    """
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
        raise ValueError(f"Aucune donnée Yahoo pour {symbol} avec {kwargs}")

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


# ─────────────────────────────────────────────────────────────
# API publique
# ─────────────────────────────────────────────────────────────

def load_ohlc(
    symbol: str,
    period: str = "2y",
    interval: str = "1h",
    start: str | None = None,
    end: str | None = None,
    # ── Parquet bridge ──
    instrument: str | None = None,
    data_root: str | None = None,
    instruments_csv: str | None = None,
    prefer_parquet: bool = True,
) -> pd.DataFrame:
    """Charge les données OHLC — Parquet d'abord, Yahoo Finance en fallback.

    Args:
        symbol: Symbole Yahoo (ex: "EURUSD=X"). Utilisé pour le fallback.
        period: Période Yahoo (ex: "2y", "730d"). Ignoré si start/end.
        interval: "1h" pour Arabesque.
        start/end: Dates optionnelles "YYYY-MM-DD".
        instrument: Nom FTMO (ex: "EURUSD", "BTC"). Si None, déduit de symbol.
        data_root: Chemin vers le data root barres_au_sol.
        instruments_csv: Chemin vers instruments.csv barres_au_sol.
        prefer_parquet: Si True (défaut), tente Parquet d'abord.

    Returns:
        DataFrame OHLCV, index DatetimeIndex UTC.
    """
    data_root = data_root or DEFAULT_DATA_ROOT
    source_used = "yahoo"

    # Déduire l'instrument depuis le symbol Yahoo si pas fourni
    if instrument is None:
        instrument = _instrument_from_yahoo_symbol(symbol)

    # 1. Tenter Parquet
    if prefer_parquet and instrument:
        parquet_path = _resolve_parquet_path(
            instrument, data_root,
            timeframe=interval.replace("min", "m"),
            csv_path=instruments_csv,
        )

        if parquet_path:
            try:
                df = _load_parquet_ohlc(parquet_path, start=start, end=end)

                # Vérifier qu'on a assez de données
                min_bars = 100
                if len(df) >= min_bars:
                    source_used = "parquet"
                    logger.info(
                        f"Loaded {len(df)} bars from Parquet: {parquet_path}"
                    )
                    print(f"  [Parquet] {os.path.basename(parquet_path)} → {len(df)} bars")
                    return df
                else:
                    logger.warning(
                        f"Parquet too short ({len(df)} bars < {min_bars}), "
                        f"falling back to Yahoo"
                    )
                    print(f"  [Parquet] {os.path.basename(parquet_path)} → "
                          f"seulement {len(df)} bars, fallback Yahoo")
            except Exception as e:
                logger.warning(f"Parquet load failed: {e}, falling back to Yahoo")
                print(f"  [Parquet] Erreur: {e}, fallback Yahoo")

    # 2. Fallback Yahoo Finance
    yahoo_sym = symbol if "=" in symbol or "-" in symbol or "^" in symbol else yahoo_symbol(symbol)
    print(f"  [Yahoo] {yahoo_sym}")
    df = _load_yahoo_ohlc(yahoo_sym, period=period, interval=interval,
                          start=start, end=end)
    return df


# ─────────────────────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────────────────────

def _instrument_from_yahoo_symbol(symbol: str) -> str | None:
    """Reverse mapping : Yahoo symbol → FTMO instrument name.

    Ex: "EURUSD=X" → "EURUSD", "BTC-USD" → "BTC", "GC=F" → "XAUUSD"
    """
    # Reverse du yahoo_symbol mapping
    reverse = {}
    mapping = _yahoo_mapping()
    for ftmo, yahoo in mapping.items():
        # Garder le premier match (le plus court / canonique)
        if yahoo not in reverse:
            reverse[yahoo] = ftmo

    return reverse.get(symbol, None)


def _yahoo_mapping() -> dict[str, str]:
    """Retourne le mapping complet FTMO → Yahoo. Centralisé ici."""
    return {
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
        "DOGE": "DOGE-USD", "DOGEUSD": "DOGE-USD",
        "ADA": "ADA-USD", "ADAUSD": "ADA-USD",
        "DOT": "DOT-USD", "DOTUSD": "DOT-USD",
        "XMR": "XMR-USD", "XMRUSD": "XMR-USD",
        "DASH": "DASH-USD", "DASHUSD": "DASH-USD",
        "NEO": "NEO-USD", "NEOUSD": "NEO-USD",
        "UNI": "UNI-USD", "UNIUSD": "UNI-USD",
        "XLM": "XLM-USD", "XLMUSD": "XLM-USD",
        "AAVE": "AAVE-USD", "AAVEUSD": "AAVE-USD",
        "MANA": "MANA-USD", "MANAUSD": "MANA-USD",
        "IMX": "IMX-USD", "IMXUSD": "IMX-USD",
        "GRT": "GRT-USD", "GRTUSD": "GRT-USD",
        "ETC": "ETC-USD", "ETCUSD": "ETC-USD",
        "ALGO": "ALGO-USD", "ALGOUSD": "ALGO-USD",
        "NEAR": "NEAR-USD", "NEARUSD": "NEAR-USD",
        "LINK": "LINK-USD", "LINKUSD": "LINK-USD",
        "AVAX": "AVAX-USD", "AVAXUSD": "AVAX-USD",
        "XTZ": "XTZ-USD", "XTZUSD": "XTZ-USD",
        "FET": "FET-USD", "FETUSD": "FET-USD",
        "ICP": "ICP-USD", "ICPUSD": "ICP-USD",
        "SAND": "SAND-USD", "SANDUSD": "SAND-USD",
        "GAL": "GAL-USD", "GALUSD": "GAL-USD",
        "VET": "VET-USD", "VETUSD": "VET-USD",
        # ── Métaux ──
        "XAUUSD": "GC=F", "GOLD": "GC=F",
        "XAGUSD": "SI=F", "SILVER": "SI=F",
        "XPDUSD": "PA=F", "PALLADIUM": "PA=F",
        "XPTUSD": "PL=F", "PLATINUM": "PL=F",
        "COPPER": "HG=F",
        # ── Énergie ──
        "USOIL": "CL=F", "WTI": "CL=F",
        "UKOIL": "BZ=F", "BRENT": "BZ=F",
        "NATGAS": "NG=F", "NGAS": "NG=F",
        "HEATINGOIL": "HO=F",
        # ── Indices ──
        "SP500": "^GSPC", "SPX500": "^GSPC", "US500": "^GSPC",
        "NAS100": "^NDX", "USTEC": "^NDX", "NASDAQ": "^NDX",
        "US30": "^DJI", "DJ30": "^DJI",
        "US2000": "^RUT", "RUSSELL": "^RUT",
        "GER40": "^GDAXI", "DAX": "^GDAXI", "DE40": "^GDAXI",
        "UK100": "^FTSE", "FTSE": "^FTSE",
        "FRA40": "^FCHI", "CAC40": "^FCHI", "FR40": "^FCHI",
        "EU50": "^STOXX50E", "STOXX50": "^STOXX50E",
        "ESP35": "^IBEX", "IBEX": "^IBEX",
        "NED25": "^AEX", "AEX": "^AEX",
        "JPN225": "^N225", "NIKKEI": "^N225", "JP225": "^N225",
        "HK50": "^HSI", "HSI": "^HSI",
        "AUS200": "^AXJO", "ASX200": "^AXJO",
        "USDX": "DX-Y.NYB", "DXY": "DX-Y.NYB",
        # ── Matières premières (Soft/Agri) ──
        "COCOA": "CC=F", "COFFEE": "KC=F",
        "CORN": "ZC=F", "COTTON": "CT=F",
        "SOYBEAN": "ZS=F", "WHEAT": "ZW=F",
        "SUGAR": "SB=F",
        # ── Actions US ──
        "AAPL": "AAPL", "AMZN": "AMZN", "GOOG": "GOOG",
        "MSFT": "MSFT", "NFLX": "NFLX", "NVDA": "NVDA",
        "META": "META", "TSLA": "TSLA", "BAC": "BAC",
        "V": "V", "WMT": "WMT", "PFE": "PFE",
        "T": "T", "ZM": "ZM", "BABA": "BABA",
        # ── Actions EU ──
        "RACE": "RACE",
        "MC": "MC.PA",
        "AF": "AF.PA",
        "ALV": "ALV.DE",
        "BAYN": "BAYN.DE",
        "DBK": "DBK.DE",
        "VOW3": "VOW3.DE",
        "IBE": "IBE.MC",
    }


def yahoo_symbol(instrument: str) -> str:
    """Convertit un nom d'instrument FTMO/GFT en symbole Yahoo Finance."""
    return _yahoo_mapping().get(instrument.upper(), instrument)


def _clean_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Nettoie les données OHLC.

    - Supprime les lignes avec O/H/L/C = 0 ou NaN
    - Vérifie H >= L
    - Marque les changements de jour pour le reset daily DD
    """
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
    """Split en in-sample / out-of-sample par date.

    Args:
        df: DataFrame OHLC complet
        in_sample_pct: Proportion in-sample (0.70 = 70%)

    Returns:
        (df_in, df_out)
    """
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
    """Génère des données OHLC synthétiques réalistes pour tester le pipeline.

    Utile quand Yahoo Finance n'est pas accessible (réseau restreint).
    Les données ne sont PAS utilisables pour évaluer un edge — uniquement
    pour valider le code du pipeline.

    Le modèle :
    - GBM (Geometric Brownian Motion) pour le prix de base
    - Mean-reversion overlay (Ornstein-Uhlenbeck) pour créer des excès BB
    - Volatility clustering (GARCH-like) pour ATR réaliste
    - Gaps weekend (pas de barres samedi/dimanche)
    """
    np.random.seed(42)  # Reproductible

    # Générer un index datetime (business hours only, ~24h/day FX)
    dates = pd.date_range(start=start_date, periods=n_bars * 2, freq=freq, tz="UTC")
    # Filtrer weekends
    dates = dates[dates.dayofweek < 5][:n_bars]

    # GBM + mean-reversion
    prices = np.zeros(n_bars)
    prices[0] = start_price

    vol = volatility
    mr_speed = 0.02   # Mean-reversion speed
    mr_level = start_price

    for i in range(1, n_bars):
        # Volatility clustering
        vol = 0.95 * vol + 0.05 * volatility * (1 + 0.5 * abs(np.random.randn()))

        # Mean-reversion + drift
        mr_pull = mr_speed * (mr_level - prices[i-1])
        innovation = np.random.randn() * vol
        prices[i] = prices[i-1] + trend + mr_pull + innovation

        # Slowly drift mean level
        mr_level += trend * 0.5

    # Générer OHLC à partir du prix
    opens = prices.copy()
    closes = np.roll(prices, -1)
    closes[-1] = prices[-1]

    # Ajouter noise pour H/L
    highs = np.maximum(opens, closes) + np.abs(np.random.randn(n_bars)) * volatility * 0.6
    lows = np.minimum(opens, closes) - np.abs(np.random.randn(n_bars)) * volatility * 0.6
    volumes = np.random.randint(100, 10000, n_bars).astype(float)

    df = pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    }, index=dates[:n_bars])

    df["date"] = df.index.date

    return df


# ─────────────────────────────────────────────────────────────
# Diagnostic
# ─────────────────────────────────────────────────────────────

def check_parquet_availability(
    instruments: list[str],
    data_root: str | None = None,
    csv_path: str | None = None,
) -> dict[str, dict]:
    """Diagnostic : vérifie la disponibilité Parquet pour une liste d'instruments.

    Returns:
        Dict {instrument: {"available": bool, "path": str|None, "bars": int|None,
                           "date_range": str|None, "source": str}}
    """
    data_root = data_root or DEFAULT_DATA_ROOT
    results = {}

    for inst in instruments:
        path = _resolve_parquet_path(inst, data_root, csv_path=csv_path)
        info = {"available": False, "path": None, "bars": None,
                "date_range": None, "source": "yahoo (fallback)"}

        if path:
            try:
                df = pd.read_parquet(path)
                info["available"] = True
                info["path"] = path
                info["bars"] = len(df)
                if len(df) > 0:
                    info["date_range"] = f"{df.index[0]} → {df.index[-1]}"
                # Extract source from path
                if "/dukascopy/" in path:
                    info["source"] = "parquet (dukascopy)"
                elif "/ccxt/" in path:
                    info["source"] = "parquet (ccxt)"
                else:
                    info["source"] = "parquet"
            except Exception as e:
                info["available"] = False
                info["error"] = str(e)

        results[inst] = info

    return results


def print_data_status(
    instruments: list[str],
    data_root: str | None = None,
) -> None:
    """Affiche un tableau de disponibilité des données."""
    status = check_parquet_availability(instruments, data_root)

    print(f"\n{'='*70}")
    print(f"  DATA AVAILABILITY (root: {data_root or DEFAULT_DATA_ROOT})")
    print(f"{'='*70}")
    print(f"  {'Instrument':<12} {'Source':<22} {'Bars':>8} {'Range'}")
    print(f"  {'-'*65}")

    for inst, info in status.items():
        bars = str(info["bars"]) if info["bars"] else "-"
        rng = info["date_range"] or "-"
        src = info["source"]
        print(f"  {inst:<12} {src:<22} {bars:>8} {rng}")

    print(f"{'='*70}\n")
