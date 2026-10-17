"""Real-data test: TSLA from the live FMP API into the *_test PostgreSQL database.

Needs FMP_API_KEY (shell or FTMO .env) and the test database; skipped otherwise,
or when RAG_SKIP_LIVE=1. Uses ~25 API calls on the first run."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rag import config, retrieve, structured  # noqa: E402
from rag.fmp import repo  # noqa: E402
from rag.fmp.client import FmpAuthError, FmpClient, FmpQuotaExceeded  # noqa: E402
from rag_fixtures import cfg, store  # noqa: E402,F401  (pytest fixtures)


@pytest.fixture
def live():
    if os.environ.get("RAG_SKIP_LIVE") == "1":
        pytest.skip("RAG_SKIP_LIVE=1")
    try:
        config.fmp_api_key()
        FmpClient().get("profile", symbol="TSLA")                    # one call: key valid and quota left?
    except config.MissingCredential as e:
        pytest.skip(str(e))
    except FmpQuotaExceeded as e:
        pytest.skip(f"FMP quota used up for now: {e}")


def test_live_bad_key_is_rejected(live):
    with pytest.raises(FmpAuthError):
        FmpClient(api_key="definitely-not-a-valid-key").get("profile", symbol="TSLA")


def test_live_tsla_ingest_dedupe_retrieve_and_answer_evidence(live, cfg, store, tmp_path):
    from rag.fmp.ingest import ingest_symbol
    s = config.Settings(state_dir=tmp_path, pg=cfg)
    first = ingest_symbol("TSLA", s, reports=1, transcripts=1, log=lambda *_: None)
    assert first["statements"]["inserted"] >= 15 and first["documents_added"] >= 2, first
    assert not first.get("symbol_not_in_plan") and not first["errors"], first

    # the stored value is exactly what the API returns now
    api = FmpClient().get("income-statement", symbol="TSLA", period="quarter", limit=1)[0]
    db = repo.statements(store.db, "TSLA", "quarter", latest=1)
    income = next(r for r in db if r["statement"] == "income")
    assert str(income["period_end"]) == api["date"] and income["data"]["revenue"] == api["revenue"]

    # nothing new: no statement or report downloads, no new rows
    again = ingest_symbol("TSLA", s, reports=1, transcripts=1, log=lambda *_: None)
    assert again["statements"] == {"inserted": 0, "updated": 0, "unchanged": 0}
    assert again["documents_added"] == 0 and again["api_calls"] <= 3, again

    # narrative retrieval over the real notes, with FMP provenance
    hits = retrieve.search("debt and credit facilities", symbol="TSLA", store=store, k=3)["hits"]
    assert hits and all(h["source"] == "FMP" and "apikey" not in h["source_url"] for h in hits)

    # structured evidence carries the API's number
    ev = structured.evidence(store.db, "TSLA", "What was Tesla's latest revenue?")
    assert any(f["field"] == "revenue" and f["value"] == float(api["revenue"]) for e in ev for f in e["facts"])

    # transcripts: on a plan that includes them (Ultimate), the latest call is stored once and searchable;
    # on a plan that refuses them the refusal is recorded honestly
    # (the second ingest skips the call when the dates are fresh, so read the latest real fetch)
    logged = store.db.execute("SELECT outcome FROM rag.fetch_log WHERE endpoint='earning-call-transcript-dates' "
                              "AND outcome <> 'skipped' ORDER BY fetched_at DESC LIMIT 1").fetchone()
    assert logged and logged["outcome"] in ("unsupported", "ok")
    if logged["outcome"] == "ok":
        assert first.get("transcripts_added") == 1 and not again.get("transcripts_added")
        hits = retrieve.search("What did management say about demand?", symbol="TSLA", store=store, k=5,
                               doc_types=["TRANSCRIPT"])["hits"]
        assert hits and all(h["form"] == "TRANSCRIPT" and h["section"].startswith("Earnings call") for h in hits)
