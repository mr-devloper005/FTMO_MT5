"""The backtest engine.

Walks a candle series forward one bar at a time, asks the strategy what it
thinks using only the data available at that bar, and simulates the resulting
trades against the instrument's real contract specification.

No instrument, date, capital or timeframe is written into this module. All of
them arrive on `BacktestConfig`.

Avoiding look-ahead bias
------------------------
The rule this engine enforces is that a decision made from a bar's *close*
cannot be filled at that same bar's open or at any price inside it. Under the
default execution model:

    bar i closes -> strategy decides -> fill at bar i+1's OPEN

Stops and targets are the one place intrabar prices are used, and only for the
bar *after* entry onward — never the entry bar itself. Where a bar's high and
low would have hit both the stop and the target, the stop is taken: a backtest
that resolves that ambiguity in its own favour flatters every result.
"""

__all__ = ["run_backtest"]

from datetime import datetime, timezone
from typing import Optional

import structlog

from ftmo_mt5.backtest.models import (
    BacktestConfig,
    BacktestResult,
    ExecutionModel,
    OptionAction,
    Trade,
    TradeMode,
)
from ftmo_mt5.instruments import Instrument, resolve_instrument
from ftmo_mt5.marketdata import DataUnavailable, get_provider
from ftmo_mt5.marketdata.base import TIMEFRAME_SECONDS, CandleSeries
from ftmo_mt5.options import (
    ExpiryRule,
    atm_strike,
    black76_price,
    contract_label,
    daily_volatility_series,
    next_expiry,
    periods_per_year,
)
from ftmo_mt5.signals.engine import SignalEngine, StrategyConfig
from ftmo_mt5.signals import indicators as ind

logger = structlog.get_logger(__name__)


def _pnl(direction: str, entry: float, exit_price: float,
         quantity: float, instrument: Instrument) -> float:
    """Cash result of a round trip, using the instrument's own point value.

    A futures point is worth `contract_multiplier` dollars; a share's point is
    one dollar. Keeping that here is what lets the same engine price /NQ and
    AAPL without either knowing about the other.
    """
    move = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
    return move * quantity * instrument.point_value()


def _slip(price: float, direction: str, opening: bool,
          ticks: float, instrument: Instrument) -> float:
    """Apply slippage so it always works against the trade."""
    if ticks <= 0:
        return price
    offset = ticks * instrument.tick_size
    if direction == "LONG":
        return price + offset if opening else price - offset
    return price - offset if opening else price + offset


def _buy_and_hold(config: BacktestConfig, instrument: Instrument,
                  candles) -> list[tuple[int, float]]:
    """Equity from buying on the first bar and holding to the last.

    Deliberately charged the same way as the strategy — same position size,
    same slippage on both fills, one round trip of commission — so the two
    curves differ because of the decisions, not the accounting.

    In options mode the benchmark is still the underlying future: "what if I
    had just held it instead" is the question a reader is actually asking.
    """
    if len(candles) < 2:
        return []

    quantity = config.position_size
    entry = _slip(candles[0].open, "LONG", True, config.slippage_ticks, instrument)
    commission = config.commission * quantity * 2

    curve = [(candles[0].timestamp, config.initial_capital)]
    for candle in candles:
        value = _pnl("LONG", entry, candle.close, quantity, instrument)
        curve.append((candle.timestamp, config.initial_capital + value - commission))

    # Settle the final bar at a price a seller could actually have got.
    last = candles[-1]
    exit_price = _slip(last.close, "LONG", False, config.slippage_ticks, instrument)
    final = _pnl("LONG", entry, exit_price, quantity, instrument) - commission
    curve[-1] = (last.timestamp, config.initial_capital + final)
    return curve


def _trail_from(config: BacktestConfig, direction: str, best: float,
                atr_value: Optional[float]) -> Optional[float]:
    """Where a trailing stop sits, given the best price reached since entry.

    ATR wins over percent when both are given, matching how the fixed stop
    resolves. Returns None when no trailing stop is configured, or when an ATR
    trail was asked for and ATR has no value yet.
    """
    sign = 1 if direction == "LONG" else -1
    if config.trailing_stop_atr:
        if not atr_value:
            return None
        return best - sign * config.trailing_stop_atr * atr_value
    if config.trailing_stop_pct:
        return best * (1 - sign * config.trailing_stop_pct / 100.0)
    return None


