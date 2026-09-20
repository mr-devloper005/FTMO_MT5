"""Instrument-agnostic signal generation.

Reads normalized candles and produces signals. Knows nothing about brokers,
credentials, providers or order routing.
"""

__all__ = [
    "Signal",
    "SignalEngine",
    "StrategyConfig",
    "Verdict",
    "get_strategy",
    "list_strategies",
]

from ftmo_mt5.signals.engine import Signal, SignalEngine, StrategyConfig
from ftmo_mt5.signals.strategies import Verdict, get_strategy, list_strategies

# Imported for its side effect: registering the hold-until-broken family into
# STRATEGIES. Must come after `strategies`, which owns the registry.
from ftmo_mt5.signals import hold_strategies  # noqa: F401,E402
