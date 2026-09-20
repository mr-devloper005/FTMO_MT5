"""Robustness attack on /CL 1d, volatility breakout + trend regime, variant A.

Eight tests, none of which re-tunes anything. Every buy-and-hold figure is
recomputed inside whatever window the strategy is being measured over, so no
comparison ever spans two different periods.
"""
import json, logging, statistics, structlog
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
BASE = {"lookback": 20, "atr_period": 14, "atr_average": 20,
        "trend_period": 200, "trade_shorts": True, "exit_on_trend_cross": False}
SPEC = dict(strategy="VOL_BREAKOUT_TREND", allow_short=True,
            stop_loss_atr=2.0, take_profit_atr=3.0, strategy_parameters=dict(BASE))
C = data.load(SYM, TF)
ts = lambda t: dt.datetime.fromtimestamp(int(t), dt.UTC)


def spec(**over):
    s = json.loads(json.dumps(SPEC))
    p = over.pop("params", None)
    if p: s["strategy_parameters"].update(p)
    s.update(over)
    return s


def line(label, r, width=30):
    return (f"  {label:<{width}}{r['trades']:>6}{r['net']:>11,.0f}{r['bh']:>10,.0f}"
            f"{r['vsBH']:>11,.0f}{(r['pf'] or 0):>6.2f}{abs(r['maxDDPct']):>7.1f}"
            f"{r['winRate']:>7.1f}{r['topTradeShare']:>7.1f}"
            + ("  BEAT" if r["beatsBH"] else ""))


HEAD = (f"  {'':<30}{'trades':>6}{'net':>11}{'B&H':>10}{'vs B&H':>11}"
        f"{'PF':>6}{'DD%':>7}{'win%':>7}{'top1%':>7}")

base = harness.run(SYM, TF, SPEC, C)
print("  BASELINE\n"); print(HEAD); print(line("20 bars, 2/3 ATR, L+S", base)); print()

# ---- 1. breakout lookback -------------------------------------------------
print("\n  TEST 1 — breakout lookback sensitivity\n"); print(HEAD)
t1 = {}
for n in (10, 15, 20, 25, 30):
    r = harness.run(SYM, TF, spec(params={"lookback": n}), C)
    t1[n] = r; print(line(f"lookback {n} bars", r))

# ---- 2. stop / target -----------------------------------------------------
print("\n\n  TEST 2 — ATR stop and target sensitivity\n"); print(HEAD)
t2 = {}
for sl, tp in ((1.5, 2.5), (1.5, 3.0), (2.0, 3.0), (2.0, 4.0), (2.5, 4.0)):
    r = harness.run(SYM, TF, spec(stop_loss_atr=sl, take_profit_atr=tp), C)
    t2[f"{sl}/{tp}"] = r; print(line(f"stop {sl} / target {tp}", r))

# ---- 3. the volatility filter itself --------------------------------------
print("\n\n  TEST 3 — the ATR volatility filter\n"); print(HEAD)
t3 = {}
for lbl, p in (("ATR avg 10 bars", {"atr_average": 10}),
               ("ATR avg 15 bars", {"atr_average": 15}),
               ("ATR avg 20 bars", {"atr_average": 20}),
               ("ATR avg 30 bars", {"atr_average": 30}),
               ("ATR avg 40 bars", {"atr_average": 40}),
               ("ATR period 10", {"atr_period": 10}),
               ("ATR period 20", {"atr_period": 20}),
               ("filter off (avg 1 bar)", {"atr_average": 1})):
    r = harness.run(SYM, TF, spec(params=p), C)
    t3[lbl] = r; print(line(lbl, r))

# ---- 4. five-fold walk-forward -------------------------------------------
print("\n\n  TEST 4 — five-fold chronological walk-forward\n")
print(f"  {'fold':<30}{'from':>12}{'to':>12}{'trades':>7}{'net':>11}{'B&H':>10}"
      f"{'vs B&H':>11}{'PF':>6}")
t4, step, won = [], len(C) // 5, 0
for k in range(5):
    lo, hi = k * step, (k + 1) * step if k < 4 else len(C)
    seg = C[max(0, lo - 250):hi]
    r = harness.run(SYM, TF, SPEC, seg)
    t4.append(r); won += bool(r["beatsBH"])
    print(f"  {'fold ' + str(k+1):<30}{ts(C[lo].timestamp).strftime('%b %Y'):>12}"
          f"{ts(C[hi-1].timestamp).strftime('%b %Y'):>12}{r['trades']:>7}"
          f"{r['net']:>11,.0f}{r['bh']:>10,.0f}{r['vsBH']:>11,.0f}"
          f"{(r['pf'] or 0):>6.2f}" + ("  BEAT" if r["beatsBH"] else ""))
print(f"\n  beat buy & hold in {won} of 5 folds")

# ---- 5. regime analysis ---------------------------------------------------
print("\n\n  TEST 5 — bull / bear / sideways years\n")
print(f"  {'year':<8}{'/CL move':>10}{'regime':<12}{'trades':>7}{'net':>11}"
      f"{'B&H':>10}{'vs B&H':>11}")
