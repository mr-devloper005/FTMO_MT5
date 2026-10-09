// FMP Ultimate upgrade: test + retest results — 6 slides for management.
// Every number is from 2026-10-09 artefacts: rag/state/fmp/ultimate_probe_2026-10-09.txt,
// coverage_before/after_ultimate.json, rag ingest logs, research_agent/state/reports/retest-20261009-*.json,
// run agent-20261009-154659-add3 (leaderboard CSV). Charts are native shapes (Keynote drops pptxgenjs charts).
const pptxgen = require("pptxgenjs");
const React = require("react");
const RDS = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa6");

const OUT = process.argv[2] || "ultimate.pptx";

const BG = "0B1220", CARD = "131D2E", CARD2 = "1A2740", LINE = "2A3A57";
const INK = "F3F5F8", SOFT = "C9D2DE", MUTED = "93A0B4", DIM = "66758E", GOLD = "E3B04B";
const GREEN = "34C77B", AMBER = "F2A93B", RED = "F0625A", BLUE = "5B9BFF", VIOLET = "A78BFA";
const TF = "Georgia", BF = "Arial";
const W = 13.333, M = 0.6, CW = W - 2 * M;

const ICONS = { key: "FaKey", mic: "FaMicrophoneLines", db: "FaDatabase", check: "FaCircleCheck", xmark: "FaCircleXmark",
  rotate: "FaRotate", shield: "FaShieldHalved", search: "FaMagnifyingGlass", chart: "FaChartLine", warn: "FaTriangleExclamation",
  flask: "FaFlask", list: "FaListOl", quote: "FaQuoteLeft", bolt: "FaBolt" };
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
pres.title = "FMP Ultimate upgrade: test and retest results";

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
function rect(s, x, y, w, h, color, o = {}) {
  s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color, transparency: o.ft || 0 }, line: { type: "none" } });
}
async function dot(s, key, x, y, d, color) {
  s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color, transparency: 82 }, line: { color, width: 1 } });
  const p = d * 0.25;
  s.addImage({ data: await iconPng(key, color), x: x + p, y: y + p, w: d - 2 * p, h: d - 2 * p });
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
  txt(s, "FTMO AI Research · FMP Ultimate upgrade: test and retest · 9 Oct 2026 · research only, no trading", M, 7.05, 10, 0.25,
    { size: 8.5, color: DIM });
  txt(s, String(n), W - M - 0.6, 7.05, 0.6, 0.25, { size: 8.5, color: DIM, align: "right" });
  s.addNotes(notes);
  return s;
}
const bl = (items, o = {}) => items.map((t, j, a) => ({ text: t, options: { bullet: { indent: 12 }, breakLine: j < a.length - 1, ...o } }));

