"""The one candidate from Strategy 6: regime switch on /GC 8h.

Its margin over buy and hold is $1,526 on $212,405 — seven tenths of one
percent. That is inside the noise of any single assumption, so these tests are
less about whether it is robust and more about how quickly the margin
disappears when anything is touched.
"""
import json, logging, structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
import datetime as dt
from datetime import date, timedelta
from pathlib import Path
import study.lab.strats
from study.lab import harness, data
from ftmo_mt5.backtest import BacktestConfig, run_backtest
from ftmo_mt5.instruments import resolve_instrument

SYM, TF = "/GC", "8h"
MULT = resolve_instrument(SYM).contract_multiplier
CORE = {"mid_period":50,"slow_period":200,"breakout_lookback":20,
        "atr_period":14,"trade_shorts":True}
SPEC = dict(strategy="REGIME_SWITCH_LS", allow_long=True, allow_short=True,
            stop_loss_atr=2.0, trailing_stop_atr=2.0, strategy_parameters=dict(CORE))
C = data.load(SYM, TF)
ts = lambda t: dt.datetime.fromtimestamp(int(t), dt.UTC)

def S(**o):
    s = json.loads(json.dumps(SPEC))
    p = o.pop("params", None)
    if p: s["strategy_parameters"].update(p)
    s.update(o); return s

H = (f"  {'':<28}{'tr':>5}{'net':>11}{'B&H':>11}{'vs B&H':>10}{'PF':>6}"
     f"{'DD%':>7}{'win%':>7}{'top1%':>7}")
L = lambda n, r: (f"  {n:<28}{r['trades']:>5}{r['net']:>11,.0f}{r['bh']:>11,.0f}"
                 f"{r['vsBH']:>10,.0f}{(r['pf'] or 0):>6.2f}{abs(r['maxDDPct']):>7.1f}"
                 f"{r['winRate']:>7.1f}{r['topTradeShare']:>7.1f}"
                 + ("  BEAT" if r["beatsBH"] else ""))

base = harness.run(SYM, TF, SPEC, C)
print("  BASELINE\n"); print(H); print(L("regime switch, /GC 8h", base))

print("\n\n  PARAMETER SENSITIVITY\n"); print(H)
sens = {}
for lbl, kind, val in (
    ("EMA 50/100", "params", {"slow_period":100}),
    ("EMA 50/300", "params", {"slow_period":300}),
    ("EMA 20/200", "params", {"mid_period":20}),
    ("EMA 100/200", "params", {"mid_period":100}),
    ("short channel 10", "params", {"breakout_lookback":10}),
    ("short channel 15", "params", {"breakout_lookback":15}),
    ("short channel 30", "params", {"breakout_lookback":30}),
    ("stop 1.5 ATR", "cfg", {"stop_loss_atr":1.5}),
    ("stop 3 ATR", "cfg", {"stop_loss_atr":3.0}),
    ("trail 1.5 ATR", "cfg", {"trailing_stop_atr":1.5}),
    ("trail 3 ATR", "cfg", {"trailing_stop_atr":3.0}),
    ("long only", "cfg", {"allow_short":False}),
):
    s = S(params=val) if kind == "params" else S(**val)
    r = harness.run(SYM, TF, s, C); sens[lbl] = r; print(L(lbl, r))

print("\n\n  FIVE-FOLD WALK-FORWARD\n")
print(f"  {'fold':<28}{'from':>10}{'to':>10}{'tr':>5}{'net':>11}{'B&H':>11}"
      f"{'vs B&H':>10}{'PF':>6}")
folds, step, won = [], len(C)//5, 0
for k in range(5):
    lo, hi = k*step, (k+1)*step if k < 4 else len(C)
    r = harness.run(SYM, TF, SPEC, C[max(0, lo-250):hi]); folds.append(r)
    won += bool(r["beatsBH"])
    print(f"  {'fold '+str(k+1):<28}{ts(C[lo].timestamp).strftime('%b %y'):>10}"
          f"{ts(C[hi-1].timestamp).strftime('%b %y'):>10}{r['trades']:>5}"
          f"{r['net']:>11,.0f}{r['bh']:>11,.0f}{r['vsBH']:>10,.0f}"
          f"{(r['pf'] or 0):>6.2f}" + ("  BEAT" if r["beatsBH"] else ""))
