-- Оптимизация: составной индекс (sku, date) для запросов по рекламной статистике
-- Покрывает запросы WHERE sku = ANY(...) AND date >= ... AND date < ...
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_campaign_stat_sku_date
    ON campaign_statistics (sku, date);
