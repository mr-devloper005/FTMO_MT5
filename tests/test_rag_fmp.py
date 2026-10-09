"""FMP collector + structured answering.

Data: real FMP responses for TSLA captured on 2026-10-08 (tests/fixtures/fmp). HTTP
error paths use a tiny HTTP stand-in that returns FMP's real error texts (as seen
live: 401 'Invalid API KEY', 402 'Restricted Endpoint', 402 'Premium Query
Parameter'). Database tests run on the *_test database only."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rag import config, guard, structured  # noqa: E402
from rag.fmp import normalize as N  # noqa: E402
from rag.fmp import repo  # noqa: E402
from rag.fmp.client import (FmpAuthError, FmpClient, FmpHTTPError, FmpPlanRestricted,  # noqa: E402
                            FmpQuotaExceeded, FmpRateLimited, public_url)
from rag_fixtures import cfg, store  # noqa: E402,F401  (pytest fixtures)

FX = json.loads((Path(__file__).parent / "fixtures" / "fmp" / "tsla_real_2026-10-08.json").read_text())
KEY = "TEST-KEY-not-real-1234567890"
URL = "https://financialmodelingprep.com/stable/income-statement?symbol=TSLA&period=quarter"


# ------------------------------------------------------------------ HTTP stand-in for error paths
class Resp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(*responses, **kw):
    slept = []
    c = FmpClient(api_key=KEY, session=Session(*responses), sleep=slept.append, max_rps=1000, **kw)
    return c, slept


def test_key_goes_in_header_never_in_url_or_repr(tmp_path):
    c, _ = client(Resp(200, FX["income_quarter"][:1]), raw_dir=tmp_path)
    body = c.get("income-statement", symbol="TSLA", period="quarter", limit=1)
    call = c.session.calls[0]
    assert body[0]["revenue"] == 28236000000
    assert call["headers"]["apikey"] == KEY and "apikey" not in call["params"]
    assert KEY not in repr(c) and KEY not in public_url("x", {"symbol": "TSLA", "apikey": KEY})
    saved = list(tmp_path.rglob("*.json"))
    assert len(saved) == 1 and KEY not in saved[0].read_text()             # raw cache never holds the key


def test_auth_error_401():
    c, _ = client(Resp(401, {"Error Message": "Invalid API KEY. Feel free to create a Free API Key"}))
    with pytest.raises(FmpAuthError) as e:
        c.get("profile", symbol="TSLA")
    assert e.value.status == 401 and KEY not in str(e.value)


@pytest.mark.parametrize("msg", [
    "Restricted Endpoint: This endpoint is not available under your current subscription please visit ...",
    "Premium Query Parameter: 'Special Parameters : The values for 'limit' must be between 0 and 5 ..."])
def test_plan_restriction_402(msg):
    c, _ = client(Resp(402, {"Error Message": msg}))
    with pytest.raises(FmpPlanRestricted) as e:
        c.get("earning-call-transcript-dates", symbol="TSLA")
    assert e.value.status == 402 and "please visit" not in str(e.value)


def test_rate_limit_is_retried_with_backoff_then_succeeds():
    c, slept = client(Resp(429, {}, {"Retry-After": "3"}), Resp(503, {}), Resp(200, FX["earnings"]))
    assert len(c.get("earnings", symbol="TSLA", limit=5)) == 5
    assert c.calls == 3 and 3.0 in slept and 2 in slept                 # Retry-After honoured, then 2^1


def test_rate_limit_gives_up_with_typed_error():
    c, _ = client(*[Resp(429, {})] * 3, max_retries=2)
    with pytest.raises(FmpRateLimited):
        c.get("earnings", symbol="TSLA")


def test_quota_exhausted_stops_immediately():
    """FMP's real 429 body once the plan's quota is used up (seen 2026-10-08): no retries."""
    c, slept = client(Resp(429, {"Error Message": "Limit Reach . Please upgrade your plan or visit our "
                                                  "documentation for more details at https://site.financialmodelingprep.com/"}))
    with pytest.raises(FmpQuotaExceeded):
        c.get("profile", symbol="TSLA")
    assert c.calls == 1 and slept == []


def test_network_errors_and_error_bodies():
    import requests
    c, _ = client(requests.ConnectionError(f"boom https://x?apikey={KEY}"), Resp(200, FX["profile"]))
    assert c.get("profile", symbol="TSLA")[0]["symbol"] == "TSLA"           # retried after a network error
    c, _ = client(Resp(200, {"Error Message": "Something odd"}))
    with pytest.raises(FmpHTTPError):
        c.get("profile", symbol="TSLA")


def test_missing_key_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.setattr(config, "REPO", tmp_path)
    with pytest.raises(config.MissingCredential):
        config.fmp_api_key()


# ------------------------------------------------------------------ normalisation (real payloads)
def test_statement_rows_keep_every_field_and_type_the_keys():
    rows = N.statement_rows("tsla", "income", "quarter", FX["income_quarter"], URL)
    r = rows[0]
    assert (r["symbol"], r["period_end"], r["fiscal_year"], r["fiscal_quarter"], r["period"]) == \
        ("TSLA", "2026-06-30", 2026, "Q2", "Q2 FY2026")
    assert r["filing_date"] == "2026-07-23" and r["data"] == FX["income_quarter"][0]
    assert r["source"] == "FMP" and r["source_url"] == URL and len(r["data_sha"]) == 64
    assert [x["period"] for x in rows] == ["Q2 FY2026", "Q1 FY2026", "Q4 FY2025", "Q3 FY2025", "Q2 FY2025"]


def test_earnings_map_to_the_quarter_they_report():
    q = [(r["period_end"], r["fiscal_year"], r["fiscal_quarter"])
         for r in N.statement_rows("TSLA", "income", "quarter", FX["income_quarter"], URL)]
    rows = {r["announce_date"]: r for r in N.earnings_rows("TSLA", FX["earnings"], q, "u")}
    assert rows["2026-07-22"]["period"] == "Q2 FY2026" and rows["2026-07-22"]["revenue_actual"] == 28236000000
    assert rows["2026-01-28"]["period"] == "Q4 FY2025"
    upcoming = [r for r in rows.values() if r["eps_actual"] is None]
    assert upcoming and all(r["fiscal_year"] is None for r in upcoming)    # future dates: no guessed period


def test_report_notes_are_cleaned_and_tables_skipped():
    notes = dict(N.report_notes(FX["report_2026_Q2"]))
    assert "Commitments and Contingencies" in notes and "Debt" in notes
    assert not any("(Tables)" in t or "(Details)" in t for t in notes)
    cc = notes["Commitments and Contingencies"]
    assert cc.startswith("Commitments and Contingencies\nTariffs\nIn February 2026")   # glued headings split
    assert N.clean_note_text("year.The company") == "year.\nThe company"


def test_report_document_metadata():
    doc, chunks = N.report_document("TSLA", "Tesla, Inc.", 2026, "Q2", FX["report_2026_Q2"], "2026-07-23",
                                    public_url("financial-reports-json", {"symbol": "TSLA", "year": 2026,
                                                                          "period": "Q2"}))
    assert doc["doc_id"] == "FMP:report:TSLA:2026:Q2" and doc["form"] == "10-Q" and doc["source"] == "FMP"
    assert doc["n_chunks"] == len(chunks) > 3
    c = chunks[0]
    for k, v in {"symbol": "TSLA", "company": "Tesla, Inc.", "fiscal_year": 2026, "fiscal_quarter": "Q2",
                 "period": "Q2 FY2026", "filing_date": "2026-07-23", "section": "Notes to financial statements",
                 "source": "FMP", "document_type": "notes to financial statements (10-Q, via FMP)"}.items():
        assert c[k] == v, k
    assert "apikey" not in c["source_url"] and c["chunk_id"] == "FMP:report:TSLA:2026:Q2:0000"
    assert all(len(x["text"].split()) <= 300 for x in chunks)


# ------------------------------------------------------------------ routing + fact check
@pytest.mark.parametrize("q,structured_,documents", [
    ("What did Tesla management say about demand?", False, True),
    ("What was Micron's latest revenue?", True, False),
    ("What guidance did Micron give for next quarter?", False, True),
    ("What risks did Microsoft mention?", False, True),
    ("Compare Microsoft's guidance from the previous quarter with the latest quarter.", False, True),
    ("How did Tesla's revenue and free cash flow change, and what did management say about it?", True, True),
])
def test_router(q, structured_, documents):
    r = structured.route(q)
    assert (r.structured, r.documents) == (structured_, documents), r.reason


ITEMS = [{"kind": "fact", "facts": [
    {"metric": "revenue", "field": "revenue", "period": "Q2 FY2026", "value": 28236000000.0},
    {"metric": "revenue", "field": "revenue", "period": "Q1 FY2026", "value": 22387000000.0},
    {"metric": "EPS", "field": "epsDiluted", "period": "Q2 FY2026", "value": 0.32},
    {"metric": "EPS", "field": "eps_actual", "period": "Q2 FY2026", "value": 0.33}]}]


def test_factcheck_accepts_correct_figures_and_flags_overrides():
    ok = structured.factcheck("Revenue was $28.2 billion in Q2 FY2026 [1], up from $22.4 billion in Q1 FY2026 [1]. "
                              "Diluted EPS was $0.32 [1].", ITEMS)
    assert ok["checked"] == 3 and ok["conflicts"] == []
    bad = structured.factcheck("Tesla's Q2 FY2026 revenue was $30.1 billion [1].", ITEMS)
    assert [c["figure"] for c in bad["conflicts"]] == ["$30.1 billion"]
    assert bad["conflicts"][0]["database"] == [{"period": "Q2 FY2026", "field": "revenue", "value": 28236000000.0}]
    assert "28,236,000,000" in structured.correction_note(bad["conflicts"])
    skip = structured.factcheck("Revenue grew 26.1% (computed from [1] and [1]) to $28.2 billion [1].", ITEMS)
    assert skip["conflicts"] == []                                        # percentages / computed are not DB facts


# ------------------------------------------------------------------ ingestion (database)
class Replay:
    """Serves the captured real FMP responses; endpoints FMP refused on this plan raise FmpPlanRestricted."""

    def __init__(self, hide_newest: bool = False):
        self.calls, self.log = 0, []
        fx = copy.deepcopy(FX)
        if hide_newest:                                       # the world before TSLA filed Q2 FY2026
            for k in ("income_quarter", "balance_quarter", "cashflow_quarter"):
                fx[k] = fx[k][1:]
            fx["financial_reports_dates"] = fx["financial_reports_dates"][1:]
        self.data = {("income-statement", "quarter"): fx["income_quarter"],
                     ("balance-sheet-statement", "quarter"): fx["balance_quarter"],
                     ("cash-flow-statement", "quarter"): fx["cashflow_quarter"],
                     ("earnings", None): fx["earnings"], ("profile", None): fx["profile"],
                     ("financial-reports-dates", None): fx["financial_reports_dates"],
                     ("financial-reports-json", "Q2"): FX["report_2026_Q2"]}

    def get(self, endpoint, **params):
        self.calls += 1
        self.log.append((endpoint, params))
        body = self.data.get((endpoint, params.get("period")))
        if body is None:
            raise FmpPlanRestricted(f"{endpoint}: not available on the current FMP plan (replay)", 402, endpoint)
        return body[:params["limit"]] if isinstance(body, list) and params.get("limit") else body


def run(cfg, tmp_path, client, **kw):
    from rag.fmp.ingest import ingest_symbol
    s = config.Settings(state_dir=tmp_path, pg=cfg)
    return ingest_symbol("TSLA", s, client=client, log=lambda *_: None, **kw)


def test_incremental_ingest_fetches_only_the_new_quarter(cfg, store, tmp_path):
    r1 = run(cfg, tmp_path, Replay(hide_newest=True), reports=0)
    assert r1["statements"]["inserted"] == 12                     # 4 quarters x 3 statements
    assert ("Q2 FY2026",) not in [(x["period"],) for x in repo.statements(store.db, "TSLA")]
    new = Replay()                                                # TSLA files Q2 FY2026
    r2 = run(cfg, tmp_path, new, reports=0, refresh_hours=0)
    assert r2["statements"] == {"inserted": 3, "updated": 0, "unchanged": 0}
    limits = {p.get("limit") for e, p in new.log if e.endswith("statement") and p.get("period") == "quarter"}
    assert limits == {1}                                          # asked FMP for just the newest period
    again = Replay()
    r3 = run(cfg, tmp_path, again, reports=0)
    assert not [e for e, _ in again.log if e.endswith("statement")]      # nothing missing: no statement calls
    assert any("already stored" in s for s in r3["skipped"])


def test_upsert_dedup_and_revision(store):
    rows = N.statement_rows("TSLA", "income", "quarter", FX["income_quarter"][:2], URL)
    assert repo.upsert(store.db, "financial_statements", rows) == {"inserted": 2, "updated": 0, "unchanged": 0}
    assert repo.upsert(store.db, "financial_statements", rows) == {"inserted": 0, "updated": 0, "unchanged": 2}
    revised = copy.deepcopy(FX["income_quarter"][0])
    revised["revenue"] += 1_000_000
    rows2 = N.statement_rows("TSLA", "income", "quarter", [revised], URL)
    assert repo.upsert(store.db, "financial_statements", rows2) == {"inserted": 0, "updated": 1, "unchanged": 0}
    n = store.db.execute("SELECT count(*) AS n FROM rag.financial_statements").fetchone()["n"]
    assert n == 2


def test_documents_ingested_once_with_metadata_and_retrievable(cfg, store, tmp_path, monkeypatch):
    from rag import retrieve
    r = run(cfg, tmp_path, Replay(), reports=1)
    assert r["documents_added"] == 2                              # profile + Q2 FY2026 notes
    doc = store.db.execute("SELECT * FROM rag.documents WHERE doc_id='FMP:report:TSLA:2026:Q2'").fetchone()
    assert doc["source"] == "FMP" and doc["form"] == "10-Q" and str(doc["filing_date"]) == "2026-07-23"
    assert doc["period"] == "Q2 FY2026" and "apikey" not in doc["source_url"]
    again = Replay()
    r2 = run(cfg, tmp_path, again, reports=1, force=False)
    assert r2["documents_added"] == 0 and not [e for e, _ in again.log if e == "financial-reports-json"]
    hits = retrieve.search("Supreme Court ruling on tariffs refund", symbol="TSLA", store=store, k=3)["hits"]
    top = hits[0]
    assert top["subsection"] == "Commitments and Contingencies" and top["source"] == "FMP"
    cite = retrieve.cite(top)
    for part in ("TSLA 10-Q via FMP", "Q2 FY2026", "filed 2026-07-23", "Notes to financial statements > "
                 "Commitments and Contingencies", "financial-reports-json?symbol=TSLA&year=2026&period=Q2"):
        assert part in cite
    assert ", p." not in cite                                     # FMP notes have no page numbers


def test_unsupported_endpoints_are_logged_and_not_retried(cfg, store, tmp_path):
    first = Replay()
    run(cfg, tmp_path, first, reports=0)
    assert ("earning-call-transcript-dates", {"symbol": "TSLA"}) in first.log
    st = repo.status(store.db, "TSLA")
    assert {u["endpoint"] for u in st["unsupported"]} >= {"earning-call-transcript-dates", "news/press-releases"}
    second = Replay()
    r = run(cfg, tmp_path, second, reports=0)
    assert "earning-call-transcript-dates" not in [e for e, _ in second.log]      # known 402: not hammered
    assert any(u.startswith("earning-call-transcript-dates: not in current FMP plan") for u in r["unsupported"])


# ------------------------------------------------------------------ answering with structured data
def test_structured_answer_citations_and_database_check(cfg, store, tmp_path, monkeypatch):
    from rag import answer
    run(cfg, tmp_path, Replay(), reports=0)
    s = config.Settings(state_dir=tmp_path, pg=cfg)
    prompts = []

    def fake(text):
        def _claude(system, prompt, model=None):
            prompts.append(prompt)
            return {"text": text, "cost_usd": 0.0, "seconds": 0.0}
        return _claude
    monkeypatch.setattr(answer, "claude", fake("Tesla's latest revenue was $28.2 billion in Q2 FY2026 [1]."))
    r = answer.ask("What was Tesla's latest revenue?", s, symbol="TSLA")
    assert r["route"]["structured"] and not r["hits"]
    assert "revenue (income statement): 28,236,000,000 USD" in prompts[-1]
    c = r["citations"][0]
    assert c["kind"] == "fact" and "FMP income-statement: TSLA Q2 FY2026 (period ended 2026-06-30, filed " \
                                   "2026-07-23)" in c["source"] and "apikey" not in c["source"]
    assert r["grounded"] and r["factcheck"] == {"checked": 1, "derived_skipped": 0, "derived_verified": 0, "doc_supported": 0, "conflicts": []}
    monkeypatch.setattr(answer, "claude", fake("Tesla's latest revenue was $31.5 billion in Q2 FY2026 [1]."))
    r = answer.ask("What was Tesla's latest revenue?", s, symbol="TSLA")
    assert not r["grounded"] and r["factcheck"]["conflicts"][0]["figure"] == "$31.5 billion"
    assert "DATABASE CHECK FAILED" in r["answer"] and "28,236,000,000" in r["answer"]


def test_missing_data_says_not_found_without_calling_claude(cfg, store, monkeypatch):
    from rag import answer

    def boom(*a, **k):
        raise AssertionError("Claude must not be called without evidence")
    monkeypatch.setattr(answer, "claude", boom)
    r = answer.ask("What was GS revenue in Q2 FY2026?", config.Settings(pg=cfg), symbol="GS")
    assert r["answer"] == config.NOT_FOUND == "Not found in the available data." and r["not_found"]


class TickerOutsidePlan(Replay):
    """FMP's real answer for a ticker the plan does not cover (seen live for MU / SNDK): profile works, data is 402."""

    def get(self, endpoint, **params):
        self.calls += 1
        self.log.append((endpoint, params))
        if endpoint == "profile":
            return FX["profile"]
        raise FmpPlanRestricted(f"{endpoint}: not available on the current FMP plan (Premium Query Parameter: "
                                f"'Special Endpoint : This value set for 'symbol' is not available under your "
                                f"current subscription)", 402, endpoint)


