"""Dashboard application factory — route registration and lifecycle."""
from aiohttp import web

from src.dashboard.routes.pages import (
    index, finance_costs_page, palletization_page, palletization_asset, shared_theme_css,
)
from src.dashboard.routes.system import (
    health, restart_server, create_pool, close_pool,
    sync_ozon_data, get_sync_status, get_sync_reports,
)
from src.dashboard.routes.orders import get_orders, get_sales, get_articles
from src.dashboard.routes.returns import get_returns
from src.dashboard.routes.finance import (
    get_cash_flow, get_finance_report,
    get_finance_report_accrual,
    get_accruals_comp_by_article, get_accruals_comp_by_article_accrual,
    get_returns_analytics,
    ensure_finance_report_tables,
    get_finance_costs, upload_finance_costs, save_finance_plan,
    get_settings_costs, save_settings_cost,
    analyze_finance_data, get_realization_v2, get_wb_finance_report_daily,
)
from src.dashboard.routes.stocks import (
    get_warehouse_stock, get_analytics_stocks, get_stock_balances, get_wb_stock_balances,
    get_analytics_turnover, get_average_delivery_time,
)
from src.dashboard.routes.analytics import (
    get_analytics_product_queries, get_article_query_matrix,
    get_article_analytics, get_article_characteristics, refresh_article_characteristics,
)
from src.dashboard.routes.actions import (
    get_actions, get_action_products, get_actions_report,
    activate_action_products, deactivate_action_products,
)
from src.dashboard.routes.advertising import (
    get_advertising_summary, get_advertising_report, get_wb_advertising_report, toggle_campaign,
    disable_ad_for_sku, remove_sku_from_all_promos,
)
from src.dashboard.routes.reviews import (
    get_reviews_report, get_reviews_report_detail,
    get_reviews_service_status, post_review_reply,
)
from src.dashboard.routes.questions import (
    get_questions_service_status, get_questions_report,
    get_question_answers, post_question_answer,
    post_questions_change_status,
)
from src.dashboard.routes.chats import (
    get_chats_service_status, get_chats_report,
    get_chat_history, post_chat_send_message,
)
from src.dashboard.routes.unitka import (
    get_unitka_clusters, get_unitka_offer_search, get_unitka_logistics_tariff,
    get_unitka_load_fact, get_unitka_fetch_dimensions, post_unitka_competitor_lookup,
    get_unitka_shop_averages,
    post_unitka_import_bestsellers, post_unitka_import_competitor,
    get_unitka_competitors_recent,
    get_unitka_metrics, get_unitka_refresh_targets, post_unitka_fetch_bestsellers_direct,
)
from src.dashboard.routes.supply import (
    get_supply_plan, save_supply_plan_state, reset_hidden_supply_plan_items,
    fill_supply_plan_from_availability_report, calculate_supply_plan_pallets,
    export_supply_plan_pallets, build_supply_plan_acceptance, filter_supply_plan_pallets,
    repack_supply_plan_cluster, request_supply_plan_timeslots, upload_supply_file,
    sync_cluster_warehouses_to_db,
)
from src.dashboard.routes.supply_chrome import (
    supply_stage2_set_warehouses, supply_multi_cluster_api, supply_mixed_flow,
    supply_scan_warehouses_ui, supply_collect_timeslots, supply_filter_timeslots,
    supply_fill_draft, supply_reconcile_draft_quantities, supply_check_drafts,
    supply_set_vehicle_pass, chrome_auth_init, chrome_auth_status,
)
from src.dashboard.routes.palletization_routes import (
    palletization_products_get, palletization_products_create,
    palletization_products_update, palletization_products_delete,
    palletization_products_import, palletization_shipment_get,
    palletization_shipment_create, palletization_shipment_bulk,
    palletization_shipment_clear, palletization_shipment_missing,
    palletization_pallets_calculate,
)
from src.dashboard.routes.report import get_monthly_report
from src.dashboard.routes.prices import get_price_report
from src.dashboard.routes.serp import (
    plugin_poll, plugin_result,
    post_serp_scrape, post_serp_scrape_by_sku,
    get_serp_snapshot, post_serp_competitor, get_serp_competitors,
    get_serp_primary_query, put_serp_primary_query,
    get_serp_article_report, post_serp_recalculate_primary,
    post_serp_save_from_overlay, get_serp_all_primary_queries,
)


@web.middleware
async def _cors_middleware(request: web.Request, handler):
    """CORS для Chrome-расширения (и preflight OPTIONS)."""
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        resp = await handler(request)
    origin = request.headers.get("Origin") or "*"
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp


