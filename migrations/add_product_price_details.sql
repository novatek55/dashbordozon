CREATE TABLE IF NOT EXISTS product_price_details (
    id SERIAL PRIMARY KEY,
    sku BIGINT NOT NULL UNIQUE,
    offer_id VARCHAR(255),
    customer_price NUMERIC(15, 2),
    price NUMERIC(15, 2),
    price_indexes JSONB,
    details_status VARCHAR(50) NOT NULL DEFAULT 'ok',
    error_message TEXT,
    raw_data JSONB,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_product_price_details_offer
    ON product_price_details(offer_id);

CREATE INDEX IF NOT EXISTS idx_product_price_details_synced
    ON product_price_details(last_synced_at);
