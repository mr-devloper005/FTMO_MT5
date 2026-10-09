"""FMP -> PostgreSQL, one symbol at a time, without re-downloading what we already have.

Duplicate / incremental rules
- Statements: ``financial-reports-dates`` (one cheap call) lists the periods FMP has.
  Periods already stored for all three statements are not requested again; when
  some are missing only enough of the latest periods to cover them are fetched
  (this plan caps ``limit`` at 5). Re-delivered rows are upserted by content hash.
- Narrative reports (notes to the 10-Q/10-K): one document per period; a period
  already in rag.documents is never downloaded again (``force`` re-fetches).
- Snapshots (profile, earnings, estimates, ratings, ...): fetched at most once per
  ``refresh_hours``; unchanged records are not rewritten.
- Endpoints the plan refuses (HTTP 402) are logged as 'unsupported' and not
  retried for 24 hours.
Every call or deliberate skip is recorded in rag.fetch_log.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from rag import embed
from rag.config import PILOT, Settings
from rag.fmp import normalize as N
from rag.fmp import repo
from rag.fmp.client import (MAX_LIMIT, FmpAuthError, FmpClient, FmpError, FmpPlanRestricted, FmpQuotaExceeded,
                            public_url)
from rag.ingest import embedding_text
from rag.store import Store

REFRESH_HOURS = 12.0
REPORTS_PER_SYMBOL = 5             # latest 10-Q/10-K note sets embedded per company
TRANSCRIPTS_PER_SYMBOL = 4         # latest earnings calls embedded per company
NO_NOTES = "report has no narrative notes"
Q_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 5}

# dataset name -> (endpoint, params, key field)
SNAPSHOTS: Dict[str, Tuple[str, Dict[str, Any], Optional[str]]] = {
    "analyst_estimates_annual": ("analyst-estimates", {"period": "annual", "limit": MAX_LIMIT}, "date"),
    "key_metrics_annual": ("key-metrics", {"period": "annual", "limit": MAX_LIMIT}, "date"),
    "ratios_annual": ("ratios", {"period": "annual", "limit": MAX_LIMIT}, "date"),
    "key_metrics_ttm": ("key-metrics-ttm", {}, None),
    "ratios_ttm": ("ratios-ttm", {}, None),
    "grades_consensus": ("grades-consensus", {}, None),
    "price_target_consensus": ("price-target-consensus", {}, None),
    "key_executives": ("key-executives", {}, "name"),
    "stock_peers": ("stock-peers", {}, "symbol"),
}
# verified to exist but refused by the current plan (HTTP 402) on 2026-10-08
# probed for the record, not ingested: on the Ultimate plan this returns headlines + short summaries only
TEXT_ENDPOINTS_TO_PROBE = {"news/press-releases": "press releases (headline + short summary, not full text)"}


class Run:
    """Book-keeping for one symbol: calls FMP, logs every outcome, collects the summary."""

    def __init__(self, symbol: str, conn, client: FmpClient, log: Callable[[str], None], force: bool = False):
        self.symbol, self.conn, self.client, self.log, self.force = symbol, conn, client, log, force
        self.symbol_blocked: Optional[str] = None
        self.summary: Dict[str, Any] = {"symbol": symbol, "statements": {"inserted": 0, "updated": 0, "unchanged": 0},
                                        "earnings": {}, "company_data": {"inserted": 0, "updated": 0, "unchanged": 0},
                                        "documents_added": 0, "chunks_added": 0, "skipped": [], "unsupported": [],
                                        "errors": []}

    def fetch(self, endpoint: str, params: Dict[str, Any]) -> Optional[Any]:
        full = {"symbol": self.symbol, **params} if "symbols" not in params else params
        if self.symbol_blocked:                                  # plan excludes this ticker: stop calling
            self.skip(endpoint, params, self.symbol_blocked)
            return None
        prev = None if self.force else repo.known_unsupported(self.conn, self.symbol, endpoint, full)
        if prev:                                                 # (force re-checks: the plan may have changed)                                                 # the plan refused this < 24h ago: don't hammer
            self.summary["unsupported"].append(f"{endpoint}: not in current FMP plan "
                                               f"(HTTP {prev['http_status']}, checked {prev['fetched_at']:%Y-%m-%d %H:%M})")
            return None
        try:
            body = self.client.get(endpoint, **full)
        except FmpAuthError:
            repo.log_fetch(self.conn, self.symbol, endpoint, full, "error", 401, message="invalid API key")
            raise
        except FmpQuotaExceeded as e:                            # stop the whole run: every call would fail
            repo.log_fetch(self.conn, self.symbol, endpoint, full, "error", e.status, message=str(e))
            raise
        except FmpPlanRestricted as e:
            repo.log_fetch(self.conn, self.symbol, endpoint, full, "unsupported", e.status, message=str(e))
            self.summary["unsupported"].append(str(e))
            if "for 'symbol'" in str(e):
                self.symbol_blocked = f"{self.symbol} is not covered by the current FMP plan (HTTP {e.status})"
                self.summary["symbol_not_in_plan"] = True
                self.log(f"  {self.symbol}: ticker not covered by the current FMP plan - skipping its other endpoints")
            self.log(f"  {endpoint}: not in current FMP plan (HTTP {e.status})")
            return None
        except FmpError as e:
            repo.log_fetch(self.conn, self.symbol, endpoint, full, "error", e.status, message=str(e))
            self.summary["errors"].append(str(e))
            self.log(f"  {endpoint}: ERROR {e}")
            return None
        n = len(body) if isinstance(body, list) else (1 if body else 0)
        repo.log_fetch(self.conn, self.symbol, endpoint, full, "ok", 200, records=n)
        return body

    def skip(self, endpoint: str, params: Dict[str, Any], why: str) -> None:
        repo.log_fetch(self.conn, self.symbol, endpoint, {"symbol": self.symbol, **params}, "skipped", message=why)
        self.summary["skipped"].append(f"{endpoint}: {why}")

    def is_fresh(self, endpoint: str, params: Dict[str, Any], hours: float) -> bool:
        return repo.fresh(self.conn, self.symbol, endpoint, {"symbol": self.symbol, **params}, hours)


def _add(total: Dict[str, int], part: Dict[str, int]) -> None:
    for k, v in part.items():
        total[k] = total.get(k, 0) + v


def _catalog(run: Run, force: bool, hours: float) -> List[Tuple[int, str]]:
    """(fiscal_year, period) FMP has financial reports for, newest first. Stored in company_data."""
    ep, params = "financial-reports-dates", {}
    if force or not run.is_fresh(ep, params, hours):
        body = run.fetch(ep, params)
        if body:
            _add(run.summary["company_data"], repo.upsert(run.conn, "company_data", N.company_rows(
                run.symbol, "financial_report_dates", body, public_url(ep, {"symbol": run.symbol}))))
    else:
        run.skip(ep, params, f"catalog fetched < {hours:g}h ago")
    rows = repo.company(run.conn, run.symbol, "financial_report_dates")
    out = {(N._fy(r["data"].get("fiscalYear")), N._fq(r["data"].get("period"))) for r in rows}
    out = {p for p in out if p[0] and p[1]}
    out -= {(fy, "Q4") for fy, q in out if q == "FY"}        # FMP lists the same 10-K as both FY and Q4
    return sorted(out, key=lambda p: (p[0], Q_ORDER[p[1]]), reverse=True)


def _statements(run: Run, catalog: List[Tuple[int, str]], force: bool) -> None:
    stored_q = set(repo.stored_periods(run.conn, run.symbol, "quarter"))
    stored_a = set(repo.stored_periods(run.conn, run.symbol, "annual"))
    # quarterly statements label the fourth quarter Q4; the catalog lists the 10-K as FY
    want_q = list(dict.fromkeys((fy, "Q4" if p == "FY" else p) for fy, p in catalog))[:MAX_LIMIT]
    want_a = [(fy, "FY") for fy, p in catalog if p == "FY"][:MAX_LIMIT]
    for period_type, want, stored in (("quarter", want_q, stored_q), ("annual", want_a, stored_a)):
        missing = [w for w in want if w not in stored]
        if not force and want and not missing:
            for st, ep in N.STATEMENTS.items():
                run.skip(ep, {"period": period_type}, f"latest {len(want)} {period_type} periods already stored")
            continue
        # fetch just enough of the newest periods to cover the oldest missing one
        limit = MAX_LIMIT if (force or not want) else min(MAX_LIMIT, want.index(missing[-1]) + 1)
        for st, ep in N.STATEMENTS.items():
            params = {"period": period_type, "limit": limit}
            body = run.fetch(ep, params)
            if body is not None:
                rows = N.statement_rows(run.symbol, st, period_type, body,
                                        public_url(ep, {"symbol": run.symbol, "period": period_type}))
                _add(run.summary["statements"], repo.upsert(run.conn, "financial_statements", rows))
        run.log(f"  statements {period_type}: requested latest {limit} (missing {missing or 'n/a'})")


def _earnings(run: Run, force: bool, hours: float) -> None:
    ep, params = "earnings", {"limit": MAX_LIMIT}
    if not force and run.is_fresh(ep, params, hours):
        return run.skip(ep, params, f"fetched < {hours:g}h ago")
    body = run.fetch(ep, params)
    if body is not None:
        rows = N.earnings_rows(run.symbol, body, repo.quarter_ends(run.conn, run.symbol),
                               public_url(ep, {"symbol": run.symbol}))
        run.summary["earnings"] = repo.upsert(run.conn, "earnings", rows)


def _snapshots(run: Run, force: bool, hours: float) -> None:
    today = date.today()
    sets = dict(SNAPSHOTS)
    # this plan refuses a 365-day window ('from' is a premium value) but accepts year-to-date
    sets["sec_filings"] = ("sec-filings-search/symbol", {"from": date(today.year, 1, 1).isoformat(),
                                                         "to": today.isoformat(), "limit": MAX_LIMIT}, "link")
    for dataset, (ep, params, key) in sets.items():
        gate = {k: v for k, v in params.items() if k not in ("from", "to")}
        if not force and run.is_fresh(ep, gate, hours):
            run.skip(ep, gate, f"fetched < {hours:g}h ago")
            continue
        body = run.fetch(ep, params)
        if body is not None:
            # log under the gate params too, so the freshness check matches next run
            if gate != params:
                repo.log_fetch(run.conn, run.symbol, ep, {"symbol": run.symbol, **gate}, "ok", 200, len(body))
            rows = N.company_rows(run.symbol, dataset, body if isinstance(body, list) else [body],
                                  public_url(ep, {"symbol": run.symbol, **params}), key, today.isoformat())
            _add(run.summary["company_data"], repo.upsert(run.conn, "company_data", rows))


def _profile(run: Run, store: Store, force: bool, hours: float) -> str:
    ep = "profile"
    company = repo.company_name(run.conn, run.symbol) or run.symbol
    if not force and run.is_fresh(ep, {}, hours):
        run.skip(ep, {}, f"fetched < {hours:g}h ago")
        return company
    body = run.fetch(ep, {})
    if not body:
        return company
    url = public_url(ep, {"symbol": run.symbol})
    _add(run.summary["company_data"], repo.upsert(run.conn, "company_data",
                                                  N.company_rows(run.symbol, "profile", body, url)))
    doc, chunks = N.profile_document(run.symbol, body[0], url, date.today().isoformat())
    if force or repo.doc_exists(run.conn, doc["doc_id"]) != doc["sha256"]:
        _store_doc(run, store, doc, chunks)
    return body[0].get("companyName") or run.symbol


def _store_doc(run: Run, store: Store, doc: Dict[str, Any], chunks: List[Dict[str, Any]]) -> None:
    vecs = embed.passages([embedding_text(c) for c in chunks])
    store.replace_document(doc, chunks, vecs)
    run.summary["documents_added"] += 1
    run.summary["chunks_added"] += len(chunks)
    run.log(f"    indexed {doc['doc_id']}: {len(chunks)} chunks")


def _reports(run: Run, store: Store, company: str, catalog: List[Tuple[int, str]], force: bool, n: int) -> None:
    ep = "financial-reports-json"
    for fy, period in catalog[:n]:
        doc_id = f"FMP:report:{run.symbol}:{fy}:{period}"
        if not force and repo.doc_exists(run.conn, doc_id) is not None:
            run.skip(ep, {"year": fy, "period": period}, f"{doc_id} already stored")
            continue
        empty = repo.last_fetch(run.conn, run.symbol, ep, {"symbol": run.symbol, "year": fy, "period": period},
                                ("skipped",))
        if not force and empty and empty["message"] == NO_NOTES:
            run.skip(ep, {"year": fy, "period": period}, f"{NO_NOTES} (seen {empty['fetched_at']:%Y-%m-%d})")
            continue
        body = run.fetch(ep, {"year": fy, "period": period})
        if not isinstance(body, dict):
            continue
        doc, chunks = N.report_document(run.symbol, company, fy, period, body,
                                        repo.filing_date(run.conn, run.symbol, fy, period),
                                        public_url(ep, {"symbol": run.symbol, "year": fy, "period": period}))
        if chunks:
            _store_doc(run, store, doc, chunks)
        else:
            run.skip(ep, {"year": fy, "period": period}, NO_NOTES)


def _transcripts(run: Run, store: Store, company: str, force: bool, hours: float, n: int) -> None:
    """Latest ``n`` earnings calls. A call already stored is never downloaded again."""
    if n <= 0:
        return
    ep = "earning-call-transcript-dates"
    if force or not run.is_fresh(ep, {}, hours):
        body = run.fetch(ep, {})
        if body:
            rows = N.company_rows(run.symbol, "transcript_dates", body, public_url(ep, {"symbol": run.symbol}))
            for r in rows:                                        # key = fiscal period, stable across refreshes
                r["record_key"] = f"{r['data'].get('fiscalYear')}-Q{r['data'].get('quarter')}"
            _add(run.summary["company_data"], repo.upsert(run.conn, "company_data", rows))
    else:
        run.skip(ep, {}, f"fetched < {hours:g}h ago")
    calls = [(N._fy(r["data"].get("fiscalYear")), r["data"].get("quarter"), r["data"].get("date"))
             for r in repo.company(run.conn, run.symbol, "transcript_dates")]
    calls = sorted({c for c in calls if c[0] and c[1]}, key=lambda c: (c[0], int(c[1])), reverse=True)[:n]
    ep = "earning-call-transcript"
    for fy, q, _ in calls:
        doc_id = f"FMP:transcript:{run.symbol}:{fy}:Q{q}"
        if not force and repo.doc_exists(run.conn, doc_id) is not None:
            run.skip(ep, {"year": fy, "quarter": int(q)}, f"{doc_id} already stored")
            continue
        body = run.fetch(ep, {"year": fy, "quarter": int(q)})
        if not body or not isinstance(body, list) or not body[0].get("content"):
            continue
        doc, chunks = N.transcript_document(run.symbol, company, body[0], public_url(
            ep, {"symbol": run.symbol, "year": fy, "quarter": int(q)}))
        if chunks:
            _store_doc(run, store, doc, chunks)
            run.summary["transcripts_added"] = run.summary.get("transcripts_added", 0) + 1


def _probe_text_endpoints(run: Run) -> None:
    """Record whether the plan allows the remaining text endpoints. Nothing is ingested from them."""
    for ep, what in TEXT_ENDPOINTS_TO_PROBE.items():
        prev = None if run.force else repo.known_unsupported(run.conn, run.symbol, ep)
        if prev:
            run.summary["unsupported"].append(f"{ep}: {what} not in current FMP plan (checked {prev['fetched_at']:%Y-%m-%d %H:%M})")
            continue
        params = {"symbols": run.symbol, "limit": 1} if ep.startswith("news/") else {}
        body = run.fetch(ep, params)
        if body is not None:
            run.summary.setdefault("notes", []).append(f"{ep}: available on this plan - {what}; not ingested")


def ingest_symbol(symbol: str, settings: Optional[Settings] = None, client: Optional[FmpClient] = None,
                  force: bool = False, refresh_hours: float = REFRESH_HOURS, reports: int = REPORTS_PER_SYMBOL,
                  transcripts: int = TRANSCRIPTS_PER_SYMBOL,
                  log: Callable[[str], None] = print) -> Dict[str, Any]:
    settings = settings or Settings()
    settings.ensure_dirs()
    t0 = time.time()
    symbol = symbol.upper()
    client = client or FmpClient(raw_dir=settings.raw_dir / "fmp")
    calls0 = client.calls
    with Store(settings.pg_config()) as store:
        run = Run(symbol, store.db, client, log, force=force)
        log(f"{symbol}:")
        company = _profile(run, store, force, refresh_hours)
        catalog = _catalog(run, force, refresh_hours)
        _statements(run, catalog, force)
        _earnings(run, force, refresh_hours)
        _snapshots(run, force, refresh_hours)
        _reports(run, store, company, catalog, force, reports)
        _transcripts(run, store, company, force, refresh_hours, transcripts)
        _probe_text_endpoints(run)
    run.summary.update(api_calls=client.calls - calls0, seconds=round(time.time() - t0, 1))
    return run.summary


def ingest_all(symbols=PILOT, **kw) -> List[Dict[str, Any]]:
    symbols = list(symbols)
    client = kw.pop("client", None)
    settings = kw.pop("settings", None) or Settings()
    client = client or FmpClient(raw_dir=settings.raw_dir / "fmp")
    out = []
    for s in symbols:
        try:
            out.append(ingest_symbol(s, settings, client, **kw))
        except FmpAuthError:
            raise
        except FmpQuotaExceeded as e:
            out.append({"symbol": s, "errors": [str(e)]})
            out += [{"symbol": rest, "errors": ["not attempted: FMP quota used up"]}
                    for rest in symbols[symbols.index(s) + 1:]]
            break
        except Exception as e:                                   # noqa: BLE001 - one symbol must not stop the rest
            out.append({"symbol": s, "errors": [f"{type(e).__name__}: {e}"]})
    return out
