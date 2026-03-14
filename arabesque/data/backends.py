# data_backends.py
import os
import time
import lzma
from datetime import datetime, timedelta, date
from typing import Optional, List, Dict, Tuple

import pandas as pd
import numpy as np
import requests

DEFAULT_PRICE_SCALE = 1e5
DUKAS_BASE_URL = "https://datafeed.dukascopy.com/datafeed"


# ─────────────────────────────────────────────────────────────
# Utils
# ─────────────────────────────────────────────────────────────

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def cache_paths(root: str, provider: str, key: str) -> Tuple[str, str]:
    min1_dir = os.path.join(root, provider, "min1")
    der_dir = os.path.join(root, provider, "derived")
    ensure_dir(min1_dir)
    ensure_dir(der_dir)
    return os.path.join(min1_dir, f"{key}.parquet"), der_dir


def load_cache(path: str) -> Optional[pd.DataFrame]:
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    if df is None or df.empty:
        return None
    return df.sort_index()


def merge_store(path: str, df_old: Optional[pd.DataFrame], df_new: pd.DataFrame) -> pd.DataFrame:
    if df_old is None or df_old.empty:
        df = df_new
    else:
        df = pd.concat([df_old, df_new])
        # on garde la dernière valeur si doublon d’index
        df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_parquet(path)
    return df


def compute_missing_days(df: Optional[pd.DataFrame], start_dt: datetime, end_dt: datetime) -> List[date]:
    """Calcule les jours manquants entre start et end.

    Exclut samedi et dimanche : pas de données de marché
    (FX ferme vendredi ~22h UTC, rouvre dimanche ~22h UTC).
    """
    cur = start_dt.date()
    target: List[date] = []
    while cur <= end_dt.date():
        # Exclure weekends (5=samedi, 6=dimanche)
        if cur.weekday() < 5:
            target.append(cur)
        cur += timedelta(days=1)

    if df is None:
        return target

    have = set(pd.Index(df.index.date).unique())
    return [d for d in target if d not in have]


def tf_to_rule(tf: str) -> str:
    tf_map = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1D",
    }
    t = tf.lower()
    if t not in tf_map:
        raise ValueError(f"Timeframe inconnu : {tf}")
    return tf_map[t]


def ohlcv_resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    # df index : DatetimeIndex
    out = (
        df.resample(rule)
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )
    return out


# ─────────────────────────────────────────────────────────────
# Dukascopy backend
# ─────────────────────────────────────────────────────────────

def _dukascopy_url(symbol: str, day: datetime) -> str:
    y = day.year
    m0 = day.month - 1  # dukascopy : mois 0-indexé
    d = day.day
    return f"{DUKAS_BASE_URL}/{symbol}/{y}/{m0:02d}/{d:02d}/BID_candles_min_1.bi5"


def _dukascopy_fetch_day(
    session: requests.Session,
    symbol: str,
    day: datetime,
    price_scale: float,
    timeout_s: int,
) -> Optional[pd.DataFrame]:
    r = session.get(_dukascopy_url(symbol, day), timeout=timeout_s)
    if r.status_code != 200 or not r.content:
        return None

    try:
        raw = lzma.decompress(r.content)
    except lzma.LZMAError:
        return None

    arr = np.frombuffer(raw, dtype=">i4")
    if arr.size % 6 != 0:
        return None

    # big-endian -> native
    arr = arr.reshape(-1, 6).byteswap().view(arr.dtype.newbyteorder("="))

    # colonnes dukascopy : time, open, close, low, high, volume
    df = pd.DataFrame(arr, columns=["ts", "open", "close", "low", "high", "volume"])

    base = pd.Timestamp(day.date(), tz="UTC")

    # ts peut être en secondes (0..86400) ou en millisecondes (0..86400000) selon flux
    ts = df["ts"].astype("int64")
    unit = "s" if ts.max() < 1_000_000 else "ms"

    df["timestamp"] = base + pd.to_timedelta(ts, unit=unit)
    df = df.drop(columns=["ts"]).set_index("timestamp").sort_index()

    # normalisation prix + volume
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype("float64") / float(price_scale)
    df["volume"] = df["volume"].astype("float64")

    # ── Validation ──
    # Supprimer les barres avec prix ≤ 0 ou NaN
    mask = (df["open"] > 0) & (df["close"] > 0) & (df["high"] > 0) & (df["low"] > 0)
    df = df[mask]

    # Corriger high/low si incohérents
    df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
    df["low"] = df[["open", "high", "low", "close"]].min(axis=1)

    # Supprimer les variations aberrantes (> 50% en 1 minute)
    if len(df) > 1:
        pct_chg = df["close"].pct_change().abs()
        df = df[pct_chg.isna() | (pct_chg < 0.50)]

    return df