def test_ticker_outside_plan_stops_after_first_refusal(cfg, store, tmp_path):
    c = TickerOutsidePlan()
    r = run(cfg, tmp_path, c, reports=2)
    assert r["symbol_not_in_plan"] and c.calls == 2                       # profile, then one refusal: stop
    assert r["statements"]["inserted"] == 0 and any("not covered" in s for s in r["skipped"])


def test_fy_and_q4_listing_of_one_10k_is_fetched_once(cfg, store, tmp_path):
    c = Replay()
    c.data[("financial-reports-dates", None)] = [{"symbol": "TSLA", "fiscalYear": 2025, "period": "FY"},
                                                 {"symbol": "TSLA", "fiscalYear": 2025, "period": "Q4"},
                                                 {"symbol": "TSLA", "fiscalYear": 2026, "period": "Q2"}]
    run(cfg, tmp_path, c, reports=5)
    asked = [(p["year"], p["period"]) for e, p in c.log if e == "financial-reports-json"]
    assert (2025, "FY") in asked and (2025, "Q4") not in asked


def test_report_without_notes_is_not_refetched(cfg, store, tmp_path):
    c = Replay()
    c.data[("financial-reports-json", "Q2")] = {k: FX["report_2026_Q2"][k] for k in ("symbol", "period", "year",
                                                                                  "Cover")}
    run(cfg, tmp_path, c, reports=1)
    again = Replay()
    r = run(cfg, tmp_path, again, reports=1)
    assert not [e for e, _ in again.log if e == "financial-reports-json"]
    assert any("no narrative notes" in s for s in r["skipped"])


