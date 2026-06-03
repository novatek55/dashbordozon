-- migrations/add_serp_tables.sql
-- SERP-модуль: снимки выдачи, позиции, конкуренты, главный запрос

-- 1. Снимки выдачи
CREATE TABLE IF NOT EXISTS serp_snapshots (
    id          SERIAL PRIMARY KEY,
    query_text  VARCHAR(500) NOT NULL,
    scraped_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    position_count INTEGER,
    raw_data    JSONB
);
CREATE INDEX IF NOT EXISTS idx_serp_snapshots_query   ON serp_snapshots (query_text);
CREATE INDEX IF NOT EXISTS idx_serp_snapshots_scraped ON serp_snapshots (scraped_at DESC);

-- 2. Позиции в снимке
CREATE TABLE IF NOT EXISTS serp_positions (
    id              SERIAL PRIMARY KEY,
    snapshot_id     INTEGER NOT NULL REFERENCES serp_snapshots(id) ON DELETE CASCADE,
    position        SMALLINT NOT NULL,
    sku             BIGINT,
    title           VARCHAR(500),
    brand           VARCHAR(255),
    price           NUMERIC(15,2),
    price_before    NUMERIC(15,2),
    rating          FLOAT,
    review_count    INTEGER,
    stock           INTEGER,
    promo_label     VARCHAR(100),
    thumbnail_url   TEXT,
    revenue_30d     NUMERIC(15,2),
    sales_per_day   FLOAT,
    is_our_product  BOOLEAN NOT NULL DEFAULT false,
    is_competitor   BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (snapshot_id, position)
);
CREATE INDEX IF NOT EXISTS idx_serp_pos_snapshot ON serp_positions (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_serp_pos_sku      ON serp_positions (sku);

-- 3. Справочник конкурентов (глобальный)
CREATE TABLE IF NOT EXISTS serp_competitors (
    sku         BIGINT PRIMARY KEY,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. Главный запрос артикула
CREATE TABLE IF NOT EXISTS sku_primary_query (
    sku          BIGINT PRIMARY KEY,
    offer_id     VARCHAR(255),
    query_text   VARCHAR(500) NOT NULL,
    set_manually BOOLEAN NOT NULL DEFAULT false,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_spq_offer_id ON sku_primary_query (offer_id);
