"""
DEPRECATED — Remplacé par arabesque/live/bar_aggregator.py

Conservé temporairement pour ne pas casser les imports existants.
Suppression prévue dans la prochaine version majeure.
"""
import warnings
warnings.warn(
    "bar_poller est déprécié. Utiliser arabesque.live.bar_aggregator.BarAggregator.",
    DeprecationWarning,
    stacklevel=2,
)

from arabesque.live.bar_aggregator import BarAggregator as BarPoller  # noqa: F401
