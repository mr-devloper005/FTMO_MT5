"""EMA-crossover trend strategy with an ATR volatility filter.

Produces a LONG or SHORT direction on the bar where a fast EMA crosses a slow
EMA, provided the cross is wide enough relative to current ATR to be worth
trading. Everything is computed on *closed* candles only — the still-forming
last bar is dropped so a signal never repaints.

The output dict matches what SignalAgent.process() consumes:
    {symbol, direction, confidence, meta_label_prob, timestamp}
"""

__all__ = [
    "EMACrossoverStrategy",
]

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence

import structlog

from ftmo_mt5.core.models import SignalDirection

logger = structlog.get_logger(__name__)


def _ema(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """Exponential moving average, seeded with a simple average.

    Returns a list the same length as `values`; entries before `period` are
    the running simple average so the series has no None holes.
    """
    if not values:
        return []

    alpha = Decimal(2) / Decimal(period + 1)
    out: list[Decimal] = []
    running = Decimal(0)

    for i, v in enumerate(values):
        if i < period:
            running += v
            out.append(running / Decimal(i + 1))
        else:
            out.append(alpha * v + (Decimal(1) - alpha) * out[-1])

    return out


def _atr(bars: Sequence[dict], period: int = 14) -> Optional[Decimal]:
    """Average true range over the last `period` bars."""
    if len(bars) < period + 1:
        return None

    trs: list[Decimal] = []
    for prev, cur in zip(bars[-(period + 1):-1], bars[-period:]):
        high, low, prev_close = cur["high"], cur["low"], prev["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    return sum(trs) / Decimal(len(trs))


class EMACrossoverStrategy:
    """Fast/slow EMA crossover, gated on ATR-relative separation.

    Attributes:
        fast_period: Span of the fast EMA.
        slow_period: Span of the slow EMA.
        atr_period: Lookback for the ATR filter.
        min_atr_fraction: How fast the EMA gap must be opening at the cross,
            as a fraction of ATR per bar, before the signal is taken
            seriously. Slower crosses are discarded as noise.
    """

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        atr_period: int = 14,
        min_atr_fraction: Decimal = Decimal("0.10"),
    ) -> None:
        if fast_period >= slow_period:
            raise ValueError("fast_period must be shorter than slow_period")

        self.fast_period = fast_period
        self.slow_period = slow_period
        self.atr_period = atr_period
        self.min_atr_fraction = min_atr_fraction
        self.name = "ema_crossover"

    @property
    def min_bars(self) -> int:
        """Candles needed before the strategy can produce anything."""
        return max(self.slow_period, self.atr_period) + 2

    def evaluate(self, symbol: str, bars: Sequence[dict]) -> Optional[dict]:
        """Look for a fresh crossover on the most recently closed bar.

        Args:
            symbol: Symbol the candles belong to.
            bars: Candles oldest-first, as returned by the gateway. The final
                element is assumed to be the still-forming bar and is ignored.

        Returns:
            Signal dict for SignalAgent, or None when there is no fresh cross.
        """
        if len(bars) < self.min_bars:
            logger.debug("strategy_insufficient_bars", symbol=symbol, bars=len(bars))
            return None

        closed = list(bars[:-1])  # drop the forming bar — no repainting
        closes = [b["close"] for b in closed]

        fast = _ema(closes, self.fast_period)
        slow = _ema(closes, self.slow_period)

        # A cross means the sign of (fast - slow) flipped on the last bar.
        prev_delta = fast[-2] - slow[-2]
        curr_delta = fast[-1] - slow[-1]

        crossed_up = prev_delta <= 0 < curr_delta
        crossed_down = prev_delta >= 0 > curr_delta

        if not (crossed_up or crossed_down):
            return None

        atr = _atr(closed, self.atr_period)
        if atr is None or atr == 0:
            logger.debug("strategy_no_atr", symbol=symbol)
            return None

        # At a crossover the two EMAs are by definition almost touching, so the
        # gap itself says nothing. What separates a real trend change from noise
        # is how fast the gap is opening — measure that, in ATRs per bar.
        separation_rate = abs(curr_delta - prev_delta) / atr
        if separation_rate < self.min_atr_fraction:
            logger.debug(
                "strategy_cross_too_weak",
                symbol=symbol,
                separation_rate=str(round(separation_rate, 4)),
                threshold=str(self.min_atr_fraction),
            )
            return None

        direction = SignalDirection.LONG if crossed_up else SignalDirection.SHORT

        # Map the rate onto 0.50-0.95: a gap opening by a full ATR in one bar is
        # treated as maximally convincing, anything faster adds nothing.
        confidence = Decimal("0.50") + min(separation_rate, Decimal("1")) * Decimal("0.45")
        confidence = confidence.quantize(Decimal("0.01"))

        logger.info(
            "strategy_signal",
            symbol=symbol,
            strategy=self.name,
            direction=direction.value,
            confidence=str(confidence),
            separation_rate=str(round(separation_rate, 3)),
            atr=str(round(atr, 5)),
            close=str(closes[-1]),
        )

        return {
            "symbol": symbol,
            "direction": direction,
            "confidence": confidence,
            "meta_label_prob": confidence,
            "timestamp": datetime.now(timezone.utc),
            "atr": atr,
            "close": closes[-1],
        }
