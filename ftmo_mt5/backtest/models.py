"""Backtest configuration, trades and results.

Nothing here carries a default instrument, date or capital that would make one
particular run privileged. Every field the user can choose is a field on
`BacktestConfig`, and the engine reads all of them from there.
"""

__all__ = ["BacktestConfig", "Trade", "BacktestResult", "ExecutionModel"]

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Optional


class ExecutionModel(str, Enum):
    """Where a signal generated at a bar's close actually gets filled."""

    NEXT_OPEN = "next_open"      # open of the following bar — the honest default
    SAME_CLOSE = "same_close"    # close of the signalling bar; optimistic


class TradeMode(str, Enum):
    """What a signal actually buys or sells."""

    UNDERLYING = "underlying"    # the future or the share itself
    OPTIONS = "options"          # a modelled option on it — see ftmo_mt5.options


class OptionAction(str, Enum):
    """The position a signal opens in the options market.

    Named so the mapping stays explicit: a SELL signal that *sells a call* is a
    short-premium bet on the market not rising, which is a different strategy
    from a SELL signal that *buys a put*, even though both are bearish.
    """

    SELL_CALL = "sell_call"
    BUY_PUT = "buy_put"
    SELL_PUT = "sell_put"
    BUY_CALL = "buy_call"
    NONE = "none"                # ignore signals in this direction


@dataclass
class BacktestConfig:
    """Everything a run needs. All of it comes from the user.

    Attributes:
        symbol: Instrument to test, e.g. "/NQ", "AAPL", "SPY".
        timeframe: Canonical bar size, e.g. "5m", "1d".
        start, end: Inclusive date range to test over.
        data_source: Provider key — "yahoo", "mt5", "tasty".
        strategy: Strategy registry key.
        strategy_parameters: Overrides passed through to the strategy.
        initial_capital: Starting account value in the quote currency.
        position_size: Units per trade — contracts for futures, shares for
            equities. Interpreted through the instrument's metadata.
        commission: Cash charged per unit per side.
        slippage_ticks: Adverse price movement applied on entry and exit,
            expressed in the instrument's ticks so it means the same thing
            across asset classes.
        stop_loss_atr / take_profit_atr: Exit distances as ATR multiples;
            None disables that exit.
        stop_loss_pct / take_profit_pct: Alternative percentage exits.
        trailing_stop_atr / trailing_stop_pct: A stop that follows the best price
            reached since entry and never moves back. Given both, the ATR
            version wins, matching how the fixed stop resolves. It can only
            tighten the fixed stop, never loosen it.
        breakeven_at_atr: Once the position is this many ATR in profit, the stop
            moves to the entry price. Only ever tightens.
        trail_activate_atr: Hold the trailing stop back until the position is
            this many ATR in profit. Without it a trail starts at entry, which
            on a wide initial stop takes the trade out before it can develop.
        risk_per_trade_pct: Size each position so that the distance from entry
            to the initial stop is this percentage of current equity. When set,
            it replaces `position_size`.
        max_bars_in_trade: Close any position still open this many bars after
            entry, at the next open. None leaves holding time to the strategy
            and the stops.
        allow_long / allow_short: Which directions may be taken.
        asset_type: Optional override when the symbol shape is ambiguous.
        execution_model: How a close-based signal is filled.
    """

    symbol: str
    timeframe: str
    start: date
    end: date
    data_source: str = "yahoo"
    strategy: str = "MTF_GREEN_STACK"
    strategy_parameters: dict[str, Any] = field(default_factory=dict)

    initial_capital: float = 100_000.0
    position_size: float = 1.0
    commission: float = 0.0
    slippage_ticks: float = 0.0

    stop_loss_atr: Optional[float] = None
    take_profit_atr: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    trailing_stop_atr: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    breakeven_at_atr: Optional[float] = None
    trail_activate_atr: Optional[float] = None
    risk_per_trade_pct: Optional[float] = None
    # A hard cap on how long one position may stay open, in bars. None disables
    # it. Counted from the entry bar, so 10 means the trade is closed at the
    # open of the tenth bar after entry if nothing else has closed it first.
    max_bars_in_trade: Optional[int] = None

    allow_long: bool = True
    allow_short: bool = True
    asset_type: Optional[str] = None
    execution_model: ExecutionModel = ExecutionModel.NEXT_OPEN

    # --- options mode -------------------------------------------------
    # Only consulted when trade_mode is OPTIONS. Premiums are modelled, not
    # historical; see ftmo_mt5.options for what that costs in accuracy.
    trade_mode: TradeMode = TradeMode.UNDERLYING
    # Long premium by default: a bullish signal buys a call, a bearish one buys
    # a put. Measured ATM implied volatility on /NQ was ~12.8% against realized
    # of roughly 20% — with volatility priced that cheaply, paying for it is the
    # side of the trade the data supports. Both remain configurable.
    option_on_sell: OptionAction = OptionAction.BUY_PUT
    option_on_buy: OptionAction = OptionAction.BUY_CALL
    option_expiry_rule: str = "mwf"
    option_min_days_to_expiry: float = 0.0
    # Volatility: fixed when set, otherwise estimated from realized vol over
    # `option_vol_lookback` bars and scaled by `option_vol_premium` to stand in
    # for the variance risk premium that makes real IV exceed realized.
    option_implied_vol: Optional[float] = None
    option_vol_lookback: int = 20
    option_vol_premium: float = 1.0
    option_risk_free_rate: float = 0.0
    # Exits, as fractions of the premium received or paid.
    option_take_profit_pct: Optional[float] = None
    option_stop_loss_pct: Optional[float] = None
    option_hold_to_expiry: bool = False
    option_close_on_opposite: bool = True
    # Refuse to open a contract worth less than this. Near expiry the model
    # prices a far-from-the-money option at almost nothing, and "selling" it
    # records a position with no credit and unlimited risk — which no real
    # market would have filled.
    option_min_premium: float = 0.05


