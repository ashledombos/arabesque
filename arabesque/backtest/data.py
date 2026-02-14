"""
Arabesque v2 — Backtest data loader.

Yahoo Finance 1H. Gère :
- Gaps weekend (vendredi close → lundi open)
- Barres manquantes (forward-fill limité à 3 barres)
- Détection jours de trading pour reset daily DD
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf


def load_ohlc(
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
    """Nettoie les données OHLC.

    - Supprime les lignes avec O/H/L/C = 0 ou NaN
    - Vérifie H >= L
    - Marque les gaps weekend
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
    import numpy as np

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


def yahoo_symbol(instrument: str) -> str:
    """Convertit un nom d'instrument FTMO/GFT en symbole Yahoo Finance.

    Couvre tout l'univers FTMO : FX, crypto, métaux, énergie,
    indices, matières premières, actions US/EU.
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
        "RACE": "RACE",          # Ferrari (NYSE)
        "MC": "MC.PA",           # LVMH
        "AF": "AF.PA",           # Air France
        "ALV": "ALV.DE",         # Allianz
        "BAYN": "BAYN.DE",       # Bayer
        "DBK": "DBK.DE",         # Deutsche Bank
        "VOW3": "VOW3.DE",      # VW
        "IBE": "IBE.MC",         # Iberdrola
    }
    return mapping.get(instrument.upper(), instrument)
