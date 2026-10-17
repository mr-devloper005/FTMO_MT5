"""One-way migration of the retired SQLite store (rag/state/rag.db) into PostgreSQL.

- Reads SQLite read-only; never modifies or deletes it.
- Upserts by primary key (doc_id / chunk_id), so re-running never duplicates rows.
- Each row runs in its own savepoint: one bad row is reported, the rest still migrate.
- Embeddings are copied bit-for-bit (float32 -> vector(384)); a blob of the wrong
  size or with NaN/inf is reported and stored as NULL (``reembed=True`` recomputes it).
- Verification re-reads PostgreSQL and compares counts, every metadata field, the
  text and each embedding (exact float32 equality) with SQLite.
- ``retire_sqlite`` renames rag.db only after a fresh verification passes.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import psycopg

from rag import db
from rag.config import PgConfig, Settings
from rag.store import DIM, DOC_COLS, META, parse_vec, vec_literal

# The SQLite schema this migrates from (rag/store.py before 2026-10-08). Kept for reference and tests.
LEGACY_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY, symbol TEXT, company TEXT, cik INTEGER, form TEXT, accession TEXT,
  file TEXT, filing_date TEXT, report_date TEXT, fiscal_year INTEGER, fiscal_quarter TEXT,
  source_url TEXT, path TEXT, sha256 TEXT, n_chunks INTEGER, ingested_at TEXT);
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY, doc_id TEXT, symbol TEXT, company TEXT, document_type TEXT, form TEXT,
  filing_date TEXT, fiscal_year INTEGER, fiscal_quarter TEXT, section TEXT, subsection TEXT,
  page INTEGER, kind TEXT, source_url TEXT, text TEXT, embedding BLOB);
CREATE INDEX IF NOT EXISTS chunks_filter ON chunks(symbol, form, fiscal_year, fiscal_quarter);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id UNINDEXED, section, text, tokenize='porter unicode61');
"""
DATE_COLS = {"filing_date", "report_date"}


