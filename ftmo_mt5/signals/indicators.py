"""Technical indicators over a normalized candle series.

Every function returns a list the same length as its input, with `None` in the
positions where there is not yet enough history to compute a value. That
alignment is what makes look-ahead bias hard to write by accident: index i of
any indicator is computed from candles 0..i and nothing later.

Adding an indicator means adding a function here and referencing it from a
strategy — no change to the engine.
"""

__all__ = ["ema", "sma", "rsi", "atr", "true_range", "adx", "directional"]

from typing import Optional, Sequence

from ftmo_mt5.marketdata.base import Candle

Series = list[Optional[float]]


def sma(values: Sequence[float], period: int) -> Series:
    """Simple moving average."""
    out: Series = [None] * len(values)
    if period <= 0:
        return out
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: Sequence[float], period: int) -> Series:
    """Exponential moving average, seeded with the first `period` average.

    Seeding on a simple average rather than the first value keeps the early
    part of the series from being dragged by a single print.
    """
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out

    alpha = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def true_range(candles: Sequence[Candle]) -> Series:
    """True range per bar; the first bar has no previous close so it is None."""
    out: Series = [None] * len(candles)
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        out[i] = max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
    return out


def atr(candles: Sequence[Candle], period: int = 14) -> Series:
    """Average true range, smoothed the way Wilder defined it."""
    out: Series = [None] * len(candles)
    tr = true_range(candles)
    if len(candles) < period + 1:
        return out

    window = [v for v in tr[1:period + 1] if v is not None]
    if len(window) < period:
        return out

    prev = sum(window) / period
    out[period] = prev
    for i in range(period + 1, len(candles)):
        current = tr[i]
        if current is None:
            out[i] = prev
            continue
        prev = (prev * (period - 1) + current) / period
        out[i] = prev
    return out


def rsi(values: Sequence[float], period: int = 14) -> Series:
    """Relative strength index, Wilder smoothing.

    A period with no losses gives 100 rather than dividing by zero.
    """
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)

    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)

    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)

    return out


def directional(candles: Sequence[Candle], period: int = 14) -> tuple[Series, Series]:
    """Wilder's +DI and -DI, index-aligned.

    Directional movement is deliberately exclusive: on a bar where price
    extended in both directions, only the larger extension counts and the other
    is zero. Without that rule an inside bar would register movement both ways
    and the two lines would rise together, which is the opposite of what they
    are meant to show.
    """
    up: Series = [None] * len(candles)
    down: Series = [None] * len(candles)
    if len(candles) < period + 1:
        return up, down

    tr = true_range(candles)
    plus_dm, minus_dm = [], []
    for i in range(1, len(candles)):
        gain = candles[i].high - candles[i - 1].high
        drop = candles[i - 1].low - candles[i].low
        plus_dm.append(gain if gain > drop and gain > 0 else 0.0)
        minus_dm.append(drop if drop > gain and drop > 0 else 0.0)

    # Wilder smoothing: seed with a sum, then decay one period's worth per bar.
    sm_tr = sum(t for t in tr[1:period + 1] if t is not None)
    sm_plus = sum(plus_dm[:period])
    sm_minus = sum(minus_dm[:period])

    for i in range(period, len(candles)):
        if i > period:
            sm_tr = sm_tr - sm_tr / period + (tr[i] or 0.0)
            sm_plus = sm_plus - sm_plus / period + plus_dm[i - 1]
            sm_minus = sm_minus - sm_minus / period + minus_dm[i - 1]
        if sm_tr > 0:
            up[i] = 100.0 * sm_plus / sm_tr
            down[i] = 100.0 * sm_minus / sm_tr
    return up, down


def adx(candles: Sequence[Candle], period: int = 14) -> Series:
    """Average directional index — trend strength, with no opinion on direction.

    ADX rises when one directional line dominates the other, whichever one it
    is. A falling market with conviction scores as highly as a rising one, which
    is why it is a filter for *whether* to trade rather than which way.
    """
    out: Series = [None] * len(candles)
    up, down = directional(candles, period)

    dx = []
    for i in range(len(candles)):
        if up[i] is None or down[i] is None:
            dx.append(None)
            continue
        total = up[i] + down[i]
        dx.append(100.0 * abs(up[i] - down[i]) / total if total > 0 else 0.0)

    first = next((i for i, v in enumerate(dx) if v is not None), None)
    if first is None or first + period > len(candles):
        return out

    seed = [v for v in dx[first:first + period] if v is not None]
    if len(seed) < period:
        return out
    value = sum(seed) / period
    out[first + period - 1] = value
    for i in range(first + period, len(candles)):
        if dx[i] is None:
            continue
        value = (value * (period - 1) + dx[i]) / period
        out[i] = value
    return out
