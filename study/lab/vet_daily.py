"""Gate the daily-bar winners from batch 7."""
import json
from pathlib import Path
import study.lab.strats
from study.lab import vet

CAND = [
 ("xrsi", "/CL", "EMA crossover + RSI",
  dict(strategy="EMA_RSI_PATTERN", allow_short=True, stop_loss_atr=2.0,
       take_profit_atr=3.0, strategy_parameters={"fast_period":20,"slow_period":50,
       "rsi_buy_above":50.0,"rsi_sell_below":50.0,"require_pattern":True}),
  [("EMA 10/30",{"fast_period":10,"slow_period":30}),
   ("EMA 15/40",{"fast_period":15,"slow_period":40}),
   ("EMA 30/80",{"fast_period":30,"slow_period":80}),
   ("RSI 45/55",{"rsi_buy_above":45.0,"rsi_sell_below":55.0}),
   ("RSI 55/45",{"rsi_buy_above":55.0,"rsi_sell_below":45.0}),
   ("no pattern",{"require_pattern":False})]),

 ("squeeze", "/CL", "Volatility squeeze breakout",
  dict(strategy="SQUEEZE_BREAKOUT", allow_short=True, trailing_stop_atr=2.5,
       strategy_parameters={"bb_period":20,"squeeze_pct":0.25,"rank_window":250,
       "range_lookback":20,"trend_period":100,"trade_shorts":True}),
  [("squeeze 15%",{"squeeze_pct":0.15}),("squeeze 35%",{"squeeze_pct":0.35}),
   ("squeeze 50%",{"squeeze_pct":0.50}),("range 10",{"range_lookback":10}),
   ("range 30",{"range_lookback":30}),("trend EMA 50",{"trend_period":50}),
   ("trend EMA 200",{"trend_period":200}),("BB 30",{"bb_period":30})]),

 ("turtle", "/CL", "Turtle 20/10 long+short",
  dict(strategy="TURTLE_LS", allow_short=True, stop_loss_atr=2.0,
       strategy_parameters={"entry_lookback":20,"exit_lookback":10,"trade_shorts":True}),
  [("entry 15",{"entry_lookback":15}),("entry 30",{"entry_lookback":30}),
   ("entry 40",{"entry_lookback":40}),("exit 5",{"exit_lookback":5}),
   ("exit 15",{"exit_lookback":15}),("exit 20",{"exit_lookback":20})]),
]

if __name__ == "__main__":
    store = {}
    for key, sym, name, spec, var in CAND:
        v = vet.vet(sym, "1d", name, spec, var)
        store[key] = v
        print(vet.report(v))
    Path("study/lab/vet_daily.json").write_text(json.dumps(store, indent=1))
