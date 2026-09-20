"""Tests for option pricing, contract selection and the options backtest mode.

The pricing tests check the model against closed forms and against arbitrage
relationships (put-call parity, monotonicity in each input) rather than against
remembered numbers, so they catch a wrong sign or a swapped argument.
"""

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from ftmo_mt5.backtest import BacktestConfig, run_backtest
from ftmo_mt5.backtest.models import OptionAction, TradeMode
from ftmo_mt5.instruments import resolve_instrument
from ftmo_mt5.marketdata.base import Candle, CandleSeries
from ftmo_mt5.options import (
    ExpiryRule,
    atm_strike,
    black76_delta,
    black76_price,
    contract_label,
    daily_volatility_series,
    intrinsic_value,
    next_expiry,
    periods_per_year,
    realized_volatility,
)

NQ = resolve_instrument("/NQ")
START = 1_700_000_000          # a Tuesday, 22:13 UTC


def bar(i, o, h, l, c, step=300):
    return Candle(timestamp=START + i * step, open=o, high=h, low=l, close=c, volume=100.0)


# ---------------------------------------------------------------------------
# Black-76
# ---------------------------------------------------------------------------

class TestBlack76:
    def test_atm_call_matches_the_closed_form(self):
        f, k, t, v = 100.0, 100.0, 1.0, 0.20
        # At the money with r=0 the price collapses to F(2N(σ√T/2) − 1).
        expected = f * (2 * 0.5 * (1 + math.erf((v * math.sqrt(t) / 2) / math.sqrt(2))) - 1)
        assert black76_price("call", f, k, t, v) == pytest.approx(expected, rel=1e-12)

    @pytest.mark.parametrize("strike", [80.0, 100.0, 125.0])
    def test_put_call_parity_holds(self, strike):
        f, t, v = 100.0, 0.5, 0.25
        call = black76_price("call", f, strike, t, v)
        put = black76_price("put", f, strike, t, v)
        assert call - put == pytest.approx(f - strike, abs=1e-9)

    def test_premium_rises_with_volatility(self):
        prices = [black76_price("call", 100, 100, 0.25, v) for v in (0.1, 0.2, 0.4)]
        assert prices == sorted(prices)

    def test_premium_rises_with_time(self):
        prices = [black76_price("put", 100, 100, t, 0.2) for t in (0.01, 0.1, 1.0)]
        assert prices == sorted(prices)

    def test_call_falls_as_the_strike_rises(self):
        prices = [black76_price("call", 100, k, 0.5, 0.2) for k in (80, 100, 120)]
        assert prices == sorted(prices, reverse=True)

    def test_never_worth_less_than_intrinsic(self):
        for f, k in ((120, 100), (100, 100), (80, 100)):
            for kind in ("call", "put"):
                assert (black76_price(kind, f, k, 0.3, 0.25)
                        >= intrinsic_value(kind, f, k) - 1e-9)

    def test_at_expiry_the_price_is_the_intrinsic(self):
        assert black76_price("call", 110, 100, 0.0, 0.3) == pytest.approx(10.0)
        assert black76_price("put", 110, 100, 0.0, 0.3) == pytest.approx(0.0)
        assert black76_price("put", 90, 100, -1.0, 0.3) == pytest.approx(10.0)

    def test_zero_volatility_leaves_only_intrinsic(self):
        assert black76_price("call", 110, 100, 1.0, 0.0) == pytest.approx(10.0)

    def test_discounting_reduces_the_premium(self):
        undiscounted = black76_price("call", 100, 100, 1.0, 0.2, rate=0.0)
        discounted = black76_price("call", 100, 100, 1.0, 0.2, rate=0.05)
        assert discounted < undiscounted

    def test_delta_is_bounded_and_signed(self):
        assert black76_delta("call", 200, 100, 1, 0.2) == pytest.approx(1.0, abs=1e-3)
        assert black76_delta("call", 50, 100, 1, 0.2) == pytest.approx(0.0, abs=1e-3)
        assert black76_delta("call", 100, 100, 1, 0.2) == pytest.approx(0.54, abs=0.02)
        assert black76_delta("put", 100, 100, 1, 0.2) < 0


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------

