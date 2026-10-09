// Builds FTMO_AI_Trading_Agent_Blueprint.docx
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, ImageRun, Table, TableRow, TableCell, Footer,
  AlignmentType, PageOrientation, WidthType, ShadingType, BorderStyle, HeadingLevel,
  LevelFormat, PageNumber, PositionalTab, PositionalTabAlignment, PositionalTabRelativeTo,
  PositionalTabLeader, VerticalAlign, TableLayoutType, PageBreak, Tab, TabStopType, SectionType,
} = require("docx");

const IMG = path.join(__dirname, "img");
const OUT = process.argv[2] || path.join(__dirname, "FTMO_AI_Trading_Agent_Blueprint.docx");

// ---------------------------------------------------------------- tokens
const FONT = "Arial";
const INK = "1F2937", MUTED = "5B6472", LINE = "D6DAE0", ZEBRA = "F7F8FA", ACCENT = "2F6FDE";
const ST = {
  built:  { fill: "E3F4EA", ink: "10593A", label: "BUILT" },
  reuse:  { fill: "E4EDFC", ink: "1B4394", label: "REUSABLE" },
  needs:  { fill: "FDF1DC", ink: "7A4F05", label: "NEEDS IMPL." },
  human:  { fill: "EFE8FB", ink: "4A2A94", label: "HUMAN" },
  hazard: { fill: "FBE6E4", ink: "8A1F18", label: "HAZARD" },
  neutral: { fill: "F3F4F6", ink: "5B6472", label: "—" },
};
const A4 = { width: 11906, height: 16838 };
const MARGIN = { top: 1134, bottom: 1134, left: 1134, right: 1134, footer: 560 };
const LW0 = A4.height - 2268;        // landscape content width 14570
const PW = LW0;                     // every table/callout spans this
const PROSE_R = 3700;               // right indent for prose: ~7.5in measure
const LW = LW0;

// ---------------------------------------------------------------- text helpers
const r = (text, o = {}) => new TextRun({ text, font: FONT, size: o.size || 20, bold: o.bold,
  italics: o.italics, color: o.color || INK, shading: o.shading });
function runs(spec, base = {}) {
  // spec: string | array of strings / [text, opts]
  if (typeof spec === "string") return [r(spec, base)];
  return spec.map(x => typeof x === "string" ? r(x, base) : r(x[0], { ...base, ...x[1] }));
}
const P = (spec, o = {}) => new Paragraph({
  children: runs(spec, { size: 21, ...o }), alignment: o.align,
  indent: o.full ? undefined : { right: PROSE_R },
  spacing: { before: o.before ?? 0, after: o.after ?? 120, line: o.line ?? 292 },
  keepNext: o.keepNext,
});
const H1 = (t, pb) => new Paragraph({ heading: HeadingLevel.HEADING_1, 
  spacing: { before: 0, after: 160 }, children: [r(t, { size: 34, bold: true })] });
const H2 = (t, pb) => new Paragraph({ heading: HeadingLevel.HEADING_2, keepNext: true, 
  spacing: { before: 280, after: 100 }, children: [r(t, { size: 25, bold: true })] });
const H3 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_3, keepNext: true,
  spacing: { before: 200, after: 80 }, children: [r(t, { size: 21, bold: true, color: ACCENT })] });
const bullet = (spec, lvl = 0) => new Paragraph({ numbering: { reference: "bul", level: lvl },
  indent: { left: 440, hanging: 260, right: PROSE_R }, children: runs(spec, { size: 21 }), spacing: { after: 70, line: 284 } });
const num = (spec) => new Paragraph({ numbering: { reference: "num", level: 0 },
  indent: { left: 440, hanging: 320, right: PROSE_R }, children: runs(spec, { size: 21 }), spacing: { after: 90, line: 284 } });
const kicker = (t) => P([[t, { size: 16, bold: true, color: ACCENT }]], { after: 40, full: true });
const caption = (t) => P([[t, { size: 17, italics: true, color: MUTED }]], { before: 80, after: 0, full: true });
const pageBreak = () => new Paragraph({ children: [new PageBreak()] });

function badge(kind) {
  const s = ST[kind];
  return new TextRun({ text: ` ${s.label} `, font: FONT, size: 15, bold: true, color: s.ink,
    shading: { type: ShadingType.CLEAR, fill: s.fill, color: "auto" } });
}

// ---------------------------------------------------------------- tables
const border = { style: BorderStyle.SINGLE, size: 4, color: LINE };
const borders = { top: border, bottom: border, left: border, right: border };
const CM = { top: 48, bottom: 48, left: 100, right: 100 };

function toParas(content) {
  // string -> one paragraph; [str, str] -> one paragraph each;
  // [[text, opts], ...] -> one paragraph of runs; [[[text, opts]], ...] -> one paragraph per run-spec
  if (typeof content === "string") return [content];
  if (content.every(x => typeof x === "string")) return content;
  if (Array.isArray(content[0]) && Array.isArray(content[0][0])) return content;
  return [content];
}
function cell(content, width, o = {}) {
  let children;
  if (content && content.status) {
    const s = ST[content.status];
    children = [new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 0 },
      children: [r(content.label || s.label, { size: 15, bold: true, color: s.ink })] })];
    o.fill = s.fill;
  } else {
    children = toParas(content).map(pp => new Paragraph({ spacing: { after: 30, line: 250 },
      children: runs(pp, { size: o.size || 17, bold: o.bold, color: o.color }) }));
  }
  return new TableCell({
    width: { size: width, type: WidthType.DXA }, borders, margins: CM,
    shading: o.fill ? { type: ShadingType.CLEAR, fill: o.fill, color: "auto" } : undefined,
    verticalAlign: o.vcenter ? VerticalAlign.CENTER : VerticalAlign.TOP, children,
  });
}

function table(widths0, header, rows, o = {}) {
  const sum0 = widths0.reduce((a, b) => a + b, 0);
  const target = o.width || LW;
  const widths = o.absolute ? widths0 : widths0.map(w => Math.round(w * target / sum0));
  if (!o.absolute) widths[widths.length - 1] += target - widths.reduce((a, b) => a + b, 0);
  const total = widths.reduce((a, b) => a + b, 0);
  const head = new TableRow({ tableHeader: true, cantSplit: true, children: header.map((h, i) =>
    cell(h, widths[i], { fill: INK, bold: true, color: "FFFFFF", size: 16, vcenter: true })) });
  const body = rows.map((row, ri) => new TableRow({ cantSplit: true, children: row.map((c, i) =>
    cell(c, widths[i], { fill: (o.zebra !== false && ri % 2 === 1) ? ZEBRA : undefined,
      bold: o.boldFirst && i === 0, vcenter: c && c.status, size: o.size })) }));
  return new Table({ width: { size: total, type: WidthType.DXA }, columnWidths: widths,
    layout: TableLayoutType.FIXED, rows: [head, ...body] });
}
const S = (k, label) => ({ status: k, label });
const spacer = (after = 120) => new Paragraph({ spacing: { after }, children: [] });

