"""Signal-producing strategies.

A strategy turns raw candles into a direction plus a confidence score. It never
talks to MT5 and never sizes a position — the risk agent owns sizing, and the
execution agent owns order placement.
"""

__all__ = [
    "EMACrossoverStrategy",
]

from ftmo_mt5.strategy.ema_crossover import EMACrossoverStrategy
