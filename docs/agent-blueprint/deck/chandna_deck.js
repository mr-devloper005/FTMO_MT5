// Chandna RAG project plan — 5 slides.
// Every fact is from the repo (github.com/Rahulchili/Research-and-Ranking-Dashboard-V1, commit 3b81a2a)
// or from our FTMO_MT5 research database. Diagrams are native shapes (Keynote drops pptxgenjs charts).
const pptxgen = require("pptxgenjs");
const React = require("react");
const RDS = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa6");

const OUT = process.argv[2] || "chandna.pptx";

const BG = "0B1220", CARD = "131D2E", CARD2 = "1A2740", LINE = "2A3A57";
const INK = "F3F5F8", SOFT = "C9D2DE", MUTED = "93A0B4", DIM = "66758E", GOLD = "E3B04B";
const GREEN = "34C77B", AMBER = "F2A93B", RED = "F0625A", BLUE = "5B9BFF", VIOLET = "A78BFA";
const TF = "Georgia", BF = "Arial";
const W = 13.333, M = 0.6, CW = W - 2 * M;

const ICONS = { file: "FaFileLines", gears: "FaGears", scale: "FaScaleBalanced", shield: "FaShieldHalved",
  web: "FaGlobe", chart: "FaChartLine", brain: "FaBrain", user: "FaUserPen", check: "FaCircleCheck",
  xmark: "FaCircleXmark", filter: "FaFilter", mic: "FaMicrophoneLines", bolt: "FaBolt", flask: "FaFlask",
  layers: "FaLayerGroup", clock: "FaClock", robot: "FaRobot", list: "FaListOl", search: "FaMagnifyingGlass",
  quote: "FaQuoteLeft", book: "FaBookOpen", db: "FaDatabase", cal: "FaCalendarCheck", star: "FaStar", user2: "FaUserGraduate" };
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
pres.title = "Chandna: Financial-Document RAG for FTMO";

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
  txt(s, "Chandna · Financial-Document RAG for FTMO", M, 7.05, 7, 0.25, { size: 8.5, color: DIM });
  txt(s, String(n), W - M - 0.6, 7.05, 0.6, 0.25, { size: 8.5, color: DIM, align: "right" });
  s.addNotes(notes);
  return s;
}

const bl = (items, o = {}) => items.map((t, j) => ({ text: t, options: { bullet: { indent: 12 }, breakLine: j < items.length - 1, ...o } }));