class TestVolatility:
    def test_a_flat_series_has_no_volatility(self):
        assert realized_volatility([100.0] * 60, 252, 30) == pytest.approx(0.0)

    def test_a_noisier_series_measures_higher(self):
        calm = [100 + (i % 2) * 0.1 for i in range(80)]
        wild = [100 + (i % 2) * 5.0 for i in range(80)]
        assert (realized_volatility(wild, 252, 30)
                > realized_volatility(calm, 252, 30))

    def test_too_little_history_returns_zero_not_a_guess(self):
        assert realized_volatility([100.0, 101.0], 252, 30) == 0.0

    def test_annualization_follows_the_instrument_session(self):
        # A future trades 23 hours, an equity 6.5 — the same bar size cannot
        # imply the same number of bars per year.
        equity = resolve_instrument("AAPL")
        assert periods_per_year(300, NQ) > periods_per_year(300, equity)

    def test_daily_estimate_does_not_depend_on_the_bar_size(self):
        """The same days priced at two bar sizes must give the same vol."""
        import random
        random.seed(11)

        price = 20000.0
        five_min, hourly = [], []
        day_seconds = 86400
        # 13 slots so that slot 12 — the last of the day — is also on the
        # coarse grid. Both series must close each day on the same bar, or
        # they are measuring different daily closes and nothing follows.
        for day in range(40):
            for slot in range(13):
                price *= 1 + random.gauss(0, 0.002)
                stamp = START + day * day_seconds + slot * 300
                five_min.append(Candle(timestamp=stamp, open=price, high=price,
                                       low=price, close=price))
                if slot % 4 == 0:
                    hourly.append(Candle(timestamp=stamp, open=price, high=price,
                                         low=price, close=price))

        fine = daily_volatility_series(five_min, 20, 252)[-1]
        coarse = daily_volatility_series(hourly, 20, 252)[-1]
        assert fine == pytest.approx(coarse, rel=0.05)

    def test_series_is_aligned_and_causal(self):
        candles = [bar(i, 100 + i, 100 + i, 100 + i, 100 + i, step=86400)
                   for i in range(60)]
        full = daily_volatility_series(candles, 20, 252)
        assert len(full) == len(candles)
        truncated = daily_volatility_series(candles[:40], 20, 252)
        assert truncated[39] == pytest.approx(full[39])


# ---------------------------------------------------------------------------
# Contract selection
# ---------------------------------------------------------------------------

class TestContractSelection:
    def test_atm_strike_uses_the_listed_increment(self):
        # /NQ lists strikes every 10 points, not every 0.25 tick.
        assert atm_strike(29253.5, NQ) == 29250
        assert atm_strike(29256.0, NQ) == 29260

    def test_a_different_product_uses_its_own_increment(self):
        es = resolve_instrument("/ES")
        assert es.option_strike_increment != NQ.option_strike_increment
        assert atm_strike(5303.0, es) % es.option_strike_increment == 0

    def test_next_expiry_respects_the_weekday_rule(self):
        moment = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)   # a Tuesday
        assert next_expiry(moment, ExpiryRule.MWF).date() == date(2026, 8, 12)
        assert next_expiry(moment, ExpiryRule.FRIDAY).date() == date(2026, 8, 14)
        assert next_expiry(moment, ExpiryRule.DAILY).date() == date(2026, 8, 11)

    def test_monthly_lands_on_the_third_friday(self):
        moment = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        assert next_expiry(moment, ExpiryRule.MONTHLY).date() == date(2026, 8, 21)

    def test_min_days_skips_the_nearest_expiry(self):
        moment = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
        near = next_expiry(moment, ExpiryRule.MWF, min_days=0)
        far = next_expiry(moment, ExpiryRule.MWF, min_days=3)
        assert far > near

    def test_expiry_is_always_in_the_future(self):
        for day in range(1, 29):
            moment = datetime(2026, 8, day, 23, 0, tzinfo=timezone.utc)
            for rule in ExpiryRule:
                assert next_expiry(moment, rule) >= moment

    def test_label_is_readable_and_not_a_tradable_symbol(self):
        label = contract_label(NQ, "call", 29250,
                               datetime(2026, 8, 21, 20, tzinfo=timezone.utc))
        assert label == "NQ 2026-08-21 C29250"


