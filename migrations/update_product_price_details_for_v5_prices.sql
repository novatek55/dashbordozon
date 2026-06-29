ALTER TABLE product_price_details
    ALTER COLUMN sku DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_product_price_details_offer_id
    ON product_price_details(offer_id)
    WHERE offer_id IS NOT NULL;