def _exit_levels(config: BacktestConfig, direction: str, entry: float,
                 atr_value: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """Stop and target prices for a position, or None where not configured."""
    stop = target = None
    sign = 1.0 if direction == "LONG" else -1.0

    if config.stop_loss_atr and atr_value:
        stop = entry - sign * config.stop_loss_atr * atr_value
    elif config.stop_loss_pct:
        stop = entry * (1 - sign * config.stop_loss_pct / 100.0)

    if config.take_profit_atr and atr_value:
        target = entry + sign * config.take_profit_atr * atr_value
    elif config.take_profit_pct:
        target = entry * (1 + sign * config.take_profit_pct / 100.0)

    return stop, target


def run_backtest(config: BacktestConfig,
                 series: Optional[CandleSeries] = None) -> BacktestResult:
    """Run one backtest and return trades, metrics and the equity curve.

    Args:
        config: The user's full configuration.
        series: Pre-fetched candles. When omitted the configured data provider
            is asked for exactly the configured symbol, timeframe and range.

    Raises:
        DataUnavailable: The provider cannot serve the request. Never
            substituted with a different instrument, timeframe or range.
        ValueError: The strategy is unknown or the configuration is impossible.
    """
    if config.start >= config.end:
        raise DataUnavailable("Start date must be before end date.")
    if config.initial_capital <= 0:
        raise ValueError("Initial capital must be greater than zero.")
    if config.position_size <= 0:
        raise ValueError("Position size must be greater than zero.")
    if not (config.allow_long or config.allow_short):
        raise ValueError("Enable at least one of long or short.")

    instrument = resolve_instrument(config.symbol, config.asset_type)
    provider = get_provider(config.data_source)
    capabilities = provider.capabilities()

    if series is None:
        series = provider.get_history(
            config.symbol, config.timeframe, config.start, config.end
        )

    candles = series.candles
    warnings: list[str] = []

    if instrument.assumed:
        warnings.append(
            f"No contract specification for {instrument.symbol}; assuming "
            f"{instrument.asset_type.value} with a point value of "
            f"{instrument.point_value():g}. Confirm before trusting P&L."
        )
    resolved = provider.resolve_symbol(config.symbol)
    if resolved.upper() != config.symbol.strip().upper():
        warnings.append(
            f"{capabilities.label} serves {config.symbol} as '{resolved}'."
        )

    engine = SignalEngine(StrategyConfig(config.strategy, config.strategy_parameters))
    verdicts = engine.run_all(series)
    # ATR for risk management belongs to the run, not to the strategy. Reading it
    # out of the strategy's state was fine while every strategy happened to
    # compute one; a strategy that does not left ATR stops silently unset, which
    # reads as "no stop was hit" rather than "no stop existed". Prefer the
    # strategy's own series when it has one — the user may have configured its
    # period — and fall back to a standard ATR over the same candles.
    strategy_state = engine.strategy.prepare(candles, config.strategy_parameters)
    atr_series = strategy_state.get("atr") or ind.atr(candles, 14)

    # A strategy that cannot honour its configuration says so here. Without
    # this the run just reports zero signals, which looks identical to a rule
    # that legitimately never triggered.
    warnings.extend(strategy_state.get("problems") or [])

    # Entries and exits are counted apart. A strategy that closes by signal
    # emits an EXIT on every bar its condition is not met, which would otherwise
    # swamp the signal count and make the run look far busier than it is.
    signal_count = sum(1 for v in verdicts if v.signal in ("BUY", "SELL"))
    exit_signals = sum(1 for v in verdicts if v.signal == "EXIT")
    unprotected = 0                    # positions opened before ATR was available

    # A strategy with no exit signal — and this now includes the default one —
    # relies entirely on the stop and the target. With neither, a position runs
    # to the end of the data and the result says more about the sample than the
    # rule, so say so rather than let the equity curve imply otherwise.
    if not exit_signals and not any([config.stop_loss_atr, config.stop_loss_pct,
                                     config.take_profit_atr, config.take_profit_pct,
                                     config.trailing_stop_atr, config.trailing_stop_pct]):
        warnings.append(
            "No stop loss or take profit configured. Positions can only close on "
            "an opposite signal or at the end of the data."
        )

    if config.trade_mode is TradeMode.OPTIONS:
        if instrument.asset_type.value != "future":
            warnings.append(
                f"Options mode is modelled with Black-76, which prices options on "
                f"futures. {instrument.symbol} is {instrument.asset_type.value}; "
                f"treat the premiums as indicative only."
            )
        warnings.append(
            "Option premiums are MODELLED with Black-76 from the underlying's price "
            "and realized volatility — no historical option prices exist for this "
            "product in any connected data source. Real implied volatility is "
            "normally higher than realized, so a short-premium strategy would have "
            "collected more credit than shown here."
        )
        trades, curve = _walk_options(config, instrument, candles, verdicts, warnings)
        return _assemble(config, instrument, capabilities, resolved, series,
                         candles, trades, curve, signal_count, warnings)

    # ------------------------------------------------------------------
    # Walk forward
    # ------------------------------------------------------------------
    equity = config.initial_capital
    curve: list[tuple[int, float]] = [(candles[0].timestamp, equity)] if candles else []
    trades: list[Trade] = []

    position: Optional[dict] = None
    same_bar = config.execution_model == ExecutionModel.SAME_CLOSE
    quantity = config.position_size

    for i, candle in enumerate(candles):
        # --- manage an open position first, on this bar's range -----------
        # The entry bar counts too, but only when the fill was its open: the
        # whole of that bar happened after the position existed, so its low can
        # hit the stop. Skipping it while still letting its high ratchet a
        # trailing stop upward hands the position the bar's upside and none of
        # its risk — worth thousands per trade on short holds. A close fill is
        # different: that bar's range is already in the past when the position
        # opens, so it is not tested.
        first_bar_is_live = not same_bar
        if position is not None and (i > position["entry_index"]
                                     or (i == position["entry_index"] and first_bar_is_live)):
            # The trail is tested at the level it reached on *previous* bars,
            # and only ratcheted afterwards. Raising it on this bar's own high
            # and then testing it would let a bar that spiked and collapsed exit
            # at a price that was never really protected — the same optimism the
            # stop-before-target rule exists to avoid.
            fixed, trail = position["stop"], position["trail"]
            target = position["target"]
            long = position["direction"] == "LONG"

            # Staged stop management, using the profit as of the *previous*
            # close. Reading this bar's own extreme would let the same bar both
            # earn the promotion and be protected by it.
            atr_at_entry = position.get("entry_atr")
            if atr_at_entry:
                gain = ((position["mark"] - position["entry_price"]) if long
                        else (position["entry_price"] - position["mark"]))
                if (config.breakeven_at_atr
                        and gain >= config.breakeven_at_atr * atr_at_entry
                        and not position["at_breakeven"]):
                    entry_px = position["entry_price"]
                    fixed = entry_px if fixed is None else (
                        max(fixed, entry_px) if long else min(fixed, entry_px))
                    position["stop"] = fixed
                    position["at_breakeven"] = True
                if (config.trail_activate_atr is not None
                        and not position["trail_live"]
                        and gain >= config.trail_activate_atr * atr_at_entry):
                    position["trail_live"] = True
            if config.trail_activate_atr is not None and not position["trail_live"]:
                trail = None
            # Whichever stop is tighter is the one in force, and the trade is
            # labelled with the one that actually did the work.
            if trail is None:
                stop, stop_name = fixed, "stop_loss"
            elif fixed is None:
                stop, stop_name = trail, "trailing_stop"
            else:
                stop = max(fixed, trail) if long else min(fixed, trail)
                stop_name = "trailing_stop" if stop == trail and trail != fixed else "stop_loss"
            hit_price = hit_reason = None

            # A stop is a market order once touched, so it fills at the stop
            # only if the market was still there. When the bar opens beyond it —
            # an overnight gap — the fill is the open, which is worse. Assuming
            # the level regardless hands the position a price the market never
            # offered, and on daily bars that is not an edge case: the median
            # gap is about the size of a 0.25 ATR trail.
            def stop_fill(level: float) -> float:
                return min(level, candle.open) if long else max(level, candle.open)

            if long:
                # Stop first when both are inside the bar: the pessimistic read.
                if stop is not None and candle.low <= stop:
                    hit_price, hit_reason = stop_fill(stop), stop_name
                elif target is not None and candle.high >= target:
                    # The target is a limit: it fills at the limit price. A gap
                    # through it would fill better, and that bonus is not claimed.
                    hit_price, hit_reason = target, "take_profit"
            else:
                if stop is not None and candle.high >= stop:
                    hit_price, hit_reason = stop_fill(stop), stop_name
                elif target is not None and candle.low <= target:
                    hit_price, hit_reason = target, "take_profit"

            if hit_price is not None:
                trades.append(_close(position, hit_price, candle.timestamp,
                                     hit_reason, i, config, instrument))
                equity += trades[-1].net_pnl
                position = None

        # --- time-based exit ----------------------------------------------
        # Checked before the strategy is consulted: a position that has run out
        # of time is closed whatever the rule now thinks, and it fills at the
        # next open like every other signal-driven exit.
        if (position is not None and config.max_bars_in_trade
                and i - position["entry_index"] >= config.max_bars_in_trade):
            if same_bar:
                out_price, out_time, out_index = candle.close, candle.timestamp, i
            elif i + 1 < len(candles):
                nxt = candles[i + 1]
                out_price, out_time, out_index = nxt.open, nxt.timestamp, i + 1
            else:
                out_price = None
            if out_price is not None:
                trades.append(_close(position, out_price, out_time, "time_exit",
                                     out_index, config, instrument))
                equity += trades[-1].net_pnl
                position = None

        verdict = verdicts[i]

        # --- close on an exit signal --------------------------------------
        # EXIT means "be flat", which is not the same as SELL: SELL would open
        # a short. Neither allow_long nor allow_short gates it — refusing to
        # leave a position the strategy has given up on is never right.
        if verdict.signal == "EXIT" and position is not None:
            if same_bar:
                out_price, out_time, out_index = candle.close, candle.timestamp, i
            elif i + 1 < len(candles):
                nxt = candles[i + 1]
                out_price, out_time, out_index = nxt.open, nxt.timestamp, i + 1
            else:
                out_price = None                  # no bar left to fill against
            if out_price is not None:
                trades.append(_close(position, out_price, out_time, "exit_signal",
                                     out_index, config, instrument))
                equity += trades[-1].net_pnl
                position = None

        # --- act on the signal --------------------------------------------
        if verdict.signal in ("BUY", "SELL"):
            wanted = "LONG" if verdict.signal == "BUY" else "SHORT"
            allowed = config.allow_long if wanted == "LONG" else config.allow_short

            # Where the fill happens. A close-derived decision filled at the
            # same bar's close is optimistic but sometimes wanted; the default
            # waits for the next bar's open, which is genuinely reachable.
            if same_bar:
                fill_price, fill_time, fill_index = candle.close, candle.timestamp, i
            elif i + 1 < len(candles):
                nxt = candles[i + 1]
                fill_price, fill_time, fill_index = nxt.open, nxt.timestamp, i + 1
            else:
                fill_price = None                 # no bar left to fill against

            if fill_price is not None:
                # Reverse an opposing position before taking the new one.
                if position is not None and position["direction"] != wanted:
                    # Name the signal that ended it. "signal" alone leaves the
                    # reader unable to tell a reversal from anything else.
                    trades.append(_close(position, fill_price, fill_time,
                                         f"{verdict.signal.lower()}_signal",
                                         fill_index, config, instrument))
                    equity += trades[-1].net_pnl
                    position = None

                if position is None and allowed:
                    entry = _slip(fill_price, wanted, True,
                                  config.slippage_ticks, instrument)
                    size = quantity
                    atr_value = atr_series[i] if i < len(atr_series) else None
                    # A stop the strategy chose for this specific setup. It is
                    # not an alternative to the run's risk settings — both apply,
                    # and the tighter one is the one that can be hit.
                    chosen = verdict.stop_price
                    if atr_value is None and (config.stop_loss_atr or config.take_profit_atr
                                              or config.trailing_stop_atr):
                        # The run asked for ATR-sized risk and ATR does not exist
                        # yet. Entering anyway would open a position with no stop
                        # at all — worse than missing the trade, and invisible in
                        # the result. Skip it and say how many were skipped.
                        unprotected += 1
                        continue
                    stop, target = _exit_levels(config, wanted, entry, atr_value)
                    if chosen is not None:
                        valid = chosen < entry if wanted == "LONG" else chosen > entry
                        if valid:
                            stop = chosen if stop is None else (
                                max(stop, chosen) if wanted == "LONG" else min(stop, chosen))
                    # Size from the distance to the stop, so a wider stop buys
                    # fewer contracts and the money at risk stays constant. With
                    # no stop there is no distance to size from, and the
                    # configured quantity stands.
                    if config.risk_per_trade_pct and stop is not None:
                        per_unit = abs(entry - stop) * instrument.contract_multiplier
                        if per_unit > 0:
                            budget = equity * config.risk_per_trade_pct / 100.0
                            step = instrument.quantity_step or 1
                            raw = budget / per_unit
                            size = max(instrument.min_quantity or step,
                                       (raw // step) * step)
                    position = {
                        "direction": wanted,
                        "quantity": size,
                        "entry_price": entry,
                        "entry_time": fill_time,
                        "entry_index": fill_index,
                        "stop": stop,
                        "target": target,
                        "best": entry,       # best price seen since entry
                        "mark": entry,       # last close, for the staged stops
                        "entry_atr": atr_value,
                        "at_breakeven": False,
                        "trail_live": config.trail_activate_atr is None,
                        # Seeded from the entry price, not left empty until the
                        # first ratchet. An unseeded trail means the entry bar
                        # can raise the stop with its high while having nothing
                        # to be tested against — the bar's upside without its
                        # risk, which on short holds books exits the position
                        # never actually held.
                        "trail": _trail_from(config, wanted, entry, atr_value),
                        "reasons": list(verdict.reasons),
                        "signal": verdict.signal,
                    }

        # --- ratchet the trailing stop ------------------------------------
        # Runs after the exit test, so a level set by this bar can only protect
        # later bars. It never moves against the position: once raised, a trail
        # stays raised even if price falls back.
        if position is not None and i >= position["entry_index"]:
            long = position["direction"] == "LONG"
            best = (max(position["best"], candle.high) if long
                    else min(position["best"], candle.low))
            position["best"] = best
            level = _trail_from(config, position["direction"], best,
                                atr_series[i] if i < len(atr_series) else None)
            if level is not None:
                held = position["trail"]
                position["trail"] = level if held is None else (
                    max(held, level) if long else min(held, level))

        if position is not None:
            position["mark"] = candle.close

        # Mark to market so the drawdown reflects open risk, not just closed P&L.
        floating = 0.0
        if position is not None:
            floating = _pnl(position["direction"], position["entry_price"],
                            candle.close, position["quantity"], instrument)
        curve.append((candle.timestamp, equity + floating))

    if unprotected:
        warnings.append(
            f"{unprotected} entry signal(s) skipped: ATR had no value yet, so the "
            f"ATR stop and target this run asked for could not be placed."
        )

    # Close whatever is still open at the last price we actually have.
    if position is not None and candles:
        last = candles[-1]
        trades.append(_close(position, last.close, last.timestamp,
                             "end_of_data", len(candles) - 1, config, instrument))
        equity += trades[-1].net_pnl
        curve.append((last.timestamp, equity))

    return _assemble(config, instrument, capabilities, resolved, series,
                     candles, trades, curve, signal_count, warnings)


def _assemble(config, instrument, capabilities, resolved, series, candles,
              trades, curve, signal_count, warnings) -> BacktestResult:
    """Package a completed walk into the result the API returns."""
    logger.info("backtest_complete", symbol=config.symbol, timeframe=config.timeframe,
                provider=config.data_source, mode=config.trade_mode.value,
                bars=len(candles), signals=signal_count, trades=len(trades))

    return BacktestResult(
        config=_config_dict(config),
        instrument={
            "symbol": instrument.symbol,
            "root": instrument.root,
            "assetType": instrument.asset_type.value,
            "exchange": instrument.exchange,
            "currency": instrument.currency,
            "tickSize": instrument.tick_size,
            "tickValue": instrument.tick_value,
            "contractMultiplier": instrument.contract_multiplier,
            "optionStrikeIncrement": instrument.option_strike_increment,
            "sessionTimezone": instrument.session_timezone,
            "assumed": instrument.assumed,
        },
        provider={
            "key": capabilities.key,
            "label": capabilities.label,
            "resolvedSymbol": resolved,
            "seriesTimezone": series.timezone,
            "note": capabilities.note,
        },
        bars=len(candles),
        first_bar=candles[0].timestamp if candles else None,
        last_bar=candles[-1].timestamp if candles else None,
        trades=trades,
        equity_curve=curve,
        benchmark_curve=_buy_and_hold(config, instrument, candles),
        price_series=[(c.timestamp, c.close) for c in candles],
        signals=signal_count,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Options mode
# ---------------------------------------------------------------------------

_ACTION_TYPE = {
    OptionAction.SELL_CALL: ("call", "SHORT"),
    OptionAction.BUY_CALL: ("call", "LONG"),
    OptionAction.SELL_PUT: ("put", "SHORT"),
    OptionAction.BUY_PUT: ("put", "LONG"),
}


def _walk_options(config: BacktestConfig, instrument: Instrument, candles,
                  verdicts, warnings: list[str]) -> tuple[list[Trade], list]:
    """Walk the series trading modelled options instead of the underlying.

    A signal opens one option position: by default a SELL signal sells an
    at-the-money call and a BUY signal sells an at-the-money put, both on the
    nearest expiry. Both mappings are configurable, including to the long side.

    Premiums come from Black-76 evaluated on each bar with the time remaining
    at that bar, so a short position's profit accrues as decay exactly the way
    it would in the market — subject to the model's limits, which are recorded
    in the result's warnings.
    """
    trades: list[Trade] = []
    curve: list[tuple[int, float]] = []
    if not candles:
        return trades, curve

    try:
        rule = ExpiryRule(config.option_expiry_rule)
    except ValueError:
        raise ValueError(
            f"Unknown expiry rule '{config.option_expiry_rule}'. "
            f"Use one of: {', '.join(r.value for r in ExpiryRule)}."
        )

    bar_seconds = TIMEFRAME_SECONDS.get(config.timeframe, 300)
    per_year = periods_per_year(bar_seconds, instrument)
    multiplier = instrument.point_value()
    quantity = config.position_size
    same_bar = config.execution_model == ExecutionModel.SAME_CLOSE

    equity = config.initial_capital
    curve.append((candles[0].timestamp, equity))
    position: Optional[dict] = None
    unpriced = 0
    worthless = 0

    # Estimated once for the whole series: each entry uses only the days up to
    # and including that bar, so this is precomputation, not look-ahead.
    vol_series = daily_volatility_series(
        candles,
        lookback_days=config.option_vol_lookback,
        trading_days_per_year=instrument.trading_days_per_year,
        fallback_periods_per_year=per_year,
    )

    def volatility_at(i: int) -> float:
        """Volatility to price with at bar i, from config or from the market."""
        if config.option_implied_vol:
            return config.option_implied_vol
        return vol_series[i] * config.option_vol_premium

    def premium(pos: dict, underlying: float, now: int) -> float:
        """Current value of the contract, intrinsic once it has expired."""
        years = max(0.0, (pos["expiry"] - now) / (365.25 * 86400))
        return black76_price(pos["option_type"], underlying, pos["strike"],
                             years, pos["vol"], config.option_risk_free_rate)

    def option_pnl(pos: dict, exit_premium: float) -> float:
        """Cash result. Short profits when the premium falls; long, when it rises."""
        move = ((pos["entry_premium"] - exit_premium) if pos["side"] == "SHORT"
                else (exit_premium - pos["entry_premium"]))
        return move * pos["quantity"] * multiplier

    def close_out(pos: dict, exit_premium: float, when: int, reason: str,
                  index: int, underlying: float) -> None:
        nonlocal equity, position
        gross = option_pnl(pos, exit_premium)
        commission = config.commission * pos["quantity"] * 2
        trades.append(Trade(
            direction=f"{pos['side']}_{pos['option_type'].upper()}",
            quantity=pos["quantity"],
            entry_time=pos["entry_time"], entry_price=pos["entry_premium"],
            exit_time=when, exit_price=exit_premium, exit_reason=reason,
            gross_pnl=gross, commission=commission, net_pnl=gross - commission,
            bars_held=max(0, index - pos["entry_index"]),
            entry_reasons=pos["reasons"], entry_signal=pos.get("signal", ""),
            strategy=config.strategy, contract=pos["contract"],
            option_type=pos["option_type"], strike=pos["strike"],
            expiry=pos["expiry"], entry_underlying=pos["entry_underlying"],
            exit_underlying=underlying, entry_vol=pos["vol"],
        ))
        equity += trades[-1].net_pnl
        position = None

    for i, candle in enumerate(candles):
        # --- manage the open contract -------------------------------------
        if position is not None and i > position["entry_index"]:
            value = premium(position, candle.close, candle.timestamp)

            if candle.timestamp >= position["expiry"]:
                # Settled: worth exactly its intrinsic value, nothing more.
                close_out(position, value, candle.timestamp, "expired", i, candle.close)
            elif not config.option_hold_to_expiry:
                credit = position["entry_premium"]
                gain = ((credit - value) / credit if position["side"] == "SHORT"
                        else (value - credit) / credit) if credit > 0 else 0.0
                if config.option_take_profit_pct and gain >= config.option_take_profit_pct / 100:
                    close_out(position, value, candle.timestamp, "take_profit", i, candle.close)
                elif config.option_stop_loss_pct and gain <= -config.option_stop_loss_pct / 100:
                    close_out(position, value, candle.timestamp, "stop_loss", i, candle.close)

        verdict = verdicts[i]

        # An exit signal closes the contract at what it is worth now, the same
        # as any other early close.
        if verdict.signal == "EXIT" and position is not None and i > position["entry_index"]:
            close_out(position, premium(position, candle.close, candle.timestamp),
                      candle.timestamp, "exit_signal", i, candle.close)

        # --- act on a signal ----------------------------------------------
        if verdict.signal in ("BUY", "SELL"):
            action = (config.option_on_buy if verdict.signal == "BUY"
                      else config.option_on_sell)

            if action is not OptionAction.NONE:
                if same_bar:
                    fill, when, index = candle.close, candle.timestamp, i
                elif i + 1 < len(candles):
                    nxt = candles[i + 1]
                    fill, when, index = nxt.open, nxt.timestamp, i + 1
                else:
                    fill = None

                if fill is not None:
                    option_type, side = _ACTION_TYPE[action]

                    if (position is not None and config.option_close_on_opposite
                            and position["option_type"] != option_type):
                        close_out(position, premium(position, fill, when),
                                  when, f"{verdict.signal.lower()}_signal",
                                  index, fill)

                    if position is None:
                        vol = volatility_at(i)
                        if vol <= 0:
                            unpriced += 1          # not enough history to price yet
                        else:
                            strike = atm_strike(fill, instrument)
                            expiry = next_expiry(
                                datetime.fromtimestamp(when, tz=timezone.utc),
                                rule, config.option_min_days_to_expiry)
                            expiry_ts = int(expiry.timestamp())
                            years = max(0.0, (expiry_ts - when) / (365.25 * 86400))
                            entry_premium = black76_price(
                                option_type, fill, strike, years, vol,
                                config.option_risk_free_rate)
                            # Slippage widens against the trade: a seller
                            # receives less, a buyer pays more.
                            edge = config.slippage_ticks * instrument.tick_size
                            entry_premium = max(
                                0.0, entry_premium - edge if side == "SHORT"
                                else entry_premium + edge)

                        if vol > 0 and entry_premium <= config.option_min_premium:
                            # Effectively worthless — usually a signal landing
                            # minutes before settlement. Taking it would book a
                            # short with no credit and open-ended risk.
                            worthless += 1
                            vol = 0.0            # fall through without opening

                        if vol > 0:
                            position = {
                                "side": side, "option_type": option_type,
                                "strike": strike, "expiry": expiry_ts,
                                "vol": vol, "quantity": quantity,
                                "entry_premium": entry_premium,
                                "entry_time": when, "entry_index": index,
                                "entry_underlying": fill,
                                "reasons": list(verdict.reasons),
                                "signal": verdict.signal,
                                "contract": contract_label(
                                    instrument, option_type, strike, expiry),
                            }

        floating = 0.0
        if position is not None:
            floating = option_pnl(position, premium(position, candle.close,
                                                    candle.timestamp))
        curve.append((candle.timestamp, equity + floating))

    if position is not None:
        last = candles[-1]
        close_out(position, premium(position, last.close, last.timestamp),
                  last.timestamp, "end_of_data", len(candles) - 1, last.close)
        curve.append((last.timestamp, equity))

    if unpriced:
        warnings.append(
            f"{unpriced} signal(s) were skipped because there was not yet enough "
            f"history to estimate volatility (needs {config.option_vol_lookback} days)."
        )
    if worthless:
        warnings.append(
            f"{worthless} signal(s) were skipped because the nearest contract was "
            f"worth {config.option_min_premium:g} or less — usually a signal arriving "
            f"just before settlement. Raise 'min days to expiry' to avoid them."
        )

    return trades, curve


def _close(position: dict, price: float, when: int, reason: str,
           index: int, config: BacktestConfig, instrument: Instrument) -> Trade:
    """Turn an open position into a completed Trade."""
    exit_price = _slip(price, position["direction"], False,
                       config.slippage_ticks, instrument)
    gross = _pnl(position["direction"], position["entry_price"], exit_price,
                 position["quantity"], instrument)
    commission = config.commission * position["quantity"] * 2      # both sides

    return Trade(
        direction=position["direction"],
        quantity=position["quantity"],
        entry_time=position["entry_time"],
        entry_price=position["entry_price"],
        exit_time=when,
        exit_price=exit_price,
        exit_reason=reason,
        gross_pnl=gross,
        commission=commission,
        net_pnl=gross - commission,
        bars_held=max(0, index - position["entry_index"]),
        entry_reasons=position["reasons"],
        entry_signal=position.get("signal", ""),
        strategy=config.strategy,
    )


def _config_dict(config: BacktestConfig) -> dict:
    return {
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "startDate": config.start.isoformat(),
        "endDate": config.end.isoformat(),
        "dataSource": config.data_source,
        "strategy": config.strategy,
        "strategyParameters": config.strategy_parameters,
        "initialCapital": config.initial_capital,
        "positionSize": config.position_size,
        "commission": config.commission,
        "slippageTicks": config.slippage_ticks,
        "stopLossAtr": config.stop_loss_atr,
        "takeProfitAtr": config.take_profit_atr,
        "trailingStopAtr": config.trailing_stop_atr,
        "trailingStopPct": config.trailing_stop_pct,
        "stopLossPct": config.stop_loss_pct,
        "takeProfitPct": config.take_profit_pct,
        "allowLong": config.allow_long,
        "allowShort": config.allow_short,
        "executionModel": config.execution_model.value,
        "tradeMode": config.trade_mode.value,
        "optionOnSell": config.option_on_sell.value,
        "optionOnBuy": config.option_on_buy.value,
        "optionExpiryRule": config.option_expiry_rule,
        "optionMinDaysToExpiry": config.option_min_days_to_expiry,
        "optionImpliedVol": config.option_implied_vol,
        "optionVolLookback": config.option_vol_lookback,
        "optionVolPremium": config.option_vol_premium,
        "optionRiskFreeRate": config.option_risk_free_rate,
        "optionTakeProfitPct": config.option_take_profit_pct,
        "optionStopLossPct": config.option_stop_loss_pct,
        "optionHoldToExpiry": config.option_hold_to_expiry,
        "optionMinPremium": config.option_min_premium,
    }
