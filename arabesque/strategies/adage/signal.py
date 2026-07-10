"""
arabesque/strategies/adage/signal.py
=====================================
Adage — Session-hold nocturne sur l'or (« session-or »).

En danse classique, l'adage est la section lente : une position tenue en
équilibre, longtemps, sans mouvement parasite, puis relâchée avec contrôle.
La stratégie tient un LONG XAUUSD toute la nuit — de la réouverture Globex
(18:00 New York) au mur du matin (08:00 Londres) — sans BE, sans trailing,
sans TP : la sortie est l'heure, pas le prix.

Design validé (dossier 2026-07-04, WF formel jalon 1 PASS par dérogation,
docs/audit/session_or_wf_protocole_2026-07-10.md — ZÉRO paramètre libre) :
- LONG à l'open de la 1re barre min1 >= 18:00 America/New_York ;
- sortie au mur : 1re barre min1 >= 08:00 Europe/London — appliquée par le
  MOTEUR via ManagerConfig.session_exit (lot 1) / monitor live (lot 2),
  PAS par ce générateur ;
- SL -1R avec R = 1.0 × sigma des 20 derniers rendements de session
  (causal, shift 1) ;
- AUCUN overlay BE/trailing/ROI/giveback/deadfish/time-stop (ils détruisent
  l'edge, dossier 07-04 §4) → profil ManagerConfig dédié :
  ``adage_manager_config()`` ;
- garde-fous de construction de session (identiques à l'étude) : saut
  weekend (exit cherché à J+1..J+3), session > 20h exclue, >= 60 barres
  min1 par session. Le refus du vendredi est couvert par ces gardes :
  le marché ferme vendredi 17:00 NY, il n'existe pas de barre >= 18:00,
  et un éventuel trou de feed produirait une session > 20h → exclue.

Timeframe cible  : min1 (comme Fouetté)
Instrument validé: XAUUSD UNIQUEMENT (XAG = kill, spread nuit 10-11 bps ;
                   le mécanisme est générique mais le dossier ne valide
                   QUE 18hNY→8hLondres sur XAUUSD)

Résultats de référence (WF formel 2026-07-10, coût primaire 2,4 bps/session)
----------------------------------------------------------------------------
Exp +0.070R, +1.51R/mois (récent 18 m), 3/3 fenêtres récentes positives,
maxDD -16.2R (dérogation DD opérateur), WR(>-0.25R) 58.6 %.
Sizing gravé : 0.20-0.30 %/session max.

Anti-lookahead strict
---------------------
Signal émis sur la barre i = dernière barre AVANT la réouverture → fill à
l'open de la barre i+1 = la 1re barre >= 18:00 NY (convention du runner).
Sigma n'utilise que des sessions dont la sortie (08:00 Londres) précède
l'entrée courante (18:00 NY = 22:00-23:00 Londres le même jour).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from arabesque.core.models import Side, Signal
from arabesque.modules.position_manager import ManagerConfig, parse_session_exit

logger = logging.getLogger(__name__)


# ─── Configuration ──────────────────────────────────────────────────────────

@dataclass
class AdageConfig:
    """Paramètres Adage — design 07-04 figé, ne pas retoucher sans nouveau
    protocole pré-enregistré (un tir)."""

    entry_time: str = "18:00@America/New_York"   # réouverture Globex or
    exit_time: str = "08:00@Europe/London"       # mur — appliqué par le moteur
    sigma_window: int = 20                       # sessions pour le sigma causal
    sl_sigma_mult: float = 1.0                   # SL = -1R = -1 sigma
    # Garde-fous de session (identiques à l'étude tmp/wf_session_or.py)
    max_exit_lookahead_days: int = 3             # saut weekend : exit à J+1..J+3
    max_session_hours: float = 20.0              # session plus longue = trou/férié
    min_session_bars: int = 60                   # barres min1 minimum par session
    # Le spread est un COÛT compté (2,4 bps/session au dossier), pas un
    # critère d'entrée : l'entrée à heure fixe est le design. signal.atr
    # porte la DISTANCE DE RISQUE (1 sigma de session) — l'ATR min1 de la
    # barre pré-fermeture (minute la plus calme du jour) rejetterait tout.
    # Le guard devient donc « spread <= 0.10 × R » : le spread nuit mesuré
    # (0,98 bps médian 21-03h UTC ≈ 0,03R) passe, une nuit anormale
    # (> ~3,5 bps) est rejetée.
    max_spread_atr: float = 0.10
    atr_period: int = 14


def adage_manager_config(cfg: Optional[AdageConfig] = None) -> ManagerConfig:
    """Profil PositionManager d'Adage : AUCUN overlay, sortie au mur.

    Partagé backtest/dry-run ; le miroir live passe par
    ``live.session_exit_by_strategy`` (settings) côté monitor.
    """
    cfg = cfg or AdageConfig()
    return ManagerConfig(
        roi_enabled=False,
        trailing_tiers=[],
        be_enabled=False,
        giveback_enabled=False,
        deadfish_enabled=False,
        time_stop_enabled=False,
        session_exit=cfg.exit_time,
    )


# ─── Générateur de signaux ───────────────────────────────────────────────────

class AdageSignalGenerator:
    """Générateur de signaux Adage (session-hold nocturne).

    Usage
    -----
    >>> sg = AdageSignalGenerator()
    >>> df = sg.prepare(df_min1)
    >>> signals = sg.generate_signals(df, instrument="XAUUSD")
    """

    def __init__(self, config: Optional[AdageConfig] = None):
        self.cfg = config or AdageConfig()
        # Réutilise le parseur du noyau (fail-fast, même format que le mur)
        self._entry_t, self._entry_tz = parse_session_exit(self.cfg.entry_time)
        self._exit_t, self._exit_tz = parse_session_exit(self.cfg.exit_time)

    # ── prepare ──────────────────────────────────────────────────────────────

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalise les colonnes et calcule l'ATR (sizing du slippage +
        ratio de spread). Aucun autre indicateur : le design n'en utilise pas.
        """
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        high, low, close = df["high"], df["low"], df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        df["atr"] = tr.ewm(alpha=1 / self.cfg.atr_period, adjust=False).mean()

        # Colonnes capitalisées TOUJOURS présentes en sortie (convention
        # signal.py : le runner backtest lit Open/High/Low/Close).
        for lc, uc in [("open", "Open"), ("high", "High"), ("low", "Low"),
                       ("close", "Close"), ("volume", "Volume")]:
            if lc in df.columns:
                df[uc] = df[lc]
        return df

    # ── sessions (réplique EXACTE de l'étude tmp/wf_session_or.py) ──────────

    def _build_sessions(self, df: pd.DataFrame) -> list[dict]:
        """Construit les sessions entrée→sortie comme l'étude du jalon 1.

        Retourne une liste chronologique de dicts
        ``{"j_in": pos entrée, "t_in": ts, "open_in": px, "open_out": px}``.
        La paire (t_in→t_out) ne sert qu'au sigma et aux garde-fous : la
        sortie effective est le mur du moteur (même règle, même fuseau).
        """
        idx = pd.DatetimeIndex(df.index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC")

        ny = idx.tz_convert(self._entry_tz)
        lon = idx.tz_convert(self._exit_tz)
        entry_min = self._entry_t.hour * 60 + self._entry_t.minute
        exit_min = self._exit_t.hour * 60 + self._exit_t.minute

        ent_mask = (np.asarray(ny.hour) * 60 + np.asarray(ny.minute)) >= entry_min
        ex_mask = (np.asarray(lon.hour) * 60 + np.asarray(lon.minute)) >= exit_min
        ny_dates = np.asarray(ny.date)
        lon_dates = np.asarray(lon.date)
        positions = np.arange(len(df))
        opens = df["open"].to_numpy()

        # 1re barre >= heure d'entrée par date NY / >= heure de mur par date Londres
        ent_j = pd.Series(positions[ent_mask]).groupby(
            ny_dates[ent_mask]).first()
        ex_j = pd.Series(positions[ex_mask]).groupby(
            lon_dates[ex_mask]).first()
        ex_by_date = {d: int(j) for d, j in ex_j.items()}

        sessions: list[dict] = []
        for d_in, j_in in ent_j.items():
            j_in = int(j_in)
            t_in = idx[j_in]
            j_out = None
            for dd in range(1, self.cfg.max_exit_lookahead_days + 1):
                d = (pd.Timestamp(d_in) + pd.Timedelta(days=dd)).date()
                if d in ex_by_date and idx[ex_by_date[d]] > t_in:
                    j_out = ex_by_date[d]
                    break
            if j_out is None:
                continue
            t_out = idx[j_out]
            if (t_out - t_in) > pd.Timedelta(hours=self.cfg.max_session_hours):
                continue
            if (j_out - j_in + 1) < self.cfg.min_session_bars:
                continue
            sessions.append({
                "j_in": j_in,
                "t_in": t_in,
                "open_in": float(opens[j_in]),
                "open_out": float(opens[j_out]),
            })
        return sessions

    # ── generate_signals ─────────────────────────────────────────────────────

    def generate_signals(
        self, df: pd.DataFrame, instrument: str = "XAUUSD"
    ) -> list[tuple[int, Signal]]:
        """Un signal LONG par session valide, sigma causal.

        Signal sur la barre i (dernière barre avant la réouverture) → fill à
        l'open de la barre i+1 = 1re barre >= 18:00 NY (= px d'entrée de
        l'étude). La sortie n'est PAS dans le signal : c'est le mur
        ``session_exit`` du ManagerConfig (backtest) / du monitor (live).
        """
        df = df.copy()
        if "close" not in df.columns or "atr" not in df.columns:
            df = self.prepare(df)

        sessions = self._build_sessions(df)
        if not sessions:
            return []

        # Sigma des rendements de session : rolling(20).std() décalé de 1
        # (la session courante n'entre pas dans son propre sigma) — réplique
        # exacte de l'étude, y compris le ddof pandas par défaut.
        raw = pd.Series([np.log(s["open_out"] / s["open_in"]) for s in sessions])
        sigma = raw.rolling(self.cfg.sigma_window).std().shift(1)

        signals: list[tuple[int, Signal]] = []
        for k, sess in enumerate(sessions):
            sig_val = sigma.iloc[k]
            if not np.isfinite(sig_val) or sig_val <= 0:
                continue

            j = sess["j_in"]
            i = j - 1  # barre signal = dernière barre avant la réouverture
            if i < 0:
                continue

            sig_row = df.iloc[i]
            entry_open = float(df.iloc[j]["open"])
            # SL -1R : sl_px = entry × exp(-sigma). recalculate_from_fill
            # ré-ancre la DISTANCE |close_signal - sl| sur le fill réel →
            # on encode la distance depuis le close de la barre signal.
            sl_dist = entry_open * (1.0 - np.exp(-self.cfg.sl_sigma_mult * sig_val))
            sig_close = float(sig_row["close"])

            signal = Signal(
                instrument=instrument,
                side=Side.LONG,
                timeframe="1m",
                close=sig_close,
                open_=float(sig_row.get("open", sig_close)),
                sl=sig_close - sl_dist,
                tp_indicative=0.0,           # pas de TP : la sortie est l'heure
                # atr = distance de risque (1 sigma de session), PAS l'ATR
                # min1 : c'est l'échelle de volatilité du trade. Le slippage
                # backtest (0.03×atr = 0.03R ≈ 1 bp) reste dans l'ordre du
                # slippage de réouverture mesuré (0,5 bps), côté pessimiste.
                atr=float(sl_dist),
                rsi=50.0,
                cmf=0.0,
                bb_lower=0.0,
                bb_mid=0.0,
                bb_upper=0.0,
                # Réutilise bb_width pour la distance de risque en prix
                # (comme Fouetté avec or_range) : le guard bb_squeeze
                # (< 0.003) est inapplicable à une entrée à heure fixe.
                bb_width=round(float(sl_dist), 6),
                wr_14=0.0,
                rsi_div=0,
                ema200_ltf=0.0,
                htf_ema_fast=0.0,
                htf_ema_slow=0.0,
                htf_adx=0.0,
                regime="session_hold",
                max_spread_atr=self.cfg.max_spread_atr,
                rr=0.0,
                strategy_type="adage",
                sub_type="session_hold",
                label_factors={
                    "sigma": round(float(sig_val), 6),
                    "entry_time": self.cfg.entry_time,
                    "exit_time": self.cfg.exit_time,
                },
                timestamp=df.index[j],
            )
            signals.append((i, signal))

        return signals