def _sqlite(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _clean(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in row.items():
        if isinstance(v, str) and v.strip() == "" and k != "text":
            v = None                                              # '' is not a date / not a period
        out[k] = v
    return out


def blob_to_vec(blob: Optional[bytes]) -> Optional[np.ndarray]:
    """float32 vector from a SQLite BLOB, or None if it is missing / wrong size / not finite."""
    if blob is None or len(blob) != DIM * 4:
        return None
    v = np.frombuffer(blob, dtype=np.float32)
    return v if np.isfinite(v).all() else None


def _sqlite_rows(con: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return []
    return [dict(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def _upsert(conn, table: str, key: str, row: Dict[str, Any], vector_col: Optional[str] = None) -> bool:
    """INSERT ... ON CONFLICT (key) DO UPDATE; returns True if the row was new."""
    cols = list(row)
    vals = ["%s::vector" if c == vector_col else "%s" for c in cols]
    upd = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != key)
    r = conn.execute(f"INSERT INTO rag.{table} ({','.join(cols)}) VALUES ({','.join(vals)}) "
                     f"ON CONFLICT ({key}) DO UPDATE SET {upd} RETURNING (xmax = 0) AS inserted",
                     [row[c] for c in cols]).fetchone()
    return bool(r["inserted"])


def migrate(settings: Optional[Settings] = None, sqlite_path: Optional[Path] = None,
            cfg: Optional[PgConfig] = None, reembed: bool = False, dry_run: bool = False,
            log=print) -> Dict[str, Any]:
    settings = settings or Settings()
    settings.ensure_dirs()
    path = Path(sqlite_path or settings.sqlite_path)
    cfg = cfg or settings.pg_config()
    report: Dict[str, Any] = {"started_at": datetime.now().isoformat(timespec="seconds"), "sqlite": str(path),
                              "postgres": repr(cfg), "dry_run": dry_run, "failures": [], "embedding_issues": []}
    if not path.exists():
        report.update(ok=False, error=f"SQLite database not found: {path}")
        return report
    con = _sqlite(path)
    docs, chunks = _sqlite_rows(con, "documents"), _sqlite_rows(con, "chunks")
    report["sqlite_counts"] = sqlite_counts(con)
    con.close()
    log(f"SQLite {path.name}: {len(docs)} documents, {len(chunks)} chunks")
    if dry_run:
        report.update(ok=True, note="dry run: nothing written")
        return report

    db.init_db(cfg)
    stats = {"documents_inserted": 0, "documents_updated": 0, "chunks_inserted": 0, "chunks_updated": 0,
             "reembedded": 0}
    with db.connect(cfg) as conn:
        for d in docs:
            row = _clean({c: d.get(c) for c in DOC_COLS if c in d})
            try:
                with conn.transaction():
                    new = _upsert(conn, "documents", "doc_id", row)
                stats["documents_inserted" if new else "documents_updated"] += 1
            except psycopg.Error as e:
                report["failures"].append({"table": "documents", "id": d.get("doc_id"), "error": _err(e)})

        to_embed: List[Dict[str, Any]] = []
        for c in chunks:
            row = _clean({k: c.get(k) for k in META})
            row["text"] = c.get("text") or ""
            vec = blob_to_vec(c.get("embedding"))
            if vec is None:
                n = len(c["embedding"]) if c.get("embedding") is not None else 0
                report["embedding_issues"].append({"chunk_id": c.get("chunk_id"),
                                                   "issue": "missing" if not n else f"invalid ({n} bytes)"})
                to_embed.append(row)
            row["embedding"] = vec_literal(vec) if vec is not None else None
            try:
                with conn.transaction():
                    new = _upsert(conn, "chunks", "chunk_id", row, vector_col="embedding")
                stats["chunks_inserted" if new else "chunks_updated"] += 1
            except psycopg.Error as e:
                report["failures"].append({"table": "chunks", "id": c.get("chunk_id"), "error": _err(e)})

        if reembed and to_embed:
            from rag import embed
            from rag.ingest import embedding_text
            ok = [r for r in to_embed if r["chunk_id"] not in {f["id"] for f in report["failures"]}]
            vecs = embed.passages([embedding_text(r) for r in ok])
            with conn.transaction():
                for r, v in zip(ok, vecs):
                    conn.execute("UPDATE rag.chunks SET embedding=%s::vector WHERE chunk_id=%s",
                                 (vec_literal(v), r["chunk_id"]))
            stats["reembedded"] = len(ok)
    report["written"] = stats
    report["verification"] = verify(path, cfg)
    report["ok"] = not report["failures"] and report["verification"]["ok"]
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    out = settings.migrations_dir / f"migration_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    report["report_file"] = str(out)
    return report


def _err(e: Exception) -> str:
    return f"{type(e).__name__}: {str(e).strip().splitlines()[0][:300]}"


def sqlite_counts(con: sqlite3.Connection) -> Dict[str, int]:
    def n(sql: str) -> int:
        try:
            return con.execute(sql).fetchone()[0]
        except sqlite3.Error:
            return 0
    valid = sum(blob_to_vec(r[0]) is not None for r in con.execute("SELECT embedding FROM chunks")) \
        if n("SELECT count(*) FROM sqlite_master WHERE name='chunks'") else 0
    return {"documents": n("SELECT count(*) FROM documents"), "chunks": n("SELECT count(*) FROM chunks"),
            "valid_embeddings": valid, "fts_rows": n("SELECT count(*) FROM chunks_fts")}


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, str) and a.strip() == "":
        a = None
    if a is None or b is None:
        return a is None and b is None
    if isinstance(b, float) or isinstance(a, float):
        return math.isclose(float(a), float(b))
    return str(a).strip() == str(b).strip()


def verify(sqlite_path: Path, cfg: PgConfig) -> Dict[str, Any]:
    """Compare every SQLite row with PostgreSQL: presence, metadata, text, embedding."""
    con = _sqlite(Path(sqlite_path))
    docs, chunks = _sqlite_rows(con, "documents"), _sqlite_rows(con, "chunks")
    counts = sqlite_counts(con)
    con.close()
    mismatches: List[Dict[str, Any]] = []
    with db.connect(cfg) as conn:
        pg_docs = {r["doc_id"]: r for r in conn.execute(
            f"SELECT {','.join(DOC_COLS)} FROM rag.documents WHERE doc_id = ANY(%s)", ([d["doc_id"] for d in docs],))}
        pg_chunks = {r["chunk_id"]: r for r in conn.execute(
            f"SELECT {','.join(META)}, text, embedding::text AS embedding, tsv IS NOT NULL AS has_tsv "
            f"FROM rag.chunks WHERE chunk_id = ANY(%s)", ([c["chunk_id"] for c in chunks],))}
        totals = db.check(cfg, conn)
    for d in docs:
        p = pg_docs.get(d["doc_id"])
        if p is None:
            mismatches.append({"id": d["doc_id"], "problem": "document missing in PostgreSQL"})
            continue
        bad = [c for c in DOC_COLS if c in d and c != "ingested_at" and not _same(d[c], p[c])]
        if bad:
            mismatches.append({"id": d["doc_id"], "problem": f"document fields differ: {bad}"})
    emb_equal = 0
    for c in chunks:
        p = pg_chunks.get(c["chunk_id"])
        if p is None:
            mismatches.append({"id": c["chunk_id"], "problem": "chunk missing in PostgreSQL"})
            continue
        bad = [k for k in META if not _same(c.get(k), p[k])] + ([] if (c.get("text") or "") == p["text"] else ["text"])
        if bad:
            mismatches.append({"id": c["chunk_id"], "problem": f"chunk fields differ: {bad}"})
        src, dst = blob_to_vec(c.get("embedding")), parse_vec(p["embedding"])
        if src is not None:
            if dst is not None and np.array_equal(src, dst):
                emb_equal += 1
            else:
                mismatches.append({"id": c["chunk_id"], "problem": "embedding differs"})
    result = {
        "sqlite": counts,
        "postgres_matched": {"documents": len(pg_docs), "chunks": len(pg_chunks),
                             "embeddings_identical": emb_equal,
                             "keyword_indexed": sum(bool(r["has_tsv"]) for r in pg_chunks.values())},
        "postgres_totals": {k: totals[k] for k in ("documents", "chunks", "embeddings")},
        "mismatches": mismatches[:200], "mismatch_count": len(mismatches)}
    result["ok"] = (len(pg_docs) == counts["documents"] and len(pg_chunks) == counts["chunks"]
                    and emb_equal == counts["valid_embeddings"] and not mismatches)
    return result


def retire_sqlite(settings: Optional[Settings] = None, cfg: Optional[PgConfig] = None) -> Dict[str, Any]:
    """Rename rag.db -> rag.db.retired-<timestamp> after a fresh verification passes. Never deletes."""
    settings = settings or Settings()
    path = settings.sqlite_path
    if not path.exists():
        return {"ok": False, "error": f"{path} not found (already retired?)"}
    v = verify(path, cfg or settings.pg_config())
    if not v["ok"]:
        return {"ok": False, "error": "verification failed; SQLite left in place", "verification": v}
    target = path.with_name(f"{path.name}.retired-{datetime.now():%Y%m%d_%H%M%S}")
    path.rename(target)
    return {"ok": True, "retired_to": str(target), "verification": v}
