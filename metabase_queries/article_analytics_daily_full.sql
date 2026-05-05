-- Ежедневная аналитика по артикулам (sku/day) из /v1/analytics/data
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
)
SELECT
    ad.date::date AS day,
    COALESCE(sm.offer_id, CONCAT('sku_', ad.sku::text)) AS article,
    ad.sku,

    COALESCE((ad.metric_values ->> 'revenue')::numeric, ad.revenue, 0) AS revenue,
    COALESCE((ad.metric_values ->> 'ordered_units')::numeric, ad.ordered_units, 0) AS ordered_units,
    COALESCE((ad.metric_values ->> 'delivered_units')::numeric, ad.delivered_units, 0) AS delivered_units,
    COALESCE((ad.metric_values ->> 'returns')::numeric, ad.returned_units, 0) AS returns_units,
    COALESCE((ad.metric_values ->> 'cancellations')::numeric, 0) AS cancellations_units,

    COALESCE((ad.metric_values ->> 'hits_view_search')::numeric, 0) AS hits_view_search,
    COALESCE((ad.metric_values ->> 'hits_view_pdp')::numeric, 0) AS hits_view_pdp,
    COALESCE((ad.metric_values ->> 'hits_view')::numeric, ad.impressions, 0) AS hits_view,

    COALESCE((ad.metric_values ->> 'hits_tocart_search')::numeric, 0) AS hits_tocart_search,
    COALESCE((ad.metric_values ->> 'hits_tocart_pdp')::numeric, 0) AS hits_tocart_pdp,
    COALESCE((ad.metric_values ->> 'hits_tocart')::numeric, ad.clicks, 0) AS hits_tocart,

    COALESCE((ad.metric_values ->> 'session_view_search')::numeric, 0) AS session_view_search,
    COALESCE((ad.metric_values ->> 'session_view_pdp')::numeric, 0) AS session_view_pdp,
    COALESCE((ad.metric_values ->> 'session_view')::numeric, 0) AS session_view,

    COALESCE((ad.metric_values ->> 'conv_tocart_search')::numeric, 0) AS conv_tocart_search,
    COALESCE((ad.metric_values ->> 'conv_tocart_pdp')::numeric, 0) AS conv_tocart_pdp,
    COALESCE((ad.metric_values ->> 'conv_tocart')::numeric, ad.ctr, 0) AS conv_tocart,

    COALESCE((ad.metric_values ->> 'position_category')::numeric, ad.position_category, ad.position, 0) AS position_category
FROM analytics_data ad
LEFT JOIN sku_map sm ON sm.sku = ad.sku
WHERE ad.date::date BETWEEN {{date_from}} AND {{date_to}}
ORDER BY day DESC, article, ad.sku;
