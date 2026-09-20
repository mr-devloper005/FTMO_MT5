"""Instrument-agnostic historical backtesting.

Takes a configuration, a data provider and a strategy; returns trades, metrics
and an equity curve. Places no orders and holds no broker credentials.
"""

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "ExecutionModel",
    "Trade",
    "run_backtest",
]

from ftmo_mt5.backtest.engine import run_backtest
from ftmo_mt5.backtest.models import (
    BacktestConfig,
    BacktestResult,
    ExecutionModel,
    Trade,
)
