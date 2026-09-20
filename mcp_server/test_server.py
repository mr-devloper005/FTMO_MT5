"""Calibration tests for the MCP tool layer -- same convention as
tests/test_lab.py: known results from real research this session, reproduced
through the tool functions rather than the raw Lab calls, to prove the
wrapping layer doesn't change the numbers underneath it.

Requires the same cached data study/lab/cache/ already has from this
session's research (LITE/1h, AMAT/1h). If that cache is ever cleared, these
calls will raise a clear "not cached" error rather than silently drifting --
that's the allow_network=False guard doing its job, not a test bug.

Run: PYTHONPATH=. venv/bin/python3 -m pytest mcp_server/test_server.py -v
"""
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server.server import (
    lab_list_strategies,
    lab_run_backtest,
    lab_run_batch,
    lab_run_matrix,
    lab_validate_strategy,
)


def test_list_strategies_returns_the_real_catalog():
    strategies = lab_list_strategies()
    names = {s["name"] for s in strategies}
    assert "MEAN_REVERSION" in names
    # The hold-until-broken family registers into the same catalog.
    assert {"HOLD_UNTIL_BROKEN", "BUY_AND_HOLD_BASELINE"} <= names
    assert len(strategies) == 16  # HIDDEN entries (EMA_RSI_PATTERN) excluded, per strategies.py


def test_validate_reproduces_the_known_lite_mean_reversion_result():
    """LITE/1h/MEAN_REVERSION was validated by hand earlier this session:
    10 trades, FRAGILE, beats B&H in-sample by +52.3pp. If this ever comes
    back different, either the cache changed or the wrapper broke something --
    either way, worth knowing, not silently drifting."""
    out = lab_validate_strategy("LITE", "1h", "MEAN_REVERSION", allow_network=False)
    assert out["verdict"] == "FRAGILE"
    assert out["full"]["trades"] == 10
    assert out["full"]["beatsBH"] is True
    assert out["full"]["vsBHPct"] == pytest.approx(52.28, abs=0.05)


def test_run_backtest_reproduces_the_known_amat_mean_reversion_result():
    out = lab_run_backtest("AMAT", "1h", "MEAN_REVERSION", allow_network=False)
    assert out["trades"] == 11
    assert out["beatsBH"] is True
    assert out["vsBHPct"] == pytest.approx(8.18, abs=0.05)


def test_run_matrix_default_is_condensed_not_raw_cells():
    out = lab_run_matrix(["AMAT", "AMD"], ["1h", "2h"],
                         strategies=["MEAN_REVERSION", "EMA_REGIME_LS"], allow_network=False)
    assert set(out.keys()) == {"cellCount", "beatCount", "beatRatePct", "thinSampleCount",
                               "errorCount", "topByAlpha", "errors", "note"}
    assert out["cellCount"] == 8


def test_run_matrix_full_detail_returns_every_cell():
    out = lab_run_matrix(["AMAT"], ["1h"], strategies=["MEAN_REVERSION"],
                         allow_network=False, full_detail=True)
    assert "cells" in out and len(out["cells"]) == 1


def test_run_batch_returns_every_result_in_request_order():
    """Same two known-good calls as the single-run tests above, dispatched
    together. Order must survive the round trip through the process pool even
    though workers can finish out of order."""
    out = lab_run_batch([
        {"symbol": "AMAT", "timeframe": "1h", "strategy": "MEAN_REVERSION"},
        {"symbol": "LITE", "timeframe": "1h", "strategy": "MEAN_REVERSION"},
    ], allow_network=False)
    assert out["count"] == 2 and out["okCount"] == 2 and out["errorCount"] == 0
    assert [r["index"] for r in out["results"]] == [0, 1]
    assert out["results"][0]["result"]["trades"] == 11
    assert out["results"][1]["result"]["trades"] == 10


def test_run_batch_isolates_a_bad_request_from_the_rest():
    """One uncached symbol must not take the other, good requests down with
    it -- the whole point of a batch is that a single bad cell costs only
    itself, same as lab_run_matrix already guarantees for its cells."""
    out = lab_run_batch([
        {"symbol": "AMAT", "timeframe": "1h", "strategy": "MEAN_REVERSION"},
        {"symbol": "DEFINITELY_NOT_A_REAL_TICKER", "timeframe": "1h",
         "strategy": "MEAN_REVERSION"},
    ], allow_network=False)
    assert out["okCount"] == 1 and out["errorCount"] == 1
    ok_row = next(r for r in out["results"] if "result" in r)
    bad_row = next(r for r in out["results"] if "error" in r)
    assert ok_row["result"]["trades"] == 11
    assert "Not cached" in bad_row["error"]


def test_run_batch_rejects_more_than_ten_requests():
    with pytest.raises(ToolError, match="over the 10-call cap"):
        lab_run_batch([{"symbol": "AMAT", "timeframe": "1h",
                        "strategy": "MEAN_REVERSION"}] * 11, allow_network=False)