@dataclass
class Trade:
    """One completed round trip."""

    direction: str                 # LONG | SHORT
    quantity: float
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    exit_reason: str               # signal | stop_loss | take_profit | end_of_data
    gross_pnl: float
    commission: float
    net_pnl: float
    bars_held: int
    entry_reasons: list[str] = field(default_factory=list)
    # Which signal opened it, and which strategy produced that signal — so a
    # row in the trade table names its cause instead of just "signal".
    entry_signal: str = ""
    strategy: str = ""

    # Set only in options mode. `entry_price`/`exit_price` then hold the option
    # premium, and these record the contract it belonged to.
    contract: str = ""
    option_type: str = ""
    strike: Optional[float] = None
    expiry: Optional[int] = None
    entry_underlying: Optional[float] = None
    exit_underlying: Optional[float] = None
    entry_vol: Optional[float] = None

    def as_dict(self) -> dict:
        stamp = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
        out = {
            "direction": self.direction,
            "quantity": self.quantity,
            "entryTime": stamp(self.entry_time),
            "entryPrice": round(self.entry_price, 6),
            "exitTime": stamp(self.exit_time),
            "exitPrice": round(self.exit_price, 6),
            "exitReason": self.exit_reason,
            "grossPnl": round(self.gross_pnl, 2),
            "commission": round(self.commission, 2),
            "netPnl": round(self.net_pnl, 2),
            "barsHeld": self.bars_held,
            "entryReasons": list(self.entry_reasons),
            "entrySignal": self.entry_signal,
            "strategy": self.strategy,
        }
        if self.contract:
            out.update({
                "contract": self.contract,
                "optionType": self.option_type,
                "strike": self.strike,
                "expiry": stamp(self.expiry) if self.expiry else None,
                "entryUnderlying": round(self.entry_underlying, 4) if self.entry_underlying else None,
                "exitUnderlying": round(self.exit_underlying, 4) if self.exit_underlying else None,
                "entryVol": round(self.entry_vol, 4) if self.entry_vol else None,
            })
        return out


