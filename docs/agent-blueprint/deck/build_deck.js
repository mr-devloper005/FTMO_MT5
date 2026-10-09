// AI Trading Agent — executive briefing deck for Sir.
// Source of every number: FTMO_AI_Trading_Agent_Blueprint.docx (30 Sep 2026).
const pptxgen = require("pptxgenjs");
const React = require("react");
const RDS = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa6");

const OUT = process.argv[2] || "deck.pptx";

// ------------------------------------------------------------------ theme
const BG = "0B1220", CARD = "131D2E", CARD2 = "1A2740", LINE = "2A3A57";
const INK = "F3F5F8", SOFT = "C9D2DE", MUTED = "93A0B4", DIM = "66758E", GOLD = "E3B04B";
const ST = {
  built:  { c: "34C77B", label: "BUILT" },
  reuse:  { c: "5B9BFF", label: "REUSABLE" },
  build:  { c: "F2A93B", label: "TO BUILD" },
  human:  { c: "A98BFF", label: "YOU APPROVE" },
  hazard: { c: "F0625A", label: "FIX FIRST" },
  none:   { c: "66758E", label: "—" },
};
const TF = "Georgia", BF = "Arial";
const W = 13.333, H = 7.5, M = 0.6, CW = W - 2 * M;

// ------------------------------------------------------------------ icons
const ICONS = {
  screen: "FaMagnifyingGlassChart", market: "FaChartLine", strategy: "FaChessKnight", backtest: "FaClockRotateLeft",
  validate: "FaCircleCheck", plan: "FaClipboardList", risk: "FaScaleBalanced", execute: "FaBolt",
  monitor: "FaEye", report: "FaFileLines", user: "FaUserCheck", lock: "FaLock", warn: "FaTriangleExclamation",
  ban: "FaBan", ok: "FaCircleCheck", moon: "FaMoon", flag: "FaFlag", halt: "FaHand", gears: "FaGears",
  calendar: "FaCalendarWeek", sun: "FaSun", bell: "FaBell", shield: "FaShieldHalved",
};
const iconCache = {};
async function iconPng(key, color) {
  const id = key + color;
  if (iconCache[id]) return iconCache[id];
  const Comp = fa[ICONS[key]];
  if (!Comp) throw new Error("missing icon " + key);
  let svg = RDS.renderToStaticMarkup(React.createElement(Comp, { size: 256 }));
  svg = svg.replace(/currentColor/g, "#" + color);
  if (!/fill=/.test(svg.slice(0, 200))) svg = svg.replace("<svg", `<svg fill="#${color}"`);
  const buf = await sharp(Buffer.from(svg)).resize(256, 256, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } }).png().toBuffer();
  return (iconCache[id] = "image/png;base64," + buf.toString("base64"));
}

// ------------------------------------------------------------------ helpers
const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "FTMO_MT5";
pres.title = "AI Trading Agent for FTMO_MT5 — Briefing";

function txt(slide, text, x, y, w, h, o = {}) {
  slide.addText(text, { x, y, w, h, isTextBox: true, margin: o.margin ?? 0, fontFace: o.font || BF,
    fontSize: o.size || 14, color: o.color || INK, bold: o.bold, italic: o.italic, align: o.align || "left",
    valign: o.valign || "top", charSpacing: o.cs, lineSpacingMultiple: o.lsm, paraSpaceAfter: o.psa, fit: "none" });
}
function rrect(slide, x, y, w, h, o = {}) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: o.r ?? 0.08,
    fill: { color: o.fill || CARD, transparency: o.ft || 0 },
    line: o.noline ? { type: "none" } : { color: o.line || LINE, width: o.lw || 1, dashType: o.dash || "solid" } });
}
function chip(slide, kind, x, y, w = 1.25, h = 0.27, label) {
  const s = ST[kind];
  rrect(slide, x, y, w, h, { fill: s.c, ft: 82, line: s.c, lw: 1, r: 0.06 });
  txt(slide, label || s.label, x, y, w, h, { size: 8.5, bold: true, color: s.c, align: "center", valign: "middle", cs: 1 });
}
function chipC(slide, color, x, y, w, h, label) {
  rrect(slide, x, y, w, h, { fill: color, ft: 82, line: color, lw: 1, r: 0.06 });
  txt(slide, label, x, y, w, h, { size: 8.5, bold: true, color, align: "center", valign: "middle", cs: 1 });
}
function panel(slide, x, y, w, h, title) {
  rrect(slide, x, y, w, h, { fill: CARD });
  if (title) txt(slide, title, x + 0.3, y + 0.2, w - 0.6, 0.3, { size: 10, bold: true, color: MUTED, cs: 1.2 });
}
async function iconDot(slide, key, x, y, d, color, bg) {
  slide.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: bg || color, transparency: bg ? 0 : 82 }, line: { color, width: 1 } });
  const pad = d * 0.25;
  slide.addImage({ data: await iconPng(key, color), x: x + pad, y: y + pad, w: d - 2 * pad, h: d - 2 * pad });
}
let slideNo = 0;
function base(kicker, title, notes) {
  const s = pres.addSlide();
  s.background = { color: BG };
  slideNo += 1;
  if (kicker) txt(s, kicker.toUpperCase(), M, 0.42, 9, 0.3, { size: 10.5, bold: true, color: GOLD, cs: 2 });
  if (title) txt(s, title, M, 0.7, CW, 0.8, { font: TF, size: 29, color: INK });
  txt(s, "FTMO_MT5  ·  AI Trading Agent Briefing  ·  Paper trading only", M, 7.05, 8, 0.25, { size: 8.5, color: DIM });
  txt(s, String(slideNo), W - M - 0.6, 7.05, 0.6, 0.25, { size: 8.5, color: DIM, align: "right" });
  if (notes) s.addNotes(notes);
  return s;
}
const _unusedChartFrame = (extra = {}) => ({
  chartArea: { fill: { color: CARD }, border: { color: LINE, pt: 1 } },
  plotArea: { fill: { color: CARD } },
  catAxisLabelColor: SOFT, valAxisLabelColor: MUTED, catAxisLabelFontFace: BF, valAxisLabelFontFace: BF,
  catAxisLabelFontSize: 11, valAxisLabelFontSize: 9, valGridLine: { color: LINE, size: 0.75 },
  catGridLine: { style: "none" }, catAxisLineShow: false, valAxisLineShow: false,
  dataLabelColor: INK, dataLabelFontFace: BF, dataLabelFontSize: 11, dataLabelFontBold: true,
  titleColor: INK, titleFontFace: BF, titleFontSize: 12, legendColor: SOFT, legendFontFace: BF, legendFontSize: 10,
  ...extra,
});