async function build() {
  // ------------------------------------------------------------------ 1. executive summary
  {
    const s = slide("1 · Executive summary", "The FMP Ultimate key closed every data gap we had",
      "The new Ultimate key was tested on every FMP feature that failed on the old plan: seventeen checks, and all seventeen now pass. That unlocked earnings-call transcripts and full data for Micron and SanDisk. We ingested the latest four earnings calls for all five stocks, twenty calls in total, and made them searchable through the existing research assistant. Every one of the five stocks now has strong company evidence, up from two missing and three partial. On the same twenty research questions that previously came back 'not found' half the time, the system now answers all twenty. On the agent's twenty-four questions in the retest, twenty-three answers are fully grounded, and 130 financial figures were checked against the database with no conflicts. Total Claude cost for the day's testing was about two and a half dollars.");
    const stats = [["17 / 17", "FMP checks pass\n(old plan: 0 / 17)", GREEN], ["20", "earnings calls ingested\n(4 per stock)", BLUE],
      ["5 / 5", "stocks with Strong evidence\n(was 0 / 5)", GREEN], ["10 → 0", "'Not found' answers\n(same 20 questions)", GOLD],
      ["23 / 24", "answers fully grounded\n(retest run)", GREEN], ["0", "conflicts in 130 figures\nchecked vs database", GREEN]];
    stats.forEach(([v, l, c], i) => {
      const col = i % 3, row = Math.floor(i / 3);
      const x = M + col * 2.4, y = 1.6 + row * 1.85;
      rr(s, x, y, 2.25, 1.7, { fill: CARD });
      txt(s, v, x, y + 0.2, 2.25, 0.6, { font: TF, size: 28, bold: true, color: c, align: "center" });
      txt(s, l, x + 0.1, y + 0.88, 2.05, 0.7, { size: 11.5, color: MUTED, align: "center", lsm: 1.05 });
    });
    txt(s, [{ text: "Cost:  ", options: { color: MUTED } }, { text: "≈ $2.50 of Claude usage for all testing plus the research re-run · FMP calls well within the Ultimate quota", options: { color: SOFT } }],
      M, 5.45, 7.1, 0.55, { size: 12.5, lsm: 1.05 });
    txt(s, [{ text: "Unchanged by design:  ", options: { color: MUTED } }, { text: "trading verdicts. Company evidence adds context; it never upgrades a strategy.", options: { color: SOFT } }],
      M, 6.1, 7.1, 0.55, { size: 12.5, lsm: 1.05 });
    const X = 8.0, BW = W - M - X;
    rr(s, X, 1.6, BW, 5.15, { fill: CARD });
    txt(s, "WHAT WE DID", X + 0.35, 1.83, 3, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const steps = [["key", GOLD, "Tested the new key", "17 live checks on everything the old plan refused"],
      ["mic", BLUE, "Ingested earnings calls", "latest 4 calls x 5 stocks, by speaker, prepared vs Q&A"],
      ["db", VIOLET, "Filled MU and SNDK", "statements, earnings, 10-K/10-Q notes"],
      ["rotate", GREEN, "Retested failed jobs", "same questions, full 5-stock research run"],
      ["shield", AMBER, "Fixed what retest exposed", "retrieval + number checks, with tests"]];
    for (const [i, [k, c, t, d]] of steps.entries()) {
      const y = 2.28 + i * 0.86;
      await dot(s, k, X + 0.35, y, 0.52, c);
      txt(s, t, X + 1.05, y - 0.01, BW - 1.3, 0.3, { size: 14, bold: true });
      txt(s, d, X + 1.05, y + 0.29, BW - 1.3, 0.3, { size: 11, color: MUTED });
    }
  }

  // ------------------------------------------------------------------ 2. key test
  {
    const s = slide("2 · Testing the new key", "Everything the old plan refused now works",
      "These are the live checks we ran with the new key, each one a feature that failed on the old plan with a 'restricted, not in your subscription' error. All of them now pass. Earnings-call transcripts are available for all five stocks: Micron has 81 calls on record, Tesla 61, Goldman 79, Microsoft 83, and SanDisk 4 since it listed in 2025. Micron and SanDisk financial data, which the old plan blocked entirely, now comes through. One honest caveat: press releases are available but only as headlines and short summaries, several of them third-party, so we do not use them as evidence.");
    const head = ["Capability", "Old plan", "Ultimate", "What we found"];
    const rows = [
      ["Earnings-call transcripts (all 5 stocks)", "Refused", "Works", "MU 81 · TSLA 61 · GS 79 · MSFT 83 · SNDK 4 calls; ~50,000 characters each"],
      ["Micron and SanDisk financial data", "Refused", "Works", "statements, earnings, 10-K/10-Q notes"],
      ["More than 5 periods per request", "Refused", "Works", "12 quarters returned in one call"],
      ["Quarterly ratios, metrics, segments", "Refused", "Works", "previously annual only"],
      ["12-month SEC filing search", "Refused", "Works", "previously year-to-date only"],
      ["Press releases and stock news", "Refused", "Works*", "*headlines + short summaries only, often third-party: not used as evidence"]];
    const cw = [3.55, 1.25, 1.25, 6.08];
    const xs = cw.reduce((a, w, i) => (a.push(i ? a[i - 1] + cw[i - 1] : M), a), []);
    const Y0 = 1.65, RH = 0.66;
    rr(s, M, Y0, CW, 0.5, { fill: CARD2, noline: true, r: 0.05 });
    head.forEach((h, i) => txt(s, h, xs[i] + 0.15, Y0, cw[i] - 0.2, 0.5, { size: 11.5, bold: true, color: MUTED, valign: "middle" }));
    rows.forEach((r, j) => {
      const y = Y0 + 0.58 + j * (RH + 0.08);
      rr(s, M, y, CW, RH, { fill: CARD, noline: true, r: 0.05 });
      txt(s, r[0], xs[0] + 0.15, y, cw[0] - 0.2, RH, { size: 13, bold: true, valign: "middle" });
      pill(s, RED, xs[1] + 0.1, y + 0.17, cw[1] - 0.25, 0.32, r[1], 10.5);
      pill(s, r[2] === "Works" ? GREEN : AMBER, xs[2] + 0.1, y + 0.17, cw[2] - 0.25, 0.32, r[2], 10.5);
      txt(s, r[3], xs[3] + 0.15, y, cw[3] - 0.25, RH, { size: 11.5, color: SOFT, valign: "middle", lsm: 1.05 });
    });
    txt(s, "17 live checks on 9 Oct 2026; old-plan refusals recorded on 8 Oct 2026 (HTTP 402 'Restricted Endpoint').",
      M, 6.68, CW, 0.3, { size: 10.5, color: MUTED, italic: true });
  }

  // ------------------------------------------------------------------ 3. data added
  {
    const s = slide("3 · Data added", "Company evidence went from patchy to strong for all five stocks",
      "This shows what the research assistant can now read. Before the upgrade it held 18 documents; now it holds 45, with nearly 3,000 searchable passages. Micron and SanDisk went from a one-paragraph company profile to full financial statements, filing notes and four earnings calls each. Tesla, Goldman and Microsoft gained their last four earnings calls. The coverage grade is computed by a fixed rule, not by the AI: strong means at least four quarters of statements, filing text, and management commentary. All five are now strong. The one gap left is the SEC's own filing text, risk factors and management discussion, which needs a contact name we still have to set.");
    const data = [["MU", 1, 308, "Missing"], ["SNDK", 1, 453, "Missing"], ["TSLA", 383, 534, "Partial"], ["GS", 982, 1167, "Partial"], ["MSFT", 341, 503, "Partial"]];
    const X = M, BW = 7.65;
    rr(s, X, 1.6, BW, 5.15, { fill: CARD });
    txt(s, "SEARCHABLE PASSAGES PER STOCK: BEFORE → AFTER", X + 0.35, 1.83, BW - 0.7, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const LX = X + 1.15, MAXW = BW - 3.6, MAXV = 1550, Y1 = 2.35, STEP = 0.86;
    data.forEach(([sym, b, a, cov], i) => {
      const y = Y1 + i * STEP;
      txt(s, sym, X + 0.35, y, 0.8, 0.6, { size: 14, bold: true, valign: "middle" });
      const wb = Math.max(0.04, MAXW * b / MAXV), wa = MAXW * a / MAXV;
      rect(s, LX, y + 0.04, wb, 0.22, DIM);
      rect(s, LX, y + 0.32, wa, 0.26, GREEN, { ft: 10 });
      txt(s, `${b.toLocaleString()} → ${a.toLocaleString()}`, LX + wa + 0.12, y + 0.26, 1.3, 0.34, { size: 11.5, bold: true, color: INK, valign: "middle" });
      pill(s, cov === "Missing" ? RED : AMBER, X + BW - 1.75, y + 0.13, 0.78, 0.3, cov, 9);
      txt(s, "→", X + BW - 0.95, y + 0.13, 0.2, 0.3, { size: 12, color: MUTED, align: "center", valign: "middle" });
      pill(s, GREEN, X + BW - 0.72, y + 0.13, 0.62, 0.3, "Strong", 9);
    });
    rect(s, X + 0.35, 6.42, 0.22, 0.14, DIM);
    txt(s, "before", X + 0.65, 6.36, 0.8, 0.26, { size: 10, color: MUTED });
    rect(s, X + 1.45, 6.42, 0.22, 0.14, GREEN);
    txt(s, "after", X + 1.75, 6.36, 0.8, 0.26, { size: 10, color: MUTED });
    txt(s, "evidence grade", X + BW - 1.75, 6.36, 1.65, 0.26, { size: 10, color: MUTED, align: "center" });

    const RX = X + BW + 0.3, RW = W - M - RX;
    const big = [["18 → 45", "documents", BLUE], ["1,708 → 2,965", "searchable passages", BLUE], ["0 → 20", "earnings calls (763 passages)", GREEN],
      ["0 → 30", "statement rows each for MU and SNDK", GREEN]];
    big.forEach(([v, l, c], i) => {
      const y = 1.6 + i * 1.31;
      rr(s, RX, y, RW, 1.18, { fill: CARD2, noline: true });
      txt(s, v, RX + 0.3, y + 0.14, RW - 0.5, 0.5, { font: TF, size: 23, bold: true, color: c });
      txt(s, l, RX + 0.3, y + 0.68, RW - 0.5, 0.4, { size: 12, color: SOFT });
    });
  }

  // ------------------------------------------------------------------ 4. retest of failed jobs
  {
    const s = slide("4 · Retest of failed jobs", "Six of eight failed jobs fixed; two are data limits, not plan limits",
      "Here is every job that failed before the upgrade, retested. Transcripts, Micron and SanDisk data, and the unanswered research questions are all fixed. The retest also exposed two weaknesses in our own system, and we fixed both. First, questions about guidance were pulling in accounting-standard 'guidance' from the filing notes instead of what the CFO said on the call; questions about management now search the latest earnings call first. Second, our automatic number checker was raising false alarms, for example on capital spending, which FMP stores as a negative number; after fixing it, 23 of 24 answers pass and none of 130 checked figures conflict with the database. Two items remain and are not plan problems: SanDisk has too little weekly price history for weekly backtests until late 2027, and FMP's Goldman annual report has no text notes.");
    const head = ["Job", "Before", "After retest", "Status"];
    const rows = [
      ["Earnings-call transcripts (5 stocks)", "Refused by plan", "20 calls ingested, searchable", "Fixed", GREEN],
      ["MU and SNDK company data", "Refused by plan", "Statements, earnings, notes, calls", "Fixed", GREEN],
      ["Same 20 research questions", "10 of 20 'Not found'", "0 of 20 'Not found'", "Fixed", GREEN],
      ["Guidance / outlook questions", "Not found or wrong source", "Answered from CFO remarks on the call", "Fixed*", GREEN],
      ["Automatic number checks (24 answers)", "15 of 24 passed", "23 of 24 passed; 0 of 130 figures conflict", "Fixed*", GREEN],
      ["Micron 'Not found' on financials", "No data (plan)", "Answered from FMP statements", "Fixed", GREEN],
      ["SNDK weekly backtests (120)", "Fail: too little history", "Still fail: <150 weekly bars", "Data limit", AMBER],
      ["GS annual-report notes", "No text in FMP report", "Same on Ultimate", "Data limit", AMBER]];
    const cw = [3.55, 2.55, 4.4, 1.63];
    const xs = cw.reduce((a, w, i) => (a.push(i ? a[i - 1] + cw[i - 1] : M), a), []);
    const Y0 = 1.6, RH = 0.5;
    rr(s, M, Y0, CW, 0.44, { fill: CARD2, noline: true, r: 0.05 });
    head.forEach((h, i) => txt(s, h, xs[i] + 0.15, Y0, cw[i] - 0.2, 0.44, { size: 11.5, bold: true, color: MUTED, valign: "middle" }));
    rows.forEach((r, j) => {
      const y = Y0 + 0.5 + j * (RH + 0.06);
      rr(s, M, y, CW, RH, { fill: CARD, noline: true, r: 0.05 });
      txt(s, r[0], xs[0] + 0.15, y, cw[0] - 0.2, RH, { size: 12, bold: true, valign: "middle" });
      txt(s, r[1], xs[1] + 0.15, y, cw[1] - 0.2, RH, { size: 11.5, color: MUTED, valign: "middle" });
      txt(s, r[2], xs[2] + 0.15, y, cw[2] - 0.2, RH, { size: 11.5, color: SOFT, valign: "middle" });
      pill(s, r[4], xs[3] + 0.12, y + 0.1, cw[3] - 0.3, 0.3, r[3], 10);
    });
    txt(s, "*Fixed in our own code during the retest (retrieval and number checks), each with automated tests.",
      M, 6.65, CW, 0.3, { size: 10.5, color: MUTED, italic: true });
  }

  // ------------------------------------------------------------------ 5. what calls add
  {
    const s = slide("5 · What earnings calls add", "We can now compare what management said with what happened",
      "This is the real value of transcripts: management's own words, with the speaker and the call cited. SanDisk is the clearest 'said versus actual' example. On its third-quarter call it guided the next quarter to revenue of 7.75 to 8.25 billion dollars and earnings per share of 30 to 33 dollars; the quarter came in at 8.97 billion of revenue and 39.25 of earnings per share. Microsoft lowered its calendar 2026 capital spending expectation from about 190 to about 175 billion, after extending how long its data centres are depreciated. Tesla kept its 2026 capital spending at more than 25 billion and added up to 30 billion of debt facilities. Micron guided single-digit bit growth and double-digit cost declines and said it raised 2027 HBM pricing significantly. Goldman gave no numeric guidance at all, which is itself useful to know. Every line on this slide comes from a cited answer; none of it is from the AI's own memory.");
    const cards = [
      ["SNDK", "Said vs actual", GREEN, ["Q3 call guided Q4: revenue $7.75–8.25B, EPS $30–33", "Reported Q4: revenue $8.97B (FMP statements), non-GAAP EPS $39.25 (Q4 call)", "Guide beaten on both"], "CFO Luis Visoso · Q3 + Q4 FY2026 calls"],
      ["MSFT", "Guidance changed", BLUE, ["CY2026 capex: ~$190B (Q3 call) → ~$175B (Q4 call)", "after extending data-center useful life 15 → 25 years", "FY27: double-digit revenue and operating-income growth"], "CFO Amy Hood · Q3 + Q4 FY2026 calls"],
      ["TSLA", "Guidance held", AMBER, ["2026 capex 'more than $25B' on both calls", "New on Q2 call: debt facilities of up to $30B", "Free cash flow negative 'as previously guided'"], "CFO Vaibhav Taneja · Q1 + Q2 FY2026 calls"],
      ["MU", "Next-quarter guide", VIOLET, ["Q1 FY27: single-digit bit growth, double-digit cost declines", "HBM pricing for 2027 raised significantly", "FY27 R&D up 'more than $1 billion'"], "Q4 FY2026 call (Q&A)"],
      ["GS", "No numeric guidance", MUTED, ["Focus on 3–5 year earnings growth, not next quarter", "H1 efficiency ratio under the 60% target", "Absence of guidance is itself a finding"], "CEO David Solomon, CFO Denis Coleman · Q2 FY2026 call"]];
    const cwid = (CW - 4 * 0.2) / 5;
    cards.forEach(([sym, tag, c, lines, src], i) => {
      const x = M + i * (cwid + 0.2), y = 1.6;
      rr(s, x, y, cwid, 4.65, { fill: CARD });
      txt(s, sym, x + 0.2, y + 0.18, cwid - 0.4, 0.45, { font: TF, size: 20, bold: true });
      pill(s, c, x + 0.2, y + 0.7, cwid - 0.4, 0.3, tag, 10);
      s.addText(bl(lines), { x: x + 0.2, y: y + 1.18, w: cwid - 0.35, h: 2.75, isTextBox: true, margin: 0, fontFace: BF,
        fontSize: 11.5, color: SOFT, valign: "top", paraSpaceAfter: 7, lineSpacingMultiple: 1.04, fit: "none" });
      txt(s, src, x + 0.2, y + 3.95, cwid - 0.35, 0.6, { size: 9.5, color: MUTED, italic: true, lsm: 1.04 });
    });
    rr(s, M, 6.4, CW, 0.5, { fill: CARD2, noline: true });
    txt(s, "Every line comes from a cited, number-checked answer (transcript + speaker + date). Nothing is from the AI's own memory.",
      M + 0.3, 6.4, CW - 0.6, 0.5, { size: 12, color: SOFT, valign: "middle" });
  }

  // ------------------------------------------------------------------ 6. research run + next steps
  {
    const s = slide("6 · Research retest and next steps", "Richer context, same disciplined verdicts",
      "We re-ran the full five-stock research cycle on fresh price data with the new company evidence. The quantitative picture is unchanged in character: 120 strategies on ten timeframes gave 5,880 results; 609 beat buy and hold, 25 cleared the 50-point target and are on Watch, and none passed the robustness bar for Recommended. Tesla's pullback strategy on hourly bars still ranks first. That is by design: company evidence adds context and caution but never upgrades a trading verdict. Next steps: rotate both FMP keys, because they were shared in chat; set the SEC contact so we can add the official risk factors and management discussion; and run the planned 30 to 50 question evaluation now that the data is complete.");
    const head = ["#", "Stock", "Best strategy", "TF", "Return", "Buy & Hold", "Beat by", "Robustness", "Verdict"];
    const rows = [["1", "TSLA", "SIMPLE_PULLBACK", "1h", "+121.9%", "+71.4%", "+50.6 pts", "FRAGILE 75", "Watch"],
      ["2", "MU", "AI_GAP_UP_CONTINUATION", "1d", "+6,047.6%", "+5,806.6%", "+240.9 pts", "FRAGILE 63", "Watch"],
      ["3", "SNDK", "AI_ADX_TREND_STRENGTH_RIDE", "2h", "+3,728.2%", "+3,226.3%", "+501.9 pts", "FRAGILE 62", "Watch"],
      ["4", "GS", "AI_BULLISH_THRUST_VOLUME", "1w", "+970.3%", "+889.0%", "+81.4 pts", "FRAGILE 49", "Watch"],
      ["5", "MSFT", "AI_VOLUME_SURGE_CONT.", "8h", "+59.1%", "+25.0%", "+34.1 pts", "FRAGILE 78", "Rejected"]];
    const cw = [0.35, 0.7, 2.45, 0.42, 0.98, 0.98, 0.95, 1.0, 0.92];
    const TW = cw.reduce((a, b) => a + b, 0);
    const xs = cw.reduce((a, w, i) => (a.push(i ? a[i - 1] + cw[i - 1] : M), a), []);
    const Y0 = 1.6, RH = 0.5;
    rr(s, M, Y0, TW + 0.1, 0.42, { fill: CARD2, noline: true, r: 0.05 });
    head.forEach((h, i) => txt(s, h, xs[i] + 0.08, Y0, cw[i] - 0.1, 0.42, { size: 10, bold: true, color: MUTED, valign: "middle" }));
    rows.forEach((r, j) => {
      const y = Y0 + 0.48 + j * (RH + 0.06);
      rr(s, M, y, TW + 0.1, RH, { fill: CARD, noline: true, r: 0.05 });
      r.forEach((v, i) => {
        if (i === 8) return pill(s, v === "Watch" ? AMBER : RED, xs[i] + 0.04, y + 0.11, cw[i] - 0.04, 0.28, v, 9.5);
        txt(s, v, xs[i] + 0.08, y, cw[i] - 0.1, RH, { size: i === 1 ? 12.5 : 10.5, bold: i <= 1 || i === 6,
          color: i === 6 ? GREEN : i === 2 ? SOFT : INK, valign: "middle" });
      });
    });
    const sy = Y0 + 0.48 + 5 * (RH + 0.06) + 0.2;
    const st = [["5,880", "results"], ["609", "beat Buy & Hold"], ["25", "Watch"], ["0", "Recommended"]];
    const SW = (TW + 0.1 - 3 * 0.15) / 4;
    st.forEach(([v, l], i) => {
      const x = M + i * (SW + 0.15);
      rr(s, x, sy, SW, 1.0, { fill: CARD2, noline: true });
      txt(s, v, x, sy + 0.08, SW, 0.48, { font: TF, size: 22, bold: true, color: i === 3 ? RED : i === 2 ? AMBER : INK, align: "center" });
      txt(s, l, x, sy + 0.6, SW, 0.3, { size: 10.5, color: MUTED, align: "center" });
    });
    txt(s, "120 strategies x 10 timeframes x 5 stocks · data as of 8 Oct 2026 · verdicts from the fixed FTMO policy",
      M, sy + 1.12, TW + 0.1, 0.3, { size: 10, color: MUTED, italic: true });

    const X = M + TW + 0.45, BW = W - M - X;
    rr(s, X, 1.6, BW, 5.15, { fill: CARD });
    txt(s, "NEXT STEPS", X + 0.3, 1.83, BW - 0.6, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const nx = [["key", RED, "Rotate FMP keys", "both keys were shared in chat; update .env after"],
      ["search", GOLD, "Set SEC contact", "adds official Risk Factors and MD&A text"],
      ["list", BLUE, "Run A/B evaluation", "30–50 questions now that data is complete"],
      ["rotate", VIOLET, "Keep ingesting", "new calls are picked up incrementally"]];
    for (const [i, [k, c, t, d]] of nx.entries()) {
      const y = 2.3 + i * 1.08;
      await dot(s, k, X + 0.25, y, 0.52, c);
      txt(s, t, X + 0.95, y - 0.02, BW - 1.1, 0.32, { size: 13, bold: true });
      txt(s, d, X + 0.95, y + 0.3, BW - 1.1, 0.6, { size: 11, color: MUTED, lsm: 1.05 });
    }
  }

  await pres.writeFile({ fileName: OUT });
  console.log("wrote", OUT);
}
build().catch((e) => { console.error(e); process.exit(1); });
