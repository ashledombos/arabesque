"""Compat shim — use arabesque.data.store instead."""
from arabesque.data.store import *  # noqa: F401, F403
from arabesque.data.store import load_ohlc, split_in_out_sample, yahoo_symbol, _categorize
