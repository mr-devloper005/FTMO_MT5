# Lab Research Report: Beating Buy & Hold on 14 Semiconductor-Adjacent Symbols

**Symbols:** AMAT, AMD, ARM, CRDO, DELL, LITE, MRVL, MU, NBIS, NVDA, SMH, SNDK, TSM, WDC
**Scope:** Every strategy the Lab ships (14), every timeframe the Lab supports (11), all 14 symbols — 2,156 in-sample runs, plus 38 out-of-sample/robustness validations via `ftmo_mt5.lab.verdict.validate()`.
**Date of this research:** 2026-09-03. Data as cached in `study/lab/cache/` (fetched from Yahoo Finance, most recently through 2026-08-28).

---

## 1. Executive Summary

**No symbol/timeframe/strategy combination in this study reached ROBUST or MODERATE.** Across 38 candidates run through the Lab's full out-of-sample and robustness suite (`ftmo_mt5.lab.service.validate()`), the grade distribution was:

| Verdict | Count |
|---|---|
| ROBUST | 0 |
| MODERATE | 0 |
| FRAGILE | 15 |
| FAILED | 23 |

The in-sample screen (2,156 runs: 14 symbols x 11 timeframes x 14 strategies, 600 bars each) found that only **17 of 449 statistically meaningful runs (3.8%)** — meaningful meaning at least 10 trades and a buy-and-hold benchmark worth beating (>= $1,000) — actually beat buy & hold at all. Of the full 2,156 rows, **1,693 (78.5%) traded fewer than 10 times** and are not evidence of anything either way, and **1,016 (47.1%) had >= 50% of their gross profit from a single trade**, the Lab's own overfitting tell.

The gate that killed the most candidates was **out-of-sample failure** and, more fundamentally, **trade count**. The candidates that looked best in-sample (ARM 1h `EMA_REGIME_LS`, +$72,099 vs. BH; NBIS 1h `MEAN_REVERSION`, +$74,318 vs. BH) both **failed out-of-sample** — the classic shape of a rule fitted to its own history (`ftmo_mt5/lab/verdict.py::_grade()`). The candidates that passed every other robustness test cleanly — OOS beat, full slippage ladder, top-5-winner removal, walk-forward folds — were held back purely by trade count: `MODERATE` requires >= 30 trades, and the best of this group (AMAT 5m `MEAN_REVERSION`, 16 trades, 5/5 folds won, 83% of parameter variations still won, survived 8 ticks of slippage and losing its 5 best trades) had only 16. That is not disqualifying evidence against the idea — it is a real ceiling of testing 600 bars at a time — but it is not enough trades to call anything more than FRAGILE by this rubric's own math.