// callout box: one-cell table with a coloured left rule
function callout(kind, title, lines, width = LW - PROSE_R) {
  const s = ST[kind] || { fill: "F3F4F6", ink: INK };
  const kids = [new Paragraph({ spacing: { after: 60 }, children: [r(title, { bold: true, size: 19, color: s.ink })] })]
    .concat(lines.map(l => new Paragraph({ spacing: { after: 50, line: 270 }, children: runs(l, { size: 18 }) })));
  return new Table({ width: { size: width, type: WidthType.DXA }, columnWidths: [width],
    layout: TableLayoutType.FIXED, rows: [new TableRow({ children: [new TableCell({
      width: { size: width, type: WidthType.DXA }, margins: { top: 140, bottom: 140, left: 220, right: 200 },
      shading: { type: ShadingType.CLEAR, fill: s.fill, color: "auto" },
      borders: { top: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, bottom: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
                 right: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, left: { style: BorderStyle.SINGLE, size: 24, color: s.ink } },
      children: kids })] })] });
}

// ---------------------------------------------------------------- images
function png(name, maxW, maxH) {
  const buf = fs.readFileSync(path.join(IMG, name));
  const w = buf.readUInt32BE(16), h = buf.readUInt32BE(20);
  const k = Math.min(maxW / w, maxH / h);
  return new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 0 },
    children: [new ImageRun({ type: "png", data: buf, transformation: { width: Math.round(w * k), height: Math.round(h * k) },
      altText: { title: name, description: name, name } })] });
}

// ---------------------------------------------------------------- footer & sections
const footer = new Footer({ children: [new Paragraph({ spacing: { after: 0 },
  tabStops: [{ type: TabStopType.RIGHT, position: LW }],
  border: { top: { style: BorderStyle.SINGLE, size: 4, color: LINE, space: 6 } },
  children: [
    r("FTMO_MT5  ·  Independent AI Trading Agent — Integration Blueprint  ·  Paper trading only", { size: 15, color: MUTED }),
    new TextRun({ children: [new Tab(), "Page ", PageNumber.CURRENT], font: FONT, size: 15, color: MUTED }),
  ] })] });
const emptyFooter = new Footer({ children: [new Paragraph({ children: [] })] });
const page = (children) => children;
const portrait = page, landscape = page;   // one orientation throughout — see note in the report build

// ================================================================= CONTENT
const groups = [];
const sections = { push: (arr) => groups.push(arr) };

// ---------------------------------------------------------------- cover
sections.push(page([
  P([["FTMO_MT5  ·  ARCHITECTURE REPORT", { size: 18, bold: true, color: ACCENT }]], { after: 240 }),
  P([["An Independent AI Trading Agent", { size: 56, bold: true }]], { after: 60, line: 240 }),
  P([["for the FTMO_MT5 System", { size: 56, bold: true, color: MUTED }]], { after: 360, line: 240 }),
  P([["What the system already does, what Sir and the team still do by hand, and exactly where an AI agent fits — built on the code that exists today, not on generic AI theory.", { size: 24, color: MUTED }]], { after: 480, line: 320 }),
  table([2400, PW - 2400], ["Item", "Detail"], [
    [["Prepared", { bold: true }], "30 September 2026"],
    [["Basis", { bold: true }], "Direct inspection of the FTMO_MT5 source tree (~22,000 lines of Python across ftmo_mt5/, mcp_server/, systematic/, broker_hub.py), live FTMO symbol specifications, and results already produced by the research pipeline."],
    [["Scope", { bold: true }], "Paper trading only. Live trading stays disabled; nothing here enables it."],
    [["Audience", { bold: true }], "Sir, the trading team, and whoever builds the agent."],
  ].map(row => row.map(c => typeof c === "string" ? c : [c]))),
  spacer(360),
  P([["How to read the status labels", { size: 20, bold: true }]], { after: 100 }),
  table([1900, PW - 1900], ["Label", "Meaning"], [
    [S("built"), "Exists and works today. The agent uses it as-is."],
    [S("reuse"), "Code exists but nothing calls it yet. Wiring, not writing."],
    [S("needs"), "Has to be built. Scope is described in this report."],
    [S("human"), "A named person decides. The agent may recommend, never decide."],
    [S("hazard"), "Exists today and is unsafe for this purpose. Fix before building on it."],
  ]),
]));

