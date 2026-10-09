-- RAG store v1: documents + chunks with pgvector embeddings and a weighted full-text index.
-- Applied by `python -m rag db-init` (rag/db.py). Safe to re-run: everything is IF NOT EXISTS.
-- The `vector` extension must already exist (CREATE EXTENSION needs a superuser; see rag/README.md).

CREATE SCHEMA IF NOT EXISTS rag;

CREATE TABLE IF NOT EXISTS rag.documents (
    doc_id          text PRIMARY KEY,                       -- "<accession>:<file>"
    symbol          text        NOT NULL,
    company         text,
    cik             integer,
    form            text        NOT NULL,                   -- 10-K | 10-Q | 8-K
    accession       text,
    file            text,
    filing_date     date,
    report_date     date,
    fiscal_year     smallint,
    fiscal_quarter  text CHECK (fiscal_quarter IN ('Q1', 'Q2', 'Q3', 'Q4', 'FY')),
    source_url      text,
    path            text,                                   -- raw file under rag/state/raw
    sha256          char(64),
    n_chunks        integer,
    ingested_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag.chunks (
    chunk_id        text PRIMARY KEY,                       -- "<symbol>:<accession>:<file>:<n>"
    doc_id          text NOT NULL REFERENCES rag.documents(doc_id) ON DELETE CASCADE,
    symbol          text NOT NULL,
    company         text,
    document_type   text,
    form            text NOT NULL,
    filing_date     date,
    fiscal_year     smallint,
    fiscal_quarter  text CHECK (fiscal_quarter IN ('Q1', 'Q2', 'Q3', 'Q4', 'FY')),
    section         text,
    subsection      text,
    page            integer,
    kind            text CHECK (kind IN ('text', 'table')),
    source_url      text,
    text            text NOT NULL,
    embedding       vector(384),                            -- BAAI/bge-small-en-v1.5, unit length
    -- keyword index: section headings weigh twice the body (same as the SQLite FTS5 bm25 weights)
    tsv             tsvector GENERATED ALWAYS AS (
                        setweight(to_tsvector('english'::regconfig,
                                  coalesce(section, '') || ' ' || coalesce(subsection, '')), 'A')
                     || setweight(to_tsvector('english'::regconfig, coalesce(text, '')), 'B')) STORED
);

-- metadata filters
CREATE INDEX IF NOT EXISTS chunks_symbol_idx        ON rag.chunks (symbol);
CREATE INDEX IF NOT EXISTS chunks_document_type_idx ON rag.chunks (document_type);
CREATE INDEX IF NOT EXISTS chunks_form_idx          ON rag.chunks (form);
CREATE INDEX IF NOT EXISTS chunks_period_idx        ON rag.chunks (fiscal_year, fiscal_quarter);
CREATE INDEX IF NOT EXISTS chunks_section_idx       ON rag.chunks (section);
CREATE INDEX IF NOT EXISTS chunks_filing_date_idx   ON rag.chunks (filing_date);
CREATE INDEX IF NOT EXISTS chunks_filter_idx        ON rag.chunks (symbol, form, fiscal_year, fiscal_quarter);
CREATE INDEX IF NOT EXISTS chunks_doc_idx           ON rag.chunks (doc_id);
CREATE INDEX IF NOT EXISTS documents_symbol_idx     ON rag.documents (symbol, form, filing_date);

-- keyword search
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON rag.chunks USING gin (tsv);

-- vector search: HNSW (cosine). At POC scale (tens of thousands of chunks) defaults are plenty;
-- queries raise hnsw.ef_search and use iterative scans so metadata filters still return top-k.
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON rag.chunks USING hnsw (embedding vector_cosine_ops);
