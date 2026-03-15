"""
arabesque/strategies/fouette/signal.py
=======================================
Fouetté — Opening Range Breakout (ORB) sur session NY.

Un fouetté en danse classique est un coup de jambe en fouet : bref, décisif,
directionnel. Le prix "fouette" hors du range d'ouverture, impulsion nette
portée par le flux institutionnel à l'ouverture de New York.

Timeframe cible  : M1 (barres 1 minute)
Instruments      : XAUUSD en priorité, indices, crypto
Session          : NY open 9h30 EST (14h30 UTC hiver / 13h30 UTC été)

Modes disponibles
-----------------
- breakout      : entrée dès la première bougie qui close hors du range
- fvg           : entrée sur retest d'une FVG créée lors du breakout (1 seule)
- fvg_multiple  : idem avec jusqu'à N tentatives de FVG successives [défaut]

Shadow filter actif par défaut
-------------------------------
EMA filter : logue « 👻 EMA shadow » quand le prix est du mauvais côté de l'EMA,
sans bloquer le signal. Activer via ema_filter_active=True après validation.

Anti-lookahead strict
---------------------
Signal détecté sur bougie i → entrée au OPEN de la bougie i+1.

Résultats de référence (transcript, sur ~200 jours, XAUUSD M1)
---------------------------------------------------------------
mode=breakout,    range=15m, RR=1  → PF 1.14  (WR ~53%)
mode=fvg,         range=30m, RR=1  → PF 1.65  (WR ~60%)
mode=fvg_multiple,range=30m, RR=1  → PF 1.75  (WR ~65%)
mode=fvg_multiple,range=30m, RR=1, ema_filter=True → PF 2.43–3.0 (WR ~37%)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from arabesque.core.models import Side, Signal

logger = logging.getLogger(__name__)


# ─── Configuration ──────────────────────────────────────────────────────────

@dataclass
class FouetteConfig:
    """Paramètres de la stratégie Fouetté (ORB).

    Presets disponibles dans params.yaml :
        default, conservative, aggressive, ema_active
    """

    # ── Session ──────────────────────────────────────────────────────────────
    session_open_hour_utc_winter: int = 14   # EST (UTC-5)  : nov→mars
    session_open_hour_utc_summer: int = 13   # EDT (UTC-4)  : mars→nov
    session_open_minute_utc: int = 30        # 9h30 NY
    auto_dst: bool = True                    # Détection automatique heure/été
    range_minutes: int = 30                  # Durée du range : 5, 15 ou 30 min
    session_end_hour_utc: int = 20           # Fin de fenêtre de trade (16h NY hiver)
    session_end_minute_utc: int = 0

    # ── Mode de signal ───────────────────────────────────────────────────────
    mode: str = "fvg_multiple"               # "breakout" | "fvg" | "fvg_multiple"
    fvg_max_attempts: int = 3                # Tentatives max (mode fvg_multiple)
    max_trades_per_session: int = 1          # 1 seul trade par session NY

    # ── EMA filter (shadow par défaut) ───────────────────────────────────────
    ema_filter_active: bool = False          # False = shadow uniquement
    ema_period: int = 20

    # ── SL / TP ──────────────────────────────────────────────────────────────
    sl_source: str = "range"                 # "range" : SL au bord opposé du range
                                             # "fvg"   : SL au bord opposé de la FVG
    sl_buffer_factor: float = 0.05          # 5% du range en buffer SL
    rr_tp: float = 1.0                       # TP = rr_tp × taille du range

    # ── Qualité signal ────────────────────────────────────────────────────────
    min_range_atr_ratio: float = 0.0        # 0 = désactivé ; ex: 0.5 → OR > 0.5×ATR
    atr_period: int = 14                     # Pour le ratio de qualité


# ─── Helpers internes ────────────────────────────────────────────────────────

def _ny_open_hour_utc(bar_time: pd.Timestamp, auto_dst: bool,
                       hour_winter: int, hour_summer: int) -> int:
    """Retourne l'heure UTC d'ouverture NY pour cette date (DST simplifié)."""
    if not auto_dst:
        return hour_winter
    month = bar_time.month
    # Approximation DST : EDT (UTC-4) de mars à octobre inclus
    if 3 <= month <= 10:
        return hour_summer
    return hour_winter


def _detect_fvg(bar_a: pd.Series, bar_b: pd.Series, bar_c: pd.Series,
                direction: str) -> Optional[tuple[float, float]]:
    """
    Détecte un Fair Value Gap (FVG / imbalance) sur 3 bougies consécutives.

    Bullish FVG (direction='up')  : bar_a.high < bar_c.low  → gap entre les deux
    Bearish FVG (direction='down'): bar_a.low  > bar_c.high → gap entre les deux

    Returns (fvg_low, fvg_high) ou None si pas de gap.
    """
    if direction == "up":
        fvg_low = bar_a["high"]
        fvg_high = bar_c["low"]
        if fvg_high > fvg_low:
            return fvg_low, fvg_high
    elif direction == "down":
        fvg_high = bar_a["low"]
        fvg_low = bar_c["high"]
        if fvg_low < fvg_high:
            return fvg_low, fvg_high
    return None


# ─── Générateur de signaux ───────────────────────────────────────────────────

class FouetteSignalGenerator:
    """
    Générateur de signaux Fouetté (ORB).

    Usage
    -----
    >>> sg = FouetteSignalGenerator(FouetteConfig(mode="fvg_multiple", range_minutes=30))
    >>> df = sg.prepare(df_m1)
    >>> signals = sg.generate_signals(df, instrument="XAUUSD")
    """

    def __init__(self, config: Optional[FouetteConfig] = None):
        self.cfg = config or FouetteConfig()

    # ── prepare ──────────────────────────────────────────────────────────────

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calcule les indicateurs nécessaires et tague les barres du range.

        Colonnes ajoutées :
            ema, atr, is_or_bar

        Colonnes OHLCV normalisées en lowercase en interne.
        Les colonnes capitalisées (Open, High, Low, Close, Volume) sont
        conservées en parallèle pour la compatibilité avec le runner backtest.
        """
        df = df.copy()

        # Sauvegarder les colonnes capitalisées si elles existent
        _had_caps = "Close" in df.columns

        # Normaliser les noms de colonnes (tolère Open/Close ou open/close)
        df.columns = [c.lower() for c in df.columns]

        # EMA
        df["ema"] = df["close"].ewm(span=self.cfg.ema_period, adjust=False).mean()

        # ATR (Wilder, pour filtre qualité du range)
        high, low, close = df["high"], df["low"], df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)
        df["atr"] = tr.ewm(alpha=1 / self.cfg.atr_period, adjust=False).mean()

        # Tag des barres dans la fenêtre OR
        df["is_or_bar"] = self._tag_or_bars(df)

        # Restaurer les colonnes capitalisées pour le runner backtest
        if _had_caps:
            for lc, uc in [("open", "Open"), ("high", "High"), ("low", "Low"),
                           ("close", "Close"), ("volume", "Volume")]:
                if lc in df.columns:
                    df[uc] = df[lc]

        return df

    def _tag_or_bars(self, df: pd.DataFrame) -> pd.Series:
        """Marque les barres appartenant à la fenêtre d'opening range."""
        idx = pd.DatetimeIndex(df.index)
        open_m = self.cfg.session_open_minute_utc

        result = pd.Series(False, index=df.index)
        for i, ts in enumerate(idx):
            ts_pd = pd.Timestamp(ts)
            open_h = _ny_open_hour_utc(
                ts_pd, self.cfg.auto_dst,
                self.cfg.session_open_hour_utc_winter,
                self.cfg.session_open_hour_utc_summer,
            )
            open_min  = open_h * 60 + open_m
            end_min   = open_min + self.cfg.range_minutes
            bar_min   = ts_pd.hour * 60 + ts_pd.minute
            result.iloc[i] = open_min <= bar_min < end_min

        return result

    # ── generate_signals ─────────────────────────────────────────────────────

    def generate_signals(
        self, df: pd.DataFrame, instrument: str = "UNKNOWN"
    ) -> list[tuple[int, Signal]]:
        """
        Génère les signaux ORB depuis des barres M1.

        Signal détecté sur bougie i → entrée à l'open de la bougie i+1.
        Retourne une liste de (index_global, Signal).
        """
        df = df.copy()

        # S'assurer que les colonnes lowercase existent (sans dupliquer)
        if "close" not in df.columns and "Close" in df.columns:
            df = self.prepare(df)
        elif "is_or_bar" not in df.columns:
            df = self.prepare(df)

        signals: list[tuple[int, Signal]] = []

        idx_utc = pd.DatetimeIndex(df.index)
        dates = idx_utc.normalize()
        unique_dates = dates.unique()

        for session_date in unique_dates:
            day_mask = (dates == session_date)
            day_indices = np.where(day_mask)[0]
            day_df = df.iloc[day_indices]

            result = self._process_session(df, day_df, day_indices, instrument)
            if result is not None:
                signals.append(result)

        return signals

    # ── session ──────────────────────────────────────────────────────────────

    def _process_session(
        self, df: pd.DataFrame,
        day_df: pd.DataFrame,
        day_indices: np.ndarray,
        instrument: str,
    ) -> Optional[tuple[int, Signal]]:
        """Traite une session NY et retourne un signal si déclenché."""

        or_mask = day_df["is_or_bar"].values
        or_pos  = np.where(or_mask)[0]

        if len(or_pos) < 2:
            return None

        or_bars   = day_df.iloc[or_pos]
        or_high   = or_bars["high"].max()
        or_low    = or_bars["low"].min()
        or_range  = or_high - or_low

        if or_range <= 0:
            return None

        # Filtre qualité : OR doit représenter au moins X fois l'ATR
        if self.cfg.min_range_atr_ratio > 0:
            atr_at_or = or_bars["atr"].mean()
            if atr_at_or > 0 and or_range < self.cfg.min_range_atr_ratio * atr_at_or:
                logger.debug(
                    f"[Fouette] {instrument} range {or_range:.5f} < "
                    f"{self.cfg.min_range_atr_ratio}×ATR {atr_at_or:.5f} → skip"
                )
                return None

        # Barres post-OR
        or_end_local  = or_pos[-1]
        post_local    = np.arange(or_end_local + 1, len(day_df))

        if len(post_local) == 0:
            return None

        post_df      = day_df.iloc[post_local]
        post_global  = day_indices[post_local]

        end_min = self.cfg.session_end_hour_utc * 60 + self.cfg.session_end_minute_utc

        if self.cfg.mode == "breakout":
            return self._mode_breakout(
                df, post_df, post_global,
                or_high, or_low, or_range, instrument, end_min,
            )
        elif self.cfg.mode in ("fvg", "fvg_multiple"):
            max_att = self.cfg.fvg_max_attempts if self.cfg.mode == "fvg_multiple" else 1
            return self._mode_fvg(
                df, post_df, post_global,
                or_high, or_low, or_range, instrument, end_min, max_att,
            )

        return None

    # ── mode breakout ────────────────────────────────────────────────────────

    def _mode_breakout(
        self, df, post_df, post_global,
        or_high, or_low, or_range, instrument, end_min,
    ) -> Optional[tuple[int, Signal]]:
        """Mode simple : première bougie qui close hors du range."""

        for local_i in range(len(post_df)):
            row = post_df.iloc[local_i]
            ts  = pd.Timestamp(post_df.index[local_i])

            if not self._in_session(ts, end_min):
                break

            global_i = post_global[local_i]
            if global_i + 1 >= len(df):
                break

            if row["close"] > or_high:
                side = Side.LONG
                sl   = or_low  - self.cfg.sl_buffer_factor * or_range
                tp   = row["close"] + self.cfg.rr_tp * or_range
            elif row["close"] < or_low:
                side = Side.SHORT
                sl   = or_high + self.cfg.sl_buffer_factor * or_range
                tp   = row["close"] - self.cfg.rr_tp * or_range
            else:
                continue

            if not self._ema_ok(row, side, instrument):
                continue

            return self._build(df, global_i, row, side, sl, tp, or_range, instrument)

        return None

    # ── mode fvg / fvg_multiple ──────────────────────────────────────────────

    def _mode_fvg(
        self, df, post_df, post_global,
        or_high, or_low, or_range, instrument, end_min, max_attempts,
    ) -> Optional[tuple[int, Signal]]:
        """
        Mode FVG : attend une FVG après le breakout, puis son retest confirmé.
        max_attempts : nombre de FVG consécutives autorisées (1 ou cfg.fvg_max_attempts).

        État :
            breakout_side : None | "up" | "down"
            fvg           : None | (fvg_low, fvg_high)
            inside_fvg    : booléen — la bougie courante a touché la FVG
            attempts      : compteur de FVG nullifiées
        """
        breakout_side: Optional[str] = None
        fvg: Optional[tuple[float, float]] = None
        inside_fvg = False
        attempts   = 0
        n = len(post_df)

        i = 0
        while i < n:
            row = post_df.iloc[i]
            ts  = pd.Timestamp(post_df.index[i])
            global_i = post_global[i]

            if not self._in_session(ts, end_min):
                break

            # ── Phase 1 : détecter le breakout ──────────────────────────────
            if breakout_side is None:
                if row["close"] > or_high:
                    breakout_side = "up"
                elif row["close"] < or_low:
                    breakout_side = "down"
                i += 1
                continue

            # ── Phase 2 : chercher la première FVG après le breakout ────────
            if fvg is None:
                if i >= 2:
                    bar_a = post_df.iloc[i - 2]
                    bar_b = post_df.iloc[i - 1]
                    fvg_result = _detect_fvg(bar_a, bar_b, row, breakout_side)
                    if fvg_result:
                        fvg = fvg_result
                        logger.debug(
                            f"[Fouette] {instrument} FVG détectée "
                            f"[{fvg[0]:.5f} – {fvg[1]:.5f}] "
                            f"dir={breakout_side} tentative={attempts+1}"
                        )
                        i += 1
                        continue

                # Breakout annulé si le prix revient dans le range
                if breakout_side == "up"  and row["close"] < or_low:
                    breakout_side = None
                elif breakout_side == "down" and row["close"] > or_high:
                    breakout_side = None
                i += 1
                continue

            fvg_low, fvg_high = fvg

            # ── Phase 3 : nullification de la FVG ───────────────────────────
            nullified = (
                (breakout_side == "up"   and row["close"] < fvg_low)  or
                (breakout_side == "down" and row["close"] > fvg_high)
            )
            if nullified:
                attempts += 1
                logger.debug(
                    f"[Fouette] {instrument} FVG nullifiée tentative={attempts}"
                )
                if attempts >= max_attempts:
                    break
                fvg       = None
                inside_fvg = False
                i += 1
                continue

            # ── Phase 3 : retest de la FVG ──────────────────────────────────
            # La bougie touche la FVG (mèche ou corps à l'intérieur)
            touches = row["low"] <= fvg_high and row["high"] >= fvg_low
            if touches:
                inside_fvg = True

            if inside_fvg:
                # Confirmation : close de retour du bon côté (hors FVG, direction breakout)
                confirmed_long  = (breakout_side == "up"   and row["close"] > fvg_high)
                confirmed_short = (breakout_side == "down" and row["close"] < fvg_low)

                if confirmed_long or confirmed_short:
                    side = Side.LONG if confirmed_long else Side.SHORT

                    # SL : soit bord opposé du range, soit bord opposé de la FVG
                    if self.cfg.sl_source == "fvg":
                        sl = (fvg_low  - self.cfg.sl_buffer_factor * or_range) if confirmed_long \
                          else (fvg_high + self.cfg.sl_buffer_factor * or_range)
                    else:  # "range"
                        sl = (or_low  - self.cfg.sl_buffer_factor * or_range) if confirmed_long \
                          else (or_high + self.cfg.sl_buffer_factor * or_range)

                    tp = (row["close"] + self.cfg.rr_tp * or_range) if confirmed_long \
                      else (row["close"] - self.cfg.rr_tp * or_range)

                    if not self._ema_ok(row, side, instrument):
                        i += 1
                        continue

                    if global_i + 1 >= len(df):
                        break

                    return self._build(df, global_i, row, side, sl, tp, or_range, instrument)

            i += 1

        return None

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _in_session(ts: pd.Timestamp, end_min: int) -> bool:
        return ts.hour * 60 + ts.minute < end_min

    def _ema_ok(self, row: pd.Series, side: Side, instrument: str) -> bool:
        """
        Filtre EMA : long seulement si close > EMA, short si close < EMA.
        En mode shadow (ema_filter_active=False), logue mais ne bloque pas.
        """
        ema   = row.get("ema", None)
        close = row["close"]

        if ema is None or ema == 0:
            return True

        long_ko  = (side == Side.LONG  and close < ema)
        short_ko = (side == Side.SHORT and close > ema)
        violation = long_ko or short_ko

        if violation:
            direction = "BUY" if side == Side.LONG else "SELL"
            logger.info(
                f"[Fouette] 👻 EMA shadow: {instrument} {direction} "
                f"close={close:.5f} ema={ema:.5f} "
                f"→ AURAIT été filtré"
            )
            if self.cfg.ema_filter_active:
                return False

        return True

    def _build(
        self, df: pd.DataFrame, signal_bar_idx: int,
        signal_row: pd.Series, side: Side,
        sl: float, tp: float, or_range: float,
        instrument: str,
    ) -> tuple[int, Signal]:
        """
        Construit le Signal.

        ANTI-LOOKAHEAD : signal_bar_idx = bougie signal (i)
                         entry bar      = df.iloc[signal_bar_idx + 1] (i+1)
        """
        fill_bar   = df.iloc[signal_bar_idx + 1]
        entry_open = fill_bar["close"] if "open" not in fill_bar.index else fill_bar["open"]

        risk_dist = abs(entry_open - sl)
        rr = round(abs(tp - entry_open) / risk_dist, 2) if risk_dist > 0 else 0.0

        sig = Signal(
            instrument   = instrument,
            side         = side,
            timeframe    = "1m",
            close        = signal_row["close"],
            open_        = signal_row.get("open", signal_row["close"]),
            sl           = sl,
            tp_indicative= tp,
            atr          = float(signal_row.get("atr", 0.0)),
            rsi          = float(signal_row.get("rsi", 50.0)),
            cmf          = 0.0,
            bb_lower     = 0.0,
            bb_mid       = 0.0,
            bb_upper     = 0.0,
            bb_width     = round(or_range, 6),   # Réutilise bb_width pour la taille du range
            wr_14        = 0.0,
            rsi_div      = 0,
            ema200_ltf   = float(signal_row.get("ema", 0.0)),
            htf_ema_fast = 0.0,
            htf_ema_slow = 0.0,
            htf_adx      = 0.0,
            regime       = "orb_session",
            max_spread_atr = 0.3,
            rr           = rr,
            strategy_type= "fouette",
            sub_type     = self.cfg.mode,
            label_factors= {
                "mode"          : self.cfg.mode,
                "range_minutes" : self.cfg.range_minutes,
                "or_range"      : round(or_range, 6),
                "rr_tp"         : self.cfg.rr_tp,
                "ema_active"    : self.cfg.ema_filter_active,
            },
            timestamp    = df.index[signal_bar_idx + 1],
        )
        return (signal_bar_idx + 1, sig)
