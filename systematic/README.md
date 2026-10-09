# Systematic paper trading

Research, validate, rank and **paper-trade** the team's stocks, using the existing FTMO_MT5 Lab, strategy library, `RiskAgent` and `EventStore`.

> **Paper only.** Orders go to `PaperBroker`, an in-process simulated ledger. This package imports no broker gateway, no `ftmo_mt5.main`, no `broker_hub` and no `mt5_api_server`, and `tests/test_systematic.py` fails if it ever does. There is no live switch to flip.

## The pipeline

```
watchlist.txt ─► screen ─► validate ─► rank ─► paper-trade
                 Lab       verdict     grade    RiskAgent-gated
                 matrix    .validate   first    PaperBroker fills
```

| Stage | What runs | Existing code it uses |
|---|---|---|
| **Screen** | All 15 strategies (the buy-and-hold baseline is excluded: it is the benchmark) on each symbol's latest 600 completed daily bars | `metrics.measure`, the same function `lab_run_matrix` wraps |
| **Validate** | The best 3 per symbol get the full robustness test: out-of-sample split, cost ladder, top-trade removal, 5-fold walk-forward, parameter sensitivity | `verdict.validate`, the same function `lab_validate_strategy` wraps |
| **Rank** | Best rule per symbol, **grade first** (ROBUST > MODERATE > FRAGILE > FAILED), then a robustness score within a grade | new: `research.rank` |
| **Promote** | Only rules at or above `min_grade` (default **MODERATE**), at most `max_positions` (5) | |
| **Paper-trade** | Re-run each promoted rule through the same backtest engine on today's data; every trade goes through `RiskAgent` | `run_backtest`, `RiskAgent.process`, `EventStore` |

Results are also written to the shared results index, so `lab_lookup` (MCP) sees them.

## Commands

```bash
python -m systematic research      # screen -> validate -> rank the watchlist; writes state/ranking.json
python -m systematic cycle         # one paper-trading cycle — run once per day after the US close
python -m systematic run           # both
python -m systematic status        # equity, positions, halt state
python -m systematic ui            # web UI on http://127.0.0.1:8601
python -m systematic reset-halt --by "Your Name" --confirm   # a person clears a halt
```

Options: `--watchlist FILE` (the team's list), `--settings FILE`, `--state DIR`, `--min-grade FRAGILE|MODERATE|ROBUST`.

## The UI

`python -m systematic ui` serves a local page on **127.0.0.1:8601** — separate process, separate
port from `broker_hub.py` (8600). The hub holds broker gateways and order routes; this one holds
none, so the page you click Run on lives in a process with no live order path.

Pages: **Dashboard** (P&L, equity curve, open positions, promoted rules), **My Stocks** (add and
remove tickers, live quotes, click any stock for price history, how it graded, and every trade on
it), **Trades** (full history, win/loss filters, CSV export), **Research** (the ranking), **Run &
Logs**, **Reports**.

It shows profit/loss first — net P&L in dollars and percent, whether you are up or down, an equity
curve, realized vs unrealized, commissions, win rate, best and worst trade, a per-position P&L
column and a full closed-trade history with the reason each trade was closed. Alongside that it
shows open positions, the promoted rules and the full ranking with each
rule's grade and the reason for it, and it runs research / cycle / replay by shelling out to the
same CLI above — so the UI can never do something the CLI would refuse. Clearing a halt still
requires a name and an explicit confirm. Bound to loopback; do not expose it.

## Giving it the team's stocks

**In the UI:** *My Stocks* → type or paste tickers → **Add**. Each one is checked against live data
before it is saved, so a typo or a delisted symbol is refused with the reason rather than failing
three minutes into a run. Remove with the `x` on a chip; **Create** makes a separate list.

**Or by hand:** edit `systematic/watchlist.txt`, or pass `--watchlist path/to/list.txt`. One ticker
per line; `#` starts a comment. Yahoo format (`BRK-B`, `RELIANCE.NS`). Futures are refused: the
paper broker sizes in shares.

## Risk control

- **Only graded rules trade.** A high score never substitutes for a grade. `FAILED` can never be a promotion floor, even on paper. Lowering the floor to `FRAGILE` is allowed for exploration, and every report then says **EXPLORATORY**.
- **Every order goes through the real `RiskAgent`** with the paper account's equity and FTMO limits from `config/default.yaml`: NORMAL full size, CAUTION half size, RISK_OFF closes only, HALT flattens everything.
- **A halt never clears itself** (hard rule 4). Every later cycle refuses to run until a person runs `reset-halt --by NAME --confirm`.
- **Sizing:** equal-weight slots (`equity / max_positions`), scaled by RiskAgent, capped at `max_gross_exposure` × equity.
- **No stale entries:** a rule already in a trade for more than `max_entry_age_bars` (1) is not joined mid-trade. The cycle waits for a fresh signal, so paper trades match what was validated.
- **Completed bars only:** today's daily bar is ignored until 16:15 New York time.
- **Audit trail:** every signal, risk decision, fill, rejection, close and halt is appended to `state/paper/events/events.jsonl`, the same append-only `EventStore` the live loop uses, in its own folder.

## Costs

By default research **and** paper fills use the Lab's own cost model: $2.50 commission per unit per side, plus 1 tick of slippage. For stocks "unit" means share, which is far above real-world stock commissions.

> **For any equity watchlist, use `--settings systematic/settings/stock-costs-10y.yaml`.** The default is a futures/forex per-contract cost; per share it consumed **70% of gross profit** in a real-data replay and suppressed grades across the board. That preset also widens the window to 2500 bars, because MODERATE needs >=30 trades and daily rules cannot reach that in 600. See `FINDINGS.md`.

Override both together in `settings.yaml` (`commission_per_unit`, `slippage_ticks`); research and the paper broker always use the same values. Pass the **same** `--settings` to `research` and to `scripts/paper_replay.py`, or the replay charges costs the grade never accounted for.

## What's where

| File | Purpose |
|---|---|
| `watchlist.txt` | the team's symbols |
| `settings.yaml` | pipeline settings (see `config.py` for every option and its default) |
| `state/ranking.json` | latest research result; the cycle trades from this |
| `state/paper/ledger.json` | the paper account: cash, positions, closed trades, equity history, halt |
| `state/paper/events/events.jsonl` | append-only audit trail |
| `state/reports/*.md` | a readable report per research run and per cycle |
| `state/cache/YYYY-MM-DD/` | that day's fresh bars (the Lab's own cache never refreshes) |

## Limits, stated plainly

- **Daily bars only.** Intraday paper trading needs an intraday completed-bar rule first.
- **Fills are at the last completed close**, not the next open the backtest uses. That's the realistic price for a decision made after the close, but paper P&L will differ slightly from backtest P&L.
- **The grade is relative to buy & hold.** On strongly trending winners (the sample list), rules can make large profits and still grade FAILED because holding did better. See the research report's "why" section.
