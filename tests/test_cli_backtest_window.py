from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import pandas as pd


def test_run_backtest_applies_from_to_after_prepare_with_inclusive_utc_bounds(monkeypatch):
    """`run --mode backtest --from/--to` must preserve pre-window indicator warm-up."""

    import arabesque.__main__ as cli
    import arabesque.data.store as store
    import arabesque.execution.backtest as backtest
    import arabesque.strategies.extension.signal as extension_signal
    from arabesque.core.models import Signal

    idx = pd.date_range("2024-01-01T00:00:00Z", periods=240, freq="h")
    raw_df = pd.DataFrame(
        {
            "Open": range(len(idx)),
            "High": range(1, len(idx) + 1),
            "Low": range(len(idx)),
            "Close": range(len(idx)),
            "Volume": [1] * len(idx),
        },
        index=idx,
    )

    observed: dict[str, object] = {}
    window_start = pd.Timestamp("2024-01-05T00:00:00Z")
    window_end = pd.Timestamp("2024-01-09T03:00:00Z")

    class FakeSignalGenerator:
        def __init__(self, _config):
            pass

        def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
            observed["prepare_first_ts"] = df.index[0]
            observed["prepare_last_ts"] = df.index[-1]
            prepared = df.copy()
            prepared["warm_count"] = range(1, len(prepared) + 1)
            return prepared

        def generate_signals(self, df: pd.DataFrame, instrument: str):
            observed["generate_first_ts"] = df.index[0]
            observed["generate_last_ts"] = df.index[-1]
            observed["generate_len"] = len(df)
            observed["first_warm_count"] = int(df.iloc[0]["warm_count"])
            return [
                (0, Signal(instrument=instrument, timestamp=df.index[0].to_pydatetime())),
                (len(df) - 1, Signal(instrument=instrument, timestamp=df.index[-1].to_pydatetime())),
            ]

    class FakeBacktestRunner:
        def __init__(self, _cfg, manager_config=None, signal_generator=None, exec_config=None):
            self.signal_generator = signal_generator

        def run(self, df: pd.DataFrame, instrument: str, sub_bar_df=None):
            observed["runner_first_ts"] = df.index[0]
            observed["runner_last_ts"] = df.index[-1]
            self.signal_generator.generate_signals(df, instrument)
            return SimpleNamespace(report="fake report")

    monkeypatch.setattr(extension_signal, "ExtensionSignalGenerator", FakeSignalGenerator)
    monkeypatch.setattr(store, "load_ohlc", lambda *args, **kwargs: raw_df.copy())
    monkeypatch.setattr(backtest, "BacktestRunner", FakeBacktestRunner)
    monkeypatch.setattr(backtest, "manager_config_for", lambda instrument, timeframe: object())

    rc = cli._run_backtest(
        Namespace(
            strategy="extension",
            interval=None,
            no_weekend=False,
            instruments=["BTCUSD"],
            universe=None,
            period="730d",
            start="2024-01-05T00:00:00Z",
            end="2024-01-09T03:00:00Z",
            risk=0.40,
            verbose=False,
            no_sub_bar=True,
        )
    )

    assert rc == 0
    assert observed["prepare_first_ts"] == idx[0]
    assert observed["prepare_last_ts"] == idx[-1]
    assert observed["runner_first_ts"] == window_start
    assert observed["runner_last_ts"] == window_end
    assert observed["generate_first_ts"] == window_start
    assert observed["generate_last_ts"] == window_end
    assert observed["generate_len"] == 100
    assert observed["first_warm_count"] == 97


def test_run_backtest_date_only_to_includes_full_last_day(monkeypatch):
    """`--to YYYY-MM-DD` (sans heure) doit inclure toute la journée, pas s'arrêter
    à 00:00 UTC (réserve revue 2026-06-23). `--from` date seule reste à 00:00."""

    import arabesque.__main__ as cli
    import arabesque.data.store as store
    import arabesque.execution.backtest as backtest
    import arabesque.strategies.extension.signal as extension_signal

    idx = pd.date_range("2024-01-01T00:00:00Z", periods=240, freq="h")
    raw_df = pd.DataFrame(
        {
            "Open": range(len(idx)),
            "High": range(1, len(idx) + 1),
            "Low": range(len(idx)),
            "Close": range(len(idx)),
            "Volume": [1] * len(idx),
        },
        index=idx,
    )

    observed: dict[str, object] = {}

    class FakeSignalGenerator:
        def __init__(self, _config):
            pass

        def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
            return df.copy()

        def generate_signals(self, df: pd.DataFrame, instrument: str):
            return []

    class FakeBacktestRunner:
        def __init__(self, _cfg, manager_config=None, signal_generator=None, exec_config=None):
            self.signal_generator = signal_generator

        def run(self, df: pd.DataFrame, instrument: str, sub_bar_df=None):
            observed["runner_first_ts"] = df.index[0]
            observed["runner_last_ts"] = df.index[-1]
            from types import SimpleNamespace
            return SimpleNamespace(report="fake")

    monkeypatch.setattr(extension_signal, "ExtensionSignalGenerator", FakeSignalGenerator)
    monkeypatch.setattr(store, "load_ohlc", lambda *args, **kwargs: raw_df.copy())
    monkeypatch.setattr(backtest, "BacktestRunner", FakeBacktestRunner)
    monkeypatch.setattr(backtest, "manager_config_for", lambda instrument, timeframe: object())

    rc = cli._run_backtest(
        Namespace(
            strategy="extension", interval=None, no_weekend=False,
            instruments=["BTCUSD"], universe=None, period="730d",
            start="2024-01-02", end="2024-01-06", risk=0.40,
            verbose=False, no_sub_bar=True,
        )
    )

    assert rc == 0
    # --from date seule → 00:00 inclus
    assert observed["runner_first_ts"] == pd.Timestamp("2024-01-02T00:00:00Z")
    # --to date seule → dernière barre de la journée (23:00 H1), pas 00:00
    assert observed["runner_last_ts"] == pd.Timestamp("2024-01-06T23:00:00Z")
