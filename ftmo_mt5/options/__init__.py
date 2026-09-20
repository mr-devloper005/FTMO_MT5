"""Modelled options on futures, for backtesting.

No historical option prices are available to this application, so premiums are
computed with Black-76 from the underlying's price and its realized volatility.
Results are estimates of what the strategy would have done, not a record of
traded prices — see `pricing` for exactly which way each approximation errs.
"""

__all__ = [
    "ExpiryRule",
    "atm_strike",
    "black76_delta",
    "black76_price",
    "contract_label",
    "daily_volatility_series",
    "intrinsic_value",
    "next_expiry",
    "periods_per_year",
    "realized_volatility",
]

from ftmo_mt5.options.contracts import (
    ExpiryRule,
    atm_strike,
    contract_label,
    daily_volatility_series,
    next_expiry,
    periods_per_year,
)
from ftmo_mt5.options.pricing import (
    black76_delta,
    black76_price,
    intrinsic_value,
    realized_volatility,
)
