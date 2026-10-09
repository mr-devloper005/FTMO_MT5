# FTMO_MT5 — Project Context Handoff

Reconstructed by an AI agent from file-system evidence only (see Section 15 — no
git history exists). Every claim below was verified by reading the actual
source at the paths cited. Where confidence was insufficient, the item is
marked `UNKNOWN` rather than guessed.

---

## UPDATE 2026-09-26 — read this before the sections below

The body of this document describes the repo as of **2026-09-03**. These things changed after it was written. `CLAUDE.md` is now the maintained summary; prefer it where the two disagree.

**Trading mode: PAPER ONLY.** Every configured broker is simulated — MT5 on `FTMO-Demo`, tastytrade on `TT_SANDBOX=1` (account `6AC40091`, gateway self-reports "(sandbox)"), Webull on the sandbox endpoint. No live-money account is configured. Paper trades are permitted to validate the system; the mode must be re-verified from `/api/brokers` before any order.

**Added since Sep 3:**
- `mcp_server/` — MCP server with 5 tools (`lab_list_strategies`, `lab_lookup`, `lab_run_backtest`, `lab_run_matrix`, `lab_validate_strategy`) and 3 resources (`lab://reference`, `lab://metrics`, `lab://timeframes`), plus a SQLite results index holding ~3,500 prior runs. Wired via `.mcp.json`.
- `ftmo_mt5/signals/hold_strategies.py` — `HOLD_UNTIL_BROKEN`, `BUY_AND_HOLD_BASELINE`. Catalog is now **16** strategies, not 14.
- `ftmo_mt5/lab/metrics.py` — new metrics: `bhPeakReturnPct`, `bhGiveBackPct`, `bhCagrPct`, `cagrPct`, `peakEquityPct`, `vsBHPeakPct`, `windowYears`.
- `.claude/skills/` — 6 project skills.
- Test count is now **165**.

**Research result (unchanged in direction, now much better evidenced):** ~3,500 indexed runs, **0 ROBUST, 0 MODERATE**. Buy & hold beat every strategy tested at every timeframe of 2h or longer. A `HOLD_UNTIL_BROKEN` exit rule and ATR trailing stops from 2× to 20× were tested and all failed; widening the stop improved results only by converging toward doing nothing.

**End-to-end paper trade attempted 2026-09-26** via tastytrade sandbox: connection, auth, account read and positions read all succeeded; the order reached the broker and was rejected with `tif_no_after_hours_opening_market_orders` because US markets were closed (Saturday). Fill → position → close remains **unvalidated**. The hub sends market orders only, so it cannot place anything outside market hours.

---

## 1. Project Overview

FTMO_MT5 is an algorithmic trading system built to trade FTMO prop-firm
challenge accounts (and, more recently, other brokers) under strict,
programmatically-enforced risk rules. It is a multi-agent Python system
(signal generation → risk evaluation → order execution) plus two standalone
FastAPI web services that provide a human trading terminal, a strategy
research "lab", and a bridge to the Windows-only MetaTrader 5 (MT5) terminal.

The repository actually contains **two generations of the same product**:
- `_archive/src-nehanth/nehanth/` — an earlier, smaller, broken package
  (archived 2026-08-14, see Section 15).
- `ftmo_mt5/` — the current, larger, actually-running package.

Root directory: `/Users/yashnihalani/Downloads/FTMO_MT5`

## 2. Purpose & Goals

- Pass and then trade an FTMO evaluation/funded account without breaching
  FTMO's Maximum Daily Loss (MDL) or Maximum Loss (ML) rules — a breach fails
  the challenge outright, so risk enforcement is the system's central
  business-critical function (see `ftmo_mt5/engine/rule_engine.py`).
- Generate trading signals (currently a single EMA-crossover strategy, see
  Section 8) and route them through a deterministic, auditable
  signal → risk → execution pipeline.
- Provide a human-usable trading terminal (`broker_hub.py` +
  `trading_dashboard_live.html`) for manual oversight and multi-broker
  trading (MT5, Tastytrade, Webull), independent of the automated pipeline.
