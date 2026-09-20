"""Tests for the signal and backtest engines.

The look-ahead tests matter most: a backtest that peeks at the future produces
excellent results and no useful information, and the failure is invisible in
the output. Those tests construct series where peeking would change the answer.
"""

from datetime import date, timedelta

import pytest

from ftmo_mt5.backtest import BacktestConfig, ExecutionModel, run_backtest
from ftmo_mt5.instruments import AssetType, resolve_instrument
from ftmo_mt5.marketdata.base import Candle, CandleSeries, DataUnavailable
from ftmo_mt5.marketdata.yahoo import YahooHistoricalDataProvider
from ftmo_mt5.signals import indicators as ind
from ftmo_mt5.signals import patterns as pat
from ftmo_mt5.signals.engine import SignalEngine, StrategyConfig

START = 1_700_000_000


def bar(i, o, h, l, c, v=100.0):
    return Candle(timestamp=START + i * 300, open=o, high=h, low=l, close=c, volume=v)


def series(candles, symbol="/NQ", timeframe="5m"):
    return CandleSeries(symbol=symbol, timeframe=timeframe,
                        candles=candles, provider="test")


def rising(n=120, start=100.0, step=1.0):
    """A clean uptrend where every bar closes above its open."""
    out = []
    for i in range(n):
        o = start + i * step
        out.append(bar(i, o, o + step * 0.8, o - step * 0.2, o + step * 0.6))
    return out


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

class TestIndicators:
    def test_sma_matches_hand_calculation(self):
        assert ind.sma([1, 2, 3, 4, 5], 3)[2:] == [2.0, 3.0, 4.0]

    def test_series_are_index_aligned_with_none_prefix(self):
        values = list(range(1, 31))
        for out in (ind.ema(values, 10), ind.rsi(values, 14), ind.sma(values, 10)):
            assert len(out) == len(values)
            assert out[0] is None

    def test_ema_reacts_faster_than_sma(self):
        # Compared three bars after a step change, while both are still
        # catching up. Ten bars later the SMA has fully absorbed the step and
        # the EMA still carries a tail, so the comparison inverts — which is
        # the behaviour of the two averages, not a fault.
        values = [10.0] * 20 + [20.0] * 3
        assert ind.ema(values, 10)[-1] > ind.sma(values, 10)[-1]

    def test_rsi_is_100_when_nothing_falls(self):
        assert ind.rsi([float(i) for i in range(1, 40)], 14)[-1] == 100.0

    def test_rsi_stays_within_bounds(self):
        import random
        random.seed(7)
        values = [100 + random.uniform(-5, 5) for _ in range(200)]
        for v in ind.rsi(values, 14):
            if v is not None:
                assert 0.0 <= v <= 100.0

    def test_atr_is_positive_and_aligned(self):
        out = ind.atr(rising(60), 14)
        assert len(out) == 60
        assert out[-1] is not None and out[-1] > 0

    def test_indicators_survive_series_shorter_than_their_period(self):
        assert ind.ema([1.0, 2.0], 50) == [None, None]
        assert ind.rsi([1.0, 2.0], 14) == [None, None]


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

class TestPatterns:
    def test_bullish_engulfing(self):
        candles = [bar(0, 105, 106, 99, 100), bar(1, 99, 108, 98, 107)]
        assert pat.bullish_engulfing(candles, 1)
        assert not pat.bearish_engulfing(candles, 1)

    def test_bearish_engulfing(self):
        candles = [bar(0, 100, 106, 99, 105), bar(1, 106, 107, 98, 99)]
        assert pat.bearish_engulfing(candles, 1)

    def test_hammer_and_shooting_star_are_mirror_images(self):
        hammer = [bar(0, 100, 100.6, 94, 100.2)]
        star = [bar(0, 100, 106, 99.4, 99.8)]
        assert pat.hammer(hammer, 0)
        assert pat.shooting_star(star, 0)
        assert not pat.shooting_star(hammer, 0)

    def test_doji_has_a_negligible_body(self):
        assert pat.doji([bar(0, 100, 104, 96, 100.05)], 0)
        assert not pat.doji([bar(0, 100, 104, 96, 103)], 0)

    def test_first_bar_cannot_form_a_two_bar_pattern(self):
        assert not pat.bullish_engulfing([bar(0, 100, 101, 99, 100.5)], 0)

    def test_zero_range_bar_does_not_divide_by_zero(self):
        pat.detect_all([bar(0, 100, 100, 100, 100)], 0)


# ---------------------------------------------------------------------------
# Instrument metadata
# ---------------------------------------------------------------------------

class TestInstruments:
    def test_futures_carry_their_contract_multiplier(self):
        nq = resolve_instrument("/NQ")
        assert nq.asset_type is AssetType.FUTURE
        assert nq.contract_multiplier == 20.0
        assert nq.tick_size == 0.25

    def test_dated_contract_resolves_to_its_root(self):
        assert resolve_instrument("/NQU6").root == "NQ"
        assert resolve_instrument("/NQU6").contract_multiplier == 20.0

    def test_micro_and_full_size_differ(self):
        assert (resolve_instrument("/MNQ").contract_multiplier
                < resolve_instrument("/NQ").contract_multiplier)

    def test_equity_is_one_dollar_per_point(self):
        aapl = resolve_instrument("AAPL")
        assert aapl.asset_type is AssetType.EQUITY
        assert aapl.contract_multiplier == 1.0
        assert not aapl.assumed          # equity defaults are correct, not guessed

    def test_known_etf_is_labelled_as_one(self):
        assert resolve_instrument("SPY").asset_type is AssetType.ETF

    def test_unknown_future_is_flagged_rather_than_invented(self):
        assert resolve_instrument("/ZZZ").assumed

    def test_declared_type_overrides_the_symbol_shape(self):
        assert resolve_instrument("AAPL", "future").asset_type is AssetType.FUTURE


# ---------------------------------------------------------------------------
# Yahoo symbol mapping — no network
# ---------------------------------------------------------------------------