// ---------------------------------------------------------------- contents + exec summary
sections.push(portrait([
  H1("Contents"),
  ...[
    ["1", "Executive summary"], ["2", "The system today"], ["3", "What the team does by hand — and what the agent takes over"],
    ["4", "Target architecture"], ["5", "The agent's operating rhythm"], ["6", "Stage-by-stage design"],
    ["7", "Tool contract"], ["8", "Worked FTMO examples"], ["9", "Safety architecture"],
    ["10", "Human approval matrix"], ["11", "Delivery roadmap"], ["12", "What the agent will not solve"],
    ["A", "Evidence — file and line references"], ["B", "Glossary"],
  ].map(([n, t]) => P([[n + "    ", { bold: true, color: ACCENT }], t], { after: 70 })),
  spacer(200),
  H1("1  Executive summary"),
  P("FTMO_MT5 already contains most of what an AI trading agent needs in order to be safe: a research lab that grades strategies honestly, a risk engine that enforces FTMO's daily and overall loss limits, an append-only audit log, and a paper-trading pipeline that cannot reach a real broker. What is missing is the layer that runs this machinery on a schedule, reads the results, explains them, and hands decisions to a person at the right moments. That layer is the agent."),
  callout("built", "The design in one sentence", [
    "The agent should not be a trader that happens to have safeguards. It should be an analyst and planner with no ability to trade, sitting on top of deterministic code that does.",
  ]),
  H2("Five findings from the code"),
  table([420, 3900, 3818, 1500], ["#", "Finding", "Why it matters", "Status"], [
    ["1", [["An AI agent already runs inside the hub. ", { bold: true }], "/agents is a general-purpose Claude Code runner: shell access, file edits, acceptEdits by default, bypassPermissions selectable from the page — in the same process as the order routes."],
      "It must not become the trading agent. With a shell it can call the hub's own order endpoint, bypassing the import boundary that makes the MCP server safe.", S("hazard")],
    ["2", [["The live loop is disconnected from research. ", { bold: true }], "main.py trades one hardcoded EMA-crossover rule. Nothing the Lab validates ever reaches it."],
      "The agent's plans have one destination: the paper pipeline. The live loop stays out of scope.", S("needs")],
    ["3", [["An ML validation layer is written but dormant. ", { bold: true }], "Meta-labeler, drift detection, CSCV, Deflated Sharpe and a promotion gate exist; nothing imports them. The meta-label gate passes every signal when no score is supplied."],
      "Ready-made parts for the agent's validation step. Wiring, not writing.", S("reuse")],
    ["4", [["Promoted rules are mostly untradable. ", { bold: true }], "Of the five rules research promoted, four — VZ, IEF, TLT, GIS — are not on FTMO's symbol list. Only NKE is."],
      "The pipeline has no tradability check. It is the first concrete job for both the agent and the code.", S("needs")],
    ["5", [["There is no validated edge yet. ", { bold: true }], "0 ROBUST, 7 MODERATE after calibration fixes — out of 555 screens, which is not clearly better than chance."],
      "The agent's first duty is honest reporting, not trading. The grading rubric already enforces this.", S("built")],
  ]),
  H2("Recommendation"),
  num([["Phase 0 — make it safe. ", { bold: true }], "Lock /agents down (tool allowlist, remove bypassPermissions from the page, validate the working directory) and replace the three health checks that always report healthy."]),
  num([["Run the agent as a separate process ", { bold: true }], "on the Claude Agent SDK, with allowed_tools restricted to MCP tools: no shell, no file edits, a dollar budget per run."]),
  num([["Give it tools that read and propose — never execute. ", { bold: true }], "Extend the existing ftmo-lab MCP server with research, paper-account and reporting tools."]),
  num([["Keep every decision with money attached in deterministic code: ", { bold: true }], "grading (verdict.validate), limits and sizing (RiskAgent), fills (PaperBroker), audit (EventStore)."]),
  num([["Put a person at five gates: ", { bold: true }], "promoting a rule, approving a cycle plan (until Phase 3), clearing a HALT, changing risk limits, and anything touching live trading."]),
]));

// ---------------------------------------------------------------- 2 system today
sections.push(landscape([
  H1("2  The system today"),
  png("01_as_built.png", 960, 565),
  caption("Figure 1 — Components as they exist on 30 Sep 2026. The red dashed arc is the hazard in Finding 1; the amber dashed arrow is the missing Lab → live-loop bridge."),
]));
sections.push(portrait([
  P("Every component below was located in the source tree and checked for whether anything actually calls it. \"Built\" means it runs; \"Reusable\" means the code exists but is not wired into any path."),
  table([1900, 2050, 2980, 1250, 1458], ["Component", "Where", "What it does today", "Status", "Role for the agent"], [
    ["Research Lab", "ftmo_mt5/lab/", "Backtests, metrics, robustness grading: ROBUST / MODERATE / FRAGILE / FAILED", S("built"), "Research engine it calls"],
    ["Strategy library", "ftmo_mt5/signals/", "16 registered rules; parameters typed int / float / bool", S("built"), "The menu it selects from — it never writes rules"],
    ["ftmo-lab MCP server", "mcp_server/", "6 tools, 3 reference resources, results index of ~3,500 runs", S("built"), "Primary tool surface"],
    ["Systematic pipeline", "systematic/", "Watchlist → screen → validate → rank → paper cycle", S("built"), "Executes the approved plan"],
    ["RiskAgent + rule engine", "agents/risk_agent.py\nengine/rule_engine.py", "NORMAL → CAUTION → RISK_OFF → HALT against FTMO daily and overall loss", S("built"), "Final say on every order"],
    ["PaperBroker", "systematic/paper_broker.py", "In-process ledger; fills at the close with slippage and commission", S("built"), "Where approved trades land"],
    ["EventStore", "store/event_store.py", "Append-only audit; 8 event types", S("built"), "Source of truth for monitoring and reports"],
    ["Systematic panel", "systematic/webui.py (:8601)", "Stocks, P&L, trade history, research, run controls", S("built"), "Where approvals get clicked"],
    ["Supervisor + reconciliation", "supervisor/, execution/reconciliation.py", "Heartbeats, kill switch, position reconciliation", S("built"), "Monitoring inputs"],
    ["ML layer", "ftmo_mt5/ml/", "Meta-labeler, drift (PSI / ADWIN), CSCV, Deflated Sharpe, promotion gate", S("reuse"), "Extra validation and drift checks"],
    ["Market data (FMP)", "MCP connector", "Quotes, company profiles, earnings and economic calendars", S("reuse"), "Pre-market event scan"],
    ["Live loop", "ftmo_mt5/main.py", "Signal → risk → execution with one EMA rule; has never completed a trade cycle", S("needs"), "Outside the agent's path until Phase 4"],
    ["Health checks", "observability/health.py", "Three checks hardcoded to healthy=True", S("needs"), "Must be real before the agent trusts them"],
    ["Claude runner /agents", "broker_hub.py:1316–1476", "Free-form Claude Code tasks with shell and edits, in the hub process", S("hazard"), "Developer tool only — lock down, never promote"],
  ].map(row => row.map((c, i) => (typeof c === "string" && i === 1) ? c.split("\n") : c))),
]));

