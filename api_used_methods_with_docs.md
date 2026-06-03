# Used API methods with Ozon docs

Swagger file: `swagger.json`
Spec version: `3.0.0`
Matched: **52** / 66
Missing in swagger: **14**

| Method | Path | operationId | Summary | Deprecated |
|---|---|---|---|---|
| GET | `/v1/actions` | `Promos` | Список акций | no |
| POST | `/v1/actions/candidates` | `PromosCandidates` | Список доступных для акции товаров | no |
| POST | `/v1/actions/products` | `PromosProducts` | Список участвующих в акции товаров | no |
| POST | `/v1/actions/products/activate` | `PromosProductsActivate` | Добавить товар в акцию | no |
| POST | `/v1/actions/products/deactivate` | `PromosProductsDeactivate` | Удалить товары из акции | no |
| POST | `/v1/analytics/data` | `AnalyticsAPI_AnalyticsGetData` | Данные аналитики | no |
| POST | `/v1/analytics/manage/stocks` | `AnalyticsAPI_ManageStocks` | Управление остатками | no |
| POST | `/v1/analytics/product-queries` | `AnalyticsAPI_AnalyticsProductQueries` | Получить информацию о запросах моих товаров | no |
| POST | `/v1/analytics/product-queries/details` | `AnalyticsAPI_AnalyticsProductQueriesDetails` | Получить детализацию запросов по товару | no |
| POST | `/v1/analytics/stocks` | `AnalyticsAPI_AnalyticsStocks` | Получить аналитику по остаткам | no |
| POST | `/v1/analytics/turnover/stocks` | `AnalyticsAPI_StocksTurnover` | Оборачиваемость товара | no |
| POST | `/v1/cluster/list` | `SupplyDraftAPI_DraftClusterList` | Информация о кластерах и их складах | no |
| POST | `/v1/delivery-method/list` | `WarehouseAPI_DeliveryMethodList` | Список методов доставки склада | no |
| POST | `/v1/finance/balance` | `GetFinanceBalanceV1` | Получить отчёт о балансе | no |
| POST | `/v1/finance/cash-flow-statement/list` | `FinanceAPI_FinanceCashFlowStatementList` | Финансовый отчёт | no |
| POST | `/v1/finance/compensation` | `ReportAPI_GetCompensationReport` | Отчёт о компенсациях | no |
| POST | `/v1/finance/decompensation` | `ReportAPI_GetDecompensationReport` | Отчёт о декомпенсациях | no |
| POST | `/v1/finance/document-b2b-sales` | `ReportAPI_CreateDocumentB2BSalesReport` | Реестр продаж юридическим лицам | no |
| POST | `/v1/finance/mutual-settlement` | `ReportAPI_CreateMutualSettlementReport` | Отчёт о взаиморасчётах | no |
| POST | `/v1/product/info/warehouse/stocks` | `ProductInfoWarehouseStocks` | Получить информацию по остаткам на складе FBS и rFBS | no |
| POST | `/v1/question/answer/list` | `QuestionAnswer_List` | Список ответов на вопрос | no |
| POST | `/v1/question/count` | `Question_Count` | Количество вопросов по статусам | no |
| POST | `/v1/question/info` | `Question_Info` | Информация о вопросе | no |
| POST | `/v1/question/list` | `Question_List` | Список вопросов | no |
| POST | `/v1/rating/history` | `RatingAPI_RatingHistoryV1` | Получить информацию о рейтингах продавца за период | no |
| POST | `/v1/rating/summary` | `RatingAPI_RatingSummaryV1` | Получить информацию о текущих рейтингах продавца | no |
| POST | `/v1/report/info` | `ReportAPI_ReportInfo` | Информация об отчёте | no |
| POST | `/v1/report/list` | `ReportAPI_ReportList` | Список отчётов | no |
| POST | `/v1/report/postings/create` | `ReportAPI_CreateCompanyPostingsReport` | Отчёт об отправлениях | no |
| POST | `/v1/report/products/create` | `ReportAPI_CreateCompanyProductsReport` | Отчёт по товарам | no |
| POST | `/v1/report/warehouse/stock` | `ReportAPI_CreateStockByWarehouseReport` | Отчёт об остатках на FBS-складе | no |
| POST | `/v1/returns/list` | `returnsList` | Информация о возвратах FBO и FBS | no |
| POST | `/v1/review/comment/create` | `ReviewAPI_CommentCreate` | Оставить комментарий на отзыв | no |
| POST | `/v1/review/comment/list` | `ReviewAPI_CommentList` | Получить список комментариев на отзыв | no |
| POST | `/v1/review/count` | `ReviewAPI_ReviewCount` | Количество отзывов по статусам | yes |
| POST | `/v1/review/info` | `ReviewAPI_ReviewInfo` | Получить информацию об отзыве | yes |
| POST | `/v1/review/list` | `ReviewAPI_ReviewList` | Получить список отзывов | yes |
| POST | `/v1/warehouse/fbo/seller/list` | `WarehouseFboSellerList` | Получить список складов продавца | no |
| POST | `/v2/finance/realization` | `FinanceAPI_GetRealizationReportV2` | Отчёт о реализации товаров (версия 2) | no |
| POST | `/v2/posting/fbo/list` | `PostingAPI_GetFboPostingList` | Список отправлений | yes |
| POST | `/v2/product/info/stocks-by-warehouse/fbs` | `ProductAPI_GetProductInfoStocksByWarehouseFbsV2` | Получить информацию об остатках на складах продавца | no |
| POST | `/v2/report/returns/create` | `ReportAPI_ReportReturnsCreate` | Отчёт о возвратах | no |
| POST | `/v2/returns/rfbs/get` | `RFBSReturnsAPI_ReturnsRfbsGetV2` | Информация о заявке на возврат | no |
| POST | `/v2/returns/rfbs/list` | `RFBSReturnsAPI_ReturnsRfbsListV2` | Список заявок на возврат | no |
| POST | `/v3/finance/transaction/list` | `FinanceAPI_FinanceTransactionListV3` | Список транзакций | no |
| POST | `/v3/finance/transaction/totals` | `FinanceAPI_FinanceTransactionTotalV3` | Суммы транзакций | no |
| POST | `/v3/posting/fbs/get` | `PostingAPI_GetFbsPostingV3` | Получить информацию об отправлении по идентификатору | no |
| POST | `/v3/posting/fbs/list` | `PostingAPI_GetFbsPostingListV3` | Список отправлений | yes |
| POST | `/v3/product/info/list` | `ProductAPI_GetProductInfoList` | Получить информацию о товарах по идентификаторам | no |
| POST | `/v3/product/list` | `ProductAPI_GetProductList` | Список товаров | no |
| POST | `/v4/product/info/attributes` | `ProductAPI_GetProductAttributesV4` | Получить описание характеристик товара | no |
| POST | `/v4/product/info/stocks` | `ProductAPI_GetProductInfoStocks` | Информация о количестве товаров | no |

## Not Found In Swagger
- GET `/api/client/campaign`
- POST `/api/client/statistic/orders/generate/json`
- POST `/api/client/statistics/json`
- POST `/api/client/token`
- POST `/v1/analytics/average-delivery-time`
- POST `/v1/analytics/average-delivery-time/details`
- POST `/v1/analytics/average-delivery-time/summary`
- POST `/v1/chat/history`
- POST `/v1/chat/list`
- POST `/v1/chat/updates`
- POST `/v1/product/info/list`
- POST `/v1/question/top_sku`
- POST `/v2/product/info`
- POST `/v4/product/info/prices`
