# Independent AI Trading Research Agent

A separate Claude Agent SDK process with three jobs, and no way to trade:

1. **Screen** the NASDAQ universe in parallel, incrementally.
2. **Choose strategies**: the project's existing rules, plus new candidates the AI designs as JSON specs under a fixed contract.
3. **Backtest** every promising stock × strategy on the project's own engine and compare it with Buy & Hold.

The output is a ranked leaderboard: Stock, Strategy, Return, Buy & Hold, Outperformance, Robustness, Verdict and Reason. It is exported to CSV and Excel.

**Research only.** The agent has no broker, order, account or position tool. It has no shell and no file access. This package imports no execution code. The boundary is enforced by tests, not by the prompt.

## Data flow

```
nasdaqtrader.com nasdaqlisted.txt ──► universe.py   ~5,600 listed → ~3,100 common stocks (ETFs, warrants,
                                                    units, rights, preferreds, test issues, deficient filers removed)
                                                    diffed daily into research.db (added / delisted)
Yahoo daily bars (Lab loader, per-day cache) ──► screener.py   8 processes · liquidity, price, ATR%, history,
                                                    trend, 1y return, overnight gaps → pass/fail + score
                                                    skips anything already screened today
shortlist (top N by score) ──► AI agent: market_snapshot → propose_strategy (JSON spec → validated → AI_<NAME>_<hash>)
                               catalog.py: 15 builtin + team/*.json + accepted AI specs
stock × strategy ──► backtester.py  phase "screen":    metrics.measure      (one backtest, vs Buy & Hold)
                                    phase "validated": verdict.validate     (OOS, cost ladder, top-trade removal,
                                                                              walk-forward, parameter sensitivity)
                                    8 processes · fingerprint dedupe · same ftmo_mt5.backtest engine as everything else
tests table ──► ranking.py  fixed verdict policy → Recommended / Watch / Rejected + reason
            ──► export.py   leaderboard-*.csv / .xlsx       agent ──► write_report → reports/*.md
```

## Commands

```bash
PYTHONPATH=. venv/bin/python3 -m research_agent universe          # refresh NASDAQ listing
PYTHONPATH=. venv/bin/python3 -m research_agent screen            # screen what isn't fresh today (~10 min cold, all 3,100)
PYTHONPATH=. venv/bin/python3 -m research_agent watch --interval-min 30   # continuous: keep universe + screen current
PYTHONPATH=. venv/bin/python3 -m research_agent strategies        # builtin / team / ai
PYTHONPATH=. venv/bin/python3 -m research_agent backtest --symbols AAPL,NVDA --strategies all --validate
PYTHONPATH=. venv/bin/python3 -m research_agent research --shortlist 25   # whole pipeline, no LLM, existing strategies
PYTHONPATH=. venv/bin/python3 -m research_agent agent --shortlist 15 --max-new 4 --budget-usd 5
PYTHONPATH=. venv/bin/python3 -m research_agent leaderboard --best-per-stock
PYTHONPATH=. venv/bin/python3 -m research_agent export
PYTHONPATH=. venv/bin/python3 -m research_agent status
PYTHONPATH=. venv/bin/python3 -m pytest tests/test_research_agent.py -q
```

Global options include `--workers`, `--bars` (default 2,500 ≈ 10 years), `--target-pts` (default 50) and `--promote-grade` (default MODERATE). The agent needs `claude-agent-sdk` and the export needs `openpyxl`. Both are in the `research` extra of `pyproject.toml`.

## The 10 timeframes

Every shortlisted stock is tested with every strategy on all 10 timeframes. Each result is compared with Buy & Hold on the same bars, the best combinations are validated, and the results are ranked. The 10 are defined once, as `TIMEFRAMES` in `config.py`:

**5m · 15m · 30m · 1h · 2h · 4h · 6h · 8h · 1d · 1w**

- **Where they come from.** This is the Lab's 11 timeframes minus 12h, which on US stocks is the daily bar again (chosen 2026-10-03).
- **How they're used.** The backtester, the agent's tools and prompt, the leaderboard, the dashboard, the Excel workbook and the Google Sheet all read that one tuple. A test fails if a timeframe is written out anywhere else in `research_agent/`.
- **History.** 1d uses the last `Settings.bars` (2,500 bars, about 10 years). Every other timeframe uses all the history Yahoo serves: about 60 days for 5m–30m, about 2 years for 1h–8h, and about 26 years for 1w.
- **Screening** stays on daily bars, because it measures the stock, not a strategy.
- **Validation** takes the best 3 strategy × timeframe combinations per stock.
- **Results** show Stock, Strategy, **Timeframe**, Return, Buy & Hold, Outperformance, Robustness and Verdict everywhere. A new **Timeframes** tab (dashboard, Excel and Sheet) lists the 10 with their history, real window, tests, wins and best result.

