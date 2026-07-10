"""Tests lot 3 session-or : générateur Adage (session-hold nocturne or).

Design figé (docs/audit/session_or_wf_protocole_2026-07-10.md) :
- signal LONG émis sur la barre i = dernière barre AVANT la 1re barre
  min1 >= 18:00 America/New_York → fill à l'open de i+1 (convention runner) ;
- SL -1R avec R = 1.0 × sigma(20 rendements de session, causal shift 1) ;
- garde-fous : exit apparié à J+1..J+3, session > 20h exclue, >= 60 barres ;
- la sortie n'est PAS dans le signal (mur du moteur via ManagerConfig).
"""

import numpy as np
import pandas as pd
import pytest

from arabesque.core.models import Side
from arabesque.strategies.adage.signal import (
    AdageConfig, AdageSignalGenerator, adage_manager_config,
)
from arabesque.modules.position_manager import PositionManager


# ── Fabrique de données min1 synthétiques ─────────────────────────────

def _synthetic_min1(n_days: int = 30, start: str = "2025-06-02",
                    seed: int = 7) -> pd.DataFrame:
    """Barres min1 UTC continues, trouées comme le marché de l'or :
    fermeture 17:00-18:00 NY chaque jour + weekend (ven 17:00 NY →
    dim 18:00 NY). Prix = marche aléatoire douce (sigma de session > 0).
    """
    idx = pd.date_range(start, periods=n_days * 1440, freq="1min", tz="UTC")
    ny = idx.tz_convert("America/New_York")
    # Trou quotidien 17:00-17:59 NY
    keep = ny.hour != 17
    # Weekend : rien entre vendredi 17:00 NY et dimanche 18:00 NY
    wd, hr = ny.weekday, ny.hour
    weekend = ((wd == 4) & (hr >= 17)) | (wd == 5) | ((wd == 6) & (hr < 18))
    keep &= ~weekend
    idx = idx[keep]

    rng = np.random.RandomState(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 2e-5, len(idx))))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * 1.00001
    low = np.minimum(open_, close) * 0.99999
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close,
         "Volume": 1.0},
        index=idx,
    )


@pytest.fixture(scope="module")
def df_month():
    return _synthetic_min1(n_days=45)


@pytest.fixture(scope="module")
def signals_month(df_month):
    gen = AdageSignalGenerator()
    df = gen.prepare(df_month)
    return df, gen.generate_signals(df, "XAUUSD")


# ── prepare ───────────────────────────────────────────────────────────

def test_prepare_conserve_les_colonnes_capitalisees(df_month):
    out = AdageSignalGenerator().prepare(df_month)
    for col in ("Open", "High", "Low", "Close", "Volume", "atr"):
        assert col in out.columns
    assert (out["atr"].iloc[50:] > 0).all()


# ── Convention d'entrée (anti-lookahead) ──────────────────────────────

def test_fill_bar_est_la_premiere_barre_apres_18h_ny(signals_month):
    """Pour chaque signal (i, sig) : la barre i+1 (le fill du runner) est
    >= 18:00 NY et la barre i est AVANT (dernière barre pré-réouverture)."""
    df, signals = signals_month
    assert len(signals) > 0
    ny = pd.DatetimeIndex(df.index).tz_convert("America/New_York")
    for i, sig in signals:
        fill = ny[i + 1]
        assert fill.hour >= 18, f"fill {fill} avant la réouverture"
        prev = ny[i]
        # La barre signal appartient à la plage pré-réouverture (marché
        # fermé 17:00-18:00 NY → la barre i est <= 16:59 le même jour,
        # ou plus tôt après un trou).
        assert prev < fill
        assert not (prev.hour >= 18 and prev.date() == fill.date()), \
            "la barre signal ne doit pas déjà être dans la session"
        assert sig.side == Side.LONG
        assert sig.strategy_type == "adage"
        assert sig.tp_indicative == 0.0
        assert sig.timestamp == df.index[i + 1]


def test_un_seul_signal_par_date_ny(signals_month):
    df, signals = signals_month
    ny = pd.DatetimeIndex(df.index).tz_convert("America/New_York")
    fill_dates = [ny[i + 1].date() for i, _ in signals]
    assert len(fill_dates) == len(set(fill_dates))


def test_pas_d_entree_vendredi_ni_samedi(signals_month):
    """Marché fermé vendredi 17:00 NY : aucune barre >= 18:00 NY ne peut
    exister vendredi/samedi → aucune entrée ces jours-là."""
    df, signals = signals_month
    ny = pd.DatetimeIndex(df.index).tz_convert("America/New_York")
    for i, _ in signals:
        assert ny[i + 1].weekday() not in (4, 5)


