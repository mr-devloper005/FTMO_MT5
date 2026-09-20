# FTMO Trading Strategy Program — PRD

For you, to understand the whole program — not just the website (see `FTMO_WEBSITE_PRD.md` for that specific piece). This document is the parent: trading engine, research automation, team workflow, and website all sit underneath it.

---

## 1. Purpose

One program, two objectives, held to the same standard of evidence:

- **Objective A — Financial**: find, validate, and (if one exists) deploy a trading approach with real evidence of beating Buy & Hold, inside FTMO's compliance rules.
- **Objective B — Capability**: build genuine, demonstrable AI/agentic-engineering expertise — concretely, readiness for the Claude Certified Architect – Foundations certification — using this real codebase as the vehicle, not toy exercises.

**Guiding principle, stated once because it governs every decision below**: Objective A's urgency must never lower the rigor Objective B depends on. A strategy only counts as "found" if it survives the Lab's actual validation (out-of-sample, walk-forward, cost stress) — never because it was wanted to be true. This isn't a constraint fighting the financial goal; it's the only way that goal has a real shot.

## 2. Background

`ftmo_mt5/` already exists: a multi-agent trading system (signal → risk → execution) with a dedicated FTMO compliance engine (`rule_engine.py`, enforcing the 5% daily / 10% total loss limits), and a research Lab (`lab/`) with a genuine anti-overfitting engine (`verdict.py`) — out-of-sample splits, cost ladders, walk-forward folds, parameter sensitivity, graded ROBUST/MODERATE/FRAGILE/FAILED.

**Current evidence** (from this program's own testing, real, not projected): across roughly 500 rigorous `validate()` runs on 14 real symbols, multiple timeframes, four strategies — **0 ROBUST, 0 MODERATE**, ~29 FRAGILE, the rest FAILED. Buy & Hold beat every tested strategy at every timeframe of 2 hours or longer, with zero exceptions. This is the honest starting point for Objective A — not a setback, a baseline.

## 3. Scope

**In scope:**
- Research automation — MCP tools + an agentic research loop, so the symbol/strategy search scales without hand-writing a new script every time.
- Safety guardrails — hooks that structurally prevent any of this automation from ever touching live orders, broker connections, or credentials.
- Team review workflow — a place for the Technical Analyst and Trading Expert to sign off on any candidate before it's treated as real.
- Visibility — a website (v1: compliance dashboard + research explorer, read-only).
- Continued Lab research, at a larger scale than the initial 14-symbol pass.

**Out of scope, deliberately, for now:**
- Any change to the live trading execution logic itself.
- Any public-facing use of results, marketing, or investor materials.
- Any claim of profitability ahead of real, validated evidence.

## 4. Users & Roles

| Role | Responsibility |
|---|---|
| You | Program owner — sets priorities, resolves the review queue's disagreements |
| Technical Analyst | Proposes strategies/indicators, flags in-sample-only results that look like artifacts |
| Trading Expert | Reviews risk/sizing realism, signs off (or not) on whether a candidate is fundable |
| Claude | Research automation, tooling, engineering, and — same as this whole conversation — the second set of eyes that keeps every claim tied to actual evidence |

## 5. Functional Requirements

1. **Research Automation** — MCP tools (`lab_run_backtest`, `lab_run_matrix`, `lab_validate_strategy`, `lab_list_strategies`) wrapping the existing Lab, plus an orchestrator loop (screen → filter → verify → report) with a hard budget cap and no LLM involvement in the actual pass/fail math.
2. **Guardrails** — hooks blocking any tool call that would reach live order/close/connect routes, `.env`, or the live trading entrypoint, regardless of how the request is phrased.
3. **Team Review Workflow** — a queue where automated candidates wait for both required human sign-offs before being called validated for program purposes.
4. **Website v1** — read-only compliance dashboard (risk state, loss budgets) and research explorer (the symbol × timeframe matrix with drill-down into real evidence).
5. **Provenance** — every report and every candidate traces back to the exact script, parameters, and data snapshot that produced it.

## 6. Non-Functional Requirements

- **No live-trading exposure** from any new tooling, structurally, not by convention.
- **Reproducibility** — a result should be re-derivable from its recorded provenance.
- **Auditability** — every automated decision (what to test next, what a subagent concluded) should be traceable to a written record, not just a remembered conversation.

## 7. Success Criteria

- **Objective A**: either a strategy reaches MODERATE or better under real validation, or the program reaches a well-evidenced, honest conclusion that none exists in the tested universe — both are valid, useful outcomes.
- **Objective B**: the MCP server, the agentic loop, the guardrails, the skill, and the website v1 all exist, work, and were built by you with Claude, matching the four Claude Certified Architect – Foundations exam domains.

## 8. Milestones (tracking the certification calendar already set)

- **Sep 6–12**: agentic research loop, manual then Claude-driven filtering.
- **Sep 13–20**: MCP server (4 tools) + Trading Research skill.
- **Following weeks**: hooks/guardrails, website v1, team review workflow, scaled research.

## 9. Risks

- **Overfitting / false positives** — mitigated structurally by `verdict.py`'s existing rubric; the program's discipline is to trust it over any single impressive-looking number.
- **Compliance risk** — mitigated by the guardrails being enforced at the hook layer, not left to any model's judgment in the moment.
- **Scope creep** — flagged repeatedly already: parallel subagents, heavy provenance tooling, and full autonomy are all over-engineering at this program's current scale. Build the minimum that solves a demonstrated need.
- **Team bandwidth** — the review workflow depends on the Technical Analyst and Trading Expert actually having candidates to review; don't build it before there's a queue to put in it.

## 10. Open Question

If, after scaled research, still nothing reaches MODERATE — what happens to Objective A? This should be decided in advance, not discovered under pressure: a likely honest answer is that the program's financial goal shifts from "trade this specific strategy" to "you now have a validated research process that can be pointed at new ideas as they come up" — which is itself a real asset.
