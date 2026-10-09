"""PostgreSQL + pgvector store: documents, chunks + metadata, each chunk's
embedding (``vector(384)``) and a weighted full-text index (``tsv``), all in the
``rag`` schema. Same interface the pipeline used with SQLite; schema in
rag/migrations/001_init.sql.

Vector search uses the HNSW index with iterative scans, so metadata filters
still return the requested number of candidates. Keyword search uses the GIN
full-text index; it stays alongside vectors because exact filing terms
('HBM', 'Item 1A', 'diluted') often beat paraphrase."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from rag import db
from rag.config import PgConfig

DIM = 384
DOC_TYPES = {"10-K": "annual report (10-K)", "10-Q": "quarterly report (10-Q)",
             "8-K": "earnings release (8-K exhibit)"}
META = ("chunk_id", "doc_id", "symbol", "company", "document_type", "form", "filing_date", "fiscal_year",
        "fiscal_quarter", "section", "subsection", "page", "kind", "source_url")
DOC_COLS = ("doc_id", "symbol", "company", "cik", "form", "accession", "file", "filing_date", "report_date",
            "fiscal_year", "fiscal_quarter", "source_url", "path", "sha256", "n_chunks", "ingested_at")
CHUNK_EXTRA = ("source", "period")      # added by migration 002 (SEC rows default to 'SEC EDGAR')
DOC_EXTRA = ("source", "period")
EF_SEARCH = 100                     # >= retrieve.CANDIDATES (60); pgvector's default of 40 would cap results
RANK_WEIGHTS = "{0.1, 0.2, 0.5, 1.0}"   # D, C, B (body), A (section): section counts 2x body, as before


def vec_literal(v: Sequence[float]) -> str:
    """pgvector text form. repr() of each float32-as-double round-trips exactly back to float32."""
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


def parse_vec(s: Optional[str]) -> Optional[np.ndarray]:
    return None if s is None else np.array([float(x) for x in s.strip("[]").split(",")], dtype=np.float32)


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (date, datetime)) else v


class Store:
    def __init__(self, cfg: PgConfig, init: bool = True):
        self.cfg = cfg
        if init:
            db.ensure_schema(cfg)
        self.db = db.connect(cfg)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------ writes
    def doc_sha(self, doc_id: str) -> Optional[str]:
        r = self.db.execute("SELECT sha256 FROM rag.documents WHERE doc_id=%s", (doc_id,)).fetchone()
        return r["sha256"] if r else None

    def replace_document(self, doc: Dict[str, Any], chunks: List[Dict[str, Any]], vectors: np.ndarray) -> None:
        """Atomically replace one document and all its chunks (re-ingest of a changed filing)."""
        cols = [c for c in DOC_COLS + DOC_EXTRA if c in doc and doc[c] is not None]
        with self.db.transaction():
            self.db.execute("DELETE FROM rag.documents WHERE doc_id=%s", (doc["doc_id"],))   # cascades to chunks
            self.db.execute(f"INSERT INTO rag.documents ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})",
                            [doc[c] for c in cols])
            rows = [[c.get(k) for k in META] + [c.get("source") or "SEC EDGAR", c.get("period"), c["text"],
                                                 vec_literal(v)]
                    for c, v in zip(chunks, vectors.astype(np.float32))]
            cols = META + CHUNK_EXTRA + ("text",)
            with self.db.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO rag.chunks ({','.join(cols)}, embedding) "
                    f"VALUES ({','.join(['%s'] * len(cols))}, %s::vector)", rows)

    def delete_document(self, doc_id: str) -> None:
        with self.db.transaction():
            self.db.execute("DELETE FROM rag.documents WHERE doc_id=%s", (doc_id,))

    # ------------------------------------------------------------ filters
    @staticmethod
    def where(symbol=None, forms: Optional[Iterable[str]] = None, fiscal_year=None, fiscal_quarter=None,
              alias: str = "") -> Tuple[str, list]:
        a = f"{alias}." if alias else ""
        sql, args = [], []
        if symbol:
            sql.append(f"{a}symbol = %s")
            args.append(symbol.upper())
        if forms:
            sql.append(f"{a}form = ANY(%s)")
            args.append(list(forms))
        if fiscal_year:
            sql.append(f"{a}fiscal_year = %s")
            args.append(int(fiscal_year))
        if fiscal_quarter:
            q = fiscal_quarter.upper()
            same = ["Q4", "FY"] if q in ("Q4", "FY") else [q]     # a 10-K (FY) and the Q4 release cover Q4
            sql.append(f"{a}fiscal_quarter = ANY(%s)")
            args.append(same)
        return (" WHERE " + " AND ".join(sql)) if sql else "", args

    # ------------------------------------------------------------ retrieval
    def count(self, filters: Dict[str, Any]) -> int:
        where, args = self.where(**filters)
        return self.db.execute(f"SELECT count(*) AS n FROM rag.chunks{where}", args).fetchone()["n"]

    def semantic(self, qvec: np.ndarray, filters: Dict[str, Any], limit: int) -> List[Tuple[str, float]]:
        """Top ``limit`` chunks by cosine similarity among those matching ``filters``."""
        where, args = self.where(**filters)
        where = (where + " AND " if where else " WHERE ") + "embedding IS NOT NULL"
        q = vec_literal(qvec)
        with self.db.transaction():
            self.db.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
            self.db.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            rows = self.db.execute(
                f"SELECT chunk_id, 1 - (embedding <=> %s::vector) AS sim FROM rag.chunks{where} "
                f"ORDER BY embedding <=> %s::vector LIMIT %s", [q, *args, q, limit]).fetchall()
        # relaxed_order may return near-ties slightly out of order; restore exact order
        return sorted(((r["chunk_id"], float(r["sim"])) for r in rows), key=lambda t: (-t[1], t[0]))

    def keyword(self, terms: Sequence[str], filters: Dict[str, Any], limit: int = 50) -> List[str]:
        """Chunks matching ANY term (English stemming), ranked by weighted text rank."""
        if not terms:
            return []
        where, args = self.where(**filters, alias="c")
        tsq = " || ".join(["plainto_tsquery('english', %s)"] * len(terms))
        sql = (f"WITH q AS (SELECT ({tsq}) AS q) "
               f"SELECT c.chunk_id FROM rag.chunks c, q "
               f"WHERE c.tsv @@ q.q{where.replace(' WHERE ', ' AND ')} "
               f"ORDER BY ts_rank('{RANK_WEIGHTS}', c.tsv, q.q, 1) DESC, c.chunk_id LIMIT %s")
        return [r["chunk_id"] for r in self.db.execute(sql, [*terms, *args, limit])]

    def chunks(self, ids: List[str], with_embedding: bool = False) -> List[Dict[str, Any]]:
        if not ids:
            return []
        extra = ", embedding::text AS embedding" if with_embedding else ""
        rows = self.db.execute(f"SELECT {','.join(META + CHUNK_EXTRA)}, text{extra} FROM rag.chunks "
                               f"WHERE chunk_id = ANY(%s)",
                               (list(ids),)).fetchall()
        by = {r["chunk_id"]: {k: _iso(v) for k, v in r.items()} for r in rows}
        return [by[i] for i in ids if i in by]

    def periods(self, symbol: str, forms: Optional[Iterable[str]] = None) -> List[tuple]:
        where, args = self.where(symbol, forms)
        where = (where + " AND " if where else " WHERE ") + "fiscal_year IS NOT NULL"
        return [(r["fiscal_year"], r["fiscal_quarter"]) for r in
                self.db.execute(f"SELECT DISTINCT fiscal_year, fiscal_quarter FROM rag.chunks{where}", args)]

    # ------------------------------------------------------------ reporting
    def stats(self) -> List[Dict[str, Any]]:
        q = """SELECT d.symbol, d.form, count(*) AS docs, sum(d.n_chunks) AS chunks,
                      min(d.filing_date) AS first_filed, max(d.filing_date) AS last_filed
               FROM rag.documents d GROUP BY d.symbol, d.form ORDER BY d.symbol, d.form"""
        return [{k: _iso(v) for k, v in r.items()} for r in self.db.execute(q)]

    def documents(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        where, args = ("WHERE symbol=%s", [symbol.upper()]) if symbol else ("", [])
        return [{k: _iso(v) for k, v in r.items()} for r in self.db.execute(
            f"SELECT doc_id, symbol, form, file, filing_date, fiscal_year, fiscal_quarter, n_chunks, source_url "
            f"FROM rag.documents {where} ORDER BY symbol, filing_date DESC", args)]
