"""PostgreSQL connection + schema setup. Migrations are the numbered .sql files in
rag/migrations, applied once each in order and recorded in rag.schema_version."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import psycopg
from psycopg.rows import dict_row

from rag.config import PgConfig

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
LOCK_KEY = 74_201_008                                   # pg_advisory_xact_lock: one initialiser at a time
SUPERUSER_HINT = ("The pgvector extension is missing in database {db}. Creating it needs a superuser once:\n"
                  "  psql -h {host} -p {port} -d {db} -c 'CREATE EXTENSION IF NOT EXISTS vector'\n"
                  "(and pgvector must be installed for this PostgreSQL version; see rag/README.md)")


class DatabaseError(RuntimeError):
    pass


def connect(cfg: PgConfig) -> psycopg.Connection:
    """Autocommit connection: reads need no transaction; every write runs in an explicit ``conn.transaction()``."""
    try:
        return psycopg.connect(**cfg.kwargs(), row_factory=dict_row, autocommit=True)
    except psycopg.OperationalError as e:
        raise DatabaseError(f"cannot connect to PostgreSQL {cfg!r}: {str(e).strip().splitlines()[0]}") from e


def _ensure_vector(conn: psycopg.Connection, cfg: PgConfig) -> None:
    if conn.execute("SELECT 1 FROM pg_extension WHERE extname='vector'").fetchone():
        return
    try:
        with conn.transaction():
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.FeatureNotSupported,
            psycopg.errors.UndefinedFile) as e:
        raise DatabaseError(SUPERUSER_HINT.format(db=cfg.dbname, host=cfg.host, port=cfg.port)) from e


def migrations() -> List[Path]:
    return sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))


def init_db(cfg: PgConfig) -> Dict[str, Any]:
    """Idempotent: create the vector extension (if allowed), the rag schema and apply pending migrations."""
    with connect(cfg) as conn:
        _ensure_vector(conn, cfg)
        applied: List[str] = []
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
            conn.execute("CREATE SCHEMA IF NOT EXISTS rag")
            conn.execute("CREATE TABLE IF NOT EXISTS rag.schema_version ("
                         "version integer PRIMARY KEY, name text NOT NULL, applied_at timestamptz DEFAULT now())")
            done = {r["version"] for r in conn.execute("SELECT version FROM rag.schema_version")}
            for path in migrations():
                version = int(path.name[:3])
                if version in done:
                    continue
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO rag.schema_version (version, name) VALUES (%s, %s)", (version, path.name))
                applied.append(path.name)
        return {"database": repr(cfg), "applied": applied, **check(cfg, conn)}


_READY: set = set()


def ensure_schema(cfg: PgConfig) -> None:
    """init_db once per process per database (Store() calls this; searches stay one round-trip lighter)."""
    key = (cfg.host, cfg.port, cfg.dbname)
    if key not in _READY:
        init_db(cfg)
        _READY.add(key)


def check(cfg: PgConfig, conn: psycopg.Connection = None) -> Dict[str, Any]:
    """Connection + extension + schema status, for `python -m rag db-check` and tests."""
    own = conn is None
    conn = conn or connect(cfg)
    try:
        ver = conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
        has_schema = conn.execute("SELECT to_regclass('rag.chunks') IS NOT NULL AS ok").fetchone()["ok"]
        out: Dict[str, Any] = {
            "server": conn.execute("SHOW server_version").fetchone()["server_version"],
            "pgvector": ver["extversion"] if ver else None,
            "schema_versions": [], "documents": None, "chunks": None, "embeddings": None}
        if has_schema:
            out["schema_versions"] = [r["version"] for r in
                                      conn.execute("SELECT version FROM rag.schema_version ORDER BY version")]
            out.update(conn.execute(
                "SELECT (SELECT count(*) FROM rag.documents) AS documents, (SELECT count(*) FROM rag.chunks) AS chunks,"
                " (SELECT count(*) FROM rag.chunks WHERE embedding IS NOT NULL) AS embeddings").fetchone())
        return out
    finally:
        if own:
            conn.close()
