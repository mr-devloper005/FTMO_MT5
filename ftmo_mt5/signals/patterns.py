"""Candlestick pattern detection.

Each detector answers one question about the bar at index `i`, using only that
bar and the ones before it. Detectors are registered in `PATTERNS` so a
strategy can name the ones it cares about and new patterns can be added without
touching the engine.

Pattern definitions vary between sources; the thresholds here are the common
ones and are exposed as arguments rather than buried as literals.
"""

__all__ = ["PATTERNS", "BULLISH", "BEARISH", "detect", "detect_all"]

from typing import Callable, Sequence

from ftmo_mt5.marketdata.base import Candle

Detector = Callable[[Sequence[Candle], int], bool]


def _body(c: Candle) -> float:
    return abs(c.close - c.open)


def _range(c: Candle) -> float:
    return max(c.high - c.low, 1e-12)          # never divide by a zero-range bar


def _upper_wick(c: Candle) -> float:
    return c.high - max(c.open, c.close)


def _lower_wick(c: Candle) -> float:
    return min(c.open, c.close) - c.low


def bullish_engulfing(candles: Sequence[Candle], i: int) -> bool:
    """A down bar fully covered by the next bar's up body."""
    if i < 1:
        return False
    prev, cur = candles[i - 1], candles[i]
    return (prev.close < prev.open
            and cur.close > cur.open
            and cur.close >= prev.open
            and cur.open <= prev.close
            and _body(cur) > _body(prev))


def bearish_engulfing(candles: Sequence[Candle], i: int) -> bool:
    """An up bar fully covered by the next bar's down body."""
    if i < 1:
        return False
    prev, cur = candles[i - 1], candles[i]
    return (prev.close > prev.open
            and cur.close < cur.open
            and cur.open >= prev.close
            and cur.close <= prev.open
            and _body(cur) > _body(prev))


def hammer(candles: Sequence[Candle], i: int, wick_ratio: float = 2.0) -> bool:
    """Small body at the top, long lower wick — rejection of lower prices.

    The opposing wick is judged against the long wick rather than against the
    body: on a real hammer the body is tiny, so requiring the upper wick to be
    smaller still would reject almost every genuine one.
    """
    c = candles[i]
    body = _body(c)
    if body <= 0 or body / _range(c) > 0.4:
        return False
    lower, upper = _lower_wick(c), _upper_wick(c)
    return lower >= wick_ratio * body and lower >= 2 * upper


def shooting_star(candles: Sequence[Candle], i: int, wick_ratio: float = 2.0) -> bool:
    """Small body at the bottom, long upper wick — rejection of higher prices."""
    c = candles[i]
    body = _body(c)
    if body <= 0 or body / _range(c) > 0.4:
        return False
    lower, upper = _lower_wick(c), _upper_wick(c)
    return upper >= wick_ratio * body and upper >= 2 * lower


def doji(candles: Sequence[Candle], i: int, max_body: float = 0.1) -> bool:
    """Open and close effectively equal — indecision, direction-neutral."""
    c = candles[i]
    return _body(c) / _range(c) <= max_body


PATTERNS: dict[str, Detector] = {
    "bullish_engulfing": bullish_engulfing,
    "bearish_engulfing": bearish_engulfing,
    "hammer": hammer,
    "shooting_star": shooting_star,
    "doji": doji,
}

# Which patterns argue which way. Doji belongs to neither on purpose.
BULLISH: tuple[str, ...] = ("bullish_engulfing", "hammer")
BEARISH: tuple[str, ...] = ("bearish_engulfing", "shooting_star")


def detect(name: str, candles: Sequence[Candle], i: int) -> bool:
    detector = PATTERNS.get(name)
    return bool(detector and detector(candles, i))


def detect_all(candles: Sequence[Candle], i: int,
               names: Sequence[str] = ()) -> list[str]:
    """Every named pattern present at bar `i` (all known patterns by default)."""
    wanted = names or tuple(PATTERNS)
    return [n for n in wanted if detect(n, candles, i)]