// ================================================================== SLIDES
async function build() {
  // ---------------------------------------------------------------- 1 title
  {
    const s = base(null, null, "Purpose: show Sir what already exists in FTMO_MT5, what an AI agent would automate, and exactly where he stays in control. Everything here is paper trading; nothing enables live trading.");
    txt(s, "FTMO_MT5  ·  BRIEFING FOR SIR", M, 1.25, 8, 0.3, { size: 11, bold: true, color: GOLD, cs: 2 });
    txt(s, "An AI Trading Agent\nfor FTMO_MT5", M, 1.7, 7.4, 1.9, { font: TF, size: 46, color: INK, lsm: 1.0 });
    txt(s, "What is already built, what the agent automates,\nand where you approve.", M, 3.75, 7.2, 0.9, { size: 18, color: SOFT, lsm: 1.15 });
    txt(s, "30 September 2026   ·   Paper trading only — live trading stays disabled", M, 5.35, 7.4, 0.3, { size: 11.5, color: MUTED });
    txt(s, "Source: FTMO_AI_Trading_Agent_Blueprint.docx — direct inspection of the FTMO_MT5 code", M, 5.7, 7.4, 0.3, { size: 10, color: DIM });
    // legend as the cover's visual — the colour language of the whole deck
    rrect(s, 8.55, 1.25, 4.18, 4.8, { fill: CARD });
    txt(s, "HOW TO READ THIS DECK", 8.85, 1.5, 3.6, 0.3, { size: 10, bold: true, color: MUTED, cs: 1.5 });
    const rows = [["built", "Running in code today"], ["reuse", "Written, not yet wired in"], ["build", "Agent work still to build"],
                  ["human", "A person decides — never the agent"], ["hazard", "Unsafe today — fix before building"]];
    rows.forEach(([k, t], i) => {
      const y = 2.0 + i * 0.78;
      chip(s, k, 8.85, y, 1.45, 0.3);
      txt(s, t, 10.45, y - 0.02, 2.1, 0.36, { size: 11.5, color: SOFT, valign: "middle" });
    });
  }

  // ---------------------------------------------------------------- 2 bottom line
  {
    const s = base("The bottom line", "The engine is built. The agent is the missing operator.",
      "Three numbers. Half the pipeline stages already run in code. The agent will be given zero tools that can place an order. Five decisions stay with a person. The agent's job is to run the machinery on schedule, explain it, and bring decisions to you.");
    const cards = [
      ["5 / 10", "pipeline stages already built and running in code", ST.built.c, "gears"],
      ["0", "tools the agent will hold that can place an order", GOLD, "lock"],
      ["5", "decisions that stay with a person — the agent only recommends", ST.human.c, "user"],
    ];
    const cw = (CW - 2 * 0.35) / 3;
    for (let i = 0; i < 3; i++) {
      const [big, lab, col, ic] = cards[i];
      const x = M + i * (cw + 0.35);
      rrect(s, x, 1.85, cw, 3.2, { fill: CARD });
      await iconDot(s, ic, x + 0.35, 2.15, 0.62, col);
      txt(s, big, x + 0.35, 2.95, cw - 0.7, 1.05, { font: TF, size: 60, color: col });
      txt(s, lab, x + 0.35, 4.05, cw - 0.7, 0.8, { size: 14, color: SOFT, lsm: 1.1 });
    }
    rrect(s, M, 5.4, CW, 1.25, { fill: CARD2, noline: true });
    txt(s, [
      { text: "The agent ", options: { color: SOFT } },
      { text: "screens, analyses, explains and plans.  ", options: { color: INK, bold: true } },
      { text: "Tested code ", options: { color: SOFT } },
      { text: "grades, sizes and fills.  ", options: { color: INK, bold: true } },
      { text: "You ", options: { color: SOFT } },
      { text: "approve.", options: { color: ST.human.c, bold: true } },
    ], M + 0.4, 5.4, CW - 0.8, 1.25, { size: 18, valign: "middle" });
  }

  // ---------------------------------------------------------------- 3 stage board
  {
    const s = base("Stage by stage", "Where the agent works — and where you decide",
      "The core slide. Ten stages from screening to reporting. Green is already running in code. Amber is what the agent adds. Purple marks the five places a person approves. Execution is green and has no agent role at all: the agent holds no order tool.");
    const stages = [
      ["screen", "1  Stock screening", "build", "Ranks the FTMO-tradable universe weekly: liquidity, ATR, gap risk", "Approve watchlist"],
      ["market", "2  Market analysis", "build", "08:30 brief: earnings, macro, overnight gap exposure", null],
      ["strategy", "3  Strategy selection", "built", "Chooses only from 16 tested rules — never invents one", null],
      ["backtest", "4  Backtesting", "built", "Scheduled research; ~3,500 runs already indexed", null],
      ["validate", "5  Validation", "built", "Explains every grade and how many tests were run", "Promote a rule"],
      ["plan", "6  Trade planning", "build", "Dry-run plan before the cycle, one reason per trade", "Approve the plan"],
      ["risk", "7  Risk checks", "built", "RiskAgent enforces FTMO limits; agent explains headroom", "Change limits"],
      ["execute", "8  Execution", "built", "Code only. The agent holds no order tool.", "Any live mode"],
      ["monitor", "9  Monitoring", "build", "Watches the event log; drafts incident reports", "Clear a HALT"],
      ["report", "10  Reporting", "build", "Daily P&L, weekly memo, proposed vs executed", "Review outbound"],
    ];
    const gap = 0.17, tw = (CW - 4 * gap) / 5, th = 2.5;
    for (let i = 0; i < 10; i++) {
      const [ic, name, st, role, gate] = stages[i];
      const x = M + (i % 5) * (tw + gap), y = i < 5 ? 1.68 : 4.33;
      rrect(s, x, y, tw, th, { fill: CARD });
      await iconDot(s, ic, x + 0.16, y + 0.17, 0.46, ST[st].c);
      txt(s, name, x + 0.72, y + 0.17, tw - 0.82, 0.46, { size: 12, bold: true, valign: "middle" });
      chip(s, st, x + 0.16, y + 0.78, 1.12, 0.26);
      txt(s, role, x + 0.16, y + 1.14, tw - 0.32, 0.82, { size: 10.5, color: SOFT, lsm: 1.08 });
      if (gate) {
        rrect(s, x + 0.16, y + 2.0, tw - 0.32, 0.34, { fill: ST.human.c, ft: 80, line: ST.human.c, r: 0.06 });
        txt(s, "You: " + gate, x + 0.16, y + 2.0, tw - 0.32, 0.34, { size: 9.5, bold: true, color: ST.human.c, align: "center", valign: "middle" });
      } else {
        txt(s, "No approval needed", x + 0.16, y + 2.0, tw - 0.32, 0.34, { size: 9.5, color: DIM, align: "center", valign: "middle" });
      }
    }
  }

  // ---------------------------------------------------------------- 4 today vs agent
  {
    const s = base("What changes day to day", "From manual steps to a daily operating rhythm",
      "Five stages where the team's time goes today. Left is what happens by hand now; right is what the agent does instead. None of these right-hand items places a trade.");
    const rows = [
      ["screen", "Screening", "Tickers picked by judgement", "FTMO-tradable universe ranked weekly on measured liquidity, ATR and gap risk"],
      ["market", "Market analysis", "Informal reading; no written brief", "08:30 brief: earnings, macro, and overnight gap exposure vs daily-loss room"],
      ["validate", "Validation", "Read the verdict, then decide", "Every grade explained — with the number of tests behind it"],
      ["plan", "Trade planning", "Run the cycle, read results after", "Dry-run plan before anything executes, with one reason per trade"],
      ["report", "Monitor & report", "Dashboards, curl checks, reports by hand", "Event-log watch, incident drafts, daily P&L and proposed-vs-executed diff"],
    ];
    txt(s, "TODAY — BY HAND", 2.9, 1.62, 3.5, 0.3, { size: 10, bold: true, color: MUTED, cs: 1.5 });
    txt(s, "WITH THE AGENT", 7.25, 1.62, 5, 0.3, { size: 10, bold: true, color: GOLD, cs: 1.5 });
    for (let i = 0; i < rows.length; i++) {
      const [ic, name, today, agent] = rows[i];
      const y = 2.0 + i * 0.95;
      await iconDot(s, ic, M, y + 0.1, 0.55, ST.build.c);
      txt(s, name, M + 0.72, y, 1.55, 0.75, { size: 13, bold: true, valign: "middle" });
      rrect(s, 2.9, y + 0.04, 3.85, 0.68, { fill: CARD, line: LINE });
      txt(s, today, 3.05, y + 0.04, 3.6, 0.68, { size: 12, color: MUTED, valign: "middle" });
      s.addShape(pres.shapes.RIGHT_ARROW, { x: 6.83, y: y + 0.24, w: 0.34, h: 0.28, fill: { color: GOLD }, line: { type: "none" } });
      rrect(s, 7.25, y + 0.04, CW - 6.65, 0.68, { fill: CARD2, line: ST.build.c, lw: 1 });
      txt(s, agent, 7.42, y + 0.04, CW - 7.0, 0.68, { size: 12.5, color: INK, valign: "middle" });
    }
  }

  // ---------------------------------------------------------------- 5 architecture
  {
    const s = base("Architecture", "The agent proposes. Tested code decides.",
      "Five layers. You at the top. The agent is a separate process whose only tools are MCP tools that read data or propose a plan — no shell, no file edits, a dollar cap per run. Below it, the core that already exists: the grading rubric, the cycle, RiskAgent, the paper broker and the audit log. The broker is at the bottom and no agent tool reaches it.");
    const LX = M, LWd = 2.15, PX = M + LWd + 0.2, PW = CW - LWd - 0.2, pg = 0.14, pw = (PW - 4 * pg) / 5;
    const layers = [
      { y: 1.62, h: 0.78, name: "YOU", sub: "approval gates", kind: "human",
        pills: ["Promote a rule", "Approve the plan", "Clear a HALT", "Change limits", "Anything live"].map(t => [t, null, "human"]) },
      { y: 2.55, h: 1.02, name: "AI AGENT", sub: "own process · MCP tools only · no shell · $ cap", kind: "build",
        pills: [["Analyst", "brief · calendar"], ["Researcher", "run · read research"], ["Planner", "dry-run · explain"], ["Monitor", "risk state · events"], ["Reporter", "P&L · incidents"]].map(p => [...p, "build"]) },
      { y: 3.72, h: 1.02, name: "TOOLS", sub: "read or propose — never execute", kind: "none",
        pills: [["ftmo-lab", "6 tools · built", "built"], ["ftmo-research", "run · rank", "build"], ["ftmo-paper", "read · dry-run", "build"], ["market data", "FMP · reusable", "reuse"], ["ftmo-reports", "memo · incident", "build"]] },
      { y: 4.89, h: 1.02, name: "CORE", sub: "built and tested — unchanged", kind: "built",
        pills: [["verdict.validate", "the grade"], ["Systematic cycle", "exits, then entries"], ["RiskAgent", "FTMO limits · sizing"], ["PaperBroker", "fills · ledger"], ["EventStore", "append-only audit"]].map(p => [...p, "built"]) },
    ];
    for (const L of layers) {
      const col = ST[L.kind].c;
      txt(s, L.name, LX, L.y, LWd, 0.42, { size: 13, bold: true, color: L.kind === "none" ? SOFT : col, cs: 1.5, valign: "bottom" });
      txt(s, L.sub, LX, L.y + 0.42, LWd, L.h - 0.42, { size: 9.5, color: MUTED, lsm: 1.05 });
      for (let i = 0; i < 5; i++) {
        const [t, sub, k] = L.pills[i];
        const x = PX + i * (pw + pg), c = ST[k].c;
        rrect(s, x, L.y, pw, L.h, { fill: c, ft: 86, line: c, lw: 1 });
        if (sub) {
          txt(s, t, x, L.y + 0.12, pw, 0.4, { size: 12, bold: true, align: "center", valign: "middle" });
          txt(s, sub, x, L.y + 0.52, pw, 0.36, { size: 9.5, color: SOFT, align: "center", valign: "middle" });
        } else {
          txt(s, t, x, L.y, pw, L.h, { size: 11.5, bold: true, align: "center", valign: "middle", color: INK });
        }
      }
    }
    rrect(s, PX, 6.06, PW, 0.52, { fill: BG, line: DIM, dash: "dash" });
    txt(s, "Reached only by the approved cycle.  No agent tool reaches it.", PX, 6.06, PW, 0.52, { size: 11, color: SOFT, align: "center", valign: "middle" });
    txt(s, "BROKER", LX, 6.06, LWd, 0.52, { size: 13, bold: true, color: DIM, cs: 1.5, valign: "middle" });
  }

  // ---------------------------------------------------------------- 6 research funnel
  {
    const s = base("What research has found", "555 screens → 1 rule you could trade at FTMO",
      "The honest state of the research. The latest run screened 37 stocks against 15 rules — 555 backtests. 81 went to full validation; 7 graded MODERATE; 5 were promoted; and only one of those, NKE, is actually listed at FTMO. Zero rules grade ROBUST. Seven out of 555 is not clearly better than chance, so these need forward confirmation. This is why the agent's first job is honest reporting.");
    panel(s, M, 1.65, 7.6, 5.0, "RULES AT EACH STAGE — LATEST RUN, 37 STOCKS × 15 RULES");
    const funnel = [["Screened", 555, "6E6246"], ["Fully validated", 81, "8F7A47"], ["Graded MODERATE", 7, "B89445"], ["Promoted", 5, "CFA044"], ["Tradable at FTMO", 1, GOLD]];
    const fx = M + 2.45, fw = 3.9;
    funnel.forEach(([lab, v, c], i) => {
      const y = 2.4 + i * 0.8, bw = Math.max(0.08, fw * v / 555), last = i === funnel.length - 1;
      txt(s, lab, M + 0.3, y, 2.05, 0.5, { size: 12.5, color: last ? GOLD : SOFT, bold: last, valign: "middle" });
      rrect(s, fx, y + 0.05, bw, 0.4, { fill: c, noline: true, r: 0.03 });
      txt(s, v.toLocaleString(), fx + bw + 0.14, y, 1.0, 0.5, { font: TF, size: 20, color: last ? GOLD : INK, valign: "middle" });
    });
    rrect(s, 8.55, 1.65, CW - 7.95, 5.0, { fill: CARD });
    txt(s, "WHAT IT MEANS", 8.85, 1.9, 3.6, 0.3, { size: 10, bold: true, color: MUTED, cs: 1.5 });
    const pts = [
      ["0 ROBUST.", " No rule has cleared the strict bar yet."],
      ["7 of 555", " is not clearly better than chance — forward confirmation needed."],
      ["Only NKE", " survives to an FTMO-tradable rule."],
      ["Agent's first job:", " report this honestly, and state how many tests sit behind every pass."],
    ];
    txt(s, pts.flatMap(([b, t], i) => [
      { text: b, options: { bold: true, color: i === 3 ? GOLD : INK, breakLine: false } },
      { text: t, options: { color: SOFT, breakLine: i < pts.length - 1 } },
    ]), 8.85, 2.35, 3.6, 4.1, { size: 13.5, psa: 14, lsm: 1.1 });
  }

  // ---------------------------------------------------------------- 7 calibration
  {
    const s = base("Research quality", "Two calibration fixes changed the verdicts",
      "Same 37 stocks, three runs. The Lab's default commission was a futures figure — $2.50 per contract — charged per share; it ate 70% of gross profit in a replay. Fixing it lifted FRAGILE from 3 to 20. MODERATE needs 30 trades, which daily rules can't reach in 600 bars; a 2,500-bar window produced the first 7 MODERATE grades. The agent's job: audit these assumptions in every research memo so this is caught on day one.");
    panel(s, M, 1.65, 7.2, 5.0, "VALIDATED CANDIDATES BY GRADE — SAME 37 STOCKS, THREE RUNS");
    const runs = [["Default cost", "600 bars", [102, 3, 0]], ["Stock cost", "600 bars", [66, 20, 0]], ["Stock cost", "2,500 bars", [45, 29, 7]]];
    const segC = ["4A5873", ST.build.c, ST.built.c], segN = ["FAILED", "FRAGILE", "MODERATE"];
    const baseY = 5.5, plotH = 3.0, maxV = 110, colW = 1.3, colGap = 0.85, cx0 = M + 0.8;
    s.addShape(pres.shapes.LINE, { x: cx0 - 0.25, y: baseY, w: 3 * colW + 2 * colGap + 0.5, h: 0, line: { color: LINE, width: 1 } });
    runs.forEach(([l1, l2, vals], i) => {
      const x = cx0 + i * (colW + colGap); let y = baseY;
      vals.forEach((v, k) => {
        if (!v) return;
        const h = plotH * v / maxV; y -= h;
        s.addShape(pres.shapes.RECTANGLE, { x, y, w: colW, h, fill: { color: segC[k] }, line: { color: CARD, width: 1 } });
        if (h >= 0.3) txt(s, String(v), x, y, colW, h, { size: 14, bold: true, color: k === 0 ? INK : BG, align: "center", valign: "middle" });
        else txt(s, v + " " + segN[k], x - 0.35, y - 0.34, colW + 0.7, 0.3, { size: 11, bold: true, color: segC[k], align: "center" });
      });
      txt(s, l1, x - 0.25, baseY + 0.1, colW + 0.5, 0.28, { size: 12, bold: true, align: "center" });
      txt(s, l2, x - 0.25, baseY + 0.38, colW + 0.5, 0.28, { size: 10.5, color: MUTED, align: "center" });
    });
    segN.forEach((n, k) => {
      const x = M + 0.8 + k * 1.75;
      s.addShape(pres.shapes.RECTANGLE, { x, y: 6.22, w: 0.2, h: 0.2, fill: { color: segC[k] }, line: { type: "none" } });
      txt(s, n, x + 0.3, 6.15, 1.4, 0.34, { size: 10.5, color: SOFT, valign: "middle" });
    });
    const cards = [
      [ST.hazard.c, "70%", "of gross profit eaten by a per-contract fee charged per share", "$5,755 of $8,226 in a real-data replay"],
      [ST.build.c, "30 trades", "needed for MODERATE — daily rules can't reach it in 600 bars", "a 2,500-bar window gave the first 7"],
    ];
    cards.forEach(([c, big, t1, t2], i) => {
      const y = 1.65 + i * 2.55, x = 8.15;
      rrect(s, x, y, CW - 7.55, 2.35, { fill: CARD });
      txt(s, big, x + 0.3, y + 0.25, 4, 0.8, { font: TF, size: 36, color: c });
      txt(s, t1, x + 0.3, y + 1.08, CW - 8.15, 0.6, { size: 12.5, color: INK, lsm: 1.08 });
      txt(s, t2, x + 0.3, y + 1.7, CW - 8.15, 0.4, { size: 10.5, color: MUTED });
    });
  }

  // ---------------------------------------------------------------- 8 tradability
  {
    const s = base("FTMO example", "4 of the 5 promoted rules can't be traded at FTMO",
      "A real case. The pipeline promoted five rules at MODERATE. Cross-checked against FTMO's own symbol list on 30 September, only Nike is listed. VZ and GIS aren't offered; IEF and TLT are ETFs, and FTMO lists single stocks. The pipeline has no tradability check. The agent catches this before planning and explains it; the permanent fix belongs in code, so the grade and the market list can never disagree again.");
    const t = [["VZ", "Verizon", "MEAN_REVERSION", false, "Not listed"], ["IEF", "7–10y Treasury ETF", "DONCHIAN_BREAKOUT", false, "ETF — FTMO lists single stocks"],
               ["TLT", "20y+ Treasury ETF", "MEAN_REVERSION", false, "ETF — FTMO lists single stocks"], ["GIS", "General Mills", "MEAN_REVERSION", false, "Not listed"],
               ["NKE", "Nike", "MEAN_REVERSION", true, "Listed · Spot CFD"]];
    const gap = 0.25, tw = (CW - 4 * gap) / 5;
    for (let i = 0; i < 5; i++) {
      const [sym, co, rule, ok, why] = t[i];
      const x = M + i * (tw + gap), c = ok ? ST.built.c : ST.hazard.c;
      rrect(s, x, 1.75, tw, 2.95, { fill: CARD, line: c, lw: 1.25 });
      await iconDot(s, ok ? "ok" : "ban", x + tw - 0.72, 1.95, 0.5, c);
      txt(s, sym, x + 0.25, 1.92, tw - 1, 0.7, { font: TF, size: 34, color: INK });
      txt(s, co, x + 0.25, 2.62, tw - 0.4, 0.3, { size: 11, color: MUTED });
      chip(s, "none", x + 0.25, 3.08, 1.25, 0.26, "MODERATE");
      txt(s, rule, x + 0.25, 3.42, tw - 0.4, 0.3, { size: 10, color: SOFT });
      txt(s, why, x + 0.25, 3.82, tw - 0.4, 0.7, { size: 12, bold: true, color: c, lsm: 1.05 });
    }
    const boxes = [[ST.build.c, "WHAT THE AGENT DOES", "Drops the four before planning and says why in the research memo."],
                   [ST.built.c, "WHAT THE CODE SHOULD DO", "Add a tradability check to the pipeline, so a grade and FTMO's list can never disagree."]];
    boxes.forEach(([c, h, b], i) => {
      const x = M + i * (CW / 2 + 0.12), w = CW / 2 - 0.12;
      rrect(s, x, 5.0, w, 1.6, { fill: CARD2, noline: true });
      txt(s, h, x + 0.3, 5.2, w - 0.6, 0.3, { size: 10, bold: true, color: c, cs: 1.5 });
      txt(s, b, x + 0.3, 5.55, w - 0.6, 0.9, { size: 14, color: INK, lsm: 1.1 });
    });
  }

  // ---------------------------------------------------------------- 9 risk ladder
  {
    const s = base("FTMO risk control", "The daily-loss ladder on a $100,000 2-Step account",
      "RiskAgent is already built and runs ahead of FTMO's own limit. At $3,000 of daily loss new orders go to half size; at $4,000 entries are refused; at $4,500 everything is flattened and trading stops until a named person clears it. FTMO's hard limit is $5,000. The agent never overrides this; its job is to warn before a threshold is reached and to write the incident report.");
    const steps = [["CAUTION", 3000, ST.build.c, "New orders at half size", "Alerts the team; lists open exposure"],
                   ["RISK_OFF", 4000, "F08A4B", "Entries refused, exits allowed", "Drafts incident note; proposes no entries"],
                   ["HALT", 4500, ST.hazard.c, "Flatten everything, stay halted", "Writes the report — cannot clear it"],
                   ["FTMO BREACH", 5000, "D64B41", "Account fails — FTMO's own rule", "—"]];
    const base0 = 6.55, maxH = 4.0, bw = 1.55, bg = 0.3, x0 = M + 0.1;
    s.addShape(pres.shapes.LINE, { x: M, y: base0, w: 4 * (bw + bg) + 0.1, h: 0, line: { color: LINE, width: 1 } });
    steps.forEach(([name, amt, c], i) => {
      const h = maxH * amt / 5000, x = x0 + i * (bw + bg), y = base0 - h;
      rrect(s, x, y, bw, h, { fill: c, ft: 18, noline: true, r: 0.04 });
      txt(s, "$" + amt.toLocaleString(), x, y - 0.72, bw, 0.4, { font: TF, size: 20, color: INK, align: "center" });
      txt(s, name, x, y - 0.34, bw, 0.3, { size: 10.5, bold: true, color: c, align: "center", cs: 1 });
    });
    txt(s, "Daily loss that triggers each state", M, base0 + 0.08, 7.5, 0.3, { size: 10, color: MUTED });
    const RX = 8.25, RW = CW - (RX - M);
    rrect(s, RX, 1.65, RW, 5.0, { fill: CARD });
    txt(s, "CODE ACTS  ·  AGENT EXPLAINS", RX + 0.3, 1.88, RW - 0.6, 0.3, { size: 10, bold: true, color: MUTED, cs: 1.5 });
    steps.forEach(([name, , c, code, agent], i) => {
      const y = 2.35 + i * 1.05;
      chipC(s, c, RX + 0.3, y, 1.45, 0.28, name);
      txt(s, [{ text: "Code: ", options: { bold: true, color: ST.built.c } }, { text: code, options: { color: INK } }], RX + 0.3, y + 0.35, RW - 0.6, 0.3, { size: 11.5 });
      txt(s, [{ text: "Agent: ", options: { bold: true, color: ST.build.c } }, { text: agent, options: { color: SOFT } }], RX + 0.3, y + 0.64, RW - 0.6, 0.3, { size: 11.5 });
    });
  }

  // ---------------------------------------------------------------- 10 gap risk
  {
    const s = base("FTMO market structure", "Overnight gaps land straight on the daily-loss limit",
      "FTMO's US shares trade only 09:35 to 16:00 New York, so an overnight gap can't be traded out of. Measured over the last year, AMD's 95th-percentile overnight gap is 6.1%: on a $20,000 position that is $1,228 in one night — 41% of the way to the $3,000 CAUTION trigger from a single name. The agent's morning brief adds up this worst-case exposure across open positions and flags earnings.");
    panel(s, M, 1.65, 7.6, 5.0, "WORST-CASE (95TH-PCT) OVERNIGHT LOSS · $20,000 POSITION · % OF CAUTION");
    const gaps = [["AMD", "6.14% gap", 1228], ["AVGO", "3.65% gap", 730], ["AAPL", "1.43% gap", 286], ["MCD", "1.27% gap", 254]];
    const gx = M + 1.75, gw = 5.25;
    gaps.forEach(([sym, g, v], i) => {
      const y = 2.35 + i * 0.93, bw = gw * v / 3000;
      txt(s, sym, M + 0.3, y, 1.35, 0.36, { font: TF, size: 18, valign: "middle" });
      txt(s, g, M + 0.3, y + 0.37, 1.4, 0.26, { size: 10, color: MUTED });
      rrect(s, gx, y + 0.08, bw, 0.46, { fill: GOLD, noline: true, r: 0.03 });
      txt(s, "$" + v.toLocaleString() + "   " + Math.round(v / 30) + "%", gx + bw + 0.14, y + 0.08, 2.0, 0.46, { size: 13, bold: true, valign: "middle" });
    });
    s.addShape(pres.shapes.LINE, { x: gx + gw, y: 2.25, w: 0, h: 3.75, line: { color: ST.build.c, width: 1.5, dashType: "dash" } });
    txt(s, "CAUTION trigger · $3,000 daily loss", gx + gw - 3.2, 6.08, 3.2, 0.3, { size: 10.5, bold: true, color: ST.build.c, align: "right" });
    const RX = 8.55, RW = CW - (RX - M);
    rrect(s, RX, 1.65, RW, 2.2, { fill: CARD });
    await iconDot(s, "moon", RX + 0.3, 1.9, 0.5, GOLD);
    txt(s, "09:35 – 16:00 ET", RX + 0.95, 1.9, RW - 1.2, 0.5, { font: TF, size: 20, color: INK, valign: "middle" });
    txt(s, "FTMO US share CFDs trade 16:35–23:00 GMT+3 only. A gap overnight can't be traded out of.", RX + 0.3, 2.55, RW - 0.6, 1.1, { size: 12, color: SOFT, lsm: 1.1 });
    rrect(s, RX, 4.05, RW, 2.6, { fill: CARD2, line: ST.build.c });
    txt(s, "AGENT AT 08:30", RX + 0.3, 4.25, RW - 0.6, 0.3, { size: 10, bold: true, color: ST.build.c, cs: 1.5 });
    txt(s, "Adds up worst-case gap exposure across open positions and checks earnings. If a name reports within one session, or the total would cross CAUTION, it proposes no new entry there.",
      RX + 0.3, 4.6, RW - 0.6, 1.9, { size: 12.5, color: INK, lsm: 1.12 });
  }

  // ---------------------------------------------------------------- 11 real HALT
  {
    const s = base("FTMO example", "A real HALT — what the agent writes, and where it stops",
      "From a 250-day replay of the promoted rules. Realised profit had lifted the day's starting balance to about $104,700; four open positions carried roughly minus $5,300 of float; equity fell to $99,414 against a daily floor of $99,693, and RiskAgent halted and flattened everything. The finding is about sizing, not rule choice: 20% slots are too large for rules that sit through drawdowns this deep. The agent writes the report; a named person clears the halt.");
    const stats = [["$99,414", "equity at the breach"], ["$99,693", "daily-loss floor"], ["≈ −$5,300", "open float, 4 positions"]];
    stats.forEach(([v, l], i) => {
      const x = M + i * 2.55;
      rrect(s, x, 1.65, 2.4, 1.05, { fill: CARD });
      txt(s, v, x + 0.2, 1.72, 2.1, 0.55, { font: TF, size: 22, color: i === 2 ? ST.hazard.c : INK });
      txt(s, l, x + 0.2, 2.27, 2.1, 0.3, { size: 10, color: MUTED });
    });
    panel(s, M, 2.9, 7.45, 3.75, "CLOSED P&L WHEN THE HALT FLATTENED THE BOOK");
    const pnl = [["NKE", "long 175", -2451.75], ["VZ", "short 471", -2190.15], ["GIS", "long 249", -749.49], ["IEF", "short 105", 101.85]];
    const px = M + 1.75, pw = 5.3, lo = -2600, hi = 400, k = pw / (hi - lo), axX = px - lo * k;
    s.addShape(pres.shapes.LINE, { x: axX, y: 3.35, w: 0, h: 3.0, line: { color: MUTED, width: 1 } });
    pnl.forEach(([sym, side, v], i) => {
      const y = 3.45 + i * 0.72, bw = Math.abs(v) * k, neg = v < 0, x = neg ? axX - bw : axX;
      const c = neg ? ST.hazard.c : ST.built.c, lab = (neg ? "−$" : "+$") + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
      txt(s, sym, M + 0.3, y, 0.7, 0.44, { font: TF, size: 16, valign: "middle" });
      txt(s, side, M + 0.95, y, 0.8, 0.44, { size: 10, color: MUTED, valign: "middle" });
      rrect(s, x, y + 0.02, bw, 0.4, { fill: c, noline: true, r: 0.03 });
      if (neg && bw >= 1.5) txt(s, lab, x + 0.12, y + 0.02, 1.4, 0.4, { size: 12, bold: true, color: BG, valign: "middle" });
      else if (neg) txt(s, lab, x - 1.2, y + 0.02, 1.1, 0.4, { size: 12, bold: true, color: c, align: "right", valign: "middle" });
      else txt(s, lab, x + bw + 0.1, y + 0.02, 1.0, 0.4, { size: 12, bold: true, color: c, valign: "middle" });
    });
    const RX = 8.35, RW = CW - (RX - M);
    rrect(s, RX, 1.65, RW, 5.0, { fill: CARD2, line: ST.human.c });
    txt(s, "THE AGENT'S INCIDENT REPORT", RX + 0.3, 1.88, RW - 0.6, 0.3, { size: 10, bold: true, color: ST.human.c, cs: 1.5 });
    const inc = [["Cause", "Float from four positions counted in full against the day's allowance."],
                 ["Finding", "The rules hold through drawdowns deep enough to fail a challenge."],
                 ["Fix", "Smaller slots or a cap on total open float — then re-run."]];
    inc.forEach(([h, b], i) => {
      const y = 2.35 + i * 1.02;
      txt(s, h.toUpperCase(), RX + 0.3, y, RW - 0.6, 0.26, { size: 9.5, bold: true, color: MUTED, cs: 1 });
      txt(s, b, RX + 0.3, y + 0.27, RW - 0.6, 0.68, { size: 12, color: INK, lsm: 1.08 });
    });
    rrect(s, RX + 0.3, 5.55, RW - 0.6, 0.85, { fill: ST.human.c, ft: 80, line: ST.human.c });
    txt(s, "It cannot clear the HALT. A named person reviews and clears it.", RX + 0.45, 5.55, RW - 0.9, 0.85, { size: 12, bold: true, color: INK, valign: "middle" });
  }

  // ---------------------------------------------------------------- 12 rhythm
  {
    const s = base("Operating rhythm", "A day with the agent (New York time)",
      "Every agent decision point sits outside the trading session. Sunday: research and a memo; you promote rules. 08:30: the morning brief. During the session the agent watches and decides nothing. 16:15, after the close is published: a dry-run plan, your approval, then the cycle executes. End of day: the P&L report. Always on: if a HALT fires, it writes the incident and a person clears it.");
    const ax = 2.95, x1 = M + 0.3, x2 = W - M - 0.3;
    s.addShape(pres.shapes.LINE, { x: x1, y: ax, w: x2 - x1, h: 0, line: { color: LINE, width: 2 } });
    const nodes = [
      { x: 1.55, t: "SUNDAY", h: "Research + memo", b: "Screen, validate, rank. Memo explains grades and test count.", g: "You promote rules", k: "human", ic: "calendar" },
      { x: 4.1, t: "08:30", h: "Morning brief", b: "Mode and data check, earnings, macro, gap exposure.", g: "Information only", k: "none", ic: "sun" },
      { x: 6.65, t: "09:35 – 16:00", h: "Market session", b: "Agent watches the event log. Decides nothing.", g: "No actions", k: "none", ic: "monitor" },
      { x: 9.2, t: "16:15", h: "Plan → approve → run", b: "Dry-run plan with reasons. RiskAgent re-checks at execution.", g: "You approve the plan", k: "human", ic: "plan" },
      { x: 11.75, t: "END OF DAY", h: "P&L report", b: "Realised, unrealised, risk state, proposed vs executed.", g: "Review outbound", k: "human", ic: "report" },
    ];
    const cw = 2.3;
    for (const n of nodes) {
      await iconDot(s, n.ic, n.x - 0.3, ax - 0.3, 0.6, n.k === "human" ? ST.human.c : GOLD, BG);
      txt(s, n.t, n.x - cw / 2, 1.85, cw, 0.35, { size: 11, bold: true, color: GOLD, align: "center", cs: 1 });
      rrect(s, n.x - cw / 2, 3.45, cw, 2.15, { fill: CARD });
      txt(s, n.h, n.x - cw / 2 + 0.18, 3.6, cw - 0.36, 0.4, { size: 13, bold: true });
      txt(s, n.b, n.x - cw / 2 + 0.18, 4.02, cw - 0.36, 1.0, { size: 10.5, color: SOFT, lsm: 1.08 });
      if (n.k === "human") chip(s, "human", n.x - cw / 2 + 0.18, 5.12, cw - 0.36, 0.3, n.g.toUpperCase());
      else txt(s, n.g, n.x - cw / 2 + 0.18, 5.12, cw - 0.36, 0.3, { size: 9.5, color: DIM, valign: "middle" });
    }
    rrect(s, M, 5.85, CW, 0.75, { fill: ST.hazard.c, ft: 88, line: ST.hazard.c });
    await iconDot(s, "halt", M + 0.25, 5.97, 0.5, ST.hazard.c, BG);
    txt(s, [{ text: "ALWAYS ON   ", options: { bold: true, color: ST.hazard.c } },
            { text: "HALT fires → RiskAgent flattens → agent writes the incident report → ", options: { color: INK } },
            { text: "a named person clears it", options: { bold: true, color: ST.human.c } }],
      M + 0.95, 5.85, CW - 1.2, 0.75, { size: 12.5, valign: "middle" });
  }

  // ---------------------------------------------------------------- 13 approvals
  {
    const s = base("Control", "Five decisions stay with you",
      "The agent recommends with evidence; a person decides. Five gates: promoting a rule, approving the daily plan, clearing a halt, changing risk limits, and anything touching live trading. Placing an order is never an agent action — the cycle executes only after the gate. In Phase 3 the per-plan approval becomes a policy envelope you agree once; anything outside it still waits for you.");
    const g = [["validate", "Promote a rule", "Recommends, with grade and reasons", "Puts capital behind a rule"],
               ["plan", "Approve the daily plan", "Drafts it, one reason per trade", "First decision on the money path"],
               ["halt", "Clear a HALT", "Writes the incident report", "Never auto-recovers — by design"],
               ["risk", "Change risk limits", "Suggests in a report", "Policy, not operations"],
               ["lock", "Anything live", "Never", "Separate sign-off outside this system"]];
    const gap = 0.2, cw = (CW - 4 * gap) / 5;
    for (let i = 0; i < 5; i++) {
      const [ic, t, agent, why] = g[i];
      const x = M + i * (cw + gap);
      rrect(s, x, 1.7, cw, 3.85, { fill: CARD, line: ST.human.c, lw: 1 });
      await iconDot(s, ic, x + 0.25, 1.95, 0.6, ST.human.c);
      txt(s, t, x + 0.25, 2.72, cw - 0.5, 0.75, { size: 15, bold: true, lsm: 1.05 });
      txt(s, "AGENT", x + 0.25, 3.55, cw - 0.5, 0.25, { size: 9, bold: true, color: ST.build.c, cs: 1 });
      txt(s, agent, x + 0.25, 3.8, cw - 0.5, 0.55, { size: 11, color: SOFT, lsm: 1.05 });
      txt(s, "WHY YOU", x + 0.25, 4.45, cw - 0.5, 0.25, { size: 9, bold: true, color: ST.human.c, cs: 1 });
      txt(s, why, x + 0.25, 4.7, cw - 0.5, 0.7, { size: 11, color: SOFT, lsm: 1.05 });
    }
    rrect(s, M, 5.85, CW, 0.75, { fill: CARD2, noline: true });
    txt(s, [{ text: "Placing an order is never an agent action. ", options: { bold: true, color: INK } },
            { text: "The cycle executes — only after your gate, and RiskAgent re-checks at that moment.", options: { color: SOFT } }],
      M + 0.35, 5.85, CW - 0.7, 0.75, { size: 13.5, valign: "middle" });
  }

  // ---------------------------------------------------------------- 14 hazard
  {
    const s = base("Fix first", "One thing to fix before building anything",
      "An AI agent already runs inside the trading hub at /agents. It is a general-purpose developer tool: it can use the shell and edit files, defaults to accepting edits, and a fully-open permission mode can be selected from the page — all in the same process that holds the order routes. With a shell it can call the order endpoint directly. The hub is only reachable from this machine, which helps, but doesn't separate it from its own order routes. Phase 0 closes this in two to four days.");
    rrect(s, M, 1.65, 7.3, 5.0, { fill: CARD, line: LINE });
    txt(s, "broker_hub.py  ·  one process  ·  127.0.0.1:8600", M + 0.3, 1.85, 6.8, 0.35, { size: 11.5, bold: true, color: MUTED });
    rrect(s, M + 0.35, 2.55, 2.9, 1.7, { fill: CARD2, line: ST.built.c });
    txt(s, "Order routes", M + 0.55, 2.7, 2.5, 0.4, { size: 14, bold: true });
    txt(s, "POST /api/{broker}/order\nPOST /api/{broker}/close", M + 0.55, 3.15, 2.6, 0.8, { size: 11, color: SOFT, font: "Courier New" });
    rrect(s, M + 4.05, 2.55, 2.9, 1.7, { fill: ST.hazard.c, ft: 85, line: ST.hazard.c, lw: 1.5 });
    await iconDot(s, "warn", M + 6.3, 2.7, 0.48, ST.hazard.c, BG);
    txt(s, "/agents runner", M + 4.25, 2.7, 2.1, 0.4, { size: 14, bold: true });
    txt(s, "Claude Agent SDK with shell + file edits", M + 4.25, 3.15, 2.55, 0.8, { size: 11, color: SOFT, lsm: 1.08 });
    s.addShape(pres.shapes.LINE, { x: M + 3.3, y: 3.4, w: 0.7, h: 0, line: { color: ST.hazard.c, width: 2, dashType: "dash", beginArrowType: "triangle" } });
    txt(s, "curl", M + 3.3, 3.02, 0.7, 0.3, { size: 10, bold: true, color: ST.hazard.c, align: "center" });
    const facts = ["acceptEdits by default", "bypass mode selectable on the page", "no tool allowlist", "working directory set by the request"];
    facts.forEach((f, i) => {
      const x = M + 0.35 + (i % 2) * 3.45, y = 4.6 + Math.floor(i / 2) * 0.6;
      chip(s, "hazard", x, y, 3.25, 0.42, f.toUpperCase());
    });
    txt(s, "Source: broker_hub.py lines 1316–1476", M + 0.35, 6.0, 6.5, 0.3, { size: 9.5, color: DIM });
    const RX = 8.25, RW = CW - (RX - M);
    rrect(s, RX, 1.65, RW, 5.0, { fill: CARD2, line: ST.hazard.c });
    txt(s, "PHASE 0  ·  2–4 DAYS", RX + 0.3, 1.88, RW - 0.6, 0.3, { size: 10, bold: true, color: ST.hazard.c, cs: 1.5 });
    const fixes = ["Tool allowlist on the runner", "Remove the bypass option from the page", "Fix the working directory in code", "Replace 3 health checks that always say healthy"];
    txt(s, fixes.map((f, i) => ({ text: f, options: { bullet: true, breakLine: i < fixes.length - 1 } })), RX + 0.3, 2.35, RW - 0.6, 2.6, { size: 13, color: INK, psa: 10 });
    txt(s, "EXIT TEST", RX + 0.3, 5.1, RW - 0.6, 0.26, { size: 9.5, bold: true, color: MUTED, cs: 1 });
    txt(s, "/agents cannot run a shell or reach an order route; each health check fails when it should.", RX + 0.3, 5.38, RW - 0.6, 1.1, { size: 11.5, color: SOFT, lsm: 1.08 });
  }

  // ---------------------------------------------------------------- 15 roadmap
  {
    const s = base("Roadmap", "Five phases — each must pass its exit test",
      "Indicative timing for one engineer part-time with Claude Code. Phase 0 makes it safe. Phase 1 is a read-only analyst: briefs and research memos. Phase 2 adds dry-run plans and your approval in the panel. Phase 3 runs inside a policy envelope with a four-week forward test — its length is set by the test, not the build. Live trading is out of scope and needs a separate sign-off.");
    const ph = [["0", "Make it safe", "Wk 0–2", "Lock /agents · real health checks", "Runner can't reach an order route", "hazard"],
                ["1", "Read-only analyst", "Wk 2–5", "Research, paper & report tools · daily brief", "10 briefs, every number traced to a tool", "build"],
                ["2", "Proposer + you", "Wk 5–9", "Dry-run plans · approval in the panel", "20 approved cycles; plan = execution", "human"],
                ["3", "Autonomous paper", "Wk 9–15", "Policy envelope · 4-week forward test", "No daily-loss breach; results vs validation", "build"],
                ["4", "Live", "Out of scope", "Separate sign-off · capital limits", "New review of every control", "none"]];
    const gap = 0.16, cw = (CW - 4 * gap) / 5;
    for (let i = 0; i < 5; i++) {
      const [n, t, wk, d, exit, k] = ph[i];
      const x = M + i * (cw + gap), c = ST[k].c;
      s.addShape(pres.shapes.CHEVRON, { x, y: 1.7, w: cw + 0.1, h: 0.75, fill: { color: c, transparency: k === "none" ? 70 : 20 }, line: { type: "none" } });
      txt(s, "PHASE " + n, x + 0.5, 1.7, cw - 0.6, 0.75, { size: 13, bold: true, color: k === "none" ? SOFT : BG, valign: "middle", cs: 1 });
      rrect(s, x, 2.65, cw, 3.95, { fill: CARD, line: k === "none" ? LINE : c, dash: k === "none" ? "dash" : "solid" });
      txt(s, t, x + 0.2, 2.82, cw - 0.4, 0.7, { size: 15, bold: true, lsm: 1.05 });
      txt(s, wk, x + 0.2, 3.5, cw - 0.4, 0.3, { size: 11, bold: true, color: GOLD });
      txt(s, d, x + 0.2, 3.9, cw - 0.4, 0.95, { size: 11, color: SOFT, lsm: 1.08 });
      txt(s, "EXIT TEST", x + 0.2, 4.95, cw - 0.4, 0.25, { size: 9, bold: true, color: MUTED, cs: 1 });
      txt(s, exit, x + 0.2, 5.22, cw - 0.4, 1.2, { size: 11, color: INK, lsm: 1.08 });
    }
  }

  // ---------------------------------------------------------------- 16 ask
  {
    const s = base(null, null, "Close. The decision needed today is small and reversible: approve Phase 0, the safety fix, and Phase 1, the read-only analyst. Be clear about what the agent won't do: it doesn't create an edge — nothing grades ROBUST yet — it doesn't solve position sizing, which is what caused the replay halt, and four weeks of paper results will catch broken plumbing but won't prove profitability.");
    txt(s, "THE ASK", M, 0.9, 6, 0.3, { size: 11, bold: true, color: GOLD, cs: 2 });
    txt(s, "Approve Phase 0\nand Phase 1", M, 1.3, 6.2, 1.8, { font: TF, size: 42, color: INK, lsm: 1.0 });
    const asks = [["0", "Make it safe", "Lock /agents · real health checks · 2–4 days", ST.hazard.c],
                  ["1", "Read-only analyst", "Briefs and research memos — no trading · 1.5–2 weeks", ST.build.c]];
    asks.forEach(([n, t, d, c], i) => {
      const y = 3.45 + i * 1.3;
      rrect(s, M, y, 6.3, 1.1, { fill: CARD, line: c });
      txt(s, n, M + 0.25, y, 0.7, 1.1, { font: TF, size: 36, color: c, valign: "middle" });
      txt(s, t, M + 1.05, y + 0.17, 5, 0.4, { size: 16, bold: true });
      txt(s, d, M + 1.05, y + 0.58, 5.1, 0.4, { size: 11.5, color: SOFT });
    });
    const RX = 7.45, RW = W - M - RX;
    rrect(s, RX, 0.9, RW, 5.75, { fill: CARD2, noline: true });
    txt(s, "WHAT THE AGENT WON'T DO", RX + 0.35, 1.15, RW - 0.7, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const wont = [["Create an edge.", "Nothing grades ROBUST yet. The agent makes research faster and more honest, not profitable."],
                  ["Fix position sizing.", "The replay HALT came from sizing, not rule choice — that's policy work for people."],
                  ["Prove profit in 4 weeks.", "A short paper test catches broken plumbing, not skill."]];
    wont.forEach(([h, b], i) => {
      const y = 1.7 + i * 1.5;
      txt(s, h, RX + 0.35, y, RW - 0.7, 0.4, { size: 15, bold: true });
      txt(s, b, RX + 0.35, y + 0.42, RW - 0.7, 0.9, { size: 12, color: SOFT, lsm: 1.1 });
    });
    txt(s, "Paper trading only — live trading stays disabled and needs a separate sign-off.", RX + 0.35, 6.05, RW - 0.7, 0.45, { size: 10.5, color: GOLD });
  }

  await pres.writeFile({ fileName: OUT });
  console.log("wrote", OUT, "slides:", slideNo);
}
build().catch(e => { console.error(e); process.exit(1); });
