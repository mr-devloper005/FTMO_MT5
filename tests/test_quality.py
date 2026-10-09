"""Quality engine: Rahul's lenses fed by FMP. Offline — no API calls."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from research_agent.quality import inputs, lenses as L, model   # noqa: E402

# MU, FMP responses of 2026-10-03 (fields used by the lenses only).
MU = {
    "income_q": [
        {"date": "2026-09-03", "fiscalYear": "2026", "period": "Q4", "revenue": 54229000000, "operatingIncome": 44318000000, "netIncome": 37701000000},
        {"date": "2026-05-28", "fiscalYear": "2026", "period": "Q3", "revenue": 41456000000, "operatingIncome": 33318000000, "netIncome": 28243000000},
        {"date": "2026-02-26", "fiscalYear": "2026", "period": "Q2", "revenue": 23860000000, "operatingIncome": 16135000000, "netIncome": 13785000000},
        {"date": "2025-11-27", "fiscalYear": "2026", "period": "Q1", "revenue": 13643000000, "operatingIncome": 6136000000, "netIncome": 5240000000},
        {"date": "2025-08-28", "fiscalYear": "2025", "period": "Q4", "revenue": 11315000000, "operatingIncome": 3754000000, "netIncome": 3201000000}],
    "cashflow_q": [{"freeCashFlow": 26808000000}],
    "balance_q": [{"longTermDebt": 4688000000, "totalStockholdersEquity": 138378000000}],
    "ratios_ttm": {"priceToFreeCashFlowRatioTTM": 22.944923586225148, "dividendYieldTTM": 0.00049307371},
    "estimates": [{"date": "2028-09-03", "epsAvg": 205.33802}, {"date": "2027-09-03", "epsAvg": 171.83317},
                  {"date": "2026-09-03", "epsAvg": 73.853}],
    "quote": {"price": 1074.89, "priceAvg50": 956.0458, "priceAvg200": 681.6143, "yearHigh": 1255},
    "quote_change": {"3M": 9.15359, "1Y": 472.26748},
    "rsi14": 59.26408454981731,
}
PEERS = {"LRCX": {"price": 347.49, "estimates": [{"date": "2027-06-28", "epsAvg": 9.47321}]},
         "AMAT": {"price": 540.04, "estimates": [{"date": "2026-10-26", "epsAvg": 12.81105}]},
         "LOSS": {"price": 14.3, "estimates": [{"date": "2026-12-31", "epsAvg": -2.26434}]}}
TODAY = date(2026, 10, 3)


def test_fy1_is_the_nearest_unreported_fiscal_year():
    assert inputs.fy1_eps(MU["estimates"], TODAY) == {"fiscal_year_end": "2027-09-03", "eps": 171.83317}
    assert inputs.fy1_eps([{"date": "2025-12-31", "epsAvg": 1.0}], TODAY) is None


def test_company_inputs_follow_rahuls_definitions():
    s = inputs.company(MU, "Information Technology")["fundamentals"]
    assert s["summary"]["yoy_revenue_growth_pct"] == 379.3          # vs the same quarter a year earlier
    assert s["summary"]["latest_net_margin_pct"] == 69.52
    assert s["summary"]["latest_operating_margin_pct"] == 81.72
    assert s["summary"]["latest_fcf_b"] == 26.81
    assert s["summary"]["ttm_revenue_b"] == 133.19                  # last four quarters
    assert s["quarters"][0]["long_debt_to_equity"] == 0.03


def test_peer_median_skips_loss_makers():
    fs = inputs.peer_comp(MU, PEERS, TODAY)
    assert fs["target"]["PE_FY1"] == 6.26 and fs["target"]["FCF_Yield_pct"] == 4.36
    assert [p["PE_FY1"] for p in fs["peers"]] == [36.68, 42.15, None]
    assert fs["peer_aggregates"]["median"]["PE_FY1"] == round((36.68 + 42.15) / 2, 2)


def test_mu_scores_through_the_ported_lenses():
    co = inputs.company(MU, "Information Technology")
    fs = inputs.peer_comp(MU, PEERS, TODAY)
    ta = inputs.technicals(MU)
    assert L.fundamentals_score(co) == 93.5
    assert L.valuation_score(co, fs, ta) == 81.0
    assert L.technicals_score(ta) == 75.9


def test_composite_renormalises_missing_lenses_like_rahuls_dashboard():
    three = {"fundamentals": 93.5, "valuation": 81.0, "technicals": 75.9, "management": None, "options": None}
    assert model.composite(three) == round((93.5 * .35 + 81.0 * .05 + 75.9 * .35) / .75, 2)
    assert model.composite({"fundamentals": None}) is None
    assert [model.bucket(x) for x in (90, 70, 55, 40, 39.9)] == ["High", "Medium", "Low", "Watchlist", "Avoid"]


def test_quality_view_places_stocks_on_the_grid(tmp_path, monkeypatch):
    """Stored quality + FTMO tested result -> grid quadrant; untested stocks say exactly why."""
    from fastapi.testclient import TestClient
    from research_agent import config, universe, views, web
    from research_agent.quality import build
    from research_agent.store import Store
    s = config.Settings(state_dir=tmp_path / "state", workers=2)
    universe.refresh(s, text="Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
                             "MU|Micron Technology, Inc. - Common Stock|Q|N|N|100|N|N\n"
                             "TSLA|Tesla, Inc. - Common Stock|Q|N|N|100|N|N\n")
    snap = {"stocks": {"MU": {**MU, "sector": "Information Technology", "peers": ["LRCX", "AMAT"]},
                       "TSLA": {**MU, "sector": "Consumer Discretionary", "peers": []},
                       "GS": {**MU, "sector": "Financials", "peers": []}},
            "peers": PEERS}
    build.save(s, build.score_snapshot(snap, TODAY), as_of="2026-10-02 close", source="test")
    store = Store(s.db_path)
    m = {"returnPct": 150.0, "bhReturnPct": 80.0, "vsBHPct": 70.0, "trades": 40, "robustnessScore": 60.0}
    store.save_test({"fingerprint": "a", "symbol": "MU", "strategy": "R1", "params": {}, "phase": "validated",
                     "as_of": "2026-10-01", "bars": 2500, "timeframe": config.DAILY, "metrics": m, "grade": "FRAGILE", "why": []})
    q = views.quality(s)
    by = {r["Stock"]: r for r in q["rows"]}
    assert by["MU"]["Grid"] == "Best trading candidate" and by["MU"]["FTMO"]["verdict"] == "Watch"
    assert by["TSLA"]["Grid"] == "Not tested yet" and "Not backtested" in by["TSLA"]["Grid meaning"]
    assert "Not in our NASDAQ universe" in by["GS"]["FTMO"]["reason"]
    assert len(q["grid"]) == 4 and q["strong_threshold"] == 70.0
    monkeypatch.setattr(web, "_settings", lambda: s)
    c = TestClient(web.standalone_app())
    assert {r["Stock"] for r in c.get("/api/research/quality").json()["rows"]} == {"MU", "TSLA", "GS"}
