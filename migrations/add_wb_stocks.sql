CREATE TABLE IF NOT EXISTS wb_stocks (
    nm_id BIGINT NOT NULL,
    supplier_article TEXT NOT NULL DEFAULT '',
    barcode TEXT NOT NULL DEFAULT '',
    warehouse_name TEXT NOT NULL DEFAULT '',
    category TEXT,
    subject TEXT,
    brand TEXT,
    tech_size TEXT,
    quantity INTEGER NOT NULL DEFAULT 0,
    in_way_to_client INTEGER NOT NULL DEFAULT 0,
    in_way_from_client INTEGER NOT NULL DEFAULT 0,
    quantity_full INTEGER NOT NULL DEFAULT 0,
    price NUMERIC,
    discount NUMERIC,
    is_supply BOOLEAN,
    is_realization BOOLEAN,
    sc_code TEXT,
    last_change_date TIMESTAMPTZ,
    raw_data JSONB,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (nm_id, supplier_article, barcode, warehouse_name)
);

CREATE INDEX IF NOT EXISTS idx_wb_stocks_supplier_article ON wb_stocks (supplier_article);
CREATE INDEX IF NOT EXISTS idx_wb_stocks_nm_id ON wb_stocks (nm_id);
CREATE INDEX IF NOT EXISTS idx_wb_stocks_last_synced_at ON wb_stocks (last_synced_at);