async function build() {
  // ------------------------------------------------------------------ 1. at a glance
  {
    const s = slide("1 · Project at a glance", "A research assistant that reads company documents",
      "Chandna's project is a financial-document RAG. It answers questions about a company from its SEC filings and earnings calls, always with the source, and it plugs into the FTMO research agent. Today the FTMO agent works from prices and backtests; the quality engine has the numbers but not what management actually said. Her work adds that missing knowledge. Six weeks, five pilot stocks, one working piece and one demo every week.");
    txt(s, "Build a RAG system that answers questions about a company from its SEC filings and earnings calls, with the source for every answer, and plug it into the FTMO research agent.",
      M, 1.55, 6.3, 1.1, { size: 16, color: SOFT, lsm: 1.15 });
    rr(s, M, 2.8, 6.3, 1.25, { fill: GOLD, ft: 88, line: GOLD });
    txt(s, "WHY", M + 0.3, 2.95, 2, 0.3, { size: 10.5, bold: true, color: GOLD, cs: 1.5 });
    txt(s, "FTMO already knows prices and backtests. It does not yet know what companies say: guidance, risks, quarterly results. Her RAG adds that.",
      M + 0.3, 3.25, 5.75, 0.75, { size: 13.5, lsm: 1.1 });
    const stats = [["6", "weeks"], ["5", "pilot stocks"], ["3", "document types"], ["1", "demo every week"]];
    stats.forEach(([v, l], i) => {
      const x = M + i * 1.6;
      rr(s, x, 4.3, 1.45, 1.2, { fill: CARD });
      txt(s, v, x, 4.4, 1.45, 0.55, { font: TF, size: 28, bold: true, color: [GOLD, BLUE, GREEN, VIOLET][i], align: "center" });
      txt(s, l, x, 4.98, 1.45, 0.4, { size: 11, color: MUTED, align: "center" });
    });
    txt(s, [{ text: "Pilot stocks:  ", options: { color: MUTED } }, { text: "MU · SNDK · TSLA · GS · MSFT", options: { bold: true } }], M, 5.75, 6.4, 0.35, { size: 14 });
    txt(s, [{ text: "Documents:  ", options: { color: MUTED } }, { text: "10-K / 10-Q filings · earnings-call transcripts · earnings releases (8-K)", options: { color: SOFT } }],
      M, 6.15, 6.4, 0.35, { size: 12.5 });
    const X = 7.35, BW = 5.35;
    rr(s, X, 1.55, BW, 5.15, { fill: CARD });
    txt(s, "WHAT SHE WILL BUILD", X + 0.35, 1.8, BW - 0.7, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const steps = [["file", BLUE, "Collect", "Download filings (SEC EDGAR) and earnings calls (FMP)"],
      ["search", GOLD, "Index & search", "Split by section, embed, search by stock and quarter"],
      ["quote", GREEN, "Answer with sources", "Grounded answers; every line cites document and section"],
      ["robot", VIOLET, "Plug into FTMO", "A research tool for the agent + a section on the dashboard"]];
    for (const [i, [k, c, t, d]] of steps.entries()) {
      const y = 2.3 + i * 1.08;
      await dot(s, k, X + 0.35, y, 0.6, c);
      txt(s, `${i + 1}  ${t}`, X + 1.15, y + 0.02, BW - 1.5, 0.32, { size: 15, bold: true });
      txt(s, d, X + 1.15, y + 0.36, BW - 1.5, 0.5, { size: 12, color: MUTED, lsm: 1.05 });
    }
  }

  // ------------------------------------------------------------------ 2. RAG for stock research
  {
    const s = slide("2 · How her RAG skills apply", "Same RAG skills, financial documents",
      "The pipeline is the one she already knows, adapted to filings: documents are split by their real sections such as Risk Factors and Management's Discussion, searched with both keywords and vectors, filtered by stock and quarter, and answered with citations. The four example questions are exactly the kind of work she will do. One rule matters most in finance: any number in an answer is checked against FMP's financial statements, and if the documents do not contain the answer, the system says so instead of guessing.");
    const flow = [["file", BLUE, "Documents", "10-K, 10-Q, earnings calls"], ["layers", GOLD, "Split by section", "Risk Factors, MD&A, guidance"],
      ["db", GREEN, "Embed + index", "vector + keyword; stock, quarter"], ["search", VIOLET, "Retrieve", "top passages for the question"],
      ["quote", AMBER, "Answer + cite", "document, section, date"]];
    const fw = 2.2, gap = (CW - 5 * fw) / 4;
    for (const [i, [k, c, t, d]] of flow.entries()) {
      const x = M + i * (fw + gap);
      rr(s, x, 1.6, fw, 1.6, { fill: CARD });
      await dot(s, k, x + (fw - 0.55) / 2, 1.75, 0.55, c);
      txt(s, t, x + 0.1, 2.38, fw - 0.2, 0.3, { size: 13.5, bold: true, align: "center" });
      txt(s, d, x + 0.1, 2.7, fw - 0.2, 0.45, { size: 11, color: MUTED, align: "center", lsm: 1.05 });
      if (i < 4) arrowR(s, x + fw + (gap - 0.28) / 2, 2.27, 0.28, 0.26);
    }
    txt(s, "EXAMPLE QUESTIONS SHE WILL MAKE IT ANSWER", M, 3.5, 8, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const qs = [["“What revenue did Micron guide for next quarter?”", "Finds the guidance line in the earnings call; cites call and date"],
      ["“Which new risks did Tesla add to its latest 10-K?”", "Compares Risk Factors with last year's filing"],
      ["“Did Microsoft deliver what it promised last quarter?”", "Pairs guidance with reported results: said vs actual"],
      ["“Summarise Goldman's latest quarter in 5 bullets.”", "Short brief; every bullet links to its source"]];
    qs.forEach(([q, a], i) => {
      const x = M + (i % 2) * 6.17, y = 3.85 + Math.floor(i / 2) * 1.3;
      rr(s, x, y, 5.95, 1.15, { fill: CARD });
      txt(s, q, x + 0.3, y + 0.15, 5.4, 0.4, { font: TF, size: 14.5 });
      txt(s, a, x + 0.3, y + 0.62, 5.4, 0.4, { size: 12, color: MUTED });
    });
    txt(s, [{ text: "Golden rule:  ", options: { bold: true, color: GOLD } },
      { text: "every number is checked against FMP's financial statements, and if the documents don't say it, the answer is \"not found\", never a guess.", options: { color: SOFT } }],
      M, 6.5, CW, 0.4, { size: 12.5 });
  }

  // ------------------------------------------------------------------ 3. week by week
  {
    const s = slide("3 · Week-by-week plan", "Six weeks, one working piece every week",
      "Each week has three parts: something to learn, something to build, and one concrete deliverable shown at Friday's demo. Weeks one to three build the core RAG on financial documents. Week four is the most valuable piece for FTMO: checking what management promised against what they reported. Week five turns it into readable research briefs, and week six connects it to the FTMO agent and dashboard.");
    const H = (t) => ({ text: t, options: { bold: true, color: GOLD, fill: { color: CARD2 }, fontSize: 12 } });
    const W = (t, c) => ({ text: t, options: { bold: true, color: c, fill: { color: CARD }, fontSize: 13, valign: "middle" } });
    const C = (t, o = {}) => ({ text: t, options: { color: o.color || SOFT, fill: { color: CARD }, bold: o.bold } });
    const rows = [
      [H("Week"), H("Learn"), H("Build"), H("Deliverable (Friday)")],
      [W("1", BLUE), C("How 10-K, 10-Q, 8-K and earnings calls are structured; SEC EDGAR and FMP"), C("Downloader for the 5 stocks; clean text with stock, form, quarter, section"), C("Document store (~10 documents per stock) + a one-page data note", { color: INK })],
      [W("2", BLUE), C("Splitting long filings by section; keyword + vector search; filters"), C("Index and search(stock, question, quarter)"), C("Search API + a 50-question test set with correct answers", { color: INK })],
      [W("3", GOLD), C("Grounded answers, citations, saying \"not found\""), C("Q&A with sources; numbers checked against FMP"), C("Q&A demo + accuracy report on the 50 questions", { color: INK })],
      [W("4", GOLD), C("Earnings guidance; said vs actual"), C("Pull guidance from calls → match next quarter's results → Beat / Hit / Miss"), C("Promises table for the 5 stocks + a management-credibility score", { color: INK })],
      [W("5", GREEN), C("Writing useful research summaries"), C("One-page company brief: business, latest quarter, guidance, risks, what changed"), C("5 briefs, every line cited, reviewed by the team", { color: INK })],
      [W("6", GREEN), C("How the FTMO research agent and dashboard work"), C("Read-only research tool + dashboard section + tests"), C("Merged code + team demo", { color: INK })],
    ];
    s.addTable(rows, { x: M, y: 1.5, w: CW, colW: [0.9, 3.55, 3.85, 3.83], rowH: [0.42, 0.78, 0.7, 0.7, 0.78, 0.78, 0.7], fontFace: BF, fontSize: 12,
      color: SOFT, border: { type: "solid", pt: 1, color: BG }, valign: "middle", margin: [4, 10, 4, 10] });
  }

  // ------------------------------------------------------------------ 4. weekly results
  {
    const s = slide("4 · Results she must show", "Clear targets, checked every Friday",
      "The weekly rhythm is simple: agree the week's goal on Monday, a short daily update, and a Friday demo with a written note on what works, what doesn't, and the numbers. The targets on the right are proposed starting points, measured on her own 50-question test set, so progress is visible from week two. The most important one is zero made-up numbers. By the end she will have learned to read financial filings and to build, test and evaluate a production RAG.");
    txt(s, "WEEKLY RHYTHM", M, 1.6, 5, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const rhythm = [["cal", BLUE, "Monday", "Agree the week's goal and deliverable"], ["clock", GOLD, "Every day", "Short written update: done, next, blocked"],
      ["star", GREEN, "Friday", "Demo + one-page note: what works, what doesn't, numbers"]];
    for (const [i, [k, c, t, d]] of rhythm.entries()) {
      const y = 2.0 + i * 1.0;
      await dot(s, k, M, y, 0.6, c);
      txt(s, t, M + 0.8, y + 0.02, 4.8, 0.3, { size: 15, bold: true });
      txt(s, d, M + 0.8, y + 0.34, 4.8, 0.35, { size: 12, color: MUTED });
    }
    rr(s, M, 5.15, 5.7, 1.5, { fill: CARD });
    txt(s, "SKILLS SHE GAINS", M + 0.3, 5.3, 5, 0.3, { size: 10.5, bold: true, color: VIOLET, cs: 1.5 });
    txt(s, bl(["Reading 10-Ks, 10-Qs and earnings calls", "Evaluating a RAG system with a real test set", "Writing tested, production code in our repo"]),
      M + 0.3, 5.62, 5.2, 1.0, { size: 12.5, color: SOFT, psa: 3 });
    const X = M + 6.17, BW = CW - 6.17;
    txt(s, "PROPOSED QUALITY TARGETS", X, 1.6, BW, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const t = [["Search", "Right passage in the top 5 for ≥ 80% of test questions", BLUE, "from week 2"],
      ["Answers", "≥ 90% carry a correct citation", GOLD, "from week 3"],
      ["Numbers", "Zero made-up numbers: all checked against FMP", RED, "always"],
      ["Said vs actual", "Hand-check of one stock agrees with the tool", GREEN, "week 4"],
      ["Briefs", "A reviewer can verify any line from its source in under 5 minutes", VIOLET, "week 5"]];
    t.forEach(([a, b, c, when], i) => {
      const y = 2.0 + i * 0.93;
      rr(s, X, y, BW, 0.8, { fill: CARD });
      txt(s, a, X + 0.3, y, 1.7, 0.8, { size: 14, bold: true, color: c, valign: "middle" });
      txt(s, b, X + 2.05, y, BW - 3.4, 0.8, { size: 12.5, color: SOFT, valign: "middle" });
      txt(s, when, X + BW - 1.25, y, 1.05, 0.8, { size: 10.5, color: DIM, valign: "middle", align: "right" });
    });
  }

  // ------------------------------------------------------------------ 5. integration
  {
    const s = slide("5 · How it plugs into FTMO", "Her RAG gives the FTMO agent company knowledge",
      "Her system sits between the documents and three parts of FTMO that exist today. First, the quality engine we ported from Rahul's dashboard has a Management lens worth fifteen percent that is still empty; her said-versus-actual work fills it, and it shows up on the Quality by Edge dashboard. Second, the AI research agent gets a new read-only tool to look up guidance and risks before it proposes strategies, and to cite them in its reports. Third, each stock's page on the dashboard gets a short cited brief. The same safety rules apply as for the agent: read-only, sources on every answer, no buy or sell advice, and no access to brokers.");
    // sources
    rr(s, M, 1.6, 2.7, 3.9, { fill: CARD });
    txt(s, "SOURCES", M + 0.25, 1.8, 2.3, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    await dot(s, "file", M + 0.25, 2.2, 0.5, BLUE);
    txt(s, [{ text: "SEC EDGAR", options: { bold: true, breakLine: true } }, { text: "10-K, 10-Q, 8-K (free)", options: { color: MUTED, size: 11 } }], M + 0.85, 2.2, 1.8, 0.7, { size: 13 });
    await dot(s, "mic", M + 0.25, 3.15, 0.5, GOLD);
    txt(s, [{ text: "FMP connector", options: { bold: true, breakLine: true } }, { text: "earnings-call transcripts, financial statements", options: { color: MUTED, size: 11 } }], M + 0.85, 3.15, 1.8, 0.95, { size: 13 });
    arrowR(s, M + 2.8, 3.4, 0.3, 0.28);
    // core
    const CX = M + 3.25;
    rr(s, CX, 2.25, 3.0, 2.6, { fill: GOLD, ft: 86, line: GOLD });
    await dot(s, "user2", CX + 1.2, 2.45, 0.6, GOLD);
    txt(s, "Chandna's RAG", CX, 3.15, 3.0, 0.4, { font: TF, size: 19, bold: true, align: "center" });
    txt(s, "Company knowledge\nindex · retrieve · cite", CX + 0.2, 3.6, 2.6, 0.7, { size: 12, color: SOFT, align: "center", lsm: 1.1 });
    arrowR(s, CX + 3.1, 3.4, 0.3, 0.28);
    // outputs
    const OX = CX + 3.55, OW = W - M - OX;
    const outs = [["scale", GREEN, "Management lens (quality engine)", "Fills the empty 15% lens: said vs actual → Quality × Edge dashboard"],
      ["robot", VIOLET, "New research tool for the AI agent", "Agent checks guidance and risks before proposing strategies; cites them in reports"],
      ["list", BLUE, "Stock brief on the dashboard", "Click a stock → cited brief next to its quality score and backtest verdict"]];
    for (const [i, [k, c, t, d]] of outs.entries()) {
      const y = 1.6 + i * 1.32;
      rr(s, OX, y, OW, 1.18, { fill: CARD });
      await dot(s, k, OX + 0.22, y + 0.28, 0.6, c);
      txt(s, t, OX + 1.0, y + 0.14, OW - 1.15, 0.35, { size: 13.5, bold: true });
      txt(s, d, OX + 1.0, y + 0.5, OW - 1.15, 0.6, { size: 11.5, color: MUTED, lsm: 1.05 });
    }
    // guardrails
    rr(s, M, 5.75, CW, 0.9, { fill: CARD2, line: LINE });
    await dot(s, "shield", M + 0.25, 5.9, 0.6, RED);
    txt(s, [{ text: "Same safety rules as the agent:  ", options: { bold: true, color: INK } },
      { text: "read-only · sources on every answer · numbers checked against FMP · no buy/sell advice · no broker access", options: { color: SOFT } }],
      M + 1.05, 5.75, CW - 1.3, 0.9, { size: 12, valign: "middle" });
  }

  await pres.writeFile({ fileName: OUT });
  console.log("wrote", OUT);
}
build().catch((e) => { console.error(e); process.exit(1); });
