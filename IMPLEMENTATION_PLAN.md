# Implementation Plan — FTMO Trading Strategy Program

For Claude to work against, across sessions. Each phase: goal, deliverable, dependencies, definition of done, real files touched. New files only — nothing in `ftmo_mt5/` itself gets modified by any of this.

---

## Phase 0 — Foundation (COMPLETE)

Already done this session: `PROJECT_CONTEXT.md`, `LAB_RESEARCH_REPORT.md`, `AGENTIC_ARCHITECTURE_GUIDE.md`, `FTMO_WEBSITE_PRD.md`, `FTMO_PROGRAM_PRD.md`. These are the grounding for every phase below — read them before starting a phase, don't re-derive their findings.

## Phase 1 — MCP Server — ✅ COMPLETE (2026-09-09)

**Goal**: turn the four Lab functions used all session into real, callable MCP tools instead of one-off scripts.

**Delivered**: [`mcp_server/`](mcp_server/) — an `MCPServer` app (official `mcp` SDK v2.2.0; `MCPServer` is that SDK's current name for what was `FastMCP` in v1) exposing `lab_run_backtest`, `lab_run_matrix`, `lab_validate_strategy`, `lab_list_strategies`, implementing all ten decisions settled in the grilling session — see [`mcp_server/README.md`](mcp_server/README.md) for the full list, client config for Claude Code and Claude Desktop, and the tool reference table.

**Depends on**: nothing new — imports `ftmo_mt5.lab` directly, same as `study/lab/*.py`. Confirmed the import boundary holds: `mcp_server/` imports only `ftmo_mt5.lab` and `ftmo_mt5.signals`, never `ftmo_mt5.execution`/`ftmo_mt5.agents`/`ftmo_mt5.main`.

**Definition of done — verified**: server runs locally (`mcp.list_tools()` returns all four, `lab_`-prefixed); [`mcp_server/test_server.py`](mcp_server/test_server.py) (8 tests, all passing) reproduces the known LITE/1h/MEAN_REVERSION result (10 trades, FRAGILE, +52.3pp) and the AMAT/1h/MEAN_REVERSION result (11 trades, +8.2pp) exactly through the tool layer, not just the raw script; the cache-first network guard, the condensed-vs-full-detail matrix output, and the `TooManyCells` hard-error path were each exercised and confirmed working.

## Phase 2 — Agentic Research Loop

**Goal**: replace "ask a question, I write a script" with a bounded, autonomous screen → filter → verify → report cycle.

**Deliverable**: an orchestrator (new directory, e.g. `orchestrator/`) that calls `lab_run_matrix` (screen), has an LLM step propose a `CandidateSelection`-schema list of what deserves deeper testing (filter), calls `lab_validate_strategy` only on those (verify), and writes a short report — all under a hard call-budget cap set before the run starts.

**Depends on**: Phase 1's MCP tools.

**Definition of done**: runs unattended end-to-end on a test symbol batch; never exceeds its budget; the filter step's output is schema-validated with retry-on-failure; the loop never overrides `verdict.py`'s actual grade.

## Phase 3 — Hooks & Guardrails

**Goal**: make "never touch live trading" a structural fact, not a followed instruction.

**Deliverable**: a `PreToolUse` hook (registered in `.claude/settings.json`) blocking any Bash command matching `mt5_api_server`, `ftmo_mt5.main`/`ftmo-mt5`, `/api/.*(order|close|connect)`, or `.env` reads/writes that would expose values; a `PostToolUse` audit hook logging any tool call touching `ftmo_mt5/execution/*` or the routes above to a dedicated security log (separate from `EventStore`).

**Depends on**: nothing else — can be built in parallel with Phases 1-2.

**Definition of done**: verified blocking against real test commands that should fail; verified it does *not* false-positive on legitimate reads of `mt5_executor.py` or similar source.

## Phase 4 — Trading Research Skill

**Goal**: encode this session's hard-won Lab methodology so it's never re-discovered by trial and error again.

**Deliverable**: a `SKILL.md` (project-scoped, `.claude/skills/`) describing when it triggers ("testing, validating, or researching a symbol or strategy in this project") and the exact conventions: `size_to_capital=True`, `allow_short=True`, prefer `validate()` over raw `measure()`, flag `trades < 10`, never call something robust without checking the real grade, cite the three hand-graded calibration cases in `tests/test_lab.py`.

**Depends on**: Phase 1 ideally (so the skill can reference the MCP tools directly), but can be written standalone first.

**Definition of done**: a fresh session, given a Lab question, applies the right defaults and skepticism without re-deriving them — spot-check by asking a "test symbol X" question in a clean session and confirming it doesn't repeat the `allow_short` default bug from earlier this session.

## Phase 5 — Website v1 (read-only)

**Goal**: the Compliance Dashboard + Research Explorer from `FTMO_WEBSITE_PRD.md`'s suggested v1 scope — nothing beyond that yet.

**Deliverable**: a thin read-only API layer wrapping `rule_engine.py`, `health.py`, `event_store.py` (compliance data) and the Phase 1 MCP tools (research data), plus a simple frontend rendering the dashboard and the matrix/drill-down view (same visual language as the Semiconductor Backtest Matrix artifact already built this session).

**Depends on**: Phase 1 (research data source).

**Definition of done**: dashboard shows a real, current risk state pulled from the actual event store/health checks; explorer reproduces the same matrix data already validated by hand this session. No auth required yet if it stays local-only; flag clearly if it will ever run beyond localhost, since auth becomes non-optional at that point.

## Phase 6 — Team Review Workflow

**Goal**: the human-in-the-loop gate — Technical Analyst + Trading Expert sign-off before a candidate counts as validated for program purposes.

**Deliverable**: a queue view (extends Phase 5's website) where automated candidates land, with two required sign-off fields per the roles defined in `FTMO_PROGRAM_PRD.md`.

**Depends on**: Phase 2 (candidates to review) and Phase 5 (a website to host the queue in).

**Definition of done**: not built until there's an actual queue of candidates to review — build this only once Phase 2 has produced real output worth a human look, per the over-engineering warning already flagged twice this session.

## Phase 7 — Scaled Research

**Goal**: point the now-automated pipeline (Phases 1-2) at a symbol universe larger than the original 14, tracking toward the program's Objective A success criterion.

**Depends on**: Phases 1-4 all functioning and trusted.

**Definition of done**: an honest, evidenced update to `FTMO_PROGRAM_PRD.md`'s open question — either a real MODERATE+ candidate, or a documented, wider-evidence version of "still none found."

---

## Sequencing note

Phases 1 and 3 (MCP tools, hooks) have no dependency on each other and can run in parallel. Phase 2 needs Phase 1. Phase 4 is cheap and can slot in anywhere after Phase 1. Phases 5-6-7 are deliberately last — this program's current scale doesn't need them yet, and building them early risks exactly the over-engineering flagged throughout this whole conversation.