def test_factcheck_regressions_from_real_answers():
    """Cases from real Claude answers on 2026-10-08 that the first fact checker got wrong."""
    items = [{"kind": "fact", "facts": [
        {"metric": "free cash flow", "field": "freeCashFlow", "period": "Q2 FY2026", "value": -1092000000.0},
        {"metric": "EPS", "field": "epsDiluted", "period": "Q2 FY2026", "value": 0.32},
        {"metric": "EPS", "field": "eps_actual", "period": "Q2 FY2026", "value": 0.33},
        {"metric": "EPS", "field": "eps_estimated", "period": "Q2 FY2026", "value": 0.5, "estimate": True},
        {"metric": "revenue", "field": "revenue", "period": "Q4 FY2026", "value": 90007000000.0}]}]
    real = ("- **Free cash flow:** -$1.092 billion (i.e., a cash outflow) [1]\n"
            "- EPS missed consensus — actual EPS was 0.33 versus a consensus estimate of 0.5 [2], "
            "a shortfall of 0.17 (computed from [2]).\n"
            "Revenue in Q4 FY2026 (quarter ended 2026-06-30) was $90.007 billion [1].")
    assert structured.factcheck(real, items) == {"checked": 4, "derived_skipped": 0, "derived_verified": 0, "doc_supported": 0, "conflicts": []}
    assert structured.factcheck("Actual EPS was 0.50 [2].", items)["conflicts"]           # estimate passed off as actual
    assert structured.factcheck("Free cash flow was $1.092 billion [1].", items)["conflicts"]   # sign dropped
    assert structured.factcheck("Free cash flow: -$1,092,000,000 (negative $1.09 billion) [1].", items) == \
        {"checked": 2, "derived_skipped": 0, "derived_verified": 0, "doc_supported": 0, "conflicts": []}


