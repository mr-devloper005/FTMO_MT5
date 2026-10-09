"""RAG store on PostgreSQL + pgvector.

Runs against the TEST database only (POSTGRES_TEST_DB, default '<POSTGRES_DB>_test';
any name not ending in _test is refused). The rag schema is dropped and
re-created for every test. Skipped, with the reason, when PostgreSQL or
pgvector is not available."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rag import config, db, migrate, retrieve  # noqa: E402
from rag.store import Store, parse_vec, vec_literal  # noqa: E402
from rag_fixtures import cfg, store  # noqa: E402,F401  (pytest fixtures)


def unit(seed: int, dim: int = 384) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=dim).astype(np.float32)
    return v / np.linalg.norm(v)


BASE = {"company": "Micron Technology", "document_type": "quarterly report (10-Q)", "form": "10-Q",
        "filing_date": "2026-06-26", "section": "Part I, Item 2. Management's Discussion and Analysis",
        "subsection": "Results of Operations", "page": 31, "kind": "text",
        "source_url": "https://www.sec.gov/Archives/edgar/data/723125/000072312526000012/mu-20260528.htm"}


def add(st: Store, doc_id: str, chunks, symbol="MU", fy=2026, fq="Q3", form="10-Q"):
    rows = [{**BASE, "form": form, "symbol": symbol, "doc_id": doc_id, "fiscal_year": fy, "fiscal_quarter": fq,
             "chunk_id": f"{doc_id}:{i:04d}", "text": t} for i, (t, _) in enumerate(chunks)]
    st.replace_document({"doc_id": doc_id, "symbol": symbol, "company": BASE["company"], "form": form,
                         "filing_date": "2026-06-26", "fiscal_year": fy, "fiscal_quarter": fq,
                         "source_url": BASE["source_url"], "sha256": "a" * 64, "n_chunks": len(rows)},
                        rows, np.vstack([v for _, v in chunks]))
    return rows


# ------------------------------------------------------------------ connection + schema
def test_connection_and_pgvector(cfg):
    info = db.check(cfg)
    assert info["pgvector"] and info["server"].startswith(("16", "17", "18"))
    assert cfg.dbname.endswith("_test")


def test_schema_creation_is_idempotent(cfg):
    assert db.init_db(cfg)["applied"] == []                               # fixture already applied 001
    with db.connect(cfg) as conn:
        cols = {r["column_name"]: r["udt_name"] for r in conn.execute(
            "SELECT column_name, udt_name FROM information_schema.columns "
            "WHERE table_schema='rag' AND table_name='chunks'")}
        idx = {r["indexname"] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname='rag'")}
    assert cols["embedding"] == "vector" and cols["tsv"] == "tsvector" and cols["filing_date"] == "date"
    for want in ("chunks_symbol_idx", "chunks_document_type_idx", "chunks_period_idx", "chunks_section_idx",
                 "chunks_filing_date_idx", "chunks_tsv_idx", "chunks_embedding_hnsw"):
        assert want in idx


def test_test_database_guard(monkeypatch):
    monkeypatch.setenv("POSTGRES_TEST_DB", "ftmo_rag")
    with pytest.raises(config.MissingCredential):
        config.PgConfig.test_from_env()


# ------------------------------------------------------------------ insert / retrieve
def test_insert_retrieve_roundtrip_and_reingest(store):
    v = unit(1)
    add(store, "acc1:doc.htm", [("Revenue increased on HBM demand.", v)])
    got = store.chunks(["acc1:doc.htm:0000"], with_embedding=True)[0]
    assert got["filing_date"] == "2026-06-26" and got["page"] == 31 and got["fiscal_quarter"] == "Q3"
    assert got["section"] == BASE["section"] and got["source_url"] == BASE["source_url"]
    assert np.array_equal(parse_vec(got["embedding"]), v)                   # float32 round-trips exactly
    assert store.doc_sha("acc1:doc.htm") == "a" * 64
    add(store, "acc1:doc.htm", [("Replaced text.", unit(2))])               # re-ingest: old chunks gone
    assert store.count({"symbol": "MU"}) == 1
    store.delete_document("acc1:doc.htm")
    assert store.count({}) == 0


def test_vec_literal_round_trip():
    v = unit(7)
    assert np.array_equal(parse_vec(vec_literal(v)), v)


# ------------------------------------------------------------------ search
def test_vector_search_matches_exact_cosine(store):
    vecs = [unit(100 + i) for i in range(120)]
    add(store, "acc2:a.htm", [(f"chunk {i}", v) for i, v in enumerate(vecs[:60])], fq="Q3")
    add(store, "acc3:b.htm", [(f"chunk {i}", v) for i, v in enumerate(vecs[60:])], fq="Q2")
    q = unit(999)
    got = store.semantic(q, {"symbol": "MU", "fiscal_quarter": "Q3"}, 10)
    exact = sorted(((f"acc2:a.htm:{i:04d}", float(vecs[i] @ q)) for i in range(60)), key=lambda t: -t[1])[:10]
    assert [g[0] for g in got] == [e[0] for e in exact]
    assert all(abs(g[1] - e[1]) < 1e-5 for g, e in zip(got, exact))
    assert len(store.semantic(q, {"symbol": "MU"}, 60)) == 60               # filters + iterative scan fill k


def test_keyword_search_stems_and_weights_sections(store):
    add(store, "acc4:k.htm", [("Gross margins expanded on pricing.", unit(3)),
                              ("Unrelated text about leases.", unit(4))])
    assert store.keyword(["margin"], {"symbol": "MU"}) == ["acc4:k.htm:0000"]   # 'margin' matches 'margins'
    hits = store.keyword(["results"], {"symbol": "MU"})                         # in the subsection heading
    assert set(hits) == {"acc4:k.htm:0000", "acc4:k.htm:0001"}
    assert store.keyword(["zzzunseen"], {"symbol": "MU"}) == []


def test_metadata_filters(store):
    add(store, "q3:x.htm", [("HBM revenue grew strongly", unit(10))], fy=2026, fq="Q3")
    add(store, "fy:x.htm", [("Risk factors: export controls", unit(11))], fy=2025, fq="FY", form="10-K")
    add(store, "rel:x.htm", [("Fourth quarter release", unit(12))], fy=2025, fq="Q4", form="8-K")
    add(store, "ts:x.htm", [("Tesla deliveries", unit(13))], symbol="TSLA")
    assert store.count({"symbol": "MU"}) == 3 and store.count({"symbol": "TSLA"}) == 1
    assert store.count({"symbol": "MU", "forms": ["10-K"]}) == 1
    assert store.count({"symbol": "MU", "fiscal_year": 2025, "fiscal_quarter": "Q4"}) == 2   # Q4 includes FY
    assert store.count({"symbol": "MU", "fiscal_year": 2026, "fiscal_quarter": "Q3"}) == 1
    assert sorted(store.periods("MU")) == [(2025, "FY"), (2025, "Q4"), (2026, "Q3")]


def test_hybrid_search_filters_and_citations(cfg, store, monkeypatch):
    a, b, c = unit(20), unit(21), unit(22)
    add(store, "d1:x.htm", [("HBM revenue grew strongly this quarter", a)], fy=2026, fq="Q3")
    add(store, "d2:x.htm", [("HBM revenue grew modestly this quarter", b)], fy=2026, fq="Q2")
    add(store, "d3:x.htm", [("Risk factors: export controls on China sales", c)], fy=2025, fq="FY", form="10-K")
    monkeypatch.setattr(retrieve.embed, "query", lambda q: a)
    r = retrieve.search("HBM revenue Q3 FY2026", symbol="MU", store=store)
    assert r["filters"]["period_filter"] == "parsed from question"
    assert [h["chunk_id"] for h in r["hits"]] == ["d1:x.htm:0000"]
    h = r["hits"][0]
    assert h["keyword_rank"] == 1 and h["similarity"] == pytest.approx(1.0, abs=1e-4)
    r = retrieve.search("HBM revenue in the latest quarter", symbol="MU", store=store)
    assert {x["chunk_id"] for x in r["hits"]} == {"d1:x.htm:0000"}            # latest = Q3 FY2026
    r = retrieve.search("export controls", symbol="MU", store=store, fiscal_year=2025, fiscal_quarter="Q4")
    assert [x["chunk_id"] for x in r["hits"]] == ["d3:x.htm:0000"]
    assert retrieve.search("anything", symbol="TSLA", store=store)["hits"] == []
    cite = retrieve.cite(h)                                                  # document, section, page, date, URL
    for part in ("MU 10-Q", "Q3 FY2026", "filed 2026-06-26", "Item 2", "Results of Operations", "p.31",
                 BASE["source_url"]):
        assert part in cite


def test_number_verification_on_postgres_hits(cfg, store, monkeypatch):
    from rag import answer
    v = unit(30)
    add(store, "n1:x.htm", [("Revenue | $41,456 | $9,301 (in millions). Gross margin was 74.9%.", v)])
    monkeypatch.setattr(retrieve.embed, "query", lambda q: v)
    s = config.Settings(pg=cfg)
    monkeypatch.setattr(answer, "claude", lambda system, prompt, model=None: {
        "text": "Revenue was $41.5 billion and gross margin 74.9% [1].", "cost_usd": 0.0, "seconds": 0.1})
    r = answer.ask("What was MU revenue in Q3 FY2026?", s, symbol="MU")
    assert r["grounded"] and r["guard"]["unverified"] == [] and r["citations"][0]["id"] == "n1:x.htm:0000"
    assert "p.31" in r["citations"][0]["source"]
    monkeypatch.setattr(answer, "claude", lambda system, prompt, model=None: {
        "text": "Revenue was $45.0 billion [1].", "cost_usd": 0.0, "seconds": 0.1})
    r = answer.ask("What was MU revenue in Q3 FY2026?", s, symbol="MU")
    assert not r["grounded"] and r["guard"]["unverified"] == ["$45.0 billion"]


# ------------------------------------------------------------------ migration
def legacy_db(path: Path, orphan: bool = False, bad_vector: bool = False) -> Path:
    con = sqlite3.connect(path)
    con.executescript(migrate.LEGACY_SQLITE_SCHEMA)
    for d, fy, fq in (("acc1:a.htm", 2026, "Q3"), ("acc2:b.htm", 2025, "FY")):
        con.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (d, "MU", "Micron", 723125, "10-Q", d.split(":")[0], d.split(":")[1], "2026-06-26", "",
                     fy, fq, BASE["source_url"], "/raw/x", "b" * 64, 2, "2026-10-08T00:00:00+00:00"))
    rows = [("acc1:a.htm", 0, unit(40)), ("acc1:a.htm", 1, unit(41)), ("acc2:b.htm", 0, unit(42))]
    if bad_vector:
        rows.append(("acc2:b.htm", 1, None))
    if orphan:
        rows.append(("ghost:z.htm", 0, unit(43)))
    for doc, n, v in rows:
        cid = f"MU:{doc}:{n:04d}"
        blob = v.tobytes() if v is not None else b"\x00" * 12
        con.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, doc, "MU", "Micron", "quarterly report (10-Q)", "10-Q", "2026-06-26", 2026, "Q3",
                     BASE["section"], "", 31, "text", BASE["source_url"], f"Revenue text {n} for {doc}", blob))
        con.execute("INSERT INTO chunks_fts (chunk_id, section, text) VALUES (?,?,?)", (cid, BASE["section"], "t"))
    con.commit()
    con.close()
    return path


def test_migration_copies_everything_and_reruns_without_duplicates(cfg, tmp_path):
    src = legacy_db(tmp_path / "rag.db")
    s = config.Settings(state_dir=tmp_path, pg=cfg)
    r = migrate.migrate(s, log=lambda *_: None)
    assert r["ok"] and r["failures"] == [] and r["embedding_issues"] == []
    assert r["sqlite_counts"] == {"documents": 2, "chunks": 3, "valid_embeddings": 3, "fts_rows": 3}
    v = r["verification"]
    assert v["postgres_matched"] == {"documents": 2, "chunks": 3, "embeddings_identical": 3, "keyword_indexed": 3}
    assert r["written"]["chunks_inserted"] == 3 and Path(r["report_file"]).exists()
    again = migrate.migrate(s, log=lambda *_: None)                            # idempotent
    assert again["ok"] and again["written"]["chunks_inserted"] == 0 and again["written"]["chunks_updated"] == 3
    assert db.check(cfg)["chunks"] == 3 and db.check(cfg)["documents"] == 2
    assert src.exists()                                                        # never deleted


def test_migration_reports_failed_records_and_bad_embeddings(cfg, tmp_path):
    legacy_db(tmp_path / "rag.db", orphan=True, bad_vector=True)
    s = config.Settings(state_dir=tmp_path, pg=cfg)
    r = migrate.migrate(s, log=lambda *_: None)
    assert not r["ok"]
    assert [f["id"] for f in r["failures"]] == ["MU:ghost:z.htm:0000"]                # no parent document
    assert "ForeignKeyViolation" in r["failures"][0]["error"]
    assert r["embedding_issues"] == [{"chunk_id": "MU:acc2:b.htm:0001", "issue": "invalid (12 bytes)"}]
    assert db.check(cfg)["chunks"] == 4 and db.check(cfg)["embeddings"] == 3        # good rows still landed
    assert migrate.retire_sqlite(s)["ok"] is False and (tmp_path / "rag.db").exists()


def test_migration_reembeds_invalid_vectors(cfg, tmp_path):
    legacy_db(tmp_path / "rag.db", bad_vector=True)
    s = config.Settings(state_dir=tmp_path, pg=cfg)
    r = migrate.migrate(s, reembed=True, log=lambda *_: None)
    assert r["ok"] and r["written"]["reembedded"] == 1 and db.check(cfg)["embeddings"] == 4


def test_retire_renames_only_after_verification(cfg, tmp_path):
    legacy_db(tmp_path / "rag.db")
    s = config.Settings(state_dir=tmp_path, pg=cfg)
    assert migrate.retire_sqlite(s)["ok"] is False                                  # not migrated yet
    assert (tmp_path / "rag.db").exists()
    migrate.migrate(s, log=lambda *_: None)
    out = migrate.retire_sqlite(s)
    assert out["ok"] and not (tmp_path / "rag.db").exists() and Path(out["retired_to"]).exists()