Known caveats:

- **Outperformance points depend on the window.** A 26-year weekly result and a 60-day 5m result are not on the same scale, so validation tends to pick each stock's longest or most favourable window.
- **Annualised statistics are overstated on intraday stock data.** The Lab annualises Sharpe and CAGR with futures calendars (e.g. 5,716 hourly bars a year; US stocks have about 1,764). Windows shown in the reports use real bar dates instead.

## 24/7 agent: worker and Control Center

```
UI (Control Center, /research)  ──POST start/pause/stop/run-now/settings──►  control.py  (desired state, in research.db)
                                                                                 │
          ◄──GET status · events · runs (every 3 s)──  agent_worker / agent_events tables
                                                                                 ▲
worker.py  (separate process: python -m research_agent worker)  ── heartbeat, stage, progress, events ──┘
   └─ every interval / on Run now → agent.run_agent → Claude Agent SDK → 13 research tools → research.db
```

| Command | Effect |
|---|---|
| **Start 24/7** | Schedule on, with the first run now. Starts the worker process if needed |
| **Pause** | No new runs. A run in progress finishes |
| **Stop** | Cancels the run in progress and shuts the worker down |
| **Run now** | One cycle immediately. When stopped, it runs once without turning the schedule on |
| **Settings** | Scan interval, shortlist size, max new strategies, budget per run, daily budget, model, max turns, focus |

The same commands are available from the terminal: `python -m research_agent control start|pause|stop|run-now|status`.

**Recovery.** All state lives in SQLite.

- If the worker dies mid-run, the next worker records that run as *interrupted* and continues the schedule.
- If the agent was left on, the hub (and `ui`) restart the worker when they start, and on every status poll.
- Only one worker can run at a time (pid + heartbeat).

**Unattended guards.**

- Per-run budget: a hard SDK cap.
- Daily budget: no new run starts once today's spend would pass it.
- Auto-pause after 3 consecutive failed runs, for example an expired `claude` login. The reason appears in the UI.

**Safety.**

- The worker runs exactly the same locked-down `run_agent`: `tools=[]`, research MCP only, deny hooks.
- It is spawned with a scrubbed environment, so none of the hub's broker credentials reach it.
- The Control Center can only change research state. It has no order path.

## Dashboard, Excel and Google Sheet

All three read `views.py`, which reads the store. There is no second query, cache or verdict policy, so they cannot disagree.

| Where | What |
|---|---|
| `http://127.0.0.1:8600/research` | Inside the FTMO hub (Terminal → AI Research). Needs a hub restart after updating: `./start.sh --stop && ./start.sh` |
| `python -m research_agent ui` | The same dashboard on its own, on port 8602, without the trading hub |
| **Excel** button / `GET /api/research/export.xlsx` | Workbook with the tabs Summary · Leaderboard · Strategy Tests · Screening |
| **Google Sheet** button / `python -m research_agent sheets-sync` | The same four tabs, rebuilt on every sync. Values are written RAW, so data is never evaluated as a formula |

The dashboard has five parts:

- **Pipeline:** universe → screened → shortlisted → backtested → verdicts.
- **Headline finding:** the top of the board, in plain English.
- **Leaderboard:** the best strategy per stock. Click a row to see every strategy tested on that stock.
- **Strategy tests:** every test, with filters.
- **Screening:** all ~3,100 stocks, with PASS/FAIL and the exact reason. Then a "Runs & how to read" tab with a glossary, the settings and recent runs.

Google Sheet setup (one time):

1. In Google Cloud, enable the Sheets API, then create a service account and a JSON key for it.
2. Save the key as `research_agent/state/google-service-account.json` (gitignored), or set `GOOGLE_SERVICE_ACCOUNT_FILE` to its path.
3. Create a Sheet and share it, as Editor, with the service account's email. The dashboard's Google Sheet panel shows that email.
4. Paste the Sheet URL into the panel, or run `sheets-sync --sheet <url>`, or set `RESEARCH_SHEET_ID`.

## Management credibility (`quality/mcs.py`)

The Management lens of the quality engine. It replaces the dashboard's "Beat = 1, Hit = 0.5, Miss = 0" score, which ranked a sandbagged guide above an accurate one and, for 88 of 93 stocks, graded guidance its own script had invented.