def test_net_income_comes_from_the_income_statement(cfg, store, tmp_path):
    """TSLA Q2 FY2026: income-statement netIncome 1,114,000,000; cash-flow netIncome (incl. minority) 1,128,000,000."""
    for st, key in (("income", "income_quarter"), ("cashflow", "cashflow_quarter"), ("balance", "balance_quarter")):
        repo.upsert(store.db, "financial_statements", N.statement_rows("TSLA", st, "quarter", FX[key], URL))
    ev = structured.evidence(store.db, "TSLA", "What was Tesla's latest net income?")
    facts = [f for e in ev for f in e["facts"] if f["field"] == "netIncome"]
    assert facts and facts[0]["value"] == 1114000000.0


def test_factcheck_regressions_from_the_fixed_5_stock_run():
    """Sentences from run agent-20261008-172755-5dcc that the checker wrongly flagged."""
    msft = [{"kind": "fact", "facts": [
        {"metric": "revenue", "field": "revenue", "period": "Q4 FY2026", "value": 90007000000.0},
        {"metric": "revenue", "field": "revenue", "period": "Q3 FY2026", "value": 82886000000.0},
        {"metric": "revenue", "field": "revenue_estimated", "period": "Q4 FY2026", "value": 87619550000.0, "estimate": True},
        {"metric": "EPS", "field": "eps_actual", "period": "Q4 FY2026", "value": 4.74},
        {"metric": "EPS", "field": "eps_estimated", "period": "Q4 FY2026", "value": 4.24, "estimate": True}]}]
    text = ("Microsoft's latest reported quarter is Q4 FY2026, with revenue of $90,007,000,000 and EPS of $4.74 [1][3]. "
            "This beat analyst consensus estimates of $87,619,550,000 in revenue and $4.24 in EPS for that quarter [3]. "
            "Compared quarter-over-quarter, revenue increased by $7,121,000,000, or about $7.1 billion (computed from [1] and [2]).")
    r = structured.factcheck(text, msft)
    assert r["conflicts"] == [] and r["derived_skipped"] == 1 and r["checked"] == 4
    gs = [{"kind": "fact", "facts": [
        {"metric": "revenue", "field": "revenue", "period": "Q2 FY2026", "value": 20338000000.0},
        {"metric": "revenue", "field": "revenue_estimated", "period": "Q2 FY2026", "value": 16225000000.0, "estimate": True},
        {"metric": "EPS", "field": "eps_actual", "period": "Q2 FY2026", "value": 20.98},
        {"metric": "EPS", "field": "eps_estimated", "period": "Q2 FY2026", "value": 14.47, "estimate": True}]}]
    t2 = ("The earnings announcement reports actual revenue of $20.338 billion and EPS of $20.98, both beating consensus "
          "estimates of $16.225 billion revenue and $14.47 EPS [3].")
    assert structured.factcheck(t2, gs)["conflicts"] == []
    # still catches a real error in the same shapes
    assert structured.factcheck("Consensus estimates were $95.0 billion in revenue [3].", msft)["conflicts"]
    assert structured.factcheck("Revenue was $95.0 billion in Q4 FY2026 [1].", msft)["conflicts"]