// ---------------------------------------------------------------- 3 manual vs agent
const LCOL = [1500, 2900, 3450, 2600, 1300, 2820];
sections.push(landscape([
  H1("3  What the team does by hand — and what the agent takes over"),
  P([["Read across a row: today's manual work, the agent's job, what stays in deterministic code, delivery status, and where a person still decides.", { color: MUTED }]], { after: 160, full: true }),
  table(LCOL, ["Stage", "Today — Sir / team", "The agent", "Stays deterministic", "Status", "Human gate"], [
    [["1  Stock screening", { bold: true }], "Picks tickers by judgement and types them into My Stocks. A one-off FTMO top-25 ranking was produced on request on 30 Sep.",
      "Rebuilds the FTMO-tradable universe weekly from FTMO's symbol list; ranks by liquidity, ATR fit and overnight-gap risk; flags delistings.", "Ticker validation (universe.py); live-data check on add", S("needs"), "Approve watchlist changes"],
    [["2  Market analysis", { bold: true }], "Reads news, charts and quotes informally. No written brief.",
      "Pre-market brief: overnight moves, earnings and macro calendar, gap risk per open position, data freshness.", "Nothing — analysis only", S("needs"), "None — informational"],
    [["3  Strategy selection", { bold: true }], "Chooses which rule to try on which stock.",
      "Selects only from the 16 registered rules and the current ranking. Never invents or edits a rule.", "Strategy registry and rule code", S("built"), "None — selecting is not deploying"],
    [["4  Backtesting", { bold: true }], "Runs the Lab page or a Claude session ad hoc.",
      "Runs research on a schedule through MCP; checks the results index before re-running; every run is logged.", "Lab engine, cost model, windows", S("built"), "None"],
    [["5  Validation", { bold: true }], "Reads the verdict and decides whether it is good enough.",
      "Explains why each rule graded as it did, and states how many tests were run so a lucky pass is visible.", "verdict.validate — the grade itself", S("built"), "Promote a rule (≥ MODERATE)"],
    [["6  Trade planning", { bold: true }], "Clicks Paper cycle after the close, then reads what happened.",
      "Dry-runs the cycle and writes a plan with a reason per trade; checks FTMO tradability, earnings blackout and exposure.", "Cycle logic; fresh-signal rule", S("needs"), "Approve the plan (Phase 2)"],
    [["7  Risk checks", { bold: true }], "Watches equity and trusts RiskAgent.",
      "Explains risk state and remaining daily-loss room in dollars; warns before a threshold, not after.", "RiskAgent, rule engine, sizing", S("built"), "Change risk limits"],
    [["8  Execution", { bold: true }], "Clicks Paper cycle; places manual orders in the terminal.",
      [["Nothing. ", { bold: true }], "The agent holds no order tool."], "PaperBroker (later: sandbox adapter)", S("built"), "Any live mode"],
    [["9  Monitoring", { bold: true }], "Opens dashboards; runs curl to confirm paper mode.",
      "Tails EventStore; detects HALT, stale data, broker-mode drift and reconciliation breaks; raises an incident.", "Supervisor, kill switch, HALT", S("needs"), "Clear a HALT"],
    [["10  Reporting", { bold: true }], "Writes manager updates by hand (a manager-report skill exists).",
      "Daily P&L, weekly research memo, incident reports, and a proposed-vs-executed diff.", "EventStore as the source of every number", S("needs"), "Review before sending"],
    [["Ops  Environment", { bold: true }], "Starts the VM, hub and port forwarder; checks mode with curl.",
      "Verifies paper mode and data freshness before any work; refuses to continue if either fails.", "start.sh; LIVE_TRADING_ENABLED = False", S("needs"), "None"],
  ], { size: 16 }),
]));

// ---------------------------------------------------------------- 4 target
sections.push(landscape([
  H1("4  Target architecture"),
  png("02_target.png", 960, 565),
  caption("Figure 2 — Five layers. The agent's authority is its tool list: every tool reads or proposes. Execution happens only in L3, after an L0 gate."),
]));
sections.push(portrait([
  H2("Design principles"),
  bullet([["The LLM proposes; deterministic code disposes. ", { bold: true }], "Grades, limits, sizing and fills are computed by code that already exists and is tested."]),
  bullet([["Authority is the tool list, not the prompt. ", { bold: true }], "A prompt can be ignored or manipulated; a missing tool cannot be called."]),
  bullet([["Every number comes from a tool. ", { bold: true }], "The model explains figures; it never produces them. Reports are assembled from tool outputs, with prose around them."]),
  bullet([["Structural safety over behavioural safety. ", { bold: true }], "The same idea already protects the codebase: mcp_server/ and systematic/ cannot import execution code, and a test enforces it."]),
  bullet([["People decide at irreversible or policy-changing moments ", { bold: true }], "— and nowhere else, so approvals stay meaningful instead of becoming a rubber stamp."]),
  H2("Agent process specification"),
  table([2100, 3900, 3638], ["Setting", "Value", "Why"], [
    [["Runtime", { bold: true }], "Claude Agent SDK (Python), its own process, started by a scheduler", "Isolated from broker_hub and its order routes"],
    [["allowed_tools", { bold: true }], "mcp__ftmo-lab__*, mcp__ftmo-research__*, mcp__ftmo-paper__*, mcp__ftmo-reports__*, market-data read tools", "The tool list is the permission model"],
    [["Denied", { bold: true }], "Bash, Write, Edit, WebFetch, and any broker tool", "Removes every path to an order or a code change"],
    [["permission_mode", { bold: true }], "default — never acceptEdits or bypassPermissions", "No silent writes"],
    [["max_budget_usd / max_turns", { bold: true }], "Per run, e.g. $1–2 and a turn cap (the hub already uses a $2 cap)", "A loop cannot burn money"],
    [["Models", { bold: true }], "Claude Sonnet 5 for daily briefs and plans; Claude Opus 5.5 for the weekly research memo; Claude Haiku 4.5 for monitoring summaries", "Match cost to the judgement needed"],
    [["Working directory", { bold: true }], "Fixed in code, never taken from a request", "The current /agents accepts any cwd from the page"],
  ]),
  spacer(160),
  callout("hazard", "Why not reuse /agents?", [
    "It was built as a developer convenience, and its own comment calls it a free-form Claude Code runner. It loads project settings, can edit files, can use the shell, and defaults to acceptEdits. Turning it into a trading agent would mean removing almost everything it is for. Build the trading agent separately; keep /agents as a locked-down developer tool.",
  ]),
]));

// ---------------------------------------------------------------- 5 rhythm
sections.push(landscape([
  H1("5  The agent's operating rhythm"),
  png("03_loop.png", 960, 565),
  caption("Figure 3 — Four loops. FTMO US equity CFDs trade 16:35–23:00 GMT+3 (09:35–16:00 New York), so every agent decision falls outside the session."),
]));
sections.push(portrait([
  H2("Schedule"),
  table([1500, 2500, 2700, 1638, 1300], ["When (NY)", "Job", "Tools used", "Output", "Gate"], [
    ["Sunday", "Refresh the FTMO universe, run research, write the memo", "universe_ftmo, research_run, research_ranking, lab_lookup", "Research memo with grades, reasons and test count", S("human", "PERSON")],
    ["08:30 daily", "Mode and data check; event scan; gap flags", "risk_status, paper_account, market calendar and quotes", "Morning brief", S("neutral", "INFO ONLY")],
    ["16:15 daily", "Dry-run the cycle; explain each planned trade", "paper_plan, risk_status, research_ranking", "Cycle plan", S("human", "PERSON")],
    ["Continuous", "Watch risk state and events; draft incidents", "events_tail, risk_status", "Alerts and incident reports", S("human", "PERSON")],
    ["End of day", "P&L and proposed-vs-executed diff", "paper_trades, paper_account, events_tail", "Daily report", S("neutral", "INFO ONLY")],
  ]),
  P([["Gate: ", { bold: true }], "PERSON means someone acts before anything changes. INFO ONLY means the output changes nothing by itself."], { before: 120 }),
  P("Why 16:15 and not 16:00: the pipeline ignores the day's bar until 16:15 New York time so it never trades on a close that has not been published. The agent inherits that rule rather than re-deciding it."),
]));

