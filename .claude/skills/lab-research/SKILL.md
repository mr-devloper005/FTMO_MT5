---
name: lab-research
description: Research whether a strategy beats buy & hold on a symbol using the FTMO_MT5 Lab. Use when asked to test, backtest, screen, or validate a symbol or strategy, "beat buy and hold", try timeframes, try an exit rule, or find the best strategy for a stock.
---

# Lab research

Answer "does anything beat buy & hold here?" with evidence, not a script per question.

## Order of operations

1. **Check what is already known.** Call `lab_lookup(symbol, timeframe, strategy)` before running anything. ~3,500 results are indexed and the Lab is deterministic on cached data — a repeat run returns the same number. If it's already tested, report that and stop unless the user wants a re-run.
2. **Get real strategy names.** Call `lab_list_strategies`. Never guess a name.
3. **Screen.** `lab_run_matrix(symbols, timeframes, strategies=[...])`. Returns a condensed summary by default; don't pass `full_detail=true` unless asked.
4. **Validate survivors only.** For each cell that beats B&H, call `lab_validate_strategy`. Nothing else counts as evidence.
5. **Report** using the format below.

If the MCP tools aren't connected, use the same functions directly: `from ftmo_mt5.lab import service, verdict, windows` and run with `PYTHONPATH=. venv/bin/python3` from the repo root. Put any script in `study/lab/`.

## Defaults — never change silently

`bars=600`, `allow_short=True`, `size_to_capital=True`, `allow_network=False`.

A script that left `allow_short` off once reported 5 trades where the correct run had 10. If the user wants different settings, state the change in the report.

## Rules of evidence

- A "beats B&H" from `lab_run_backtest` or `lab_run_matrix` is a **lead, not a finding**.
- Only call something a result if `lab_validate_strategy` grades it **MODERATE** or **ROBUST**. FRAGILE and FAILED are not wins.
- Fewer than 10 trades → say so; it is noise.
- `topTradeShare >= 50` → one trade carries the result; say so.
- Intraday timeframes (5m/15m/30m) span only days of real time — say so.
- Report **both** benchmarks: `bhReturnPct` (to last bar) and `bhPeakReturnPct` (to peak). An exit rule competes against the peak.
- Don't keep searching until something wins. Enough combinations on one dataset always produce a false positive. Stop when the search is done and report the honest result, including "nothing found".

## Indian symbols

Yahoo tickers use `.NS` (NSE) or `.BO` (BSE), e.g. `TARIL.NS`. The cost model is USD-calibrated and currency-blind — mention it.

## Report format

1. One-line answer: did anything beat B&H, validated or not.
2. Table: symbol · timeframe · strategy · trades · return% · B&H% · alpha (pp) · verdict.
3. B&H to peak, peak date, and give-back from peak.
4. Caveats that apply (trade count, intraday span, currency).
5. Suggested next question — never "keep trying".
