"""Structured financial data in answers: routing, evidence, and fact checking.

route()     decides whether a question needs structured FMP numbers, document
            retrieval, or both (deterministic keyword rules, unit-tested).
evidence()  turns rows from rag.financial_statements / rag.earnings /
            rag.company_data into numbered evidence blocks with their source.
factcheck() pairs every figure in the answer with the metric it describes and
            compares it to the database value. A mismatch is reported and the
            database value is appended to the answer: the LLM cannot override it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rag import guard
from rag.config import parse_period, period_label
from rag.fmp import repo

# question words -> FMP fields (first field found in a row wins for display; all are checked)
METRICS: List[Tuple[str, str, Tuple[str, ...]]] = [
    (r"\brevenues?\b|\bsales\b|\btop[- ]line\b", "revenue", ("revenue", "revenue_estimated")),
    (r"\bgross (?:profit|margin)s?\b", "gross profit", ("grossProfit",)),
    (r"\boperating (?:income|profit|margin)s?\b|\bebit\b", "operating income", ("operatingIncome",)),
    (r"\bnet (?:income|profit|earnings)\b|\bbottom[- ]line\b", "net income", ("netIncome",)),
    (r"\beps\b|\bearnings per share\b", "EPS", ("epsDiluted", "eps", "eps_actual", "eps_estimated")),
    (r"\bebitda\b", "EBITDA", ("ebitda",)),
    (r"\br&d\b|\bresearch and development\b", "R&D expense", ("researchAndDevelopmentExpenses",)),
    (r"\bsg&a\b|\bselling, general\b", "SG&A", ("sellingGeneralAndAdministrativeExpenses",)),
    (r"\bfree cash flow\b|\bfcf\b", "free cash flow", ("freeCashFlow",)),
    (r"\boperating cash flow\b|\bcash (?:flow )?from operations\b", "operating cash flow", ("operatingCashFlow",)),
    (r"\bcapex\b|\bcapital expenditures?\b", "capital expenditure", ("capitalExpenditure",)),
    (r"\bbuybacks?\b|\brepurchases?\b", "share repurchases", ("commonStockRepurchased",)),
    (r"\bdividends?\b", "dividends paid", ("commonDividendsPaid",)),
    (r"\bcash\b(?! flow)", "cash", ("cashAndCashEquivalents", "cashAndShortTermInvestments")),
    (r"\bdebt\b", "debt", ("totalDebt", "longTermDebt", "netDebt")),
    (r"\btotal assets\b|\bassets\b", "total assets", ("totalAssets",)),
    (r"\bliabilities\b", "total liabilities", ("totalLiabilities",)),
    (r"\bequity\b", "shareholders' equity", ("totalStockholdersEquity",)),
    (r"\binventor(?:y|ies)\b", "inventory", ("inventory",)),
    (r"\bshares outstanding\b|\bshare count\b|\bdiluted shares\b", "diluted shares", ("weightedAverageShsOutDil",)),
    (r"\bincome tax(?:es)?\b|\btax expense\b", "income tax expense", ("incomeTaxExpense",)),
    (r"\binterest expense\b", "interest expense", ("interestExpense",)),
]
STATEMENT_PREFERENCE = ("income", "balance", "cashflow")
CORE = ("revenue", "grossProfit", "operatingIncome", "netIncome", "epsDiluted", "operatingCashFlow", "freeCashFlow")
DOC_WORDS = re.compile(
    r"\b(say|said|says|mention\w*|comment\w*|discuss\w*|explain\w*|why|management|ceo|cfo|guid\w*|outlook|"
    r"expect\w*|plan\w*|strateg\w*|risks?|demand|headwind\w*|tailwind\w*|litigation|legal|lawsuit\w*|tariff\w*|"
    r"competition|competitive|describe\w*|business|segments?|polic\w*|notes?|contingenc\w*|regulat\w*|"
    r"supply|customers?|products?|acquisition\w*|debt terms|covenant\w*)\b", re.I)
NUMBER_WORDS = re.compile(r"\b(how much|how many|what (?:was|were|is|are) .{0,40}(?:revenue|income|eps|margin|cash|"
                          r"debt|sales|profit)|latest (?:revenue|results|numbers)|grow\w*|growth|"
                          r"year[- ]over[- ]year|yoy|beat|miss|surprise|numbers?|figures?|financials?)\b", re.I)
EARNINGS_WORDS = re.compile(r"\b(beat|miss\w*|surprise|consensus|estimat\w*|eps|earnings)\b", re.I)
ANALYST_WORDS = re.compile(r"\b(analysts?|consensus|street|estimat\w*|price target|rating)\b", re.I)
COMPARE = re.compile(r"\b(compar\w*|previous|prior|versus|vs\.?|change[sd]? (?:from|since)|last two|"
                     r"quarter[- ]over[- ]quarter|sequential\w*)\b", re.I)
YOY = re.compile(r"\b(year[- ]over[- ]year|yoy|a year (?:ago|earlier)|same quarter last year)\b", re.I)
ANNUAL = re.compile(r"\b(annual|full[- ]year|fiscal year|for the year)\b", re.I)


@dataclass
class Route:
    structured: bool
    documents: bool
    metrics: List[Tuple[str, Tuple[str, ...]]] = field(default_factory=list)
    reason: str = ""


def route(question: str) -> Route:
    found = [(label, fields) for pat, label, fields in METRICS if re.search(pat, question, re.I)]
    numeric = bool(found) or bool(NUMBER_WORDS.search(question))
    textual = bool(DOC_WORDS.search(question))
    structured = numeric
    documents = textual or not numeric
    why = []
    if found:
        why.append("metrics: " + ", ".join(lbl for lbl, _ in found))
    if textual:
        why.append(f"narrative words: {DOC_WORDS.search(question).group(0)!r}")
    if not why:
        why.append("no metric named -> documents")
    return Route(structured, documents, found, "; ".join(why))


# ------------------------------------------------------------------ evidence
def _fmt(v: Any) -> str:
    v = float(v)
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.4f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v)}"


def _periods(conn, symbol: str, question: str, fiscal_year: Optional[int], fiscal_quarter: Optional[str]):
    """(period_type, [(fy, fq), ...]) the question is about; newest first."""
    if not (fiscal_year or fiscal_quarter):
        fiscal_year, fiscal_quarter = parse_period(question)
    annual = fiscal_quarter == "FY" or bool(ANNUAL.search(question))
    ptype = "annual" if annual else "quarter"
    latest = repo.statements(conn, symbol, ptype, latest=8)
    have = []
    for r in latest:
        p = (r["fiscal_year"], r["fiscal_quarter"])
        if p not in have:
            have.append(p)
    if not have:
        return ptype, []
    if fiscal_year:
        fq = "FY" if annual else ("Q4" if fiscal_quarter in ("Q4", "FY") else fiscal_quarter)
        target = [(fiscal_year, fq)] if fq else [p for p in have if p[0] == fiscal_year][:1]
    else:
        target = [have[0]]
    out = list(target)
    if target and COMPARE.search(question):
        i = have.index(target[0]) if target[0] in have else -1
        if 0 <= i < len(have) - 1:
            out.append(have[i + 1])
    if target and YOY.search(question) and not annual:
        fy, fq = target[0]
        out.append((fy - 1, fq))
    return ptype, out


def evidence(conn, symbol: str, question: str, rt: Optional[Route] = None, fiscal_year: Optional[int] = None,
             fiscal_quarter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Evidence blocks: {'kind':'fact', 'text', 'source', 'source_url', 'facts': [{metric, field, period, value}]}."""
    rt = rt or route(question)
    symbol = symbol.upper()
    fields = [f for _, fs in rt.metrics for f in fs] or list(CORE)
    labels = {f: lbl for lbl, fs in rt.metrics for f in fs}
    ptype, periods = _periods(conn, symbol, question, fiscal_year, fiscal_quarter)
    items: List[Dict[str, Any]] = []
    for fy, fq in periods:
        rows = repo.statements(conn, symbol, ptype, fy, fq)
        if not rows:
            continue
        # a field in several statements (netIncome: income vs cash-flow, which includes minority
        # interests) is taken from the income statement first, then balance sheet, then cash flow
        rows = sorted(rows, key=lambda r: STATEMENT_PREFERENCE.index(r["statement"]))
        r0 = rows[0]
        lines, facts, urls = [], [], []
        for f in fields:
            for r in rows:
                v = r["data"].get(f)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    stmt = {"income": "income statement", "balance": "balance sheet",
                            "cashflow": "cash-flow statement"}[r["statement"]]
                    lines.append(f"{f} ({stmt}): {_fmt(v)} {r['reported_currency'] or ''}".rstrip())
                    facts.append({"metric": labels.get(f, f), "field": f, "period": r["period"], "value": float(v)})
                    urls.append(r["source_url"])
                    break
        if not lines:
            continue
        label = r0["period"] or period_label(fy, fq)
        kind = "quarter" if ptype == "quarter" else "fiscal year"
        header = (f"FMP financial statements: {symbol} {label} ({kind} ended {r0['period_end']}, "
                  f"filed {r0['filing_date']}). Figures in units of the reporting currency, as filed.")
        items.append({"kind": "fact", "text": header + "\n" + "\n".join(lines), "facts": facts,
                      "source": f"FMP {', '.join(sorted({u.split('/stable/')[1].split('?')[0] for u in urls}))}: "
                                f"{symbol} {label} (period ended {r0['period_end']}, filed {r0['filing_date']})",
                      "source_url": sorted(set(urls))[0], "period": label})
    if EARNINGS_WORDS.search(question) or not rt.metrics:
        for e in repo.earnings(conn, symbol, limit=2 if COMPARE.search(question) else 1):
            label = e["period"] or f"announced {e['announce_date']}"
            text = (f"FMP earnings feed: {symbol} results announced {e['announce_date']} ({label}). "
                    f"EPS actual {_fmt(e['eps_actual'])} vs consensus estimate {_fmt(e['eps_estimated'])}; "
                    f"revenue actual {_fmt(e['revenue_actual'])} vs consensus estimate "
                    f"{_fmt(e['revenue_estimated'])}. (FMP's EPS here can differ from GAAP diluted EPS.)")
            facts = [{"metric": "EPS", "field": "eps_actual", "period": label, "value": float(e["eps_actual"])}]
            if e["eps_estimated"] is not None:
                facts.append({"metric": "EPS", "field": "eps_estimated", "period": label, "estimate": True,
                              "value": float(e["eps_estimated"])})
            if e["revenue_actual"] is not None:
                facts.append({"metric": "revenue", "field": "revenue", "period": label,
                              "value": float(e["revenue_actual"])})
            if e["revenue_estimated"] is not None:
                facts.append({"metric": "revenue", "field": "revenue_estimated", "period": label, "estimate": True,
                              "value": float(e["revenue_estimated"])})
            items.append({"kind": "fact", "text": text, "facts": facts, "period": label,
                          "source": f"FMP earnings: {symbol} announced {e['announce_date']}",
                          "source_url": e["source_url"]})
    if ANALYST_WORDS.search(question):
        rows = repo.company(conn, symbol, "analyst_estimates_annual")
        if rows:
            lines = [f"fiscal year ending {r['record_key']}: revenueAvg {_fmt(r['data'].get('revenueAvg') or 0)}, "
                     f"epsAvg {_fmt(r['data'].get('epsAvg') or 0)}, analysts {r['data'].get('numAnalystsEps')}"
                     for r in rows[:3]]
            items.append({"kind": "fact", "facts": [], "period": "forward",
                          "text": f"FMP analyst consensus estimates for {symbol} (third-party analysts, NOT company "
                                  f"guidance):\n" + "\n".join(lines),
                          "source": f"FMP analyst-estimates (annual): {symbol}", "source_url": rows[0]["source_url"]})
    return items