// ---------------------------------------------------------------- 6 stage by stage
function stageTable(n, title, status, exists, adds, stays, gate) {
  const rows = [["Exists today", exists], ["Agent adds", adds], ["Stays in code", stays], ["Human gate", gate]];
  const W = [2300, LW - 2300];
  const hdr = new TableRow({ cantSplit: true, children: [
    new TableCell({ width: { size: W[0], type: WidthType.DXA }, borders, margins: CM,
      shading: { type: ShadingType.CLEAR, fill: INK, color: "auto" },
      children: [new Paragraph({ spacing: { after: 0 }, children: [r(`${n}`, { bold: true, color: "FFFFFF", size: 18 })] })] }),
    new TableCell({ width: { size: W[1], type: WidthType.DXA }, borders, margins: CM,
      shading: { type: ShadingType.CLEAR, fill: INK, color: "auto" },
      children: [new Paragraph({ spacing: { after: 0 }, children: [r(title + "   ", { bold: true, color: "FFFFFF", size: 18 }), badge(status)] })] }),
  ] });
  const body = rows.map(([k, v]) => new TableRow({ cantSplit: true, children: [
    cell([[k, { bold: true }]], W[0], { fill: ZEBRA }), cell(v, W[1]) ] }));
  return [new Table({ width: { size: PW, type: WidthType.DXA }, columnWidths: W, layout: TableLayoutType.FIXED, rows: [hdr, ...body] }), spacer(100)];
}
sections.push(portrait([
  H1("6  Stage-by-stage design"),
  P("One card per stage. \"Stays in code\" is the part the agent may read or trigger but never replace."),
  ...stageTable("1", "Stock screening", "needs",
    "Watchlist validation checks every ticker against live data before saving (fixed 27 Sep to accept exchange suffixes such as RELIANCE.NS). An FTMO top-25 list was measured once on 30 Sep (systematic/watchlists/ftmo-top25.txt); the scoring script was not added to the repo.",
    "Weekly: read FTMO's symbol list, measure all US-session names over one identical window, rank by liquidity, ATR fit and gap safety, and propose additions and removals with the evidence.",
    "Ticker parsing, data validation, the scoring formula.",
    "A person approves watchlist changes."),
  ...stageTable("2", "Market analysis", "needs",
    "Nothing written. The FMP connector already returns quotes, profiles and calendars in Claude sessions.",
    "08:30 brief: overnight moves on held names, earnings within two sessions, macro releases, gap risk against remaining daily-loss room, and whether today's bars are fresh.",
    "Price data and the completed-bar rule.",
    "None — the brief changes nothing by itself."),
  ...stageTable("3", "Strategy selection", "built",
    "16 registered rules with typed parameters; research picks the best three per stock for full validation.",
    "Explains which rules were tried and why the winners won. Never proposes a rule outside the registry and never tunes parameters live.",
    "The registry, rule code, parameter types.",
    "None — selection is not deployment."),
]));
sections.push(portrait([
  H2("Stage-by-stage design — stages 4 to 6"),
  ...stageTable("4", "Backtesting", "built",
    "Lab engine and six MCP tools with canonical defaults; results index of ~3,500 runs.",
    "Runs research on schedule; calls lab_lookup first so repeated work is skipped; reports cost assumptions next to results.",
    "Engine, cost model, windows, canonical settings.",
    "None."),
  ...stageTable("5", "Validation", "built",
    "verdict.validate: 70/30 out-of-sample split, cost ladder, top-trade removal, five walk-forward folds, parameter sensitivity. The ML layer adds CSCV and Deflated Sharpe but is not wired.",
    "States the number of tests behind every pass, so a result from 555 screens reads differently from one from 5. Wires the Deflated Sharpe check in as a second opinion.",
    "The grade. The agent can explain it, never raise it.",
    "A person promotes a rule; the floor is MODERATE and FAILED can never be promoted."),
  ...stageTable("6", "Trade planning", "needs",
    "The cycle computes exits then entries, refuses to join trades older than one bar, and sizes equal-weight slots.",
    "A dry-run tool returns the planned orders without placing them. The agent adds a reason per trade and three checks the pipeline lacks: FTMO tradability, earnings blackout, and exposure against daily-loss room.",
    "Cycle logic, sizing, fresh-signal rule.",
    "A person approves the plan (Phase 2); only out-of-policy plans in Phase 3."),
]));
sections.push(portrait([
  H2("Stage-by-stage design — stages 7 to 10"),
  ...stageTable("7", "Risk checks", "built",
    "RiskAgent state machine; on a $100k 2-Step account CAUTION at $3,000 daily loss, RISK_OFF at $4,000, HALT at $4,500 — ahead of FTMO's $5,000 limit.",
    "Translates state and headroom into plain language and warns in the morning brief when open gap exposure could cross a threshold overnight.",
    "Every limit, every size multiplier, every HALT.",
    "A person changes limits."),
  ...stageTable("8", "Execution", "built",
    "PaperBroker fills at the completed close with slippage and commission; every order passes RiskAgent first.",
    "Nothing. Execution is triggered by the scheduler or the approval button, never by an agent tool call.",
    "All of it.",
    "Live mode needs a separate sign-off outside this system."),
  ...stageTable("9", "Monitoring", "needs",
    "Supervisor heartbeats and kill switch; EventStore records every signal, risk decision, fill and HALT.",
    "Reads the event log continuously; detects HALT, stale data, broker-mode drift and reconciliation mismatches; drafts an incident report with a timeline.",
    "HALT itself, and its refusal to clear automatically.",
    "Only a named person clears a HALT."),
  ...stageTable("10", "Reporting", "needs",
    "Reports per research run and per cycle as Markdown; a manager-report skill; the panel's P&L view.",
    "Daily P&L, weekly research memo, incidents, and a proposed-vs-executed diff that proves the agent never acted outside its plan.",
    "Numbers come from the ledger and event log.",
    "A person reviews anything sent outside the team."),
]));

