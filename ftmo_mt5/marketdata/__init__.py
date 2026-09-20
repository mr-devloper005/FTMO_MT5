"""Market data providers.

One interface over every source of candles the application can read: the
Yahoo Finance history used for research and backtesting, and the live broker
gateways (MetaTrader 5, tastytrade) that already serve the trading panel.

Everything above this layer — the signal engine, the backtest engine — works
against `Candle` and never learns which provider produced it.
"""

__all__ = [
    "Candle",
    "MarketDataProvider",
    "ProviderCapabilities",
    "TIMEFRAMES",
    "DataUnavailable",
    "get_provider",
    "list_providers",
]

from ftmo_mt5.marketdata.base import (
    TIMEFRAMES,
    Candle,
    DataUnavailable,
    MarketDataProvider,
    ProviderCapabilities,
)
from ftmo_mt5.marketdata.registry import get_provider, list_providers