def test_run_batch_rejects_a_request_missing_required_keys():
    with pytest.raises(ToolError, match=r"requests\[0\] missing"):
        lab_run_batch([{"symbol": "AMAT", "timeframe": "1h"}], allow_network=False)


def test_uncached_symbol_is_refused_not_silently_fetched():
    """ToolError, not a bare exception: a designed refusal must reach the agent
    as a readable error, not as an UnexpectedToolError crash traceback."""
    with pytest.raises(ToolError, match="Not cached"):
        lab_run_backtest("DEFINITELY_NOT_A_REAL_TICKER", "1h", "MEAN_REVERSION",
                         allow_network=False)


def test_unknown_timeframe_is_refused_with_the_real_list():
    with pytest.raises(ToolError, match="Unknown timeframe"):
        lab_run_backtest("AMAT", "13h", "MEAN_REVERSION", allow_network=False)


def test_oversized_matrix_raises_rather_than_silently_truncating():
    """The Lab's 300-cell cap, surfaced as a clean ToolError carrying its own
    advice about which axis to cut -- not a raw TooManyCells crash."""
    with pytest.raises(ToolError, match="over the 300-run cap"):
        lab_run_matrix(["AMAT"] * 20, ["1h", "2h", "4h", "8h"],
                       strategies=["MEAN_REVERSION"] * 5, allow_network=True)


# --- context economy: the reason the trimming defaults exist -------------

def test_validate_is_trimmed_by_default_and_stays_useful():
    """Measured: the untrimmed payload is ~18,000 tokens, almost all of it the
    candle array and equity curve the caller supplied. Trimming must be the
    default, and must not cost anything that matters."""
    import json
    lean = lab_validate_strategy("LITE", "1h", "MEAN_REVERSION", allow_network=False)
    fat = lab_validate_strategy("LITE", "1h", "MEAN_REVERSION", allow_network=False,
                                full_detail=True)
    assert len(json.dumps(lean, default=str)) * 5 < len(json.dumps(fat, default=str))
    # everything a decision actually needs survives the trim
    assert lean["verdict"] == "FRAGILE"
    assert lean["full"]["trades"] == 10
    assert lean["foldsRun"] == 5
    assert "detailOmitted" in lean["full"]
    assert "candles" not in lean["full"] and "equity" not in lean["full"]
    assert "candles" in fat["full"]


def test_backtest_is_trimmed_by_default():
    import json
    lean = lab_run_backtest("AMAT", "1h", "MEAN_REVERSION", allow_network=False)
    fat = lab_run_backtest("AMAT", "1h", "MEAN_REVERSION", allow_network=False,
                           full_detail=True)
    assert len(json.dumps(lean, default=str)) * 10 < len(json.dumps(fat, default=str))
    assert lean["trades"] == 11 and lean["beatsBH"] is True


def test_lookup_reports_a_tally_before_rows():
    """'Tested 30 times, all FAILED' should cost one answer, not thirty rows."""
    from mcp_server.server import lab_lookup
    out = lab_lookup("LITE", "1h", "MEAN_REVERSION")
    assert out["tested"] is True
    assert out["matchCount"] >= 1
    assert isinstance(out["verdictTally"], dict)


def test_lookup_on_something_never_tested_says_so():
    from mcp_server.server import lab_lookup
    out = lab_lookup("NO_SUCH_SYMBOL_XYZ")
    assert out["tested"] is False and out["matchCount"] == 0


# --- interface reference resources ---------------------------------------

def test_reference_resources_render_and_stay_compact():
    """The whole point is being cheaper than reading the source it replaces
    (metrics.py + verdict.py + windows.py, ~830 lines). If these ever grow
    past a few thousand characters they have stopped paying for themselves."""
    from mcp_server import reference
    for fn in (reference.overview_reference, reference.metrics_reference,
               reference.timeframes_reference):
        text = fn()
        assert text.strip()
        assert len(text) < 4000, f"{fn.__name__} has grown too large to be worth reading"


def test_reference_is_derived_from_live_code_not_hand_copied():
    """Three documents in this repo already drifted from the code. The numbers
    here must come from the modules that own them, so this one cannot."""
    from ftmo_mt5.lab import verdict as v
    from ftmo_mt5.lab import service as svc
    from mcp_server import reference
    text = reference.overview_reference()
    assert f"${v.FLAT_BENCHMARK:,.0f}" in text
    assert str(v.FOLDS) in text
    # every registered strategy is listed, including ones added later
    for name in svc.strategy_defaults():
        assert name in text


def test_metric_dictionary_reconciles_against_a_real_run():
    """A metric added to measure() without a doc entry must announce itself
    rather than quietly going undocumented."""
    from mcp_server import reference
    assert reference._reconcile() == [], reference._reconcile()
