"""Choosing which option contract a signal trades.

Two decisions, both configurable and neither hard-coded to one product:

    which expiry   — the nearest one satisfying the configured rule
    which strike   — at the money, rounded to the product's strike increment

Expiry calendars here are generated from a weekday rule rather than read from
an exchange calendar. That is an approximation: it does not know about holidays
or the exact listing schedule of every weekly. For a strategy that always takes
the *nearest* expiry the error is at most a day, and it is documented in the
result rather than hidden.
"""

__all__ = [
    "ExpiryRule",
    "atm_strike",
    "next_expiry",
    "periods_per_year",
    "contract_label",
    "daily_volatility_series",
]

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Optional, Sequence

from ftmo_mt5.instruments import Instrument


class ExpiryRule(str, Enum):
    """Which expiries the product is assumed to list."""

    DAILY = "daily"                  # every weekday
    MWF = "mwf"                      # Mon/Wed/Fri — the /NQ weekly pattern
    FRIDAY = "friday"                # weeklies only
    MONTHLY = "monthly"              # third Friday of each month


_WEEKDAYS = {
    ExpiryRule.DAILY: {0, 1, 2, 3, 4},
    ExpiryRule.MWF: {0, 2, 4},
    ExpiryRule.FRIDAY: {4},
}

# Futures options settle in the afternoon on their expiry date. Using a single
# settlement hour keeps time-to-expiry honest without an exchange calendar.
_SETTLEMENT_HOUR_UTC = 20


def _third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    # 4 == Friday; step to the first Friday, then two weeks on.
    offset = (4 - first.weekday()) % 7
    return first + timedelta(days=offset + 14)


def next_expiry(after: datetime, rule: ExpiryRule = ExpiryRule.MWF,
                min_days: float = 0.0) -> datetime:
    """First expiry at least `min_days` after `after`, as a UTC datetime.

    Args:
        after: The moment the position would be opened.
        rule: Which weekdays the product lists expiries on.
        min_days: Skip expiries closer than this, for strategies that do not
            want same-day settlement risk.
    """
    threshold = after + timedelta(days=min_days)
    day = threshold.date()

    for _ in range(400):                       # a year of candidates is plenty
        candidate: Optional[date] = None
        if rule is ExpiryRule.MONTHLY:
            third = _third_friday(day.year, day.month)
            candidate = third if third >= day else _third_friday(
                day.year + (day.month == 12), day.month % 12 + 1)
            day = candidate
        elif day.weekday() in _WEEKDAYS[rule]:
            candidate = day

        if candidate is not None:
            moment = datetime.combine(candidate, time(_SETTLEMENT_HOUR_UTC),
                                      tzinfo=timezone.utc)
            if moment >= threshold:
                return moment

        day += timedelta(days=1)

    raise ValueError(f"No expiry found for rule {rule} after {after}")


def atm_strike(price: float, instrument: Instrument,
               increment: Optional[float] = None) -> float:
    """Nearest listed strike to the current price.

    Uses the product's strike increment, not its tick size: /NQ trades in 0.25
    ticks but lists options every 10 points, and rounding to the tick would
    invent strikes that do not exist.
    """
    step = increment or instrument.option_strike_increment
    if step <= 0:
        return price
    return round(price / step) * step


def periods_per_year(timeframe_seconds: int, instrument: Instrument) -> float:
    """How many bars of this size fit in a trading year.

    Needed to annualize a per-bar volatility. Uses the instrument's own session
    length: futures trade nearly around the clock, equities six and a half
    hours, and treating them alike would misstate volatility by a factor of
    three or more.
    """
    if timeframe_seconds <= 0:
        return 0.0
    seconds_per_year = (instrument.trading_hours_per_day * 3600
                        * instrument.trading_days_per_year)
    return seconds_per_year / timeframe_seconds


def daily_volatility_series(candles: Sequence, lookback_days: int = 20,
                            trading_days_per_year: float = 252.0,
                            fallback_periods_per_year: float = 0.0) -> list[float]:
    """Annualized volatility at every bar, estimated from *daily* closes.

    Volatility is a daily-scale quantity and estimating it from intraday
    returns understates it badly: measured on /NQ, 15-minute bars give ~11%
    where daily bars give ~21% over the same window. Scaling intraday returns
    up by the number of bars in a year assumes every bar is equally active,
    and the overnight bars are not.

    Estimating from daily closes also makes the estimate independent of the
    backtest's timeframe, which it has to be — the same option cannot be worth
    two different amounts because the user picked 5m instead of 1h.

    Args:
        candles: The series, chronological.
        lookback_days: Trading days in the estimation window.
        trading_days_per_year: Annualization factor.
        fallback_periods_per_year: When the range is too short to hold enough
            daily closes, fall back to a per-bar estimate scaled by this. Zero
            disables the fallback, leaving those bars unpriceable.

    Returns:
        One volatility per bar, aligned by index. 0.0 where not yet estimable.
    """
    from ftmo_mt5.options.pricing import realized_volatility

    out: list[float] = []
    daily: list[float] = []
    current_day = None

    for i, candle in enumerate(candles):
        day = datetime.fromtimestamp(candle.timestamp, tz=timezone.utc).date()
        if day != current_day:
            current_day = day
            daily.append(candle.close)
        else:
            daily[-1] = candle.close        # running close of the day so far

        vol = realized_volatility(daily, trading_days_per_year, lookback_days)
        if vol <= 0 and len(daily) > 3:
            # Short range: use whatever full days exist rather than nothing.
            vol = realized_volatility(daily, trading_days_per_year, len(daily) - 1)
        if vol <= 0 and fallback_periods_per_year > 0:
            vol = realized_volatility([c.close for c in candles[: i + 1]],
                                      fallback_periods_per_year, 30)
        out.append(vol)

    return out


def contract_label(instrument: Instrument, option_type: str,
                   strike: float, expiry: datetime) -> str:
    """A readable identifier for the modelled contract.

    Deliberately not a real exchange symbol: these contracts are priced by a
    model, and dressing them up as tradable symbols would invite someone to
    send one to a broker.
    """
    kind = "C" if option_type.upper().startswith("C") else "P"
    return f"{instrument.root} {expiry.strftime('%Y-%m-%d')} {kind}{strike:g}"