print(f"\n  beat buy & hold in {won} of 5 folds")

print("\n\n  BIGGEST WINNERS REMOVED\n")
cfg = BacktestConfig(symbol=SYM, timeframe=data.ENGINE_TF.get(TF, TF),
    start=date.today()-timedelta(days=1), end=date.today(), data_source="yahoo",
    strategy=SPEC["strategy"], strategy_parameters=SPEC["strategy_parameters"],
    initial_capital=100_000.0, position_size=1.0, commission=2.50, slippage_ticks=1.0,
    allow_long=True, allow_short=True, stop_loss_atr=2.0, trailing_stop_atr=2.0)
res = run_backtest(cfg, data.series(SYM, TF, C))
pnls = sorted((t.net_pnl for t in res.trades), reverse=True)
bh = base["bh"]
print(f"  {'':<28}{'tr':>5}{'net':>11}{'B&H':>11}{'vs B&H':>10}{'PF':>6}")
top = {}
for k in (0, 1, 3, 5):
    kept = pnls[k:]; net = sum(kept)
    gp = sum(x for x in kept if x > 0); gl = -sum(x for x in kept if x < 0)
    top[k] = dict(net=net, vsBH=net-bh)
    lbl = "all trades" if k == 0 else f"top {k} removed"
    print(f"  {lbl:<28}{len(kept):>5}{net:>11,.0f}{bh:>11,.0f}{net-bh:>10,.0f}"
          f"{(gp/gl if gl else 0):>6.2f}" + ("  BEAT" if net > bh else ""))

print("\n\n  REGIME BREAKDOWN (by year)\n")
years, agg = {}, {}
for i, c in enumerate(C):
    years.setdefault(ts(c.timestamp).year, []).append(i)
print(f"  {'year':<8}{'/GC move':>10}{'regime':<12}{'trades':>7}{'net':>11}"
      f"{'B&H':>11}{'vs B&H':>11}")
for y in sorted(years):
    idx = years[y]
    if len(idx) < 60: continue
    lo, hi = idx[0], idx[-1]+1
    move = (C[hi-1].close / C[lo].open - 1) * 100
    reg = "bull" if move > 10 else "bear" if move < -10 else "sideways"
    yr_bh = (C[hi-1].close - C[lo].open) * MULT
    net = sum(t.net_pnl for t in res.trades if ts(t.exit_time).year == y)
    n = sum(1 for t in res.trades if ts(t.exit_time).year == y)
    a = agg.setdefault(reg, {"years":0,"trades":0,"net":0.0,"bh":0.0})
    a["years"] += 1; a["trades"] += n; a["net"] += net; a["bh"] += yr_bh
    print(f"  {y:<8}{move:>9.1f}%{reg:<12}{n:>7}{net:>11,.0f}{yr_bh:>11,.0f}"
          f"{net-yr_bh:>11,.0f}" + ("  BEAT" if net > yr_bh else ""))
print(f"\n  {'regime':<12}{'years':>7}{'trades':>8}{'strategy':>12}{'buy & hold':>12}"
      f"{'vs B&H':>11}")
for reg in ("bull","sideways","bear"):
    if reg in agg:
        a = agg[reg]
        print(f"  {reg:<12}{a['years']:>7}{a['trades']:>8}{a['net']:>12,.0f}"
              f"{a['bh']:>12,.0f}{a['net']-a['bh']:>11,.0f}"
              + ("  BEAT" if a['net'] > a['bh'] else ""))

Path("study/lab/attack_gc8h.json").write_text(json.dumps(
    {"baseline": base, "sensitivity": sens, "folds": folds,
     "topRemoved": {str(k): v for k, v in top.items()}, "regime": agg}, indent=1))
n = sum(1 for r in sens.values() if r["beatsBH"])
print(f"\n\n  parameter variations that still beat: {n} of {len(sens)}")