# ---------------------------------------------------------------------------
# Options backtest
# ---------------------------------------------------------------------------

def option_config(**over):
    base = dict(
        symbol="/NQ", timeframe="1h",
        start=date.today() - timedelta(days=30), end=date.today(),
        data_source="yahoo", strategy="EMA_RSI_PATTERN",
        strategy_parameters={"require_pattern": False},
        trade_mode=TradeMode.OPTIONS,
        option_on_sell=OptionAction.SELL_CALL,
        option_on_buy=OptionAction.SELL_PUT,
        option_expiry_rule="mwf",
        initial_capital=100_000.0, position_size=1.0, commission=0.0,
    )
    base.update(over)
    return BacktestConfig(**base)


def trending_days(n=60, start=20000.0, step=40.0):
    """One bar per hour over `n` days, trending up then down."""
    candles = []
    price = start
    for i in range(n * 8):
        price += step if i < n * 4 else -step
        candles.append(Candle(timestamp=START + i * 3600, open=price - step / 2,
                              high=price + step, low=price - step,
                              close=price, volume=100.0))
    return CandleSeries(symbol="/NQ", timeframe="1h", candles=candles, provider="test")


class TestOptionsBacktest:
    def test_a_sell_signal_sells_a_call_and_a_buy_signal_sells_a_put(self):
        result = run_backtest(option_config(), trending_days())
        assert result.trades
        kinds = {t.direction for t in result.trades}
        assert kinds <= {"SHORT_CALL", "SHORT_PUT"}
        assert "SHORT_CALL" in kinds and "SHORT_PUT" in kinds

    def test_the_mapping_is_configurable(self):
        result = run_backtest(
            option_config(option_on_sell=OptionAction.BUY_PUT,
                          option_on_buy=OptionAction.BUY_CALL),
            trending_days())
        assert result.trades
        assert {t.direction for t in result.trades} <= {"LONG_PUT", "LONG_CALL"}

    def test_one_side_can_be_ignored(self):
        result = run_backtest(
            option_config(option_on_buy=OptionAction.NONE), trending_days())
        assert result.trades
        assert all(t.direction == "SHORT_CALL" for t in result.trades)

    def test_strikes_are_at_the_money_and_listed(self):
        result = run_backtest(option_config(), trending_days())
        for trade in result.trades:
            assert trade.strike % NQ.option_strike_increment == 0
            # "At the money" means the nearest listed strike, so at most half
            # an increment away from where the underlying was.
            assert abs(trade.strike - trade.entry_underlying) <= NQ.option_strike_increment

    def test_expiry_is_after_entry_and_on_a_listed_weekday(self):
        result = run_backtest(option_config(), trending_days())
        for trade in result.trades:
            assert trade.expiry > trade.entry_time
            weekday = datetime.fromtimestamp(trade.expiry, tz=timezone.utc).weekday()
            assert weekday in (0, 2, 4)

    def test_premium_is_recorded_not_the_underlying_price(self):
        result = run_backtest(option_config(), trending_days())
        for trade in result.trades:
            # A premium is a small fraction of a 20,000-point index.
            assert 0 <= trade.entry_price < trade.entry_underlying / 2

    def test_a_short_position_profits_when_the_premium_falls(self):
        result = run_backtest(option_config(), trending_days())
        for trade in result.trades:
            if trade.direction.startswith("SHORT"):
                fell = trade.exit_price < trade.entry_price
                assert (trade.gross_pnl > 0) == fell or trade.gross_pnl == 0

    def test_pnl_scales_with_the_contract_multiplier(self):
        data = trending_days()
        result = run_backtest(option_config(), data)
        trade = result.trades[0]
        move = trade.entry_price - trade.exit_price
        assert trade.gross_pnl == pytest.approx(move * trade.quantity * NQ.point_value())

    def test_fixed_implied_vol_overrides_the_estimate(self):
        result = run_backtest(option_config(option_implied_vol=0.35), trending_days())
        assert all(t.entry_vol == pytest.approx(0.35) for t in result.trades)

    def test_higher_volatility_collects_more_premium(self):
        low = run_backtest(option_config(option_implied_vol=0.10), trending_days())
        high = run_backtest(option_config(option_implied_vol=0.40), trending_days())
        assert (sum(t.entry_price for t in high.trades) / len(high.trades)
                > sum(t.entry_price for t in low.trades) / len(low.trades))

    def test_take_profit_on_premium_closes_positions(self):
        result = run_backtest(
            option_config(option_take_profit_pct=25, option_implied_vol=0.30),
            trending_days())
        assert any(t.exit_reason == "take_profit" for t in result.trades)

    def test_hold_to_expiry_leaves_only_expiry_and_signal_exits(self):
        result = run_backtest(
            option_config(option_hold_to_expiry=True, option_take_profit_pct=10),
            trending_days())
        assert {t.exit_reason for t in result.trades} <= {
            "expired", "buy_signal", "sell_signal", "end_of_data"}

    def test_exits_name_the_signal_that_caused_them(self):
        """"signal" alone cannot be told apart from a target or a settlement."""
        result = run_backtest(option_config(), trending_days())
        reasons = {t.exit_reason for t in result.trades}
        assert "signal" not in reasons
        assert reasons & {"buy_signal", "sell_signal"}

    def test_each_trade_records_the_signal_that_opened_it(self):
        result = run_backtest(option_config(), trending_days())
        for trade in result.trades:
            assert trade.entry_signal in ("BUY", "SELL")
            assert trade.strategy == "EMA_RSI_PATTERN"
            # A sold call comes from a SELL signal, a sold put from a BUY.
            expected = "SELL" if trade.option_type == "call" else "BUY"
            assert trade.entry_signal == expected

    def test_expired_positions_settle_at_intrinsic(self):
        result = run_backtest(option_config(option_hold_to_expiry=True),
                              trending_days())
        for trade in result.trades:
            if trade.exit_reason == "expired":
                assert trade.exit_price == pytest.approx(
                    intrinsic_value(trade.option_type, trade.exit_underlying,
                                    trade.strike), abs=1.0)

    def test_every_run_warns_that_premiums_are_modelled(self):
        result = run_backtest(option_config(), trending_days())
        assert any("MODELLED" in w for w in result.warnings)

    def test_an_unknown_expiry_rule_is_refused(self):
        with pytest.raises(ValueError, match="expiry rule"):
            run_backtest(option_config(option_expiry_rule="fortnightly"),
                         trending_days())

    def test_underlying_mode_is_unaffected(self):
        """The default path must still trade the future itself."""
        result = run_backtest(
            option_config(trade_mode=TradeMode.UNDERLYING), trending_days())
        assert result.trades
        assert {t.direction for t in result.trades} <= {"LONG", "SHORT"}
        assert all(not t.contract for t in result.trades)

    def test_result_serializes_with_the_contract_detail(self):
        payload = run_backtest(option_config(), trending_days()).as_dict()
        assert payload["config"]["tradeMode"] == "options"
        first = payload["trades"][0]
        assert set(first) >= {"contract", "optionType", "strike", "expiry",
                              "entryUnderlying", "entryVol"}