// ---------------------------------------------------------------- 7 tool contract
sections.push(portrait([
  H1("7  Tool contract"),
  P("The complete list of what the agent can call. Read tools return data; Compute tools run research; Propose tools return a plan without acting; the single Write tool can only write into a reports folder."),
  table([2500, 1500, 1100, 1250, 3288], ["Tool", "Server", "Kind", "Status", "Notes"], [
    ["lab_list_strategies", "ftmo-lab", "Read", S("built"), "The 16-rule menu"],
    ["lab_run_backtest", "ftmo-lab", "Compute", S("built"), "Canonical defaults enforced by the server"],
    ["lab_run_matrix", "ftmo-lab", "Compute", S("built"), "All rules × timeframes"],
    ["lab_run_batch", "ftmo-lab", "Compute", S("built"), "Many specs in one call"],
    ["lab_validate_strategy", "ftmo-lab", "Compute", S("built"), "Returns the grade"],
    ["lab_lookup", "ftmo-lab", "Read", S("built"), "~3,500 indexed runs; call before re-running"],
    ["research_run", "ftmo-research", "Compute", S("needs"), "Wraps python -m systematic research; weekly budget"],
    ["research_ranking", "ftmo-research", "Read", S("needs"), "ranking.json with reasons and test counts"],
    ["universe_ftmo", "ftmo-research", "Read", S("needs"), "FTMO symbol list and tradability check"],
    ["paper_account / paper_trades", "ftmo-paper", "Read", S("needs"), "Ledger and P&L (logic exists in webui._pnl)"],
    ["paper_plan", "ftmo-paper", "Propose", S("needs"), "Dry-run cycle: planned orders, none placed"],
    ["risk_status", "ftmo-paper", "Read", S("needs"), "State plus headroom in dollars"],
    ["events_tail", "ftmo-paper", "Read", S("needs"), "EventStore reader since a timestamp"],
    ["quotes, calendars, news", "FMP connector", "Read", S("reuse"), "News text is untrusted data, never instructions"],
    ["report_write", "ftmo-reports", "Write", S("needs"), "The only write — into reports/ only"],
  ]),
  H2("Deliberately not exposed"),
  bullet([["Order routes ", { bold: true }], "— /api/{broker}/order and /close. No broker gateway is reachable from any agent tool."]),
  bullet([["Running the cycle ", { bold: true }], "— triggered by the scheduler or the approval button, never by the agent."]),
  bullet([["reset-halt ", { bold: true }], "— requires --by NAME --confirm from a person, by design (hard rule 4)."]),
  bullet([["Settings and risk limits ", { bold: true }], "— the agent may suggest a change in a report; it cannot write one."]),
  bullet([["Watchlist writes ", { bold: true }], "— propose-only; a person saves the change in the panel."]),
  bullet([["Shell and file edits ", { bold: true }], "— the single biggest difference from today's /agents runner."]),
]));

// ---------------------------------------------------------------- 8 examples
sections.push(landscape([
  H1("8  Worked FTMO examples"),
  png("04_proposal.png", 960, 565),
  caption("Figure 4 — One proposal through every gate, using a real case from this codebase: VZ graded MODERATE, but FTMO does not list VZ."),
]));
sections.push(portrait([
  P("Every figure below comes from this codebase's own runs or from FTMO's published specifications. None is illustrative."),
  H2("A.  A MODERATE rule the account cannot trade"),
  P("The 10-year research run on 26 Sep promoted five rules at the default MODERATE floor. Cross-checked against FTMO's US-session equity list on 30 Sep:"),
  table([1400, 2300, 1500, 4438], ["Symbol", "Rule", "Grade", "On FTMO?"], [
    ["VZ", "MEAN_REVERSION", "MODERATE", [["No", { bold: true, color: ST.hazard.ink }], " — not listed"]],
    ["IEF", "DONCHIAN_BREAKOUT", "MODERATE", [["No", { bold: true, color: ST.hazard.ink }], " — an ETF; FTMO lists single stocks"]],
    ["TLT", "MEAN_REVERSION", "MODERATE", [["No", { bold: true, color: ST.hazard.ink }], " — an ETF"]],
    ["GIS", "MEAN_REVERSION", "MODERATE", [["No", { bold: true, color: ST.hazard.ink }], " — not listed"]],
    ["NKE", "MEAN_REVERSION", "MODERATE", [["Yes", { bold: true, color: ST.built.ink }], " — Nike, Spot CFD"]],
  ]),
  callout("needs", "What the agent does", [
    "Drops four of five candidates before planning, and says why in the memo. It also recommends a code change: put the tradability check into the pipeline itself, so the grade and the market list can never disagree again. The agent finds the gap; the fix belongs in deterministic code.",
  ]),
  H2("B.  The daily-loss ladder on a $100,000 2-Step account"),
  table([1500, 1700, 3400, 3038], ["State", "Daily loss", "Agent action", "Code action"], [
    [S("built", "NORMAL"), "under $3,000", "Morning brief states headroom in dollars", "NORMAL — full size"],
    [S("needs", "CAUTION"), "$3,000", "Alerts the team; lists open exposure and its gap risk", "CAUTION — new orders at half size"],
    [S("needs", "RISK_OFF"), "$4,000", "Drafts an incident note; proposes no entries", "RISK_OFF — entries refused, exits allowed"],
    [S("hazard", "HALT"), "$4,500", "Writes the incident report; cannot clear the HALT", "HALT — flatten everything, stay halted"],
    [S("hazard", "FTMO BREACH"), "$5,000", "—", "FTMO's own limit — the account fails"],
  ]),
  P([["Thresholds from risk_agent.py (lines 6–8); dollar values assume the $100,000 initial capital in config/default.yaml. Colour marks severity.", { size: 16, italics: true, color: MUTED }]], { before: 80 }),
  H2("C.  A real HALT, and what the agent writes"),
  P("A 250-day replay of the five promoted rules halted on an FTMO daily-loss breach: equity fell to $99,414 against a floor of $99,693. Realised profit had lifted the day's starting balance to about $104,700, so the open float counted against the day's allowance."),
  table([1400, 1400, 1700, 5138], ["Position", "Side", "Closed P&L", "Note"], [
    ["NKE", "LONG 175", [["−$2,451.75", { color: ST.hazard.ink }]], "Largest contributor"],
    ["VZ", "SHORT 471", [["−$2,190.15", { color: ST.hazard.ink }]], "Short into a rally"],
    ["GIS", "LONG 249", [["−$749.49", { color: ST.hazard.ink }]], ""],
    ["IEF", "SHORT 105", [["+$101.85", { color: ST.built.ink }]], ""],
  ]),
  callout("human", "The agent's incident report — and where it stops", [
    "Cause: four positions opened across several weeks carried a combined float of about −$5,300; FTMO measures daily loss on equity, so the float counted in full.",
    "Finding: the rules hold through drawdowns deep enough to fail a challenge. Equal-weight slots of 20% of equity are too large for them.",
    "Recommendation: cut the slot size or cap total open float, then re-run the replay.",
    [["It cannot clear the HALT. ", { bold: true }], "A named person reviews the report and runs reset-halt --by NAME --confirm."],
  ]),
  H2("D.  Overnight gaps on a market that only trades by day"),
  P("FTMO's US shares trade 09:35–16:00 New York. A gap overnight cannot be traded out of and lands directly on the daily-loss limit. Measured over the year to 29 Sep 2026:"),
  table([1500, 2000, 2400, 3738], ["Symbol", "95th-pct gap", "On a $20,000 slot", "Share of the $3,000 CAUTION trigger"], [
    ["AMD", "6.14%", "$1,228", "41% from one position in one night"],
    ["AVGO", "3.65%", "$730", "24%"],
    ["AAPL", "1.43%", "$286", "10%"],
    ["MCD", "1.27%", "$254", "8%"],
  ]),
  callout("needs", "What the agent does", [
    "In the 08:30 brief it checks the earnings calendar and adds up worst-case gap exposure across open positions. If a name reports within one session, or the combined 95th-percentile gap would cross CAUTION, it proposes no new entry in that name. The cycle still decides; the agent only makes the risk visible before the market opens.",
  ]),
  H2("E.  Auditing the research itself"),
  P("The Lab's default commission, $2.50 per unit per side, is a futures figure. Applied per share it consumed $5,755 of $8,226 gross profit (70%) in a real-data replay. Correcting it raised the FRAGILE count from 3 to 20; widening the window to 2,500 bars then produced the project's first MODERATE grades."),
  callout("needs", "What the agent does", [
    "Every research memo prints cost per round trip beside average profit per trade, and trade count beside the 30-trade MODERATE gate. Either comparison would have caught this calibration error on day one.",
  ]),
]));

