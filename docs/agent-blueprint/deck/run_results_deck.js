// Fixed 5-stock research run results — 5 slides.
// Every number is from run agent-20261008-172755-5dcc (research_agent/state: leaderboard CSV, report,
// company_research_log, factcheck re-check). Charts are native shapes (Keynote drops pptxgenjs charts).
const pptxgen = require("pptxgenjs");
const React = require("react");
const RDS = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa6");

const OUT = process.argv[2] || "run.pptx";

const BG = "0B1220", CARD = "131D2E", CARD2 = "1A2740", LINE = "2A3A57";
const INK = "F3F5F8", SOFT = "C9D2DE", MUTED = "93A0B4", DIM = "66758E", GOLD = "E3B04B";
const GREEN = "34C77B", AMBER = "F2A93B", RED = "F0625A", BLUE = "5B9BFF", VIOLET = "A78BFA";
const TF = "Georgia", BF = "Arial";
const W = 13.333, M = 0.6, CW = W - 2 * M;
const RUN = "agent-20261008-172755-5dcc";

const ICONS = { chart: "FaChartLine", db: "FaDatabase", search: "FaMagnifyingGlass", flask: "FaFlask",
  shield: "FaShieldHalved", list: "FaListOl", file: "FaFileLines", robot: "FaRobot", check: "FaCircleCheck",
  xmark: "FaCircleXmark", scale: "FaScaleBalanced", gears: "FaGears", warn: "FaTriangleExclamation",
  key: "FaKey", mic: "FaMicrophoneLines", rotate: "FaRotate", filter: "FaFilter", layers: "FaLayerGroup" };
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
pres.title = "FTMO AI Research: fixed 5-stock run results";

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
  txt(s, `FTMO AI Research · fixed 5-stock run ${RUN} · research only, no trading`, M, 7.05, 9, 0.25, { size: 8.5, color: DIM });
  txt(s, String(n), W - M - 0.6, 7.05, 0.6, 0.25, { size: 8.5, color: DIM, align: "right" });
  s.addNotes(notes);
  return s;
}
const VCOL = { Watch: AMBER, Rejected: RED, Recommended: GREEN };
const COV = { Partial: AMBER, Missing: RED, Strong: GREEN };