years = {}
for i, c in enumerate(C):
    years.setdefault(ts(c.timestamp).year, []).append(i)
t5, agg = [], {}
for y in sorted(years):
    idx = years[y]
    if len(idx) < 150: continue
    lo, hi = idx[0], idx[-1] + 1
    seg = C[max(0, lo - 250):hi]
    r = harness.run(SYM, TF, SPEC, seg)
    # The year's own move decides its label; B&H over the year is the benchmark.
    move = (C[hi-1].close / C[lo].open - 1) * 100
    reg = "bull" if move > 15 else "bear" if move < -15 else "sideways"
    yr_bh = (C[hi-1].close - C[lo].open) * MULT
    # Strategy P&L inside the year only, from trades that closed in it.
    cfg = BacktestConfig(symbol=SYM, timeframe=TF, start=date.today()-timedelta(days=1),
        end=date.today(), data_source="yahoo", strategy=SPEC["strategy"],
        strategy_parameters=SPEC["strategy_parameters"], initial_capital=100_000.0,
        position_size=1.0, commission=2.50, slippage_ticks=1.0,
        allow_long=True, allow_short=True, stop_loss_atr=2.0, take_profit_atr=3.0)
    res = run_backtest(cfg, data.series(SYM, TF, C))
    net = sum(t.net_pnl for t in res.trades if ts(t.exit_time).year == y)
    n = sum(1 for t in res.trades if ts(t.exit_time).year == y)
    t5.append(dict(year=y, move=move, regime=reg, trades=n, net=net, bh=yr_bh))
    a = agg.setdefault(reg, {"years": 0, "trades": 0, "net": 0.0, "bh": 0.0})
    a["years"] += 1; a["trades"] += n; a["net"] += net; a["bh"] += yr_bh
    print(f"  {y:<8}{move:>9.1f}%{reg:<12}{n:>7}{net:>11,.0f}{yr_bh:>10,.0f}"
          f"{net - yr_bh:>11,.0f}" + ("  BEAT" if net > yr_bh else ""))
print(f"\n  {'regime':<12}{'years':>7}{'trades':>8}{'strategy':>12}{'buy & hold':>12}"
      f"{'vs B&H':>11}")
for reg in ("bull", "sideways", "bear"):
    if reg in agg:
        a = agg[reg]
        print(f"  {reg:<12}{a['years']:>7}{a['trades']:>8}{a['net']:>12,.0f}"
              f"{a['bh']:>12,.0f}{a['net']-a['bh']:>11,.0f}"
              + ("  BEAT" if a["net"] > a["bh"] else ""))

# ---- 6. remove the biggest winners ---------------------------------------
print("\n\n  TEST 6 — with the biggest winning trades removed\n")
cfg = BacktestConfig(symbol=SYM, timeframe=TF, start=date.today()-timedelta(days=1),
    end=date.today(), data_source="yahoo", strategy=SPEC["strategy"],
    strategy_parameters=SPEC["strategy_parameters"], initial_capital=100_000.0,
    position_size=1.0, commission=2.50, slippage_ticks=1.0,
    allow_long=True, allow_short=True, stop_loss_atr=2.0, take_profit_atr=3.0)
res = run_backtest(cfg, data.series(SYM, TF, C))
pnls = sorted((t.net_pnl for t in res.trades), reverse=True)
bh = base["bh"]
print(f"  {'':<30}{'trades':>7}{'net':>11}{'B&H':>10}{'vs B&H':>11}{'PF':>6}")
t6 = {}
for k in (0, 1, 3, 5):
    kept = pnls[k:]
    net = sum(kept)
    gp = sum(x for x in kept if x > 0); gl = -sum(x for x in kept if x < 0)
    pf = gp / gl if gl else 0
    t6[k] = dict(net=net, vsBH=net - bh, pf=pf, beats=net > bh)
    lbl = "all trades" if k == 0 else f"top {k} winner{'s' if k > 1 else ''} removed"
    print(f"  {lbl:<30}{len(kept):>7}{net:>11,.0f}{bh:>10,.0f}{net-bh:>11,.0f}"
          f"{pf:>6.2f}" + ("  BEAT" if net > bh else ""))

# ---- 7. direction ---------------------------------------------------------
print("\n\n  TEST 7 — long only vs short only vs long and short\n"); print(HEAD)
t7 = {}
for lbl, cfg2 in (("long + short", dict(allow_long=True, allow_short=True,
                                        params={"trade_shorts": True})),
                  ("long only", dict(allow_long=True, allow_short=False,
                                     params={"trade_shorts": False})),
                  ("short only", dict(allow_long=False, allow_short=True,
                                      params={"trade_shorts": True}))):
    r = harness.run(SYM, TF, spec(**cfg2), C)
    t7[lbl] = r; print(line(lbl, r))

Path("study/lab/attack.json").write_text(json.dumps({
    "baseline": base, "lookback": {str(k): v for k, v in t1.items()},
    "stopTarget": t2, "volFilter": t3, "folds": t4, "years": t5,
    "aggregate": agg, "topRemoved": {str(k): v for k, v in t6.items()},
    "direction": t7}, indent=1))
print("\n\n  written study/lab/attack.json")
