from __future__ import annotations

import argparse

from scripts import shadow_reference_check as src


def test_build_commands_are_read_only_by_default():
    args = argparse.Namespace(
        since="2026-05-16T08:44:00Z",
        until=None,
        strategy=None,
        broker=None,
        min_missing=0,
        allow_yahoo=False,
    )

    commands = src.build_commands(args)

    flat = [" ".join(cmd) for cmd in commands]
    assert any("replay_signals_vs_live.py" in cmd for cmd in flat)
    assert any("replay_live_vs_theory.py" in cmd and "--no-persist" in cmd for cmd in flat)
    assert all("systemctl" not in cmd for cmd in flat)
    assert all("arabesque-live.service" not in cmd for cmd in flat)


def test_build_commands_forward_filters():
    args = argparse.Namespace(
        since="J-7",
        until="2026-05-28T20:00:00Z",
        strategy="extension",
        broker="ftmo_challenge",
        min_missing=3,
        allow_yahoo=True,
    )

    signals, live_theory = src.build_commands(args)

    assert "--until" in signals
    assert "--strategy" in live_theory
    assert "extension" in live_theory
    assert "--broker" in live_theory
    assert "ftmo_challenge" in live_theory
    assert "--allow-yahoo" in signals
    assert "--allow-yahoo" in live_theory
