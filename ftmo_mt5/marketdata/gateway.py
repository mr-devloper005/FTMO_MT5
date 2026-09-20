"""The existing broker gateways, exposed as market-data providers.

MetaTrader 5 and tastytrade already serve candles to the trading panel; this
wraps that same `get_ohlcv` call in the provider interface so the signal and
backtest engines can read from a live broker without knowing which one it is.

Nothing about the gateways changes — this only adapts their output.
"""

__all__ = ["BrokerGatewayDataProvider"]

from datetime import date, datetime, time, timezone
from typing import Any

import structlog

from ftmo_mt5.marketdata.base import (
    TIMEFRAME_SECONDS,
    Candle,
    CandleSeries,
    DataUnavailable,
    ProviderCapabilities,
    normalize_epoch,
    sort_and_dedupe,
)

logger = structlog.get_logger(__name__)

# Canonical timeframe -> the MT5-style code the gateways expect.
_GATEWAY_TIMEFRAME = {
    "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
    "1h": "H1", "4h": "H4", "1d": "D1",
}

# A gateway is asked for "the last N bars", not a date range, so a range has to
# be converted into a count. This caps how many we will ever request.
_MAX_BARS = 5000


class BrokerGatewayDataProvider:
    """Adapts a `BrokerGateway` to `MarketDataProvider`.

    Attributes:
        gateway: Any object exposing `get_ohlcv(symbol, timeframe, count)`.
        key: Provider id, matching the broker key ("mt5", "tasty").
        label: Human-readable name for the panel.
    """

    def __init__(self, gateway: Any, key: str, label: str = "") -> None:
        self.gateway = gateway
        self.key = key
        self.label = label or key

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            key=self.key,
            label=self.label,
            timeframes=tuple(_GATEWAY_TIMEFRAME),
            supports_history=False,
            note=("Live broker feed. Serves a trailing window of recent bars rather "
                  "than an arbitrary historical range — use Yahoo for long backtests."),
        )

    def resolve_symbol(self, symbol: str) -> str:
        """Broker symbols are already the application's symbols."""
        text = (symbol or "").strip().upper()
        if not text:
            raise DataUnavailable("No symbol given.")
        return text

    def get_history(self, symbol: str, timeframe: str, start: date, end: date) -> CandleSeries:
        code = _GATEWAY_TIMEFRAME.get(timeframe)
        if not code:
            raise DataUnavailable(
                f"{self.label} does not serve {timeframe} candles. "
                f"Supported: {', '.join(_GATEWAY_TIMEFRAME)}."
            )
        if start >= end:
            raise DataUnavailable("Start date must be before end date.")

        span = TIMEFRAME_SECONDS[timeframe]
        wanted = int(((end - start).days + 1) * 86400 / span)
        count = max(50, min(wanted, _MAX_BARS))

        try:
            bars = self.gateway.get_ohlcv(self.resolve_symbol(symbol),
                                          timeframe=code, count=count)
        except Exception as e:
            raise DataUnavailable(f"{self.label} could not serve {symbol}: {e}") from e

        if not bars:
            raise DataUnavailable(
                f"{self.label} returned no {timeframe} candles for {symbol}. "
                f"The broker may be disconnected or the symbol unknown to it."
            )

        # Trim to the requested window: the gateway answers with its most recent
        # N bars, which will usually overshoot on one side.
        lo = int(datetime.combine(start, time.min, tzinfo=timezone.utc).timestamp())
        hi = int(datetime.combine(end, time.max, tzinfo=timezone.utc).timestamp())

        candles = []
        for b in bars:
            try:
                stamp = normalize_epoch(b["time"])
            except (KeyError, TypeError):
                continue
            if not (lo <= stamp <= hi):
                continue
            candles.append(Candle(
                timestamp=stamp,
                open=float(b["open"]), high=float(b["high"]),
                low=float(b["low"]), close=float(b["close"]),
                volume=float(b.get("volume") or 0),
            ))

        candles = sort_and_dedupe(candles)
        if not candles:
            raise DataUnavailable(
                f"{self.label} has no {timeframe} data for {symbol} inside "
                f"{start.isoformat()}–{end.isoformat()}. Its history reaches back only "
                f"so far; use Yahoo for older ranges."
            )

        logger.info("gateway_history", provider=self.key, symbol=symbol,
                    timeframe=timeframe, bars=len(candles))

        return CandleSeries(symbol=symbol.upper(), timeframe=timeframe,
                            candles=candles, provider=self.key, timezone="UTC")
