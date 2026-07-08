from __future__ import annotations

from datetime import datetime, timezone

from scripts.audit_uptime_events import summarize


def _event(ts: str, status: str, cause: str):
    return {
        "ts": ts,
        "_ts": datetime.fromisoformat(ts).replace(tzinfo=timezone.utc),
        "status": status,
        "cause": cause,
    }


def test_summarize_computes_uptime_factor_and_degraded_windows():
    events = [
        _event("2026-05-29T00:00:00", "ok:age=10s", "ok"),
        _event("2026-05-29T00:05:00", "pricefeed_partial:30/31", "partial_feed"),
        _event("2026-05-29T00:10:00", "ok:age=20s", "ok"),
    ]

    summary = summarize(events)

    assert summary["duration_s"] == 600
    assert summary["uptime_factor"] == 0.5
    assert summary["by_cause"]["ok"] == 300
    assert summary["by_cause"]["partial_feed"] == 300
    assert len(summary["degraded_windows"]) == 1


def test_summarize_caps_large_timer_gap():
    events = [
        _event("2026-05-29T00:00:00", "feed_stale:900s", "feed_stale"),
        _event("2026-05-29T01:00:00", "ok:age=20s", "ok"),
    ]

    summary = summarize(events)

    assert summary["duration_s"] == 900
    assert summary["by_cause"]["feed_stale"] == 900
