"""SEC EDGAR collector: official, free, no API key.

Endpoints (sec.gov/search-filings/edgar-application-programming-interfaces and
sec.gov/os/accessing-edgar-data, checked 2026-10-07):
  https://www.sec.gov/files/company_tickers.json                 ticker -> CIK
  https://data.sec.gov/submissions/CIK##########.json            filing history
  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json  XBRL facts (fiscal year/period per filing)
  https://www.sec.gov/Archives/edgar/data/<cik>/<accession>/...  the filed documents
Fair access: a declared User-Agent on every request, at most 10 requests/second.

Every response is written to ``state/raw`` and reused, so re-running ingest
costs no requests for documents already on disk.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests

from rag.config import SEC_MAX_RPS, Settings, sec_user_agent

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"
INDEX = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{accession}-index.htm"

EARNINGS_ITEM = "2.02"            # 8-K Item 2.02 "Results of Operations and Financial Condition"


class SecClient:
    def __init__(self, user_agent: Optional[str] = None, max_rps: float = SEC_MAX_RPS):
        self.ua = user_agent or sec_user_agent()
        self.gap = 1.0 / max_rps
        self._last = 0.0
        self.requests = 0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.ua, "Accept-Encoding": "gzip, deflate"})

    def get(self, url: str, cache: Optional[Path] = None, max_age_days: Optional[float] = None) -> bytes:
        if cache is not None and cache.exists():
            fresh = max_age_days is None or (time.time() - cache.stat().st_mtime) < max_age_days * 86400
            if fresh:
                return cache.read_bytes()
        for attempt in range(4):
            wait = self._last + self.gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            r = self.session.get(url, timeout=60)
            if r.status_code in (429, 503):                       # throttled: back off, never hammer
                time.sleep(2 ** attempt * 2)
                continue
            if r.status_code == 403 and b"Undeclared Automated Tool" in r.content:
                raise RuntimeError("SEC rejected the User-Agent as undeclared. Check SEC_USER_AGENT.")
            r.raise_for_status()
            if cache is not None:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(r.content)
            return r.content
        raise RuntimeError(f"SEC kept throttling {url}; try again later")

    def json(self, url: str, cache: Optional[Path] = None, max_age_days: Optional[float] = None):
        return json.loads(self.get(url, cache, max_age_days))


@dataclass
class Filing:
    symbol: str
    company: str
    cik: int
    form: str                    # 10-K | 10-Q | 8-K
    accession: str               # 0000723125-26-000012
    filing_date: str
    report_date: str             # period end (10-K/10-Q) or event date (8-K)
    primary_doc: str
    items: str = ""
    fiscal_year: Optional[int] = None
    fiscal_quarter: Optional[str] = None     # Q1..Q4 | FY

    @property
    def acc_nodash(self) -> str:
        return self.accession.replace("-", "")

    def url(self, name: Optional[str] = None) -> str:
        return ARCHIVE.format(cik=self.cik, acc=self.acc_nodash, name=name or self.primary_doc)

    def folder(self, raw_dir: Path) -> Path:
        return raw_dir / self.symbol / f"{self.form}_{self.filing_date}_{self.acc_nodash}"


# ------------------------------------------------------------------ lookups
def cik_for(client: SecClient, settings: Settings, symbol: str) -> Tuple[int, str]:
    data = client.json(TICKERS_URL, settings.raw_dir / "company_tickers.json", max_age_days=7)
    for row in data.values():
        if row["ticker"].upper() == symbol.upper():
            return int(row["cik_str"]), row["title"]
    raise KeyError(f"{symbol} is not in the SEC ticker list")


def fiscal_periods(facts: dict) -> Dict[str, Tuple[int, str]]:
    """accession -> (fiscal year, fiscal period) as tagged by the company in its XBRL."""
    out: Dict[str, Tuple[int, str]] = {}
    for taxonomy in facts.get("facts", {}).values():
        for concept in taxonomy.values():
            for unit in concept.get("units", {}).values():
                for f in unit:
                    a, fy, fp = f.get("accn"), f.get("fy"), f.get("fp")
                    if a and fy and fp and a not in out and f.get("form", "").startswith(("10-K", "10-Q")):
                        out[a] = (int(fy), "FY" if fp == "FY" else fp)
    return out


NEXT_Q = {"Q1": "Q2", "Q2": "Q3", "Q3": "FY", "FY": "Q1"}


def period_for_release(filed: str, periods: List[Tuple[str, int, str]]) -> Tuple[Optional[int], Optional[str]]:
    """Fiscal period an earnings release reports on: the latest period that ended
    before the release (released within ~75 days). If that period's 10-Q/10-K is
    not filed yet, step one quarter past the latest known period."""
    d = date.fromisoformat(filed)
    known = sorted(periods)                                            # (period_end, fy, fp)
    best = [p for p in known if date.fromisoformat(p[0]) < d <= date.fromisoformat(p[0]) + timedelta(days=75)]
    if best:
        _, fy, fp = best[-1]
        return fy, ("Q4" if fp == "FY" else fp)
    if known:
        end, fy, fp = known[-1]
        nxt_end = date.fromisoformat(end) + timedelta(days=91)
        if nxt_end - timedelta(days=10) < d <= nxt_end + timedelta(days=75):
            nfp = NEXT_Q[fp]
            return (fy + 1 if fp == "FY" else fy), ("Q4" if nfp == "FY" else nfp)
    return None, None


def select_filings(symbol: str, company: str, cik: int, subs: dict, limits: Dict[str, int],
                   periods: Dict[str, Tuple[int, str]]) -> List[Filing]:
    r = subs["filings"]["recent"]
    rows = [dict(zip(r.keys(), vals)) for vals in zip(*r.values())]
    picked: Dict[str, List[Filing]] = {k: [] for k in limits}
    for row in rows:                                                   # newest first
        form = row["form"]
        if form not in limits or len(picked[form]) >= limits[form]:
            continue
        if form == "8-K" and EARNINGS_ITEM not in (row.get("items") or ""):
            continue
        fy, fq = periods.get(row["accessionNumber"], (None, None))
        picked[form].append(Filing(symbol, company, cik, form, row["accessionNumber"], row["filingDate"],
                                   row.get("reportDate") or row["filingDate"], row["primaryDocument"],
                                   row.get("items") or "", fy, fq))
    known = [(f.report_date, f.fiscal_year, f.fiscal_quarter)
             for f in picked.get("10-K", []) + picked.get("10-Q", []) if f.fiscal_year]
    # periods of older filings too, so releases older than our 10-Q window still resolve
    for row in rows:
        p = periods.get(row["accessionNumber"])
        if p and row["form"] in ("10-K", "10-Q") and row.get("reportDate"):
            known.append((row["reportDate"], p[0], p[1]))
    for f in picked.get("8-K", []):
        f.fiscal_year, f.fiscal_quarter = period_for_release(f.filing_date, list(set(known)))
    return [f for k in limits for f in picked[k]]


def earnings_exhibits(index_html: str) -> List[str]:
    """Documents typed EX-99.x in an 8-K's filing index (the press release, CFO commentary)."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", index_html, flags=re.S | re.I):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S | re.I)
        if len(cells) < 4:
            continue
        doc_type = re.sub(r"<[^>]+>", "", cells[3]).strip().upper()
        href = re.search(r'href="([^"]+)"', cells[2])
        if doc_type.startswith("EX-99") and href and href.group(1).lower().endswith((".htm", ".html", ".txt")):
            out.append(href.group(1).rsplit("/", 1)[-1])
    return out