# A call in FMP's exact transcript shape ("Name : text" turns separated by blank lines). Invented wording.
CALL = ("Operator : Good afternoon and welcome to the Example Corp fourth quarter call. I will now hand over to Jane Doe.  \n\n"
        "Jane Doe : Thank you. Revenue grew in the quarter and demand for our products remained strong across regions. "
        + "We continue to invest in capacity. " * 30 + "With that, let's open it up for questions.  \n\n"
        "Operator : Your first question comes from the line of Sam Analyst from Big Bank.  \n\n"
        "Sam Analyst : What is your outlook for gross margin next quarter?  \n\n"
        "Jane Doe : We expect gross margin to be roughly flat next quarter.")


def test_transcript_parsing_keeps_speakers_and_the_qa_split():
    rec = {"symbol": "EXM", "period": "Q4", "year": 2026, "date": "2026-09-30", "content": CALL}
    turns = N.transcript_turns(CALL)
    assert [t[0] for t in turns] == ["Operator", "Jane Doe", "Operator", "Sam Analyst", "Jane Doe"]
    assert N.transcript_sections(turns) == ["Prepared remarks", "Prepared remarks", "Q&A", "Q&A", "Q&A"]
    doc, chunks = N.transcript_document("EXM", "Example Corp", rec, "https://financialmodelingprep.com/stable/x")
    assert doc["doc_id"] == "FMP:transcript:EXM:2026:Q4" and doc["form"] == "TRANSCRIPT"
    assert {c["section"] for c in chunks} == {"Earnings call - Prepared remarks", "Earnings call - Q&A"}
    qa = [c for c in chunks if c["section"].endswith("Q&A")][0]
    assert "Sam Analyst: What is your outlook" in qa["text"] and "Sam Analyst" in qa["subsection"]
    c = chunks[0]
    assert (c["fiscal_year"], c["fiscal_quarter"], c["period"], c["filing_date"], c["source"]) == \
        (2026, "Q4", "Q4 FY2026", "2026-09-30", "FMP")
    assert all(len(x["text"].split()) <= 290 for x in chunks)


