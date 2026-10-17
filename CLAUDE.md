# FTMO_MT5

Algorithmic trading research platform. Two independent systems in one repo:

1. **Research Lab** (`ftmo_mt5/lab/`) — backtesting + robustness validation. Where almost all real work happens.
2. **Live trading loop** (`ftmo_mt5/main.py`) — autonomous signal → risk → execution pipeline. Rarely run; see Status.

Plus a **manual multi-broker terminal** (`broker_hub.py`, port 8600) and an **MCP server** (`mcp_server/`) exposing the Lab as tools.

---

## TRADING MODE: PAPER ONLY

**Every broker configured in this repo is a simulated/paper account. No live-money account is configured.**

| Broker | Account | Mode | Verified |
|---|---|---|---|
| MT5 | `MT5_SERVER=FTMO-Demo` | FTMO **demo** — simulated | 2026-09-26 |
| tastytrade | `6AC40091`, `TT_SANDBOX=1` | **sandbox** — simulated | 2026-09-26, gateway self-reports "(sandbox)" |
| Webull | `api.sandbox.webull.com` | sandbox, not connected | — |

Paper trades may be executed to validate the system end to end.

**Before sending any order, re-verify the mode** — never assume from this file:
```bash
curl -s http://localhost:8600/api/brokers | grep -i 'sandbox\|demo'
grep -E '^(TT_SANDBOX|MT5_SERVER)=' .env
```
If a broker does not self-report demo/sandbox, **do not send an order**. If live credentials are ever added, this section must be rewritten first and paper-trade permission revoked.

**Paper-trading notes (verified 2026-09-26):**
- Hub broker keys are `mt5`, `tasty`, `webull`, so the order route is `/api/tasty/order`, not `/api/tastytrade/...`.
- The hub always sends MARKET orders, and brokers refuse those while the market is closed (tastytrade: `tif_no_after_hours_opening_market_orders`). Test stock orders during US market hours; on weekends only MT5 crypto CFDs trade.
- The hub drops the broker's rejection text: a refused order comes back as `retcode -1` with an empty `comment`. To see the real reason, reproduce with the gateway and `place_order(..., dry_run=True)` after asserting `gw.sandbox is True`. A dry run is validated by the broker but never placed.
- The autonomous bot (`main.py`) supports only `BROKER=mt5` or `BROKER=webull`. tastytrade is terminal-only.
- FTMO also runs paid challenge and funded accounts on its demo servers. "Demo" means no real money moves, not that FTMO rule breaches are free, so keep MT5 test trades tiny and inside FTMO's rules.

Never read, print, or echo credential *values*. Names only.

---

## Layout

```
ftmo_mt5/
  lab/          windows, metrics, service, verdict, predict   ← the research engine
  signals/      strategies.py (14) + hold_strategies.py (2)   ← 16 registered
  backtest/     engine.py (fills, slippage, commission), models.py
  engine/       rule_engine.py — FTMO MDL/ML compliance
  agents/       coordinator, signal, risk, execution
  execution/    broker_base.py (BrokerGateway protocol) + mt5/remote/tastytrade/webull gateways
  marketdata/   yahoo.py, replay.py, registry.py
  store/        event_store.py — append-only JSONL
  observability/ health.py, telemetry.py, logging_config.py
  supervisor/   watchdog.py — heartbeat kill switch
  main.py       live loop entry point
mcp_server/     MCP server: 6 tools, 3 resources, results index  (NEW since Sep 9)
broker_hub.py   FastAPI terminal + lab API, port 8600
mt5_api_server.py   runs in the Windows VM, port 8500
systematic/     paper-only pipeline: watchlist → screen → validate → rank → paper-trade (see systematic/README.md)
                settings/ cost+window presets · watchlists/ · FINDINGS.md (calibration results)
research_agent/ AI research agent (Claude Agent SDK, separate process): NASDAQ screen → strategies
                (existing + AI-proposed JSON specs) → backtest vs Buy & Hold → leaderboard + Excel.
                Research only — no broker tools. See research_agent/README.md
                10 timeframes (5m 15m 30m 1h 2h 4h 6h 8h 1d 1w) defined ONCE in research_agent/config.py TIMEFRAMES
study/lab/      ad-hoc research scripts + cached Yahoo data + results_index.db
.claude/skills/ 6 project skills (lab-research, add-strategy, start-terminal, add-broker, manager-report, cross-review)
```

