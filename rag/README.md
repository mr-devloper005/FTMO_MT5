# rag/ — company-knowledge RAG proof of concept (research only)

Tests whether retrieval over a company's own filings adds value over FMP + Claude.
Two sources feed it: **Financial Modeling Prep** (`rag/fmp/`, structured numbers + filing notes)
and **SEC EDGAR** (`rag/sec.py`, full 10-K/10-Q/8-K text; needs `SEC_USER_AGENT`).
Isolated: no imports from `ftmo_mt5`, `research_agent`, brokers or MCP servers;
the answering model has **no tools** (enforced by `tests/test_rag.py`).

```
SEC EDGAR (10-K, 10-Q, 8-K Item 2.02 EX-99 releases)
   -> rag/state/raw/<SYM>/<form>_<date>_<accession>/   raw files, reused on re-run
   -> extract.py   HTML -> paragraphs, tables as "cell | cell", page markers, hidden iXBRL dropped
   -> chunk.py     Item / Part structure -> sub-headings -> ~280-word chunks; tables keep header + intro
   -> embed.py     BAAI/bge-small-en-v1.5 via fastembed (local CPU, 384-dim, free)
   -> store.py     PostgreSQL + pgvector, schema `rag`: documents, chunks + metadata,
                   embedding vector(384) [HNSW], tsv tsvector [GIN, section weighted 2x]
   -> retrieve.py  filters (symbol, form, fiscal year/quarter; parsed from the question or "latest")
                   -> pgvector cosine + full-text rank -> reciprocal-rank fusion -> top-k with metadata
   -> answer.py    Claude (no tools) answers ONLY from numbered excerpts, cites [n],
                   "Not found in the available documents." otherwise
   -> guard.py     every figure in the answer must exist in the excerpts (unit/rounding aware)
```

Fiscal year/quarter come from the company's own XBRL tags (companyfacts `fy`/`fp`
per accession); earnings releases take the period they report on.

## FMP data (`python -m rag fmp ...`)

```
FMP stable API (key in the `apikey` header, never in URLs/logs/files)
  -> rag/fmp/client.py     rate limit (5 req/s), retries on 429/5xx, typed errors (401 / 402 plan / ...)
  -> rag/fmp/normalize.py  numbers -> typed rows; narrative -> RAG documents
  -> rag/fmp/repo.py       upsert by key + content hash (inserted / updated / unchanged), fetch log
  -> PostgreSQL            rag.financial_statements (+ view rag.financial_facts), rag.earnings,
                           rag.company_data, rag.documents/rag.chunks (source='FMP'), rag.fetch_log
```

