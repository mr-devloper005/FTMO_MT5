-- RAG store v2: Financial Modeling Prep (FMP) data.
-- Numbers go into typed tables (never embedded); narrative text goes into rag.documents / rag.chunks.

-- provenance on the existing document tables (SEC rows keep the default)
ALTER TABLE rag.documents ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'SEC EDGAR';
ALTER TABLE rag.documents ADD COLUMN IF NOT EXISTS period text;            -- e.g. 'Q2 FY2026'
ALTER TABLE rag.chunks    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'SEC EDGAR';
ALTER TABLE rag.chunks    ADD COLUMN IF NOT EXISTS period text;
CREATE INDEX IF NOT EXISTS chunks_source_idx ON rag.chunks (source);

-- income / balance / cash-flow statements, one row per company x statement x period
CREATE TABLE IF NOT EXISTS rag.financial_statements (
    symbol            text        NOT NULL,
    statement         text        NOT NULL CHECK (statement IN ('income', 'balance', 'cashflow')),
    period_type       text        NOT NULL CHECK (period_type IN ('quarter', 'annual')),
    period_end        date        NOT NULL,                    -- FMP "date"
    fiscal_year       smallint,
    fiscal_quarter    text CHECK (fiscal_quarter IN ('Q1', 'Q2', 'Q3', 'Q4', 'FY')),
    period            text,                                    -- 'Q2 FY2026' / 'FY2025'
    filing_date       date,
    accepted_at       timestamp,
    reported_currency text,
    cik               text,
    data              jsonb       NOT NULL,                    -- every FMP field, as returned
    data_sha          char(64)    NOT NULL,
    source            text        NOT NULL DEFAULT 'FMP',
    source_url        text,                                    -- endpoint URL without the API key
    fetched_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, statement, period_type, period_end)
);
CREATE INDEX IF NOT EXISTS fs_period_idx ON rag.financial_statements (symbol, fiscal_year, fiscal_quarter);
CREATE INDEX IF NOT EXISTS fs_filing_idx ON rag.financial_statements (symbol, filing_date);

-- one numeric fact per row, derived (no copy of the data)
CREATE OR REPLACE VIEW rag.financial_facts AS
SELECT s.symbol, s.statement, s.period_type, s.period_end, s.fiscal_year, s.fiscal_quarter, s.period,
       s.filing_date, s.reported_currency, s.source, s.source_url,
       kv.key AS metric, (kv.value #>> '{}')::numeric AS value
FROM rag.financial_statements s
CROSS JOIN LATERAL jsonb_each(s.data) AS kv
WHERE jsonb_typeof(kv.value) = 'number';

-- earnings announcements: actual vs consensus EPS / revenue
CREATE TABLE IF NOT EXISTS rag.earnings (
    symbol             text    NOT NULL,
    announce_date      date    NOT NULL,
    fiscal_year        smallint,
    fiscal_quarter     text CHECK (fiscal_quarter IN ('Q1', 'Q2', 'Q3', 'Q4', 'FY')),
    period             text,
    eps_actual         numeric,
    eps_estimated      numeric,
    revenue_actual     numeric,
    revenue_estimated  numeric,
    last_updated       date,
    data_sha           char(64) NOT NULL,
    source             text     NOT NULL DEFAULT 'FMP',
    source_url         text,
    fetched_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, announce_date)
);

-- other company information (profile, analyst estimates, ratings, targets, TTM metrics, executives, filing index)
CREATE TABLE IF NOT EXISTS rag.company_data (
    symbol      text        NOT NULL,
    dataset     text        NOT NULL,
    record_key  text        NOT NULL,                          -- e.g. estimate date, filing accession, 'current'
    as_of       date,
    data        jsonb       NOT NULL,
    data_sha    char(64)    NOT NULL,
    source      text        NOT NULL DEFAULT 'FMP',
    source_url  text,
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, dataset, record_key)
);

-- every API call (or deliberate skip): drives incremental updates and the status report
CREATE TABLE IF NOT EXISTS rag.fetch_log (
    id          bigserial   PRIMARY KEY,
    source      text        NOT NULL DEFAULT 'FMP',
    symbol      text,
    endpoint    text        NOT NULL,
    params      jsonb,
    outcome     text        NOT NULL CHECK (outcome IN ('ok', 'skipped', 'unsupported', 'error')),
    http_status integer,
    records     integer,
    added       integer,
    message     text,
    fetched_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fetch_log_recent_idx ON rag.fetch_log (symbol, endpoint, fetched_at DESC);