class TestYahooProvider:
    provider = YahooHistoricalDataProvider()

    def test_futures_map_to_continuous_contracts(self):
        assert self.provider.resolve_symbol("/NQ") == "NQ=F"
        assert self.provider.resolve_symbol("/NQU6") == "NQ=F"
        assert self.provider.resolve_symbol("/ES") == "ES=F"

    def test_equities_pass_through(self):
        assert self.provider.resolve_symbol("AAPL") == "AAPL"

    def test_unsupported_timeframe_is_refused(self):
        with pytest.raises(DataUnavailable, match="4h"):
            self.provider.validate("/NQ", "4h", date(2026, 1, 1), date(2026, 2, 1))

    def test_intraday_beyond_the_window_is_refused(self):
        with pytest.raises(DataUnavailable, match="last 60 days"):
            self.provider.validate("/NQ", "5m", date(2020, 1, 1), date.today())

    def test_reversed_dates_are_refused(self):
        with pytest.raises(DataUnavailable, match="before end"):
            self.provider.validate("AAPL", "1d", date(2026, 2, 1), date(2026, 1, 1))

    def test_daily_history_has_no_short_window(self):
        self.provider.validate("AAPL", "1d", date(2015, 1, 1), date(2026, 1, 1))


# ---------------------------------------------------------------------------
# Signal engine
# ---------------------------------------------------------------------------

class TestSignalEngine:
    def test_early_bars_are_flat_not_defaulted_to_buy(self):
        # An indicator strategy has nothing to say until its windows fill, and
        # "nothing to say" must be FLAT rather than a default BUY.
        out = SignalEngine(StrategyConfig("EMA_RSI_PATTERN")).run_all(series(rising(60)))
        assert all(s.signal == "FLAT" for s in out[:50])

    def test_signal_carries_its_reasons(self):
        engine = SignalEngine(StrategyConfig("EMA_RSI_PATTERN",
                                             {"require_pattern": False}))
        found = engine.run(series(rising(200)))
        assert found and all(s.reasons for s in found)

    def test_output_shape_is_instrument_independent(self):
        engine = SignalEngine(StrategyConfig("EMA_RSI_PATTERN",
                                             {"require_pattern": False}))
        for symbol in ("/NQ", "AAPL", "SPY"):
            found = engine.run(series(rising(200), symbol=symbol))
            assert found
            payload = found[0].as_dict()
            assert set(payload) >= {"symbol", "timeframe", "timestamp", "signal",
                                    "price", "strategy", "reasons"}
            assert payload["symbol"] == symbol

    def test_unknown_strategy_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            SignalEngine(StrategyConfig("NOPE"))

    def test_parameters_change_the_outcome(self):
        data = series(rising(200))
        strict = SignalEngine(StrategyConfig(
            "EMA_RSI_PATTERN", {"require_pattern": True})).run(data)
        loose = SignalEngine(StrategyConfig(
            "EMA_RSI_PATTERN", {"require_pattern": False})).run(data)
        assert len(loose) > len(strict)

    def test_a_signal_only_sees_its_own_bar_and_earlier(self):
        """Truncating the series after bar i must not change bar i's verdict."""
        candles = rising(160)
        full = SignalEngine(StrategyConfig(
            "EMA_RSI_PATTERN", {"require_pattern": False})).run_all(series(candles))

        for i in (120, 140, 159):
            truncated = SignalEngine(StrategyConfig(
                "EMA_RSI_PATTERN", {"require_pattern": False}
            )).run_all(series(candles[: i + 1]))
            assert truncated[i].signal == full[i].signal
            assert truncated[i].indicators["ema_fast"] == full[i].indicators["ema_fast"]


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------

def config(**over):
    base = dict(
        symbol="/NQ", timeframe="5m",
        start=date.today() - timedelta(days=10), end=date.today(),
        data_source="yahoo", strategy="EMA_RSI_PATTERN",
        strategy_parameters={"require_pattern": False},
        initial_capital=100_000.0, position_size=1.0,
        commission=0.0, slippage_ticks=0.0,
    )
    base.update(over)
    return BacktestConfig(**base)


