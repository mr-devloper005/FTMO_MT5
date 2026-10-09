// FTMO AI Trading Agent — final 5-slide deck for the boss.
// Screening → AI strategy → Backtesting → Buy & Hold comparison → Ranked results.
// Charts are native shapes (Keynote drops pptxgenjs charts). Every number is from our research runs.
const pptxgen = require("pptxgenjs");
const React = require("react");
const RDS = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa6");

const OUT = process.argv[2] || "final.pptx";

const BG = "0B1220", CARD = "131D2E", CARD2 = "1A2740", LINE = "2A3A57";
const INK = "F3F5F8", SOFT = "C9D2DE", MUTED = "93A0B4", DIM = "66758E", GOLD = "E3B04B";
const GREEN = "34C77B", AMBER = "F2A93B", RED = "F0625A", BLUE = "5B9BFF";
const TF = "Georgia", BF = "Arial";
const W = 13.333, M = 0.6, CW = W - 2 * M;

const ICONS = { screen: "FaMagnifyingGlassChart", strategy: "FaChessKnight", backtest: "FaClockRotateLeft",
  compare: "FaScaleBalanced", rank: "FaRankingStar", brain: "FaBrain", data: "FaChartLine", news: "FaNewspaper",
  library: "FaBookOpen", target: "FaBullseye", db: "FaDatabase", engine: "FaGears", filter: "FaFilter",
  loop: "FaArrowsRotate", list: "FaListOl", chat: "FaCommentDots", excel: "FaFileExcel", auto: "FaRobot",
  tools: "FaToolbox", user: "FaUserCheck", check: "FaCircleCheck", xmark: "FaCircleXmark" };
const cache = {};
async function iconPng(key, color) {
  const id = key + color;
  if (cache[id]) return cache[id];
  const C = fa[ICONS[key]];
  if (!C) throw new Error("missing icon " + key);
  let svg = RDS.renderToStaticMarkup(React.createElement(C, { size: 256 })).replace(/currentColor/g, "#" + color);
  if (!/fill=/.test(svg.slice(0, 200))) svg = svg.replace("<svg", `<svg fill="#${color}"`);
  const buf = await sharp(Buffer.from(svg)).resize(256, 256, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } }).png().toBuffer();
  return (cache[id] = "image/png;base64," + buf.toString("base64"));
}

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "FTMO_MT5";
pres.title = "FTMO AI Trading Agent";

function txt(s, text, x, y, w, h, o = {}) {
  s.addText(text, { x, y, w, h, isTextBox: true, margin: o.margin ?? 0, fontFace: o.font || BF, fontSize: o.size || 14,
    color: o.color || INK, bold: o.bold, italic: o.italic, align: o.align || "left", valign: o.valign || "top",
    charSpacing: o.cs, lineSpacingMultiple: o.lsm, paraSpaceAfter: o.psa, fit: "none" });
}
function rr(s, x, y, w, h, o = {}) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: o.r ?? 0.08,
    fill: { color: o.fill || CARD, transparency: o.ft || 0 },
    line: o.noline ? { type: "none" } : { color: o.line || LINE, width: o.lw || 1, dashType: o.dash || "solid" } });
}
async function dot(s, key, x, y, d, color, solidBg) {
  s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: solidBg || color, transparency: solidBg ? 0 : 82 }, line: { color, width: 1 } });
  const p = d * 0.25;
  s.addImage({ data: await iconPng(key, color), x: x + p, y: y + p, w: d - 2 * p, h: d - 2 * p });
}
function arrowR(s, x, y, w = 0.26, h = 0.24, color = GOLD) {
  s.addShape(pres.shapes.RIGHT_ARROW, { x, y, w, h, fill: { color }, line: { type: "none" } });
}
function pill(s, color, x, y, w, h, label, size = 9) {
  rr(s, x, y, w, h, { fill: color, ft: 82, line: color, r: 0.06 });
  txt(s, label, x, y, w, h, { size, bold: true, color, align: "center", valign: "middle", cs: 1 });
}
let n = 0;
function slide(kicker, title, notes) {
  const s = pres.addSlide();
  s.background = { color: BG };
  n += 1;
  if (kicker) txt(s, kicker.toUpperCase(), M, 0.42, 10, 0.3, { size: 10.5, bold: true, color: GOLD, cs: 2 });
  if (title) txt(s, title, M, 0.7, CW, 0.8, { font: TF, size: 28 });
  if (n > 1) {
    txt(s, "FTMO  ·  AI Trading Agent", M, 7.05, 6, 0.25, { size: 8.5, color: DIM });
    txt(s, String(n), W - M - 0.6, 7.05, 0.6, 0.25, { size: 8.5, color: DIM, align: "right" });
  }
  s.addNotes(notes);
  return s;
}

