"""
Arabesque v2 — Chargement des données OHLCV.

Priorité : Parquet (barres_au_sol) > Yahoo Finance (fallback).

Fonctions publiques :
    load_ohlc(instrument, period, start, end, data_root) → DataFrame
    split_in_out_sample(df, split_pct)                   → (df_in, df_out)
    yahoo_symbol(instrument)                             → str
    list_available_parquet(data_root)                    → list[str]
    list_all_ftmo_instruments()                          → list[str]
    generate_synthetic_ohlc(n_bars, ...)                 → DataFrame
    get_last_source_info()                               → dict
    _categorize(instrument)                              → str

Format de sortie :
    Index : DatetimeIndex UTC (freq ≈ 1h)
    Colonnes : Open, High, Low, Close, Volume (majuscules — convention pandas)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

# ── Chemins Parquet par défaut ────────────────────────────────────────────────
_DEFAULT_DATA_ROOTS = [
    Path("data/parquet"),
    Path("~/dev/barres_au_sol/data/parquet").expanduser(),
    Path("~/dev/arabesque/data/parquet").expanduser(),
    Path("/home/raphael/dev/barres_au_sol/data/parquet"),
]

# Contexte de la dernière source chargée
_LAST_SOURCE: dict = {"source": "unknown", "path": "", "rows": 0}


# ── Catégories ────────────────────────────────────────────────────────────────
_FX_CURRENCIES = {
    "EUR", "USD", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
    "CNH", "CZK", "HKD", "HUF", "ILS", "MXN", "NOK", "PLN",
    "SEK", "SGD", "ZAR", "TRY", "DKK",
}
_METALS  = {"XAU", "XAG", "XPT", "XPD"}
_ENERGY  = {"XBR", "XTI", "NGAS", "USOIL", "UKOIL"}
_INDICES = {
    "SPX","NAS","DJI","DAX","FTSE","CAC","NI225","HSI",
    "US30","US500","US100","DE40","UK100","FR40","JP225",
}


def _categorize(instrument: str) -> str:
    """Retourne la catégorie : fx | metals | energy | indices | crypto | other."""
    inst = instrument.upper().replace(".CASH","").replace(".C","")
    # Métaux : XAU/XAG/XPT/XPD en base
    if len(inst) >= 6 and inst[:3] in _METALS:
        return "metals"
    # Énergie
    if inst in _ENERGY or inst[:4] in _ENERGY or inst[:3] in _ENERGY:
        return "energy"
    # Indices
    if inst in _INDICES or any(inst.startswith(x) for x in _INDICES):
        return "indices"
    # FX : 6 chars, base ET quote dans les devises connues
    if len(inst) == 6:
        base, quote = inst[:3], inst[3:]
        if base in _FX_CURRENCIES and quote in _FX_CURRENCIES:
            return "fx"
        # Sinon (ex: XRPUSD, ETHUSD à 6 chars) → crypto si quote = USD/EUR/BTC
        if quote in ("USD","EUR","BTC","ETH","USDT"):
            return "crypto"
    # Crypto long format : LINKUSD, ALGOUSD, etc.
    if inst.endswith(("USD","USDT","BTC","ETH")) and len(inst) > 6:
        return "crypto"
    return "other"


# ── Mapping symboles ──────────────────────────────────────────────────────────
_YAHOO_MAP: dict[str, str] = {
    # Crypto
    "BTCUSD":"BTC-USD","ETHUSD":"ETH-USD","BNBUSD":"BNB-USD",
    "XRPUSD":"XRP-USD","SOLUSD":"SOL-USD","ADAUSD":"ADA-USD",
    "DOTUSD":"DOT-USD","AVAXUSD":"AVAX-USD","MATICUSD":"MATIC-USD",
    "LINKUSD":"LINK-USD","LNKUSD":"LINK-USD","UNIUSD":"UNI-USD",
    "LTCUSD":"LTC-USD","BCHUSD":"BCH-USD","XLMUSD":"XLM-USD",
    "ATOMUSD":"ATOM-USD","ALGOUSD":"ALGO-USD","ALGUSD":"ALGO-USD",
    "VETUSD":"VET-USD","VECUSD":"VET-USD","XTZUSD":"XTZ-USD",
    "DASHUSD":"DASH-USD","ZECUSD":"ZEC-USD","XMRUSD":"XMR-USD",
    "FILUSD":"FIL-USD","AAVUSD":"AAVE-USD","AAVEUSD":"AAVE-USD",
    "GRTUSD":"GRT-USD","ICPUSD":"ICP-USD","IMXUSD":"IMX-USD",
    "NEOUSD":"NEO-USD","NERUSD":"NER-USD","HBARUSD":"HBAR-USD",
    "SUSHIUSD":"SUSHI-USD","COMPUSD":"COMP-USD","SNXUSD":"SNX-USD",
    "MKRUSD":"MKR-USD","YFIUSD":"YFI-USD","ENJUSD":"ENJ-USD",
    "MANAUSD":"MANA-USD","SANDUSD":"SAND-USD","AXSUSD":"AXS-USD",
    "GALAUSD":"GALA-USD","APEUSD":"APE-USD","GMTUSD":"GMT-USD",
    "OPUSD":"OP-USD","ARBUSD":"ARB-USD","STXUSD":"STX-USD",
    # Metals
    "XAUUSD":"GC=F","XAGUSD":"SI=F","XPTUSD":"PL=F",
    # Energy
    "XBRUSD":"BZ=F","XTIUSD":"CL=F","USOIL":"CL=F","UKOIL":"BZ=F",
}


def yahoo_symbol(instrument: str) -> str:
    """Traduit un symbole FTMO en symbole Yahoo Finance."""
    key = instrument.upper()
    if key in _YAHOO_MAP:
        return _YAHOO_MAP[key]
    if len(key) == 6 and not key.endswith("-USD"):
        return f"{key}=X"   # FX standard
    return instrument


def _normalize_to_ftmo(instrument: str) -> str:
    return instrument.replace("-","").replace("=X","").upper()


# ── Candidats Parquet ─────────────────────────────────────────────────────────
def _parquet_candidates(instrument: str, root: Path) -> Iterator[Path]:
    inst = instrument.upper()
    yield root / f"{inst}_H1.parquet"
    yield root / f"{inst.lower()}_H1.parquet"
    yield root / f"{inst}_1h.parquet"
    yield root / f"{inst.lower()}_1h.parquet"
    # Crypto CCXT format : BTCUSD → BTC_USDT_1h.parquet
    if inst.endswith("USD") and len(inst) > 6:
        base = inst[:-3]
        yield root / f"{base}_USDT_1h.parquet"
        yield root / f"{base.lower()}_usdt_1h.parquet"
    yield root / f"{inst}.parquet"
    yield root / f"{inst.lower()}.parquet"


def _find_data_root(data_root: str | None) -> Path | None:
    if data_root:
        p = Path(data_root)
        return p if p.exists() else None
    for p in _DEFAULT_DATA_ROOTS:
        if p.exists():
            return p
    return None


# ── Chargement ────────────────────────────────────────────────────────────────
def load_ohlc(
    instrument: str,
    period: str = "730d",
    start: str | None = None,
    end: str | None = None,
    data_root: str | None = None,
) -> pd.DataFrame:
    """Charge les données OHLCV — Parquet prioritaire, Yahoo Finance en fallback.

    Args:
        instrument : Symbole FTMO (ex: « XRPUSD ») ou Yahoo (ex: « XRP-USD »).
        period     : Durée au format NNd/NNm/NNy (ex: « 730d », « 2y »).
        start      : Date début ISO (ex: « 2024-01-01 ») — prioritaire sur period.
        end        : Date fin ISO (optionnel, défaut = aujourd'hui).
        data_root  : Répertoire Parquet (None = auto-détection).

    Returns:
        DataFrame avec index DatetimeIndex UTC, colonnes Open/High/Low/Close/Volume.
    """
    ftmo = _normalize_to_ftmo(instrument)

    # 1. Essai Parquet
    df = _load_parquet(ftmo, data_root)
    if df is not None:
        df = _filter_period(df, period, start, end)
        _LAST_SOURCE.update({"source":"parquet","path":ftmo,"rows":len(df)})
        return df

    # 2. Fallback Yahoo Finance
    yahoo_sym = yahoo_symbol(ftmo)
    df = _load_yahoo(yahoo_sym, period, start, end)
    _LAST_SOURCE.update({"source":"yahoo","path":yahoo_sym,"rows":len(df)})
    return df


def _load_parquet(instrument: str, data_root: str | None) -> pd.DataFrame | None:
    root = _find_data_root(data_root)
    if root is None:
        return None
    for path in _parquet_candidates(instrument, root):
        if path.exists():
            try:
                df = pd.read_parquet(path)
                return _normalize(_ensure_utc(df))
            except Exception:
                continue
    return None


def _load_yahoo(
    symbol: str,
    period: str = "730d",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("pip install yfinance --break-system-packages")
    ticker = yf.Ticker(symbol)
    if start:
        t_end = end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        df = ticker.history(start=start, end=t_end, interval="1h", auto_adjust=True)
    else:
        df = ticker.history(period=period, interval="1h", auto_adjust=True)
    if df.empty:
        raise ValueError(f"Aucune donnée Yahoo Finance pour {symbol!r}")
    return _normalize(_ensure_utc(df))


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    col_map = {}
    for c in df.columns:
        cl = c.lower()
        if cl in ("open","o"):       col_map[c] = "Open"
        elif cl in ("high","h"):     col_map[c] = "High"
        elif cl in ("low","l"):      col_map[c] = "Low"
        elif cl in ("close","c"):    col_map[c] = "Close"
        elif cl in ("volume","vol","v"): col_map[c] = "Volume"
    df = df.rename(columns=col_map)
    keep = [c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]
    df = df[keep].copy()
    if "Volume" not in df.columns:
        df["Volume"] = 1.0
    return df


def _ensure_utc(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df = df.sort_index()
    return df[~df.index.duplicated(keep="last")]


def _filter_period(
    df: pd.DataFrame,
    period: str = "730d",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    now = pd.Timestamp.now(tz="UTC")
    if start:
        t_start = pd.Timestamp(start, tz="UTC")
    else:
        m = re.match(r"(\d+)([dmy])", period.lower())
        if m:
            n, u = int(m.group(1)), m.group(2)
            days = n if u=="d" else n*30 if u=="m" else n*365
        else:
            days = 730
        t_start = now - pd.Timedelta(days=days)
    t_end = pd.Timestamp(end, tz="UTC") if end else now
    return df[(df.index >= t_start) & (df.index <= t_end)]


# ── Utilitaires ───────────────────────────────────────────────────────────────
def split_in_out_sample(
    df: pd.DataFrame,
    split_pct: float = 0.70,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Divise chronologiquement en in-sample / out-of-sample."""
    n = len(df)
    split_idx = int(n * split_pct)
    if split_idx < 10 or n - split_idx < 10:
        raise ValueError(f"Trop peu de données ({n} barres) pour split {split_pct:.0%}")
    return df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy()


def list_available_parquet(data_root: str | None = None) -> list[str]:
    """Liste les instruments disponibles dans le répertoire Parquet."""
    root = _find_data_root(data_root)
    if root is None:
        return []
    instruments = []
    for p in sorted(root.glob("*.parquet")):
        name = p.stem.upper()
        if "_H1" in name:
            instruments.append(name.replace("_H1",""))
        elif "_1H" in name:
            parts = name.replace("_1H","").split("_")
            # BTC_USDT → BTCUSD
            if len(parts) >= 2 and parts[-1] in ("USDT","BTC","ETH"):
                instruments.append(parts[0] + "USD")
            else:
                instruments.append("_".join(parts))
        else:
            instruments.append(name)
    return [i for i in instruments if i]


def list_all_ftmo_instruments() -> list[str]:
    """Liste de tous les instruments FTMO connus (référence statique)."""
    return [
        # FX Majors
        "EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","NZDUSD","USDCAD",
        # FX Crosses
        "EURGBP","EURJPY","EURCHF","EURCAD","EURAUD","EURNZD",
        "GBPJPY","GBPCHF","GBPCAD","GBPAUD","GBPNZD",
        "AUDJPY","AUDNZD","AUDCAD","AUDCHF",
        "NZDJPY","NZDCAD","NZDCHF","CADJPY","CADCHF","CHFJPY",
        # FX Exotiques
        "USDSGD","USDCNH","USDNOK","USDCZK","USDHKD","EURNOK",
        # Métaux
        "XAUUSD","XAGUSD",
        # Crypto
        "BTCUSD","ETHUSD","BNBUSD","XRPUSD","SOLUSD","ADAUSD",
        "LNKUSD","UNIUSD","LTCUSD","BCHUSD","XLMUSD","XTZUSD",
        "DASHUSD","NEOUSD","ALGOUSD","ALGUSD","AAVUSD","GRTUSD",
        "ICPUSD","IMXUSD","NERUSD","VECUSD",
    ]


def generate_synthetic_ohlc(
    n_bars: int = 5000,
    start_price: float = 1.0800,
    volatility: float = 0.0008,
    trend: float = 0.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Génère des données OHLCV synthétiques pour les tests."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(trend, volatility, n_bars)
    closes = start_price * np.exp(np.cumsum(returns))
    highs = closes * (1 + abs(rng.normal(0, volatility * 0.5, n_bars)))
    lows  = closes * (1 - abs(rng.normal(0, volatility * 0.5, n_bars)))
    opens = np.roll(closes, 1); opens[0] = start_price
    idx = pd.date_range("2023-01-01", periods=n_bars, freq="1h", tz="UTC")
    return pd.DataFrame({
        "Open":opens,"High":highs,"Low":lows,"Close":closes,
        "Volume":rng.integers(100,10000,n_bars).astype(float),
    }, index=idx)


def get_last_source_info() -> dict:
    """Retourne les informations sur la dernière source de données chargée."""
    return dict(_LAST_SOURCE)