# ------------------------------------------------------------------ fact check
ESTIMATE_CONTEXT = re.compile(r"\b(estimat\w*|consensus|expected|expectations?|forecast\w*|street)\b", re.I)
MINUS = ("-", "\u2212", "\u2013")
NEGATIVE_WORDS = re.compile(r"\b(negative|minus|outflow|burn(?:ed)?|deficit|loss of)\s*\(?$", re.I)


def _metric_spans(sentence: str) -> List[Tuple[int, int, str, Tuple[str, ...]]]:
    spans = []
    for pat, label, fields in METRICS:
        for m in re.finditer(pat, sentence, re.I):
            spans.append((m.start(), m.end(), label, fields))
    return sorted(spans)


# a figure introduced as a change ("increased by $5.8 billion", "an increase of $7.1 billion") is derived,
# not a stored value: the number guard still asks for its "(computed ...)" marker, the database check skips it
DELTA = re.compile(r"\b(increas\w*|decreas\w*|rose|rise|fell|grew|growth|declin\w*|gain\w*|drop\w*|jump\w*|"
                   r"chang\w*|difference|shortfall|beat|miss\w*|up|down)\s+(?:by|of)\s+"
                   r"(?:about|approximately|roughly|around|~)?\s*$", re.I)
ACTUAL_CONTEXT = re.compile(r"\b(actual|reported|was|were|came in|delivered|posted)\b", re.I)