// ---------------------------------------------------------------- 9 safety
sections.push(portrait([
  H1("9  Safety architecture"),
  P("Each risk is specific to this codebase, and each control is either already built or scoped in the roadmap."),
  table([1800, 2700, 3638, 1500], ["Risk", "How it would happen here", "Control", "Status"], [
    [["Agent places an order", { bold: true }], "Shell → curl the hub's order route (possible today via /agents)", "Separate process; MCP-only allowlist; no broker tool; import boundary already tested", S("needs")],
    [["Prompt injection", { bold: true }], "A headline or filing says \"buy X now\"", "Market text is data; no tool executes; plans still pass every deterministic gate", S("needs")],
    [["Invented numbers", { bold: true }], "The model states a Sharpe or P&L it never computed", "Reports assembled from tool outputs; the model writes prose around them", S("needs")],
    [["P-hacking", { bold: true }], "The agent re-runs research until something passes", "Weekly research budget; the results index logs every run; memos state tests run", S("reuse")],
    [["Runaway cost", { bold: true }], "A loop burns API spend", "max_budget_usd and max_turns per run", S("built")],
    [["Stale data", { bold: true }], "Trading on a bar still forming", "Completed-bar rule at 16:15 ET; freshness check in the brief", S("built")],
    [["Broker-mode drift", { bold: true }], "Credentials switched to a live account", "Mode assertion before every run; LIVE_TRADING_ENABLED = False", S("built")],
    [["Silent failure", { bold: true }], "Health checks always report green", "Replace the three hardcoded checks (Phase 0)", S("needs")],
    [["HALT override", { bold: true }], "The agent \"fixes\" a halt to resume", "No reset tool exists; reset needs --by NAME --confirm", S("built")],
  ]),
  spacer(160),
  callout("hazard", "The one to fix first", [
    "Today, a person on this machine can open /agents, switch to bypassPermissions, and ask for anything the shell can do — including POST /api/tasty/order. The hub binds to 127.0.0.1, which keeps it off the network, but not away from its own order routes. Phase 0 closes this before any agent work starts.",
  ]),
]));

// ---------------------------------------------------------------- 10 approvals
sections.push(portrait([
  H1("10  Human approval matrix"),
  table([2400, 2600, 2600, 2038], ["Decision", "The agent may", "A person must", "Why"], [
    [["Change the watchlist", { bold: true }], "Propose with evidence", "Approve and save", "Changes the universe traded"],
    [["Run research", { bold: true }], "Run on schedule, within budget", "—", "Reversible and logged"],
    [["Promote a rule", { bold: true }], "Recommend, with grade and reasons", "Approve", "Puts capital behind a rule"],
    [["Daily cycle plan", { bold: true }], "Draft it, with a reason per trade", "Approve (Phase 2); exceptions only (Phase 3)", "First decision on the money path"],
    [["Execute an order", { bold: true }], "Never", "Never directly — the cycle executes", "Keeps proposal and execution apart"],
    [["Clear a HALT", { bold: true }], "Write the incident report", "Review, then clear with --confirm", "Hard rule 4: never auto-recover"],
    [["Change risk limits or sizing", { bold: true }], "Suggest in a report", "Approve and edit config", "Policy, not operations"],
    [["Enable live trading", { bold: true }], "Never", "Separate sign-off outside this system", "Out of scope"],
  ]),
  spacer(120),
  P("Phase 3 replaces per-plan approval with a policy envelope — maximum positions, gross exposure, per-name caps, earnings blackout — agreed once and enforced in code. Plans inside the envelope run; anything outside it waits for a person."),
]));