@dataclass
class BacktestResult:
    """Trades, metrics and the equity curve for one run."""

    config: dict
    instrument: dict
    provider: dict
    bars: int
    first_bar: Optional[int]
    last_bar: Optional[int]

    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)
    signals: int = 0
    warnings: list[str] = field(default_factory=list)
    # Buying the same exposure on the first bar and holding it to the last.
    # Without it a P&L figure says nothing: a strategy that made 8% in a market
    # that rose 30% lost money relative to doing nothing.
    benchmark_curve: list[tuple[int, float]] = field(default_factory=list)
    # The price itself, so the panel can show where each trade actually
    # happened. An equity curve says how much was made; only this says where.
    price_series: list[tuple[int, float]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Performance
    # ------------------------------------------------------------------

    def metrics(self) -> dict:
        """Summary statistics over the completed trades."""
        wins = [t for t in self.trades if t.net_pnl > 0]
        losses = [t for t in self.trades if t.net_pnl < 0]

        gross_profit = sum(t.net_pnl for t in wins)
        gross_loss = sum(t.net_pnl for t in losses)          # negative
        net = gross_profit + gross_loss
        total = len(self.trades)

        start_equity = self.equity_curve[0][1] if self.equity_curve else 0.0

        return {
            "totalTrades": total,
            "winningTrades": len(wins),
            "losingTrades": len(losses),
            "winRate": round(len(wins) / total * 100, 2) if total else 0.0,
            "grossProfit": round(gross_profit, 2),
            "grossLoss": round(gross_loss, 2),
            "netPnl": round(net, 2),
            "returnPct": round(net / start_equity * 100, 2) if start_equity else 0.0,
            "averageTrade": round(net / total, 2) if total else 0.0,
            "averageWinner": round(gross_profit / len(wins), 2) if wins else 0.0,
            "averageLoser": round(gross_loss / len(losses), 2) if losses else 0.0,
            "largestWinner": round(max((t.net_pnl for t in wins), default=0.0), 2),
            "largestLoser": round(min((t.net_pnl for t in losses), default=0.0), 2),
            # Undefined rather than infinite when nothing lost — reporting a
            # number here would read as a real, extremely good result.
            "profitFactor": (round(gross_profit / abs(gross_loss), 2)
                             if gross_loss else None),
            "maxDrawdown": round(self._max_drawdown(), 2),
            "maxDrawdownPct": round(self._max_drawdown_pct(), 2),
            "totalCommission": round(sum(t.commission for t in self.trades), 2),
            "finalEquity": round(self.equity_curve[-1][1], 2) if self.equity_curve else 0.0,
        }

    def _max_drawdown(self, curve=None) -> float:
        peak = float("-inf")
        worst = 0.0
        for _, equity in (self.equity_curve if curve is None else curve):
            peak = max(peak, equity)
            worst = min(worst, equity - peak)
        return abs(worst)

    def _max_drawdown_pct(self, curve=None) -> float:
        peak = float("-inf")
        worst = 0.0
        for _, equity in (self.equity_curve if curve is None else curve):
            peak = max(peak, equity)
            if peak > 0:
                worst = min(worst, (equity - peak) / peak * 100)
        return abs(worst)

    def benchmark(self) -> Optional[dict]:
        """Buy-and-hold over the same bars, same size, same costs.

        Returned alongside the strategy's own numbers so the comparison is
        unavoidable rather than something the reader has to work out.
        """
        if len(self.benchmark_curve) < 2:
            return None

        start = self.benchmark_curve[0][1]
        finish = self.benchmark_curve[-1][1]
        net = finish - start
        strategy_net = self.metrics()["netPnl"]

        return {
            "netPnl": round(net, 2),
            "returnPct": round(net / start * 100, 2) if start else 0.0,
            "finalEquity": round(finish, 2),
            "maxDrawdown": round(self._max_drawdown(self.benchmark_curve), 2),
            "maxDrawdownPct": round(self._max_drawdown_pct(self.benchmark_curve), 2),
            # Positive means the strategy beat simply holding.
            "outperformance": round(strategy_net - net, 2),
            "beatsBuyAndHold": strategy_net > net,
        }

    def as_dict(self, include_curve: bool = True) -> dict:
        stamp = lambda t: (datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
                           if t else None)
        out = {
            "config": self.config,
            "instrument": self.instrument,
            "provider": self.provider,
            "bars": self.bars,
            "firstBar": stamp(self.first_bar),
            "lastBar": stamp(self.last_bar),
            "signals": self.signals,
            "metrics": self.metrics(),
            "benchmark": self.benchmark(),
            "trades": [t.as_dict() for t in self.trades],
            "warnings": list(self.warnings),
        }
        if include_curve:
            out["equityCurve"] = [
                {"time": stamp(t), "epoch": t, "equity": round(e, 2)}
                for t, e in self.equity_curve
            ]
            out["benchmarkCurve"] = [
                {"time": stamp(t), "epoch": t, "equity": round(e, 2)}
                for t, e in self.benchmark_curve
            ]
            # Thinned for transport: a two-year hourly run is ~11,000 bars and
            # a chart a few hundred pixels wide cannot show more than a couple
            # of thousand. Trade markers are positioned by time, not by index,
            # so thinning the line never moves a marker.
            step = max(1, len(self.price_series) // 2500)
            out["priceSeries"] = [
                {"epoch": t, "price": round(p, 4)}
                for t, p in self.price_series[::step]
            ]
        return out