def test_transcripts_ingested_once_and_retrievable(cfg, store, tmp_path, monkeypatch):
    from rag import retrieve
    c = Replay()
    c.data[("earning-call-transcript-dates", None)] = [{"quarter": 2, "fiscalYear": 2026, "date": "2026-07-22"}]
    c.data[("earning-call-transcript", None)] = [{"symbol": "TSLA", "period": "Q2", "year": 2026,
                                                   "date": "2026-07-22", "content": CALL}]
    r = run(cfg, tmp_path, c, reports=0, transcripts=4)
    assert r["transcripts_added"] == 1
    again = Replay()
    again.data.update({k: v for k, v in c.data.items() if k[0].startswith("earning-call")})
    r2 = run(cfg, tmp_path, again, reports=0, transcripts=4)
    assert not [e for e, _ in again.log if e == "earning-call-transcript"] and not r2.get("transcripts_added")
    hits = retrieve.search("gross margin outlook next quarter", symbol="TSLA", store=store, k=2)["hits"]
    assert hits[0]["form"] == "TRANSCRIPT" and hits[0]["section"] == "Earnings call - Q&A"
    assert "TSLA TRANSCRIPT via FMP (Q2 FY2026, filed 2026-07-22), Earnings call - Q&A" in retrieve.cite(hits[0])


