"""Gate the matrix winners that have not yet faced unseen data.

Six of the twenty quality winners were never split. Each is run here on a
chronological 70/30 split, in thirds, against neighbouring parameters, and at
higher costs, with the parameters locked before the unseen half is touched.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import vet

CAND = [
 ("mtf_cl_8h", "/CL", "8h", "Multi-timeframe EMA trend",
  dict(strategy="MTF_GREEN_STACK", allow_short=True, trailing_stop_atr=2.0,
       strategy_parameters={"tf1_minutes":480,"tf2_minutes":960,"tf3_minutes":1920,
       "require_completed":True,"trade_shorts":True,"exit_on_condition":True}),
  [("legs 480/1440/2880",{"tf2_minutes":1440,"tf3_minutes":2880}),
   ("legs 480/960/2880",{"tf3_minutes":2880}),
   ("legs 480/1920/3840",{"tf2_minutes":1920,"tf3_minutes":3840}),
   ("trail 1.5 ATR",{}), ("two legs only",{"tf3_minutes":960})]),

 ("macd_cl_6h", "/CL", "6h", "MACD + ADX trend",
  dict(strategy="MACD_ADX", allow_short=True,
       strategy_parameters={"adx_min":25.0,"adx_exit":15.0,"trade_shorts":True}),
  [("ADX in 20",{"adx_min":20.0}),("ADX in 30",{"adx_min":30.0}),
   ("ADX out 10",{"adx_exit":10.0}),("ADX out 20",{"adx_exit":20.0}),
   ("MACD 8/21/5",{"fast":8,"slow":21,"signal":5}),
   ("MACD 16/34/12",{"fast":16,"slow":34,"signal":12})]),

 ("squeeze_cl_1d", "/CL", "1d", "Volatility squeeze breakout",
  dict(strategy="SQUEEZE_BREAKOUT", allow_short=True, trailing_stop_atr=2.5,
       strategy_parameters={"bb_period":20,"squeeze_pct":0.25,"rank_window":250,
       "range_lookback":20,"trend_period":100,"trade_shorts":True}),
  [("squeeze 15%",{"squeeze_pct":0.15}),("squeeze 35%",{"squeeze_pct":0.35}),
   ("range 10",{"range_lookback":10}),("range 30",{"range_lookback":30}),
   ("trend EMA 50",{"trend_period":50}),("trend EMA 200",{"trend_period":200})]),

 ("turtle_cl_1d", "/CL", "1d", "Turtle 20/10 long+short",
  dict(strategy="TURTLE_LS", allow_short=True, stop_loss_atr=2.0,
       strategy_parameters={"entry_lookback":20,"exit_lookback":10,"trade_shorts":True}),
  [("entry 30",{"entry_lookback":30}),("entry 40",{"entry_lookback":40}),
   ("exit 15",{"exit_lookback":15}),("exit 20",{"exit_lookback":20}),
   ("entry 55 exit 20",{"entry_lookback":55,"exit_lookback":20})]),

 ("mtf_cl_4h", "/CL", "4h", "Multi-timeframe EMA trend",
  dict(strategy="MTF_GREEN_STACK", allow_short=True, trailing_stop_atr=2.0,
       strategy_parameters={"tf1_minutes":240,"tf2_minutes":480,"tf3_minutes":960,
       "require_completed":True,"trade_shorts":True,"exit_on_condition":True}),
  [("legs 240/720/1440",{"tf2_minutes":720,"tf3_minutes":1440}),
   ("legs 240/480/1440",{"tf3_minutes":1440}),
   ("legs 240/960/1920",{"tf2_minutes":960,"tf3_minutes":1920})]),

 ("xrsi_cl_1d_long", "/CL", "1d", "EMA crossover + RSI, long only",
  dict(strategy="EMA_RSI_PATTERN", allow_short=False, stop_loss_atr=2.0,
       take_profit_atr=3.0, strategy_parameters={"fast_period":20,"slow_period":50,
       "rsi_buy_above":50.0,"rsi_sell_below":50.0,"require_pattern":True}),
  [("EMA 15/40",{"fast_period":15,"slow_period":40}),
   ("EMA 30/80",{"fast_period":30,"slow_period":80}),
   ("no pattern",{"require_pattern":False})]),
]

if __name__ == "__main__":
    store, summary = {}, []
    for key, sym, tf, name, spec, var in CAND:
        v = vet.vet(sym, tf, name, spec, var)
        store[key] = v
        s = v["split"]; th = sum(1 for x in v["thirds"] if x["beatsBH"])
        sens = sum(1 for x in v["sensitivity"] if x["result"]["beatsBH"])
        costs = [int(k) for k, x in v["costs"].items() if x["beatsBH"]]
        summary.append((f"{sym} {tf}", name, s["full"], s["dev"], s["oos"], th,
                        len(v["thirds"]), sens, len(v["sensitivity"]),
                        max(costs) if costs else 0))
        print(vet.report(v))
    Path("study/lab/vet_matrix.json").write_text(json.dumps(store, indent=1))

    print("\n\n  ==================== GATE SUMMARY ====================\n")
    print(f"  {'cell':<9}{'strategy':<28}{'full':>10}{'dev':>10}{'oos':>11}"
          f"{'OOS PF':>8}{'thirds':>8}{'params':>8}{'cost':>7}  verdict")
    for cell, name, f, d, o, th, thn, se, sen, cost in summary:
        ok = o["beatsBH"] and th >= 2 and se >= sen * 0.6
        print(f"  {cell:<9}{name[:27]:<28}{f['vsBH']:>10,.0f}{d['vsBH']:>10,.0f}"
              f"{o['vsBH']:>11,.0f}{(o['pf'] or 0):>8.2f}{f'{th}/{thn}':>8}"
              f"{f'{se}/{sen}':>8}{f'{cost}t':>7}  "
              f"{'SURVIVED' if ok else 'FAILED OUT-OF-SAMPLE' if not o['beatsBH'] else 'PARTIAL'}")
