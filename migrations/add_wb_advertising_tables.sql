CREATE TABLE IF NOT EXISTS wb_advertising_campaigns (
    advert_id BIGINT PRIMARY KEY,
    name TEXT NULL,
    type TEXT NULL,
    status TEXT NULL,
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wb_advertising_daily (
    advert_id BIGINT NOT NULL,
    report_date DATE NOT NULL,
    views BIGINT NOT NULL DEFAULT 0,
    clicks BIGINT NOT NULL DEFAULT 0,
    carts BIGINT NOT NULL DEFAULT 0,
    orders BIGINT NOT NULL DEFAULT 0,
    shks BIGINT NOT NULL DEFAULT 0,
    canceled BIGINT NOT NULL DEFAULT 0,
    spend NUMERIC(15, 2) NOT NULL DEFAULT 0,
    stats_spend NUMERIC(15, 2) NOT NULL DEFAULT 0,
    revenue NUMERIC(15, 2) NOT NULL DEFAULT 0,
    avg_position NUMERIC(10, 2) NOT NULL DEFAULT 0,
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (advert_id, report_date)
);

ALTER TABLE wb_advertising_daily ADD COLUMN IF NOT EXISTS shks BIGINT NOT NULL DEFAULT 0;
ALTER TABLE wb_advertising_daily ADD COLUMN IF NOT EXISTS canceled BIGINT NOT NULL DEFAULT 0;
ALTER TABLE wb_advertising_daily ADD COLUMN IF NOT EXISTS stats_spend NUMERIC(15, 2) NOT NULL DEFAULT 0;
ALTER TABLE wb_advertising_daily ADD COLUMN IF NOT EXISTS avg_position NUMERIC(10, 2) NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_wb_advertising_daily_report_date
ON wb_advertising_daily (report_date);

CREATE TABLE IF NOT EXISTS wb_advertising_nm_daily (
    advert_id BIGINT NOT NULL,
    report_date DATE NOT NULL,
    nm_id BIGINT NOT NULL,
    name TEXT NULL,
    views BIGINT NOT NULL DEFAULT 0,
    clicks BIGINT NOT NULL DEFAULT 0,
    carts BIGINT NOT NULL DEFAULT 0,
    orders BIGINT NOT NULL DEFAULT 0,
    shks BIGINT NOT NULL DEFAULT 0,
    canceled BIGINT NOT NULL DEFAULT 0,
    stats_spend NUMERIC(15, 2) NOT NULL DEFAULT 0,
    revenue NUMERIC(15, 2) NOT NULL DEFAULT 0,
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (advert_id, report_date, nm_id)
);

CREATE INDEX IF NOT EXISTS idx_wb_advertising_nm_daily_report_date
ON wb_advertising_nm_daily (report_date);
