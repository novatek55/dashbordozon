# Accrual Table Group Header Style

Date: 2026-05-06
File: `web/orders_dashboard.html`
Scope: report `accruals_comp_by_article` (table `.accrual-unitka-table`)

## Purpose
Unified visual standard for grouped columns with expand/collapse behavior.

## Group Header Rules
1. Expand/collapse is triggered only by click on the first header row group title (e.g., `ВЫРУЧКА +`).
2. In collapsed mode, only the group total column is visible.
3. In expanded mode, nested columns are shown first, and the group total column is always last.
4. Same rule applies recursively for nested groups.
5. Group total header orientation: vertical in collapsed mode, horizontal in expanded mode.

## Border Rules
1. Keep vertical separators and outer borders.
2. Remove only the seam between first and second header rows (`border-bottom` of group row and `border-top` of second row).
3. For nested columns in second row, keep top separator with same style as side separators:
   - color: `#ccd5e0`
   - width: `1px`

## Color Rules
1. Group row (`.col-group-row`) has base group colors.
2. Group total columns (`.col-group-total`) must have the same group color family as the parent group to look like one block.
3. Nested columns (`.nested-head`) use softer tints of the same group palette to improve readability and distinguish detail columns from totals.

## Current Group Order Standard
- `revenue_sales`: `revenue`, `returns_total`, `returns_pct`, `client_revenue`, then total `revenue_sales`.
- `marketplace_expenses`: `ozon_fee_total`, `delivery_services_total`, `agent_services_total`, `fbo_services_total`, `promotion_total`, `other_grouped`, then total `marketplace_expenses`.
- `delivery_services_total`: `dropoff_processing`, `logistics`, `reverse_logistics`, `courier_departure`, `pickup_processing`, `pickup_courier_delivery`, then total.
- `agent_services_total`: `acquiring`, `partner_returns_processing`, `star_products`, `delivery_to_pickup`, `partner_dropoff_processing`, `temporary_partner_storage`, then total.
- `fbo_storage_services`: `warehouse_placement`, `valid_preparation`, `ozon_delivery_to_pvz`, then total.
- `promotion_total`: `ad_spend`, `pay_per_click`, `premium_plus_subscription`, `review_points`, `review_pin`, then total.

## Notes
If new nested groups are added, apply the same pattern:
- nested first,
- total last,
- same border model,
- same palette family with softer nested tint.

