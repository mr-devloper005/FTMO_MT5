"""The shape every market-data provider returns, and the interface it satisfies.

Numbers here are floats, not the Decimals used on the execution side. This
layer feeds research — indicators over thousands of bars, backtest arithmetic —
and no value from it ever reaches an order. The Decimal rule still holds
everywhere below `ftmo_mt5.execution`.

Timezone policy
---------------
Providers hand back whatever their venue uses; this layer converts every
timestamp to **UTC epoch seconds** in `Candle.timestamp`, which is the only
representation the signal and backtest engines ever see. The originating
session/exchange timezone is preserved separately on `CandleSeries.timezone`
so a caller that needs to reason about sessions still can.
"""

__all__ = [
    "Candle",
    "CandleSeries",
    "MarketDataProvider",
    "ProviderCapabilities",
    "TIMEFRAMES",
    "DataUnavailable",
]

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional, Protocol, Sequence, runtime_checkable

# Canonical timeframes. A provider advertises the subset it can actually serve;
# nothing in the engine assumes any particular one of these exists.
TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")

# How many seconds one bar of each timeframe covers.
TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


class DataUnavailable(Exception):
    """The provider cannot serve this symbol/timeframe/range combination.

    Raised instead of quietly returning a different instrument, a different
    timeframe, or a truncated range — a backtest run on substituted data is
    worse than one that refuses to start.
    """


@dataclass(frozen=True)
class Candle:
    """One completed bar, normalized.

    Attributes:
        timestamp: Bar open time as UTC epoch seconds.
        open, high, low, close: Prices in the instrument's quote currency.
        volume: Contracts or shares traded; 0 when the venue reports none.
    """

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def time(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)

    def as_dict(self) -> dict:
        return {
            "time": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True)
class CandleSeries:
    """Candles plus the context needed to interpret them."""

    symbol: str
    timeframe: str
    candles: list[Candle]
    provider: str = ""
    # IANA name of the venue the bars came from, before UTC normalization.
    timezone: str = "UTC"

    def __len__(self) -> int:
        return len(self.candles)


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider can be asked for, so the UI never offers the impossible.

    Attributes:
        key: Stable id used in API requests ("yahoo", "mt5", "tasty").
        label: Human-readable name.
        timeframes: Canonical timeframes this provider serves.
        supports_history: Whether an explicit start/end range is honoured.
        max_intraday_days: Longest intraday lookback, per timeframe. Absent
            entries mean no known limit.
        note: Free-form caveat surfaced in the panel.
    """

    key: str
    label: str
    timeframes: tuple[str, ...] = TIMEFRAMES
    supports_history: bool = True
    max_intraday_days: dict[str, int] = field(default_factory=dict)
    note: str = ""


@runtime_checkable
class MarketDataProvider(Protocol):
    """Minimum surface a source of candles must expose."""

    def capabilities(self) -> ProviderCapabilities:
        """What this provider can serve. Must not raise or do I/O."""

    def resolve_symbol(self, symbol: str) -> str:
        """Map an application symbol to this provider's own ticker.

        Raises DataUnavailable when the provider has no equivalent.
        """

    def get_history(
        self,
        symbol: str,
        timeframe: str,
        start: date,
        end: date,
    ) -> CandleSeries:
        """Candles for exactly the requested symbol, timeframe and range.

        Raises DataUnavailable rather than substituting anything.
        """


def normalize_epoch(value) -> int:
    """Best-effort conversion of a provider timestamp to UTC epoch seconds."""
    if isinstance(value, (int, float)):
        # Some feeds report milliseconds; seconds since 1970 will not plausibly
        # exceed ~4e9 for another sixty years.
        return int(value / 1000) if value > 4_000_000_000 else int(value)
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(moment.timestamp())
    raise TypeError(f"cannot interpret {value!r} as a timestamp")


def sort_and_dedupe(candles: Sequence[Candle]) -> list[Candle]:
    """Chronological order, one bar per timestamp.

    Streaming sources re-emit the bar currently forming, so the same period can
    arrive several times; keeping every copy would put duplicate bars into every
    moving average computed on top of them.
    """
    by_time: dict[int, Candle] = {}
    for candle in candles:
        by_time[candle.timestamp] = candle
    return [by_time[t] for t in sorted(by_time)]


def drop_incomplete_last(candles: list[Candle], timeframe: str,
                         now: Optional[int] = None) -> list[Candle]:
    """Remove a still-forming final bar so signals cannot repaint."""
    if not candles:
        return candles
    span = TIMEFRAME_SECONDS.get(timeframe)
    if not span:
        return candles
    current = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    if candles[-1].timestamp + span > current:
        return candles[:-1]
    return candles