ESTIMATE_AFTER = re.compile(r"^\s*(?:analyst\s+|consensus\s+)?(estimates?|consensus|expectations?|forecasts?)\b", re.I)
ACTUAL_AFTER = re.compile(r"^\s*\(?(actual|reported)\b", re.I)
# "X vs. Y", "X topped the Y estimate": X is the actual side of a comparison, Y the comparator
COMPARE_AFTER = re.compile(r"^\s*(?:\([^)]{0,30}\)\s*)?(vs\.?|versus|against|compared(?: with| to)?|topped|beat|beating|"
                           r"exceeded|missed|above|below|ahead of)\b", re.I)
COMPARATOR = re.compile(r"^\s*\)?\s*(vs\.?|versus|against|compared(?: with| to)?)\s*(?:a|an|the)?\s*$", re.I)
LIST_JOIN = re.compile(r"^[\s\w]{0,24}\b(and|as well as)\b[\s\w]{0,16}$|^\s*,\s*$", re.I)


def _is_estimate(window: str, following: str, prev_est: Optional[bool], sentence: str = "") -> bool:
    """Is this figure an analyst estimate? Decided on the text since the previous figure:
    a word right after the figure wins ("the $81.4B estimate", "33.42 actual"); then whichever of
    estimate/actual words came last in the window ("consensus estimate of 0.5" vs "actual EPS was 0.33");
    a figure joined to an estimate by "and" inherits it ("estimates of $87.6B in revenue and $4.24 in EPS")."""
    if ESTIMATE_AFTER.match(following):
        return True
    if ACTUAL_AFTER.match(following) or COMPARE_AFTER.match(following):
        return False
    if COMPARATOR.match(window):                              # second side of "X vs. Y" in an estimates context
        return bool(ESTIMATE_CONTEXT.search(sentence)) and not prev_est
    labelled = bool(ESTIMATE_AFTER.match(window))           # "...$81.4B estimate and EPS of": that word was the
    window = ESTIMATE_AFTER.sub("", window, count=1)          # previous figure's own label, not a list heading
    est = [m.end() for m in ESTIMATE_CONTEXT.finditer(window)]
    act = [m.end() for m in ACTUAL_CONTEXT.finditer(window)]
    if est or act:
        return bool(est) and (not act or est[-1] > act[-1])
    return bool(prev_est) if (LIST_JOIN.match(window) and not labelled) else False


