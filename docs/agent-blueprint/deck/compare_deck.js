// Rahul's Research & Ranking Dashboard vs FTMO_MT5 — 5-slide comparison deck.
// Every fact is from the repo (github.com/Rahulchili/Research-and-Ranking-Dashboard-V1, commit 3b81a2a)
// or from our FTMO_MT5 research database. Diagrams are native shapes (Keynote drops pptxgenjs charts).
const pptxgen = require("pptxgenjs");
const React = require("react");
const RDS = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa6");

const OUT = process.argv[2] || "compare.pptx";

const BG = "0B1220", CARD = "131D2E", CARD2 = "1A2740", LINE = "2A3A57";
const INK = "F3F5F8", SOFT = "C9D2DE", MUTED = "93A0B4", DIM = "66758E", GOLD = "E3B04B";
const GREEN = "34C77B", AMBER = "F2A93B", RED = "F0625A", BLUE = "5B9BFF", VIOLET = "A78BFA";
const TF = "Georgia", BF = "Arial";
const W = 13.333, M = 0.6, CW = W - 2 * M;

const ICONS = { file: "FaFileLines", gears: "FaGears", scale: "FaScaleBalanced", shield: "FaShieldHalved",
  web: "FaGlobe", chart: "FaChartLine", brain: "FaBrain", user: "FaUserPen", check: "FaCircleCheck",
  xmark: "FaCircleXmark", filter: "FaFilter", mic: "FaMicrophoneLines", bolt: "FaBolt", flask: "FaFlask",
  layers: "FaLayerGroup", clock: "FaClock", robot: "FaRobot", list: "FaListOl" };
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
pres.title = "Research Dashboard vs FTMO_MT5";

function txt(s, text, x, y, w, h, o = {}) {
  s.addText(text, { x, y, w, h, isTextBox: true, margin: o.margin ?? 0, fontFace: o.font || BF, fontSize: o.size || 14,
    color: o.color || INK, bold: o.bold, italic: o.italic, align: o.align || "left", valign: o.valign || "top",
    charSpacing: o.cs, lineSpacingMultiple: o.lsm, paraSpaceAfter: o.psa, fit: "none" });
}
function rr(s, x, y, w, h, o = {}) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: o.r ?? 0.08,
    fill: { color: o.fill || CARD, transparency: o.ft || 0 },
    line: o.noline ? { type: "none" } : { color: o.line || LINE, width: o.lw || 1 } });
}
async function dot(s, key, x, y, d, color) {
  s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color, transparency: 82 }, line: { color, width: 1 } });
  const p = d * 0.25;
  s.addImage({ data: await iconPng(key, color), x: x + p, y: y + p, w: d - 2 * p, h: d - 2 * p });
}
function arrowR(s, x, y, w = 0.3, h = 0.26, color = GOLD) {
  s.addShape(pres.shapes.RIGHT_ARROW, { x, y, w, h, fill: { color }, line: { type: "none" } });
}
function pill(s, color, x, y, w, h, label, size = 10) {
  rr(s, x, y, w, h, { fill: color, ft: 82, line: color, r: 0.06 });
  txt(s, label, x, y, w, h, { size, bold: true, color, align: "center", valign: "middle" });
}
let n = 0;
function slide(kicker, title, notes) {
  const s = pres.addSlide();
  s.background = { color: BG };
  n += 1;
  txt(s, kicker.toUpperCase(), M, 0.42, 10, 0.3, { size: 10.5, bold: true, color: GOLD, cs: 2 });
  txt(s, title, M, 0.72, CW, 0.75, { font: TF, size: 28 });
  txt(s, "Research Dashboard (Rahul)  vs  FTMO_MT5", M, 7.05, 7, 0.25, { size: 8.5, color: DIM });
  txt(s, String(n), W - M - 0.6, 7.05, 0.6, 0.25, { size: 8.5, color: DIM, align: "right" });
  s.addNotes(notes);
  return s;
}

