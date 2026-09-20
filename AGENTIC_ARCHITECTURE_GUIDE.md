# Agentic Architecture Guide — FTMO_MT5

A teaching document, not an implementation. Nothing in this file has been built — every code path named below is a real, already-existing file in this repo; every proposed addition is marked as such and does not exist yet. Written for Claude Certified Architect–Foundations-level understanding, grounded entirely in this project.

The single idea that ties all six concepts together, stated once so it doesn't need repeating six times: **this codebase already draws a hard line between deterministic code (the trading/research math) and judgment (what to do about it), and every concept below is really just "how do you let an LLM sit on the judgment side of that line without ever crossing into the deterministic side."** `CLAUDE.md`'s own rules — no `asyncio.gather` on the decision path, risk-agent crash is a fail-safe not a bug, event store is append-only — are all instances of the same principle a decade before any of this AI tooling existed. You are not introducing a new philosophy. You are extending one that is already load-bearing in `ftmo_mt5/`.

---

## 1. Agentic Loop & Task Decomposition

**What it is.** An agentic loop is: perceive state → decide the next action → act (usually a tool call) → observe the result → repeat until a stop condition. Task decomposition is breaking one ambiguous goal into an ordered sequence of smaller, checkable subtasks instead of trying to do everything in one shot. Neither of these requires an LLM — they're a control-flow shape, and this project already has one.

