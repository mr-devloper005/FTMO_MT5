"""MCS v2: management credibility from sourced guidance ledgers. Offline, no API calls."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from research_agent.quality import build, mcs, mcs_extract   # noqa: E402

METRICS = {"revenue", "gross_margin", "opex", "eps", "tax_rate", "capex"}


def item(metric, low=None, high=None, actual=None, kind="range", unit=None, **kw):
    unit = unit or ("pct" if metric in ("gross_margin", "tax_rate") else "usd" if metric == "eps" else "usd_m")
    mid = kw.pop("mid", None)
    if mid is None and low is not None and high is not None:
        mid = (low + high) / 2
    return {"metric": metric, "unit": unit, "kind": kind, "low": low, "high": high, "mid": mid, "actual": actual, **kw}


def period(items, price_attributed=False, reported_on="2026-01-01", **pv):
    return {"period": "Q", "guided_on": "2025-10-01", "reported_on": reported_on, "items": items,
            "price_volume": {"price_attributed": price_attributed, **pv}}


# ------------------------------------------------------------------ grading rules
def test_bands_for_each_kind_of_guide():
    assert mcs.band(item("revenue", 90, 110)) == (90, 110)
    assert mcs.band(item("revenue", kind="approx", mid=1000)) == pytest.approx((975, 1025))
    assert mcs.band(item("capex", kind="approx", mid=1000)) == pytest.approx((950, 1050))
    assert mcs.band(item("gross_margin", kind="approx", mid=81.0)) == (80.5, 81.5)
    assert mcs.band(item("capex", low=3000, kind="floor"))[0] == 3000


def test_inside_the_range_is_fully_accurate_and_delivered_whichever_side():
    for actual in (90, 100, 110):
        it = item("revenue", 90, 110, actual)
        assert mcs.outcome(it) == "hit" and mcs.accuracy(period([it]), it) == 100 and mcs.delivery(it) == 1


def test_a_beat_is_delivered_but_less_accurate_than_a_hit():
    beat = item("revenue", 90, 110, 121)                  # 10% of the 100 guide beyond the top edge
    assert mcs.outcome(beat) == "beat" and mcs.delivery(beat) == 1
    assert mcs.accuracy(period([beat]), beat) == pytest.approx(100 - 11 * mcs.PENALTY_PER_PCT)


def test_a_shortfall_costs_double_an_equal_overshoot():
    up, down = item("revenue", 90, 110, 115), item("revenue", 90, 110, 85)
    assert mcs.delivery(down) == 0 and mcs.outcome(down) == "miss"
    assert 100 - mcs.accuracy(period([down]), down) == pytest.approx(2 * (100 - mcs.accuracy(period([up]), up)))


def test_opex_overspend_is_partly_kept_up_to_five_percent():
    assert mcs.delivery(item("opex", kind="approx", mid=1000, actual=1060)) == 0.5     # 3.4% over 1025
    assert mcs.delivery(item("opex", kind="approx", mid=1000, actual=1100)) == 0.0
    assert mcs.outcome(item("opex", 980, 1020, 950)) == "under"
    assert mcs.delivery(item("opex", 980, 1020, 950)) == 1


def test_capex_is_a_plan_and_either_direction_is_off_plan():
    assert mcs.delivery(item("capex", kind="approx", mid=1000, actual=800)) == 0.5
    assert mcs.delivery(item("capex", kind="approx", mid=1000, actual=1200)) == 0.5
    assert mcs.outcome(item("capex", kind="approx", mid=1000, actual=1010)) == "on-plan"


def test_discretionary_spend_is_graded_out_of_opex_but_flagged():
    it = item("opex", kind="approx", mid=1650, actual=2568, discretionary=918)
    assert mcs.outcome(it) == "hit"
    res = mcs.score_period(period([item("revenue", 90, 110, 100), it]))
    assert res["flags"] and "918" in res["flags"][0]


def test_market_credit_needs_management_to_attribute_the_surprise_to_price():
    rev = item("revenue", 90, 110, 130)                    # +30% vs the 100 midpoint
    assert mcs.market_credit(period([rev]), rev) == 0
    p = period([rev], price_attributed=True)
    assert mcs.market_credit(p, rev) == pytest.approx(0.9)            # 1 - 3/30, at the cap
    small = item("revenue", 90, 110, 104)
    assert mcs.market_credit(period([small], price_attributed=True), small) == pytest.approx(0.25)
    assert mcs.accuracy(p, rev) > mcs.accuracy(period([rev]), rev)
    opex = item("opex", 90, 110, 130)
    assert mcs.market_credit(period([rev, opex], price_attributed=True), opex) == 0   # spend is controllable


def test_eps_is_shown_but_not_scored():
    a = mcs.score_period(period([item("revenue", 90, 110, 100), item("eps", 1.0, 1.2, 1.1)]))
    b = mcs.score_period(period([item("revenue", 90, 110, 100), item("eps", 1.0, 1.2, 9.9)]))
    assert a["mcs"] == b["mcs"] == 100
    assert any(r["metric"] == "eps" and not r["scored"] for r in b["items"])


def test_commitments_are_graded_in_the_quarter_they_fall_due_and_pending_never_counts():
    ledger = {"symbol": "X", "periods": [
        period([item("revenue", 90, 110, 100)], reported_on="2025-06-01"),
        period([item("revenue", 90, 110, 100)], reported_on="2025-09-01")],
        "commitments": [{"target": "a", "status": "missed", "graded_on": "2025-08-15"},
                        {"target": "b", "status": "pending", "due": "2027-01-01"}]}
    res = mcs.score_ledger(ledger)
    first, second = res["periods"]
    assert first["mcs"] == 100 and second["delivery"] == 50
    assert [c["target"] for c in res["pending_commitments"]] == ["b"]


def test_weights_move_the_score_between_delivery_and_accuracy():
    p = period([item("revenue", 90, 110, 140)])
    assert mcs.score_period(p, weights={"delivery": 1.0, "accuracy": 0.0})["mcs"] == 100
    assert mcs.score_period(p, weights={"delivery": 0.0, "accuracy": 1.0})["mcs"] < 100


# ------------------------------------------------------------------ ledgers on disk
@pytest.mark.parametrize("symbol", mcs.available())
def test_every_ledger_is_well_formed_and_sourced(symbol):
    led = mcs.load_ledger(symbol)
    assert led["symbol"] == symbol and led["periods"]
    for p in led["periods"]:
        for it in p["items"]:
            assert it["metric"] in METRICS, (p["period"], it)
            assert it["kind"] in ("range", "approx", "floor", "ceiling")
            lo, hi = mcs.band(it)
            assert lo <= hi
            if it.get("mid") is not None and it["kind"] == "range":
                assert it["low"] <= it["mid"] <= it["high"], (p["period"], it["metric"])
            assert it.get("guide_source"), (p["period"], it["metric"], "guidance needs a source")
            if p.get("reported_on") and it.get("actual") is not None:
                assert it.get("actual_source"), (p["period"], it["metric"], "actual needs a source")
            assert len((it.get("guide_quote") or "").split()) <= 30
    for c in led.get("commitments", []):
        assert c["status"] in ("kept", "partial", "missed", "pending")
        assert c.get("made_on") and c.get("target")
        if c["status"] != "pending":
            assert c.get("graded_on") and c.get("evidence"), c.get("id")


def test_micron_scores_are_stable():
    res = mcs.score_ledger(mcs.load_ledger("MU"))
    by = {p["period"]: p for p in res["periods"]}
    assert len([p for p in res["periods"] if p["mcs"] is not None]) == 8
    assert by["FQ1 FY25"]["mcs"] == pytest.approx(92.0, abs=0.1)
    assert by["FQ3 FY26"]["mcs"] == pytest.approx(81.7, abs=0.1)
    assert res["average"] == pytest.approx(90.1, abs=0.1)
    assert by["FQ4 FY26"]["flags"], "the $918M discretionary opex must stay visible"
    assert all(r["outcome"] != "miss" for p in res["periods"] for r in p["items"]
               if r["metric"] in ("revenue", "gross_margin"))


def test_management_lens_uses_the_ledger_and_is_absent_without_one(tmp_path):
    lens = mcs.management_lens("MU")
    assert lens and 0 <= lens["score"] <= 100 and lens["why"][-1].startswith("→ M =")
    assert mcs.management_lens("NOPE", tmp_path) is None


# ------------------------------------------------------------------ transcript extraction
CALL = ("Thanks. Total revenue was approximately $7.8 billion, up 14% sequentially. "
        "Now turning to our guidance for the first quarter. We expect revenue to be $8.7 billion plus or minus "
        "$200 million. Gross margin to be in the range of 39.5% plus or minus 100 basis points and operating "
        "expenses to be approximately $1.085 billion plus or minus $15 million. Based on a share count of "
        "approximately 1.14 billion shares, we expect EPS to be $1.74 per share plus or minus $0.08.")


def test_extracts_plus_or_minus_guidance_and_ignores_reported_results():
    got = {i["metric"]: i for i in mcs_extract.parse_guidance(CALL)}
    assert (got["revenue"]["low"], got["revenue"]["high"]) == (8500, 8900)
    assert (got["gross_margin"]["low"], got["gross_margin"]["high"]) == (38.5, 40.5)
    assert (got["opex"]["low"], got["opex"]["high"]) == (1070, 1100)
    assert (got["eps"]["low"], got["eps"]["high"]) == (1.66, 1.82)
    assert all(i["actual"] is None and i["status"].startswith("draft") for i in got.values())


def test_extracts_to_ranges_and_approximate_guides():
    text = ("For the fourth quarter, we expect revenue in the range of $2.75 billion to $2.85 billion. "
            "We expect gross margin to be between 41% and 42%. We expect operating expenses of approximately "
            "$375 million. We expect EPS of $1.70 to $1.90 per share.")
    got = {i["metric"]: i for i in mcs_extract.parse_guidance(text)}
    assert (got["revenue"]["low"], got["revenue"]["high"]) == (2750, 2850)
    assert (got["gross_margin"]["low"], got["gross_margin"]["high"]) == (41, 42)
    assert got["opex"]["kind"] == "approx" and got["opex"]["mid"] == 375
    assert (got["eps"]["low"], got["eps"]["high"]) == (1.7, 1.9)


def test_load_transcript_accepts_a_saved_fmp_response(tmp_path):
    f = tmp_path / "t.json"
    f.write_text('[{"symbol": "MU", "period": "Q4", "year": 2024, "date": "2024-09-25", "content": "%s"}]' % CALL)
    t = mcs_extract.load_transcript(f)
    draft = mcs_extract.draft_period(t, "FQ1 FY25")
    assert draft["guided_on"] == "2024-09-25" and {i["metric"] for i in draft["items"]} >= {"revenue", "eps"}


# ------------------------------------------------------------------ quality engine hook
def test_quality_engine_reports_management_as_missing_without_a_ledger(monkeypatch):
    monkeypatch.setattr(mcs, "management_lens", lambda sym, directory=None: None)
    from tests.test_quality import MU, PEERS, TODAY
    snap = {"stocks": {"ZZZZ": {**MU, "sector": "Information Technology", "peers": ["LRCX", "AMAT"]}},
            "peers": PEERS}
    out = build.score_snapshot(snap, TODAY)["ZZZZ"]
    assert out["lenses"]["management"] is None and "management" in out["notes"]["missing"]
