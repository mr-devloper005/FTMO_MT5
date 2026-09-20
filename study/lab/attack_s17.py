"""S17 attacked. It is the best of nineteen variants, so its margin is inflated
by that selection before a single robustness test is run. These tests exist to
find out how much of it survives that fact.

  S17: short only, 20-bar low break, ATR above its 20-bar average, close below
       EMA 200, stop 2 x ATR, no target, 3 x ATR trailing stop.
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

SYM, TF = "/CL", "1d"
MULT = resolve_instrument(SYM).contract_multiplier
CORE = {"lookback":20,"atr_period":14,"atr_average":20,"trend_period":200,
        "trade_shorts":True,"exit_on_trend_cross":False,
        "atr_multiple":1.0,"adx_min":0.0,"atr_rising_bars":0}
S17 = dict(strategy="VOL_BREAKOUT_TREND", allow_long=False, allow_short=True,
           stop_loss_atr=2.0, trailing_stop_atr=3.0,
           strategy_parameters=dict(CORE))
C = data.load(SYM, TF)
ts = lambda t: dt.datetime.fromtimestamp(int(t), dt.UTC)


def S(**o):
    s = json.loads(json.dumps(S17))
    p = o.pop("params", None)
    if p: s["strategy_parameters"].update(p)
    s.update(o); return s


H = (f"  {'':<28}{'tr':>5}{'net':>11}{'B&H':>10}{'vs B&H':>11}{'PF':>6}"
     f"{'DD%':>7}{'win%':>7}{'top1%':>7}")
L = lambda n, r: (f"  {n:<28}{r['trades']:>5}{r['net']:>11,.0f}{r['bh']:>10,.0f}"
                 f"{r['vsBH']:>11,.0f}{(r['pf'] or 0):>6.2f}{abs(r['maxDDPct']):>7.1f}"
                 f"{r['winRate']:>7.1f}{r['topTradeShare']:>7.1f}"
                 + ("  BEAT" if r["beatsBH"] else ""))

base = harness.run(SYM, TF, S17, C)
print("  BASELINE S17\n"); print(H); print(L("short, trail 3 ATR", base))

print("\n\n  TRAILING STOP SENSITIVITY — the parameter S17 was selected on\n"); print(H)
t_trail = {}
for t in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0):
    r = harness.run(SYM, TF, S(trailing_stop_atr=t), C); t_trail[t] = r
    print(L(f"trail {t} ATR", r))

print("\n\n  BREAKOUT LOOKBACK\n"); print(H)
t_lb = {}
for n in (10, 15, 20, 25, 30):
    r = harness.run(SYM, TF, S(params={"lookback": n}), C); t_lb[n] = r
    print(L(f"lookback {n}", r))

print("\n\n  EMA REGIME\n"); print(H)
t_ema = {}
for p in (50, 100, 200, 300, 400):
    r = harness.run(SYM, TF, S(params={"trend_period": p}), C); t_ema[p] = r
    print(L(f"EMA {p}", r))

print("\n\n  INITIAL STOP\n"); print(H)
t_stop = {}
for sl in (1.5, 2.0, 2.5, 3.0):
    r = harness.run(SYM, TF, S(stop_loss_atr=sl), C); t_stop[sl] = r
    print(L(f"stop {sl} ATR", r))

print("\n\n  FIVE-FOLD WALK-FORWARD\n")
print(f"  {'fold':<28}{'from':>10}{'to':>10}{'tr':>5}{'net':>11}{'B&H':>10}"
      f"{'vs B&H':>11}{'PF':>6}")
folds, step, won = [], len(C)//5, 0
for k in range(5):
    lo, hi = k*step, (k+1)*step if k < 4 else len(C)
    r = harness.run(SYM, TF, S17, C[max(0, lo-250):hi]); folds.append(r)
    won += bool(r["beatsBH"])
    print(f"  {'fold '+str(k+1):<28}{ts(C[lo].timestamp).strftime('%b %y'):>10}"
          f"{ts(C[hi-1].timestamp).strftime('%b %y'):>10}{r['trades']:>5}"
          f"{r['net']:>11,.0f}{r['bh']:>10,.0f}{r['vsBH']:>11,.0f}"
          f"{(r['pf'] or 0):>6.2f}" + ("  BEAT" if r["beatsBH"] else ""))
print(f"\n  beat buy & hold in {won} of 5 folds")

print("\n\n  BIGGEST WINNERS REMOVED\n")
cfg = BacktestConfig(symbol=SYM, timeframe=TF, start=date.today()-timedelta(days=1),
    end=date.today(), data_source="yahoo", strategy=S17["strategy"],
    strategy_parameters=S17["strategy_parameters"], initial_capital=100_000.0,
    position_size=1.0, commission=2.50, slippage_ticks=1.0,
    allow_long=False, allow_short=True, stop_loss_atr=2.0, trailing_stop_atr=3.0)
res = run_backtest(cfg, data.series(SYM, TF, C))
pnls = sorted((t.net_pnl for t in res.trades), reverse=True)
bh = base["bh"]
print(f"  {'':<28}{'tr':>5}{'net':>11}{'B&H':>10}{'vs B&H':>11}{'PF':>6}")
top = {}
for k in (0, 1, 3, 5):
    kept = pnls[k:]; net = sum(kept)
    gp = sum(x for x in kept if x > 0); gl = -sum(x for x in kept if x < 0)
    top[k] = dict(net=net, vsBH=net-bh, pf=gp/gl if gl else 0)
    lbl = "all trades" if k == 0 else f"top {k} removed"
    print(f"  {lbl:<28}{len(kept):>5}{net:>11,.0f}{bh:>10,.0f}{net-bh:>11,.0f}"
          f"{(gp/gl if gl else 0):>6.2f}" + ("  BEAT" if net > bh else ""))

print("\n\n  REGIME\n")
years, agg = {}, {}
for i, c in enumerate(C):
    years.setdefault(ts(c.timestamp).year, []).append(i)
for y in sorted(years):
    idx = years[y]
    if len(idx) < 150: continue
    lo, hi = idx[0], idx[-1]+1
    move = (C[hi-1].close / C[lo].open - 1) * 100
    reg = "bull" if move > 15 else "bear" if move < -15 else "sideways"
    yr_bh = (C[hi-1].close - C[lo].open) * MULT
    net = sum(t.net_pnl for t in res.trades if ts(t.exit_time).year == y)
    n = sum(1 for t in res.trades if ts(t.exit_time).year == y)
    a = agg.setdefault(reg, {"years":0,"trades":0,"net":0.0,"bh":0.0})
    a["years"] += 1; a["trades"] += n; a["net"] += net; a["bh"] += yr_bh
print(f"  {'regime':<12}{'years':>7}{'trades':>8}{'strategy':>12}{'buy & hold':>12}"
      f"{'vs B&H':>11}")
for reg in ("bull","sideways","bear"):
    a = agg[reg]
    print(f"  {reg:<12}{a['years']:>7}{a['trades']:>8}{a['net']:>12,.0f}"
          f"{a['bh']:>12,.0f}{a['net']-a['bh']:>11,.0f}"
          + ("  BEAT" if a['net'] > a['bh'] else ""))

Path("study/lab/attack_s17.json").write_text(json.dumps({
    "baseline": base, "trail": {str(k): v for k, v in t_trail.items()},
    "lookback": {str(k): v for k, v in t_lb.items()},
    "ema": {str(k): v for k, v in t_ema.items()},
    "stop": {str(k): v for k, v in t_stop.items()},
    "folds": folds, "topRemoved": {str(k): v for k, v in top.items()},
    "regime": agg}, indent=1))
n = sum(1 for d in (t_trail, t_lb, t_ema, t_stop) for r in d.values() if r["beatsBH"])
tot = sum(len(d) for d in (t_trail, t_lb, t_ema, t_stop))
print(f"\n\n  parameter variations that still beat buy & hold: {n} of {tot}")
