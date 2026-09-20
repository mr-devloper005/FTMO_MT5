"""Yahoo Finance as a historical/research data source.

Research only — Yahoo never executes anything. Live quotes and order flow stay
with the MetaTrader 5 and tastytrade gateways exactly as before.

Yahoo's intraday history is short and the limits are not documented in the
response, only in behaviour: ask for 5-minute bars over two years and it
returns an empty frame rather than an error. Those limits are encoded in
`capabilities()` and enforced up front so the user gets a sentence explaining
what to change instead of an empty backtest.
"""

__all__ = ["YahooHistoricalDataProvider"]

from datetime import date, timedelta
from typing import Optional

import structlog

from ftmo_mt5.marketdata.base import (
    Candle,
    CandleSeries,
    DataUnavailable,
    ProviderCapabilities,
    normalize_epoch,
    sort_and_dedupe,
)

logger = structlog.get_logger(__name__)

# Canonical timeframe -> Yahoo interval string.
_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "60m", "1d": "1d",
}

# Yahoo serves intraday history for a limited trailing window only.
_MAX_INTRADAY_DAYS = {
    "1m": 30, "5m": 60, "15m": 60, "30m": 60, "1h": 730,
}

# Futures roots the application uses, mapped to Yahoo's continuous-contract
# tickers. Anything else with a leading slash falls back to "<ROOT>=F", which
# is Yahoo's own convention.
_FUTURES_TICKER = {
    "NQ": "NQ=F", "MNQ": "MNQ=F",
    "ES": "ES=F", "MES": "MES=F",
    "YM": "YM=F", "RTY": "RTY=F",
    "CL": "CL=F", "MCL": "MCL=F",
    "GC": "GC=F", "MGC": "MGC=F",
    "SI": "SI=F", "NG": "NG=F",
    "ZB": "ZB=F", "ZN": "ZN=F",
}

# Dated contracts ("/NQU6") have no Yahoo equivalent; Yahoo only publishes the
# continuous front-month series, so the root is what we resolve to.
_MONTH_CODES = "FGHJKMNQUVXZ"