# ── Sigma causal ──────────────────────────────────────────────────────

def test_pas_de_signal_avant_20_sessions_d_historique(signals_month):
    """Les 20 premières sessions n'ont pas de sigma → pas de signal."""
    df, signals = signals_month
    gen = AdageSignalGenerator()
    sessions = gen._build_sessions(df)
    assert len(sessions) > 20
    first_signal_fill = df.index[signals[0][0] + 1]
    assert first_signal_fill == sessions[20]["t_in"]
    assert len(signals) == len(sessions) - 20


def test_sl_correspond_a_moins_un_sigma(signals_month):
    """SL encodé = distance entry_open × (1 - exp(-sigma)) depuis le close
    de la barre signal, sigma = std(ddof=1) des 20 rendements précédents."""
    df, signals = signals_month
    gen = AdageSignalGenerator()
    sessions = gen._build_sessions(df)
    raw = pd.Series([np.log(s["open_out"] / s["open_in"]) for s in sessions])
    t_in_to_k = {s["t_in"]: k for k, s in enumerate(sessions)}

    for i, sig in signals:
        k = t_in_to_k[df.index[i + 1]]
        sigma = raw.iloc[k - 20:k].std()
        assert sigma > 0
        assert sig.label_factors["sigma"] == pytest.approx(sigma, abs=1e-6)
        entry_open = df.iloc[i + 1]["Open"]
        expected_dist = entry_open * (1.0 - np.exp(-sigma))
        assert (sig.close - sig.sl) == pytest.approx(expected_dist, rel=1e-9)


# ── Garde-fous de session ─────────────────────────────────────────────

def test_session_trouee_moins_de_60_barres_exclue():
    """Une session dont le chemin min1 compte < 60 barres est exclue
    (trou de feed) — les autres sessions restent signalées."""
    df = _synthetic_min1(n_days=45)
    gen = AdageSignalGenerator()
    prepared = gen.prepare(df)
    sessions = gen._build_sessions(prepared)
    victim = sessions[25]

    # Vider la session sauf ses 5 premières barres (jusqu'au mur 08:00 Londres)
    lon = pd.DatetimeIndex(df.index).tz_convert("Europe/London")
    t_in = victim["t_in"]
    day_after = (t_in.tz_convert("Europe/London") + pd.Timedelta(days=1))
    wall = day_after.normalize() + pd.Timedelta(hours=8)
    in_path = (df.index > t_in + pd.Timedelta(minutes=5)) & (lon < wall)
    trimmed = gen.prepare(df[~in_path])

    fills = {s["t_in"] for s in gen._build_sessions(trimmed)}
    assert t_in not in fills
    signal_fills = {trimmed.index[i + 1]
                    for i, _ in gen.generate_signals(trimmed, "XAUUSD")}
    assert t_in not in signal_fills
    assert len(signal_fills) > 0


def test_session_de_plus_de_20h_exclue():
    """Si le 1er mur disponible est à > 20h de l'entrée (férié/trou),
    la session est exclue — pas d'entrée orpheline."""
    df = _synthetic_min1(n_days=45)
    gen = AdageSignalGenerator()
    sessions = gen._build_sessions(gen.prepare(df))
    victim = sessions[25]
    t_in = victim["t_in"]

    # Supprimer TOUTES les barres du lendemain (date Londres) → le mur
    # apparié glisse à J+2, soit > 20h après l'entrée.
    lon = pd.DatetimeIndex(df.index).tz_convert("Europe/London")
    next_day = (t_in.tz_convert("Europe/London") + pd.Timedelta(days=1)).date()
    trimmed = gen.prepare(df[np.asarray(lon.date) != next_day])

    fills = {s["t_in"] for s in gen._build_sessions(trimmed)}
    assert t_in not in fills


# ── Profil ManagerConfig ──────────────────────────────────────────────

def test_adage_manager_config_aucun_overlay():
    cfg = adage_manager_config()
    assert cfg.be_enabled is False
    assert cfg.roi_enabled is False
    assert cfg.trailing_tiers == []
    assert cfg.giveback_enabled is False
    assert cfg.deadfish_enabled is False
    assert cfg.time_stop_enabled is False
    assert cfg.session_exit == "08:00@Europe/London"
    # Parse fail-fast OK à l'init du manager
    PositionManager(cfg)


def test_config_entry_time_invalide_echoue_a_l_init():
    with pytest.raises(ValueError, match="session_exit invalide"):
        AdageSignalGenerator(AdageConfig(entry_time="18h@New_York"))