class TestBacktestEngine:
    def test_entry_fills_at_the_next_bar_open_not_the_signal_bar(self):
        candles = rising(200)
        result = run_backtest(config(), series(candles))
        assert result.trades

        by_time = {c.timestamp: c for c in candles}
        for trade in result.trades:
            entry_bar = by_time[trade.entry_time]
            # The fill is that bar's open — never its close, and never a price
            # from the bar that produced the signal.
            assert trade.entry_price == pytest.approx(entry_bar.open)

    def test_same_close_model_fills_on_the_signal_bar(self):
        candles = rising(200)
        result = run_backtest(
            config(execution_model=ExecutionModel.SAME_CLOSE), series(candles))
        by_time = {c.timestamp: c for c in candles}
        for trade in result.trades:
            assert trade.entry_price == pytest.approx(by_time[trade.entry_time].close)

    def test_futures_pnl_uses_the_contract_multiplier(self):
        candles = rising(200)
        futures = run_backtest(config(symbol="/NQ"), series(candles))
        equity = run_backtest(config(symbol="AAPL"),
                              series(candles, symbol="AAPL"))
        # Same price path, same trades — only the point value differs, and /NQ
        # is worth twenty times a share.
        assert futures.trades and equity.trades
        assert len(futures.trades) == len(equity.trades)
        assert futures.trades[0].gross_pnl == pytest.approx(
            equity.trades[0].gross_pnl * 20)

    def test_micro_contract_is_a_tenth_of_the_full_size(self):
        candles = rising(200)
        full = run_backtest(config(symbol="/NQ"), series(candles))
        micro = run_backtest(config(symbol="/MNQ"), series(candles, symbol="/MNQ"))
        assert full.trades[0].gross_pnl == pytest.approx(
            micro.trades[0].gross_pnl * 10)

    def test_capital_shifts_equity_without_changing_trades(self):
        candles = rising(200)
        big = run_backtest(config(initial_capital=100_000), series(candles))
        small = run_backtest(config(initial_capital=25_000), series(candles))
        assert len(big.trades) == len(small.trades)
        assert (big.metrics()["finalEquity"] - small.metrics()["finalEquity"]
                == pytest.approx(75_000, abs=0.01))

    def test_commission_is_charged_on_both_sides(self):
        candles = rising(200)
        free = run_backtest(config(commission=0.0), series(candles))
        paid = run_backtest(config(commission=5.0), series(candles))
        assert paid.metrics()["totalCommission"] == pytest.approx(
            len(paid.trades) * 10.0)
        assert paid.metrics()["netPnl"] < free.metrics()["netPnl"]

    def test_slippage_always_works_against_the_trade(self):
        candles = rising(200)
        clean = run_backtest(config(slippage_ticks=0), series(candles))
        slipped = run_backtest(config(slippage_ticks=4), series(candles))
        assert slipped.metrics()["netPnl"] < clean.metrics()["netPnl"]

    def test_disabling_shorts_removes_them(self):
        candles = rising(120) + list(reversed(rising(120)))
        candles = [bar(i, c.open, c.high, c.low, c.close)
                   for i, c in enumerate(candles)]
        result = run_backtest(config(allow_short=False), series(candles))
        assert all(t.direction == "LONG" for t in result.trades)

    @staticmethod
    def _rise_then_wide(reach_low: bool, reach_high: bool, n=80, wide=12):
        """An uptrend that turns into bars wide enough to reach an exit level.

        The wide bars close where they opened, so the strategy's own signal
        does not flip — whatever ends the trade is the stop or the target.
        """
        up = rising(n)
        last = up[-1].close
        out = list(up)
        for i in range(wide):
            out.append(bar(n + i, last,
                           last + (500 if reach_high else 0.5),
                           last - (500 if reach_low else 0.5),
                           last))
        return out

    def test_stop_loss_exits_are_recorded(self):
        candles = self._rise_then_wide(reach_low=True, reach_high=False)
        result = run_backtest(config(stop_loss_atr=1.0), series(candles))
        assert any(t.exit_reason == "stop_loss" for t in result.trades)

    def test_take_profit_exits_are_recorded(self):
        candles = self._rise_then_wide(reach_low=False, reach_high=True)
        result = run_backtest(config(take_profit_atr=1.0), series(candles))
        assert any(t.exit_reason == "take_profit" for t in result.trades)

    def test_stop_wins_when_a_bar_could_have_hit_both(self):
        # A bar whose range spans stop and target must resolve as the stop:
        # choosing the target would flatter every result the engine produces.
        candles = self._rise_then_wide(reach_low=True, reach_high=True)
        result = run_backtest(config(stop_loss_atr=1.0, take_profit_atr=1.0),
                              series(candles))
        wide_exits = [t.exit_reason for t in result.trades
                      if t.exit_reason in ("stop_loss", "take_profit")
                      and t.exit_time >= candles[80].timestamp]
        assert wide_exits and set(wide_exits) == {"stop_loss"}

    def test_exits_name_the_signal_that_caused_them(self):
        candles = rising(120) + list(reversed(rising(120)))
        candles = [bar(i, c.open, c.high, c.low, c.close)
                   for i, c in enumerate(candles)]
        reasons = {t.exit_reason for t in run_backtest(config(), series(candles)).trades}
        assert "signal" not in reasons          # too vague to act on
        assert reasons & {"buy_signal", "sell_signal"}

    def test_each_trade_records_the_signal_that_opened_it(self):
        result = run_backtest(config(), series(rising(200)))
        for trade in result.trades:
            assert trade.entry_signal in ("BUY", "SELL")
            assert trade.strategy == "EMA_RSI_PATTERN"

    def test_open_position_is_closed_at_the_end_of_data(self):
        result = run_backtest(config(), series(rising(200)))
        assert result.trades[-1].exit_reason in (
            "end_of_data", "buy_signal", "sell_signal", "stop_loss", "take_profit")
        # Nothing may remain open once the series is exhausted.
        assert sum(1 for t in result.trades if t.exit_time is None) == 0

    def test_equity_curve_covers_every_bar(self):
        candles = rising(200)
        result = run_backtest(config(), series(candles))
        assert len(result.equity_curve) >= len(candles)
        assert result.equity_curve[0][1] == pytest.approx(100_000)

    def test_metrics_are_internally_consistent(self):
        result = run_backtest(config(commission=2.0), series(rising(200)))
        m = result.metrics()
        assert m["winningTrades"] + m["losingTrades"] <= m["totalTrades"]
        assert m["netPnl"] == pytest.approx(m["grossProfit"] + m["grossLoss"], abs=0.02)
        assert m["maxDrawdown"] >= 0

    def test_profit_factor_is_undefined_rather_than_infinite(self):
        result = run_backtest(config(), series(rising(200)))
        if not [t for t in result.trades if t.net_pnl < 0]:
            assert result.metrics()["profitFactor"] is None

    def test_impossible_configurations_are_refused(self):
        candles = series(rising(200))
        with pytest.raises(ValueError, match="capital"):
            run_backtest(config(initial_capital=0), candles)
        with pytest.raises(ValueError, match="Position size"):
            run_backtest(config(position_size=0), candles)
        with pytest.raises(ValueError, match="long or short"):
            run_backtest(config(allow_long=False, allow_short=False), candles)
        with pytest.raises(DataUnavailable, match="before end"):
            run_backtest(config(start=date(2026, 2, 1), end=date(2026, 1, 1)), candles)

    def test_benchmark_matches_the_underlying_move(self):
        """Holding one /NQ through a 200-point rise is 200 x $20."""
        candles = [bar(i, 20000 + i, 20000 + i + 1, 20000 + i - 1, 20000 + i)
                   for i in range(201)]
        result = run_backtest(config(commission=0, slippage_ticks=0), series(candles))
        bench = result.benchmark()
        assert bench["netPnl"] == pytest.approx(200 * 20.0)
        assert bench["finalEquity"] == pytest.approx(104_000)

    def test_benchmark_scales_with_the_instrument(self):
        candles = [bar(i, 20000 + i, 20000 + i + 1, 20000 + i - 1, 20000 + i)
                   for i in range(201)]
        full = run_backtest(config(symbol="/NQ", commission=0, slippage_ticks=0),
                            series(candles)).benchmark()
        micro = run_backtest(config(symbol="/MNQ", commission=0, slippage_ticks=0),
                             series(candles, symbol="/MNQ")).benchmark()
        assert full["netPnl"] == pytest.approx(micro["netPnl"] * 10)

    def test_benchmark_pays_the_same_costs_as_the_strategy(self):
        candles = [bar(i, 20000 + i, 20000 + i + 1, 20000 + i - 1, 20000 + i)
                   for i in range(201)]
        free = run_backtest(config(commission=0, slippage_ticks=0),
                            series(candles)).benchmark()
        paid = run_backtest(config(commission=5, slippage_ticks=2),
                            series(candles)).benchmark()
        assert paid["netPnl"] < free["netPnl"]

    def test_outperformance_is_the_difference(self):
        result = run_backtest(config(), series(rising(200)))
        bench = result.benchmark()
        assert bench["outperformance"] == pytest.approx(
            result.metrics()["netPnl"] - bench["netPnl"], abs=0.02)
        assert bench["beatsBuyAndHold"] == (bench["outperformance"] > 0)

    def test_benchmark_covers_the_same_bars(self):
        candles = rising(200)
        result = run_backtest(config(), series(candles))
        assert result.benchmark_curve[0][0] == candles[0].timestamp
        assert result.benchmark_curve[-1][0] == candles[-1].timestamp

    def test_result_serializes_for_the_panel(self):
        payload = run_backtest(config(), series(rising(200))).as_dict()
        assert set(payload) >= {"config", "instrument", "provider", "metrics",
                                "trades", "equityCurve", "warnings"}
        assert payload["config"]["symbol"] == "/NQ"
        assert payload["instrument"]["contractMultiplier"] == 20.0

    def test_the_same_engine_runs_any_instrument(self):
        candles = rising(200)
        for symbol in ("/NQ", "/MES", "AAPL", "SPY", "TSLA"):
            result = run_backtest(config(symbol=symbol),
                                  series(candles, symbol=symbol))
            assert result.bars == 200
            assert result.config["symbol"] == symbol

    def test_no_trade_can_close_before_it_opens(self):
        result = run_backtest(config(stop_loss_atr=1.0, take_profit_atr=1.5),
                              series(rising(300)))
        for trade in result.trades:
            assert trade.exit_time >= trade.entry_time
            assert trade.bars_held >= 0