**On the Windows research PC?** Follow `deploy/windows/SETUP_WITH_CLAUDE_CODE.md`. That PC runs the research side only (dashboard + worker on 127.0.0.1:8602, RAG, MCS); never the broker hub, the MT5 bridge or `ftmo_mt5.main`, and never broker credentials.

**Ignore `_archive/`.** It holds the retired `src/nehanth` package. Earlier versions of this file pointed at it; that was wrong. `pyproject.toml`'s console script still names `nehanth.main:main` and is **broken** — run `python -m ftmo_mt5.main` instead.

---

## Hard rules

1. **`Decimal` for all money/prices.** Never let a float reach an order. Precision 10, set in `main.py`.
2. **Prague time via `zoneinfo.ZoneInfo("Europe/Prague")`.** Never a hardcoded UTC offset — the daily-loss midnight boundary depends on it.
3. **No `asyncio.gather()` on the signal → risk → execution path.** `Coordinator.process_tick()` stays sequential.
4. **HALT is never auto-recoverable.** A risk-agent exception becomes `CircuitBreakerError` and stops the system. Manual restart only.
5. **`EventStore` is append-only.** No edit, no delete.
6. **MT5 calls are serialized** with `threading.Lock()`. No concurrent order submission.
7. **`mcp_server/` must never import `ftmo_mt5.execution`, `ftmo_mt5.agents`, or `ftmo_mt5.main`.** That import boundary is what makes the Lab tools structurally incapable of placing an order.
8. **Strategy parameter types may only be `int`, `float`, `bool`.** A registry test enforces it.
9. **No look-ahead**: `evaluate(candles, i, state)` reads only bars `0..i`.
10. **`systematic/` must never import `ftmo_mt5.execution`, `ftmo_mt5.main`, `broker_hub`, `mt5_api_server` or any broker SDK.** It trades only through `PaperBroker`. `tests/test_systematic.py` enforces this by importing the whole package in a clean process.
11. **`research_agent/` must never import broker/execution code** (same list as rule 10, plus `systematic.trader`, `systematic.paper_broker`, `mcp_server.server`). Its Claude session gets `tools=[]`, `strict_mcp_config`, `setting_sources=[]`, a PreToolUse deny hook and a deny-by-default `can_use_tool` — only `mcp__research__*`. AI strategies are JSON specs validated by `research_agent/strategy_spec.py`, never code. `tests/test_research_agent.py` enforces all of it.

---

## Commands

```bash
# tests (165 passing)
PYTHONPATH=. venv/bin/python3 -m pytest tests/test_lab.py tests/test_backtest.py tests/test_mtf_strategy.py mcp_server/test_server.py -q

# terminal (Mac). VM must already run MT5 + mt5_api_server.py for MT5 to work
./start.sh          # port forwarder + hub on :8600
./start.sh --stop
./start.sh --bot    # ALSO starts the autonomous bot — only when explicitly asked

# systematic paper trading (team watchlist in systematic/watchlist.txt)
PYTHONPATH=. venv/bin/python3 -m systematic research   # screen → validate → rank
PYTHONPATH=. venv/bin/python3 -m systematic cycle      # one paper cycle, after the US close
PYTHONPATH=. venv/bin/python3 -m systematic ui        # web panel on http://127.0.0.1:8601 (add stocks, P&L, trade history)
PYTHONPATH=. venv/bin/python3 -m pytest tests/test_systematic.py -q

# AI research agent (research only; state in research_agent/state/)
PYTHONPATH=. venv/bin/python3 -m research_agent screen        # parallel NASDAQ screen (skips what's fresh today)
PYTHONPATH=. venv/bin/python3 -m research_agent research      # deterministic pipeline, existing strategies, Excel export
PYTHONPATH=. venv/bin/python3 -m research_agent agent --budget-usd 5   # Claude-driven run incl. new strategies
PYTHONPATH=. venv/bin/python3 -m research_agent leaderboard
PYTHONPATH=. venv/bin/python3 -m research_agent ui            # dashboard + Agent Control Center on :8602 (also /research in the hub)
PYTHONPATH=. venv/bin/python3 -m research_agent control status   # 24/7 worker: start|pause|stop|run-now|status (worker = separate process)
PYTHONPATH=. venv/bin/python3 -m research_agent sheets-sync   # push Summary/Leaderboard/Strategy Tests/Screening to Google Sheets
PYTHONPATH=. venv/bin/python3 -m research_agent mcs score MU   # management credibility (MCS v2) from research_agent/quality/ledgers/<SYM>.json
PYTHONPATH=. venv/bin/python3 -m pytest tests/test_research_agent.py -q

# replay the paper cycle over real past bars (proves fill/exit/risk plumbing, NOT an edge)
# --settings MUST match the research run, or costs differ and the halt is spurious
PYTHONPATH=. venv/bin/python3 scripts/paper_replay.py --state systematic/state-10y \
  --settings systematic/settings/stock-costs-10y.yaml --days 250

# health
curl -s localhost:8600/api/brokers      # per-broker status + mode
curl -s localhost:8500/api/health       # MT5 bridge (empty = VM down)
```