# figures in non-money units are never statement values
UNIT_AFTER = re.compile(r"^\s*(GWh|GW|MWh|MW|kWh|units?|vehicles|cars|employees|people|weeks?|days?|months?|years?|"
                        r"quarters?|bits?|wafers?|times|x\b|basis points|bps)", re.I)
OUTFLOWS = {"capitalExpenditure", "commonStockRepurchased", "commonDividendsPaid"}   # stored negative by FMP


AFTER_GAP = re.compile(r"^\s*(?:in|of)?\s*$", re.I)          # "$87.6 billion in revenue", "$14.47 EPS"


PAIR_AFTER = re.compile(r"^\s*(?:vs\.?|versus|against)\s*(?:a|an|the)?\s*\$?\d[\d,]*(?:\.\d+)?\s?(?:[BMK]\b|billion|million)?\s*", re.I)


def _pair(spans, start: int, end: int, sentence: str):
    """The metric a figure describes: one directly after it ("$4.24 in EPS"), the one after its comparator
    ("$22.387B vs. $22.101B revenue"), else the last one before it."""
    pm = PAIR_AFTER.match(sentence[end:])
    if pm:
        for sp in spans:
            if sp[0] >= end + pm.end() and AFTER_GAP.match(sentence[end + pm.end():sp[0]]):
                return sp, True
    for sp in spans:
        if sp[0] >= end and AFTER_GAP.match(sentence[end:sp[0]]):
            return sp, True
    before = [sp for sp in spans if sp[1] <= start]
    return (before[-1], False) if before else (None, False)


# sentence ends, but not after "vs." / "approx." / "Inc." and similar abbreviations
SENTENCES = re.compile(r"(?<!\bvs\.)(?<!\bv\.)(?<!\bapprox\.)(?<!\bInc\.)(?<!\bCorp\.)(?<!\bCo\.)(?<!\bNo\.)"
                       r"(?<=[\.\!\?])\s+(?=[A-Z\-\*\(\[\$\d]|$)|\n+")


