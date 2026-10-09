"""Download -> raw files -> text -> section-aware chunks -> embeddings -> PostgreSQL (pgvector)."""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from rag import embed, sec
from rag.chunk import chunk_lines
from rag.config import Settings, period_label
from rag.extract import html_to_lines, text_to_lines
from rag.store import DOC_TYPES, Store


def chunk_document(doc: Dict, raw: bytes) -> List[Dict]:
    text = raw.decode("utf-8", errors="ignore")
    lines = text_to_lines(text) if doc["file"].lower().endswith(".txt") else html_to_lines(text)
    out = []
    for n, c in enumerate(chunk_lines(lines, doc["form"])):
        out.append({
            "chunk_id": f"{doc['symbol']}:{doc['accession']}:{doc['file']}:{n:04d}",
            "doc_id": doc["doc_id"], "symbol": doc["symbol"], "company": doc["company"],
            "document_type": DOC_TYPES.get(doc["form"], doc["form"]), "form": doc["form"],
            "filing_date": doc["filing_date"], "fiscal_year": doc.get("fiscal_year"),
            "fiscal_quarter": doc.get("fiscal_quarter"), "section": c.section, "subsection": c.subsection,
            "page": c.page, "kind": c.kind, "source_url": doc["source_url"], "text": c.text})
    return out


def embedding_text(c: Dict) -> str:
    """What gets embedded: the chunk plus a one-line context header, so a table row
    like 'Revenue | 54,229' still knows which company, filing and period it is from."""
    head = (f"{c['company']} ({c['symbol']}) {c['document_type']}, "
            f"{period_label(c['fiscal_year'], c['fiscal_quarter'])}. {c['section']}"
            + (f" - {c['subsection']}" if c.get("subsection") else ""))
    return head + "\n" + c["text"]


def ingest(symbol: str, settings: Optional[Settings] = None, client=None, log=print,
           force: bool = False) -> Dict:
    settings = settings or Settings()
    settings.ensure_dirs()
    t0 = time.time()
    store = Store(settings.pg_config())
    client = client or sec.SecClient()
    docs = sec.download(symbol.upper(), settings, client, log)
    added = skipped = n_chunks = 0
    for d in docs:
        raw = Path(d["path"]).read_bytes()
        d["doc_id"] = f"{d['accession']}:{d['file']}"
        sha = hashlib.sha256(raw).hexdigest()
        if not force and store.doc_sha(d["doc_id"]) == sha:
            skipped += 1
            continue
        chunks = chunk_document(d, raw)
        vecs = embed.passages([embedding_text(c) for c in chunks])
        store.replace_document({
            "doc_id": d["doc_id"], "symbol": d["symbol"], "company": d["company"], "cik": d["cik"],
            "form": d["form"], "accession": d["accession"], "file": d["file"], "filing_date": d["filing_date"],
            "report_date": d["report_date"], "fiscal_year": d["fiscal_year"], "fiscal_quarter": d["fiscal_quarter"],
            "source_url": d["source_url"], "path": d["path"], "sha256": sha, "n_chunks": len(chunks),
            "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, chunks, vecs)
        added += 1
        n_chunks += len(chunks)
        log(f"    indexed {d['form']} {d['file']}: {len(chunks)} chunks")
    store.close()
    return {"symbol": symbol.upper(), "documents": len(docs), "indexed": added, "unchanged": skipped,
            "new_chunks": n_chunks, "sec_requests": client.requests, "seconds": round(time.time() - t0, 1)}
