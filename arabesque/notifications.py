"""Notification routing policy shared by live code and operational scripts.

Telegram is the audit/information stream. Ntfy is reserved for situations
requiring prompt human attention. Keep this policy in one place so a new
reporting script cannot accidentally turn routine output into push alarms.
"""

from __future__ import annotations

from collections.abc import Iterable


def is_telegram_channel(channel: str) -> bool:
    """Return whether an Apprise URL targets Telegram."""
    value = channel.lower()
    return value.startswith("tgram://") or value.startswith("telegram://")


def select_notification_channels(
    channels: Iterable[object], *, urgent: bool = False
) -> list[str]:
    """Select Apprise destinations according to Arabesque alert policy.

    Routine events are Telegram-only. Urgent intervention events are sent to
    every configured destination, including ntfy.
    """
    configured = [channel for channel in channels if isinstance(channel, str)]
    if urgent:
        return configured
    return [channel for channel in configured if is_telegram_channel(channel)]