# ------------------------------------------------------------------ download
def download(symbol: str, settings: Settings, client: Optional[SecClient] = None,
             log=print) -> List[Dict]:
    """Fetch (or reuse) the selected filings for one company. Returns one record per
    document file: the filing metadata plus ``path`` and ``source_url``."""
    settings.ensure_dirs()
    client = client or SecClient()
    cik, company = cik_for(client, settings, symbol)
    base = settings.raw_dir / symbol
    subs = client.json(SUBMISSIONS_URL.format(cik=cik), base / "submissions.json", max_age_days=1)
    facts = client.json(FACTS_URL.format(cik=cik), base / "companyfacts.json", max_age_days=1)
    filings = select_filings(symbol, subs.get("name") or company, cik, subs, settings.limits, fiscal_periods(facts))
    docs: List[Dict] = []
    for f in filings:
        folder = f.folder(settings.raw_dir)
        names: Iterable[str] = [f.primary_doc]
        if f.form == "8-K":
            idx = client.get(INDEX.format(cik=cik, acc=f.acc_nodash, accession=f.accession),
                             folder / "index.htm").decode("utf-8", "ignore")
            names = earnings_exhibits(idx) or [f.primary_doc]
        for name in names:
            path = folder / name
            client.get(f.url(name), path)
            docs.append({**asdict(f), "file": name, "path": str(path), "source_url": f.url(name)})
        (folder / "meta.json").write_text(json.dumps(asdict(f), indent=1))
        log(f"  {symbol} {f.form:5} filed {f.filing_date}  {f.fiscal_quarter or '?'} FY{f.fiscal_year or '?'}"
            f"  {len(list(names))} file(s)")
    return docs