def create_app() -> web.Application:
    app = web.Application(middlewares=[_cors_middleware])
    app.router.add_get("/", index)
    app.router.add_get("/shared-report-theme.css", shared_theme_css)
    app.router.add_get("/finance-costs", finance_costs_page)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/monthly-report", get_monthly_report)
    app.router.add_get("/api/orders", get_orders)
    app.router.add_get("/api/sales", get_sales)
    app.router.add_get("/api/actions", get_actions)
    app.router.add_get("/api/price-report", get_price_report)
    app.router.add_get("/api/action-products", get_action_products)
    app.router.add_get("/api/returns", get_returns)
    app.router.add_get("/api/cash-flow", get_cash_flow)
    app.router.add_get("/api/finance-report", get_finance_report)
    app.router.add_get("/api/finance-report-accrual", get_finance_report_accrual)
    app.router.add_get("/api/wb/finance-report-daily", get_wb_finance_report_daily)
    app.router.add_get("/api/accruals-comp-by-article", get_accruals_comp_by_article)
    app.router.add_get("/api/accruals-comp-by-article-accrual", get_accruals_comp_by_article_accrual)
    app.router.add_get("/api/returns-analytics", get_returns_analytics)
    app.router.add_get("/api/actions-report", get_actions_report)
    app.router.add_get("/api/advertising-report", get_advertising_report)
    app.router.add_get("/api/wb/advertising-report", get_wb_advertising_report)
    app.router.add_get("/api/reviews-report", get_reviews_report)
    app.router.add_get("/api/reviews-report/{sku}", get_reviews_report_detail)
    app.router.add_get("/api/reviews/service-status", get_reviews_service_status)
    app.router.add_post("/api/reviews/reply", post_review_reply)
    app.router.add_get("/api/questions/service-status", get_questions_service_status)
    app.router.add_get("/api/questions-report", get_questions_report)
    app.router.add_get("/api/questions-report/{question_id}/answers", get_question_answers)
    app.router.add_post("/api/questions/answer", post_question_answer)
    app.router.add_post("/api/questions/change-status", post_questions_change_status)
    app.router.add_get("/api/chats/service-status", get_chats_service_status)
    app.router.add_get("/api/chats-report", get_chats_report)
    app.router.add_get("/api/chats-report/{chat_id}/history", get_chat_history)
    app.router.add_post("/api/chats/send", post_chat_send_message)
    app.router.add_get("/api/advertising-summary", get_advertising_summary)
    app.router.add_post("/api/advertising/campaign/toggle", toggle_campaign)
    app.router.add_post("/api/rnp/sku/disable-ad", disable_ad_for_sku)
    app.router.add_post("/api/rnp/sku/remove-promos", remove_sku_from_all_promos)
    app.router.add_get("/api/unitka/clusters", get_unitka_clusters)
    app.router.add_get("/api/unitka/offer-search", get_unitka_offer_search)
    app.router.add_get("/api/unitka/refresh-targets", get_unitka_refresh_targets)
    app.router.add_get("/api/unitka/logistics-tariff", get_unitka_logistics_tariff)
    app.router.add_get("/api/unitka/load-fact", get_unitka_load_fact)
    app.router.add_get("/api/unitka/fetch-dimensions", get_unitka_fetch_dimensions)
    app.router.add_post("/api/unitka/competitor-lookup", post_unitka_competitor_lookup)
    app.router.add_get("/api/unitka/shop-averages", get_unitka_shop_averages)
    app.router.add_post("/api/unitka/import/bestsellers", post_unitka_import_bestsellers)
    app.router.add_post("/api/unitka/import/competitor", post_unitka_import_competitor)
    app.router.add_get("/api/unitka/competitors/recent", get_unitka_competitors_recent)
    app.router.add_get("/api/unitka/metrics", get_unitka_metrics)
    app.router.add_post("/api/unitka/fetch-bestsellers-direct", post_unitka_fetch_bestsellers_direct)
    app.router.add_post("/api/action-products/activate", activate_action_products)
    app.router.add_post("/api/action-products/deactivate", deactivate_action_products)
    app.router.add_post("/api/finance-report/plan", save_finance_plan)
    app.router.add_post("/api/restart", restart_server)
    app.router.add_post("/api/sync-ozon", sync_ozon_data)
    app.router.add_post("/api/ozon/cluster-warehouses/sync", sync_cluster_warehouses_to_db)
    app.router.add_get("/api/sync-status", get_sync_status)
    app.router.add_get("/api/sync-reports", get_sync_reports)
    app.router.add_get("/api/finance-costs", get_finance_costs)
    app.router.add_post("/api/finance-costs/upload", upload_finance_costs)
    app.router.add_get("/api/settings/costs", get_settings_costs)
    app.router.add_post("/api/settings/costs", save_settings_cost)
    app.router.add_post("/api/supply-plan/upload-supply-file", upload_supply_file)
    app.router.add_get("/api/warehouse-stock", get_warehouse_stock)
    app.router.add_get("/api/analytics-stocks", get_analytics_stocks)
    app.router.add_get("/api/stock-balances", get_stock_balances)
    app.router.add_get("/api/wb/stock-balances", get_wb_stock_balances)
    app.router.add_get("/api/analytics-product-queries", get_analytics_product_queries)
    app.router.add_get("/api/article-query-matrix", get_article_query_matrix)
    app.router.add_get("/api/article-analytics", get_article_analytics)
    app.router.add_get("/api/supply-plan", get_supply_plan)
    app.router.add_post("/api/supply-plan/state", save_supply_plan_state)
    app.router.add_post("/api/supply-plan/reset-hidden", reset_hidden_supply_plan_items)
    app.router.add_post("/api/supply-plan/fill-from-availability-report", fill_supply_plan_from_availability_report)
    app.router.add_post("/api/supply-plan/pallets", calculate_supply_plan_pallets)
    app.router.add_post("/api/supply-plan/pallets/export", export_supply_plan_pallets)
    app.router.add_post("/api/supply-plan/acceptance", build_supply_plan_acceptance)
    app.router.add_post("/api/supply-plan/pallets/repack-cluster", repack_supply_plan_cluster)
    app.router.add_post("/api/supply-plan/pallets/filter", filter_supply_plan_pallets)
    app.router.add_post("/api/supply-plan/timeslots", request_supply_plan_timeslots)
    app.router.add_post("/api/supply-plan/stage2-warehouses", supply_stage2_set_warehouses)
    app.router.add_post("/api/supply-plan/fill-draft", supply_fill_draft)
    app.router.add_post("/api/supply-plan/multi-cluster-api", supply_multi_cluster_api)
    app.router.add_post("/api/supply-plan/mixed-flow", supply_mixed_flow)
    app.router.add_post("/api/supply-plan/scan-warehouses-ui", supply_scan_warehouses_ui)
    app.router.add_post("/api/supply-plan/collect-timeslots", supply_collect_timeslots)
    app.router.add_post("/api/supply-plan/filter-timeslots", supply_filter_timeslots)
    app.router.add_post("/api/supply-plan/reconcile-draft-quantities", supply_reconcile_draft_quantities)
    app.router.add_post("/api/supply-plan/check-drafts", supply_check_drafts)
    app.router.add_post("/api/supply-plan/set-vehicle-pass", supply_set_vehicle_pass)
    app.router.add_post("/api/chrome/init", chrome_auth_init)
    app.router.add_get("/api/chrome/status", chrome_auth_status)
    app.router.add_get("/api/analytics-turnover", get_analytics_turnover)
    app.router.add_get("/api/average-delivery-time", get_average_delivery_time)
    app.router.add_get("/api/realization-v2", get_realization_v2)
    app.router.add_get("/api/articles", get_articles)
    app.router.add_get("/api/article-characteristics", get_article_characteristics)
    app.router.add_post("/api/article-characteristics/refresh", refresh_article_characteristics)
    app.router.add_get("/api/finance-analyze", analyze_finance_data)
    app.router.add_get("/palletization/", palletization_page)
    app.router.add_get("/palletization/{filename}", palletization_asset)
    app.router.add_get("/api/palletization/products", palletization_products_get)
    app.router.add_post("/api/palletization/products", palletization_products_create)
    app.router.add_put("/api/palletization/products/{sku}", palletization_products_update)
    app.router.add_delete("/api/palletization/products/{sku}", palletization_products_delete)
    app.router.add_post("/api/palletization/products/import", palletization_products_import)
    app.router.add_get("/api/palletization/shipment", palletization_shipment_get)
    app.router.add_post("/api/palletization/shipment", palletization_shipment_create)
    app.router.add_post("/api/palletization/shipment/bulk", palletization_shipment_bulk)
    app.router.add_delete("/api/palletization/shipment", palletization_shipment_clear)
    app.router.add_get("/api/palletization/shipment/missing", palletization_shipment_missing)
    app.router.add_get("/api/palletization/pallets/calculate", palletization_pallets_calculate)
    # SERP module
    app.router.add_get("/api/plugin/poll", plugin_poll)
    app.router.add_post("/api/plugin/result", plugin_result)
    app.router.add_post("/api/serp/scrape", post_serp_scrape)
    app.router.add_post("/api/serp/scrape-by-sku", post_serp_scrape_by_sku)
    app.router.add_get("/api/serp/snapshot", get_serp_snapshot)
    app.router.add_post("/api/serp/competitor", post_serp_competitor)
    app.router.add_get("/api/serp/competitors", get_serp_competitors)
    app.router.add_get("/api/serp/primary-query", get_serp_primary_query)
    app.router.add_put("/api/serp/primary-query", put_serp_primary_query)
    app.router.add_get("/api/serp/article-report", get_serp_article_report)
    app.router.add_post("/api/serp/recalculate-primary", post_serp_recalculate_primary)
    app.router.add_post("/api/serp/save-from-overlay", post_serp_save_from_overlay)
    app.router.add_get("/api/serp/all-primary-queries", get_serp_all_primary_queries)

    app.on_startup.append(create_pool)
    app.on_cleanup.append(close_pool)
    return app