class TestExitSignal:
    """EXIT closes a position without opening the opposite one.

    The distinction matters: before EXIT existed, a strategy that wanted out had
    to emit SELL, which opens a short whenever the run allows shorts.
    """

    def _run(self, signals, **over):
        from ftmo_mt5.signals.strategies import STRATEGIES, Verdict

        class Scripted:
            name = "SCRIPTED_TEST"

            def describe(self, params=None):
                return {"name": self.name, "label": "scripted", "parameters": []}

            def prepare(self, candles, params):
                return {}

            def evaluate(self, candles, i, state):
                return Verdict(signals[i] if i < len(signals) else "FLAT")

        STRATEGIES["SCRIPTED_TEST"] = Scripted()
        try:
            base = dict(symbol="/NQ", timeframe="1h",
                        start=date.today() - timedelta(days=5), end=date.today(),
                        data_source="yahoo", strategy="SCRIPTED_TEST",
                        initial_capital=100_000.0, position_size=1.0)
            base.update(over)
            return run_backtest(BacktestConfig(**base),
                                series(rising(len(signals) + 2)))
        finally:
            STRATEGIES.pop("SCRIPTED_TEST", None)

    def test_exit_closes_the_position(self):
        out = self._run(["BUY"] + ["FLAT"] * 8 + ["EXIT"] + ["FLAT"] * 5)
        assert len(out.trades) == 1
        assert out.trades[0].exit_reason == "exit_signal"
        assert out.trades[0].direction == "LONG"

    def test_exit_does_not_open_a_short_even_when_shorts_are_allowed(self):
        out = self._run(["BUY"] + ["FLAT"] * 5 + ["EXIT"] + ["FLAT"] * 8,
                        allow_short=True)
        assert [t.direction for t in out.trades] == ["LONG"]

    def test_a_sell_would_have_reversed_where_exit_does_not(self):
        with_sell = self._run(["BUY"] + ["FLAT"] * 5 + ["SELL"] + ["FLAT"] * 8,
                              allow_short=True)
        with_exit = self._run(["BUY"] + ["FLAT"] * 5 + ["EXIT"] + ["FLAT"] * 8,
                              allow_short=True)
        assert "SHORT" in [t.direction for t in with_sell.trades]
        assert "SHORT" not in [t.direction for t in with_exit.trades]

    def test_exit_while_flat_does_nothing(self):
        out = self._run(["EXIT"] * 10)
        assert out.trades == []

    def test_exit_signals_are_not_counted_as_entries(self):
        out = self._run(["BUY"] + ["EXIT"] * 9)
        assert out.signals == 1

    def test_a_self_closing_strategy_is_not_warned_about_missing_stops(self):
        out = self._run(["BUY"] + ["FLAT"] * 5 + ["EXIT"] + ["FLAT"] * 5)
        assert not any("No stop loss" in w for w in out.warnings)

    def test_a_strategy_with_no_exit_still_warns(self):
        out = self._run(["BUY"] + ["FLAT"] * 12)
        assert any("No stop loss" in w for w in out.warnings)


