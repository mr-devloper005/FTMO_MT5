"""Batch 1 — the ten existing strategies on all three markets and timeframes.

These are not new ideas. They are run here because the project had only ever
tested them on gold: whether any of them behaves differently on an index or on
a commodity is information the research loop needs before inventing anything.
"""
from study.lab import harness, board, data

TREND = {"30m": 60, "1h": 240, "4h": 1440}

def specs(tf):
    m = {"30m": 30, "1h": 60, "4h": 240}[tf]
    t = TREND[tf]
    return [
     ("EMA trend following", dict(strategy="EMA_REGIME_LS", allow_short=True,
        trailing_stop_atr=2.0, strategy_parameters={"period": 100, "trade_shorts": True})),
     ("EMA + RSI pullback", dict(strategy="EMA_RSI_PULLBACK", stop_loss_atr=2.0,
        take_profit_atr=3.0, strategy_parameters={"trend_minutes": t})),
     ("EMA+RSI+ATR risk mgmt", dict(strategy="TREND_REGIME_PULLBACK",
        strategy_parameters={"trend_minutes": t})),
     ("Multi-timeframe EMA trend", dict(strategy="MTF_GREEN_STACK", allow_short=True,
        trailing_stop_atr=2.0, strategy_parameters={"tf1_minutes": m, "tf2_minutes": m*2,
        "tf3_minutes": m*4, "require_completed": True, "trade_shorts": True,
        "exit_on_condition": True})),
     ("EMA crossover + RSI", dict(strategy="EMA_RSI_PATTERN", allow_short=True,
        stop_loss_atr=2.0, take_profit_atr=3.0,
        strategy_parameters={"fast_period": 20, "slow_period": 50,
        "rsi_buy_above": 50.0, "rsi_sell_below": 50.0, "require_pattern": True})),
     ("Trend regime filter (SMA200)", dict(strategy="TREND_REGIME",
        strategy_parameters={"regime_period": 200, "buffer_pct": 0.0})),
     ("Four-regime breakout", dict(strategy="FOUR_REGIME", trailing_stop_atr=2.0,
        strategy_parameters={"trend_minutes": t})),
     ("Donchian breakout 55/20", dict(strategy="DONCHIAN_BREAKOUT", allow_short=True,
        strategy_parameters={"entry_lookback": 55, "exit_lookback": 20})),
     ("Mean reversion + RSI", dict(strategy="MEAN_REVERSION", allow_short=True,
        stop_loss_atr=3.0, strategy_parameters={"period": 100, "entry_z": 2.5,
        "exit_z": 0.5, "trade_shorts": True})),
     ("ATR-managed trend (master)", dict(strategy="GOLD_MASTER_TREND",
        trailing_stop_atr=2.0, breakeven_at_atr=1.0, trail_activate_atr=2.0,
        strategy_parameters={"trend_minutes": t})),
    ]


def main():
    n = 0
    for sym in ("/NQ", "/GC", "/CL"):
        for tf in ("30m", "1h", "4h"):
            c = data.load(sym, tf)
            print(f"\n  {sym} {tf}  ({len(c)} bars)")
            for name, spec in specs(tf):
                n += 1
                try:
                    r = harness.run(sym, tf, spec, c)
                except Exception as e:
                    print(f"  #{n:>3} {sym:<4}{tf:>4} {name:<34} FAILED {str(e)[:60]}")
                    continue
                board.add(batch="baseline", symbol=sym, tf=tf, name=name,
                          spec={k: v for k, v in spec.items()}, result=r)
                print(board.line(n, sym, tf, name, r))


if __name__ == "__main__":
    main()
