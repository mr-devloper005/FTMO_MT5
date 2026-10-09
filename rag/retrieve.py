"""Hybrid retrieval: metadata filters first (company, document type, fiscal
year/quarter), then semantic (pgvector cosine over local embeddings) and keyword
(PostgreSQL full-text) rankings fused with reciprocal-rank fusion. Keyword search
matters for filings: exact terms like 'HBM', 'Item 1A' or 'diluted' beat paraphrase."""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Sequence

from rag import embed
from rag.config import Settings, parse_period
from rag.store import Store

RRF_K = 60
CANDIDATES = 60
FORM_ALIASES = {"10-k": "10-K", "annual": "10-K", "10-q": "10-Q", "quarterly": "10-Q",
                "8-k": "8-K", "earnings": "8-K", "release": "8-K", "press": "8-K",
                "transcript": "TRANSCRIPT", "call": "TRANSCRIPT", "earnings-call": "TRANSCRIPT"}
COMPARE = re.compile(r"\b(compar\w*|previous|prior|versus|last two|quarter[- ]over[- ]quarter|sequential\w*)\b", re.I)
LATEST = re.compile(r"\b(latest|most recent|current|this quarter|last quarter|recently reported)\b", re.I)
STOP = set("""a an and are as at be by did do does for from has have how in is it its of on or that the this to
was were what when which who why will with about company their they than then there these those our over
quarter fiscal year report reported said says management""".split())
Q_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 4}


def forms_from(doc_types: Optional[Sequence[str]]) -> Optional[List[str]]:
    if not doc_types:
        return None
    return sorted({FORM_ALIASES.get(d.lower(), d.upper()) for d in doc_types})


def keyword_terms(q: str) -> List[str]:
    """Distinct content words of the question; the store matches ANY of them."""
    toks = [t for t in re.findall(r"[A-Za-z][A-Za-z0-9&\-]{2,}", q) if t.lower() not in STOP]
    return list(dict.fromkeys(toks))


def recent_periods(store: Store, symbol: str, forms, n: int) -> List[Dict[str, Any]]:
    ps = sorted({p for p in store.periods(symbol, forms) if p[0]},
                key=lambda p: (p[0], Q_ORDER.get(p[1], 0)), reverse=True)
    return [{"fiscal_year": fy, "fiscal_quarter": fq} for fy, fq in ps[:n]]


def latest_period(store: Store, symbol: str, forms) -> Dict[str, Any]:
    ps = recent_periods(store, symbol, forms, 1)
    return ps[0] if ps else {}


def _ranked(store: Store, query: str, filters: Dict[str, Any], k: int):
    """Semantic + keyword rankings under one filter set, fused (RRF). Returns (hits, candidates)."""
    candidates = store.count(filters)
    sem_hits = store.semantic(embed.query(query), filters, CANDIDATES) if candidates else []
    sem = [cid for cid, _ in sem_hits]
    sim_of = dict(sem_hits)
    kw = store.keyword(keyword_terms(query), filters, CANDIDATES) if candidates else []
    fused: Dict[str, float] = {}
    for ranking in (sem, kw):
        for r, cid in enumerate(ranking):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + r + 1)
    best = sorted(fused, key=fused.get, reverse=True)[:k]
    hits = store.chunks(best)
    for h in hits:
        h["score"] = round(fused[h["chunk_id"]], 5)
        h["similarity"] = round(sim_of.get(h["chunk_id"], 0.0), 4)
        h["keyword_rank"] = kw.index(h["chunk_id"]) + 1 if h["chunk_id"] in kw else None
    return hits, candidates