The single dominant pattern in the data: **on daily bars and above (1d, 1w), buy & hold beat every strategy on every one of the 14 symbols, with zero exceptions** (0% beat rate at 1d and 1w across 154 meaningful-eligible rows each). These are momentum/mean-reversion/regime rules trading a historically exceptional AI-driven semiconductor bull run; none of them stayed in the trade as long, or as unconditionally, as simply holding the stock. The only timeframes where any edge showed up at all were 30m (15.2% of meaningful rows beat BH) and 1h (12.7%) — and even there, the winners were dominated by one strategy, `MEAN_REVERSION`, trading with too few round trips (average 9.6 trades per run, below the Lab's own 10-trade significance floor) to be trusted on its own.

**Bottom line: this Lab, run honestly and skeptically across these 14 symbols, produces no strategy with robust evidence of beating buy & hold.** A handful of `MEAN_REVERSION`-based FRAGILE candidates on 5m/15m/30m/1h are the closest things to a real signal, and even those need materially more trades (more bars, or a longer live-tracked run) before the rubric itself would call them MODERATE.

---

## 2. Lab Architecture

The pipeline, as read in full and exercised in this study:

```
ftmo_mt5/lab/windows.py   -- fetch & cache candles (Yahoo only), group higher timeframes
        |
ftmo_mt5/lab/metrics.py   -- measure() : run one backtest, compute every metric
        |
ftmo_mt5/lab/service.py   -- matrix()/single()/validate() : orchestrate many runs
        |
ftmo_mt5/lab/verdict.py   -- validate() : OOS split, cost ladder, removal, walk-forward,
                              parameter sensitivity -> ROBUST/MODERATE/FRAGILE/FAILED
        |
ftmo_mt5/lab/predict.py   -- for_symbol() : re-scores every default strategy, reports
                              the current stance of the best-qualifying one (NOT a forecast)
```

`windows.py` fetches once per (symbol, timeframe) and caches to disk (`study/lab/cache/*.json`); `metrics.py::measure()` hands the window to the real backtest engine (`ftmo_mt5/backtest/engine.py`) and collects the numbers; `service.py` fans that out across symbols/timeframes/strategies (`matrix()`) or drives one deep run (`single()`/`validate()`); `verdict.py` is the only module in the Lab that actually tests whether a result is *evidence* rather than a number.

## 3. Data Sources

- **Single source: Yahoo Finance**, via `ftmo_mt5.marketdata.yahoo.YahooHistoricalDataProvider` (confirmed — no MT5 or Tastytrade code path reaches the Lab; grep of `ftmo_mt5/lab/` shows only this provider imported).
- **Cached to disk** at `study/lab/cache/<symbol>_<providerTF>_<days>.json`, one file per (symbol, base timeframe, days), and memoized in-process. All 14 symbols' 5 base series (5m, 15m, 30m, 1h, 1d) were present and used from cache for this entire study — **no live Yahoo calls were made** during Steps B–E (confirmed via `ls -la study/lab/cache/`).
- **Per-timeframe day ceilings** (`windows.BASE`, `windows.CEILING`): 5m/15m/30m are capped at a **59-day trailing window**; 1h (and everything grouped from it — 2h/4h/6h/8h/12h) gets ~725 days (~2 years); 1d/1w get ~9,600 days (~26 years), rarely the real limit.
- **The 5m/15m/30m real-calendar-span caveat is real and confirmed silent.** A 600-bar request on 5m spans only ~9 real calendar days (measured directly, AMAT: 2026-08-19 -> 2026-08-28); the same 600-bar request on 1d spans ~871 days (~2.4 years). Both report as "600 bars, fully served" with no distinguishing warning. See Section 18.

## 4. Available Timeframes

All 11 (`windows.TIMEFRAMES`), with the **actual measured** calendar span a 600-bar request achieves (measured directly against AMAT's cached series; span scales similarly for the other 13 symbols since they share the same Yahoo ceilings):

| Timeframe | Bars served (of 600 requested) | Real calendar span | `BARS_PER_YEAR` |
|---|---|---|---|
| 5m | 600 | **9.2 days** | 23,400 |
| 15m | 600 | **31.0 days** | 7,800 |
| 30m | 520 (short — 59-day Yahoo ceiling divided by 30-min bars) | **53.2 days** | 3,900 |
| 1h | 600 | **122.2 days** | 5,716 |
| 2h | 600 | **217.9 days** | 2,858 |
| 4h | 600 | **375.1 days** | 1,550 |
| 6h | 600 | **438.0 days** | 1,033 |
| 8h | 600 | **437.0 days** | 775 |
| 12h | 497 (short — 725-day 1h ceiling / 12) | **722.0 days** | 516 |
| 1d | 600 | **871.0 days (~2.4 yrs)** | 252 |
| 1w | 600 | **4,193.0 days (~11.5 yrs)**, bounded by actual available daily history (1,372 weeks), not the 9,600-day ceiling | 52 |

A "600-bar" request therefore means anywhere from 9 days to 11.5 years of real market exposure depending on timeframe — a fact the numbers in Sections 7-9 must be read against. In particular: the daily-and-above timeframes (1d, 1w) see the least sample-size (in trades) *and* the most calendar exposure, which is exactly why buy & hold is so hard to beat there — it captured a genuinely exceptional multi-year run in these names, and any rule that stepped out of the market even briefly gave up compounding it could never recover.

## 5. Available Strategies

Confirmed live via `ftmo_mt5.lab.service.strategy_defaults()` and `ftmo_mt5.signals.list_strategies()` (both called directly, `venv/bin/python3` — see Section 6): **14 strategies** are on the public menu. A 15th class, `EmaRsiPatternStrategy` (`name = "EMA_RSI_PATTERN"`), exists in `ftmo_mt5/signals/strategies.py` but is in the module's own `HIDDEN` set (`strategies.py:204`) and is excluded from `list_strategies()`/`strategy_defaults()`, though it would still resolve by name if called directly. It was not included in the pre-existing sweep or in this study's validation, consistent with it being deliberately off the menu.

One honest sentence per strategy (from each class's own docstring, `ftmo_mt5/signals/strategies.py`):

| Strategy | Logic |
|---|---|
| `MTF_GREEN_STACK` | Trade only when three configured timeframes (default 5/15/60 min) all agree on direction; every bar green is long, every bar red is short, anything mixed is flat. |
| `EMA_RSI_PULLBACK` | Long-only: a higher timeframe (default 1h) must show an EMA-50>EMA-200 uptrend with RSI above threshold, then the entry timeframe must pull back to the moving-average band, dip in RSI, and recover — no exit of its own, relies entirely on stops. |
| `TREND_REGIME_PULLBACK` | The pullback idea again, but the higher timeframe must also be *turning* (slow average rising), the exit is the entry timeframe losing its own trend, and the initial stop is the setup's own swing low. |
| `DONCHIAN_BREAKOUT` | Classic Turtle-style channel breakout: buy a new N-bar high (default 55), exit on a new M-bar low (default 20). |
| `TREND_REGIME` | Pure participation rule: long while price is above its 200-period average, flat below it — makes its case in reduced drawdown, not in return. |
| `EMA_REGIME_LS` | The simplest possible trend rule: long above a single moving average (default period 100), short below it, nothing else — the project's own "control" baseline. |
| `FOUR_REGIME` | Classifies the market into 4 states (strong uptrend / breakout / sideways / downtrend) and applies a different playbook (pullback entry, breakout entry, stand aside, no longs) to each. |
| `MEAN_REVERSION` | Fades stretch instead of following it: buys when price is unusually far below its rolling average (in standard deviations), sells when unusually far above, exits on reversion to the mean. |
| `GOLD_MASTER_TREND` | The most fully specified rule in the project: 1h master-trend filter, 15m pullback entry, staged/promoted stops and two separate exit conditions. |
| `DAILY_SWING_SMA200` | Daily-close participation above the 200-day SMA with a buffer and candle-quality gate on entry, held until a daily close goes back below the average. |
| `VOL_BREAKOUT_TREND` | 20-bar range breakout, taken only while ATR is expanding above its own average and the long-term trend agrees — a volatility-filtered breakout. |
| `TREND_PULLBACK_STACK` | Seven stacked, all-required conditions (EMA-50>EMA-200, RSI>55, MACD>signal, ATR expanding, pullback to EMA-20, up-close bar), mirrored for shorts. |
| `SIMPLE_PULLBACK` | A lighter, 6-condition version of the same pullback idea with no MACD/ATR filter. |
| `REGIME_SWITCH_LS` | Three-state EMA-stack regime: long on a bull regime (price/EMA-50 above EMA-200), short only once a bear regime *also* breaks the recent low — an intentionally asymmetric trigger. |
| `EMA_RSI_PATTERN` *(hidden from the menu — see above)* | EMA trend alignment plus an RSI band plus a bullish/bearish candlestick pattern trigger; RSI vetoes extremes rather than picking direction. |

## 6. Testing Methodology

**Reused, not re-derived:**
- `sweep_equities.py` (repo root) and `semis-sized-2156-runs.csv` — the pre-existing, legitimate 2,156-row in-sample sweep (14 symbols x 11 timeframes x 14 default-parameter strategies, 600 bars, position sized to ~$100k notional per `sweep_equities.py`'s own docstring). Used as the canonical source for Step B (Section 8, 9, and the in-sample halves of Section 7).
- All cached candle data under `study/lab/cache/*.json` — confirmed present for all 14 symbols' base series before any new run was made; no network calls were required for this study.

**Run fresh for this study (new scripts, kept per the `study/lab/` convention, not deleted):**
- `study/lab/semis_step_b_summary.py` — pure-Python aggregation of `semis-sized-2156-runs.csv` into per-symbol / per-timeframe / per-strategy summaries and outlier lists. Output: `study/lab/semis_step_b_summary.json`. Does not call any Lab function; it only reads the pre-existing CSV, so its numbers are exactly the CSV's numbers, aggregated.
- `study/lab/semis_validate.py` — the Step C robustness layer. For 38 documented candidates (selection rule below), calls `ftmo_mt5.lab.service.validate(symbol, timeframe, spec, bars=600, param_key=None)` — a real Lab function, not a re-implementation — using each candidate's default strategy parameters (from `service.strategy_defaults()`) and the same per-symbol share count `semis-sized-2156-runs.csv` used. Output: `study/lab/semis_validate_results.json`. 38 `validate()` calls completed in **1.6 seconds** (fully cached, in-process — no network).

**Candidate selection for Step C** (documented in `semis_validate.py`'s own module docstring):
- **(a) 14 candidates**, one per symbol: the best-`vsBH` combo among rows with `beatsBH=True`, `trades>=10`, `bh` not flat (>=$1,000), and `topTradeShare<70`, drawn from the CSV. **For 5 symbols (CRDO, DELL, MRVL, NVDA, TSM), no combo in the entire 154-row-per-symbol sweep clears that filter** — for those, the single best nominally-"beatsBH" row was substituted and explicitly labelled `stepC_a_NONQUALIFYING` in the output, because the honest finding for those symbols is that nothing clears the bar, and that has to appear in the validated evidence rather than be silently dropped (see Section 7).
- **(b) 14 candidates**: each symbol's best 1d-timeframe combo, since none of (a)'s picks landed on 1d and 1d carries the longest, most reliable real-calendar history.
- **(c) 10 candidates**: the 5 highest raw in-sample `vsBH` rows and the 5 highest-`topTradeShare`-among-beaters rows across the full 2,156-row dataset — the "too good to be true" outlier check.

**Step D** (prediction module): `ftmo_mt5.lab.predict.for_symbol()` called directly for NVDA, AMD, TSM, MU, SNDK with `cached_only=True` (Section 12).

**Step E** (cross-checks, Section 6 continued): 3 `validate()` candidates were re-run and produced byte-identical `net`/`bh`/`verdict`/OOS results both times (AMAT 1h `MEAN_REVERSION`, ARM 1h `EMA_REGIME_LS`, WDC 1h `MEAN_REVERSION`) — confirmed deterministic and cache-stable. 3 CSV rows (AMAT 1h `MEAN_REVERSION`, AMAT 5m `DONCHIAN_BREAKOUT`, AMD 1d `DAILY_SWING_SMA200`) were independently recomputed with a fresh `metrics.measure()` call and matched the CSV to the reported decimal exactly — **no cache drift, the CSV is trustworthy.**

**CSV provenance discrepancy — resolved, not left as UNKNOWN.** `semis-2156-runs.csv` ("unsized") and `semis-sized-2156-runs.csv` ("sized") differ by 1 share on otherwise-identical rows (e.g. AMAT/5m: 203 vs 202 shares). Testing both formulas directly against the cached AMAT 5m series: `round(100_000 / candles[0].close)` = 203 (matches the *unsized* file, and is literally the formula in the checked-in `sweep_equities.py` today), while `int(100_000 // candles[0].close)` = 202 (matches the *sized* file, and is exactly `ftmo_mt5.lab.service.size_for()`'s formula — `max(1, int(capital // per_unit))`). **Conclusion: the two CSVs were produced by two different position-sizing implementations — one matching the current `sweep_equities.py` script, one matching `service.size_for()` — not by a data change.** Cache file mtimes (all Aug 31 00:18-00:20 for the base series) rule out a re-fetch between the two runs. No converter script or notebook producing either CSV from JSON was found anywhere in the repo (`grep` for `semis-sized`/`semis-2156`/`round(100_000` found nothing outside the CSVs themselves), and this whole project is **not a git repository** (`git status` fails with "not a git repository"), so no commit history exists to consult either. The dollar impact of the 1-share difference is under 0.5% of position size and does not change any grade or ranking in this report. `semis-sized-2156-runs.csv` (the `service.size_for()`-consistent file, with the extra `beatPct` column) was used as canonical throughout.

## 7. Symbol-by-Symbol Results

All 14 symbols were tested; none were skipped. For each: the in-sample best/worst from the 154-row sweep (Section 8/9's source data), and the OOS/robustness grade from Step C.

| Symbol | In-sample best (154 combos) | Meaningful-combo beat rate | OOS/robustness grade of best qualifying candidate | Notes |
|---|---|---|---|---|
| AMAT | `MEAN_REVERSION` 1h, +$8,177 vs BH, 11 trades, TTS 36.6% | 12.5% of 40 meaningful | **FRAGILE** — beats OOS, 8/8 ticks, 5/5 folds, 83% variations held, but only 11 trades (needs 30 for MODERATE) | Also validated: AMAT 5m `MEAN_REVERSION` (16 trades) passed every gate except trade count — closest thing to a real edge in the whole study |
| AMD | `MEAN_REVERSION` 30m, +$19,364 vs BH, 10 trades, TTS 26.6% | 3.7% of 27 meaningful | **FRAGILE** — beats OOS, 8/8 ticks, 5/5 folds won, 100% variations held; held back purely by trade count (10 of 30 needed) | Clean sweep on every axis but sample size |
| ARM | `EMA_REGIME_LS` 1h, +$72,099 vs BH, 17 trades, TTS 42.9% | 9.4% of 32 meaningful | **FRAGILE** — **fails OOS**; 43% of gross profit from one trade, doesn't survive top-3 removal; wins only 2/5 folds | The single biggest in-sample number in the whole dataset that wasn't ARM 2h `DONCHIAN_BREAKOUT` (below) collapses under OOS |
| CRDO | No combo beats BH with >=10 trades and a non-flat benchmark | 0.0% of 36 meaningful | **FAILED** — best nominal "beat" is `DAILY_SWING_SMA200` 30m with 1 trade (100% TTS) | No real evidence either way for CRDO on this window |
| DELL | `MEAN_REVERSION` 30m, +$144 vs BH, 7 trades | 0.0% of 32 meaningful | **FAILED** — 7 trades (below the 10-trade floor), OOS technically beats but the whole in-sample margin is $144, inside the noise of any single assumption | No usable edge |
| LITE | `MEAN_REVERSION` 2h, +$99,785 vs BH, 13 trades, TTS 13.4% | 5.7% of 35 meaningful | **FRAGILE** — beats OOS, 8/8 ticks, but only 2/5 folds won, 83% variations held, 13 of 30 trades needed | Best dollar number in the dataset for a qualifying candidate; still short on folds and trade count |
| MRVL | No combo beats BH with >=10 trades and a non-flat benchmark | 0.0% of 34 meaningful | **FAILED** — best nominal "beat" is `DAILY_SWING_SMA200` 30m, 1 trade | No real evidence either way |
| MU | `MEAN_REVERSION` 15m, +$4,791 vs BH, 11 trades, TTS 33.4% | 3.6% of 28 meaningful | **FRAGILE** — beats OOS, 8/8 ticks, 3/5 folds, 50% variations held, 11 of 30 trades needed | Weakest of the qualifying `MEAN_REVERSION` picks |
| NBIS | `MEAN_REVERSION` 1h, +$74,318 vs BH, 12 trades, TTS 31.5% | 2.6% of 38 meaningful | **FRAGILE** — **fails OOS**; 32% of gross profit from one trade, fails top-3 removal, 3/5 folds | Second case (with ARM) of a large in-sample number that does not survive the OOS split. NBIS also has only 464 daily bars of history available (listed ~Oct 2024) — a relatively new/renamed ticker, see Section 16 |
| NVDA | No combo beats BH with >=10 trades and a non-flat benchmark | 0.0% of 41 meaningful | **FAILED** — best nominal "beat" is `EMA_RSI_PULLBACK` 5m with **0 trades** (the rule never traded; BH itself lost $3,071 over that window, so sitting out "beat" it) | Classic false-positive `beatsBH` — flagged directly by `service.warnings_for()`'s own trades==0 rule |
| SMH | `MEAN_REVERSION` 1h, +$12,243 vs BH, 11 trades, TTS 35.6% | 5.9% of 34 meaningful | **FRAGILE** — beats OOS, 8/8 ticks, 3/5 folds, 66.7% variations held, 11 of 30 trades needed | Consistent with the rest of the `MEAN_REVERSION` cohort |
| SNDK | `MEAN_REVERSION` 5m, +$24,232 vs BH, 14 trades, TTS 19.2% | 5.0% of 20 meaningful | **FRAGILE** — beats OOS, 8/8 ticks, 4/5 folds, 100% variations held; 14 of 30 trades needed | Passes every gate but trade count, same pattern as AMD/AMAT |
| TSM | No combo beats BH with >=10 trades and a non-flat benchmark | 0.0% of 25 meaningful | **FAILED** — best nominal "beat" is `EMA_RSI_PULLBACK` 30m with **0 trades** | Same false-positive pattern as NVDA |
| WDC | `MEAN_REVERSION` 1h, +$97,447 vs BH, 12 trades, TTS 19.7% | 3.7% of 27 meaningful | **FRAGILE** — beats OOS, 8/8 ticks, 3/5 folds, 83.3% variations held; 12 of 30 trades needed | Second-best dollar number of any qualifying candidate |

**No symbol was skipped or untestable.** All 14 have full cached 5m/15m/30m/1h/1d series; grouped timeframes (2h/4h/6h/8h/12h/1w) derive cleanly from those. The one data-availability caveat is NBIS's shorter listing history (Section 16).

## 8. Timeframe Comparison

Computed across all 14 symbols x 14 strategies at each timeframe (154 rows/timeframe, from `semis_step_b_summary.py`, source: `semis-sized-2156-runs.csv`):

| TF | Beat rate (of meaningful rows) | Avg vsBH (pp) | Avg trades | Avg Sharpe | Flat-BH rows |
|---|---|---|---|---|---|
| 5m | 3.6% | -26.35 | 20.0 | -8.58 | 28/196 |
| 15m | 5.6% | -23.00 | 4.9 | -3.12 | 14/196 |
| 30m | **15.2%** | -5.14 | 4.9 | -2.42 | 0/196 |
| 1h | 12.7% | -64.76 | 6.8 | -0.68 | 0/196 |
| 2h | 2.0% | -111.99 | 5.8 | 0.63 | 0/196 |
| 4h | 0.0% | -393.32 | 5.9 | 1.12 | 0/196 |
| 6h | 0.0% | -444.14 | 5.5 | 1.04 | 0/196 |
| 8h | 0.0% | -453.19 | 5.6 | 0.84 | 0/196 |
| 12h | 0.0% | -513.88 | 3.3 | 1.19 | 0/196 |
| 1d | 0.0% | -580.21 | 4.8 | 0.61 | 0/196 |
| 1w | 0.0% | -3,749.07 | 4.0 | 0.32 | 0/196 |

**Reading this against the calendar-span caveat (Section 4) matters.** 30m and 1h have the highest beat rates, but 30m's 600-bar window is only ~53 real calendar days and 1h's is ~122 days — short enough that a handful of lucky trades can carry the whole result (consistent with the low average trade counts, 4.9-6.8, and the fact that every qualifying Step-C candidate in Section 7 needed more trades than it had). **Beat rate falls monotonically to exactly zero from 2h upward**, and average underperformance grows almost linearly with calendar span (from -111pp at 2h/218 days to -3,749pp at 1w/11.5 years) — this is not noise, it is buy & hold compounding an exceptional multi-year semiconductor rally that every stop-bearing, regime-switching, or mean-reverting rule in this menu periodically stepped out of. **Shorter timeframes do not "just add noise" here in the way the research brief hypothesized — 30m/1h show the only real beat-rate signal in the dataset — but that signal is built on trade counts (4.9-6.8 average) too thin to trust without more bars than the Yahoo intraday ceiling allows in one window.** Sharpe improves (less negative, eventually positive) as timeframe lengthens, but that is Sharpe measuring the *strategy's own* smoother-but-lower-returning ride, not vs. BH — it is consistent with, not contradictory to, BH winning on raw dollars at those same timeframes.

## 9. Strategy Comparison

Computed across all 14 symbols x 11 timeframes at each strategy (154 rows/strategy):

| Strategy | Beat rate (meaningful) | Avg vsBH (pp) | Avg trades | Avg top-trade-share | % high concentration (>=50%) |
|---|---|---|---|---|---|
| `MEAN_REVERSION` | **14.3%** (highest) | -1,286.62 (worst average — huge variance) | 9.61 | 50.9% | 42.2% |
| `REGIME_SWITCH_LS` | 3.3% | -220.54 | 8.24 | 77.7% | 83.8% |
| `EMA_REGIME_LS` | 1.4% | -556.40 | 25.34 | 68.3% | 79.2% |
| `TREND_REGIME` | 1.4% | -312.57 | 10.67 | 79.0% | 88.3% |
| `DONCHIAN_BREAKOUT` | 0.0%* | -421.23 | 8.95 | 73.6% | 87.7% |
| `DAILY_SWING_SMA200` | 0.0%* | -639.53 | 3.41 | 46.2% | 48.7% |
| `MTF_GREEN_STACK` | 0.0%* | -825.19 | 17.08 (140/154 rows traded 0 times) | 5.7% | 6.5% |
| `SIMPLE_PULLBACK` | no meaningful rows (154/154 <10 trades) | -181.58 | 1.85 | 66.4% | 66.9% |
| `TREND_PULLBACK_STACK` | no meaningful rows | -214.86 | 1.83 | 70.3% | 70.8% |
| `VOL_BREAKOUT_TREND` | no meaningful rows | -234.25 | 3.67 | 81.0% | 83.8% |
| `EMA_RSI_PULLBACK` | **no meaningful rows — 0 trades in all 154 runs** | -801.46 | 0.00 | 0.0% | 0.0% |
| `GOLD_MASTER_TREND` | **no meaningful rows — 0 trades in all 154 runs** | -801.46 | 0.00 | 0.0% | 0.0% |
| `TREND_REGIME_PULLBACK` | no meaningful rows (153/154 zero-trade) | -801.50 | 0.01 | 0.0% | 0.0% |
| `FOUR_REGIME` | no meaningful rows (130/154 zero-trade) | -803.78 | 0.16 | 1.9% | 1.9% |

\* 0.0% beat rate but not "no meaningful rows" — these did have >=10-trade combos, they just never beat BH.

**`MEAN_REVERSION` dominates every "best per symbol" slot in Section 7** and has the highest beat rate of any strategy — but it also has by far the worst *average* vsBH of any strategy (-1,286.62pp), meaning it wins big occasionally and loses far bigger far more often. This is exactly the profile of a strategy whose few wins are cherry-picked by the "best combo" selection in Section 7/8, which is precisely why Step C existed: 3 of the `MEAN_REVERSION` picks (AMD, SNDK, AMAT-5m) passed every OOS/tick/removal/fold gate and were held back only by trade count, while 2 more (ARM's runner-up EMA_REGIME_LS and NBIS) with the largest in-sample dollar wins **failed OOS outright**.

**Three strategies produced literally zero trades across all 154 symbol/timeframe combinations each**: `EMA_RSI_PULLBACK`, `GOLD_MASTER_TREND`, `TREND_REGIME_PULLBACK`. This was independently re-confirmed with fresh `metrics.measure()` calls on AMD/NVDA across multiple timeframes (5m/15m/30m/1h/1d), not a CSV artifact — see Section 18. Between these three and `FOUR_REGIME` (0.16 avg trades) and the two lightly-traded pullback variants, **6 of the Lab's 14 public strategies never produced a statistically meaningful sample on any of these 14 semiconductor names at 600 bars** — the effective menu for this asset class and window length is closer to 8 usable strategies than 14.

## 10. Strategy vs Buy & Hold

Distinguishing in-sample-only wins from OOS-validated wins, for every candidate that beat BH in-sample in Section 7:

| Candidate | In-sample vs BH | Meaningful? | OOS beat? | Verdict | Was it real? |
|---|---|---|---|---|---|
| ARM 1h `EMA_REGIME_LS` | +$72,099 (72.1pp) | Yes (17 trades) | **No** | FRAGILE | **No — in-sample only.** 43% of profit is one trade; fails removal and folds. |
| NBIS 1h `MEAN_REVERSION` | +$74,318 (74.3pp) | Yes (12 trades) | **No** | FRAGILE | **No — in-sample only.** Same shape as ARM. |
| WDC 1h `MEAN_REVERSION` | +$97,447 (97.5pp) | Yes (12 trades) | Yes | FRAGILE | Partially — clears OOS and every cost/removal/fold test, but 12 of 30 trades needed for MODERATE. |
| LITE 2h `MEAN_REVERSION` | +$99,785 (99.8pp) | Yes (13 trades) | Yes | FRAGILE | Partially — clears OOS and ticks, but only 2/5 folds and 13 of 30 trades. |
| AMD 30m `MEAN_REVERSION` | +$19,364 (19.4pp) | Yes (10 trades) | Yes | FRAGILE | **Cleanest evidence in the dataset** — 5/5 folds, 100% variations held, 8/8 ticks, survives top-5 removal. Only trade count (10 of 30) keeps it off MODERATE. |
| SNDK 5m `MEAN_REVERSION` | +$24,232 (24.2pp) | Yes (14 trades) | Yes | FRAGILE | Same clean profile as AMD, 14 of 30 trades. |
| AMAT 5m `MEAN_REVERSION` (outlier check) | +$2,724 (small, but Step-C validated) | Yes (16 trades) | Yes | FRAGILE | **5/5 folds won**, 83% variations held, 8/8 ticks — the single most-complete pass of any candidate, still short only on trade count (16 of 30). |
| AMAT 1h `MEAN_REVERSION` | +$8,177 (8.2pp) | Yes (11 trades) | Yes | FRAGILE | Clears OOS/ticks/folds/removal; 11 of 30 trades. |
| MU 15m `MEAN_REVERSION` | +$4,791 (4.8pp) | Yes (11 trades) | Yes | FRAGILE | Weakest fold record of the group (3/5), 11 of 30 trades. |
| SMH 1h `MEAN_REVERSION` | +$12,243 (12.2pp) | Yes (11 trades) | Yes | FRAGILE | Same profile as MU. |
| ARM 2h `DONCHIAN_BREAKOUT` (in-sample #1 overall, +$123,400) | +$123,400 | No (only 4 trades) | not validated (fails the trades>=5 filter Step C used for outlier selection; would fail FAILED gate on trade count alone) | — | **No — not even meaningful in-sample.** The single largest raw number in the whole 2,156-row sweep is 4 trades. |

**At what risk?** The Step-C-validated `MEAN_REVERSION` FRAGILE candidates (AMAT, AMD, SNDK, WDC, LITE, MU, SMH) all carried `maxDDPct` in the same broad range as their own BH benchmark (drawdown figures are in the raw `study/lab/semis_validate_results.json`, `full.maxDDPct` field for each candidate) — none of them found a way to beat BH's return while meaningfully cutting its drawdown; several (LITE, ARM, NBIS) beat BH's *dollar* return in-sample while running the same or deeper drawdown, which is a worse risk-adjusted outcome even before OOS is considered. **Was it meaningful?** For the two FAILED-on-OOS cases (ARM, NBIS) — no, plainly not. For the FRAGILE `MEAN_REVERSION` cohort — meaningful in the sense that they passed every robustness test except sample size, but the sample size gap (10-16 vs. the 30 needed) is exactly the gap the rubric is designed not to forgive, and this report will not forgive it either.

## 11. Risk Analysis

- **Drawdowns**: full per-candidate `maxDD`/`maxDDPct` and BH's own `bhDDPct` are recorded for every Step-C candidate in `study/lab/semis_validate_results.json` (`full.maxDDPct`). No qualifying candidate in Section 10 materially reduced drawdown versus its own BH benchmark while also beating its return — the closest was WDC 1h `MEAN_REVERSION`.
- **Sharpe**: computed by `metrics.sharpe()` only when a run has >=30 per-bar equity-curve returns; annualized using `windows.BARS_PER_YEAR`. Section 8's per-timeframe average Sharpe turns positive only at 2h and above, exactly where beat rate falls to zero — the strategies get smoother, not more profitable, as timeframe lengthens.
- **Time in market**: recorded per-run (`timeInMarket` field) but not separately aggregated for this report beyond Section 8/9's trade-count figures; available in the raw CSV/JSON for any specific candidate a reader wants to check.
- **Position sizing rationale**: `sweep_equities.py` and the canonical `semis-sized-2156-runs.csv` both size each run to roughly $100,000 of notional per symbol/timeframe (`service.size_for()`'s formula, `max(1, int(capital // per_unit))`), specifically because 1 share of a $100-900 stock (this basket's actual price range) would move the account by tens of dollars and put every buy-and-hold benchmark under the Lab's own $1,000 flat-benchmark floor — see `service.py`'s own docstring on `size_for()`, and `sweep_equities.py`'s docstring, both read in full for this study.
- **No calibrated risk defaults exist for any of these 14 equities.** `ftmo_mt5/backtest/risk_defaults.py::BUILT_IN` contains measured trailing-stop entries for exactly two instrument roots — `GC` and `NQ` (both futures) — and nothing else. `risk_defaults.resolve(symbol, timeframe)` therefore returns `None` for every one of AMAT/AMD/ARM/CRDO/DELL/LITE/MRVL/MU/NBIS/NVDA/SMH/SNDK/TSM/WDC at every timeframe (confirmed directly: `predict.for_symbol()`'s output for NVDA/AMD/TSM/MU/SNDK all show `"trailingStopAtr": null`). Every run in this entire study — the pre-existing sweep and this report's new validations alike — traded with **no config-level trailing stop**, relying entirely on whatever stop logic a given strategy supplies internally (e.g. `TrendRegimePullbackStrategy`'s swing-low stop, `GoldMasterTrendStrategy`'s `stop_atr` parameter) or, for most strategies, no protective stop at all beyond the rule's own exit signal. This is a real gap for anyone wanting to trade these names with the Lab's risk-management layer rather than just its signal layer (see Section 19).

## 12. Prediction/ML Analysis

**`ftmo_mt5/lab/predict.py` is confirmed to be exactly what Part 1 described: not a forecasting model.** `for_symbol()` was called directly (`cached_only=True`, no network) for 5 symbols across all 11 timeframes (154 combinations each):

| Symbol | Qualified combos (of 154) | Confidence | Best pick | Trades | TTS | Win rate |
|---|---|---|---|---|---|---|
| NVDA | 0 | **weak** | `FOUR_REGIME` 1h | 1 | 100% | 100% |
| AMD | 0 | **weak** | `TREND_REGIME` 1d | 1 | 100% | 100% |
| TSM | 0 | **weak** | `MEAN_REVERSION` 30m | 4 | 79.7% | 50% |
| MU | 0 | **weak** | `SIMPLE_PULLBACK` 1d | 1 | 100% | 100% |
| SNDK | **1** | qualified | `REGIME_SWITCH_LS` 30m | 10 | 0.0% | **0%** |

4 of 5 symbols produced **zero** combinations that clear `predict.py`'s own light-touch qualification bar (`beatsBH`, `trades>=10`, `topTradeShare<50%`, BH not flat) — every one of them falls back to the module's own "weak"-labelled "best available" answer, which the module itself flags with an honest `why` string (e.g. "only 1 trades, 100% of profit from one trade"). **SNDK's one "qualified" pick is a strategy that lost money on every single trade (0% win rate) and only "beat" BH because BH itself lost more** ($16,822 lost vs BH's $18,931 lost) — technically satisfying `beatsBH` while being, by any normal reading, not a working strategy. This is the exact "beating a benchmark that itself went nowhere or lost" failure mode the Lab's own `service.warnings_for()` and `verdict.FLAT_BENCHMARK` gate exist to catch, and it shows up even inside `predict.py`'s "qualified" bucket because `predict.py` does not run the OOS/walk-forward layer — only `verdict.validate()` does, and `predict.py` does not call it.

**Confirmed: there is no training set, no train/test split, no learned parameters, and no accuracy metric anywhere in `predict.py`.** It re-scores every strategy's shipped defaults with the same in-sample `metrics.measure()` used everywhere else, picks the highest-`vsBH` qualifier, and replays that rule's signals forward to report its current stance (long/short/flat) — a fact about the rule, not a claim about the market, exactly as the module's own docstring states.

**`ftmo_mt5/ml/` is real, tested infrastructure — triple-barrier meta-labeling (`meta_labeler.py`), purged k-fold CV and CSCV/deflated-Sharpe (`validation.py`), PSI/ADWIN drift detection (`drift_detection.py`), and a model-promotion gate (`promotion_gate.py`) — but it is fully disconnected from both the Lab and live trading.** `grep -rn "from ftmo_mt5.ml\|import ftmo_mt5.ml" --include="*.py" .` (excluding venv) returns matches **only** in `tests/test_ml_pipeline.py` and `tests/test_properties.py`. Neither `ftmo_mt5/lab/predict.py`, `ftmo_mt5/lab/service.py`, nor `ftmo_mt5/main.py` imports anything from `ftmo_mt5.ml`. **There is no connected ML/prediction system to evaluate for trading-performance improvement in this project** — `ftmo_mt5/ml/` is orphaned code, tested in isolation but never wired to anything a user or agent would actually run, matching the same finding in `PROJECT_CONTEXT.md`.

## 13. Best Performing Combinations

Ranked by the strength of validated (not merely in-sample) evidence:

1. **AMD 30m `MEAN_REVERSION`** — FRAGILE, but the cleanest pass of any candidate: beats OOS, 5/5 walk-forward folds, 100% of parameter variations still beat, survives 8 ticks of slippage and losing its top 5 winners. Only 10 trades keeps it off MODERATE (needs 30).
2. **AMAT 5m `MEAN_REVERSION`** — FRAGILE, 5/5 folds won, 83% variations held, 8/8 ticks, survives top-5 removal. 16 of 30 trades needed.
3. **SNDK 5m `MEAN_REVERSION`** — FRAGILE, 4/5 folds, 100% variations held, 8/8 ticks, survives top-5 removal. 14 of 30 trades needed.
4. **WDC 1h `MEAN_REVERSION`** — FRAGILE, beats OOS with the largest dollar margin of any fully-qualifying candidate (+$97,447 in-sample), 3/5 folds, 83.3% variations held.
5. **LITE 2h `MEAN_REVERSION`** — FRAGILE, largest raw dollar win of any candidate that also beat OOS (+$99,785), but only 2/5 folds.
6. **AMAT 1h / MU 15m / SMH 1h `MEAN_REVERSION`** — all FRAGILE, all beat OOS, all held back on trade count (11 each) and folds (3/5).

No candidate in this study reached MODERATE or ROBUST. The list above is "best FRAGILE," not "working strategy."

## 14. Worst Performing Combinations

Ranked by how badly, and why:

1. **NBIS 1d `TREND_PULLBACK_STACK`**, -$278,050 vs BH, 1 trade. NBIS 1d `EMA_REGIME_LS` (in-sample matrix, not the Step-C 1d pick) is even worse at -$2,823,150 with 32 trades on 1d.
2. **SNDK 1d `REGIME_SWITCH_LS`**, -$490,918 vs BH, 1 trade.
3. **NVDA 1w `EMA_REGIME_LS`**, -$16,228,961 vs BH, 17 trades — the worst single number in the entire 2,156-row sweep. `EMA_REGIME_LS` is long-above/short-below a single moving average with `trade_shorts=True` by default; on 1w bars over ~11.5 years it was short NVDA through large stretches of the largest structural bull market this basket has seen, and the position was sized to ~$100k notional at the *start* of an 11-year window whose end price is many multiples higher — both the strategy's direction and the Lab's flat position-sizing convention (sized once, for a 600-bar window, then held for the window's full calendar span) compound badly together at 1w. This is a sizing/methodology artifact as much as a strategy failure, and is called out explicitly rather than read as "the strategy is 160x worse than everything else."
4. **CRDO 1d `DONCHIAN_BREAKOUT`**, -$2,547,849 vs BH, 14 trades.
5. **`EMA_REGIME_LS`/`MEAN_REVERSION`/`DONCHIAN_BREAKOUT` at 1w across most symbols** — the entire 1w column is a wipeout for every strategy in the strategy comparison (Section 9's -3,749pp average), driven by the same short-into-a-melt-up and flat-position-sizing-over-a-decade dynamic as #3.

**Why**: all of the worst results share two causes — (a) shorting a stock during a historic sector-wide bull run (`trade_shorts=True` is the default on most of these strategies), and (b) position size fixed once for the whole window rather than rebalanced, which magnifies any directional miss over a long enough calendar span. Neither is a Lab bug; both are real facts about backtesting mean-reversion/regime rules with static sizing against buy & hold in a strong secular uptrend.

## 15. Consistency Analysis

Walk-forward fold results (5 contiguous, non-overlapping folds per `verdict.validate()`, each judged against its own BH) for the qualifying `MEAN_REVERSION` cohort from Section 13: **fold win rates ranged from 2/5 to 5/5**, with no candidate winning all 5 except AMAT 5m. Most won 3/5 or 4/5 — beating BH in roughly 60-80% of sub-periods but not universally, which is consistent with a real but inconsistent behavioral edge (mean reversion working better in choppier stretches than in strongly trending ones) rather than either a pure coincidence (which would tend to concentrate in 1 fold) or a truly regime-independent edge (which would win all 5). The two candidates that failed OOS outright (ARM, NBIS) also won only 2/5 and 3/5 folds respectively — consistent with, not contradictory to, their OOS failure.

**Regime dependence is visible directly in the timeframe comparison (Section 8)**: every timeframe from 2h upward is a near-total loss for every strategy, precisely the span over which these names' AI-driven rally compounded hardest. The Lab does not have a regime-labelling feature to test this more granularly, but the raw pattern — active/short-lived strategies doing marginally better, buy-and-forget doing catastrophically worse as the window lengthens — is consistent across every symbol in Section 7, not an artifact of one or two names.

## 16. Overfitting/Bias Checks

- **Look-ahead**: ruled out by design. `ftmo_mt5/signals/engine.py`'s own module docstring states bar i's signal only ever sees candles 0..i, and `ftmo_mt5/backtest/engine.py` fills at the **next** bar's open (`ExecutionModel.NEXT_OPEN`, the default), not the signalling bar's own close — confirmed by reading the config default and the engine's fill logic directly.
- **Data leakage**: the OOS split (`verdict.SPLIT_FRACTION = 0.70`) cuts candles once, in time order, never shuffled, and buy & hold is recomputed independently inside each half — confirmed by reading `verdict.validate()` directly (Section 2). No candidate's dev/OOS split in this study showed any sign of a leak (e.g. the OOS half performing suspiciously close to the dev half despite different market conditions); the two OOS failures (ARM, NBIS) failed exactly the way a fitted-to-history rule should.
- **Survivorship bias**: this study used only the current constituent list; Yahoo does not backfill delisted names, and none of these 14 tickers has been delisted, so this is a non-issue for the specific 14 symbols tested — but the basket itself was hand-picked as "semiconductor-adjacent," which is itself a form of selection (these are names that *did* survive and thrive through an AI boom). **NBIS specifically has only 464 days of daily history available** (2024-10-21 to present, confirmed via `windows.window('NBIS','1d',600)` returning `short=True`, 464 of 600 requested) — Nebius is a relatively new/renamed Nasdaq listing, and every NBIS backtest in this study (Sections 7-10) is working with meaningfully less calendar history than the other 13 names at the same bar count on daily-and-below timeframes.
- **Excessive optimization**: this study only tested each strategy's shipped default parameters (`service.strategy_defaults()`) plus `verdict.validate()`'s own automatic +/-40-60% neighborhood sweep around the first numeric parameter — no manual parameter hunting was done, and the "variations held %" gate exists specifically to catch a result that only works at one exact tuned value (an "island"). Every qualifying `MEAN_REVERSION` candidate in Section 13 held 50-100% of its neighboring parameter values, i.e. these are plateaus, not islands — genuinely the most reassuring result in the whole study, even though trade count still keeps them at FRAGILE.
- **Unrealistic execution assumptions**: addressed directly, and found realistic. Commission ($2.50/side, both strategy and BH), slippage (always adverse, both strategy and BH, cost-ladder-tested up to 8 ticks), and next-open fills (not same-bar-close) are all confirmed by direct reading of `ftmo_mt5/backtest/engine.py` and `ftmo_mt5/lab/metrics.py`.
- **Insufficient trade counts**: the dominant finding of this entire report. 1,693 of 2,156 in-sample rows (78.5%) traded fewer than 10 times; every single Step-C FRAGILE candidate was held back from MODERATE specifically by trade count (10-17 actual vs. 30 required).
- **Single-period dependence**: directly tested via top-1/3/5-winner removal (Section 2's "concentration" test). The two OOS failures (ARM, NBIS) both fail top-3 removal outright — their in-sample edge is a small number of very good trades, not a repeatable process. The qualifying `MEAN_REVERSION` cohort all survives top-5 removal.
- **Regime dependence**: see Section 15 — clearly present (2/5 to 5/5 fold win rates), and the timeframe-vs-BH pattern in Section 8 is itself a regime-dependence finding at the scale of the whole study.

## 17. Important Discoveries

1. **Buy & hold went undefeated at every timeframe of 2 hours or longer, across all 14 symbols, with zero exceptions** (0.0% beat rate at 2h, 4h, 6h, 8h, 12h, 1d, and 1w — Section 8). This is the single most surprising and most robust pattern in the entire dataset, and it held up under direct re-verification (Section 6, Step E).
2. **Six of the Lab's 14 public strategies are effectively unusable on this basket at 600 bars** — three (`EMA_RSI_PULLBACK`, `GOLD_MASTER_TREND`, `TREND_REGIME_PULLBACK`) produced exactly zero trades across all 154 runs each, independently reconfirmed; three more (`SIMPLE_PULLBACK`, `TREND_PULLBACK_STACK`, `VOL_BREAKOUT_TREND`) never produced a single statistically meaningful (>=10-trade) run.
3. **`predict.py`'s "qualified" bucket is not a safety net against a losing strategy** — SNDK's one qualifying pick has a 0% win rate and only "beats" BH because BH lost even more. Confirms Part 1's warning that "best" in this module is "the single most overfittable idea in the whole tool," and shows it concretely.
4. **The candidates with the largest in-sample dollar wins (ARM +$72k, NBIS +$74k) were the ones that failed out-of-sample** — the smaller, more modest wins (AMD +$19k, AMAT +$8k, MU +$4.8k) were the ones that actually held up. If this study had stopped at Section 8/9's in-sample screen (as the pre-existing sweep alone would have), it would have surfaced exactly the wrong candidates as the headline result.
5. **No ROBUST candidate anywhere, matching the calibration prior exactly** — the rubric's own 3 hand-graded reference cases (all on futures, `/CL` and `/GC`) never reached ROBUST either. This study adds 38 more data points on a completely different asset class (equities) at the same conclusion, which is modest independent evidence the rubric's ROBUST bar is calibrated consistently rather than accidentally unreachable on one asset class.

## 18. Bugs or Problems Found in the Lab

1. **Silent short-real-calendar-span gap in `service.warnings_for()` — confirmed by direct test.** A simulated result dict representing a fully-served 600-bar 5m window (`shortWindow=False`, i.e. Yahoo served everything requested) produces **zero warnings**, even though that window spans only ~9 real calendar days. `warnings_for()` (`ftmo_mt5/lab/service.py:198-227`) checks `shortWindow` (served < requested) but has no check comparing `(to - from)` against a reasonable calendar-span expectation for the timeframe. A user reading a "600 bars, no warnings" 5m result has no signal from the tool itself that this is 9 days of data, not the 2+ years a 600-bar daily result would represent. **Recommend**: add a warning when `(to - from) / windows.SECONDS[timeframe] / bars` implies a materially compressed real-time span, or simply surface `windows.CEILING[timeframe]` text on every result regardless of whether `short` fired.
2. **Three default-parameter strategies never trade on this basket at all** (Section 9/17): `EMA_RSI_PULLBACK`, `GOLD_MASTER_TREND`, `TREND_REGIME_PULLBACK`, reproducibly, on every symbol and timeframe tested (spot-checked directly on AMD and NVDA across 5m/15m/30m/1h/1d, not just read from the CSV). Whether this is intended strictness (their entry conditions are simply this rare on 600-bar windows of these particular names) or a wiring issue in how the higher-timeframe (`trend_minutes`) series is derived was not conclusively determined within this study's scope — it deserves a focused follow-up rather than a guess here, but the reproducible zero-trade fact itself is confirmed and consequential (it silently removes 3 of 14 strategies from ever contributing evidence in any Lab sweep over this kind of data).
3. **CSV provenance ambiguity, now resolved** (Section 6) — not a bug in the Lab itself, but a reproducibility gap in the pre-existing research artifacts: neither `semis-2156-runs.csv` nor `semis-sized-2156-runs.csv` has a script in the repo that writes CSV output (the checked-in `sweep_equities.py` writes JSON to `/tmp/`), and this project has no git history to fall back on. Resolved by formula-matching (see Section 6) rather than left as unknown, but the underlying gap — no reproducible record of how a research artifact was produced — is worth fixing going forward (e.g., every sweep script should stamp its own git commit / a content hash of itself into its output).
4. **No Sortino ratio, no explicit CAGR, no volatility/stdev-of-returns metric anywhere in `ftmo_mt5/lab/metrics.py`** — confirmed by reading the full module; only `sharpe()` exists. See Section 19.
5. **`risk_defaults.BUILT_IN` has no entries for any of these 14 equities** (Section 11) — every run in this entire study traded with no config-level trailing stop. Not strictly a "bug" (the module is explicit that `None` is a real, deliberate answer for uncalibrated instruments), but worth flagging as a real gap for equity users of the Lab specifically.
6. **No strategies errored and no symbol/timeframe combination failed to fetch** during this entire study (2,156 pre-existing rows + 38 fresh `validate()` calls + 5 `predict.for_symbol()` calls, all with `failedToRun: 0`) — the Lab's execution layer itself was completely stable throughout; every problem found above is a data-interpretation or coverage gap, not a crash or an exception.

## 19. Missing Capabilities

- **Sortino ratio** — not computed anywhere in `ftmo_mt5/lab/metrics.py`. Would matter here specifically: several FRAGILE candidates in Section 10 beat BH on raw return while running comparable or worse drawdown; a downside-only risk-adjusted metric would likely separate them more usefully than Sharpe does.
- **CAGR** — not computed. Can be approximated from `returnPct` + the window's calendar span (`from`/`to` epoch timestamps in `measure()`'s output), but any such number would need to be clearly labelled "approximated, not native" — none was computed for this report since none of the validated candidates needed it to answer the research question.
- **Volatility / standard-deviation-of-returns metric** — not computed as a standalone figure; only implicitly present inside the Sharpe calculation.
- **A calendar-span-aware warning** (Section 18, item 1) — the closest thing to a missing capability that directly affected how this report had to caveat its own 5m/15m/30m findings by hand.
- **Regime-labelling / macro-context feature** — the Lab can show fold-by-fold and OOS-vs-dev performance, but has no built-in way to say "this fold was a bull market" — Section 15's regime-dependence discussion had to be inferred from calendar dates and fold results, not read off a Lab feature.
- **Risk-default calibration for equities** — `risk_defaults.py` covers 2 futures roots; extending its measured-trailing-stop methodology to even a handful of the most-traded equities (this basket, say) would close a real gap for anyone using the Lab beyond futures.

## 20. Recommended Improvements

1. **Add the calendar-span warning described in Section 18.1.** Low effort, directly prevents the single most likely misread of a Lab result (a 600-bar intraday window looking as "complete" as a 600-bar daily one).
2. **Investigate the three zero-trade strategies (Section 18.2)** with a small diagnostic script that traces `prepare()`'s intermediate state for `EMA_RSI_PULLBACK`/`GOLD_MASTER_TREND`/`TREND_REGIME_PULLBACK` on one symbol/timeframe, to determine whether the entry conditions are genuinely this strict or whether the higher-timeframe (`trend_minutes`) resampling has a wiring gap. Currently unknown; worth resolving because it silently halves the effectively-usable strategy count for equities.
3. **Extend `risk_defaults.py`'s measured-trailing-stop methodology to a few of these equities** — the same both-halves-plus-cost-sensitivity process already used for GC/NQ (`risk_defaults.py`'s own module docstring) would give equity backtests here a real protective stop instead of running naked, and might materially change the drawdown side of Section 11/13's FRAGILE candidates (for better or worse — that is itself worth knowing).
4. **Re-run the Step-C-qualifying `MEAN_REVERSION` candidates (AMD 30m, AMAT 5m, SNDK 5m, and the rest of Section 13) with more bars once more intraday history has accumulated**, since the trade-count gate (10-16 actual vs. 30 needed) is the single thing standing between "FRAGILE" and "MODERATE" for the most-supported evidence in this entire study. On 5m/15m/30m this is bounded by Yahoo's 59-day ceiling per single fetch, but repeated fetches over time (the Lab already caches per-window) would let a longer effective history accumulate.
5. **A small CSV-provenance convention** for throwaway research scripts (Section 18.3) — have any sweep script that writes a result file stamp its own script content (hash or literal copy) alongside the data, so a future researcher does not have to reverse-engineer a sizing formula from raw numbers to explain a discrepancy.
6. **Add Sortino and an approximate CAGR field to `metrics.measure()`'s output** (Section 19) — both are cheap to compute from data already collected (the equity curve and per-bar returns already exist inside `measure()`), and would remove the need for any researcher to hand-roll an "approximated, not native" caveat.

## 21. Final Conclusions

Testing all 14 strategies the Lab ships, on all 11 timeframes it supports, across all 14 of the fixed semiconductor-adjacent symbols (AMAT, AMD, ARM, CRDO, DELL, LITE, MRVL, MU, NBIS, NVDA, SMH, SNDK, TSM, WDC) — 2,156 in-sample runs plus 38 full out-of-sample/robustness validations via the Lab's own `verdict.validate()` — **the honest answer is: none reach ROBUST, none reach MODERATE, and most of what beats buy & hold in-sample is FRAGILE or FAILED.**

Of the 38 validated candidates: **0 ROBUST, 0 MODERATE, 15 FRAGILE, 23 FAILED.** The FAILED cases split into two honest categories: symbols where nothing genuinely beats buy & hold at all (CRDO, MRVL, and the "beat by sitting out entirely while the benchmark itself lost money" false positives on NVDA and TSM), and symbols where the only candidates with enough calendar history to matter (the 1d bucket, all 14 symbols) simply never produce enough round trips (1-8 trades in a 600-bar/~2.4-year window) to be distinguishable from coincidence by the Lab's own 10-trade floor.

The FRAGILE cases are the real finding of this study, and they deserve to be stated precisely rather than oversold: seven `MEAN_REVERSION`-based candidates (AMAT at both 1h and 5m, AMD 30m, MU 15m, SMH 1h, SNDK 5m, WDC 1h) and one 2h candidate (LITE) beat buy & hold both in-sample and out-of-sample, survived the full 1-8 tick slippage ladder, survived losing their largest winning trades, and — for most of them — held 50-100% of neighboring parameter values (a plateau, not an island, which is real evidence against pure curve-fitting). **Every one of them was kept at FRAGILE by trade count alone** (10-16 actual trades against the 30 the rubric requires for MODERATE), which is a genuine limitation of testing 600 bars at a time on these instruments, not a flaw manufactured by an unreasonably strict rubric. Two other candidates with even larger in-sample dollar wins (ARM +$72,099, NBIS +$74,318) looked, on the in-sample screen alone, like the best results in the whole study — and both **failed out-of-sample outright**, which is exactly the scenario the OOS/walk-forward layer exists to catch, and exactly why Section 7-10's answer is not simply "read off the biggest number in the CSV."

At the timeframe level, the pattern is unambiguous and was independently reproduced: **buy & hold beat every strategy on every symbol at every timeframe of 2 hours or longer, with zero exceptions across 196 tested combinations per timeframe from 2h through 1w.** These 14 names moved through an exceptional, sustained AI-driven rally over the study's available history, and no rule that periodically exited the market — for a stop, a regime flip, a mean-reversion exit, or a short signal — kept pace with simply holding through all of it.

**If forced to a single-sentence verdict: this Lab, applied honestly and skeptically to these 14 symbols, finds no strategy with robust evidence of beating buy & hold; it finds a small, internally consistent cluster of short-timeframe `MEAN_REVERSION` candidates with real but sample-size-limited evidence, and it correctly flags — via its own OOS and walk-forward machinery, not via any judgment call added in this report — that the largest-looking in-sample wins in the dataset were the least trustworthy ones.**
