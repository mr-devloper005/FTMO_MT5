"""Instrument metadata: what one unit of a symbol is actually worth.

The backtest engine needs this and the strategy must never contain it. A
futures contract moves in ticks worth a fixed number of dollars; a share moves
in dollars per share. Hard-coding either inside a strategy is what makes a
system that only works for one instrument.
"""

__all__ = ["Instrument", "AssetType", "resolve_instrument", "list_instruments"]

from ftmo_mt5.instruments.metadata import (
    AssetType,
    Instrument,
    list_instruments,
    resolve_instrument,
)
