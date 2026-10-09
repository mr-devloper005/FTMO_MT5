"""Shared fixtures for the RAG PostgreSQL tests (TEST database only; skipped if unavailable)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag import config, db  # noqa: E402
from rag.store import Store  # noqa: E402


@pytest.fixture
def cfg():
    try:
        c = config.PgConfig.test_from_env()
        info = db.check(c)
    except (config.MissingCredential, db.DatabaseError) as e:
        pytest.skip(f"PostgreSQL test database unavailable: {e}")
    if not info["pgvector"]:
        pytest.skip("pgvector extension missing in the test database (see rag/README.md)")
    with db.connect(c) as conn:
        conn.execute("DROP SCHEMA IF EXISTS rag CASCADE")
    db._READY.clear()                                   # schema was just dropped
    db.init_db(c)
    return c


@pytest.fixture
def store(cfg):
    st = Store(cfg)
    yield st
    st.close()