def dukascopy_update_min1(
    root: str,
    data_symbol: str,
    key: str,
    start_dt: datetime,
    end_dt: datetime,
    price_scale: float = DEFAULT_PRICE_SCALE,
    sleep_ms: int = 0,
    timeout_s: int = 30,
    retries: int = 2,
    max_fail_streak: int = 10,
) -> Dict:
    path, _ = cache_paths(root, "dukascopy", key)
    df_old = load_cache(path)
    missing = compute_missing_days(df_old, start_dt, end_dt)

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    frames: List[pd.DataFrame] = []
    fail_streak = 0
    downloaded_days = 0
    skipped_days = 0

    sym = data_symbol.upper()

    for d in missing:
        day = datetime(d.year, d.month, d.day)

        got = None
        for _ in range(retries + 1):
            try:
                got = _dukascopy_fetch_day(session, sym, day, price_scale, timeout_s)
            except Exception:
                got = None
            if got is not None and not got.empty:
                break

        if got is None or got.empty:
            skipped_days += 1
            fail_streak += 1
            if fail_streak >= max_fail_streak:
                break
        else:
            frames.append(got)
            downloaded_days += 1
            fail_streak = 0

        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)

    if frames:
        df_new = pd.concat(frames).sort_index()
        df = merge_store(path, df_old, df_new)
    else:
        df = df_old

    return {
        "provider": "dukascopy",
        "key": key,
        "data_symbol": data_symbol,
        "min1_path": path,
        "cache_rows": 0 if df is None else int(df.shape[0]),
        "missing_days": int(len(missing)),
        "downloaded_days": int(downloaded_days),
        "skipped_days": int(skipped_days),
        "fail_streak": int(fail_streak),
    }


# ─────────────────────────────────────────────────────────────
# CCXT backend
# ─────────────────────────────────────────────────────────────

def ccxt_key(data_symbol: str, exchange_name: str) -> str:
    return data_symbol.replace("/", "").upper() + "_" + exchange_name.upper()


def ccxt_update_min1(
    root: str,
    data_symbol: str,
    exchange_name: str,
    key: str,
    start_dt: datetime,
    end_dt: datetime,
    sleep_ms: int = 0,
    timeout_ms: int = 30000,
) -> Dict:
    import ccxt

    path, _ = cache_paths(root, "ccxt", key)
    df_old = load_cache(path)

    ex = getattr(ccxt, exchange_name)({"enableRateLimit": True, "timeout": timeout_ms})

    since = int(pd.Timestamp(start_dt, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end_dt + timedelta(days=1), tz="UTC").timestamp() * 1000)

    rows: List[List[float]] = []
    while since < end_ms:
        batch = ex.fetch_ohlcv(data_symbol, timeframe="1m", since=since, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        since = batch[-1][0] + 60_000
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)

    if rows:
        df_new = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df_new["timestamp"] = pd.to_datetime(df_new["timestamp"], unit="ms", utc=True)
        df_new = df_new.set_index("timestamp").sort_index()

        # ── Validation ──
        mask = (df_new["open"] > 0) & (df_new["close"] > 0)
        df_new = df_new[mask]
        df_new["high"] = df_new[["open", "high", "low", "close"]].max(axis=1)
        df_new["low"] = df_new[["open", "high", "low", "close"]].min(axis=1)

        df = merge_store(path, df_old, df_new)
    else:
        df = df_old

    return {
        "provider": "ccxt",
        "key": key,
        "data_symbol": data_symbol,
        "exchange": exchange_name,
        "min1_path": path,
        "cache_rows": 0 if df is None else int(df.shape[0]),
        "downloaded_rows": int(len(rows)),
    }


# ─────────────────────────────────────────────────────────────
# Dérivations
# ─────────────────────────────────────────────────────────────

def derive_timeframes(root: str, provider: str, key: str, tfs: List[str]) -> Dict:
    path, der_dir = cache_paths(root, provider, key)
    df = load_cache(path)
    if df is None:
        return {"ok": False, "reason": "cache vide", "min1_path": path}

    out: Dict[str, Dict[str, object]] = {}
    for tf in tfs:
        rule = tf_to_rule(tf)
        df_tf = ohlcv_resample(df, rule)
        out_path = os.path.join(der_dir, f"{key}_{tf.lower()}.parquet")
        df_tf.to_parquet(out_path)
        out[tf.lower()] = {"path": out_path, "rows": int(df_tf.shape[0])}

    return {"ok": True, "min1_path": path, "derived": out}