def search(query: str, settings: Optional[Settings] = None, symbol: Optional[str] = None,
           doc_types: Optional[Sequence[str]] = None, fiscal_year: Optional[int] = None,
           fiscal_quarter: Optional[str] = None, k: int = 8, auto_period: bool = True,
           store: Optional[Store] = None) -> Dict[str, Any]:
    """Returns {'hits': [chunk + metadata + scores], 'filters': {...}, 'ms': ...}.
    'Compare the previous quarter with the latest' style questions get evidence from
    the two most recent periods, half of k from each."""
    t0 = time.time()
    settings = settings or Settings()
    own = store is None
    store = store or Store(settings.pg_config())
    forms = forms_from(doc_types)
    applied = "explicit" if (fiscal_year or fiscal_quarter) else "none"
    periods: List[Dict[str, Any]] = []
    if auto_period and not (fiscal_year or fiscal_quarter):
        fiscal_year, fiscal_quarter = parse_period(query)
        if fiscal_year:
            applied = "parsed from question"
        elif symbol and COMPARE.search(query):
            periods = recent_periods(store, symbol, forms, 2)
            applied = "two latest periods (comparison)" if len(periods) == 2 else "none"
        elif symbol and LATEST.search(query):
            lp = latest_period(store, symbol, forms)
            fiscal_year, fiscal_quarter = lp.get("fiscal_year"), lp.get("fiscal_quarter")
            applied = "latest available period" if lp else "none"
    filters = {"symbol": symbol, "forms": forms, "fiscal_year": fiscal_year, "fiscal_quarter": fiscal_quarter}
    if len(periods) == 2:
        hits, candidates = [], 0
        for p in periods:
            h, c = _ranked(store, query, {**filters, **p}, max(2, k // 2))
            hits += h
            candidates += c
        report = {**filters, "periods": periods}
    else:
        hits, candidates = _ranked(store, query, filters, k)
        report = filters
    if own:
        store.close()
    return {"hits": hits, "filters": {**report, "period_filter": applied}, "candidates": candidates,
            "ms": round((time.time() - t0) * 1000)}


# Questions about what management said or expects are answered from earnings calls first. "Guidance" alone is
# ambiguous in filings (FASB/ASU accounting guidance fills 10-Q notes), so these go to the latest call(s).
MANAGEMENT = re.compile(r"\b(guid\w*|outlook|expect\w*|forecast\w*|said|say|says|commentary|comment\w*|tone|"
                        r"demand|management|ceo|cfo|executive\w*|leadership|strateg\w*|plans?|priorit\w*|call)\b", re.I)
# call housekeeping that matches "outlook"/"expect" but says nothing (safe harbor, webcast, replay, IR intro)
BOILERPLATE = re.compile(r"forward[- ]looking statements|safe harbor|webcast|replay|investor relations website|"
                         r"operator instructions|please limit yourself|slides? (?:are|will be) available", re.I)
# a chunk that actually guides: forward-looking wording plus a figure ("we expect revenue of $X to $Y")
GUIDES = re.compile(r"\b(we expect|we now expect|expect(?:s|ed)? (?:to|revenue|margin|operating|capex|capital)|guidance|"
                    r"outlook|for (?:the )?(?:next|coming|first|second|third|fourth) quarter|full[- ]year|"
                    r"fiscal (?:year )?20\d\d)\b", re.I)
HAS_FIGURE = re.compile(r"\$\s?\d|\d+(?:\.\d+)?\s?(?:%|percent|billion|million)", re.I)
GUIDANCE = re.compile(r"\b(guid\w*|outlook|expect\w*|forecast\w*|next quarter|upcoming)\b", re.I)


def management_first(query: str, settings: Optional[Settings] = None, symbol: Optional[str] = None,
                     k: int = 8, store: Optional[Store] = None, transcript_share: float = 0.625,
                     **kw) -> Dict[str, Any]:
    """Earnings-call evidence first (latest call, or the two latest when comparing), then everything else.
    Falls back to plain ``search`` when the company has no stored transcripts."""
    own = store is None
    store = store or Store((settings or Settings()).pg_config())
    try:
        if not symbol or not store.count({"symbol": symbol, "forms": ["TRANSCRIPT"]}):
            return search(query, settings, symbol, k=k, store=store, **kw)
        guidance = bool(GUIDANCE.search(query))
        kt = max(2, round(k * (0.75 if guidance else transcript_share)))
        tq = query + (" we expect next quarter outlook range" if guidance else "")

        def call_hits(**f):
            r = search(tq, settings, symbol, doc_types=["TRANSCRIPT"], k=kt * 3, store=store, **f)
            keep = [h for h in r["hits"] if not BOILERPLATE.search(h["text"])]
            if guidance:                                         # chunks that state guidance first, order kept
                keep.sort(key=lambda h: not (GUIDES.search(h["text"]) and HAS_FIGURE.search(h["text"])))
            if r["filters"].get("periods"):                      # comparing calls: keep both calls represented
                per = {}
                for h in keep:
                    per.setdefault((h["fiscal_year"], h["fiscal_quarter"]), []).append(h)
                share = max(1, kt // max(1, len(per)))
                keep = [h for hs in per.values() for h in hs[:share]]
            r["hits"] = keep[:kt]
            return r

        calls = call_hits(auto_period=True, **{x: kw[x] for x in ("fiscal_year", "fiscal_quarter") if kw.get(x)})
        if calls["filters"].get("period_filter") == "none":          # no period named: use the latest call
            lp = latest_period(store, symbol, ["TRANSCRIPT"])
            calls = call_hits(fiscal_year=lp.get("fiscal_year"), fiscal_quarter=lp.get("fiscal_quarter"))
        rest = search(query, settings, symbol, k=k, store=store, **kw)
        seen = {h["chunk_id"] for h in calls["hits"]}
        others = [h for h in rest["hits"] if h["chunk_id"] not in seen
                  and not (h["form"] == "TRANSCRIPT" and BOILERPLATE.search(h["text"]))]
        hits = calls["hits"] + others[:max(0, k - len(calls["hits"]))]
        return {"hits": hits, "filters": {**rest["filters"], "transcripts": calls["filters"]},
                "candidates": calls["candidates"] + rest["candidates"], "ms": calls["ms"] + rest["ms"],
                "strategy": "management-first (earnings calls, then all documents)"}
    finally:
        if own:
            store.close()


def cite(h: Dict[str, Any]) -> str:
    from rag.config import period_label
    sec = h["section"] + (f" > {h['subsection']}" if h.get("subsection") else "")
    via = f" via {h['source']}" if h.get("source") and h["source"] != "SEC EDGAR" else ""
    when = f", filed {h['filing_date']}" if h.get("filing_date") else ""
    page = f", p.{h['page']}" if h.get("page") is not None else ""
    return (f"{h['symbol']} {h['form']}{via} ({period_label(h['fiscal_year'], h['fiscal_quarter'])}{when}), "
            f"{sec}{page} - {h['source_url']}")