async function build() {
  // ------------------------------------------------------------- 1 cover + agent type
  {
    const s = slide(null, null, "We are building an autonomous research agent on Claude. It runs three jobs in one continuous loop: screen NASDAQ stocks, create or select trading strategies for them, and backtest those strategies against simply buying and holding. What it delivers is a ranked list of the stocks and strategies that beat Buy and Hold by our target, with a verdict and a reason for each. It recommends; people decide what is traded.");
    txt(s, "FTMO  ·  AI TRADING AGENT", M, 0.75, 8, 0.3, { size: 11, bold: true, color: GOLD, cs: 2 });
    txt(s, "An AI agent that finds the stocks and strategies that beat Buy & Hold", M, 1.15, 7.3, 2.35, { font: TF, size: 40, lsm: 1.02 });
    txt(s, "An autonomous research agent built on Claude. It screens NASDAQ continuously, creates and selects trading strategies, backtests them against Buy & Hold, and delivers a ranked list of what works.",
      M, 3.6, 7.0, 1.4, { size: 15, color: SOFT, lsm: 1.18 });
    const RX = 8.25, RW = W - M - RX;
    rr(s, RX, 0.75, RW, 4.3, { fill: CARD });
    txt(s, "WHAT KIND OF AGENT", RX + 0.32, 0.98, RW - 0.6, 0.3, { size: 10, bold: true, color: MUTED, cs: 1.5 });
    const kinds = [["auto", "Autonomous", "Runs continuously — no one has to start it"],
                   ["tools", "Tool-using", "Drives our market data, strategy library and backtest engine"],
                   ["user", "Research only", "Recommends with evidence; people decide trades"]];
    for (let i = 0; i < 3; i++) {
      const [ic, h, b] = kinds[i], y = 1.45 + i * 1.18;
      await dot(s, ic, RX + 0.32, y + 0.04, 0.52, GOLD);
      txt(s, h, RX + 1.0, y, RW - 1.25, 0.34, { size: 14, bold: true });
      txt(s, b, RX + 1.0, y + 0.36, RW - 1.25, 0.66, { size: 11.5, color: SOFT, lsm: 1.08 });
    }
    const steps = ["NASDAQ screening", "AI strategy", "Backtesting", "Buy & Hold comparison", "Ranked results"];
    const gap = 0.4, pw = (CW - 4 * gap) / 5, y = 5.55;
    steps.forEach((t, i) => {
      const x = M + i * (pw + gap), last = i === 4;
      rr(s, x, y, pw, 0.72, { fill: last ? GOLD : CARD2, line: last ? GOLD : LINE });
      txt(s, (i + 1) + "  " + t, x + 0.12, y, pw - 0.24, 0.72, { size: 12, bold: true, color: last ? BG : INK, align: "center", valign: "middle" });
      if (!last) arrowR(s, x + pw + 0.08, y + 0.24);
    });
    txt(s, "It analyses and recommends. People decide what is traded.", M, 6.6, 9, 0.3, { size: 11, color: MUTED });
  }

  // ------------------------------------------------------------- 2 architecture + data flow
  {
    const s = slide("Architecture & data flow", "One agent, four tools, a continuous data flow",
      "Left to right. Four kinds of data go in: NASDAQ prices and volume, company and earnings data, our strategy library plus the team's own strategies, and the target. In the middle is the Claude agent. It plans each run, decides what to test next and explains results, and it does the work through four tools: a screener, the strategy library, the backtest engine and a results database that records every run. On the right is what comes out: a ranked leaderboard, a plain-English reason for every stock, and an Excel export. The loop runs continuously, so rankings update as new data arrives.");
    const IX = M, IW = 2.75, CX = 3.85, CWd = 5.65, OX = 9.98, OW = W - M - OX;
    txt(s, "DATA IN", IX, 1.62, IW, 0.28, { size: 10, bold: true, color: MUTED, cs: 1.5 });
    txt(s, "THE AGENT", CX, 1.62, CWd, 0.28, { size: 10, bold: true, color: GOLD, cs: 1.5 });
    txt(s, "RESULTS OUT", OX, 1.62, OW, 0.28, { size: 10, bold: true, color: MUTED, cs: 1.5 });
    const ins = [["data", "NASDAQ market data", "Daily prices and volume"], ["news", "Earnings data", "Earnings dates, company facts"],
                 ["library", "Strategies", "Library + the team's own"], ["target", "The target", "Beat Buy & Hold by 50+ pts"]];
    for (let i = 0; i < 4; i++) {
      const [ic, h, b] = ins[i], y = 1.98 + i * 1.1;
      rr(s, IX, y, IW, 0.95, { fill: CARD });
      await dot(s, ic, IX + 0.18, y + 0.24, 0.46, BLUE);
      txt(s, h, IX + 0.78, y + 0.14, IW - 0.9, 0.36, { size: 12, bold: true, valign: "middle" });
      txt(s, b, IX + 0.78, y + 0.5, IW - 0.9, 0.34, { size: 10.5, color: SOFT });
    }
    arrowR(s, IX + IW + 0.13, 3.95, 0.36, 0.32);
    rr(s, CX, 1.98, CWd, 1.25, { fill: CARD2, line: GOLD, lw: 1.5 });
    await dot(s, "brain", CX + 0.25, 2.27, 0.66, GOLD, BG);
    txt(s, "Claude AI agent", CX + 1.1, 2.12, CWd - 1.3, 0.45, { size: 18, bold: true, valign: "middle" });
    txt(s, "Plans every run · decides what to test next · explains every result", CX + 1.1, 2.6, CWd - 1.3, 0.5, { size: 11.5, color: SOFT });
    const tools = [["filter", "Screener", "Liquidity, volatility, trend"], ["library", "Strategy library", "Tested rules + AI-made variants"],
                   ["engine", "Backtest engine", "10 years, real trading costs"], ["db", "Results database", "Every run recorded and ranked"]];
    const tw = (CWd - 0.17) / 2;
    for (let i = 0; i < 4; i++) {
      const [ic, h, b] = tools[i], x = CX + (i % 2) * (tw + 0.17), y = 3.45 + Math.floor(i / 2) * 1.17;
      rr(s, x, y, tw, 1.0, { fill: CARD });
      await dot(s, ic, x + 0.18, y + 0.25, 0.46, GOLD);
      txt(s, h, x + 0.78, y + 0.16, tw - 0.9, 0.36, { size: 12.5, bold: true, valign: "middle" });
      txt(s, b, x + 0.78, y + 0.52, tw - 0.9, 0.36, { size: 10.5, color: SOFT });
    }
    rr(s, CX, 5.8, CWd, 0.5, { fill: GOLD, ft: 85, line: GOLD });
    await dot(s, "loop", CX + 0.14, 5.85, 0.4, GOLD, BG);
    txt(s, "Runs continuously — new data in, rankings update", CX + 0.68, 5.8, CWd - 0.8, 0.5, { size: 12, bold: true, color: GOLD, valign: "middle" });
    arrowR(s, CX + CWd + 0.1, 3.95, 0.36, 0.32);
    const outs = [["list", "Ranked leaderboard", "Best stocks and strategies, a verdict for each"], ["chat", "Plain-English reasons", "Why each stock passed or failed"],
                  ["excel", "Excel export", "Refreshed as new data arrives"]];
    for (let i = 0; i < 3; i++) {
      const [ic, h, b] = outs[i], y = 1.98 + i * 1.47;
      rr(s, OX, y, OW, 1.3, { fill: CARD, line: GREEN });
      await dot(s, ic, OX + 0.18, y + 0.18, 0.46, GREEN);
      txt(s, h, OX + 0.78, y + 0.12, OW - 0.9, 0.5, { size: 12.5, bold: true, valign: "middle" });
      txt(s, b, OX + 0.18, y + 0.72, OW - 0.36, 0.5, { size: 10.5, color: SOFT, lsm: 1.05 });
    }
  }

  // ------------------------------------------------------------- 3 workflow step by step
  {
    const s = slide("The workflow", "What the agent does at each step",
      "Five steps, each feeding the next. One: it pulls every NASDAQ stock and screens them in parallel, without stopping, for liquidity, volatility and trend; NVIDIA, for example, trades about 25 billion dollars a day. Two: on each shortlisted stock it tries every strategy in the library plus the team's own, creates tuned variants, and keeps the best; one research run is 555 strategy tests. Three: it backtests each strategy over ten years, about 2,500 trading days, with realistic costs. Four: it compares the result with simply holding the stock over the same period with the same money and costs, then stress-tests it. On PayPal a trend strategy made 543 percent against 40 percent for holding. Five: it ranks every stock with a verdict and a reason; Nike ranks first as Recommended.");
    const st = [
      ["screen", "NASDAQ screening", "Pulls every NASDAQ stock and screens them in parallel, nonstop: liquidity, volatility, trend.", "NVDA trades $25B a day", "A shortlist of tradable stocks"],
      ["strategy", "AI strategy", "Tries every library and team strategy on each stock, creates tuned variants, keeps the best.", "555 strategy tests in one run", "Best strategies per stock"],
      ["backtest", "Backtesting", "Replays each strategy over 10 years of daily prices with realistic trading costs.", "2,500 trading days per test", "Return, risk and trade record"],
      ["compare", "Buy & Hold comparison", "Compares with simply holding the stock — same period, money and costs — then stress-tests it.", "PYPL: +543% vs +40%", "Pass or fail vs the target"],
      ["rank", "Ranked results", "Ranks every stock with a verdict and a plain-English reason; refreshes as data arrives.", "NKE ranked #1: Recommended", "Leaderboard + Excel"],
    ];
    const gap = 0.3, cw = (CW - 4 * gap) / 5, y = 1.65, h = 4.95;
    for (let i = 0; i < 5; i++) {
      const [ic, name, does, ex, out] = st[i], x = M + i * (cw + gap), last = i === 4;
      rr(s, x, y, cw, h, { fill: last ? CARD2 : CARD, line: last ? GOLD : LINE });
      await dot(s, ic, x + 0.18, y + 0.2, 0.52, GOLD);
      txt(s, (i + 1) + "  " + name, x + 0.8, y + 0.16, cw - 0.92, 0.62, { size: 13, bold: true, valign: "middle", lsm: 1.0 });
      txt(s, "AGENT DOES", x + 0.18, y + 0.98, cw - 0.36, 0.25, { size: 8.5, bold: true, color: MUTED, cs: 1 });
      txt(s, does, x + 0.18, y + 1.24, cw - 0.36, 1.5, { size: 11, color: SOFT, lsm: 1.1 });
      txt(s, "REAL EXAMPLE", x + 0.18, y + 2.86, cw - 0.36, 0.25, { size: 8.5, bold: true, color: GOLD, cs: 1 });
      txt(s, ex, x + 0.18, y + 3.12, cw - 0.36, 0.72, { font: TF, size: 14, color: INK, lsm: 1.0 });
      txt(s, "OUTPUT", x + 0.18, y + 3.98, cw - 0.36, 0.25, { size: 8.5, bold: true, color: GREEN, cs: 1 });
      txt(s, out, x + 0.18, y + 4.24, cw - 0.36, 0.6, { size: 11.5, bold: true, color: INK, lsm: 1.05 });
      if (!last) arrowR(s, x + cw + 0.03, y + h / 2 - 0.12, 0.24, 0.24);
    }
  }

  // ------------------------------------------------------------- 4 comparison: two tests
  {
    const s = slide("Buy & Hold comparison", "How a strategy earns a Recommended verdict",
      "Every strategy must pass two tests. Test one: beat Buy and Hold by our target, 50 points or more, over the same ten years with the same money and costs. Both examples pass: Nike's strategy made 45 percent while holding lost 32 percent, 77 points ahead; PayPal's made 543 percent against 40 percent, 504 points ahead. Test two: prove the result is real. The agent re-tests on data the strategy never saw, raises trading costs, checks that one lucky trade isn't carrying it, and checks it wins in most periods. Nike passes all four, so it is Recommended. PayPal fails three, so it stays on Watch. Anything below the target, or behind Buy and Hold, is Rejected.");
    const LW = 5.85, RX = M + LW + 0.3, RW = W - M - RX, top = 1.65, ch = 3.85;
    rr(s, M, top, LW, ch, { fill: CARD });
    txt(s, "TEST 1  ·  BEAT BUY & HOLD BY THE TARGET", M + 0.3, top + 0.22, LW - 0.6, 0.3, { size: 10, bold: true, color: GOLD, cs: 1.2 });
    txt(s, "Target: beat Buy & Hold by 50+ points over the same 10 years, same money, same costs", M + 0.3, top + 0.55, LW - 0.6, 0.55, { size: 11.5, color: SOFT, lsm: 1.08 });
    const ex = [["NKE", "Mean reversion", "+45%", "−32%", "+77 pts"], ["PYPL", "Trend regime", "+543%", "+40%", "+504 pts"]];
    ex.forEach(([sym, strat, sr, bh, beat], i) => {
      const y = top + 1.28 + i * 1.22;
      rr(s, M + 0.3, y, LW - 0.6, 1.05, { fill: CARD2, noline: true });
      txt(s, sym, M + 0.5, y + 0.1, 1.2, 0.5, { font: TF, size: 24 });
      txt(s, strat, M + 0.5, y + 0.6, 1.5, 0.3, { size: 10, color: MUTED });
      txt(s, [{ text: "Strategy  ", options: { color: MUTED } }, { text: sr, options: { color: GREEN, bold: true } }], M + 1.95, y + 0.16, 1.7, 0.34, { size: 13 });
      txt(s, [{ text: "Buy & Hold  ", options: { color: MUTED } }, { text: bh, options: { color: SOFT, bold: true } }], M + 1.95, y + 0.56, 1.8, 0.34, { size: 13 });
      txt(s, beat, M + 3.7, y + 0.08, 1.75, 0.55, { font: TF, size: 24, color: GOLD, align: "right" });
      txt(s, "PASSES", M + 3.7, y + 0.62, 1.75, 0.3, { size: 10, bold: true, color: GREEN, align: "right", cs: 1 });
    });
    rr(s, RX, top, RW, ch, { fill: CARD });
    txt(s, "TEST 2  ·  PROVE IT IS REAL", RX + 0.3, top + 0.22, RW - 0.6, 0.3, { size: 10, bold: true, color: GOLD, cs: 1.2 });
    const c1 = RX + 0.3, c2 = RX + 2.7, c3 = RX + 4.2, cw2 = 1.45;
    txt(s, "NKE", c2, top + 0.62, cw2, 0.3, { size: 12, bold: true, align: "center" });
    txt(s, "PYPL", c3, top + 0.62, cw2, 0.3, { size: 12, bold: true, align: "center" });
    const rows = [["Wins on data it never saw", "+181 pts", true, "−21 pts", false],
                  ["Survives higher trading costs", "8 ticks", true, "8 ticks", true],
                  ["Not one lucky trade", "top trade 8%", true, "top trade 43%", false],
                  ["Wins in most periods", "3 of 5", true, "2 of 5", false]];
    for (let i = 0; i < rows.length; i++) {
      const [lab, a, okA, b, okB] = rows[i], y = top + 1.02 + i * 0.68;
      s.addShape(pres.shapes.LINE, { x: c1, y: y - 0.05, w: RW - 0.6, h: 0, line: { color: LINE, width: 0.75 } });
      txt(s, lab, c1, y + 0.02, 2.55, 0.55, { size: 12, color: INK, valign: "middle" });
      for (const [cx, v, ok] of [[c2, a, okA], [c3, b, okB]]) {
        await dot(s, ok ? "check" : "xmark", cx + 0.05, y + 0.12, 0.32, ok ? GREEN : RED, BG);
        txt(s, [{ text: ok ? "Pass" : "Fail", options: { bold: true, color: ok ? GREEN : RED, breakLine: true } }, { text: v, options: { color: SOFT } }],
          cx + 0.45, y + 0.02, cw2 - 0.4, 0.58, { size: 10.5, valign: "middle" });
      }
    }
    const v = [[GREEN, "NKE  →  Recommended", "Passes both tests"], [AMBER, "PYPL  →  Watch", "Beats the target, fails Test 2"],
               [RED, "Rejected", "Below the target or behind Buy & Hold"]];
    const vw = (CW - 2 * 0.25) / 3;
    v.forEach(([c, h, b], i) => {
      const x = M + i * (vw + 0.25), y = 5.72;
      rr(s, x, y, vw, 0.92, { fill: c, ft: 86, line: c });
      txt(s, h, x + 0.25, y + 0.12, vw - 0.5, 0.36, { size: 14, bold: true, color: c });
      txt(s, b, x + 0.25, y + 0.5, vw - 0.5, 0.32, { size: 11, color: SOFT });
    });
  }

  // ------------------------------------------------------------- 5 ranked results
  {
    const s = slide("Final output", "A ranked leaderboard, with a verdict for every stock",
      "This is what the agent delivers. Each row is a stock with its best strategy, the strategy's return over ten years, Buy and Hold's return over the same period, the margin between them, the robustness result, and the verdict. The rows are real research results. Nike is first: Recommended, 77 points ahead of Buy and Hold and robust, and it is listed on FTMO. PayPal is far ahead of Buy and Hold but not robust, so it is on Watch. Intel and PepsiCo beat Buy and Hold by less than the target; Microsoft and Nvidia trail simply holding. Every row comes with a plain-English reason, the board refreshes as new data arrives, and it exports to Excel.");
    const hdr = ["#", "Stock", "On FTMO", "Best strategy", "Return", "Buy & Hold", "Beat by", "Robustness", "Verdict"];
    const colW = [0.45, 1.6, 0.9, 1.95, 1.15, 1.3, 1.3, 1.35, 2.133];
    const rows = [
      ["1", "NKE  ·  NYSE", "Yes", "Mean reversion", "+45%", "−32%", "+77 pts", "Passed", "Recommended", GREEN],
      ["2", "PYPL  ·  NASDAQ", "No", "Trend regime", "+543%", "+40%", "+504 pts", "Not robust", "Watch", AMBER],
      ["3", "INTC  ·  NASDAQ", "Yes", "Volatility breakout", "+249%", "+229%", "+20 pts", "Not robust", "Rejected: below target", RED],
      ["4", "PEP  ·  NASDAQ", "No", "Mean reversion", "+27%", "+21%", "+6 pts", "Not robust", "Rejected: below target", RED],
      ["5", "MSFT  ·  NASDAQ", "Yes", "Hold until broken", "+559%", "+799%", "−240 pts", "—", "Rejected: behind B&H", RED],
      ["6", "NVDA  ·  NASDAQ", "Yes", "200-day swing", "+10,816%", "+13,541%", "−2,725 pts", "—", "Rejected: behind B&H", RED],
    ];
    const num = new Set([4, 5, 6]), mid = new Set([2, 7]);
    const head = hdr.map((h, i) => ({ text: h, options: { bold: true, color: INK, fill: { color: CARD2 }, align: num.has(i) ? "right" : mid.has(i) ? "center" : "left", fontSize: 11 } }));
    const body = rows.map((r, ri) => r.slice(0, 9).map((c, i) => {
      const o = { color: SOFT, fill: { color: ri % 2 ? "101827" : CARD }, align: num.has(i) ? "right" : mid.has(i) ? "center" : "left", fontSize: 11.5 };
      if (i === 1) Object.assign(o, { color: INK, bold: true });
      if (i === 6) Object.assign(o, { color: c.startsWith("+") ? GOLD : SOFT, bold: true });
      if (i === 8) Object.assign(o, { color: r[9], bold: true });
      return { text: c, options: o };
    }));
    s.addTable([head, ...body], { x: M, y: 1.65, w: CW, colW, rowH: 0.5, fontFace: BF, valign: "middle",
      margin: [0, 0.1, 0, 0.1], border: { type: "solid", pt: 0.75, color: LINE } });
    const del = [["list", "Ranked leaderboard", "Refreshed as new data arrives"], ["chat", "A reason for every stock", "Why it passed, or why not"],
                 ["excel", "Excel export", "Ready for the team"]];
    const dw = (CW - 2 * 0.25) / 3;
    for (let i = 0; i < 3; i++) {
      const [ic, h, b] = del[i], x = M + i * (dw + 0.25), y = 5.4;
      rr(s, x, y, dw, 0.9, { fill: CARD2, noline: true });
      await dot(s, ic, x + 0.22, y + 0.2, 0.5, GOLD);
      txt(s, h, x + 0.9, y + 0.12, dw - 1.05, 0.36, { size: 13, bold: true, valign: "middle" });
      txt(s, b, x + 0.9, y + 0.48, dw - 1.05, 0.32, { size: 11, color: SOFT });
    }
    txt(s, "Real research results: 10 years of daily prices; same money and costs for strategy and Buy & Hold. Target: beat Buy & Hold by 50+ points, then pass the robustness checks.",
      M, 6.45, CW, 0.4, { size: 10, color: MUTED });
  }

  await pres.writeFile({ fileName: OUT });
  console.log("wrote", OUT, "slides:", n);
}
build().catch(e => { console.error(e); process.exit(1); });