The dashboard has shown "Connected to MT5 bridge" while the bridge was refusing connections. **Trust the curl, not the toast.**

---

## Research rules

- A "beats buy & hold" from a backtest is a **lead, not a finding**. Only `verdict.validate()` grading **MODERATE** or **ROBUST** counts.
- Canonical run settings: `bars=600`, `allow_short=True`, `size_to_capital=True`. A script that silently disagreed once reported 5 trades where the correct run had 10.
- `<10 trades` = noise. `topTradeShare >= 50` = one trade carrying the result.
- Check `lab_lookup` first — ~3,500 runs are already indexed and the Lab is deterministic on cached data.
- Cost model is USD-calibrated and currency-blind; say so when testing `.NS`/`.BO` symbols.

---

## Status (2026-09-26)

- **Research Lab: working and heavily used.** ~3,500 indexed runs across 18 symbols. Standing result: **0 ROBUST, 0 MODERATE** — no strategy has beaten buy & hold on a validated basis; buy & hold wins at every timeframe of 2h+.
- **MCP server: working.** 6 tools, 3 resources, connected via `.mcp.json`.
- **Terminal: working** for tastytrade sandbox. MT5 needs the VM up.
- **Systematic paper pipeline (`systematic/`): built 2026-09-26, working.** 20 tests pass (185 in the scoped suite). The first run, on the 10-stock mega-cap sample at the Lab's default costs, graded **everything FAILED**. Two calibration choices were doing most of that — see **`systematic/FINDINGS.md`**:
  - The Lab's `$2.50 per unit` commission is a **per-contract** figure. Charged per *share* it ate **70% of gross profit** in the first real-data replay. At $0.005/share, 17 candidates moved out of FAILED.
  - MODERATE needs **>=30 trades**; daily mean reversion fires ~16 times in 600 bars, so the grade was **window-limited, not quality-limited**.
  - Corrected (37-symbol diversified list, stock costs, 2500 bars): **7 MODERATE — the first non-FRAGILE grades in this project.** Promoted: VZ, IEF, TLT, GIS, NKE. Still **0 ROBUST**, and 7 clearing out of 555 screens is not clearly better than chance, so it must be confirmed forward.
  - The earlier note that this "isn't a cost or window artefact" was measured on the mega-cap sample and **does not hold** on a regime-diverse list.
  - A 250-day replay of the promoted rules **halted on an FTMO MDL breach** (~-$5.3k float across 4 positions). The risk system behaved correctly; the rules hold through FTMO-failing drawdowns. **Sizing is the open work, not rule selection.**
- **Live bot (`--bot`): unproven.** The event store contains only `CircuitBreakerTriggered` heartbeat-miss events; it has never recorded a completed trade cycle.
- **Known broken:** `pyproject.toml` entry point; `scripts/paper_trade.py` is a stub; three health checks in `observability/health.py` return hardcoded `healthy=True`; `config/1step.yaml` and `2step.yaml` referenced by docs don't exist.
- **Hub sends market orders only** — no limit/queued orders, so nothing can be placed while markets are closed.
- **Full test suite: 387 tests, 373 pass, 14 fail.** The 7 failures in `test_execution.py` are stale tests, not order bugs: they assert wrong MT5 return codes (e.g. `10023` as "connection"; it is ORDER_CHANGED, and connection is `10031`), while the code's retcode sets match MT5's docs. The rest are a Hypothesis fixture health check and time-of-day-dependent tests. The scoped command above (165) is all green.
- **The running hub logs to its console only.** `logs/broker_hub.log` stopped updating on 2026-09-21.
- **No git repository.** History is file timestamps only.
