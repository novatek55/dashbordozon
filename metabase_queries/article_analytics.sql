-- Аналитика по артикулам (offer_id) из /v1/analytics/data
-- Параметры Metabase:
--   {{date_from}} (Date, required)
--   {{date_to}}   (Date, required)

WITH sku_map AS (
    SELECT DISTINCT ON (sku)
        sku,
        offer_id
    FROM (
        SELECT
            fbo_sku_id::bigint AS sku,
            trim(offer_id) AS offer_id,
            last_synced_at
        FROM report_products_items
        WHERE fbo_sku_id IS NOT NULL
          AND COALESCE(trim(offer_id), '') <> ''

        UNION ALL

        SELECT
            fbs_sku_id::bigint AS sku,
            trim(offer_id) AS offer_id,
            last_synced_at
        FROM report_products_items
        WHERE fbs_sku_id IS NOT NULL
          AND COALESCE(trim(offer_id), '') <> ''
    ) src
    ORDER BY sku, last_synced_at DESC NULLS LAST
),
agg AS (
    SELECT
        date::date AS day,
        sku,
        SUM(COALESCE(impressions, 0)) AS impressions,
        SUM(COALESCE(clicks, 0)) AS clicks,
        SUM(COALESCE(ordered_units, 0)) AS ordered_units,
        SUM(COALESCE(delivered_units, 0)) AS delivered_units,
        SUM(COALESCE(returned_units, 0)) AS returned_units,
        SUM(COALESCE(revenue, 0)) AS revenue,
        AVG(position) AS avg_position,
        AVG(position_category) AS avg_position_category,
        AVG(position_promo) AS avg_position_promo
    FROM analytics_data
    WHERE date::date BETWEEN {{date_from}} AND {{date_to}}
    GROUP BY date::date, sku
)
SELECT
    COALESCE(sm.offer_id, CONCAT('sku_', agg.sku::text)) AS article,
    agg.sku,
    SUM(agg.impressions) AS impressions,
    SUM(agg.clicks) AS clicks,
    CASE
        WHEN SUM(agg.impressions) > 0
            THEN ROUND((SUM(agg.clicks)::numeric / SUM(agg.impressions)::numeric) * 100, 2)
        ELSE 0
    END AS ctr_percent,
    SUM(agg.ordered_units) AS ordered_units,
    SUM(agg.delivered_units) AS delivered_units,
    SUM(agg.returned_units) AS returned_units,
    SUM(agg.revenue) AS revenue,
    ROUND(AVG(agg.avg_position)::numeric, 2) AS avg_position,
    ROUND(AVG(agg.avg_position_category)::numeric, 2) AS avg_position_category,
    ROUND(AVG(agg.avg_position_promo)::numeric, 2) AS avg_position_promo
FROM agg
LEFT JOIN sku_map sm ON sm.sku = agg.sku
GROUP BY COALESCE(sm.offer_id, CONCAT('sku_', agg.sku::text)), agg.sku
ORDER BY revenue DESC NULLS LAST, impressions DESC, clicks DESC;