class TestTrailingStop:
    """A stop that follows the best price and never moves back.

    The subtle part is not the arithmetic — it is that the trail must be tested
    at the level previous bars set it to, before this bar's own extreme raises
    it. Ratcheting first would let a bar that spiked and then collapsed exit at
    a price the position was never actually protected at.
    """

    def _walk(self, candles, buy_until=1, **over):
        from ftmo_mt5.signals.strategies import STRATEGIES, Verdict

        class BuyEarly:
            """Buys for the first `buy_until` bars, then never again.

            The window matters for ATR-based runs: the engine refuses to open a
            position while ATR has no value, so a single bar-0 signal would be
            skipped and nothing would ever be tested.
            """

            name = "BUY_ONCE_TEST"

            def describe(self, params=None):
                return {"name": self.name, "label": "buy early", "parameters": []}

            def prepare(self, candles, params):
                return {}

            def evaluate(self, candles, i, state):
                return Verdict("BUY" if i < buy_until else "FLAT")

        STRATEGIES["BUY_ONCE_TEST"] = BuyEarly()
        try:
            base = dict(symbol="/NQ", timeframe="1h",
                        start=date.today() - timedelta(days=5), end=date.today(),
                        data_source="yahoo", strategy="BUY_ONCE_TEST",
                        initial_capital=100_000.0, position_size=1.0)
            base.update(over)
            return run_backtest(BacktestConfig(**base), series(candles))
        finally:
            STRATEGIES.pop("BUY_ONCE_TEST", None)

    def _climb_then_drop(self):
        # Up from 100 to 120, then straight back down to 90.
        out = [bar(0, 100, 101, 99, 100)]
        for i in range(1, 21):
            p = 100 + i
            out.append(bar(i, p - 1, p, p - 1, p))
        for i, p in enumerate(range(119, 89, -1), start=21):
            out.append(bar(i, p + 1, p + 1, p, p))
        return out

    def test_it_exits_after_the_peak_not_at_the_bottom(self):
        out = self._walk(self._climb_then_drop(), trailing_stop_pct=5.0)
        assert len(out.trades) == 1
        t = out.trades[0]
        assert t.exit_reason == "trailing_stop"
        # Peak was 120, so a 5% trail sits at 114 — far above the 90 low.
        assert 113 <= t.exit_price <= 115

    def test_the_trail_never_moves_back_down(self):
        # Rise, fall short of the trail, rise again: the exit must reflect the
        # highest level the trail ever reached, not the latest one.
        candles = self._climb_then_drop()
        out = self._walk(candles, trailing_stop_pct=5.0)
        peak = max(c.high for c in candles[:22])
        assert out.trades[0].exit_price >= peak * 0.95 - 1

    def test_no_trailing_stop_means_no_early_exit(self):
        out = self._walk(self._climb_then_drop())
        assert out.trades[0].exit_reason == "end_of_data"

    def test_this_bar_high_cannot_rescue_this_bar_low(self):
        # One bar spikes to 140 and collapses to 100 in the same bar. The trail
        # entering that bar was set by the 120 peak, so the exit is 114 — not
        # 133, which a trail raised on the spike would have produced.
        candles = self._climb_then_drop()[:22]
        candles.append(bar(22, 120, 140, 100, 101))
        out = self._walk(candles, trailing_stop_pct=5.0)
        assert out.trades[0].exit_reason == "trailing_stop"
        assert out.trades[0].exit_price < 120

    def test_the_tighter_of_the_two_stops_wins(self):
        # A fixed stop below the entry and a trail that has climbed above it:
        # the trail is in force and the trade is labelled as such.
        out = self._walk(self._climb_then_drop(),
                         trailing_stop_pct=5.0, stop_loss_pct=20.0)
        assert out.trades[0].exit_reason == "trailing_stop"

    def test_a_fixed_stop_still_wins_while_it_is_tighter(self):
        # Falling from the first bar: the trail never gets above the fixed stop.
        candles = [bar(i, 100 - i, 100.5 - i, 99 - i, 99.5 - i) for i in range(30)]
        out = self._walk(candles, trailing_stop_pct=20.0, stop_loss_pct=1.0)
        assert out.trades[0].exit_reason == "stop_loss"

    def test_it_trails_a_short_the_other_way(self):
        # Down from 100 to 80, then back up. A short's trail follows the low.
        candles = [bar(0, 100, 101, 99, 100)]
        candles += [bar(i, 100 - i + 1, 100 - i + 1, 100 - i, 100 - i)
                    for i in range(1, 21)]
        candles += [bar(i, 79 + i - 21, 80 + i - 21, 79 + i - 21, 80 + i - 21)
                    for i in range(21, 40)]
        from ftmo_mt5.signals.strategies import STRATEGIES, Verdict

        class SellOnce:
            name = "SELL_ONCE_TEST"

            def describe(self, params=None):
                return {"name": self.name, "label": "sell once", "parameters": []}

            def prepare(self, candles, params):
                return {}

            def evaluate(self, candles, i, state):
                return Verdict("SELL" if i == 0 else "FLAT")

        STRATEGIES["SELL_ONCE_TEST"] = SellOnce()
        try:
            out = run_backtest(BacktestConfig(
                symbol="/NQ", timeframe="1h",
                start=date.today() - timedelta(days=5), end=date.today(),
                data_source="yahoo", strategy="SELL_ONCE_TEST",
                initial_capital=100_000.0, position_size=1.0,
                allow_short=True, trailing_stop_pct=5.0), series(candles))
        finally:
            STRATEGIES.pop("SELL_ONCE_TEST", None)
        assert out.trades[0].direction == "SHORT"
        assert out.trades[0].exit_reason == "trailing_stop"

    def test_atr_beats_percent_when_both_are_given(self):
        candles = self._climb_then_drop()
        # Signals run to bar 20 so the entry lands once ATR-14 exists; the trail
        # is still ratcheting up through the climb either way.
        wide = self._walk(candles, buy_until=20,
                          trailing_stop_atr=8.0, trailing_stop_pct=1.0)
        tight = self._walk(candles, buy_until=20, trailing_stop_pct=1.0)
        assert wide.trades and tight.trades
        assert wide.trades[0].exit_price < tight.trades[0].exit_price

    def test_it_counts_as_an_exit_for_the_missing_stop_warning(self):
        out = self._walk(self._climb_then_drop(), trailing_stop_pct=5.0)
        assert not any("No stop loss" in w for w in out.warnings)


