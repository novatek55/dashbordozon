-- Products in actions (promo_products + promo_actions)
SELECT
    a.action_id,
    a.title AS action_title,
    a.action_type,
    a.is_participating AS action_is_participating,
    p.sku,
    p.regular_price,
    p.action_price,
    p.discount_percent,
    p.is_participating AS product_is_participating,
    p.last_synced_at
FROM promo_products p
JOIN promo_actions a ON a.id = p.action_id
WHERE 1=1
  [[AND a.date_start >= {{date_from}}]]
  [[AND a.date_start < {{date_to}}]]
  [[AND a.action_id = {{action_id}}]]
  [[AND p.sku = {{sku}}]]
ORDER BY a.date_start DESC NULLS LAST, p.id DESC;

