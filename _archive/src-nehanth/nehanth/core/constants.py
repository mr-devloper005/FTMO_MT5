"""Global constants for FTMO MT5 trading system.

Defines MT5 return codes, FTMO challenge thresholds, timezone configuration,
and magic number conventions.
"""

__all__ = [
    "SUCCESS_CODES",
    "RETRYABLE_PRICE",
    "RETRYABLE_BACKOFF",
    "FATAL_CODES",
    "MT5_SUCCESS_CODES",
    "MT5_RETRYABLE_PRICE",
    "MT5_RETRYABLE_BACKOFF",
    "MT5_FATAL_CODES",
    "FTMO_THRESHOLDS",
    "FTMO_PRAGUE_TZ",
    "DEFAULT_MAGIC",
]

from decimal import Decimal
from zoneinfo import ZoneInfo

# MT5 Return Code Classifications
# https://www.mql5.com/en/docs/integration/mt5_trade/mt5_orders/sendtrade

SUCCESS_CODES = frozenset({
    10000,  # TRADE_RETCODE_DONE
    10001,  # TRADE_RETCODE_DONE_PARTIAL
})
"""MT5 return codes indicating successful order execution."""

RETRYABLE_PRICE = frozenset({
    10004,  # TRADE_RETCODE_TIMEOUT (price-dependent)
    10020,  # TRADE_RETCODE_LOCKED (market lock, retry with fresh price)
})
"""MT5 return codes retryable with fresh price fetch."""

RETRYABLE_BACKOFF = frozenset({
    10012,  # TRADE_RETCODE_PRICE_CHANGED
    10021,  # TRADE_RETCODE_FROZEN
    10024,  # TRADE_RETCODE_ONLY_REAL
    10028,  # TRADE_RETCODE_INVALID_TRADE
    10029,  # TRADE_RETCODE_TRADE_TIMEOUT
    10031,  # TRADE_RETCODE_TRADE_BAD_EXPIRATION
})
"""MT5 return codes requiring exponential backoff retry."""

FATAL_CODES = frozenset({
    10011,  # TRADE_RETCODE_NO_MONEY
    10025,  # TRADE_RETCODE_LIMIT_ORDERS
    10026,  # TRADE_RETCODE_LIMIT_VOLUME
})
"""MT5 return codes indicating unrecoverable errors."""

MT5_SUCCESS_CODES = SUCCESS_CODES
MT5_RETRYABLE_PRICE = RETRYABLE_PRICE
MT5_RETRYABLE_BACKOFF = RETRYABLE_BACKOFF
MT5_FATAL_CODES = FATAL_CODES

# FTMO Challenge Thresholds
FTMO_THRESHOLDS = {
    "daily_loss_limit_pct": Decimal("5"),  # Maximum 5% daily loss
    "overall_loss_limit_pct": Decimal("10"),  # Maximum 10% overall loss
    "minimum_profit_target_pct": Decimal("10"),  # Minimum 10% profit target (One-Step)
    "two_step_minimum_profit_pct": Decimal("5"),  # Minimum 5% for first step
    "minimum_profit_per_day_pct": Decimal("0.5"),  # Some challenges require min daily profit
}
"""Percentage-based limits for FTMO challenge rules."""

# Timezone Configuration
FTMO_PRAGUE_TZ = ZoneInfo("Europe/Prague")
"""FTMO evaluation timezone (Prague) for EOD balance calculations."""

# MT5 Magic Number
DEFAULT_MAGIC = 234000
"""Default magic number for MT5 orders from this system."""