async function build() {
  // ------------------------------------------------------------------ 1. what it is
  {
    const s = slide("1 · What this repo is", "A stock-ranking dashboard, not a trading system",
      "This is Rahul's Investment Research Priority Dashboard, built with Claude Cowork under Sreeni's mentorship. It is a static website that scores 93 large-cap stocks on five lenses and sorts them into priority buckets. Fundamentals and technicals carry most of the weight. Today it ranks GOOG, NVDA, TSM, MAR and ARM on top; nobody reaches the High bucket. It contains no trading rules and no backtests, and its data is a snapshot from mid-May 2026.");
    txt(s, "Scores 93 large-cap stocks on 5 lenses, blends them into one score (0–100) and sorts them into priority buckets.",
      M, 1.55, 6.3, 0.9, { size: 16, color: SOFT, lsm: 1.15 });
    const stats = [["93", "stocks", GOLD], ["5", "scoring lenses", BLUE], ["0", "backtests", RED], ["May '26", "data snapshot", AMBER]];
    stats.forEach(([v, l, c], i) => {
      const x = M + i * 1.6;
      rr(s, x, 2.65, 1.45, 1.35, { fill: CARD });
      txt(s, v, x, 2.8, 1.45, 0.6, { font: TF, size: v.length > 3 ? 22 : 30, bold: true, color: c, align: "center" });
      txt(s, l, x, 3.45, 1.45, 0.4, { size: 11, color: MUTED, align: "center" });
    });
    txt(s, "RESULT TODAY", M, 4.35, 4, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const buckets = [["High", "0", GREEN], ["Medium", "20", BLUE], ["Low", "65", AMBER], ["Watchlist", "7", VIOLET], ["Avoid", "1", RED]];
    buckets.forEach(([l, v, c], i) => {
      const x = M + i * 1.26;
      rr(s, x, 4.7, 1.16, 0.9, { fill: c, ft: 86, line: c });
      txt(s, v, x, 4.76, 1.16, 0.45, { font: TF, size: 20, bold: true, color: c, align: "center" });
      txt(s, l, x, 5.2, 1.16, 0.3, { size: 10.5, color: SOFT, align: "center" });
    });
    txt(s, [{ text: "Top 5:  ", options: { color: MUTED } }, { text: "GOOG · NVDA · TSM · MAR · ARM", options: { bold: true, color: INK } }],
      M, 5.85, 6.3, 0.35, { size: 14 });
    txt(s, "Built by Rahul Yalamanchili (mentor: Sreeni) with Claude Cowork  ·  github.com/Rahulchili/Research-and-Ranking-Dashboard-V1",
      M, 6.45, 6.4, 0.45, { size: 10, color: DIM, lsm: 1.1 });

    // right: the 5 lenses as weight bars
    const X = 7.35, BW = 5.35;
    rr(s, X, 1.55, BW, 5.15, { fill: CARD });
    txt(s, "THE 5 LENSES  ·  WEIGHT IN THE SCORE", X + 0.35, 1.8, BW - 0.7, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const lenses = [["Fundamentals", 35, "Revenue growth, margins, free cash flow vs sector", BLUE],
      ["Technicals", 35, "200-day average, RSI, MACD, ADX, momentum", GOLD],
      ["Management", 15, "Did they deliver what they promised on calls?", GREEN],
      ["Options", 10, "Implied volatility, put/call ratio, skew", VIOLET],
      ["Valuation", 5, "Forward P/E vs peers, value-trap check", AMBER]];
    lenses.forEach(([name, w, d, c], i) => {
      const y = 2.3 + i * 0.86, barMax = BW - 1.6;
      txt(s, name, X + 0.35, y, 2.5, 0.3, { size: 14, bold: true });
      txt(s, w + "%", X + BW - 1.05, y, 0.7, 0.3, { size: 14, bold: true, color: c, align: "right" });
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: X + 0.35, y: y + 0.36, w: barMax, h: 0.13, rectRadius: 0.05, fill: { color: CARD2 }, line: { type: "none" } });
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: X + 0.35, y: y + 0.36, w: barMax * w / 35, h: 0.13, rectRadius: 0.05, fill: { color: c }, line: { type: "none" } });
      txt(s, d, X + 0.35, y + 0.52, BW - 0.7, 0.28, { size: 11, color: MUTED });
    });
  }

  // ------------------------------------------------------------------ 2. how it works
  {
    const s = slide("2 · How it works", "Files in → five scores → a ranked website",
      "Each stock needs a folder of files that someone gathers by hand: price history, an options chain, earnings-call transcripts, SEC filings and FactSet peer data. The add_ticker pipeline turns those into numbers: technical indicators, quarterly fundamentals, management promises pulled out of the transcripts and checked against what was actually reported, options metrics, and peer valuation. Those become five scores, a weighted composite and a bucket. A quality gate checks the build, and the result is a static website with seven tabs per stock. The written view per stock is drafted automatically and then edited by hand. Claude was used to build the project; there is no AI inside the running pipeline, no trading rule and no backtest.");
    const cols = [
      { x: M, w: 2.55, key: "file", c: BLUE, h: "1  INPUTS", sub: "files added by hand, per stock",
        items: ["Price history (xlsx)", "Options chain (xlsx)", "Earnings-call transcripts", "SEC filings (10-K / 10-Q)", "FactSet peer data"] },
      { x: M + 2.95, w: 2.75, key: "gears", c: GOLD, h: "2  PIPELINE", sub: "add_ticker.py · 8 steps",
        items: ["Technicals: SMA, RSI, MACD, ATR", "Fundamentals by quarter", "Promises found in transcripts → Beat / Hit / Miss", "Options: IV, put/call, skew", "Peer valuation + 1,300+ charts"] },
      { x: M + 6.1, w: 2.55, key: "scale", c: GREEN, h: "3  SCORING", sub: "5 lens scores 0–100",
        items: ["Weighted composite (35/35/15/10/5)", "Bucket: High ≥85 · Medium ≥70 · Low ≥55 · Watchlist ≥40 · Avoid", "\"Why this score\" text per lens"] },
      { x: M + 9.05, w: 3.08, key: "web", c: VIOLET, h: "4  OUTPUT", sub: "static website (GitHub Pages)",
        items: ["Leaderboard + Excel / CSV", "7 tabs per stock", "Stance, bull / bear case: auto-drafted, then edited by hand"] },
    ];
    for (const [i, c] of cols.entries()) {
      rr(s, c.x, 1.6, c.w, 3.3, { fill: CARD });
      await dot(s, c.key, c.x + 0.25, 1.8, 0.55, c.c);
      txt(s, c.h, c.x + 0.92, 1.82, c.w - 1.0, 0.3, { size: 12, bold: true, color: c.c, cs: 1 });
      txt(s, c.sub, c.x + 0.92, 2.1, c.w - 1.0, 0.3, { size: 10, color: MUTED });
      txt(s, c.items.map((t, j) => ({ text: t, options: { bullet: { indent: 12 }, breakLine: j < c.items.length - 1 } })),
        c.x + 0.25, 2.6, c.w - 0.4, 2.6, { size: 12, color: SOFT, psa: 6, lsm: 1.05 });
      if (i < cols.length - 1) arrowR(s, c.x + c.w + 0.06, 3.12, 0.28, 0.26);
    }
    // quality gate band
    rr(s, M + 2.95, 5.12, 5.7, 0.55, { fill: CARD2, line: LINE });
    txt(s, [{ text: "Quality gate  ", options: { bold: true, color: GOLD } },
      { text: "make verify: lint, type checks, tests, schema, secret scan, write-only-to-data/ guard", options: { color: SOFT } }],
      M + 3.15, 5.12, 5.4, 0.55, { size: 11, valign: "middle" });
    // what it does NOT contain
    const nots = [["No trading rules", "no buy / sell signals are tested"], ["No backtests", "no comparison with Buy & Hold"],
      ["No AI at runtime", "Claude Cowork built it; the pipeline is plain Python"], ["Manual refresh", "prices dated 13–22 May 2026"]];
    nots.forEach(([a, b], i) => {
      const x = M + i * 3.07;
      txt(s, [{ text: "✕  " + a, options: { bold: true, color: RED, breakLine: true } }, { text: b, options: { color: MUTED, size: 10.5 } }],
        x, 6.0, 2.95, 0.75, { size: 12 });
    });
  }

  // ------------------------------------------------------------------ 3. how it differs
  {
    const s = slide("3 · How it differs from FTMO_MT5", "Different question, so different answers",
      "The dashboard asks which companies look strongest right now. Our agent asks which trading rule beats simply holding the stock, and whether that edge survives stress tests. Two real stocks show why both views matter. GOOG is the dashboard's number one, but in our tests no rule beat holding it: the best rule made 617 percent while holding made 752. NFLX is near the bottom of the dashboard at 84 of 93, yet it is our number one: a regime-switch rule on daily bars made 698 percent against 430 for holding, though it is still graded fragile. A strong company is often best simply held; a weaker one can still trade well. The two answers complement each other.");
    // two questions
    const qs = [["THE DASHBOARD ASKS", "Which companies look strongest right now?", "judgement scores from data", BLUE],
      ["FTMO_MT5 ASKS", "Which trading rule beats simply holding the stock, and does it survive stress tests?", "tested rules on our engine", GOLD]];
    qs.forEach(([k, q, how, c], i) => {
      const x = M + i * 6.17;
      rr(s, x, 1.6, 5.95, 1.55, { fill: c, ft: 88, line: c });
      txt(s, k, x + 0.3, 1.75, 5.4, 0.3, { size: 10.5, bold: true, color: c, cs: 1.5 });
      txt(s, q, x + 0.3, 2.05, 5.4, 0.7, { font: TF, size: 17, lsm: 1.05 });
      txt(s, how, x + 0.3, 2.75, 5.4, 0.3, { size: 11, color: MUTED });
    });
    txt(s, "SAME STOCK, BOTH SYSTEMS  ·  real results", M, 3.45, 8, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const ex = [
      { t: "GOOG", dash: "#1 of 93", ds: "80.8 · Medium", dc: BLUE, ftmo: "Rejected", fc: RED,
        fs: "Best rule +617%  vs  holding +752%", note: "Strong company — holding it beat every rule" },
      { t: "NFLX", dash: "#84 of 93", ds: "56.1 · Low", dc: AMBER, ftmo: "#1 · Watch", fc: AMBER,
        fs: "Best rule +698%  vs  holding +430%", note: "Weaker score — but a regime-switch rule beat holding it (FRAGILE)" },
    ];
    ex.forEach((e, i) => {
      const x = M + i * 6.17, y = 3.85;
      rr(s, x, y, 5.95, 2.4, { fill: CARD });
      txt(s, e.t, x + 0.3, y + 0.2, 1.5, 0.5, { font: TF, size: 24, bold: true });
      txt(s, "DASHBOARD", x + 0.3, y + 0.85, 2.5, 0.25, { size: 10, bold: true, color: MUTED, cs: 1 });
      txt(s, e.dash, x + 0.3, y + 1.1, 2.5, 0.45, { font: TF, size: 20, bold: true, color: e.dc });
      txt(s, e.ds, x + 0.3, y + 1.55, 2.5, 0.3, { size: 12, color: SOFT });
      txt(s, "FTMO_MT5", x + 3.0, y + 0.85, 2.7, 0.25, { size: 10, bold: true, color: MUTED, cs: 1 });
      txt(s, e.ftmo, x + 3.0, y + 1.1, 2.7, 0.45, { font: TF, size: 20, bold: true, color: e.fc });
      txt(s, e.fs, x + 3.0, y + 1.55, 2.75, 0.45, { size: 12, color: SOFT, lsm: 1.05 });
      txt(s, e.note, x + 0.3, y + 1.98, 5.4, 0.3, { size: 11, italic: true, color: MUTED });
    });
    txt(s, [{ text: "Also the same:  ", options: { bold: true, color: GOLD } },
      { text: "both rank stocks with a reason, use 200-day average / RSI / ADX, export to Excel, have tests and guard rails, and were built with Claude.", options: { color: SOFT } }],
      M, 6.45, CW, 0.45, { size: 12 });
  }

  // ------------------------------------------------------------------ 4. side by side
  {
    const s = slide("4 · Side-by-side", "Two tools, built for different jobs",
      "Row by row: the dashboard covers 93 hand-picked stocks from licensed and public data that someone collects, and turns it into judgement scores. Our agent covers the whole NASDAQ automatically, backtests real rules on ten timeframes against Buy and Hold, and stress-tests the best. Neither places trades: in FTMO_MT5 execution sits in a separate, paper-first part of the system.");
    const H = (t) => ({ text: t, options: { bold: true, color: GOLD, fill: { color: CARD2 }, fontSize: 12 } });
    const L = (t) => ({ text: t, options: { bold: true, color: INK, fill: { color: CARD } } });
    const C = (t, o = {}) => ({ text: t, options: { color: o.color || SOFT, fill: { color: CARD }, bold: o.bold } });
    const rows = [
      [H(""), H("Research Dashboard (Rahul)"), H("FTMO_MT5 research agent")],
      [L("Goal"), C("Rank companies by quality & setup"), C("Find rules that beat Buy & Hold")],
      [L("Stocks"), C("93 hand-picked, NYSE + NASDAQ"), C("3,139 NASDAQ, screened automatically → 705 pass")],
      [L("Data"), C("FactSet, SEC filings, transcripts, options — files added by hand"), C("Yahoo prices + NASDAQ listing — fetched automatically")],
      [L("Method"), C("5 weighted scores → one 0–100 score"), C("Backtests on our engine, 10 timeframes, vs Buy & Hold")],
      [L("Strategies"), C("None — a written stance per stock"), C("41 tested: 15 built-in · 2 team · 24 AI-designed")],
      [L("Proof"), C("None (no backtests)", { color: RED }), C("2,018 backtests · 136 stress tests", { color: GREEN })],
      [L("AI"), C("Claude Cowork used to build it"), C("Claude agent runs the research 24/7, research tools only")],
      [L("Freshness"), C("Snapshot, 13–22 May 2026", { color: AMBER }), C("Live — reruns on a schedule", { color: GREEN })],
      [L("Output"), C("Static website + Excel / CSV"), C("FTMO app dashboard + Excel + Google Sheet + reports")],
      [L("Result now"), C("0 High · 20 Medium · 65 Low"), C("0 Recommended · 24 Watch")],
      [L("Places trades?"), C("No"), C("No — research only; execution kept separate")],
    ];
    s.addTable(rows, { x: M, y: 1.55, w: CW, colW: [2.0, 5.07, 5.06], rowH: 0.42, fontFace: BF, fontSize: 12.5,
      color: SOFT, border: { type: "solid", pt: 1, color: BG }, valign: "middle", margin: [0, 10, 0, 10] });
  }

  // ------------------------------------------------------------------ 5. takeaways
  {
    const s = slide("5 · Key takeaways for FTMO", "Borrow its quality lenses, keep our testing",
      "Three ideas are worth borrowing, each as an extra check around our tested results rather than a replacement. First, a fundamentals filter from SEC filings, so the agent spends its tests on healthy companies. Second, management credibility: did the company deliver what it promised on its calls; this needs transcripts. Third, options context such as implied volatility and put/call as a risk flag next to each verdict; our broker hub can already read option chains. What not to copy: hand-maintained scores, a one-off data snapshot, and stances that were never tested. A practical next step: run our agent on the 57 of its 93 stocks that are NASDAQ-listed and show its quality score next to our tested verdict.");
    const ideas = [
      { k: "filter", c: BLUE, t: "Quality filter first", d: "Check revenue growth, margins and cash flow (SEC filings, free) before backtesting.", ex: "Our screen today checks only liquidity, price, volatility and history." },
      { k: "mic", c: GREEN, t: "Management credibility", d: "Did the company deliver what it promised on earnings calls? Beat / Hit / Miss.", ex: "Needs a transcript source — the repo uses files added by hand." },
      { k: "bolt", c: VIOLET, t: "Options context", d: "Implied volatility and put/call as a risk flag beside each verdict.", ex: "Our broker hub already reads option chains." },
    ];
    for (const [i, it] of ideas.entries()) {
      const x = M + i * 4.12;
      rr(s, x, 1.6, 3.9, 2.85, { fill: CARD });
      await dot(s, it.k, x + 0.3, 1.85, 0.6, it.c);
      txt(s, it.t, x + 1.05, 1.95, 2.7, 0.45, { size: 17, bold: true });
      txt(s, it.d, x + 0.3, 2.65, 3.3, 0.85, { size: 13, color: SOFT, lsm: 1.1 });
      txt(s, it.ex, x + 0.3, 3.6, 3.3, 0.7, { size: 11, italic: true, color: MUTED, lsm: 1.05 });
    }
    // don't copy
    rr(s, M, 4.7, 5.95, 1.95, { fill: RED, ft: 90, line: RED });
    txt(s, "DON'T COPY", M + 0.3, 4.88, 5, 0.3, { size: 10.5, bold: true, color: RED, cs: 1.5 });
    txt(s, ["Scores maintained by hand", "A one-off data snapshot", "Buy / sell stances that were never tested"].map((t, j, a) =>
      ({ text: t, options: { bullet: { indent: 12 }, breakLine: j < a.length - 1 } })), M + 0.3, 5.25, 5.4, 1.3, { size: 13, color: SOFT, psa: 5 });
    // next step
    rr(s, M + 6.17, 4.7, 5.96, 1.95, { fill: GOLD, ft: 86, line: GOLD });
    txt(s, "PRACTICAL NEXT STEP", M + 6.47, 4.88, 5, 0.3, { size: 10.5, bold: true, color: GOLD, cs: 1.5 });
    txt(s, "Run our agent on the 57 dashboard stocks that are NASDAQ-listed, and show their quality score next to our tested verdict.",
      M + 6.47, 5.22, 5.4, 0.85, { font: TF, size: 15, lsm: 1.1 });
    txt(s, "The other 36 (mostly NYSE) need our universe extended first.", M + 6.47, 6.2, 5.4, 0.3, { size: 11, italic: true, color: MUTED });
  }

  await pres.writeFile({ fileName: OUT });
  console.log("wrote", OUT);
}
build().catch((e) => { console.error(e); process.exit(1); });