class YahooHistoricalDataProvider:
    """Historical OHLCV from Yahoo Finance, normalized to `Candle`."""

    key = "yahoo"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            key=self.key,
            label="Yahoo Finance (historical)",
            timeframes=tuple(_INTERVAL),
            supports_history=True,
            max_intraday_days=dict(_MAX_INTRADAY_DAYS),
            note=("Research data only — not used for execution. Intraday history "
                  "is limited to a trailing window (1m: 30 days, 5m–30m: 60 days)."),
        )

    # ------------------------------------------------------------------
    # Symbols
    # ------------------------------------------------------------------

    def resolve_symbol(self, symbol: str) -> str:
        """Application symbol -> Yahoo ticker.

        "/NQ" and "/NQU6" both resolve to "NQ=F": Yahoo publishes a continuous
        front-month series rather than individual dated contracts, so a
        backtest over a dated symbol is really a backtest over the continuous
        series. That substitution is deliberate and reported to the caller.
        """
        text = (symbol or "").strip().upper()
        if not text:
            raise DataUnavailable("No symbol given.")

        if not text.startswith("/"):
            return text          # equities and ETFs use their plain ticker

        root = text.lstrip("/")
        # Strip a dated contract's month+year suffix down to the root.
        if len(root) > 2 and root[-1].isdigit():
            trimmed = root.rstrip("0123456789")
            if trimmed and trimmed[-1] in _MONTH_CODES:
                trimmed = trimmed[:-1]
            if trimmed in _FUTURES_TICKER:
                root = trimmed

        ticker = _FUTURES_TICKER.get(root)
        if ticker:
            return ticker
        # Unknown root: Yahoo's own convention is the best guess, and an empty
        # frame later will be reported honestly rather than silently swapped.
        return f"{root}=F"

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def validate(self, symbol: str, timeframe: str, start: date, end: date) -> None:
        """Reject a request Yahoo cannot serve, with a reason the user can act on."""
        if timeframe not in _INTERVAL:
            supported = ", ".join(_INTERVAL)
            raise DataUnavailable(
                f"Yahoo does not provide {timeframe} data. Supported: {supported}."
            )
        if start >= end:
            raise DataUnavailable("Start date must be before end date.")
        if end > date.today() + timedelta(days=1):
            raise DataUnavailable("End date is in the future.")

        limit = _MAX_INTRADAY_DAYS.get(timeframe)
        if limit is not None:
            oldest = date.today() - timedelta(days=limit)
            if start < oldest:
                raise DataUnavailable(
                    f"Yahoo only provides {timeframe} history for the last {limit} days "
                    f"(back to {oldest.isoformat()}). Choose a later start date, or use "
                    f"a larger timeframe such as 1d."
                )

    def get_history(self, symbol: str, timeframe: str, start: date, end: date) -> CandleSeries:
        self.validate(symbol, timeframe, start, end)
        ticker = self.resolve_symbol(symbol)
        interval = _INTERVAL[timeframe]

        try:
            import yfinance
        except ImportError as e:                      # pragma: no cover - env issue
            raise DataUnavailable("yfinance is not installed on the server.") from e

        try:
            frame = yfinance.download(
                ticker,
                start=start.isoformat(),
                # Yahoo treats `end` as exclusive; add a day so the user's last
                # selected date is actually included.
                end=(end + timedelta(days=1)).isoformat(),
                interval=interval,
                progress=False,
                auto_adjust=False,
                threads=False,
            )
        except Exception as e:
            logger.warning("yahoo_download_failed", ticker=ticker, error=str(e))
            raise DataUnavailable(f"Yahoo request failed for {ticker}: {e}") from e

        if frame is None or frame.empty:
            raise DataUnavailable(
                f"Yahoo returned no {timeframe} data for {symbol} ({ticker}) between "
                f"{start.isoformat()} and {end.isoformat()}. The symbol may not exist "
                f"or the range may be outside Yahoo's intraday window."
            )

        candles, venue_tz = self._to_candles(frame, ticker)
        if not candles:
            raise DataUnavailable(f"Yahoo returned no usable rows for {symbol} ({ticker}).")

        logger.info("yahoo_history", symbol=symbol, ticker=ticker,
                    timeframe=timeframe, bars=len(candles))

        return CandleSeries(
            symbol=symbol.upper(),
            timeframe=timeframe,
            candles=candles,
            provider=self.key,
            timezone=venue_tz,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _to_candles(frame, ticker: str) -> tuple[list[Candle], str]:
        """DataFrame -> Candles, tolerating yfinance's column shapes.

        A single-ticker download still comes back with a two-level column index
        in current yfinance, so the ticker level is dropped when present.
        """
        data = frame
        if hasattr(data.columns, "nlevels") and data.columns.nlevels > 1:
            try:
                data = data.xs(ticker, axis=1, level=-1)
            except (KeyError, ValueError):
                data = data.droplevel(-1, axis=1)

        index_tz = getattr(data.index, "tz", None)
        venue_tz = str(index_tz) if index_tz is not None else "UTC"

        wanted = {c.lower(): c for c in data.columns}
        try:
            o, h, l, c = (wanted["open"], wanted["high"], wanted["low"], wanted["close"])
        except KeyError as e:
            raise DataUnavailable(f"Yahoo response is missing the {e} column.") from e
        v = wanted.get("volume")

        candles: list[Candle] = []
        for stamp, row in data.iterrows():
            close = row[c]
            if close is None or close != close:       # NaN rows appear on holidays
                continue
            try:
                candles.append(Candle(
                    timestamp=normalize_epoch(stamp.to_pydatetime()),
                    open=float(row[o]), high=float(row[h]),
                    low=float(row[l]), close=float(close),
                    volume=float(row[v]) if v and row[v] == row[v] else 0.0,
                ))
            except (TypeError, ValueError):
                continue

        return sort_and_dedupe(candles), venue_tz
