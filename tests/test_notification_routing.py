"""Notification routing: Telegram is routine; ntfy is intervention-only."""

from arabesque.notifications import select_notification_channels


CHANNELS = [
    "tgram://bot/chat",
    "ntfys://urgent-topic",
]


def test_routine_notification_is_telegram_only():
    assert select_notification_channels(CHANNELS, urgent=False) == [
        "tgram://bot/chat"
    ]


def test_urgent_notification_includes_ntfy():
    assert select_notification_channels(CHANNELS, urgent=True) == CHANNELS