class TestRiskDefaults:
    """Per-instrument, per-timeframe trailing stops, and the user's edits."""

    def _module(self, tmp_path, monkeypatch):
        from ftmo_mt5.backtest import risk_defaults as rd
        monkeypatch.setattr(rd, "OVERRIDES_FILE", tmp_path / "defaults.json")
        return rd

    def test_measured_values_are_served(self, tmp_path, monkeypatch):
        rd = self._module(tmp_path, monkeypatch)
        assert rd.resolve("/GC", "1d") == 0.4
        assert rd.resolve("/NQ", "1d") == 0.5
        assert rd.resolve("/NQ", "1h") == 0.5

    def test_five_minute_bars_carry_no_default(self, tmp_path, monkeypatch):
        # Nothing there survived realistic costs, and None is the honest answer
        # rather than a number borrowed from a slower timeframe.
        rd = self._module(tmp_path, monkeypatch)
        assert rd.resolve("/GC", "5m") is None
        assert rd.resolve("/NQ", "5m") is None

    def test_an_unmeasured_instrument_gets_nothing(self, tmp_path, monkeypatch):
        rd = self._module(tmp_path, monkeypatch)
        assert rd.resolve("/CL", "1d") is None

    def test_symbol_forms_resolve_to_the_same_root(self, tmp_path, monkeypatch):
        rd = self._module(tmp_path, monkeypatch)
        assert rd.resolve("NQ", "1h") == rd.resolve("/NQ", "1h") == rd.resolve("/NQU6", "1h")

    def test_an_override_wins_and_survives_a_reload(self, tmp_path, monkeypatch):
        rd = self._module(tmp_path, monkeypatch)
        rd.save_overrides({"GC": {"1d": 0.9}})
        assert rd.resolve("/GC", "1d") == 0.9
        assert rd.all_entries()["builtIn"]["GC"]["1d"] == 0.4   # table untouched

    def test_an_override_can_switch_a_trail_off(self, tmp_path, monkeypatch):
        rd = self._module(tmp_path, monkeypatch)
        rd.save_overrides({"NQ": {"1d": None}})
        assert rd.resolve("/NQ", "1d") is None

    def test_a_new_instrument_can_be_added(self, tmp_path, monkeypatch):
        rd = self._module(tmp_path, monkeypatch)
        rd.save_overrides({"cl": {"1h": 1.25}})
        assert rd.resolve("/CL", "1h") == 1.25

    def test_reset_restores_the_measured_table(self, tmp_path, monkeypatch):
        rd = self._module(tmp_path, monkeypatch)
        rd.save_overrides({"GC": {"1d": 9.0}})
        rd.reset_overrides()
        assert rd.resolve("/GC", "1d") == 0.4
        assert rd.all_entries()["overrides"] == {}

    def test_a_corrupt_overrides_file_is_ignored(self, tmp_path, monkeypatch):
        rd = self._module(tmp_path, monkeypatch)
        rd.OVERRIDES_FILE.write_text("{not json", encoding="utf-8")
        assert rd.resolve("/GC", "1d") == 0.4

    def test_unparseable_values_are_dropped_not_guessed(self, tmp_path, monkeypatch):
        rd = self._module(tmp_path, monkeypatch)
        rd.OVERRIDES_FILE.write_text('{"GC": {"1d": "wide"}}', encoding="utf-8")
        assert rd.resolve("/GC", "1d") == 0.4


class TestEntryBarRisk:
    """The bar a position opens on must carry its risk, not just its upside.

    With a next-open fill the whole entry bar happens after the position exists.
    Letting its high ratchet a trailing stop while never testing its low against
    that stop is a free lunch: on short holds it books an exit above the entry
    on a bar that may never have been profitable.
    """

    def _walk(self, candles, **over):
        from ftmo_mt5.signals.strategies import STRATEGIES, Verdict

        class BuyFirst:
            name = "BUY_FIRST_TEST"

            def describe(self, params=None):
                return {"name": self.name, "label": "buy first", "parameters": []}

            def prepare(self, candles, params):
                return {}

            def evaluate(self, candles, i, state):
                return Verdict("BUY" if i == 0 else "FLAT")

        STRATEGIES["BUY_FIRST_TEST"] = BuyFirst()
        try:
            base = dict(symbol="/NQ", timeframe="1h",
                        start=date.today() - timedelta(days=5), end=date.today(),
                        data_source="yahoo", strategy="BUY_FIRST_TEST",
                        initial_capital=100_000.0, position_size=1.0)
            base.update(over)
            return run_backtest(BacktestConfig(**base), series(candles))
        finally:
            STRATEGIES.pop("BUY_FIRST_TEST", None)

    def _spike_then_drop(self):
        # Bar 1 is the entry bar: opens at 100, runs to 130, closes back at 101.
        # A trail set from its high must be tested against its own low.
        return [
            bar(0, 100, 101, 99, 100),
            bar(1, 100, 130, 95, 101),
            bar(2, 101, 102, 100, 101),
            bar(3, 101, 102, 100, 101),
        ]

    def test_the_entry_bar_can_stop_the_position_out(self):
        out = self._walk(self._spike_then_drop(), stop_loss_pct=2.0)
        assert out.trades
        t = out.trades[0]
        assert t.exit_reason == "stop_loss"
        assert t.bars_held == 0                 # stopped on the bar it opened

    def test_a_trailing_stop_cannot_book_a_gain_the_bar_never_held(self):
        out = self._walk(self._spike_then_drop(), trailing_stop_pct=5.0)
        assert out.trades
        t = out.trades[0]
        # Entry 100, trail seeded at 95, and the entry bar traded down to 95.
        # The exit is that stop — not 123.5, which is what a trail raised by the
        # same bar's 130 high but never tested against its low would have paid.
        assert t.bars_held == 0
        assert t.exit_price == 95
        assert t.net_pnl < 0

    def test_the_trail_still_ratchets_off_the_entry_bar_for_later_bars(self):
        # A calm entry bar that closes near its high: the trail should follow it
        # up and protect the next bar, without ever having been testable itself.
        candles = [bar(0, 100, 101, 99, 100),
                   bar(1, 100, 120, 99.5, 119),
                   bar(2, 119, 119, 100, 101)]
        out = self._walk(candles, trailing_stop_pct=5.0)
        assert out.trades
        t = out.trades[0]
        assert t.bars_held == 1
        assert t.exit_price == 114        # 120 high less 5%
        assert t.net_pnl > 0

    def test_a_close_fill_does_not_test_the_bar_that_is_already_over(self):
        # SAME_CLOSE enters at the bar's close, so that bar's low happened
        # before the position existed and must not stop it out.
        out = self._walk(self._spike_then_drop(), stop_loss_pct=2.0,
                         execution_model=ExecutionModel.SAME_CLOSE)
        assert out.trades
        assert out.trades[0].bars_held >= 1

    def test_a_quiet_entry_bar_still_opens_normally(self):
        candles = [bar(i, 100 + i, 101 + i, 99.5 + i, 100.8 + i) for i in range(6)]
        out = self._walk(candles, stop_loss_pct=5.0)
        assert out.trades
        assert out.trades[0].exit_reason == "end_of_data"