**Why FTMO needs it.** Two separate places, and conflating them is the single most common mistake in this whole document:
- The **live trading system already has a deterministic agentic loop.** `ftmo_mt5/main.py`'s `market_data_loop()` perceives (polls account/positions/OHLCV), decides (via `Coordinator.process_tick()`), acts (submits an order via `MT5Executor`), observes (appends to `EventStore`), and repeats every tick. Zero LLM. This loop must never gain one — see Risks.
- The **Lab has no loop at all**, and this entire conversation is proof: every "run X for all timeframes" request I answered by hand-writing a new one-off script (`study/lab/mean_reversion_validate.py`, `regime_strategies_validate.py`, `semis_predict.py`, `semis_predict_all_tf.py`), reading the JSON, deciding what to test next based on what I saw (I picked `EMA_REGIME_LS`/`FOUR_REGIME` specifically because `predict.py`'s output pointed at them), and looping across conversation turns. That decision-making — "given these results, what's the next useful test?" — was me acting as the agentic loop's "decide" step, manually, turn by turn.

**Current architecture.**
- Live: `ftmo_mt5/agents/coordinator.py::Coordinator.process_tick()` — a fixed, three-step, non-branching decomposition (signal → risk → execution), by design never adaptive.
- Research: a library of pure functions (`ftmo_mt5/lab/service.py::plan/matrix/single/validate`) with no loop wrapped around them — a human (or an AI in conversation) supplies the loop by hand.

**What we would add.** A research orchestrator loop, scoped entirely to the Lab, that: (1) decomposes "find robust evidence for symbol set X" into screen → filter → verify → report phases, (2) uses an LLM only for the filter decision (which in-sample rows deserve the expensive OOS `validate()` call), (3) stops on an explicit budget (max `validate()` calls) or coverage condition, never open-ended.

**Implementation steps.**
1. Define a stop condition and budget up front (e.g., "screen all combos, validate the top 3 per symbol plus any outlier, stop after ≤60 validate() calls") — mirrors `SYSTEM_MAX_AGENT_RESTARTS` already capping the live watchdog's retries in `ftmo_mt5/supervisor/watchdog.py`.
2. Wrap the screen phase around `service.matrix()` (already the function behind every sweep this session).
3. Wrap the verify phase around `service.validate()` (already what `mean_reversion_validate.py` and `regime_strategies_validate.py` called 462 times combined).
4. The orchestrator (a script, or an MCP-tool-calling agent per §6) runs screen, hands the LLM the compact summary (not the raw 154 rows — see §4 on structured output), gets back a schema-validated candidate list, runs verify only on those, and repeats or stops per the budget.
5. Never let the loop's LLM step touch the actual pass/fail math — that stays `verdict.py::_grade()`.

**Files/modules involved.** `ftmo_mt5/lab/service.py`, `ftmo_mt5/lab/verdict.py` (both unchanged, reused as-is), a new orchestrator module (does not exist yet) that would live in a research-tooling location like `mcp_server/` or `study/lab/`, not inside `ftmo_mt5/` itself.

**Simple FTMO example.** This conversation *is* the example, un-formalized. "Test MEAN_REVERSION for all timeframes" → I ran the screen, saw 12 FRAGILE hits clustering on 1h → decided (judgment) that ARM's `EMA_REGIME_LS` and NVDA's `FOUR_REGIME` picks from `predict.py` were the two loose threads worth closing → ran those. Formalizing this means writing that same decision policy into a loop so it runs unattended instead of needing a human to prompt each step.

**AI vs deterministic code.**
| Deterministic (never LLM) | AI/LLM |
|---|---|
| `metrics.measure()`'s math | Which combo looks worth a deeper look |
| `verdict.py`'s ROBUST/MODERATE/FRAGILE/FAILED gates | Phrasing the final written conclusion |
| Position sizing, commission, slippage | Deciding when "enough" symbols have been covered |
| The live `Coordinator`'s signal→risk→execution sequence | — (this loop should never involve an LLM at all) |

**Risks.**
- **Runaway loops** — no budget cap means unbounded tool calls, unbounded token spend, and (if network fetches are involved) unbounded Yahoo Finance requests, which is exactly the rate-limit risk `windows.py`'s caching exists to avoid.
- **Judgment creep** — the single biggest risk in this whole document: an LLM "eyeballing" a Sharpe ratio and calling something robust instead of deferring to `verdict.py`'s actual gates. This is the precise mistake the user has repeatedly, explicitly told me not to make across this conversation ("Do not declare a strategy successful only because it has higher historical returns").
- **Proximity to live trading** — if this pattern were ever pointed at `ftmo_mt5.main`'s live loop instead of the Lab, it would directly violate CLAUDE.md's "never `asyncio.gather` on the decision path" and "no auto-recovery from HALT" rules. The orchestrator must be structurally incapable of importing `ftmo_mt5.execution` or `ftmo_mt5.agents` — see §3.

---

## 2. Subagents & Orchestration

**What subagents are.** Separate agent instances, each with their own isolated context window and tool access, spawned by a coordinating agent to do a scoped unit of work and return a result — not shared memory, message-passing. I used exactly this twice already in this conversation: the "Reconstruct FTMO_MT5 project context" agent and the "Lab module research" agent, both spawned as background `Agent` calls with a self-contained brief, each returning a final report I then used.

**Coordinator/worker architecture.** One agent decomposes and delegates to N workers, then aggregates. The trading codebase already has a coordinator/worker shape at the deterministic-code layer — `ftmo_mt5/agents/coordinator.py::Coordinator` orchestrates `SignalAgent`/`RiskAgent`/`ExecutionAgent` as separate worker objects. The AI-subagent version is structurally the same idea one level up, with one critical difference: the trading `Coordinator` is deliberately **sequential-only** (CLAUDE.md forbids parallelizing it), while AI subagents are valuable specifically *because* they can run independently in parallel when the subtasks don't depend on each other.

**Parallel execution.** Useful when subtasks are genuinely independent. This session's actual research work was **not** parallelized at the subagent level — a single Python loop over cached data ran 154 `validate()` calls in 5.4 seconds, faster and cheaper than spinning up parallel agent contexts would have been. The two subagents I *did* spawn were for large, independent, expensive investigative tasks (reading ~50 files to reconstruct project history; running and interpreting hundreds of backtests) — the right instinct is "parallelize expensive, independent judgment work; don't parallelize cheap, sequential computation."

**Context sharing.** Subagents do not inherit the parent conversation. Everything they need must be explicitly written into their spawn prompt. Concretely: both agents I spawned this session got a large "CONFIRMED FACTS FROM MY OWN RECONNAISSANCE" preamble — the module map, the route list, the exact function signatures — precisely so they wouldn't burn their own budget re-deriving what I already knew. Results come back only as a final written report, not as live shared state, which is why the brief has to be self-contained up front, not iteratively clarified.

**Implementation steps** (hypothetical: parallel per-symbol research across, say, 50 new symbols where per-symbol data isn't yet cached).
1. Coordinator decomposes by symbol (or by batch of symbols) into independent units — independence is the precondition, and it holds here only because each symbol's data fetch and backtest is unrelated to every other symbol's.
2. Spawn one subagent per unit with a self-contained brief: exact symbols, exact Lab functions to call (`service.validate`), exact output schema expected back (§4).
3. Subagents execute via the same `ftmo_mt5.lab` imports the existing `study/lab/*.py` scripts already use — no LLM in the math, only in per-unit judgment/summarization.
4. Coordinator collects every subagent's report and performs the cross-comparison judgment (exactly what I did by hand comparing MEAN_REVERSION vs. `EMA_REGIME_LS` vs. `FOUR_REGIME` side by side).
5. Coordinator writes one unified report, citing which subagent produced which finding.

**14-symbol FTMO research example.** Instead of running `mean_reversion_validate.py` → `regime_strategies_validate.py` → `semis_predict.py` sequentially across separate turns (what actually happened), a coordinator could spawn 14 parallel subagents, one per symbol, each running the full strategy × timeframe `validate()` sweep for just that symbol and returning a compact verdict summary. For this project's actual scale (14 symbols, cached data, 5-second sequential sweeps) this would be **strictly worse** — more tokens, more coordination overhead, no speed benefit, since the bottleneck was never compute. It would only become worth it if the symbol count grew into the hundreds or each unit required an independent, slow, uncached network fetch.

**Failure handling.** A subagent can fail, time out, or return malformed output. The coordinator must not let one bad unit sink the batch — this is the exact principle `service.matrix()` already enforces one layer down: a per-cell `try/except` means "a cell that raises is reported as an error cell and the rest of the matrix continues" (a literal docstring quote from `ftmo_mt5/lab/service.py`). Subagent orchestration needs the identical discipline: catch, log, continue, report what's missing explicitly rather than silently dropping it — exactly what the "Bugs or Problems Found" section of `LAB_RESEARCH_REPORT.md` did when three strategies produced zero trades reproducibly rather than being silently omitted.

**When NOT to use them.**
- The work is small, fast, and sequential — proven this session: no Lab sweep so far has justified subagent overhead.
- The subtasks aren't actually independent — you cannot parallelize `Coordinator.process_tick()`'s signal→risk→execution sequence, and the same logic applies to any AI orchestration: if step 2 needs step 1's output, it's a loop (§1), not parallel subagents.
- The task needs iterative, nuanced back-and-forth judgment refined turn by turn (this whole Lab-exploration conversation) rather than "go compute X independently and report back" — a single continuous session with accumulated context served this better than fire-and-forget delegation would have.

---

## 3. Hooks & Security Guardrails

**What hooks are.** Shell commands the harness executes automatically at defined lifecycle points, configured in `.claude/settings.json`, running entirely outside the model's control — the model cannot talk its way past a hook, because the hook is harness-level enforcement, not a request the model chooses to honor. This is the mechanism that turns "the model has been told not to" into "the model structurally cannot."

**Where they run.** Defined lifecycle points: `PreToolUse` (before a tool executes — can veto it outright), `PostToolUse` (after — can inspect, log, react), plus session-level events. `PreToolUse` is the enforcement point that matters most here, because it can block an action before it happens, not just complain afterward.

**Pre/post validation.** Pre-hooks stop dangerous actions before they run; post-hooks record what happened regardless of what the model reports about its own actions — the second is important precisely because it doesn't rely on the model's self-report being accurate.

**Implementation steps.**
1. Enumerate the specific dangerous surfaces in *this* codebase (not a generic list): `.env` reads/writes, `mt5_api_server.py`'s `/api/order` and `/api/close`, `broker_hub.py`'s `/api/{broker}/order`, `/close/{ticket}`, `/connect`, and any invocation of `python -m ftmo_mt5.main` or the `ftmo-mt5` console script (which starts the live trading loop).
2. Write a `PreToolUse` hook for the `Bash` tool matcher that pattern-matches the about-to-run command string against those surfaces (`mt5_api_server`, `ftmo_mt5.main`, `/api/.*order`, `/api/.*close`, `/api/.*connect`, `.env`) and exits non-zero to block, with a message pointing at why.
3. Register it under `hooks.PreToolUse` in `.claude/settings.json`.
4. Add a `PostToolUse` (or session-scoped) audit hook that appends every tool call touching `ftmo_mt5/execution/*`, `.env`, or the order/close/connect routes to a dedicated security log — separate from `EventStore`, which is for trading events, not AI-tool-usage events.

**How to block live trading actions.** Concretely: the hook script greps the pending Bash command for the live-trading entrypoints above; a match returns a non-zero exit and a message like "Live trading actions are blocked by policy — requires explicit human execution outside Claude." This converts the safety-rule prose already governing this conversation (§8 of the earlier MCP-analysis turn: "never expose order/close/connect, no exceptions") from a promise into an enforced fact.

**Protect broker credentials.** A hook blocking `cat .env`, blocking any Write/Edit whose content would copy `.env`'s actual values elsewhere, and blocking grep patterns that would extract a credential's *value* rather than just checking a key's existence. Compare this to what's been done by policy, not enforcement, all session: `.env.example` was read and its values redacted by discipline, `.env` itself was never read for values. A hook makes that a guarantee instead of good behavior that depends on the model remembering.

**Audit logging.** Every tool call touching a money-adjacent path gets appended to a log, independent of what the model says it did — this is a genuinely new concept for this codebase, which has zero mechanism today for "what did an AI agent try to do," because none of `ftmo_mt5/` predates any AI integration. It's the same append-only philosophy as `EventStore` (`ftmo_mt5/store/event_store.py`), applied to a different subject.

**FTMO security example.** A future session is asked to "just try placing a test order to check the connection works." Without a hook, this depends entirely on the model recognizing the request as out of scope and refusing (which it should, per these safety rules — but that's a promise, not a guarantee). With a `PreToolUse` hook matching `/api/.*order`, the call is blocked structurally before it executes, regardless of how the request was phrased or how reasonable it sounded.

**Risks.**
- **False positives** — an overly broad pattern (e.g., blocking anything containing "order") would also block legitimate reading of `mt5_executor.py`'s source, which is fine to read, only unsafe to execute.
- **False sense of security** — a hook that only pattern-matches the literal Bash command text can be evaded by sufficient indirection (writing a Python file that itself calls the order endpoint, then running that file with an innocuous-looking command). The hook is a strong layer, not a perfect one; the deeper backstop remains that this AI has never had real MT5/Tastytrade/Webull credentials in the first place.
- **Alert fatigue** — a hook too aggressive on false positives trains the user to disable it, which defeats the entire purpose. Precision matters more than maximal coverage.

---

## 4. Structured Output & Retry Loops

**Why structured output is needed.** The moment an LLM's output feeds *code* rather than a human's eyes, free-form prose becomes unreliable — downstream logic needs a guaranteed shape. This codebase already lives by this discipline for its own domain: every trading model in `ftmo_mt5/core/models.py` is a frozen Pydantic model with `field_validator` coercion, specifically because a `TradeRequest` must be structurally guaranteed, never "the code produced something that looked like an order." The moment an LLM is put in a position to decide something a program will act on, it needs the exact same discipline applied to it.

**Pydantic/schema design.** Reuse the existing patterns as the template rather than inventing new conventions: `ftmo_mt5/backtest/models.py`'s `BacktestConfig`/`Trade`/`BacktestResult`, and `ftmo_mt5/core/models.py`'s frozen models with `Decimal` for money and enums for constrained choices. For an LLM decision like "which of these 154 screened rows deserve a `validate()` call," the schema should look like `class CandidateSelection(BaseModel): symbol: str; timeframe: str; strategy: str; reason: str` — a list of these, never a paragraph that has to be re-parsed by eye.

**Input validation.** Validate what goes *into* an LLM-driven step, too. If a loop asks "which timeframe should we test next," the answer must be checked against `ftmo_mt5/lab/windows.py::TIMEFRAMES` before being handed to `service.validate()` — `windows.load()` already raises `ValueError(f"Unknown timeframe '{timeframe}'...")` for bad input at the deterministic layer; the new layer either trusts that guard and budgets for the resulting error, or checks earlier and retries before ever calling it.

**Output validation.** Pydantic parsing catches structural failures (bad JSON, missing fields) automatically. It does **not** catch semantic ones — an LLM can produce syntactically valid JSON naming a strategy called `MEAN_REVERT` that doesn't exist. That has to be checked separately, by cross-referencing the real `strategy_defaults()` output (exactly the function `sweep_equities.py` and `service.py::matrix()` already call to get the authoritative strategy list) before acting on the LLM's pick.

**Retry logic.** On validation failure (schema or semantic), don't crash — feed the specific error back into the prompt and retry, capped at N attempts. This mirrors a pattern already live in this codebase: `SYSTEM_MAX_AGENT_RESTARTS` (default 3, per `README.md`/`CLAUDE.md`) bounds how many times the live watchdog (`ftmo_mt5/supervisor/watchdog.py`) will restart a crashed agent before giving up loudly rather than retrying forever. Apply the identical "bounded retries, then fail loudly" shape to LLM-output validation.

**Implementation steps.**
1. Define the Pydantic schema for the specific decision being delegated to the LLM (e.g., `CandidateSelection` above).
2. Prompt for (or use Claude's native tool-use/structured-output mechanism to enforce) JSON matching that schema, rather than trusting free text.
3. Parse with Pydantic; on `ValidationError`, capture the exact error and re-prompt with it included, up to N retries.
4. Separately, semantically cross-check against real project data (`strategy_defaults()`, `windows.TIMEFRAMES`) — schema validity is necessary, not sufficient.
5. On exhausting retries, fail loudly and log — never silently proceed on an unvalidated guess. This is the same fail-safe philosophy as the risk agent: an unhandled exception inside `RiskAgent.process()` becomes a `CircuitBreakerError` and a hard HALT, never a silent continue (`agents/coordinator.py` lines ~280-289, per the earlier `PROJECT_CONTEXT.md` investigation).

**Backtest-result example.** `ftmo_mt5/lab/metrics.py::measure()` already returns a well-defined structured dict — an existing contract between the backtest engine and every consumer (`service.py`, `verdict.py`, `predict.py`, every script written this session). If an LLM is asked "summarize these 154 rows and pick the top candidate," the correct design has it emit a structured pick that gets *re-verified against the actual CSV/`measure()` output* before being trusted — never let the LLM's summary become the source of truth. This is exactly the discipline followed by hand throughout this conversation: every verdict cited above ("FRAGILE," "failed OOS") was read directly off `verdict.validate()`'s actual gate values, never asserted from a vibe.

**Failure scenarios.**
- LLM hallucinates a symbol outside the real 14 (`AAPL`).
- LLM hallucinates a near-miss strategy name (`MEAN_REVERT` vs. the real `MEAN_REVERSION`).
- LLM returns schema-valid JSON that doesn't match the *current* schema version (drift over time).
- **The most dangerous one**: LLM returns a plausible, schema-valid verdict ("ROBUST") that simply doesn't match what `verdict.py`'s actual `_grade()` would compute — this passes structural validation while being substantively wrong, which is exactly why output validation must include a real cross-check against the deterministic engine, not just "did the JSON parse."

---

## 5. Context & Provenance Management

**CLAUDE.md hierarchy.** The project already has project-level context (`CLAUDE.md` at repo root: Decimal precision, Prague timezone, no-`asyncio.gather`, event-store immutability). This could be extended — not replacing `CLAUDE.md`, adding beneath it — with a module-scoped `ftmo_mt5/lab/CLAUDE.md` documenting the Lab's own specific conventions that this session had to rediscover by trial: position-sizing normalization (`sweep_equities.py`'s docstring on why $100k notional matters), the 300-cell `MAX_CELLS` cap, and the verdict rubric's three hand-graded calibration cases in `tests/test_lab.py`.

**Project/module/task context, three levels.**
- **Project** — `CLAUDE.md`, always loaded, describes what's true everywhere.
- **Module** — a hypothetical `ftmo_mt5/lab/CLAUDE.md`, loaded only when work touches the Lab, describing conventions specific to it.
- **Task** — the large "CONFIRMED FACTS FROM MY OWN RECONNAISSANCE" preamble written into each subagent's spawn prompt this session — context that's true for *this specific delegated task* and doesn't belong in any permanent file.

**Context loading strategy.** What's always loaded (`CLAUDE.md`) vs. what's loaded on demand (a specific module's internals, read only when the task touches it) vs. what must be explicitly passed to a subagent (since subagents don't inherit conversation history). Concretely: `ftmo_mt5/signals/strategies.py`'s full 2290 lines were never re-read in full after the first pass — only the specific `MeanReversionStrategy` class was read when it became relevant, and that extracted understanding carried forward rather than re-reading the file each time. An MCP resource like the `lab://strategies` catalog discussed in the prior MCP-design conversation formalizes exactly this: load the strategy list on demand, once, structured — instead of re-reading a 2290-line file whenever it's needed.

**Research provenance.** The most important idea in this section, given everything produced this session. Every number in `LAB_RESEARCH_REPORT.md` and every table shared in chat traces back to a specific script, specific parameters (`bars=600`, `allow_short=True`, `size_to_capital=True`), and a specific cache state (data as of 2026-08-28). Right now that provenance is informal — it lives in prose ("Ran via a new script... cached data... as of 2026-08-28"), not in a structured, machine-checkable record.

**Audit trails.** Distinct from `EventStore` (which records trading events, not research activity). A research audit trail would record "what was tested, when, with what code, producing what conclusion" as data, not narrative — so a question six months from now ("why did we conclude MEAN_REVERSION was the best candidate?") resolves to an exact `validate()` call and its output, not a remembered paragraph.

**Implementation steps.**
1. Add `ftmo_mt5/lab/CLAUDE.md` (or `study/lab/README.md`) capturing the Lab-specific conventions this session learned the hard way.
2. Establish a provenance record format: each promoted research script's output JSON gets a companion metadata record — script path, exact parameters, timestamp, and a hash or timestamp of the cache files it read.
3. Any report that cites a number cites the provenance record, not just prose.
4. For subagent task-level context, keep — and formalize as a template requirement — the "confirmed facts vs. hypotheses" separation this session already used organically when briefing subagents.

**How to make Lab results reproducible.** `windows.py` caches Yahoo data to `study/lab/cache/*.json`, and that cache updates over time. True reproducibility needs either (a) freezing/versioning the specific cache snapshot a report was built against, or (b) always recording the cache file's timestamp/hash alongside any cited result. This is not hypothetical — the background research agent's own investigation this session found two CSVs (`semis-2156-runs.csv` vs. `semis-sized-2156-runs.csv`) with subtly different numbers and no record of which script, run when, against what cache state, produced which — exactly the failure mode provenance tracking exists to prevent.

**FTMO research example.** The CSV-provenance ambiguity above is the live example: if either file had carried `{script: "sweep_equities.py", timestamp, cache_hash}` metadata, there would have been no ambiguity to investigate at all — the difference between the two files would have been legible on sight instead of requiring detective work.

**Risks.**
- **Over-formalizing** — bolting heavyweight provenance tracking onto every disposable `study/lab/*.py` exploration script defeats the entire point of that directory existing (fast, throwaway, per `sweep_equities.py`'s own framing).
- **Under-formalizing** — the actual bug already hit this session. The right balance: exploratory scripts stay lightweight; anything promoted into a cited report (`LAB_RESEARCH_REPORT.md`-tier) carries provenance metadata.

---

## 6. Claude Skills

**What Skills are.** Packaged, reusable instruction sets — a `SKILL.md` plus optional supporting files — that persist across sessions and get loaded when a task matches their description, teaching a repeatable workflow rather than being re-derived from a one-off prompt each time. This conversation has already used several concretely: `artifact-design` (loaded before building the backtest-matrix artifact) and `grilling` (loaded for the MCP-design interview).

**Skill structure.** A directory containing `SKILL.md` (frontmatter — name and a matching description — plus the instruction body) and, optionally, separate reference files for content too large or too specific to always load inline (comparable to how `artifact-design` defers to a separate `artifact-diagramming` skill only when diagrams are actually needed).

**SKILL.md.** The core artifact: a `name`, a `description` used purely for matching ("trigger when X"), and instructions written for an AI reader — terse, structured, assuming the reader is Claude, not a human skimming documentation.

**Instructions/supporting files.** Main `SKILL.md` for the everyday workflow; separate files for things referenced occasionally but not needed on every load (e.g., the full strategy-class catalog, or the verdict rubric's exact numeric thresholds from `verdict.py`).

**Implementation steps** (hypothetical "FTMO Lab Research" skill — does not exist yet).
1. Create a skill with a description like: "Runs Lab robustness research (backtest, validate, compare vs. buy & hold) for FTMO_MT5 symbols using the actual Lab module — trigger when asked to test, validate, or research a symbol or strategy in this project."
2. Encode, as the instruction body, exactly the methodology this conversation converged on through trial and error: always normalize position sizing (`size_to_capital=True`, per `sweep_equities.py`'s own stated reasoning), always set `allow_short=True` for consistency across calls (the exact default-mismatch bug hit mid-session with LITE/1h — 5 vs. 10 trades depending on which script's defaults ran), prefer `verdict.validate()` over raw `metrics.measure()` before claiming anything "beats" buy & hold, flag `trades < 10` as unreliable per the Lab's own rubric, and never call a result "robust" without checking `verdict.py`'s actual grade.
3. Reference exact file paths (`ftmo_mt5/lab/service.py`, `verdict.py`) and the three hand-graded calibration cases in `tests/test_lab.py` as ground truth for what each grade word actually means.
4. Store it project-scoped (inside this repo's `.claude/skills/`), since it is entirely specific to this codebase's Lab module and meaningless anywhere else.

**Trading Research Skill for FTMO.** This is, concretely, what would have made the back half of this very conversation consistent from the first Lab question instead of by trial and error — the `allow_short` default bug, the position-sizing convention, and the "always check `trades >= 10`" rule were each discovered mid-session rather than known up front. A loaded skill front-loads all of that.

**Skill vs. CLAUDE.md.** `CLAUDE.md` is always-loaded, passive, identity-level context — "what is this project, what are its hard rules." A Skill is triggered for a specific kind of task and carries an active procedure, not just facts. `CLAUDE.md` states the Decimal-precision rule exists; a skill would specify the exact sequence of Lab calls to make, in what order, given "test this symbol."

**Skill vs. MCP.** MCP tools grant *new capability* — a callable `lab_validate_strategy` function that didn't exist as a tool before. A Skill grants *new knowledge about using capability that may already exist* (Bash + Python, as used all session). The Lab-research skill could be built with no MCP server at all — it would just make script-writing more consistent. Conversely, the MCP server (already designed in the prior grilling round: `lab_run_backtest`, `lab_run_matrix`, `lab_validate_strategy`, `lab_list_strategies`) could exist without a skill — but then correct defaults have to be baked into the *tool's own schema* (as that grilling round's Q6 concluded) rather than relied on a skill to remind the model every time.

**Skill vs. Subagent.** A subagent is a delegated, isolated unit of *execution* — does work in its own context, reports back once. A skill is *loaded instruction* that shapes how any agent, main session or subagent, approaches a task. They compose: a Lab-research subagent could also load the Lab-research skill, giving it the same hard-won conventions the main session has, instead of each subagent independently re-deriving them the way both background agents this session each needed a large, separately-written brief.

**Example workflow.** User: "test AVGO on the Lab." Skill triggers on the description match → instructs: check cache status first, run `matrix()` with `size_to_capital=True`/`allow_short=True` across all timeframes, filter to `trades >= 10`, run `validate()` only on the survivors, report using explicit ROBUST/MODERATE/FRAGILE/FAILED framing, never claim success from in-sample alone. What currently lives only as "things I learned the hard way this session" becomes a portable, reliable procedure for any future session or subagent.

---

## How all six work together — one complete architecture

A single end-to-end scenario: *"Find any strategy with robust evidence of beating buy & hold across 30 new symbols."*

1. **Skill (§6)** triggers on the request, loading the Lab-research methodology (correct defaults, the FRAGILE-vs-MODERATE distinction, "never claim from in-sample alone").
2. **Agentic loop & task decomposition (§1)** breaks the goal into screen → filter → verify → report phases with an explicit budget (e.g., ≤90 `validate()` calls total).
3. **Subagents & orchestration (§2)** — if the 30 symbols' data isn't cached (genuinely independent, network-bound work), the coordinator spawns parallel subagents per symbol batch for the screen phase; if it's cached (cheap, sequential), it doesn't bother, per the "when NOT to" guidance.
4. **Structured output & retry (§4)** — each subagent (or the loop step) returns a schema-validated `CandidateSelection` list, retried on validation failure, cross-checked against the real `strategy_defaults()` before any `validate()` call is made on the LLM's say-so.
5. **Hooks & guardrails (§3)** — running underneath all of the above the entire time, structurally incapable of letting any of this reach `mt5_api_server.py`'s order routes, `.env`, or `ftmo_mt5.main`, regardless of what any loop, subagent, or skill instructs.
6. **Context & provenance (§5)** — the final report records exactly which script, parameters, and cache state produced every number; on-demand resources (`lab://strategies`, `lab://cache/status`, from the earlier MCP design) feed context into the loop instead of re-reading source files at every step.

---

## What runs without Claude / what requires Claude / what saves tokens / what's first / what's over-engineering

**Runs entirely without Claude, always has, always should:**
- The live trading loop — `main.py`, all of `ftmo_mt5/agents/`, `ftmo_mt5/engine/rule_engine.py`. Zero LLM, by design, forever.
- The Lab's actual math — `metrics.measure()`, `verdict.validate()`, `backtest.engine.run_backtest()`. Pure Python; 462 `validate()` calls ran in 5.4 seconds this session with no model inference involved.
- Hooks (§3) — pure shell/harness enforcement, running regardless of what any model does.

**Requires Claude:**
- Deciding what to test next given ambiguous, partial results (§1's judgment step).
- Synthesizing a written conclusion from structured data (every prose paragraph produced after a table this session).
- Orchestrating and briefing subagents (§2) — delegation is inherently a language decision.
- Interpreting a Skill's procedural instructions in context (§6).

**Saves tokens:**
- MCP tools — one typed call instead of writing, debugging, and re-parsing a whole throwaway script inline (this session's actual cost pattern for four separate Lab questions).
- Skills — encode methodology once instead of re-deriving/re-explaining it in every response.
- On-demand context resources (`lab://strategies`, cache-status) instead of re-reading whole source files.
- A cache-first policy (already the Lab's own default via `windows.py`) avoiding repeated network-bound work.

**Implement first:**
1. **Structured output & retry (§4)** — lowest risk, and this codebase already has the exact discipline to extend (Pydantic everywhere in `core/models.py` and `backtest/models.py`); it's applying an existing pattern to a new input, not inventing one.
2. **Hooks (§3)** — cheap, pure safety win, no dependency on anything else built first.
3. **The MCP tools** (already scoped in the prior grilling round) — the concrete capability everything else hangs off.
4. **A Skill** encoding the Lab methodology — cheap once the tools exist, high leverage against exactly the kind of default-inconsistency bug hit twice this session.
5. **Agentic loop/orchestration and subagents** — last, deliberately. Most complex, least urgent: this project's current scale (14–30 symbols, seconds of cached compute) doesn't yet need autonomous multi-step orchestration to be practical by hand, as this entire conversation just demonstrated.

**Unnecessary over-engineering, right now, for this project's actual scale:**
- Parallel subagent orchestration for the Lab as it stands today — coordination overhead would exceed the benefit for a 14-symbol, cached-data, 5-second sweep. Only earns its keep at symbol counts in the hundreds or genuinely network-bound, uncached work.
- A fully autonomous loop with no human checkpoint before a final conclusion — given this project's own foundational skepticism (`verdict.py`'s entire design is "don't trust a number until it's survived OOS, cost, removal, and fold tests"), removing the human checkpoint risks exactly the unchecked "declared success" the user has repeatedly warned against this whole conversation.
- Heavy provenance/audit infrastructure on disposable `study/lab/*.py` exploration scripts — that defeats the reason the directory exists. Reserve provenance tracking for promoted, cited artifacts.
- Building the Skill, the MCP server, and a subagent architecture all before validating that MCP tools with correct baked-in defaults alone solve the actual friction — consistent with the "worth it or over-engineering" verdict already reached for MCP specifically: start minimal, expand only against demonstrated need.
