-- Sales report (fact_orders)
SELECT
    date_trunc('day', created_at) AS day,
    delivery_schema,
    COUNT(*) AS orders_count,
    SUM(COALESCE(items_total, 0)) AS items_total_sum,
    SUM(COALESCE(discount_total, 0)) AS discount_total_sum,
    SUM(COALESCE(delivery_cost, 0)) AS delivery_cost_sum
FROM fact_orders
WHERE 1=1
  [[AND created_at >= {{date_from}}]]
  [[AND created_at < {{date_to}}]]
  [[AND delivery_schema = {{delivery_schema}}]]
GROUP BY 1, 2
ORDER BY 1 DESC, 2;