class TestGapThroughFills:
    """A stop that the market gapped past cannot fill at the stop price.

    On daily bars this is not an edge case. Measured on 25 years of gold, the
    median overnight gap is roughly the size of a 0.25 ATR trail, and 31% of
    trailing exits opened beyond their stop — worth 45% of the reported P&L.
    """

    def _walk(self, candles, direction="LONG", **over):
        from ftmo_mt5.signals.strategies import STRATEGIES, Verdict

        class Once:
            name = "GAP_TEST"

            def describe(self, params=None):
                return {"name": self.name, "label": "gap test", "parameters": []}

            def prepare(self, candles, params):
                return {}

            def evaluate(self, candles, i, state):
                signal = "BUY" if direction == "LONG" else "SELL"
                return Verdict(signal if i == 0 else "FLAT")

        STRATEGIES["GAP_TEST"] = Once()
        try:
            base = dict(symbol="/NQ", timeframe="1d",
                        start=date.today() - timedelta(days=5), end=date.today(),
                        data_source="yahoo", strategy="GAP_TEST",
                        initial_capital=100_000.0, position_size=1.0,
                        allow_short=(direction == "SHORT"))
            base.update(over)
            return run_backtest(BacktestConfig(**base), series(candles))
        finally:
            STRATEGIES.pop("GAP_TEST", None)

    def test_a_long_stop_gapped_through_fills_at_the_open(self):
        # Entry at 100 with a 5% stop at 95. The next bar opens at 90 — the
        # market was never at 95, so 90 is the fill.
        candles = [bar(0, 100, 101, 99, 100),
                   bar(1, 100, 101, 99, 100),
                   bar(2, 90, 91, 85, 88),
                   bar(3, 88, 89, 87, 88)]
        out = self._walk(candles, stop_loss_pct=5.0)
        assert out.trades
        assert out.trades[0].exit_reason == "stop_loss"
        assert out.trades[0].exit_price == 90

    def test_a_stop_reached_inside_the_bar_still_fills_at_the_stop(self):
        # The bar opens above the stop and trades down through it: here the
        # level really was available.
        candles = [bar(0, 100, 101, 99, 100),
                   bar(1, 100, 101, 99, 100),
                   bar(2, 99, 100, 90, 92),
                   bar(3, 92, 93, 91, 92)]
        out = self._walk(candles, stop_loss_pct=5.0)
        assert out.trades[0].exit_price == 95

    def test_a_short_stop_gapped_through_fills_at_the_open(self):
        # Short at 100, stop 5% above at 105, and the bar opens at 112.
        candles = [bar(0, 100, 101, 99, 100),
                   bar(1, 100, 101, 99, 100),
                   bar(2, 112, 115, 111, 114),
                   bar(3, 114, 115, 113, 114)]
        out = self._walk(candles, direction="SHORT", stop_loss_pct=5.0)
        assert out.trades
        assert out.trades[0].direction == "SHORT"
        assert out.trades[0].exit_price == 112

    def test_the_gap_makes_the_trade_worse_never_better(self):
        gapped = [bar(0, 100, 101, 99, 100), bar(1, 100, 101, 99, 100),
                  bar(2, 90, 91, 85, 88), bar(3, 88, 89, 87, 88)]
        clean = [bar(0, 100, 101, 99, 100), bar(1, 100, 101, 99, 100),
                 bar(2, 99, 100, 90, 92), bar(3, 92, 93, 91, 92)]
        a = self._walk(gapped, stop_loss_pct=5.0).trades[0]
        b = self._walk(clean, stop_loss_pct=5.0).trades[0]
        assert a.net_pnl < b.net_pnl

    def test_a_trailing_stop_gapped_through_also_fills_at_the_open(self):
        candles = [bar(0, 100, 101, 99, 100)]
        candles += [bar(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(1, 8)]
        candles.append(bar(8, 95, 96, 94, 95))       # gaps far below the trail
        out = self._walk(candles, trailing_stop_pct=2.0)
        assert out.trades
        t = out.trades[0]
        assert t.exit_reason == "trailing_stop"
        assert t.exit_price == 95


class TestAdx:
    """Wilder's ADX and the directional lines it is built from."""

    def _trend(self, n, step, start=100.0):
        out = []
        for i in range(n):
            base = start + i * step
            out.append(bar(i, base, base + abs(step) * 0.6,
                           base - abs(step) * 0.2, base + abs(step) * 0.5))
        return out

    def test_it_warms_up_before_reporting(self):
        from ftmo_mt5.signals.indicators import adx
        out = adx(self._trend(40, 1.0), 14)
        assert out[:14] == [None] * 14
        assert out[-1] is not None

    def test_too_little_history_is_all_none(self):
        from ftmo_mt5.signals.indicators import adx
        assert adx(self._trend(8, 1.0), 14) == [None] * 8

    def test_a_strong_trend_scores_higher_than_chop(self):
        from ftmo_mt5.signals.indicators import adx
        trend = adx(self._trend(80, 1.0), 14)[-1]
        chop = adx([bar(i, 100 + (i % 2), 101 + (i % 2),
                        99 + (i % 2), 100.5 - (i % 2)) for i in range(80)], 14)[-1]
        assert trend > chop

    def test_it_does_not_care_about_direction(self):
        # A fall as orderly as the rise should score about the same: ADX
        # measures conviction, not which way.
        from ftmo_mt5.signals.indicators import adx
        up = adx(self._trend(80, 1.0), 14)[-1]
        down = adx(self._trend(80, -1.0, start=200.0), 14)[-1]
        assert abs(up - down) < 15

    def test_directional_lines_separate_in_a_trend(self):
        from ftmo_mt5.signals.indicators import directional
        up, down = directional(self._trend(60, 1.0), 14)
        assert up[-1] > down[-1]
        up2, down2 = directional(self._trend(60, -1.0, start=200.0), 14)
        assert down2[-1] > up2[-1]

    def test_directional_movement_is_exclusive(self):
        # An outside bar extends both ways; only the larger extension counts.
        from ftmo_mt5.signals.indicators import directional
        candles = [bar(0, 100, 101, 99, 100), bar(1, 100, 105, 95, 100)]
        candles += [bar(i, 100, 101, 99, 100) for i in range(2, 20)]
        up, down = directional(candles, 14)
        assert up[-1] is not None and down[-1] is not None
        assert not (up[-1] > 0 and down[-1] > 0 and abs(up[-1] - down[-1]) < 1e-9)


class TestStagedStops:
    """Breakeven promotion and delayed trailing activation.

    Both are per-position state that only exists once a trade is open, and both
    must use the profit as of the previous close — a bar that earns the
    promotion must not also be protected by it.
    """

    def _walk(self, candles, **over):
        from ftmo_mt5.signals.strategies import STRATEGIES, Verdict

        class BuyFirst:
            name = "STAGED_TEST"

            def describe(self, params=None):
                return {"name": self.name, "label": "staged", "parameters": []}

            def prepare(self, candles, params):
                return {}

            def evaluate(self, candles, i, state):
                # Bar 20, not bar 0: the staged stops are measured in ATR, and
                # ATR(14) does not exist until fourteen bars have closed.
                return Verdict("BUY" if i == 20 else "FLAT")

        STRATEGIES["STAGED_TEST"] = BuyFirst()
        try:
            base = dict(symbol="/NQ", timeframe="1d",
                        start=date.today() - timedelta(days=5), end=date.today(),
                        data_source="yahoo", strategy="STAGED_TEST",
                        initial_capital=100_000.0, position_size=1.0)
            base.update(over)
            return run_backtest(BacktestConfig(**base), series(candles))
        finally:
            STRATEGIES.pop("STAGED_TEST", None)

    def _rise_then_fall(self):
        # Twenty quiet bars so ATR exists, then enter, climb, and collapse back
        # through the entry price.
        out = [bar(i, 100, 101, 99, 100) for i in range(21)]
        for i in range(21, 36):
            p = 100 + (i - 20) * 2
            out.append(bar(i, p - 1, p + 1, p - 2, p))
        for i, p in enumerate(range(128, 80, -3), start=36):
            out.append(bar(i, p + 2, p + 2, p - 1, p))
        return out

    def test_breakeven_stops_a_winner_turning_into_a_loser(self):
        out = self._walk(self._rise_then_fall(),
                         stop_loss_pct=20.0, breakeven_at_atr=1.0)
        assert out.trades
        t = out.trades[0]
        assert t.net_pnl >= 0, "a position promoted to breakeven should not lose"

    def test_without_breakeven_the_same_trade_gives_it_all_back(self):
        out = self._walk(self._rise_then_fall(), stop_loss_pct=20.0)
        assert out.trades
        assert out.trades[0].net_pnl < 0

    def test_delayed_trailing_lets_the_trade_develop(self):
        # A tight trail from entry exits on the first wobble; the same trail
        # held back until the position is 2 ATR up survives it.
        candles = [bar(i, 100, 101, 99, 100) for i in range(21)]
        candles.append(bar(21, 100, 101, 97, 99))          # the wobble
        candles += [bar(i, 79 + i, 81 + i, 78 + i, 80 + i) for i in range(22, 45)]
        eager = self._walk(candles, trailing_stop_pct=1.0)
        patient = self._walk(candles, trailing_stop_pct=1.0, trail_activate_atr=2.0)
        assert eager.trades and patient.trades
        assert patient.trades[0].bars_held > eager.trades[0].bars_held

    def test_a_stop_only_ever_tightens(self):
        # Breakeven must not loosen a stop that is already above the entry.
        candles = self._rise_then_fall()
        loose = self._walk(candles, trailing_stop_pct=2.0)
        both = self._walk(candles, trailing_stop_pct=2.0, breakeven_at_atr=1.0)
        assert loose.trades and both.trades
        assert both.trades[0].exit_price >= loose.trades[0].exit_price


class TestRiskSizing:
    """Position size derived from the distance to the stop."""

    def _walk(self, candles, stop_price=None, **over):
        from ftmo_mt5.signals.strategies import STRATEGIES, Verdict

        class Sized:
            name = "SIZED_TEST"

            def describe(self, params=None):
                return {"name": self.name, "label": "sized", "parameters": []}

            def prepare(self, candles, params):
                return {}

            def evaluate(self, candles, i, state):
                if i != 0:
                    return Verdict("FLAT")
                # The stop comes from the rule, so no ATR warm-up is needed.
                return Verdict("BUY", stop_price=stop_price)

        STRATEGIES["SIZED_TEST"] = Sized()
        try:
            base = dict(symbol="/NQ", timeframe="1d",
                        start=date.today() - timedelta(days=5), end=date.today(),
                        data_source="yahoo", strategy="SIZED_TEST",
                        initial_capital=100_000.0, position_size=1.0)
            base.update(over)
            return run_backtest(BacktestConfig(**base), series(candles))
        finally:
            STRATEGIES.pop("SIZED_TEST", None)

    def _flat(self):
        return [bar(i, 100, 101, 99, 100) for i in range(12)]

    def test_a_wider_stop_buys_fewer_contracts(self):
        near = self._walk(self._flat(), stop_price=99.0, risk_per_trade_pct=1.0)
        far = self._walk(self._flat(), stop_price=95.0, risk_per_trade_pct=1.0)
        assert near.trades and far.trades
        assert near.trades[0].quantity > far.trades[0].quantity

    def test_size_is_ignored_without_a_stop_to_measure_from(self):
        out = self._walk(self._flat(), risk_per_trade_pct=1.0)
        assert out.trades
        assert out.trades[0].quantity == 1.0

    def test_configured_size_stands_when_risk_sizing_is_off(self):
        out = self._walk(self._flat(), stop_price=99.0, position_size=3.0)
        assert out.trades
        assert out.trades[0].quantity == 3.0
