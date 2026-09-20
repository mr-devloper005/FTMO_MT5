"""The signal engine: candles in, signals out.

This module knows nothing about Yahoo, MetaTrader 5, tastytrade, credentials,
brokers or orders. It reads `Candle` objects and produces `Signal` objects. That
is the whole contract, and it is what lets the same strategy run over any
instrument from any provider.

Look-ahead bias
---------------
Bar `i`'s signal is computed from indicator values at index `i`, each of which
is derived only from candles `0..i`. The signal is stamped with bar `i`'s close
time. What the caller may do with it at bar `i+1` is the backtest engine's
concern, not this module's — but nothing here can see past `i`.
"""

__all__ = ["Signal", "SignalEngine", "StrategyConfig"]

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import structlog

from ftmo_mt5.marketdata.base import Candle, CandleSeries
from ftmo_mt5.signals.strategies import Strategy, get_strategy

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Signal:
    """One instruction produced by a strategy at one bar.

    Attributes:
        symbol: Instrument the signal is about.
        timeframe: Bar size it was computed on.
        timestamp: Close time of the bar that produced it, UTC epoch seconds.
        index: Position of that bar in the series, so a backtest can find the
            next bar without searching.
        signal: "BUY", "SELL", "EXIT" or "FLAT".
        price: Close of the producing bar. Not an execution price.
        strategy: Name of the strategy that produced it.
        reasons: Human-readable conditions that were satisfied.
        indicators: Indicator values at that bar, for inspection.
        stop_price: A stop the strategy chose for this setup, when it has an
            opinion about one. Carried through from the verdict so the backtest
            can honour it; None means the run's own risk settings decide.
    """

    symbol: str
    timeframe: str
    timestamp: int
    index: int
    signal: str
    price: float
    strategy: str
    reasons: list[str] = field(default_factory=list)
    indicators: dict[str, Any] = field(default_factory=dict)
    stop_price: Optional[float] = None

    def as_dict(self) -> dict:
        from datetime import datetime, timezone
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": datetime.fromtimestamp(
                self.timestamp, tz=timezone.utc).isoformat(),
            "epoch": self.timestamp,
            "signal": self.signal,
            "price": round(self.price, 6),
            "strategy": self.strategy,
            "reasons": list(self.reasons),
            "indicators": {k: (round(v, 4) if isinstance(v, float) else v)
                           for k, v in self.indicators.items()},
        }


@dataclass(frozen=True)
class StrategyConfig:
    """Which strategy to run and with what parameters.

    Attributes:
        name: Key in the strategy registry, e.g. "EMA_RSI_PATTERN".
        parameters: Strategy-specific overrides. Unknown keys are ignored by
            the strategy rather than rejected, so the panel can send a superset.
    """

    name: str = "MTF_GREEN_STACK"
    parameters: dict[str, Any] = field(default_factory=dict)


class SignalEngine:
    """Runs one strategy across one candle series."""

    def __init__(self, config: Optional[StrategyConfig] = None) -> None:
        self.config = config or StrategyConfig()
        self.strategy: Strategy = get_strategy(self.config.name)

    def describe(self) -> dict:
        return self.strategy.describe(self.config.parameters)

    def run(self, series: CandleSeries) -> list[Signal]:
        """Every non-flat signal in the series, in chronological order."""
        signals = [s for s in self.run_all(series) if s.signal != "FLAT"]
        logger.info("signals_generated", symbol=series.symbol,
                    timeframe=series.timeframe, strategy=self.config.name,
                    bars=len(series.candles), signals=len(signals))
        return signals

    def run_all(self, series: CandleSeries) -> list[Signal]:
        """One entry per bar, including FLAT — used by the backtest engine."""
        candles: Sequence[Candle] = series.candles
        if not candles:
            return []

        state = self.strategy.prepare(candles, self.config.parameters)

        out: list[Signal] = []
        for i in range(len(candles)):
            verdict = self.strategy.evaluate(candles, i, state)
            out.append(Signal(
                symbol=series.symbol,
                timeframe=series.timeframe,
                timestamp=candles[i].timestamp,
                index=i,
                signal=verdict.signal,
                price=candles[i].close,
                strategy=self.strategy.name,
                reasons=verdict.reasons,
                indicators=verdict.indicators,
                stop_price=getattr(verdict, "stop_price", None),
            ))
        return out
