-- Actions report (promo_actions)
SELECT
    action_id,
    title,
    action_type,
    status,
    is_participating,
    discount_percent,
    date_start,
    date_end,
    last_synced_at
FROM promo_actions
WHERE 1=1
  [[AND date_start >= {{date_from}}]]
  [[AND date_start < {{date_to}}]]
  [[AND is_participating = {{is_participating}}]]
ORDER BY date_start DESC NULLS LAST;

