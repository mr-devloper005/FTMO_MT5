# FTMO Trading Strategy — Website PRD (High Level)

Scope note: this is a planning document only — no code, no implementation. It assumes an **internal team tool**, not a public marketing site, given everything this project's own testing has shown so far (no ROBUST/MODERATE strategy found yet). If the intent is actually a public/investor-facing site, the "tone" and "what's never claimed" sections below need to be revisited first.

---

## 1. Vision & Purpose

A single place where the team — you, the Technical Analyst, the Trading Expert — can see, at a glance:
- Is the FTMO account currently compliant, and how much risk budget is left?
- What has the Lab actually proven, versus what's still unvalidated?
- What needs a human decision right now?

It is a **window into `ftmo_mt5` and the Lab**, not a replacement for either. Every number on this site should trace back to a real function call in the existing codebase (`rule_engine.py`, `event_store.py`, `lab/verdict.py`) — never a number invented for the UI.

## 2. Users

| User | What they need from the site |
|---|---|
| **You (owner/architect)** | Full visibility: compliance, research pipeline, team review queue |
| **Technical Analyst** | Browse Lab results, propose new strategies/indicators, flag suspicious-looking backtests |
| **Trading Expert** | Review risk/sizing assumptions, sign off (or not) on candidates before they're treated as real |
| *(Future, not v1)* FTMO account auditors / investors | Read-only compliance history, if this ever needs to be shown externally |

## 3. Goals

- Make the account's real-time FTMO compliance state (daily loss used, total loss used, risk state: NORMAL/CAUTION/RISK_OFF/HALT) impossible to miss.
- Make every Lab finding traceable to its actual grade (ROBUST/MODERATE/FRAGILE/FAILED) — never a bare "this looks good."
- Give the two human experts a real place to review and sign off on candidates, closing the loop we designed in the agentic-architecture lessons.
- Preserve provenance — every report on the site cites the exact script, parameters, and data snapshot that produced it.

## 4. Explicit non-goals (v1)