def factcheck(answer: str, items: Sequence[Dict[str, Any]], documents: Sequence[str] = ()) -> Dict[str, Any]:
    """Compare each figure tied to a metric with the database value(s) for that metric.

    Not database claims, so not conflicts: percentages, non-money units, '(computed ...)' figures, figures
    introduced as changes, and figures found verbatim in the document evidence (e.g. capex guidance quoted
    from an earnings call; the number guard checks those). A figure that equals the difference of two
    database values for the same metric (a beat vs estimate, a quarter-over-quarter change) is accepted as
    a verified derived figure. Returns {'checked', 'derived_skipped', 'derived_verified', 'doc_supported',
    'conflicts'}."""
    facts = [f for it in items for f in it.get("facts", [])]
    out = {"checked": 0, "derived_skipped": 0, "derived_verified": 0, "doc_supported": 0, "conflicts": []}
    if not facts:
        return out
    by_field: Dict[str, List[Dict[str, Any]]] = {}
    for f in facts:
        by_field.setdefault(f["field"], []).append(f)
    doc_vals = guard.evidence_values(documents) if documents else set()
    body = guard.COMPUTED.sub(lambda m: " " * len(m.group(0)), answer)
    for sentence in SENTENCES.split(body):
        spans = _metric_spans(sentence)
        if not spans:
            continue
        masked = guard.IGNORE.sub(lambda m: " " * len(m.group(0)), sentence)
        prev_end, prev_est = 0, None
        for m in guard.NUM.finditer(masked):
            window_from, prev_end = prev_end, m.end()
            n = guard.numbers(m.group(0))
            if not n:
                continue
            n = n[0]
            following = sentence[m.end():m.end() + 30]
            if n["pct"] or (n["plain"] and abs(n["value"]) <= 12 and not n["decimals"]) or UNIT_AFTER.match(following):
                continue
            near, after = _pair(spans, m.start(), m.end(), sentence)
            if near is None:
                continue
            window = sentence[window_from:m.start()]
            if DELTA.search(window):
                out["derived_skipped"] += 1
                continue
            est = _is_estimate(window, following, prev_est, sentence)
            prev_est = est
            family = [f for fld in near[3] for f in by_field.get(fld, [])]
            cands = [f for f in family if bool(f.get("estimate")) == est] or family
            if not cands:
                continue
            mentioned = [f for f in cands if f["period"] and f["period"] in sentence]
            cands = mentioned or cands
            a = n["value"] * n["scale"]
            lead = masked[max(0, m.start() - 1):m.start()]
            if a > 0 and (lead in MINUS or NEGATIVE_WORDS.search(masked[max(0, m.start() - 20):m.start()])):
                a = -a                                               # "-$1.09 billion", "negative $1.09 billion"
            close = lambda x, v: abs(x - v) <= 0.006 * abs(v) or (                                  # noqa: E731
                n["scale"] > 1 and round(v / n["scale"], n["decimals"]) == n["value"])
            if any(c["field"] in OUTFLOWS for c in cands):
                vals = [abs(c["value"]) for c in cands]; a = abs(a)                                # noqa: E702
            else:
                vals = [c["value"] for c in cands]
            out["checked"] += 1
            if any(close(a, v) for v in vals if v):
                continue
            diffs = {abs(x["value"] - y["value"]) for x in family for y in family if x is not y}
            if any(close(abs(a), d) for d in diffs if d):
                out["derived_verified"] += 1
                continue
            if doc_vals and guard._matches(n, doc_vals):
                out["doc_supported"] += 1
                continue
            out["conflicts"].append({"figure": m.group(0).strip(), "metric": near[2],
                                     "database": [{"period": c["period"], "field": c["field"], "value": c["value"]}
                                                  for c in cands]})
    return out


def correction_note(conflicts: Sequence[Dict[str, Any]]) -> str:
    lines = []
    for c in conflicts:
        db = "; ".join(f"{d['period']} {d['field']} = {_fmt(d['value'])}" for d in c["database"][:3])
        lines.append(f"- The answer's {c['metric']} figure {c['figure']} does not match FMP data ({db}). "
                     f"The database value is authoritative.")
    return "DATABASE CHECK FAILED:\n" + "\n".join(lines)