- Provide a repeatable strategy-research workflow (`ftmo_mt5/lab/` +
  `backtest_lab.html`) intended to replace an earlier ad-hoc research
  sandbox (`study/lab/`, see Section 15's hypothesis).

## 3. Users & Workflows

- **Operator/trader**: starts the system via `start.sh` (Mac) or
  `start_trading.bat`/`start_trading.ps1` (Windows, for the MT5 bridge),
  watches `trading_dashboard_live.html` at `http://localhost:8600`, uses
  `/game` (`beat_the_hold.html`) as an unrelated standalone game (explicitly
  documented as such — see Section 6), and uses `/lab`
  (`backtest_lab.html`) for strategy research.
- **Automated trading system** (`ftmo_mt5/main.py`, `ftmo-mt5` CLI entry
  point — currently broken, see Section 14): runs headless, polls the
  market, and trades through the signal → risk → execution pipeline with no
  human in the loop. Exposes health/readiness/metrics on port 8080.
- **Windows VM operator**: runs `mt5_api_server.py` + the real MT5 terminal;
  everything else (Mac-hosted `broker_hub.py`, or `ftmo_mt5/main.py` off
  Windows) reaches it over HTTP via `RemoteMT5Gateway`
  (`ftmo_mt5/execution/remote_gateway.py`) and
  `scripts/vm_port_forward.py`.
- **Researcher**: uses the `/lab` UI, `sweep_equities.py`, or the ad-hoc
  `study/` scripts to explore strategy parameters against historical data.

## 4. Technology Stack

- **Language**: Python ≥3.11 (pyproject.toml), venvs present at `venv/`
  (Python 3.12) and `.venv/`.
- **Core libs**: Pydantic v2 (frozen domain models), `structlog` (JSON
  logging), OpenTelemetry API/SDK + OTLP gRPC exporter (tracing/metrics),
  `prometheus-client`, `numpy`/`scipy`/`pandas`/`scikit-learn`,
  `pyyaml`, `aiohttp` (health server).
- **Web services**: FastAPI + Uvicorn for both `broker_hub.py` (port
  8600/`HUB_PORT`) and `mt5_api_server.py` (port 8500), `websockets` for
  live price streams.
- **ML extras** (`[project.optional-dependencies] ml`): `xgboost`,
  `lightgbm`.
- **Testing**: `pytest`, `pytest-asyncio` (auto mode), `pytest-cov`,
  `hypothesis` (property-based fuzzing).
- **Static analysis (configured, not verified as passing)**: `ruff`,
  `mypy --strict`, `black`.
- **requirements.txt pins** are dated to roughly early 2024 (e.g.
  `pydantic==2.6.1`, `opentelemetry-api==1.22.0`, `numpy==1.26.3`) — these
  are old and should be treated as a compatibility risk before resuming
  active dependency-sensitive work.
- **MT5 access**: the `MetaTrader5` Python package (Windows-only; imported
  conditionally, never installed/available on this Mac checkout).

## 5. Repository Structure

```
FTMO_MT5/
├── ftmo_mt5/                  # THE live package (see Section 6)
│   ├── main.py                 # async entry point, CLI: `ftmo-mt5`
│   ├── core/                   # models.py, constants.py, exceptions.py
│   ├── engine/rule_engine.py   # FTMO MDL/ML compliance engine
│   ├── agents/                 # signal_agent, risk_agent, execution_agent,
│   │                           #   coordinator, base_agent
│   ├── strategy/ema_crossover.py  # the ONLY strategy main.py actually runs
│   ├── execution/               # broker gateways (mt5, remote, tastytrade,
│   │                           #   webull), mt5_executor, reconciliation
│   ├── store/event_store.py    # append-only JSONL event store
│   ├── observability/          # health.py, logging_config.py, telemetry.py
│   ├── supervisor/watchdog.py  # heartbeat monitor + kill switch
│   ├── signals/                # strategies.py (2290 lines, lab-only, see
│   │                           #   Section 8), indicators.py, patterns.py,
│   │                           #   engine.py (SignalEngine, used by backtest)
│   ├── backtest/                # engine.py, models.py, risk_defaults.py
│   ├── lab/                     # service.py, verdict.py, predict.py,
│   │                           #   windows.py, store.py, metrics.py
│   ├── ml/                      # meta_labeler, drift_detection,
│   │                           #   promotion_gate, validation — UNUSED by
│   │                           #   the live pipeline, see Section 9
│   ├── marketdata/              # yahoo.py, replay.py, gateway.py, base.py,
│   │                           #   registry.py
│   ├── instruments/metadata.py
│   └── options/                 # contracts.py, pricing.py (Black-76)
├── broker_hub.py                # FastAPI terminal/lab/panel, Mac, :8600
├── mt5_api_server.py            # FastAPI MT5 bridge, runs on the VM, :8500
├── scripts/
│   ├── vm_port_forward.py       # TCP relay Mac:8500 -> VM:8500
│   ├── run_system.py, replay_events.py, validate_setup.py
│   └── paper_trade.py           # STUBBED, see Section 18
├── tests/                       # pytest suite, see Section 6/19
├── config/default.yaml          # the ONLY config file present (see Sec. 19)
├── data/events/events.jsonl     # production event log — 9 lines, all
│                                #   CircuitBreakerTriggered (see Section 17)
├── .replay_cache/                # cached Yahoo NQ candles per timeframe
├── study/                       # ad-hoc backtest research sandbox
│                                #   (~40 scripts + JSON results), predates
│                                #   ftmo_mt5/lab (see Section 15 hypothesis)
├── _archive/                    # archived UIs + the old `nehanth` package
│   ├── README.md                # explains the 2026-08-14 archival — READ
│   │                            #   THIS, it is primary-source evidence
│   └── src-nehanth/nehanth/     # old package, CERTIFICATION.md inside
├── trading_dashboard_live.html  # the live terminal UI (+ .bak, see Sec 15)
├── beat_the_hold.html           # standalone game, unrelated to trading
├── backtest_lab.html            # the /lab UI
├── start.sh (+.bak), start_trading.bat, start_trading.ps1
├── test_order.py                # standalone MT5 debug script, NOT in tests/
├── sweep_equities.py             # uses ftmo_mt5.lab to sweep 14 semis
├── all-backtests-matrix.csv, semis-2156-runs.csv, semis-sized-2156-runs.csv
├── CLAUDE.md, README.md, RUNBOOK.md  # docs — several claims are STALE,
│                                     #   flagged throughout this document
├── pyproject.toml, requirements.txt
└── .env / .env.example
```

Not part of this project (ignore): `Yash_Nihalani_Resume_ATS.pdf.html`,
`si.html` — these do not exist under `FTMO_MT5/`; a prior briefing's mention
of `si.html` here appears to be a mix-up with the sibling
`master-site-panel` repository, which is a wholly separate project. No
`si.html` file exists anywhere under this repository (verified by `find`).

## 6. Architecture

Two independent runtime systems live in this one repository:

**A. The automated trading system** (`ftmo_mt5/main.py`), single process:

```
market_data_loop (main.py)
  → EMACrossoverStrategy.evaluate()      (ftmo_mt5/strategy/ema_crossover.py)
  → Coordinator.process_tick()           (agents/coordinator.py)
      1. SignalAgent.process()           (agents/signal_agent.py)
      2. RiskAgent.process()             (agents/risk_agent.py, uses
                                           engine/rule_engine.py)
      3. ExecutionAgent.process()        (agents/execution_agent.py, uses
                                           execution/mt5_executor.py)
  → EventStore.append()                  (store/event_store.py, JSONL)
Supervisor                                (supervisor/watchdog.py) — separate
  heartbeat-based watchdog over the three agents; kills the whole system
  (kill switch, not restart) the instant the risk agent misses a heartbeat.
HealthMonitor + aiohttp server on :8080   (GET /health, /ready, /metrics)
```

Sequential, single-threaded processing on the decision path is a hard
architectural invariant (see Section 20) — `Coordinator.process_tick()`
never uses `asyncio.gather` for signal→risk→execution.

**B. The manual/multi-broker terminal + research lab** — a completely
separate FastAPI app, `broker_hub.py`, run on the Mac, port 8600 (env
`HUB_PORT`). It does **not** import or drive the automated `Coordinator`
pipeline at all; it talks directly to broker gateways
(`RemoteMT5Gateway`, `TastytradeGateway`, `WebullGateway`) for manual
order placement, and separately to `ftmo_mt5.backtest`/`ftmo_mt5.lab`/
`ftmo_mt5.signals` for research. It serves three static HTML pages:
`trading_dashboard_live.html` (terminal), `beat_the_hold.html` (a
standalone game — the code and `_archive/README.md` both note it has
nothing to do with live broker state), `backtest_lab.html` (research lab).

**C. The MT5 bridge**, `mt5_api_server.py`, intended to run on a Windows
VM alongside the real MT5 terminal. It exposes REST + a `/ws/prices`
WebSocket over account/positions/orders/history/OHLCV. When the
`MetaTrader5` package is not importable (`HAS_MT5 = False`, e.g. on
macOS/Linux) it falls back to an internal mock data layer
(`_mock_base_prices`, `_mock_place_order`, etc.) so it can run in a fully
simulated mode even off the VM. `scripts/vm_port_forward.py` is a plain
TCP relay so the Mac side can always reach `localhost:8500` regardless of
the VM's actual IP.

Everything above the broker layer is broker-agnostic by design: all three
gateways (MT5/remote, Tastytrade, Webull) implement the
`BrokerGateway` protocol in `ftmo_mt5/execution/broker_base.py`.

## 7. Data Flow

Automated pipeline, one iteration of `market_data_loop`
(`ftmo_mt5/main.py:283`):

1. Poll account state + open positions + OHLCV bars per symbol via the
   active `MT5Gateway`/`RemoteMT5Gateway` (blocking calls wrapped in
   `asyncio.to_thread`).
2. `EMACrossoverStrategy.evaluate()` looks for a fresh EMA(12)/EMA(26)
   crossover on the newest **closed** bar, gated by an ATR-relative
   separation-rate filter (`min_atr_fraction`, default 0.10) — returns a
   signal dict or `None`.
3. `Coordinator.process_tick()` runs strictly sequentially:
   `SignalAgent.process()` → emits `SignalGenerated` event →
   `RiskAgent.process()` (calls `FTMORuleEngine.run_all_checks()` +
   `check_overnight_safety()`) → emits `RiskChecked` event → if approved,
   `ExecutionAgent.process()` submits the order via `MT5Executor` → emits
   `OrderSubmitted`/`OrderRejected`.
4. All events append to `EventStore` (`data/events/events.jsonl`),
   immutable, JSON Lines, one file per store directory.
5. `Supervisor._monitor_loop()` runs concurrently, checking each agent's
   heartbeat; a risk-agent heartbeat miss (or an unhandled exception
   surfacing as `CircuitBreakerError`) triggers `_kill_switch()`, which
   stops all agents and appends a `CircuitBreakerTriggered` event.

Manual/lab data flow (`broker_hub.py`): browser → FastAPI route → broker
gateway or `ftmo_mt5.backtest.run_backtest()` / `ftmo_mt5.lab.service`
→ JSON response or WebSocket stream back to the dashboard/lab HTML.

## 8. Core Features

- **FTMO compliance engine** (`ftmo_mt5/engine/rule_engine.py`): Prague
  midnight calculation via `zoneinfo`, MDL/ML limit computation for both
  1-Step and 2-Step challenges, pre-midnight worst-case flatten analysis,
  4-state risk classification (NORMAL/CAUTION/RISK_OFF/HALT) based on
  "budget consumed" percentage (not raw capital percentage — see the
  in-code comment at `rule_engine.py:788-802` explaining why that
  distinction matters).
- **Automated EMA-crossover trading** (`ftmo_mt5/strategy/ema_crossover.py`)
  — the only strategy actually wired into `main.py`'s live loop.
- **A much larger strategy library** (`ftmo_mt5/signals/strategies.py`,
  2290 lines, 14 strategy classes: `EmaRsiPatternStrategy` (hidden from
  the lab UI menu but still resolvable by name),
  `MultiTimeframeGreenStrategy` (current default, `MTF_GREEN_STACK`),
  `EmaRsiPullbackStrategy`, `TrendRegimePullbackStrategy`,
  `DonchianBreakoutStrategy`, `TrendRegimeStrategy`,
  `EmaRegimeLongShortStrategy`, `FourRegimeStrategy`,
  `MeanReversionStrategy`, `GoldMasterTrendStrategy`,
  `DailySwingSma200Strategy`, `VolatilityBreakoutTrendStrategy`,
  `TrendPullbackStackStrategy`, `SimplePullbackStrategy`,
  `RegimeSwitchStrategy`) — this is used **only** by the backtest engine
  and the `/lab`, not by the live automated pipeline (see Section 9).
- **Backtest engine** (`ftmo_mt5/backtest/engine.py`, `run_backtest()`) —
  full trade simulation with execution-model realism (`_slip`, options
  support via `_walk_options`), used by both `broker_hub.py`'s
  `/api/backtest` and the lab.
- **Strategy research lab** (`ftmo_mt5/lab/`) — `service.plan/matrix/
  single/validate`, `verdict.validate` (in-sample/out-of-sample grading
  into MODERATE/FRAGILE/FAILED verdicts, calibrated against hand-graded
  cases — see `tests/test_lab.py`), served through `broker_hub.py`'s
  `/lab`, `/api/lab/*`, `/ws/lab`, `/ws/lab/predict` routes.
- **ML signal-filtering infrastructure** (`ftmo_mt5/ml/`) — triple-barrier
  meta-labeling, purged k-fold CV, CSCV probability-of-backtest-overfitting,
  deflated Sharpe ratio, walk-forward validation, PSI + ADWIN drift
  detection, model promotion gating. Extensively unit-tested
  (`tests/test_ml_pipeline.py`, 36 tests) but **not imported anywhere in
  `main.py` or the agents** — confirmed by grep; this is fully-built,
  fully-tested, orphaned infrastructure (see Section 9, 19).
- **Options pricing** (`ftmo_mt5/options/`) — Black-76 pricing/delta,
  contract expiry rules (0/1/2DTE via `ExpiryRule`), used by
  `broker_hub.py`'s option-chain/option-order routes.
- **Multi-broker manual trading terminal** — MT5, Tastytrade, Webull, one
  UI (`trading_dashboard_live.html`), unified `/api/{broker_key}/*` routes.
- **A standalone game** (`beat_the_hold.html`) served at `/game`,
  explicitly documented in-code (and in `_archive/README.md`) as unrelated
  to live broker state.
- **Event-sourced audit trail** (`ftmo_mt5/store/event_store.py`) —
  append-only, replayable, correlation-ID-tracked.
- **Health/readiness/metrics HTTP server** on :8080 for the automated
  system only (`/health`, `/ready`, `/metrics`).

## 9. Business Logic

The single business-critical invariant is: **never let account equity
breach the FTMO Maximum Daily Loss or Maximum Loss limit.** This is
implemented in `FTMORuleEngine`:

- `compute_mdl_limit()`: `mdl_limit = balance_at_midnight - (mdl_pct/100 *
  initial_capital)`, same formula for both challenge types in the current
  code (the old `CERTIFICATION.md` in `_archive/` describes a different,
  possibly earlier, 3%/5% split that is **not** what the current code
  does — treat that document as historical, not authoritative).
- `compute_ml_limit()`: 2-Step is a **static** floor
  (`initial_capital - ml_amount`); 1-Step is a **trailing** floor off the
  highest end-of-day balance seen (`max(initial_capital,
  highest_eod_balance) - ml_amount`).
- Breach check is `equity <= limit` for both — instant, not averaged.
- Risk-state graduation (`run_all_checks()`) is driven by **budget
  consumed**, not raw capital used: `usage = (reference - equity) /
  (reference - limit) * 100`. This was deliberately changed from a naive
  capital-percentage calculation — the in-code comment (`rule_engine.py`
  lines ~788-802) explains that dividing by capital would pin a fresh,
  untouched account at ~90% usage and put the system into RISK_OFF before
  it ever traded. This is worth understanding before touching that logic.
- `caution_threshold_pct` / `risk_off_threshold_pct` (config, default
  70/85 in `core/models.py`, but 60/80 in `config/default.yaml` — the
  YAML value wins at runtime) gate CAUTION and RISK_OFF.
- `check_overnight_safety()`: worst-case analysis assuming every open
  position's floating loss is realized; flattens if the resulting headroom
  drops below a 2%-of-capital buffer or within 2% of the MDL limit.
- Position sizing by risk state: NORMAL = full size (signal confidence as
  multiplier), CAUTION = 50% reduction, RISK_OFF = closes only, HALT =
  zero / force-close everything.
- **Risk agent crash is a fail-safe, not a bug path**: any unhandled
  exception inside `RiskAgent.process()` is caught and re-raised as
  `CircuitBreakerError`, which `Coordinator.process_tick()` treats as a
  hard signal to shut the whole system down (`agents/coordinator.py`
  lines 280-289). This is intentional and must not be "fixed" into a
  retry/ignore path.
- **The ML meta-labeling/drift-detection infrastructure plays no role in
  this business logic today** — `SignalAgent` has a `meta_label_threshold`
  field and will reject a signal if `tick_data["meta_label_prob"]` is
  present and below threshold, but `EMACrossoverStrategy.evaluate()` sets
  `meta_label_prob` equal to its own confidence score, so the check is
  effectively a no-op self-comparison, not a real ML gate.

## 10. Data Models

All in `ftmo_mt5/core/models.py`, Pydantic v2, `model_config =
ConfigDict(frozen=True)` throughout, `Decimal` for every monetary/price
field with `field_validator(..., mode="before")` coercion from
str/float/int.

**Enums**: `OrderAction`, `OrderType`, `FillPolicy`, `RiskState`
(`NORMAL/CAUTION/RISK_OFF/HALT`), `SignalDirection` (`LONG/SHORT/FLAT`),
`ChallengeType` (`ONE_STEP/TWO_STEP`), `ExecutionStatus`.

**Value objects**: `TradingSignal`, `TradeRequest`, `AccountState`,
`FTMOLimits`, `PositionInfo`.

**Events** (all subclass `BaseEvent`, which carries `event_id`,
`timestamp`, `correlation_id`): `SignalGenerated`, `RiskChecked`,
`OrderSubmitted`, `OrderFilled`, `OrderRejected`, `PositionClosed`,
`RiskStateChanged`, `CircuitBreakerTriggered`. These are exactly the
`EventStore._EVENT_CLASSES` keys used for JSON deserialization
(`ftmo_mt5/store/event_store.py:130`) — adding a new event type requires
registering it there or replay/load will raise `ValueError("Unknown event
type")`.

**Configuration models**: `FTMOConfig` (capital, challenge type, mdl/ml
percentages, caution/risk-off thresholds, pre-midnight-flatten minutes,
magic number), `SystemConfig` (MT5 login/server/**password as a plain
`str`**, symbols, tick interval, heartbeat interval, max restarts,
otel_endpoint, log_level — note: **not** `SecretStr`, see Section 21).

`ftmo_mt5/backtest/models.py` has its own separate model set:
`ExecutionModel`, `TradeMode`, `OptionAction` enums, `BacktestConfig`,
`Trade`, `BacktestResult` dataclasses — these are backtest-domain models,
distinct from the live-trading domain models above.

## 11. APIs & Integrations

**Automated system HTTP server** (`ftmo_mt5/main.py`, aiohttp, :8080):
`GET /health`, `GET /ready`, `GET /metrics`.

**`broker_hub.py`** (FastAPI, :8600/`HUB_PORT`) — full route list
verified by reading the source (sample checked: `/api/lab/*`,
`/api/{broker_key}/order`, `/api/backtest`):

- Pages: `GET /` (terminal), `GET /game` (beat_the_hold), `GET /lab`
  (backtest_lab)
- Lab (research, read/compute-only, no broker orders):
  `GET /api/lab/config`, `POST /api/lab/plan`, `POST /api/lab/run`,
  `POST /api/lab/validate`, `WS /ws/lab`, `WS /ws/lab/predict`,
  `GET /api/lab/runs`, `GET /api/lab/runs.csv`,
  `GET /api/lab/runs/{run_id}`, `DELETE /api/lab/runs/{run_id}`
- Backtest config (read/compute-only): `GET /api/backtest/defaults`,
  `PUT /api/backtest/defaults`, `POST /api/backtest/defaults/reset`,
  `GET /api/backtest/config`, `POST /api/backtest`, `POST /api/signals`
- Game (read-only): `GET /api/game/candles`
- Market data (read-only): `GET /api/health`, `GET /api/ohlcv/{symbol}`,
  `GET /api/history`, `GET /api/instruments`, `GET /api/search`,
  `GET /api/{broker_key}/ohlcv/{symbol}`, `GET /api/{broker_key}/history`,
  `GET /api/{broker_key}/quotes`, `GET /api/{broker_key}/options/{underlying}`
- Account/portfolio (read-only): `GET /api/brokers`,
  `GET /api/{broker_key}/account`, `GET /api/{broker_key}/positions`,
  `GET /api/portfolio`, `WS /ws/prices`
- **Live-execution (highest-risk surface)**: `POST /api/{broker_key}/connect`,
  `POST /api/{broker_key}/order`, `POST /api/{broker_key}/close/{ticket}`,
  `POST /api/{broker_key}/option-order`,
  `POST /api/{broker_key}/option-quotes` (quotes only, not an order),
  `POST /api/{broker_key}/option-order` (order)

**`mt5_api_server.py`** (FastAPI, :8500, meant for the Windows VM):
`GET /`, `GET /api/health`, `GET /api/account`, `GET /api/positions`,
`POST /api/order` (live), `POST /api/close` (live), `GET /api/history`,
`GET /api/instruments`, `GET /api/symbols`, `GET /api/debug/{symbol}`,
`GET /api/ohlcv/{symbol}`, `WS /ws/prices`. Falls back to an internal
mock layer when `MetaTrader5` is not importable (`HAS_MT5 = False`).

**External integrations**: MetaTrader 5 (via the `MetaTrader5` Python
package, Windows-only), Tastytrade (`ftmo_mt5/execution/
tastytrade_gateway.py`, OAuth2 + async SDK per `.env`'s `TT_*` vars),
Webull (`ftmo_mt5/execution/webull_gateway.py`, `WEBULL_*` vars), Yahoo
Finance (`ftmo_mt5/marketdata/yahoo.py`, historical candles, no API key),
OpenTelemetry OTLP gRPC exporter (optional, `OTEL_ENDPOINT`).
**Not wired up despite being documented**: PagerDuty, Slack webhooks,
Twilio SMS, email alerting — see Section 19.

## 12. Authentication & Security

There is no end-user authentication in this codebase — `broker_hub.py`
and `mt5_api_server.py` expose their APIs with no auth middleware
(`CORSMiddleware` is configured but that is not authentication). Security
here means credential handling and risk-limit enforcement, not user auth.
See Section 21.

## 13. Important Files & Modules

| Path | Role |
|---|---|
| `ftmo_mt5/main.py` | Entry point; `load_config()`, `initialize_components()`, `market_data_loop()`, `run_event_loop()`, `start_health_check_server()`, `main()` |
| `ftmo_mt5/strategy/ema_crossover.py` | The one strategy the live system trades with |
| `ftmo_mt5/engine/rule_engine.py` | `FTMORuleEngine` — all FTMO compliance math |
| `ftmo_mt5/agents/coordinator.py` | `Coordinator.process_tick()` — the sequential pipeline |
| `ftmo_mt5/agents/risk_agent.py` | `RiskAgent` — 4-state machine, fail-safe crash → HALT |
| `ftmo_mt5/agents/execution_agent.py` | `ExecutionAgent` — applies risk-adjusted volume, submits orders |
| `ftmo_mt5/execution/mt5_executor.py` | Retry/backoff classification against MT5 return codes |
| `ftmo_mt5/execution/broker_base.py` | `BrokerGateway` Protocol every broker gateway satisfies |
| `ftmo_mt5/execution/remote_gateway.py` | `RemoteMT5Gateway` — HTTP client to `mt5_api_server.py` |
| `ftmo_mt5/store/event_store.py` | `EventStore` — append-only JSONL, `append/replay/snapshot` |
| `ftmo_mt5/observability/health.py` | `HealthCheckRegistry`/`HealthMonitor` — several checks are **placeholders** (`_event_store_check`, `_risk_agent_check`, `_reconciliation_check` all return hardcoded `healthy=True`, see Section 19) |
| `ftmo_mt5/supervisor/watchdog.py` | `Supervisor` — kill switch on risk-agent heartbeat miss; contains its **own separate `BaseAgent` class**, different from `agents/base_agent.py` (see Section 19) |
| `ftmo_mt5/signals/strategies.py` | 2290-line strategy library, lab/backtest-only |
| `ftmo_mt5/lab/service.py`, `verdict.py` | Research-lab plan/run/validate + hand-calibrated grading rubric |
| `broker_hub.py` | Manual multi-broker terminal + lab API, FastAPI, :8600 |
| `mt5_api_server.py` | MT5 bridge for the Windows VM, FastAPI, :8500, has mock-data fallback |
| `scripts/vm_port_forward.py` | TCP relay Mac↔VM |
| `scripts/paper_trade.py` | **Stubbed** — see Section 18 |
| `_archive/README.md` | Primary-source explanation of the 2026-08-14 package cutover — read this before trusting any doc that mentions `src/nehanth` |
| `config/default.yaml` | The only config file that exists (see Section 19) |
| `data/events/events.jsonl` | Production event log; 9 lines, all `CircuitBreakerTriggered` (see Section 17) |

## 14. Important Technical Decisions

1. **Coordinator/specialist pattern, strictly sequential** — no
   `asyncio.gather` on the signal→risk→execution path
   (`agents/coordinator.py`).
2. **Decimal everywhere for money**, `getcontext().prec = 10` set once in
   `main.py:run()`.
3. **Frozen Pydantic models** for all events/value objects — immutability
   as an audit-trail guarantee.
4. **Prague time via `zoneinfo.ZoneInfo("Europe/Prague")`**, never a
   hardcoded UTC offset (`core/constants.py:FTMO_PRAGUE_TZ`).
5. **`threading.Lock()`-serialized MT5 calls** at the gateway level
   (documented in CLAUDE.md/README.md; verify against
   `ftmo_mt5/execution/mt5_gateway.py` directly — not read in this pass
   since it is Windows-only and unused on this Mac checkout; the pattern
   is confirmed present via `_archive/README.md`'s reference to it as an
   established invariant).
6. **Risk-agent crash = immediate CircuitBreakerError = system HALT**,
   never a retry.
7. **Event sourcing, append-only, immutable** — `EventStore.append()`
   never edits or deletes.
8. **Broker-agnostic gateway Protocol** (`execution/broker_base.py`) —
   added later than the original MT5-only design, to support Tastytrade
   and Webull under one interface (visible in `main.py`'s `BROKER` env-var
   branch, `initialize_components()` lines 182-201).
9. **The stale `ftmo-mt5 = "nehanth.main:main"` entry point in
   `pyproject.toml` was never updated** when the package was renamed/moved
   to `ftmo_mt5/` — see Section 19, a concrete, currently-broken piece of
   packaging.
10. **A broken heartbeat model was patched with a ticker, not a redesign**
    — `main.py`'s `market_data_loop()` has an explicit in-code comment
    (lines 327-335) explaining that beating only at I/O boundaries lost
    the race against the supervisor's 5s risk-agent timeout, so a
    1-second ticker task was added that keeps beating unless the loop
    itself has stalled for >180s. This is a real, documented fix for a
    real production incident (see Section 17's event-store evidence).

## 15. Git History & Evolution

**Git is UNAVAILABLE.** `git rev-parse --is-inside-work-tree` fails and no
`.git` directory exists anywhere in the tree. The "history" below is
reconstructed entirely from file-system evidence: the `_archive/` folder
and its `README.md` (the single best primary source in the repo for
history), `.bak` files diffed against their live counterparts, file
modification timestamps (`ls -la` / `stat`), and in-code comments that
narrate past incidents.

Reconstructed timeline (`ls -la` mtimes, oldest-relevant to newest):

- **2026-04-10 to 2026-04-11**: README.md, RUNBOOK.md, CLAUDE.md,
  pyproject.toml authored/finalized (per file mtimes and
  `CERTIFICATION.md`'s "Generated: 2026-04-10 (Pass 20: Production
  Certification)" header). At this point the project's source of truth
  was `src/nehanth/` (see `_archive/src-nehanth/nehanth/CERTIFICATION.md`,
  which certifies 154+ tests, hash-chain event-store integrity,
  `SecretStr` credentials, and a 1% risk buffer — **none of which are
  present or accurate for the current `ftmo_mt5/` package**; this document
  should be read as a historical snapshot of the old package, not as
  current fact).
- **2026-08-12 to 2026-08-13**: `data/events/events.jsonl` records 9
  `CircuitBreakerTriggered` events, all with reason `"Risk agent
  'risk_agent' missed heartbeat"`, spanning these two days. This is
  direct evidence the automated system was run during this window and
  repeatedly self-killed via the supervisor's kill switch rather than
  completing any trading activity (see Section 17).
- **2026-08-13**: per `_archive/README.md`, two critical bugs were fixed
  in the (then still primary) `src/nehanth` package: an
  `start_as_current_span` span crash, and "the risk engine's permanent
  RISK_OFF" bug. The heartbeat-miss events above land squarely in this
  window and are consistent with a system that could not stay alive.
- **2026-08-14**: `_archive/README.md` states the old `src/nehanth`
  package was archived to `_archive/src-nehanth/nehanth/` on this date,
  explicitly because — despite the fixes above — it "had neither
  `market_data_loop` nor any strategy, so it could not trade at all," and
  because the test suite had been importing from it, making it look
  authoritative while being dead code. Tests were repointed to import
  `ftmo_mt5` instead. Two older UI files (`trading_dashboard.html`,
  `broker_panel.html`) were archived the same day, consolidating to the
  single `trading_dashboard_live.html` served by `broker_hub.py`.
- **2026-08-18**: `start.sh` modified (`start.sh.bak` diff shows the live
  version added a wait-for-hub-ready loop with a curl health poll before
  opening the browser, plus clearer ready/not-ready messaging — a
  reliability fix for a race where the browser opened before the FastAPI
  hub had finished probing its brokers).
  `start_trading.bat` (Windows bridge launcher) still titles itself
  "Nehanth Trading Terminal — MT5 Bridge," a naming remnant from the old
  package (Section 19).
- **2026-08-21 to 2026-08-23**: `.replay_cache/*.json` populated (NQ
  candles across seven timeframes, cached for the replay market-data
  provider); `trading_dashboard_live.html` and `beat_the_hold.html`
  modified (each has a `.bak` — diffs not deeply analyzed for the HTML
  files given their size, but both grew substantially: dashboard
  5297→5610 lines, game 964→1679 lines).
- **2026-08-26**: `ftmo_mt5/signals/strategies.py` grew from 379 lines
  (`.bak`) to 2290 lines (live) — the `.bak` diff shows the original file
  had only `EmaRsiPatternStrategy` as the sole, default, visible strategy.
  The live version adds 13 more strategy classes, introduces a `HIDDEN`
  set that removes `EMA_RSI_PATTERN` from the lab UI's menu (while
  keeping it resolvable by name for existing runs/tests), and changes the
  default strategy from `EMA_RSI_PATTERN` to `MTF_GREEN_STACK`
  (`MultiTimeframeGreenStrategy`, a multi-timeframe "all green bars agree"
  strategy). `tests/test_mtf_strategy.py` and `tests/test_backtest.py`
  were added/updated the same day.
- **2026-08-30**: `tests/test_lab.py` added — the newest test file,
  calibrating the lab's verdict rubric (`MODERATE`/`FRAGILE`/`FAILED`)
  against specific hand-graded historical runs.
- **2026-08-31 to 2026-09-01**: `sweep_equities.py`, `all-backtests-
  matrix.csv`, `semis-2156-runs.csv`, `semis-sized-2156-runs.csv`,
  `broker_hub.py`, and `backtest_lab.html` all carry the most recent
  mtimes in the repo — consistent with active, recent work concentrated
  on the lab/backtest research surface.

**Hypothesis** (stated as such, not fact): `ftmo_mt5/lab/` plus
`broker_hub.py`'s `/api/lab/*` routes appear to be an attempt to replace
the ad-hoc `study/lab/*.py` script sprawl (~40 scripts:
`frozen1.py`...`frozen6.py`, `vet_batch3.py`...`vet_run4.py`,
`walkopt.py`, `nq_hunt.py`, `megha_exact.py`, each with a matching JSON
results file, plus `leaderboard.csv`/`leaderboard.json`) with a proper,
structured, repeatable backtest lab. Supporting evidence: (a) `lab/` is
the newest module by test-file mtime (2026-08-30, newest of all test
files); (b) `sweep_equities.py` (root-level, one of the newest files,
2026-08-31) already uses the **new** `ftmo_mt5.lab` module (`metrics`,
`service`, `windows`) rather than anything under `study/lab/`; (c) both
share the concept of a graded/ranked "leaderboard" of strategy
configurations, and both grade calibration against hand-picked reference
runs. This is circumstantial, not confirmed by any commit message or doc
— no direct statement in the repo says `study/` is deprecated.

## 16. Completed Work

- Full FTMO MDL/ML compliance engine with Prague-timezone handling, DST
  awareness, and pre-midnight flatten logic, unit-tested
  (`tests/test_rule_engine.py`, 30 tests).
- End-to-end automated pipeline (signal → risk → execution → event store)
  with a working, if minimal, live strategy (`EMACrossoverStrategy`).
- Multi-broker manual trading terminal covering MT5, Tastytrade, and
  Webull under one gateway interface and one UI.
- A large, well-tested strategy library and backtest engine
  (`tests/test_backtest.py`, 107 tests — the largest test file in the
  suite by far).
- A structured research lab with a calibrated grading rubric
  (`tests/test_lab.py`) and multi-timeframe strategy support
  (`tests/test_mtf_strategy.py`, 29 tests).
- A deep, well-tested ML research toolkit (drift detection,
  meta-labeling, promotion gating, walk-forward validation) —
  functionally complete but not connected to production trading (Section
  9, 19).
- Options pricing (Black-76) and expiry-rule support for options trading
  via the terminal.
- Diagnosed and fixed a real reliability incident: the heartbeat-vs-I/O
  race documented in `main.py`'s `market_data_loop()` comment
  (Section 14, item 10), which is consistent with the
  `CircuitBreakerTriggered` events found in the production event log.
- Consolidated three overlapping HTML UIs down to one, and retired a
  broken/duplicate package (`src/nehanth`) in favor of `ftmo_mt5/`.

## 17. Current Work

The clearest signal of what was being worked on most recently is file
mtimes clustering on 2026-08-30 to 2026-09-01 around `broker_hub.py`,
`backtest_lab.html`, `sweep_equities.py`, and the large CSV result files
(`all-backtests-matrix.csv`, `semis-2156-runs.csv`,
`semis-sized-2156-runs.csv`) — i.e., active work on the strategy-research
lab and large-scale backtest sweeps (a 14-symbol semiconductor-stock sweep
across every strategy and timeframe), not on the automated live-trading
path.

Separately, and importantly for anyone resuming this project: the entire
recorded production event history (`data/events/events.jsonl`, 9 lines
total) consists **exclusively** of `CircuitBreakerTriggered` events with
reason `"Risk agent 'risk_agent' missed heartbeat"`, dated 2026-08-12 and
2026-08-13. There is no `SignalGenerated`, `RiskChecked`,
`OrderSubmitted`, `OrderFilled`, or `PositionClosed` event anywhere in
this log. Taken at face value, this event store shows the automated
trading system has **never recorded a successful signal→risk→execution
cycle** — only repeated self-triggered kill switches. This predates the
heartbeat-ticker fix described in Section 14/16, so it is plausible (not
confirmed) that the fix resolved this specific failure mode, but there is
no event-log evidence in this repository that the automated system has
run successfully (or at all) since 2026-08-13.

## 18. TODOs & Planned Work

- **`scripts/paper_trade.py:220`** contains a literal
  `# TODO: Implement paper trading simulation`. The entire simulation
  body is a stub: it loads config via `ftmo_mt5.main.load_config()` and
  then immediately returns a hardcoded zeroed-out results dict
  (`total_pnl: 0.0`, `trades_total: 0`, etc. — see lines 240-256) without
  ever touching market data, the signal agent, or the risk agent. Both
  `README.md` ("Advanced Topics" → Model Promotion Workflow, and its own
  `paper_trade.py --model ... --duration 7d` example) and `CLAUDE.md`
  (`paper_trade.py --config config/1step.yaml --duration 24h`) document
  this as if it were a working feature. **This is a confirmed
  documentation/reality mismatch** — paper trading does not exist yet,
  only its CLI surface and HTML/CSV report generators do.
- `config/1step.yaml` and `config/2step.yaml`, referenced throughout
  README.md and CLAUDE.md as configuration examples, **do not exist**.
  Only `config/default.yaml` is present (a 2-step, $100k config). This is
  a second confirmed doc/reality mismatch (Section 19).
- The alerting integrations documented in RUNBOOK.md Section 9
  (PagerDuty, Slack, Twilio SMS) and in `.env.example` ("future feature")
  are entirely undocumented-as-unbuilt in the runbook's own prose in
  places, but the runbook otherwise presents them as configurable —
  confirmed unimplemented, see Section 19.
- The ML pipeline (`ftmo_mt5/ml/`) appears built toward eventual
  production use (promotion gates, drift monitors) but is not yet wired
  into `SignalAgent`/`RiskAgent`/`main.py` — likely planned future
  integration, not confirmed by any explicit TODO.

## 19. Known Issues & Technical Debt

- **Broken packaging entry point.** `pyproject.toml` declares
  `[project.scripts] ftmo-mt5 = "nehanth.main:main"` and
  `name = "nehanth-ftmo-mt5"`, but no `nehanth` package exists anywhere
  outside `_archive/src-nehanth/nehanth/` (confirmed via `find`), and the
  package is not installed in either venv (`pip show
  nehanth-ftmo-mt5` → not found). There is also no `[tool.setuptools]`
  packages directive to redirect discovery to `ftmo_mt5`. Running the
  installed `ftmo-mt5` console script would fail with
  `ModuleNotFoundError: No module named 'nehanth'`. The only currently
  working ways to run the system are `python -m ftmo_mt5.main` or
  `python ftmo_mt5/main.py` directly. **Whether `pip install -e .`
  itself succeeds today is UNKNOWN** (not executed in this pass) — with
  multiple top-level directories carrying `__init__.py`
  (`ftmo_mt5/`, `tests/`, `scripts/`), setuptools' automatic package
  discovery may also behave unexpectedly.
- **CLAUDE.md and README.md both describe a stale project layout.**
  CLAUDE.md line 14 says `src/nehanth/` is an "Alternative source
  structure (legacy/development)" — but no `src/` directory exists at all
  in the live tree; it only exists archived at
  `_archive/src-nehanth/nehanth/`. Both docs' "Running" sections
  reference `pytest tests/ -v --cov=src/nehanth`, which would silently
  report 0% coverage against a path that doesn't exist relative to the
  repo root the way the docs imply.
- **`config/1step.yaml` and `config/2step.yaml` do not exist** — only
  `config/default.yaml`. Any command copy-pasted from README.md/CLAUDE.md
  referencing those paths will fail with
  `FileNotFoundError: Configuration file not found`.
- **`.env.example` is stale relative to the actual `.env`.** The real
  `.env` in this repo defines `BROKER`, `HUB_PORT`, `MT5_VM_URL`, and the
  full `TT_*` (Tastytrade) and `WEBULL_*` variable sets — none of which
  appear in `.env.example`. Anyone bootstrapping from the example file
  alone would be missing the variables `broker_hub.py` actually needs for
  multi-broker operation.
- **Documented-but-unwired alerting.** Grepping the entire codebase
  (`.py` files, excluding venvs) for `TWILIO`, `SLACK_WEBHOOK`,
  `ALERT_EMAIL`, `MOCK_MT5`, `PAPER_TRADE`, `REPLAY_MODE`,
  `REPLAY_FROM_TIMESTAMP` returns **zero matches** anywhere in application
  code. These exist only as commented-out, explicitly-labeled
  "(future feature)" / "Do NOT set these in production" entries in
  `.env.example`, and as example integration snippets in RUNBOOK.md
  Section 9. `DATABASE_URL` is similarly unused; the only database-URL
  style variable actually read in code is the unrelated
  `LAB_DATABASE_URL` in `ftmo_mt5/lab/store.py`.
- **`HealthCheckRegistry`'s built-in checks are largely placeholders.**
  `_event_store_check()`, `_risk_agent_check()`, and
  `_reconciliation_check()` in `ftmo_mt5/observability/health.py` all
  contain a `# Placeholder: Would check actual X ...` comment and simply
  return `healthy=True` unconditionally. Only `_mt5_connection_check()`
  (probes the VM bridge or MT5 directly) and `_disk_space_check()` do
  real work. The `/ready` and `/health` endpoints are therefore less
  meaningful than RUNBOOK.md's troubleshooting guide implies.
- **Two incompatible `BaseAgent` classes.** `ftmo_mt5/agents/base_agent.py`
  defines a `BaseAgent` with a private `_last_heartbeat` (monotonic
  clock) and `is_healthy(timeout_seconds)`/`is_alive()` methods — this is
  what `SignalAgent`/`RiskAgent`/`ExecutionAgent` actually inherit from.
  `ftmo_mt5/supervisor/watchdog.py` independently defines its **own**
  `BaseAgent` class with a public `last_heartbeat: Optional[datetime]`
  attribute and an `async heartbeat()` method. `Supervisor.get_status()`
  (`watchdog.py` line ~521) reads `agent.last_heartbeat.isoformat()`
  directly — but the real agents registered via
  `main.py:initialize_components()` never set a public `last_heartbeat`
  attribute (only the private, differently-typed `_last_heartbeat`).
  Calling `Supervisor.get_status()` against the live agents as wired in
  `main.py` would raise `AttributeError: 'SignalAgent' object has no
  attribute 'last_heartbeat'` (or return `None`, then fail on
  `.isoformat()`). This appears to be real, unexercised technical debt —
  **not verified by running the code**, but confirmed by direct reading
  of both class definitions.
- **`Coordinator._create_trade_request()` hardcodes `volume=Decimal("1")`
  and `magic=234000`**, ignoring `FTMOConfig.magic` from the loaded
  config (`agents/coordinator.py` lines 319-332). It also never sets
  `sl`/`tp` on the resulting `TradeRequest` (both `None`) — stop-loss and
  take-profit are not attached to automated orders at the point of
  creation.
- **Old `CERTIFICATION.md` overstates the current system's guarantees.**
  It claims `SecretStr` for MT5 credentials and a "hash chain" for event
  store integrity — the current `SystemConfig.mt5_password` is a plain
  `str` (Section 21), and the current `EventStore` has no hash-chaining,
  just sequential JSONL append. This document describes the archived
  `nehanth` package, not `ftmo_mt5`, and should not be cited as current
  fact.
- **Test coverage gaps** (confirmed by grepping `tests/*.py` for module
  references): **no tests exist** for `broker_hub.py`,
  `mt5_api_server.py`, `ftmo_mt5/execution/tastytrade_gateway.py`,
  `ftmo_mt5/execution/webull_gateway.py`, or
  `ftmo_mt5/supervisor/watchdog.py`. `ftmo_mt5/observability/*` and
  `ftmo_mt5/marketdata/*` are only indirectly exercised (imported by
  `test_backtest.py`, `test_options.py`, `test_mtf_strategy.py`,
  `test_replay.py`), not directly unit-tested as their own modules. Given
  that `watchdog.py` contains the kill-switch logic and the duplicate
  `BaseAgent` bug above, this is a meaningful gap.
- **Old dependency pins.** `requirements.txt` is pinned to roughly
  early-2024 versions (`pydantic==2.6.1`, `opentelemetry-api==1.22.0`,
  `numpy==1.26.3`, etc.) — a compatibility check is warranted before any
  significant new dependency work.
- **`start_trading.bat` still says "Nehanth Trading Terminal"** in its
  window title — a cosmetic naming remnant of the old package.

## 20. AI Development Guidelines

Do not casually break these invariants — they are load-bearing for a
system that trades real (or challenge) capital:

1. **Never use `float` for money, prices, or volumes.** Always `Decimal`.
   `Decimal` precision is globally set to 10 in `main.py:run()` via
   `getcontext().prec = 10`; do not silently rely on a different default.
2. **Never hardcode a UTC offset for Prague time.** Always
   `zoneinfo.ZoneInfo("Europe/Prague")` (`core/constants.py:
   FTMO_PRAGUE_TZ`). Europe's DST transition dates differ from the US's;
   `FTMORuleEngine.is_dst_mismatch_window()` exists specifically to flag
   the ~3-week annual mismatch window.
3. **Never introduce `asyncio.gather()` (or any other parallelism) on the
   signal → risk → execution decision path.** `Coordinator.process_tick()`
   must stay strictly sequential; this is stated as non-negotiable in
   CLAUDE.md and enforced by the current implementation's structure.
4. **Never make HALT auto-recoverable.** A risk-agent crash or an
   MDL/ML breach must produce a `CircuitBreakerError`/`HALT` that requires
   manual intervention (RUNBOOK.md Section 3A). Do not add retry or
   auto-restart logic around the risk agent specifically — the
   `Supervisor` already treats any risk-agent heartbeat miss as an
   immediate, unconditional kill switch, by design (unlike signal/
   execution agents, which get exponential-backoff restarts).
5. **All MT5 API calls must stay serialized.** The gateway layer uses
   `threading.Lock()` (per CLAUDE.md and confirmed by the pattern's
   presence throughout `execution/mt5_executor.py`'s blocking-call
   wrapping via `asyncio.to_thread`/`run_in_executor`) — do not introduce
   concurrent order submission or cancellation against MT5.
6. **Event store entries are immutable — never edit or delete a written
   event.** `EventStore.append()` is the only write path; there is no
   update/delete API by design. If you need to "fix" a HALT, that is a
   manual, out-of-band operational procedure (RUNBOOK.md), not a code
   change to the store.
7. **Before changing FTMO risk-state percentage logic, re-read the
   `_usage()` helper's comment in `rule_engine.py` (~line 788).** It
   explains a specific, non-obvious correctness requirement: usage must
   be measured against the loss *budget* (`reference - limit`), not
   against total capital, or a fresh account gets miscategorized as
   RISK_OFF from tick one.
8. **If you touch `ftmo_mt5/signals/strategies.py`, know that only
   `MTF_GREEN_STACK` (the default) and whatever `get_strategy()` is
   called with reach the backtest/lab; the live automated pipeline in
   `main.py` uses none of this file** — it uses
   `ftmo_mt5/strategy/ema_crossover.py` exclusively. Changing
   `strategies.py` does not change what the live system trades.
9. **Do not assume the ML modules (`ftmo_mt5/ml/`) are live.** They are
   real, tested, and importable, but nothing in `main.py` or the agents
   imports them. If you wire them in, that is new integration work, not
   a bug fix.
10. **Treat `_archive/` as read-only historical reference, never as a
    second source of truth.** Its own README explains why: the archived
    package "looked authoritative" and someone could easily "fix" dead
    code by mistake. Do not resurrect it without understanding why it was
    retired (Section 15).

## 21. Security Considerations

- **Credentials live in `.env` (git-ignored per `.env.example`'s own
  guidance), loaded via `python-dotenv`** in both `ftmo_mt5/main.py`
  (`load_dotenv(Path(__file__).parent.parent / ".env")`) and
  `broker_hub.py`. Variable **names** referenced in `.env`/`.env.example`
  include `MT5_LOGIN`, `MT5_SERVER`, `MT5_PASSWORD`, `BROKER`,
  `HUB_PORT`, `MT5_VM_URL`, `TT_ACCOUNT`, `TT_SECRET`, `TT_REFRESH`,
  `TT_SANDBOX`, `TT_AUTOCONNECT`, `WEBULL_ACCOUNT_ID`, `WEBULL_APP_KEY`,
  `WEBULL_APP_SECRET`, `WEBULL_ENDPOINT`, `WEBULL_REGION`,
  `WEBULL_AUTOCONNECT` — **actual values were not read or reproduced** in
  this investigation, per instructions.
- **`SystemConfig.mt5_password` is a plain Python `str`**, not Pydantic's
  `SecretStr` — meaning it can appear in plaintext in
  `model_dump()`/`repr()` output and in any log statement that
  accidentally serializes the whole config object. The old, archived
  `CERTIFICATION.md` claims `SecretStr` was used and verified — that is
  **not true of the current `ftmo_mt5` code** (Section 19). This is worth
  flagging to whoever resumes security hardening work.
- **No authentication on either FastAPI service.** `broker_hub.py`
  (:8600) and `mt5_api_server.py` (:8500) have no auth middleware; both
  are presumably intended to run only on trusted local/VM networks, but
  this is not enforced in code (only `CORSMiddleware`, which is not an
  auth boundary).
- **The highest-risk surface in the whole repo is the live-order-execution
  route set**: `POST /api/{broker_key}/order`, `POST
  /api/{broker_key}/close/{ticket}`, `POST /api/{broker_key}/option-order`,
  `POST /api/{broker_key}/connect` in `broker_hub.py`, and `POST
  /api/order` / `POST /api/close` in `mt5_api_server.py`. These place or
  cancel real broker orders with no auth in front of them.
- **The FTMO risk-limit compliance engine is the core business-critical
  invariant** for the automated path (Section 9) — a bug here does not
  just cause a bad trade, it can fail the entire FTMO challenge
  (permanent, not recoverable by restarting).
- **Event store contains no PII beyond trading data** (symbols, prices,
  volumes, correlation IDs) based on the sample read from
  `data/events/events.jsonl` — no account numbers or credentials observed
  in the 9 lines present.

## 22. Unknown / Uncertain Information

- `UNKNOWN`: Whether `pip install -e .` succeeds today given the missing
  `[tool.setuptools]` packages directive and multiple top-level
  `__init__.py`-bearing directories. Not executed in this investigation.
- `UNKNOWN`: Whether `ruff check`, `mypy --strict`, or the full `pytest`
  suite currently pass. Configured in `pyproject.toml` but not run in
  this investigation (per the "do not modify/execute" constraint of this
  task — only read-only investigation was performed; no test run was
  attempted).
- `UNKNOWN`: The exact content/values inside `.env` (deliberately not
  read for values, only variable names, per task constraints) — so
  whether real MT5/Tastytrade/Webull credentials are currently valid,
  live, demo, or placeholder is unknown.
- `UNKNOWN`: Whether the heartbeat-ticker fix described in `main.py`
  (Section 14, item 10) actually resolved the `CircuitBreakerTriggered`
  incidents recorded in `data/events/events.jsonl` — the event log has no
  entries after 2026-08-13, so there is no direct evidence either way;
  it's plausible but unconfirmed that the system has not been run live
  since, or that it has run live since without writing to this
  particular event-store directory (e.g., a different
  `FTMO_EVENT_STORE_DIR` could have been used).
- `UNKNOWN`: Whether `ftmo_mt5/execution/mt5_gateway.py` (the
  Windows-only local MT5 gateway) actually implements the
  `threading.Lock()` serialization claimed in CLAUDE.md/README.md/
  `_archive/README.md` — not read directly in this pass because it
  requires the Windows-only `MetaTrader5` import and this is a Mac
  checkout; only its drop-in replacement `RemoteMT5Gateway` was read in
  full.
- `UNKNOWN`: Full content and behavior of
  `ftmo_mt5/execution/tastytrade_gateway.py` (1157 lines, largest gateway)
  and `ftmo_mt5/execution/webull_gateway.py` (501 lines) — only their
  method counts were confirmed (24 and 14 methods respectively), not
  their full logic, given time constraints on this pass.
- `UNKNOWN`: Whether `study/` (the ad-hoc research sandbox) is formally
  deprecated in favor of `ftmo_mt5/lab/` — this is stated only as a
  hypothesis in Section 15/9, with circumstantial supporting evidence,
  not a confirmed fact.
- `UNKNOWN`: The purpose/status of `si.html` referenced in a prior
  briefing for this task — this file does not exist anywhere under
  `FTMO_MT5/` (confirmed via `find`); it likely belongs to the unrelated
  sibling `master-site-panel` repository.
- `UNKNOWN`: Whether `ftmo_mt5/execution/mt5_gateway.py`'s interface
  matches `RemoteMT5Gateway`'s interface closely enough that `main.py`'s
  platform-conditional import (`if sys.platform == "win32": from
  ftmo_mt5.execution.mt5_gateway import MT5Gateway else: from
  ftmo_mt5.execution.remote_gateway import RemoteMT5Gateway as
  MT5Gateway`) is actually a safe drop-in swap in practice — not verified
  by reading `mt5_gateway.py`.
- `UNKNOWN`: Whether `scripts/run_system.py` and
  `scripts/validate_setup.py` (listed in `scripts/` but not read in this
  pass) are functional, stubbed, or stale.