- **Not** a live-trading control panel. No order placement, no closing positions, no broker connect/disconnect — those stay exactly where they are today (`broker_hub.py`'s existing routes, used directly, not through this new site). This mirrors the hooks/guardrails lesson: the website's blast radius should be structurally read-only for anything money-related.
- **Not** a marketing page. No claims of profitability, no "beats Buy & Hold by 3x" banner — the site should be *more* conservative about claims than the underlying research is, not less.
- **Not** a replacement for `trading_dashboard_live.html`'s manual multi-broker terminal — that keeps doing its job; this site is a different, calmer, higher-level view.

---

## 5. High-Level Features

### A. Compliance & Account Dashboard
- Current equity, balance, open positions count.
- Daily loss used / total loss used, shown as a percentage of the actual FTMO limit — not raw dollars, since the limit itself is what matters.
- Current risk state (NORMAL / CAUTION / RISK_OFF / HALT) as a prominent, unmissable status, not buried in a table.
- Time-to-midnight-boundary countdown (Prague time), since that's when daily limits reset and pre-midnight flattening logic kicks in.

### B. Research / Lab Explorer
- The symbol × timeframe alpha matrix (the same shape as the artifact table already built this session) — browsable, filterable by verdict grade.
- Drill-down from any cell into its full `validate()` result: OOS split, cost ladder, walk-forward folds, parameter sensitivity — the actual evidence, not just a badge.
- A clear, unmissable distinction between "screened" (in-sample only) and "validated" (been through the real robustness test) — this was the single most repeated caveat across this whole conversation, and it should be structural in the UI, not a footnote.

### C. Strategy Library
- The real strategy catalog (from `strategy_defaults()`), each with its plain-English description, current status (untested / FRAGILE / MODERATE / ROBUST), and which symbols/timeframes it's been tried on.
- A place for the Technical Analyst to propose a new strategy or parameter idea as a request, not a code change.

### D. Review Queue (human-in-the-loop)
- Any candidate that clears the automated screen lands here for human sign-off.
- Two required fields per reviewer: **Trading Expert** — "would you risk a funded account on this?" and **Technical Analyst** — "is this a real pattern or an artifact?"
- Nothing is called "validated" for team purposes until both sign off — this is the human gate we designed alongside the automated `verdict.py` gate.

### E. Reports & Provenance Archive
- Every generated research report (like `LAB_RESEARCH_REPORT.md`) lives here, permanently, with its metadata: exact script, parameters, cache snapshot date.
- Historical view — what did we believe last month vs. now, and why did it change.

### F. Alerts (later, not v1)
- Notify the team if risk state changes to CAUTION/RISK_OFF/HALT, or if a new candidate clears the review queue. Deliberately deferred — v1 should be pull (you check the site), not push (the site pages you), until the team actually feels that need.

---

## 6. Design Details

**Tone**: sober, data-terminal, evidence-forward — closer to the Semiconductor Backtest Matrix artifact already built (IBM Plex type, muted neutral background, semantic color reserved for pass/fail states) than to a typical fintech marketing site. No hero banners, no growth charts trending confidently upward unless they're real and current.

**Key screens**:
1. **Home / Compliance** — the risk-state badge and loss-budget gauges, front and center, everything else secondary.
2. **Research Explorer** — the matrix + drill-down, filterable by verdict, symbol, timeframe.
3. **Strategy Detail** — one strategy's full history across every symbol/timeframe it's touched.
4. **Review Queue** — a simple list/card view, one candidate at a time, with the two sign-off fields.
5. **Reports Archive** — chronological, searchable by symbol/strategy/date.

**Visual language for verdicts** — this deserves its own care, since it's the most important information on the site: ROBUST / MODERATE / FRAGILE / FAILED should read as a **status**, not a decoration — a colored pill or stripe, consistent everywhere the grade appears, semantic color kept separate from any brand accent color, exactly like the beats/loses pills in the earlier artifact.

**Access**: since this site *will* see real account data even if it never touches order execution, it needs actual authentication from day one — unlike `broker_hub.py`, which currently has none because it only ever runs on localhost. This is a real, new requirement the moment "website" implies anything beyond one person's own machine.

---

## 7. Functional Modules (what powers each feature)

| Module | Wraps / reads from | New or existing? |
|---|---|---|
| **Compliance API** | `ftmo_mt5/engine/rule_engine.py`, `ftmo_mt5/observability/health.py`, `ftmo_mt5/store/event_store.py` (read-only) | New thin read layer — no changes to those files |
| **Research API** | `ftmo_mt5/lab/service.py`, `verdict.py` — ideally through the MCP tools already scoped (`lab_run_matrix`, `lab_validate_strategy`) rather than duplicating logic | New — reuses the MCP server design from our earlier session |
| **Strategy Catalog API** | `strategy_defaults()` / `list_strategies()` | New thin wrapper, trivial |
| **Review Workflow** | New — no existing equivalent in the codebase at all | New (this is genuinely new functionality, not a rewrap) |
| **Reports & Provenance Store** | New — stores report files + the metadata records discussed in the context/provenance lesson | New |
| **Auth & Access Control** | New — does not exist anywhere in `ftmo_mt5`/`broker_hub.py` today | New, and non-optional the moment this is a "website" rather than a local script |

**Hard boundary, restated once more because it matters most**: none of these modules should import from `ftmo_mt5.execution` or be able to reach `broker_hub.py`'s order/close/connect routes. If a future version of this site ever needs to *show* order history, it reads from `EventStore`, never calls the execution layer directly.

---

## 8. Suggested v1 scope (avoid over-building)

Start with just **A (Compliance Dashboard)** and **B (Research Explorer)**, both read-only, no auth needed yet if it stays on your local machine only. That alone gives the team real value — visibility into compliance and research — without building the Review Queue, Reports Archive, or Auth system before anyone's actually felt the need for them. Add **D (Review Queue)** next, once the Technical Analyst and Trading Expert are actually producing candidates to review. Defer **Alerts** and any external/public access indefinitely, until there's a concrete reason.