// ---------------------------------------------------------------- 11 roadmap
sections.push(landscape([
  H1("11  Delivery roadmap"),
  png("05_roadmap.png", 960, 470),
  caption("Figure 5 — Indicative timing. Each phase has an exit test; the next phase does not start until it passes."),
]));
sections.push(portrait([
  H2("Phases and exit tests"),
  table([1500, 3600, 3200, 1338], ["Phase", "Deliverables", "Exit test", "Effort"], [
    [[["0  Make it safe", { bold: true }]], "Lock /agents: allowed_tools, remove bypassPermissions from the page, fixed working directory. Replace the three hardcoded health checks.", "/agents cannot run Bash or reach an order route; each health check fails when its dependency is down.", "S · 2–4 days"],
    [[["1  Read-only analyst", { bold: true }]], "ftmo-research, ftmo-paper (read) and ftmo-reports MCP servers. Agent process with MCP-only tools. Morning brief and research memo.", "Ten consecutive briefs; every number traceable to a tool call; zero writes outside reports/.", "M · 1.5–2 weeks"],
    [[["2  Proposer + human gate", { bold: true }]], "paper_plan dry-run tool. Approval screen in the :8601 panel. Tradability and earnings checks moved into the pipeline.", "Twenty approved cycles; proposed and executed orders match exactly; no agent-initiated order.", "M · 2–3 weeks"],
    [[["3  Autonomous paper", { bold: true }]], "Policy envelope enforced in code; auto-run inside it; alerts outside it. Four-week forward test.", "Forward results compared with validation; no daily-loss breach; incidents handled without manual log reading.", "L · 2 weeks + 4-week test"],
    [[["4  Live", { bold: true }]], "Out of scope for this document.", "Separate sign-off, capital limits, and a new review of every control above.", "—"],
  ]),
  spacer(120),
  P([["Effort assumes one engineer part-time with Claude Code. ", { bold: true }], "Phase 3's length is fixed by the forward test, not by build effort: daily rules fire one or two times a week each, so four weeks is the minimum that says anything."]),
  H1("12  What the agent will not solve"),
  bullet([["It does not create an edge. ", { bold: true }], "The standing result is 0 ROBUST. An agent makes research faster, more honest and better documented; it cannot make a losing rule profitable."]),
  bullet([["Position sizing is the open problem. ", { bold: true }], "The replay HALT in Example C came from sizing, not rule choice. That is engineering and policy work for people."]),
  bullet([["A short forward test is weak evidence. ", { bold: true }], "Four weeks of daily rules is tens of trades — enough to catch broken plumbing, not enough to prove profitability."]),
  bullet([["Demo still enforces FTMO's rules. ", { bold: true }], "Simulated money does not mean breaches are free on a paid challenge account."]),
]));

// ---------------------------------------------------------------- appendices
sections.push(portrait([
  H1("Appendix A — Evidence"),
  P("Every finding in this report can be checked at these locations in the FTMO_MT5 source tree (inspected 30 Sep 2026)."),
  table([3600, 3700, 2338], ["Claim", "Location", "Check"], [
    ["/agents runs the Claude Agent SDK in the hub", "broker_hub.py:1316–1476", "Read the section header comment"],
    ["Default permission mode is acceptEdits", "broker_hub.py:1357", "AgentRunRequest.permissionMode"],
    ["Working directory comes from the request", "broker_hub.py:1352, 1372", "cwd field, passed straight through"],
    ["No tool allowlist on the runner", "broker_hub.py:1365–1382", "_agent_options sets no allowed_tools"],
    ["Hub binds to loopback only", "broker_hub.py:1559", "host=\"127.0.0.1\""],
    ["Live loop uses one EMA crossover rule", "ftmo_mt5/main.py:305–307", "EMACrossoverStrategy()"],
    ["ML layer is imported by nothing", "ftmo_mt5/ml/", "grep for 'ftmo_mt5.ml' outside ml/"],
    ["Meta-label gate is fail-open", "agents/signal_agent.py:122", "if meta_label_prob is not None"],
    ["RiskAgent thresholds", "agents/risk_agent.py:6–8", "2-Step: 3% / 4% / 4.5%"],
    ["Six MCP tools", "mcp_server/server.py:164–406", "lab_* functions"],
    ["Paper pipeline cannot go live", "systematic/__init__.py:20", "LIVE_TRADING_ENABLED = False"],
    ["Import boundary is enforced by a test", "tests/test_systematic.py:38", "Imports the package in a clean process"],
    ["Four of five promoted rules untradable", "systematic/state-10y/ranking.json", "Compare with ftmo.com/en/symbols"],
  ]),
]));
sections.push(portrait([
  H1("Appendix B — Glossary"),
  table([2300, PW - 2300], ["Term", "Meaning"], [
    [["MDL", { bold: true }], "Maximum Daily Loss — FTMO's limit on loss within one day, measured on equity including open positions. $5,000 on a $100,000 2-Step account."],
    [["ML (loss)", { bold: true }], "Maximum Loss — FTMO's overall limit; equity may never fall below $90,000 on a $100,000 2-Step account."],
    [["ROBUST / MODERATE / FRAGILE / FAILED", { bold: true }], "The Lab's grades. Only MODERATE and above may be promoted; FAILED never."],
    [["ATR", { bold: true }], "Average True Range — the typical size of a day's move, here as a percentage of price."],
    [["MCP", { bold: true }], "Model Context Protocol — how the agent is given tools. Its tool list is its permission model."],
    [["Claude Agent SDK", { bold: true }], "The library that runs Claude as an agent with a defined tool list, budget and permission mode."],
    [["Dry run", { bold: true }], "Computing what the cycle would do without placing any order."],
    [["Policy envelope", { bold: true }], "Limits agreed in advance (positions, exposure, blackouts) inside which a plan may run without a per-plan approval."],
  ]),
]));

// ================================================================= document
const doc = new Document({
  creator: "FTMO_MT5", title: "Independent AI Trading Agent — Integration Blueprint",
  description: "Codebase-grounded integration blueprint for an AI trading agent in FTMO_MT5",
  styles: {
    default: { document: { run: { font: FONT, size: 20, color: INK } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 34, bold: true, font: FONT, color: INK }, paragraph: { spacing: { before: 0, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 25, bold: true, font: FONT, color: INK }, paragraph: { spacing: { before: 280, after: 100 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 21, bold: true, font: FONT, color: ACCENT }, paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2 } },
    ],
  },
  numbering: { config: [
    { reference: "bul", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 440, hanging: 260 } } } }] },
    { reference: "num", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 440, hanging: 320 } } } }] },
  ] },
  sections: groups.map((children, i) => ({
    properties: { type: SectionType.NEXT_PAGE,
      page: { size: { ...A4, orientation: PageOrientation.LANDSCAPE }, margin: MARGIN } },
    footers: { default: i === 0 ? emptyFooter : footer },
    children,
  })),
});

Packer.toBuffer(doc).then(buf => { fs.writeFileSync(OUT, buf); console.log("wrote", OUT, buf.length, "bytes"); });