Endpoints used (verified live 2026-10-08; FMP's docs site returns 403 to this machine):

| data | endpoint | stored in |
|---|---|---|
| income / balance / cash-flow statements (quarter + annual, 5 each) | `income-statement`, `balance-sheet-statement`, `cash-flow-statement` | `rag.financial_statements` (all fields as JSONB; one row per period) |
| periods FMP has (incremental catalog) | `financial-reports-dates` | `rag.company_data` |
| earnings: actual vs consensus EPS / revenue | `earnings` | `rag.earnings` |
| notes to the financial statements of each 10-Q / 10-K (narrative) | `financial-reports-json` | `rag.documents` + embedded `rag.chunks` |
| company profile (+ description, embedded) | `profile` | `rag.company_data` + `rag.documents` |
| analyst estimates, key metrics, ratios (annual), TTM metrics/ratios, ratings, price targets, executives, peers, SEC filing index | `analyst-estimates`, `key-metrics`, `ratios`, `key-metrics-ttm`, `ratios-ttm`, `grades-consensus`, `price-target-consensus`, `key-executives`, `stock-peers`, `sec-filings-search/symbol` | `rag.company_data` |

**Plan matters.** With the entry-plan key (until 2026-10-08) FMP refused transcripts, press releases, `limit` > 5,
quarterly ratios and *all* data for MU and SNDK (HTTP 402). With the **Ultimate** key (2026-10-09) every one of those
works (verified live, `rag/state/fmp/ultimate_probe_2026-10-09.txt`). Refusals are logged as `unsupported` and not
retried for 24 h; after a plan change run `fmp ingest --force` to re-check them.

**Earnings-call transcripts** (`earning-call-transcript-dates`, `earning-call-transcript`; Ultimate plan): the latest
`--transcripts` calls per company (default 4) become documents (`form='TRANSCRIPT'`, source FMP, fiscal year/quarter
as FMP labels them, call date as `filing_date`). Chunks follow speaker turns (~280 words, speaker names kept in the
text and in `subsection`) and are split into `Earnings call - Prepared remarks` and `Earnings call - Q&A`. A call already
stored is never downloaded again. `news/press-releases` is probed but not ingested: it returns headlines and short
summaries (often third-party), not full earnings-release text.

Only narrative text is embedded. Numbers are never embedded; questions about them are answered
from the tables (see *Answering* below).

Duplicates / incremental updates:
- statements: `financial-reports-dates` says which periods exist; periods already stored are not
  requested, and when one is missing only enough of the newest periods are fetched (`limit=1` for one new quarter);
- filing notes: one document per period, never downloaded twice (`--force` re-fetches);
- snapshots: at most once per `--refresh-hours` (default 12); unchanged rows are not rewritten;
- every call or deliberate skip is in `rag.fetch_log` (`python -m rag fmp status`).

```bash
venv/bin/python -m rag fmp ingest --symbol TSLA     # one ticker (comma list allowed)
venv/bin/python -m rag fmp ingest --all             # MU, SNDK, TSLA, GS, MSFT
venv/bin/python -m rag fmp status --symbol TSLA     # what is stored, what the plan refuses
venv/bin/python -m rag fmp facts --symbol TSLA      # stored statement figures, no LLM
venv/bin/python -m rag ask --symbol TSLA --question "What are Tesla's latest major risks?"
```

## Answering

`structured.route()` decides per question (deterministic rules, tested):
- **structured** when a metric is named (revenue, EPS, free cash flow, debt, ...) or numbers are asked for:
  the matching rows from `rag.financial_statements` / `rag.earnings` (and analyst estimates, labelled
  *not company guidance*, when analysts/consensus are mentioned) become numbered evidence;
- **documents** for what management said, guidance, risks, demand, legal, strategy...: hybrid retrieval.
  Management questions (guidance, outlook, expectations, demand, leadership, strategy) search the **latest earnings
  call first** (or the two latest when comparing), then all documents — "guidance" alone otherwise matches FASB/ASU
  accounting guidance in 10-Q notes;
- **both** when a question needs both; a numeric question with no matching rows falls back to documents.

Claude (no tools) answers only from that evidence, cites `[n]`, and replies
"Not found in the available data." otherwise. Then:
1. the number guard (unchanged) checks every figure against the evidence;
2. `structured.factcheck()` pairs each figure with the metric it describes and compares it to the
   database value; a mismatch marks the answer ungrounded and appends the database value
   ("DATABASE CHECK FAILED ... The database value is authoritative.").

Citations name the source: `FMP income-statement: TSLA Q2 FY2026 (period ended 2026-06-30, filed 2026-07-23) - <url>`
or `TSLA 10-Q via FMP (Q2 FY2026, filed 2026-07-23), Notes to financial statements > Debt - <url>`.

## Database: PostgreSQL + pgvector

Schema: `rag/migrations/001_init.sql` (applied by `db-init`, recorded in `rag.schema_version`).

| table | key columns |
|---|---|
| `rag.documents` | `doc_id` PK, symbol, company, cik, form, accession, file, filing_date `date`, report_date `date`, fiscal_year, fiscal_quarter (Q1-Q4/FY), source_url, path, sha256, n_chunks, ingested_at |
| `rag.chunks` | `chunk_id` PK, `doc_id` FK -> documents (cascade), symbol, company, document_type, form, filing_date, fiscal_year, fiscal_quarter, section, subsection, page, kind, source_url, text, `embedding vector(384)`, `tsv` (generated) |

Indexes: btree on symbol, document_type, form, (fiscal_year, fiscal_quarter), section, filing_date and
a composite filter index; GIN on `tsv`; HNSW (`vector_cosine_ops`) on `embedding`. Queries set
`hnsw.ef_search = 100` and `hnsw.iterative_scan = relaxed_order`, so a filtered search still returns
the full candidate list (pgvector >= 0.8).

### Environment variables

Read from the shell or the FTMO `.env` (gitignored). Only host and port have defaults.

| variable | example | |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | default `localhost` |
| `POSTGRES_PORT` | `5432` | default `5432` |
| `POSTGRES_DB` | `ftmo_rag` | required |
| `POSTGRES_USER` | `ftmo_rag` | required |
| `POSTGRES_PASSWORD` | | optional (local `trust` auth needs none) |
| `POSTGRES_TEST_DB` | `ftmo_rag_test` | tests only; default `<POSTGRES_DB>_test`; must end in `_test` |
| `SEC_USER_AGENT` | `Company Name you@company.com` | required by the SEC for `ingest` |
| `FMP_API_KEY` | | required for `fmp ingest`; never printed or stored outside `.env` |
| `RAG_SKIP_LIVE` | `1` | skip the live FMP test |

### Local setup (macOS, Homebrew PostgreSQL 16)

Homebrew's `pgvector` bottle is built only for PostgreSQL 17/18, so on 16 build it from the official source:

```bash
git clone --depth 1 --branch v0.8.7 https://github.com/pgvector/pgvector.git
cd pgvector
export PG_CONFIG=/opt/homebrew/opt/postgresql@16/bin/pg_config
make && make install
```

Then, as a PostgreSQL superuser (on Homebrew, your macOS user), once:

```bash
psql -h localhost -d postgres -c "CREATE ROLE ftmo_rag LOGIN PASSWORD '<choose one>'"
psql -h localhost -d postgres -c "CREATE DATABASE ftmo_rag OWNER ftmo_rag"
psql -h localhost -d postgres -c "CREATE DATABASE ftmo_rag_test OWNER ftmo_rag"
psql -h localhost -d ftmo_rag -c "CREATE EXTENSION IF NOT EXISTS vector"
psql -h localhost -d ftmo_rag_test -c "CREATE EXTENSION IF NOT EXISTS vector"
```

(`vector` is not a trusted extension, so the app role cannot create it itself.) Put the
`POSTGRES_*` values in the FTMO `.env`, then:

```bash
venv/bin/pip install -e ".[rag]"          # or: venv/bin/pip install fastembed "psycopg[binary]"
venv/bin/python -m rag db-init            # creates schema rag + indexes (idempotent)
venv/bin/python -m rag db-check           # server, pgvector version, row counts
```

## Use

```bash
export SEC_USER_AGENT="Your Company Name you@yourcompany.com"   # SEC requires a declared contact
venv/bin/python -m rag ingest --symbol pilot          # MU, SNDK, TSLA, GS, MSFT
venv/bin/python -m rag stats
venv/bin/python -m rag search --symbol TSLA --query "automotive gross margin" --year 2026 --quarter Q2
venv/bin/python -m rag ask --symbol MU --question "What revenue guidance did Micron give for Q1 FY2027?"
venv/bin/python -m rag eval                           # A (FMP+Claude) vs B (RAG+Claude)
```

Limits per company (most recent): 2 x 10-K, 4 x 10-Q, 6 earnings releases (`config.DEFAULT_LIMITS`).
SEC traffic is capped at 4 requests/second (SEC allows 10).

## Migrating the old SQLite store

The POC first stored everything in `rag/state/rag.db` (SQLite + FTS5). Nothing writes it any more.

```bash
venv/bin/python -m rag migrate --dry-run       # counts only
venv/bin/python -m rag migrate                 # upsert into PostgreSQL + verify; report in rag/state/migrations/
venv/bin/python -m rag verify-migration        # re-run the row-by-row comparison any time
venv/bin/python -m rag retire-sqlite           # renames rag.db -> rag.db.retired-<time> ONLY if verification passes
```

- Safe to re-run: upserts by `doc_id` / `chunk_id`, never duplicates.
- Each row has its own savepoint; failures (e.g. a chunk whose document is missing) are listed in the report.
- Embeddings are copied bit-for-bit and verified with exact float32 equality. Missing or malformed ones are
  stored as NULL and listed under `embedding_issues`; `--reembed` recomputes them.
- Exit code 1 if anything failed or verification did not match. `rag.db` is never deleted.

## Tests

```bash
venv/bin/python -m pytest tests/test_rag.py tests/test_rag_postgres.py tests/test_rag_fmp.py tests/test_rag_fmp_live.py -q
```

`test_rag_fmp.py` uses real TSLA responses captured from FMP (`tests/fixtures/fmp/`); `test_rag_fmp_live.py`
calls the real API (~25 calls) and is skipped without `FMP_API_KEY` or with `RAG_SKIP_LIVE=1`.
`test_rag.py` is offline. `test_rag_postgres.py` uses the **test** database, drops and re-creates the
`rag` schema per test, and is skipped (with the reason) if PostgreSQL/pgvector is unreachable.

## Troubleshooting

| symptom | fix |
|---|---|
| `POSTGRES_DB, POSTGRES_USER not set` | add the `POSTGRES_*` lines to the FTMO `.env` |
| `cannot connect to PostgreSQL ... Connection refused` | `brew services start postgresql@16` |
| `password authentication failed` | check `POSTGRES_PASSWORD`, or `ALTER ROLE ftmo_rag PASSWORD '...'` as superuser |
| `The pgvector extension is missing` | install pgvector for *this* server version, then `CREATE EXTENSION vector` as superuser |
| `could not open extension control file ... vector.control` | pgvector was built for another PostgreSQL; rebuild with that server's `PG_CONFIG` |
| pgvector vanished after `brew upgrade postgresql@16` | the build lives inside the Homebrew keg; re-run `make install` |
| filtered search returns fewer than k | needs pgvector >= 0.8 (iterative scans); check `db-check` |
| Postgres tests are skipped | run `venv/bin/python -m rag db-check`; the skip message gives the reason |
| `FMP rejected the API key (HTTP 401)` | check `FMP_API_KEY` in `.env` |
| `not available on the current FMP plan (HTTP 402)` | plan limit: endpoint, parameter or ticker; see `fmp status` |
| a ticker only has a profile | "value set for 'symbol' is not available": the plan does not cover it |

## Not included (yet)
- Full earnings-release text and Risk Factors / MD&A: FMP does not serve them (press releases are summaries);
  the SEC collector covers them once `SEC_USER_AGENT` is set.
- Any connection to the FTMO trading agent (by design for the POC).
