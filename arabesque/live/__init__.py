# Lazy imports pour éviter le RuntimeWarning quand engine est exécuté via python -m
# Le warning survient car l'import du package (arabesque.live) charge engine.py
# avant que python -m ne l'exécute comme __main__.


def __getattr__(name):
    """Lazy import pour éviter les imports circulaires et le RuntimeWarning."""
    if name == "PriceFeedManager":
        from arabesque.live.price_feed import PriceFeedManager
        return PriceFeedManager
    if name == "BarAggregator":
        from arabesque.live.bar_aggregator import BarAggregator
        return BarAggregator
    if name == "BarAggregatorConfig":
        from arabesque.live.bar_aggregator import BarAggregatorConfig
        return BarAggregatorConfig
    if name == "OrderDispatcher":
        from arabesque.live.order_dispatcher import OrderDispatcher
        return OrderDispatcher
    if name == "PendingSignal":
        from arabesque.live.order_dispatcher import PendingSignal
        return PendingSignal
    if name == "LiveEngine":
        from arabesque.live.engine import LiveEngine
        return LiveEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PriceFeedManager",
    "BarAggregator",
    "BarAggregatorConfig",
    "OrderDispatcher",
    "PendingSignal",
    "LiveEngine",
]