- **Ledger, one per company:** `quality/ledgers/<SYMBOL>.json`. It holds the guidance given on call N, the actuals reported on call N+1, and forward commitments logged when they were made. Every number carries its source (FMP transcript, press release or prepared remarks).
- **Score per quarter:** 60% *delivery* (kept 1, partly kept 0.5, not kept 0) plus 40% *accuracy* (100 inside the guided range, minus 4 points per 1% outside it, doubled for a shortfall).
- **Market credit:** when management attributes a surprise to price, accuracy is graded mainly on what it controls.
- **EPS** is shown but not scored, because it would count the same surprise a third time.
- **Discretionary spending:** opex decided after the guide is graded out of the score and flagged.
- **Pending commitments** are never scored.
- **Drafting a ledger:** `mcs extract` parses guidance sentences from a saved transcript into draft items, which must be checked against the press release. This repo's FMP key cannot read transcripts (HTTP 402), so save them from the FMP connector.

```bash
PYTHONPATH=. venv/bin/python3 -m research_agent mcs score                 # every ledger
PYTHONPATH=. venv/bin/python3 -m research_agent mcs score MU --json
PYTHONPATH=. venv/bin/python3 -m research_agent mcs extract transcript.json --period "FQ1 FY27"
PYTHONPATH=. venv/bin/python3 -m pytest tests/test_mcs.py -q
```

## Verdict policy (`ranking.py`, fixed and in code)

**Outperformance** = strategy return − Buy & Hold return over the same bars, in **percentage points**. For example, +130% against +80% is 50 points.

| Verdict | Rule |
|---|---|
| **Recommended** | Validated, robustness grade ≥ MODERATE, profitable, and outperformance ≥ target (default 50 pts) |
| **Watch** | Profitable and meets the target, but not yet validated or graded below MODERATE |
| **Rejected** | Under 10 trades, lost money, trails Buy & Hold, or beats it by less than the target |

Robustness grades come from the Lab's `verdict.validate`:

- **MODERATE** needs all of: ≥30 trades, an out-of-sample beat, ≥50% of parameter variations still beating, a beat at 2 ticks of slippage, and a beat with the top 3 trades removed.
- **ROBUST** raises the bar.

The agent reports verdicts. It cannot set them.

## Strategy contract (`strategy_spec.py`)

An AI strategy is data, not code:

- **Parameters:** 1–8 named numeric params. The first is the one the robustness test varies.
- **Rules:** `long_entry`, `long_exit`, `short_entry` and `short_exit` blocks, built from `{"all"|"any": [{left, op, right}]}`.
- **Expressions:** `close/open/high/low/volume`, or `sma/ema/rsi/atr/atr_pct/adx/highest/lowest/roc/volume_sma` with a `period`, plus optional `mult` and `shift`.

The validator also enforces the following:

- Period parameters are integers named `*_period` or `*_lookback`, so the engine's warm-up inference works.
- Every parameter is used.
- Every entry side can exit.
- At most 12 comparisons, nested at most 3 deep.

Everything is causal. `highest` and `lowest` use the bars before `i`, and the engine fills on the next open.

Each spec is named `AI_<NAME>_<hash6>`, so the same logic always gets the same name and is deduplicated. Every proposal, accepted or rejected, is appended to `state/strategy_log.jsonl` with its rationale. Human-written specs go in `strategies/team/*.json`.

## Agent safety (`agent.py`)

| Layer | What it does |
|---|---|
| `tools=[]` | No built-in tools exist in the session (no Bash, Read, Write, Edit, WebFetch, Task, ...) |
| `strict_mcp_config`, `setting_sources=[]` | Only the in-process `research` server. The FTMO MCP server, broker hub and user settings are not loaded |
| PreToolUse hook | Denies any tool outside `mcp__research__*` |
| `can_use_tool` | Deny by default. Nothing is pre-approved, so it sees every call |
| Tool caps | ≤600 stocks per screen call, ≤30 symbols × 30 strategies per backtest call, a per-run backtest budget, a proposal limit, `max_turns`, `max_budget_usd` |
| File writes | Only `state/reports/<run_id>-<slug>.md` and `state/exports/leaderboard-<run_id>-*` |

Each run's transcript is saved to `state/runs/<run_id>/transcript.jsonl`. It records every message, every tool call with its arguments, and every denial.

## State (`research_agent/state/`, gitignored)

| Path | Contents |
|---|---|
| `research.db` | universe · screens · strategies · tests · runs |
| `cache/YYYY-MM-DD/` | Yahoo bars for the day, shared by screening and backtests |
| `universe/` | dated copies of the raw NASDAQ listing |
| `exports/`, `reports/`, `runs/`, `strategy_log.jsonl` | leaderboard exports, agent reports, run transcripts, proposal log |