def test_factcheck_regressions_from_the_transcript_retest():
    """Sentences from run agent-20261009-154659-add3 that the checker wrongly flagged, plus the one it rightly kept."""
    mu = [{"kind": "fact", "facts": [
        {"metric": "revenue", "field": "revenue", "period": "Q4 FY2026", "value": 54229000000.0},
        {"metric": "revenue", "field": "revenue_estimated", "period": "Q4 FY2026", "value": 51334270000.0, "estimate": True},
        {"metric": "EPS", "field": "eps_actual", "period": "Q4 FY2026", "value": 33.42},
        {"metric": "EPS", "field": "eps_estimated", "period": "Q4 FY2026", "value": 31.77, "estimate": True},
        {"metric": "capital expenditure", "field": "capitalExpenditure", "period": "Q4 FY2026", "value": -17165000000.0}]}]
    ok = ("Micron beat EPS consensus by $1.65 in Q4 FY2026 (33.42 actual vs. 31.77 estimate) [2]. "
          "The revenue beat narrowed to $2.895 billion in Q4 FY2026 [1][2]. "
          "Capital expenditure was $17.165 billion in Q4 FY2026 [1]. "
          "On the call, capex would run about $10 billion that quarter [5].")
    r = structured.factcheck(ok, mu, ["Mark Murphy: CapEx would run about $10 billion that quarter and step up"])
    assert r["conflicts"] == [] and r["derived_verified"] >= 1 and r["doc_supported"] == 1, r
    bad = structured.factcheck("Revenue beat the consensus estimate of $51.334,270,000 [2].", mu)
    assert bad["conflicts"], "a garbled figure must still be flagged"
    msft = [{"kind": "fact", "facts": [
        {"metric": "revenue", "field": "revenue", "period": "Q3 FY2026", "value": 82886000000.0},
        {"metric": "revenue", "field": "revenue_estimated", "period": "Q3 FY2026", "value": 81400000000.0, "estimate": True},
        {"metric": "EPS", "field": "eps_actual", "period": "Q3 FY2026", "value": 4.27},
        {"metric": "EPS", "field": "eps_estimated", "period": "Q3 FY2026", "value": 4.06, "estimate": True}]}]
    t = "Q3 FY2026 revenue of $82.9 billion beat the $81.4 billion estimate and EPS of $4.27 beat the $4.06 estimate [4]."
    assert structured.factcheck(t, msft)["conflicts"] == []
    tsla = [{"kind": "fact", "facts": [{"metric": "gross profit", "field": "grossProfit", "period": "Q2 FY2026",
                                        "value": 4751000000.0}]}]
    t2 = "Gross margin fell while energy storage deployments rose 53% sequentially to 13.5 GWh in Q2 FY2026 (ended 6/30/2026) [5]."
    assert structured.factcheck(t2, tsla)["conflicts"] == []


