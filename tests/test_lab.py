"""The lab's two claims, tested: the guardrails fire, and the grade is calibrated.

The rubric exists to reproduce judgements a person made by hand over a long
research session. That is a property worth pinning down, because a threshold
edited later would otherwise silently start disagreeing with the reasoning it
was derived from — and the whole point of a printed verdict is that the same
evidence always earns the same word.
"""
import pytest

from ftmo_mt5.lab import metrics, service, verdict, windows

# The three candidates that session graded by hand, with the exact
# configurations it graded them under — stops included, which is what makes
# the /CL run 163 trades rather than 54.
VOL_BREAKOUT = {"lookback": 20, "atr_period": 14, "atr_average": 20,
                "trend_period": 200, "trade_shorts": True,
                "exit_on_trend_cross": False}

CALIBRATION = [
    ("/CL", "1d", "lookback", "MODERATE",
     dict(strategy="VOL_BREAKOUT_TREND", strategy_parameters=dict(VOL_BREAKOUT),
          allow_long=True, allow_short=True, stop_loss_atr=2.0, take_profit_atr=3.0)),
    ("/CL", "1d", "lookback", "FRAGILE",
     dict(strategy="VOL_BREAKOUT_TREND", strategy_parameters=dict(VOL_BREAKOUT),
          allow_long=False, allow_short=True, stop_loss_atr=2.0, take_profit_atr=3.0)),
    ("/GC", "8h", "slow_period", "FAILED",
     dict(strategy="REGIME_SWITCH_LS", allow_long=True, allow_short=True,
          stop_loss_atr=2.0, trailing_stop_atr=2.0,
          strategy_parameters={"mid_period": 50, "slow_period": 200,
                               "breakout_lookback": 20, "atr_period": 14,
                               "trade_shorts": True})),
]


@pytest.mark.parametrize("symbol,timeframe,param_key,expected,spec", CALIBRATION)
def test_rubric_reproduces_the_hand_graded_verdicts(symbol, timeframe, param_key,
                                                    expected, spec):
    got = service.validate(symbol, timeframe, spec, None, param_key)
    assert got["verdict"] == expected, (
        f"{symbol} {timeframe} graded {got['verdict']}, was hand-graded {expected}: "
        f"{got['gates']}")


def test_flat_benchmark_is_not_an_opponent():
    """Beating a benchmark that went nowhere is not evidence, and says so."""
    warnings = service.warnings_for({"bh": 120.0, "trades": 40, "topTradeShare": 10})
    assert any("flat benchmark" in w.lower() or "went nowhere" in w for w in warnings)


def test_a_window_shorter_than_its_own_lookback_is_flagged():
    warnings = service.warnings_for(
        {"bh": 50_000.0, "trades": 40, "topTradeShare": 10,
         "bars": 250, "warmupBars": 200, "usableBars": 50})
    assert any("too short" in w for w in warnings)


def test_one_trade_carrying_the_profit_is_flagged():
    warnings = service.warnings_for({"bh": 50_000.0, "trades": 40, "topTradeShare": 78})
    assert any("single trade" in w for w in warnings)


def test_warmup_is_read_from_the_parameter_names():
    assert metrics.warmup_bars({"sma_period": 200, "buffer_pct": 1.0}) == 200
    assert metrics.warmup_bars({"trend_slow": 100, "atr_period": 14}) == 100
    assert metrics.warmup_bars({"trade_shorts": True}) == 0


def test_a_matrix_over_the_cap_is_refused_with_the_dimension_to_cut():
    with pytest.raises(service.TooManyCells) as e:
        service.plan(["/NQ", "/GC", "/CL"], windows.TIMEFRAMES, 600,
                     "sma_period", list(range(20)))
    assert "parameter values" in str(e.value)


def test_a_matrix_downloads_each_series_once():
    """Two cells over the same symbol and timeframe must share one download."""
    plan = service.plan(["/NQ"], ["1d"], 300, "sma_period", [50, 100, 200])
    assert plan["cells"] == 3 and plan["series"] == 1


def test_the_split_never_spans_two_windows():
    """Each half is judged against a buy & hold recomputed inside that half."""
    candles = windows.window("/NQ", "1d", 600)["candles"]
    spec = {"strategy": "DAILY_SWING_SMA200",
            "strategy_parameters": {"sma_period": 100}}
    out = verdict.validate("/NQ", "1d", spec, candles, "sma_period")
    assert out["dev"]["bh"] != out["oos"]["bh"]
    assert out["dev"]["bars"] + out["oos"]["bars"] == out["full"]["bars"]
