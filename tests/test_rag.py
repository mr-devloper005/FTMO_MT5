"""RAG POC: offline tests (no SEC, no Claude, no database). PostgreSQL tests: tests/test_rag_postgres.py."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from rag import chunk, config, extract, guard, sec  # noqa: E402

TENQ = """<html><body>
<div style="display:none"><ix:header>hidden xbrl 999,999</ix:header></div>
<table><tr><td>Item 1.</td><td>Financial Statements</td><td>3</td></tr>
<tr><td>Item 2.</td><td>MD&A</td><td>20</td></tr><tr><td>Item 1A.</td><td>Risk Factors</td><td>40</td></tr></table>
<hr style="page-break-after:always"/>
<p>PART I. FINANCIAL INFORMATION</p>
<p>Item 2. Management's Discussion and Analysis of Financial Condition and Results of Operations</p>
<p>Results of Operations</p>
<p>Revenue increased 31% driven by higher HBM bit shipments and pricing across all end markets compared with the prior year period, while gross margin expanded on mix and cost reductions.</p>
<table><tr><td></td><td>Q3 2026</td><td>Q3 2025</td></tr>
<tr><td>Revenue</td><td>$</td><td>41,456</td><td>$</td><td>9,301</td></tr>
<tr><td>Gross margin</td><td>65.1</td><td>%</td><td>37.7</td><td>%</td></tr></table>
<hr style="page-break-after:always"/>
<p>PART II. OTHER INFORMATION</p>
<p>Item 1A. Risk Factors</p>
<p>Our business is subject to significant risks, including customer concentration, export restrictions on sales to certain customers in China, and volatility in memory pricing that could reduce our revenue and margins materially in any period.</p>
</body></html>"""


def test_extract_drops_hidden_xbrl_and_renders_tables_and_pages():
    lines = extract.html_to_lines(TENQ)
    assert not any("hidden xbrl" in ln for ln in lines)
    assert "Revenue | $41,456 | $9,301" in lines
    assert "Gross margin | 65.1% | 37.7%" in lines
    assert lines.count(extract.PAGE) == 2


def test_chunks_follow_part_and_item_structure_and_skip_toc():
    cs = chunk.chunk_lines(extract.html_to_lines(TENQ), "10-Q")
    secs = [c.section for c in cs]
    assert "Part I, Item 2. Management's Discussion and Analysis" in secs
    assert "Part II, Item 1A. Risk Factors" in secs           # Part II's 1A, not the 10-K's
    mdna = [c for c in cs if c.section.startswith("Part I, Item 2")]
    assert any(c.kind == "table" and "41,456" in c.text for c in mdna)
    assert all(c.subsection == "Results of Operations" for c in mdna)
    risk = next(c for c in cs if "Risk Factors" in c.section)
    assert risk.page == 3 and "export restrictions" in risk.text


def test_long_tables_repeat_their_header():
    rows = ["Segment | 2026 | 2025"] + [f"Line item {i} with a long descriptive name | {i},000 | {i},500"
                                         for i in range(40)]
    lines = ["Item 7. MD&A", "The following table shows segment results.", extract.TABLE, *rows, extract.END_TABLE]
    tables = [c for c in chunk.chunk_lines(lines, "10-K") if c.kind == "table"]
    assert len(tables) > 1
    assert all("Segment | 2026 | 2025" in t.text and t.text.startswith("The following table") for t in tables)
    assert all(chunk.words(t.text) <= chunk.TABLE_WORDS + 40 for t in tables)


def test_period_parsing():
    assert config.parse_period("MU revenue in Q3 FY2026?") == (2026, "Q3")
    assert config.parse_period("in fiscal Q2 2025") == (2025, "Q2")
    assert config.parse_period("2026 Q1 margins") == (2026, "Q1")
    assert config.parse_period("Q4 FY26 guidance") == (2026, "Q4")
    assert config.parse_period("risk factors in fiscal 2025") == (2025, "FY")
    assert config.parse_period("what does the Q4 10-K say") == (None, None)       # 10 is not a year
    assert config.parse_period("latest guidance") == (None, None)


def test_release_period_inference():
    periods = [("2026-05-28", 2026, "Q3"), ("2026-02-26", 2026, "Q2"), ("2025-08-28", 2025, "FY")]
    assert sec.period_for_release("2026-06-25", periods) == (2026, "Q3")
    assert sec.period_for_release("2025-09-23", periods) == (2025, "Q4")         # FY close -> Q4 release
    assert sec.period_for_release("2026-09-23", periods) == (2026, "Q4")         # 10-K not filed yet


def test_fiscal_periods_come_from_xbrl_accessions():
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        {"accn": "A1", "fy": 2026, "fp": "Q3", "form": "10-Q"},
        {"accn": "A2", "fy": 2025, "fp": "FY", "form": "10-K"},
        {"accn": "A3", "fy": 2025, "fp": "Q1", "form": "8-K"}]}}}}}
    assert sec.fiscal_periods(facts) == {"A1": (2026, "Q3"), "A2": (2025, "FY")}


def test_earnings_exhibits_from_filing_index():
    html = ('<table><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>'
            '<tr><td>1</td><td>8-K</td><td><a href="/Archives/x/mu-8k.htm">mu-8k.htm</a></td><td>8-K</td></tr>'
            '<tr><td>2</td><td>PRESS RELEASE</td><td><a href="/Archives/x/ex991.htm">ex991.htm</a></td><td>EX-99.1</td></tr>'
            '<tr><td>3</td><td>GRAPHIC</td><td><a href="/Archives/x/logo.jpg">logo.jpg</a></td><td>GRAPHIC</td></tr></table>')
    assert sec.earnings_exhibits(html) == ["ex991.htm"]


def test_sec_requires_a_declared_user_agent(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr(config, "REPO", tmp_path)                                  # no .env there
    with pytest.raises(config.MissingCredential):
        config.sec_user_agent()
    monkeypatch.setenv("SEC_USER_AGENT", "just-a-name")
    with pytest.raises(config.MissingCredential):
        config.sec_user_agent()
    monkeypatch.setenv("SEC_USER_AGENT", "Acme Research ops@acme.com")
    assert config.sec_user_agent() == "Acme Research ops@acme.com"


def test_number_guard_units_rounding_and_computed():
    ev = ["Revenue | $54,229 | $11,315 (in millions)", "Gross margin was 74.9%.", "EPS of $33.65"]
    g = guard.check("Revenue was $54.2 billion [1], up from $11.3 billion, gross margin 74.9% and EPS $33.65.", ev)
    assert g["unverified"] == [] and len(g["verified"]) == 4
    g = guard.check("Revenue was $61.0 billion in Q4 FY2026 [1].", ev)
    assert g["unverified"] == ["$61.0 billion"]
    g = guard.check("Revenue rose $42.9 billion (computed from [1] and [1]) in FY2026.", ev)
    assert g["unverified"] == [] and g["computed"]


def test_not_found_and_citation_parsing():
    from rag import answer
    assert answer.is_not_found("Not found in the available documents.")
    assert not answer.is_not_found("Revenue was $1 [1].")
    assert answer.cited_numbers("A [1][3]. B [2, 4]. C [9].", 4) == [1, 2, 3, 4]


def test_rag_is_isolated_from_trading_code():
    """No module in rag/ may import the trading system, the research agent or any broker code."""
    banned = ("ftmo_mt5", "research_agent", "broker_hub", "mt5_api_server", "mcp_server", "MetaTrader5",
              "tastytrade", "ib_insync", "ib_async", "webull", "systematic", "execution")
    for py in (REPO / "rag").rglob("*.py"):                      # includes rag/fmp/
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for n in names:
                assert not n.split(".")[0] in banned, f"{py.name} imports {n}"


def test_answer_model_gets_no_tools():
    src = (REPO / "rag" / "answer.py").read_text()
    assert "tools=[]" in src and "mcp_servers={}" in src and "strict_mcp_config=True" in src
    assert "setting_sources=[]" in src and "max_turns=1" in src


def test_eval_grading_is_mechanical():
    from rag import evaluate as ev
    q = {"facts": [{"num": 41456e6}, {"any": ["HBM", "high-bandwidth memory"]}],
         "source": {"form": "8-K", "fiscal_year": 2026, "fiscal_quarter": "Q3"},
         "period": {"fiscal_year": 2026, "fiscal_quarter": "Q3"}}
    good = {"answer": "Revenue was $41.5 billion, driven by HBM [1].", "not_found": False}
    assert ev.grade_answer(q, good)
    assert not ev.grade_answer(q, {"answer": "Revenue was $45 billion, driven by HBM [1].", "not_found": False})
    assert ev.grade_answer({"unanswerable": True}, {"answer": "Not found in the available documents.", "not_found": True})
    hit = {"form": "8-K", "fiscal_year": 2026, "fiscal_quarter": "Q3", "text": "Revenue | $41,456 | $9,301"}
    r = {**good, "hits": [hit], "citations": [{"n": 1}], "guard": {"unverified": []},
         "filters": {"fiscal_year": 2026, "fiscal_quarter": "Q3"}}
    assert ev.grade_b(q, r) == {"retrieval": True, "citation": True, "period": True, "first_relevant_rank": 1}
    r["filters"] = {"fiscal_year": 2025, "fiscal_quarter": "Q3"}
    assert ev.grade_b(q, r)["period"] is False


def test_number_guard_ignores_dates():
    g = guard.check("Q4 FY2026 (quarter ended 2026-06-30) revenue $90.007 billion [1]; motion filed Feb. 17, 2026 "
                    "and June 22, 2026.", ["revenue 90,007,000,000"])
    assert g == {"verified": ["$90.007 billion"], "unverified": [], "computed": []}


def test_number_guard_regressions_from_the_ultimate_retest():
    """Answers from the 2026-10-09 retest: a number glued to its label in XBRL note text, and a derived figure
    whose '(computed ...)' tag sits a few words after it. A figure absent from the evidence is still flagged."""
    ev = ["Long-lived assets United States$100 $93 China14 14 Malaysia445 398 Rest of Asia73 67"]
    assert guard.check("Malaysia holds $445 million of long-lived assets [8].", ev)["unverified"] == []
    g = guard.check("EPS was $13.96 vs $9.67 estimated, a beat of $4.29 per share (computed from [2]) [2].",
                    ["EPS actual 13.96 vs consensus estimate 9.67"])
    assert g["unverified"] == [] and g["computed"]
    assert guard.check("Q4 FY2026 has 14 weeks instead of the usual 13 [8].", ["the fourth quarter had 14 weeks"]
                       )["unverified"] == ["13"]