def test_factcheck_comparison_pairs_from_the_final_retest():
    """'X vs. Y' / 'X topped the Y estimate' sentences from retest-20261009-final; all figures are correct."""
    tsla = [{"kind": "fact", "facts": [
        {"metric": "revenue", "field": "revenue", "period": "Q1 FY2026", "value": 22387000000.0},
        {"metric": "revenue", "field": "revenue_estimated", "period": "Q1 FY2026", "value": 22100780000.0, "estimate": True},
        {"metric": "EPS", "field": "eps_actual", "period": "Q1 FY2026", "value": 0.41},
        {"metric": "EPS", "field": "eps_estimated", "period": "Q1 FY2026", "value": 0.3539, "estimate": True}]}]
    t = "In Q1, both revenue and EPS had beaten consensus ($22.387B vs. $22.101B revenue; $0.41 vs. $0.3539 EPS) [3][4]."
    assert structured.factcheck(t, tsla)["conflicts"] == []
    mu = [{"kind": "fact", "facts": [
        {"metric": "EPS", "field": "eps_actual", "period": "Q4 FY2026", "value": 33.42},
        {"metric": "EPS", "field": "eps_estimated", "period": "Q4 FY2026", "value": 31.77, "estimate": True}]}]
    t2 = "Micron beat consensus estimates in both quarters, with Q4 FY2026 EPS of 33.42 vs. a 31.77 estimate [3]."
    assert structured.factcheck(t2, mu)["conflicts"] == []
    t3 = "Both quarters beat analyst consensus: Q4 FY2026 EPS of 33.42 (FMP figure) topped the $31.77 estimate [3]."
    assert structured.factcheck(t3, mu)["conflicts"] == []
    assert structured.factcheck("Q4 FY2026 EPS of 35.10 vs. a 31.77 estimate [3].", mu)["conflicts"]    # still caught
    assert guard.check("Latest call (Q4 FY2026, reported 2026\u201107\u201129) [1].", [])["unverified"] == []