async function build() {
  // ------------------------------------------------------------------ 1. at a glance
  {
    const s = slide("1 · The run at a glance", "5 stocks, 5,047 tests: no strategy earned Recommended",
      "One complete research cycle on exactly five stocks: MU, SNDK, TSLA, GS and MSFT. The agent first looked at the market data, then asked the company RAG twenty questions, then tested every strategy in the library, 97 existing plus 6 designed by the AI, on all ten timeframes, each against simply buying and holding the stock. 5,047 results in total. 495 beat buy and hold, 17 cleared the 50-point target and are Watch, and none passed the robustness bar for Recommended. The run took five and a half minutes and cost 93 cents in Claude usage. Most backtests were new; 1,160 were reused from earlier runs. The 103 failures are all SNDK on weekly bars, because SNDK only listed in February 2025.");
    const stats = [["5", "stocks", GOLD], ["103", "strategies", BLUE], ["10", "timeframes", VIOLET], ["5,047", "results", INK],
      ["495", "beat Buy & Hold", GREEN], ["17", "Watch", AMBER], ["0", "Recommended", RED]];
    txt(s, "MU · SNDK · TSLA · GS · MSFT, each tested on every strategy x timeframe against Buy & Hold on the same bars. Verdicts come from the fixed FTMO policy; the company RAG adds context beside them.",
      M, 1.55, 6.6, 0.85, { size: 14.5, color: SOFT, lsm: 1.12 });
    stats.forEach(([v, l, c], i) => {
      const col = i % 4, row = Math.floor(i / 4);
      const x = M + col * 1.66, y = 2.6 + row * 1.42;
      rr(s, x, y, 1.52, 1.25, { fill: CARD });
      txt(s, v, x, y + 0.14, 1.52, 0.55, { font: TF, size: 27, bold: true, color: c, align: "center" });
      txt(s, l, x + 0.05, y + 0.74, 1.42, 0.4, { size: 11, color: MUTED, align: "center" });
    });
    rr(s, M + 3 * 1.66, 4.02, 1.52, 1.25, { fill: CARD2 });
    txt(s, "5.6 min\n$0.93", M + 3 * 1.66, 4.14, 1.52, 0.75, { font: TF, size: 17, bold: true, color: INK, align: "center", lsm: 1.05 });
    txt(s, "duration · cost", M + 3 * 1.66 + 0.05, 4.9, 1.42, 0.3, { size: 11, color: MUTED, align: "center" });
    txt(s, [{ text: "Backtests:  ", options: { color: MUTED } }, { text: "3,887 new · 1,160 reused · 103 failed (SNDK weekly: too little history)", options: { color: SOFT } }],
      M, 5.6, 6.6, 0.35, { size: 12.5 });
    txt(s, [{ text: "AI strategies:  ", options: { color: MUTED } }, { text: "6 of the 103 were designed by the agent (3 this run, 3 in a superseded first attempt)", options: { color: SOFT } }],
      M, 6.0, 6.6, 0.55, { size: 12.5, lsm: 1.05 });

    const X = 7.45, BW = W - M - X;
    rr(s, X, 1.55, BW, 5.2, { fill: CARD });
    txt(s, "WORKFLOW", X + 0.35, 1.78, 3, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const steps = [["chart", BLUE, "Market & data context", "daily screen + recent returns, 5 stocks"],
      ["search", GOLD, "Company research (RAG)", "20 cited questions, read-only"],
      ["flask", VIOLET, "Strategies", "97 existing + 6 AI-designed"],
      ["scale", GREEN, "Backtest vs Buy & Hold", "every strategy x 10 timeframes"],
      ["shield", AMBER, "Robustness validation", "top 3 per stock, 15 graded"],
      ["list", INK, "Ranking + report", "fixed policy; context never changes it"]];
    for (const [i, [k, c, t, d]] of steps.entries()) {
      const y = 2.2 + i * 0.74;
      await dot(s, k, X + 0.35, y, 0.5, c);
      txt(s, t, X + 1.05, y + 0.0, BW - 1.3, 0.28, { size: 13.5, bold: true });
      txt(s, d, X + 1.05, y + 0.28, BW - 1.3, 0.28, { size: 11, color: MUTED });
    }
  }

  // ------------------------------------------------------------------ 2. leaderboard
  {
    const s = slide("2 · Final leaderboard", "Best result per stock: four Watch, one Rejected",
      "This is the ranked leaderboard: the single best strategy and timeframe per stock, ranked by the fixed FTMO policy. All five best results beat buy and hold, but every one is graded FRAGILE, so none reaches Recommended. TSLA ranks first: a simple pullback rule on the hourly chart made 123 percent against 73 percent for holding. MSFT is Rejected because it beat buy and hold by 32.5 points, short of the 50-point target. Note the coverage column: MU and SNDK have no company evidence, because our FMP plan blocks those tickers. Across all 5,047 results only one strategy was graded MODERATE or better, GS mean reversion on five-minute bars, and it is Rejected because it beat buy and hold by only 25 points.");
    const head = ["#", "Stock", "Best strategy", "TF", "Return", "Buy & Hold", "Beat by", "Robustness", "Verdict", "RAG coverage"];
    const rows = [
      ["1", "TSLA", "SIMPLE_PULLBACK", "1h", "+123.1%", "+72.6%", "+50.5 pts", "FRAGILE 75", "Watch", "Partial"],
      ["2", "SNDK", "DONCHIAN_BREAKOUT", "15m", "+84.3%", "+33.1%", "+51.2 pts", "FRAGILE 75", "Watch", "Missing"],
      ["3", "MU", "AI_VOL_SQUEEZE_BREAKOUT", "1w", "+1,212.7%", "+1,140.2%", "+72.5 pts", "FRAGILE 63", "Watch", "Missing"],
      ["4", "GS", "AI_BULLISH_THRUST_VOLUME", "1w", "+970.3%", "+890.1%", "+80.2 pts", "FRAGILE 49", "Watch", "Partial"],
      ["5", "MSFT", "AI_VOLUME_SURGE_CONT.", "8h", "+59.1%", "+26.6%", "+32.5 pts", "FRAGILE 78", "Rejected", "Partial"]];
    const cw = [0.45, 0.85, 2.85, 0.6, 1.25, 1.25, 1.15, 1.35, 1.15, 1.23];
    const xs = cw.reduce((a, w, i) => (a.push(i ? a[i - 1] + cw[i - 1] : M), a), []);
    const Y0 = 1.65, RH = 0.62;
    rr(s, M, Y0, CW, 0.5, { fill: CARD2, noline: true, r: 0.05 });
    head.forEach((h, i) => txt(s, h, xs[i] + 0.12, Y0, cw[i] - 0.15, 0.5, { size: 11, bold: true, color: MUTED, valign: "middle" }));
    rows.forEach((r, j) => {
      const y = Y0 + 0.58 + j * (RH + 0.08);
      rr(s, M, y, CW, RH, { fill: CARD, noline: true, r: 0.05 });
      r.forEach((v, i) => {
        if (i === 8) return pill(s, VCOL[v], xs[i] + 0.08, y + 0.15, cw[i] - 0.2, 0.32, v, 10.5);
        if (i === 9) return pill(s, COV[v], xs[i] + 0.08, y + 0.15, cw[i] - 0.16, 0.32, v, 10.5);
        txt(s, v, xs[i] + 0.12, y, cw[i] - 0.15, RH, { size: i === 1 ? 14 : 12.5, bold: i <= 1 || i === 6,
          color: i === 6 ? GREEN : i === 2 ? SOFT : INK, valign: "middle", font: i === 2 ? BF : BF });
      });
    });
    const by = Y0 + 0.58 + 5 * (RH + 0.08) + 0.2;
    rr(s, M, by, CW, 0.95, { fill: GOLD, ft: 88, line: GOLD });
    txt(s, [{ text: "Only MODERATE grade in 5,047 results:  ", options: { bold: true, color: GOLD } },
      { text: "GS MEAN_REVERSION 5m made +10.0% while holding lost 15.0%, a +25 pt edge that passed the robustness tests. It is still Rejected: the policy needs +50 pts.", options: { color: INK } }],
      M + 0.3, by, CW - 0.6, 0.95, { size: 13, valign: "middle", lsm: 1.08 });
  }

  // ------------------------------------------------------------------ 3. why no Recommended
  {
    const s = slide("3 · Quantitative evidence", "Edges exist, but none survives the robustness gates",
      "Why is nothing Recommended? Recommended needs two things at once: beat buy and hold by at least 50 points and reach a MODERATE robustness grade. The funnel on the left shows how fast the results thin out. On the right, the five best results: four clear the 50-point line, but each fails a different robustness gate. TSLA and SNDK make about a quarter of their profit from a single trade, and the edge disappears without the top three trades. MU wins in-sample but loses out-of-sample by 34 points. GS's weekly result won none of the five walk-forward folds. MSFT holds up best on robustness but its edge is only 32.5 points.");
    // funnel: left-aligned bars, number inside, label beside
    const F = [["5,047", "strategy x timeframe results", 2.9, BLUE], ["495", "beat Buy & Hold", 2.35, GREEN],
      ["17", "beat it by 50+ pts  →  Watch", 1.8, AMBER], ["0", "also robust (MODERATE+)  →  Recommended", 1.25, RED]];
    txt(s, "FROM TESTS TO VERDICTS", M, 1.62, 5, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    F.forEach(([v, l, w, c], i) => {
      const y = 2.05 + i * 1.12;
      rr(s, M, y, w, 0.9, { fill: c, ft: 78, line: c });
      txt(s, v, M, y, w, 0.9, { font: TF, size: 24, bold: true, color: c, align: "center", valign: "middle" });
      txt(s, l, M + 3.1, y, 2.75, 0.9, { size: 12.5, color: SOFT, valign: "middle", lsm: 1.05 });
    });
    // bars: beat-by vs 50-pt target
    const X = 6.55, BW = W - M - X;
    rr(s, X, 1.55, BW, 5.2, { fill: CARD });
    txt(s, "BEST RESULT PER STOCK: BEAT BUY & HOLD BY (PTS)", X + 0.35, 1.78, BW - 0.7, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const data = [["TSLA", 50.54, "FRAGILE 75", "25% of profit from one trade; edge gone without top 3"],
      ["SNDK", 51.17, "FRAGILE 75", "26% of profit from one trade; edge gone without top 3"],
      ["MU", 72.5, "FRAGILE 63", "fails out-of-sample (−33.9 pts vs Buy & Hold)"],
      ["GS", 80.24, "FRAGILE 49", "won 0 of 5 walk-forward folds"],
      ["MSFT", 32.49, "FRAGILE 78", "below the 50-pt target → Rejected"]];
    const LX = X + 1.05, MAXW = BW - 2.25, MAXV = 90, Y1 = 2.6, STEP = 0.82;
    data.forEach(([sym, v, g, why], i) => {
      const y = Y1 + i * STEP;
      txt(s, sym, X + 0.35, y, 0.7, 0.36, { size: 13.5, bold: true, valign: "middle" });
      const w = MAXW * v / MAXV, c = v >= 50 ? AMBER : RED;
      rect(s, LX, y + 0.04, w, 0.3, c, { ft: 15 });
      txt(s, `+${v.toFixed(1)}`, LX + w + 0.08, y, 0.8, 0.36, { size: 12, bold: true, color: c, valign: "middle" });
      txt(s, `${g} · ${why}`, LX, y + 0.39, BW - 1.4, 0.3, { size: 10.5, color: MUTED });
    });
    const tx = LX + MAXW * 50 / MAXV;
    for (let i = 0; i < data.length; i++)                     // target marks on the bar rows only, never over captions
      s.addShape(pres.shapes.LINE, { x: tx, y: Y1 + i * STEP - 0.04, w: 0, h: 0.46, line: { color: GOLD, width: 1.75, dashType: "dash" } });
    txt(s, "50-pt target", tx - 0.6, Y1 - 0.34, 1.2, 0.25, { size: 10, bold: true, color: GOLD, align: "center" });
  }

  // ------------------------------------------------------------------ 4. company evidence
  {
    const s = slide("4 · Company evidence (RAG)", "RAG found real context for 3 stocks, nothing for 2",
      "The company RAG answered twenty questions, four per stock, from data stored in our database. It adds things a backtest cannot see. For Tesla: the latest quarter beat on revenue but missed on EPS, Tesla holds a two-billion-dollar equity investment in SpaceX, a company affiliated with its CEO, and it faces ongoing information requests from the SEC, DOJ, NHTSA and NTSB. For Goldman it caught a conflict inside FMP's own data: second-quarter revenue is 38.4 billion in the income statement but 20.3 billion in the earnings feed. For Microsoft: a revenue and EPS beat, cybersecurity as the main disclosed risk, and a 13-billion-dollar OpenAI funding commitment. Micron and SanDisk returned nothing beyond a company profile, because our FMP plan does not cover those tickers. Half the questions, including every guidance question, came back Not found, and the system said so instead of guessing.");
    const cards = [
      ["TSLA", "Partial", ["Q2 FY2026 revenue $28.236B; EPS 0.33 vs 0.50 consensus (miss)", "$2.00B equity investment in SpaceX (CEO-affiliated)", "$87M Megapack revenue from SpaceX in Q1 FY2026", "SEC, DOJ, NHTSA, NTSB information requests"]],
      ["GS", "Partial", ["Q2 FY2026 revenue conflict inside FMP: $38.431B (income statement) vs $20.338B (earnings feed)", "EPS 20.98 vs 14.47 consensus (beat)", "Legacy 1MDB matter in the filing notes"]],
      ["MSFT", "Partial", ["Q4 FY2026 revenue $90.007B; EPS 4.74 vs 4.24 consensus (beat)", "Cybersecurity: the main disclosed risk", "$13B OpenAI funding commitment, $11.6B funded"]],
      ["MU, SNDK", "Missing", ["FMP plan blocks both tickers (HTTP 402)", "Only a company profile: no financials, risks, guidance or management evidence"]]];
    const CWD = (CW - 3 * 0.25) / 4;
    cards.forEach(([sym, cov, lines], i) => {
      const x = M + i * (CWD + 0.25), y = 1.6;
      rr(s, x, y, CWD, 3.95, { fill: CARD });
      txt(s, sym, x + 0.25, y + 0.22, CWD - 1.35, 0.4, { font: TF, size: 17, bold: true });
      pill(s, COV[cov], x + CWD - 1.07, y + 0.27, 0.85, 0.3, cov, 10);
      s.addText(lines.map((t, j) => ({ text: t, options: { bullet: { indent: 12 }, breakLine: j < lines.length - 1 } })),
        { x: x + 0.22, y: y + 0.8, w: CWD - 0.4, h: 3.0, isTextBox: true, margin: 0, fontFace: BF, fontSize: 12,
          color: cov === "Missing" ? MUTED : SOFT, valign: "top", paraSpaceAfter: 7, lineSpacingMultiple: 1.05, fit: "none" });
    });
    const stats = [["20", "questions asked", BLUE], ["10", "Not found (all guidance)", AMBER], ["0", "DB conflicts after audit", GREEN], ["0", "claims from model memory", GREEN]];
    const SW = (CW - 3 * 0.25) / 4;
    stats.forEach(([v, l, c], i) => {
      const x = M + i * (SW + 0.25), y = 5.75;
      rr(s, x, y, SW, 0.95, { fill: CARD2, noline: true });
      txt(s, v, x + 0.25, y, 0.9, 0.95, { font: TF, size: 26, bold: true, color: c, valign: "middle" });
      txt(s, l, x + 1.1, y, SW - 1.25, 0.95, { size: 12, color: SOFT, valign: "middle" });
    });
  }

  // ------------------------------------------------------------------ 5. conclusion
  {
    const s = slide("5 · Conclusion and next steps", "Verdict: HYBRID. FMP numbers plus document RAG",
      "What did RAG add? Real company context that backtesting cannot provide: filing-disclosed litigation and risks, related-party and governance facts, and a data conflict inside FMP. It added caution for Tesla and Goldman. What it did not do: it changed no verdict and no rank, and it had no guidance or management commentary for any stock, while Micron and SanDisk had nothing at all. So the recommendation is a hybrid: keep FMP's structured numbers in database tables as the authority, and use document retrieval beside them for filing text. The next steps fill the gaps: set the SEC contact so 10-K and 10-Q text, risk factors and earnings releases can be ingested, upgrade the FMP plan for transcripts and full ticker coverage, then rerun this exact five-stock cycle and the A versus B evaluation.");
    const X2 = M + CW / 2 + 0.15, HW = CW / 2 - 0.15;
    rr(s, M, 1.6, HW, 2.55, { fill: CARD });
    await dot(s, "check", M + 0.3, 1.82, 0.55, GREEN);
    txt(s, "What RAG added", M + 1.05, 1.88, HW - 1.3, 0.4, { size: 16, bold: true });
    s.addText(["Filing-disclosed risks and litigation a backtest cannot see",
      "Governance facts: TSLA's CEO-affiliated SpaceX stake",
      "A data-quality flag: GS revenue disagrees inside FMP",
      "Honest gaps: Not found instead of guessed answers"].map((t, j, a) => ({ text: t, options: { bullet: { indent: 12 }, breakLine: j < a.length - 1 } })),
      { x: M + 0.35, y: 2.45, w: HW - 0.6, h: 2.0, isTextBox: true, margin: 0, fontFace: BF, fontSize: 13.5, color: SOFT, paraSpaceAfter: 9, valign: "top", fit: "none" });
    rr(s, X2, 1.6, HW, 2.55, { fill: CARD });
    await dot(s, "xmark", X2 + 0.3, 1.82, 0.55, RED);
    txt(s, "What it did not do", X2 + 1.05, 1.88, HW - 1.3, 0.4, { size: 16, bold: true });
    s.addText(["Change any verdict or rank (by design)",
      "Provide guidance or management commentary for any stock",
      "Cover MU or SNDK (FMP plan blocks both tickers)",
      "Prove decision-level value: that needs the A/B evaluation"].map((t, j, a) => ({ text: t, options: { bullet: { indent: 12 }, breakLine: j < a.length - 1 } })),
      { x: X2 + 0.35, y: 2.45, w: HW - 0.6, h: 2.0, isTextBox: true, margin: 0, fontFace: BF, fontSize: 13.5, color: SOFT, paraSpaceAfter: 9, valign: "top", fit: "none" });
    txt(s, "NEXT STEPS", M, 4.5, 4, 0.3, { size: 10.5, bold: true, color: MUTED, cs: 1.5 });
    const nx = [["key", GOLD, "Set SEC_USER_AGENT", "Ingest 10-K/10-Q text, Risk Factors, 8-K earnings releases"],
      ["mic", VIOLET, "Upgrade the FMP plan", "Earnings-call transcripts, and MU + SNDK coverage"],
      ["rotate", BLUE, "Rerun and evaluate", "Same 5-stock cycle, then the A vs B evaluation"]];
    const NW = (CW - 2 * 0.25) / 3;
    for (const [i, [k, c, t, d]] of nx.entries()) {
      const x = M + i * (NW + 0.25), y = 4.88;
      rr(s, x, y, NW, 1.55, { fill: CARD2, noline: true });
      await dot(s, k, x + 0.25, y + 0.32, 0.55, c);
      txt(s, t, x + 0.98, y + 0.26, NW - 1.15, 0.35, { size: 15, bold: true });
      txt(s, d, x + 0.98, y + 0.66, NW - 1.15, 0.7, { size: 12.5, color: MUTED, lsm: 1.05 });
    }
  }

  await pres.writeFile({ fileName: OUT });
  console.log("wrote", OUT);
}
build().catch((e) => { console.error(e); process.exit(1); });
