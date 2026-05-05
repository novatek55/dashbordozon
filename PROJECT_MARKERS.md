# Project Markers

## MARKER-2026-04-07-OZON-QUERIES-DELAY

- Area: `analytics_product_queries` (`/v1/analytics/product-queries`)
- Note: Ozon may return query analytics with a delay (often ~1 day or more).
- Symptom: API returns `There is no data for the specified period` for the most recent day.
- Project behavior: sync auto-detects the latest available day and loads data up to that date.
- Added: `2026-04-07`

