"""Dashboard routes/system.py handlers."""
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
from aiohttp import web

from src.config import settings
from src.dashboard.constants import BASE_DIR, MSK
from src.dashboard import state
from src.dashboard.state import sync_status, SYNC_LOCK_PATH
from src.dashboard.helpers import to_asyncpg_dsn
from src.dashboard.routes.finance import ensure_finance_report_tables
from src.dashboard.routes.reviews import ensure_reviews_report_tables


@dataclass(frozen=True)
class SyncReportTableSpec:
    table: str
    business_date_column: Optional[str] = None
    synced_at_column: Optional[str] = "last_synced_at"


@dataclass(frozen=True)
class OzonSyncReportSpec:
    mode: str
    title: str
    entity_type: str
    tables: Tuple[SyncReportTableSpec, ...]
    max_lag_days: int
    critical: bool = True
    periodicity: str = "daily"


def get_ozon_sync_report_specs() -> Tuple[OzonSyncReportSpec, ...]:
    return (
        OzonSyncReportSpec("products", "Каталог товаров", "products", (SyncReportTableSpec("products", "updated_at"),), 2),
        OzonSyncReportSpec("report_postings", "Заказы и продажи", "report_postings", (SyncReportTableSpec("fact_orders", "created_at"), SyncReportTableSpec("fact_order_items")), 2),
        OzonSyncReportSpec("report_returns", "Отчет возвратов", "report_returns", (SyncReportTableSpec("report_returns_items", "returned_at"),), 2, False),
        OzonSyncReportSpec("returns", "Возвраты FBS", "returns", (SyncReportTableSpec("returns", "returned_at"),), 2, False),
        OzonSyncReportSpec("returns_fbo", "Возвраты FBO", "returns_fbo", (SyncReportTableSpec("returns_fbo", "returned_at"),), 2, False),
        OzonSyncReportSpec("report_products", "Отчет товаров", "report_products", (SyncReportTableSpec("report_products_items"),), 2),
        OzonSyncReportSpec("report_warehouse_stock", "Остатки складов", "report_warehouse_stock", (SyncReportTableSpec("report_warehouse_stock_items"),), 1),
        OzonSyncReportSpec("analytics_stocks", "Аналитика остатков", "analytics_stocks", (SyncReportTableSpec("analytics_stocks"),), 1),
        OzonSyncReportSpec("analytics_data", "Дневная SKU-статистика", "analytics_data", (SyncReportTableSpec("analytics_data", "date"),), 2),
        OzonSyncReportSpec("analytics_product_queries", "Поисковые запросы по SKU", "analytics_product_queries", (SyncReportTableSpec("analytics_product_query_details", "period_start"), SyncReportTableSpec("analytics_product_query_summary", "period_start")), 2),
        OzonSyncReportSpec("campaigns", "Рекламные кампании", "campaigns", (SyncReportTableSpec("campaigns"), SyncReportTableSpec("campaign_statistics", "date", None)), 2),
        OzonSyncReportSpec("fbs_warehouse_stocks", "FBS остатки", "fbs_warehouse_stocks", (SyncReportTableSpec("fbs_warehouse_stocks"),), 1),
        OzonSyncReportSpec("analytics_turnover", "Оборачиваемость", "analytics_turnover", (SyncReportTableSpec("analytics_turnover"),), 2),
        OzonSyncReportSpec("average_delivery_time", "Время доставки", "analytics_average_delivery_time", (SyncReportTableSpec("analytics_average_delivery_time"),), 2),
        OzonSyncReportSpec("realization_v2", "Реализация", "realization_v2", (SyncReportTableSpec("realization_reports", "date"), SyncReportTableSpec("realization_report_details", "document_date")), 35, periodicity="monthly"),
        OzonSyncReportSpec("report_compensation", "Компенсации", "report_compensation", (SyncReportTableSpec("report_compensation_items", "effective_date"),), 31, periodicity="monthly"),
        OzonSyncReportSpec("transactions", "Транзакции", "transactions", (SyncReportTableSpec("transactions", "operation_date"),), 2),
        OzonSyncReportSpec("promo", "Акции", "promo", (SyncReportTableSpec("promo_actions"), SyncReportTableSpec("promo_products"),), 2),
        OzonSyncReportSpec("reviews", "Отзывы", "reviews", (SyncReportTableSpec("reviews", "published_at"),), 2, False),
    )


def _quote_ident(identifier: str) -> str:
    safe = str(identifier or "")
    if not safe.replace("_", "").isalnum() or not safe:
        raise ValueError(f"Unsafe SQL identifier: {identifier!r}")
    return '"' + safe.replace('"', '""') + '"'


def _serialize_dt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _date_lag_days(value: Any, now: datetime) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, datetime):
        value_date = value.date()
    else:
        try:
            value_date = datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return max(0, (now.date() - value_date).days)


async def _table_exists(conn: asyncpg.Connection, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1)", table))


async def _load_table_freshness(conn: asyncpg.Connection, spec: SyncReportTableSpec) -> Dict[str, Any]:
    if not await _table_exists(conn, spec.table):
        return {
            "table": spec.table,
            "rows_total": 0,
            "last_synced_at": None,
            "latest_business_date": None,
            "status": "error",
            "error": "table not found",
        }

    table_sql = _quote_ident(spec.table)
    sync_expr = "NULL"
    if spec.synced_at_column:
        sync_expr = f"max({_quote_ident(spec.synced_at_column)})"
    business_expr = "NULL"
    if spec.business_date_column:
        business_expr = f"max({_quote_ident(spec.business_date_column)})"
    row = await conn.fetchrow(
        f"""
        SELECT count(*)::bigint AS rows_total,
               {sync_expr} AS last_synced_at,
               {business_expr} AS latest_business_date
        FROM {table_sql}
        """
    )
    return {
        "table": spec.table,
        "rows_total": int(row["rows_total"] or 0) if row else 0,
        "last_synced_at": _serialize_dt(row["last_synced_at"] if row else None),
        "latest_business_date": _serialize_dt(row["latest_business_date"] if row else None),
        "status": "ok",
        "error": "",
    }


async def build_ozon_sync_reports_payload(pool: asyncpg.Pool, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    reports: List[Dict[str, Any]] = []
    async with pool.acquire() as conn:
        for spec in get_ozon_sync_report_specs():
            sync_log = await conn.fetchrow(
                """
                SELECT status, started_at, completed_at, error_message, records_processed
                FROM sync_logs
                WHERE entity_type = $1
                ORDER BY started_at DESC
                LIMIT 1
                """,
                spec.entity_type,
            )
            table_rows = [await _load_table_freshness(conn, table_spec) for table_spec in spec.tables]
            valid_tables = [row for row in table_rows if row.get("status") == "ok"]
            rows_total = sum(int(row.get("rows_total") or 0) for row in valid_tables)
            latest_sync_values = [row.get("last_synced_at") for row in valid_tables if row.get("last_synced_at")]
            latest_business_values = [row.get("latest_business_date") for row in valid_tables if row.get("latest_business_date")]
            latest_synced_at = max(latest_sync_values) if latest_sync_values else None
            latest_business_date = max(latest_business_values) if latest_business_values else None
            lag_source = latest_business_date or latest_synced_at
            lag_days = _date_lag_days(lag_source, now)

            if any(row.get("status") == "error" for row in table_rows):
                status = "error"
            elif rows_total <= 0:
                status = "no_data"
            elif lag_days is not None and lag_days > spec.max_lag_days:
                status = "stale"
            elif sync_log and sync_log["status"] == "error":
                status = "error"
            else:
                status = "ok"

            reports.append({
                "mode": spec.mode,
                "title": spec.title,
                "entity_type": spec.entity_type,
                "critical": spec.critical,
                "periodicity": spec.periodicity,
                "max_lag_days": spec.max_lag_days,
                "status": status,
                "lag_days": lag_days,
                "rows_total": rows_total,
                "last_synced_at": latest_synced_at,
                "latest_business_date": latest_business_date,
                "last_sync_status": sync_log["status"] if sync_log else None,
                "last_sync_completed_at": _serialize_dt(sync_log["completed_at"]) if sync_log else None,
                "last_sync_error": sync_log["error_message"] if sync_log else None,
                "tables": table_rows,
            })
    summary = {
        "total": len(reports),
        "ok": sum(1 for item in reports if item["status"] == "ok"),
        "stale": sum(1 for item in reports if item["status"] == "stale"),
        "error": sum(1 for item in reports if item["status"] == "error"),
        "no_data": sum(1 for item in reports if item["status"] == "no_data"),
    }
    return {"generated_at": now.isoformat(), "summary": summary, "items": reports}


async def restart_server(_: web.Request) -> web.Response:
    def do_restart() -> None:
        # If the dashboard is managed by an external supervisor (run_dashboard.ps1),
        # exiting current process is enough: supervisor will start a fresh one.
        if os.environ.get("DASHBOARD_SUPERVISOR") == "1":
            os._exit(0)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    loop = asyncio.get_running_loop()
    loop.call_later(0.5, do_restart)
    return web.json_response({"status": "restarting"})


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def create_pool(app: web.Application) -> None:
    dsn = to_asyncpg_dsn(settings.database_url)
    app["pool"] = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
    await ensure_finance_report_tables(app["pool"])
    await ensure_reviews_report_tables(app["pool"])
    # Авто-расчёт главных запросов при старте
    try:
        from src.services.serp_service import recalculate_primary_queries
        import logging
        n = await recalculate_primary_queries(app["pool"])
        logging.getLogger(__name__).info("SERP: recalculated primary queries on startup: %d", n)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("SERP: primary query recalculate failed on startup: %s", e)


async def close_pool(app: web.Application) -> None:
    pool: asyncpg.Pool = app["pool"]
    await pool.close()


def reset_sync_status():
    """Сбрасывает статус синхронизации."""
    sync_status["is_running"] = False
    sync_status["progress"] = 0
    sync_status["stage"] = ""
    sync_status["stages"] = []
    sync_status["step_results"] = []
    sync_status["current_detail"] = ""
    sync_status["current_log"] = []
    sync_status["started_at"] = None
    sync_status["completed_at"] = None
    sync_status["error"] = None


def _pid_is_running(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_sync_lock() -> Optional[Dict[str, Any]]:
    if not SYNC_LOCK_PATH.exists():
        return None
    try:
        raw = SYNC_LOCK_PATH.read_text(encoding="utf-8").strip()
        return json.loads(raw) if raw else {}
    except Exception:
        return {"raw": "unreadable"}


def _acquire_sync_lock() -> Tuple[bool, Optional[Dict[str, Any]]]:
    SYNC_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "started_at": datetime.now().isoformat(),
    }
    try:
        fd = os.open(str(SYNC_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        lock_info = _read_sync_lock() or {}
        lock_pid = lock_info.get("pid")
        if _pid_is_running(lock_pid):
            return False, lock_info
        try:
            SYNC_LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            return False, lock_info
        return _acquire_sync_lock()
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
    except Exception:
        try:
            SYNC_LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return True, payload


def _release_sync_lock() -> None:
    try:
        lock_info = _read_sync_lock() or {}
        lock_pid = lock_info.get("pid")
        if lock_pid and lock_pid != os.getpid():
            return
        SYNC_LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def update_sync_status(progress: int, stage: str):
    """Обновляет статус синхронизации."""
    sync_status["progress"] = progress
    sync_status["stage"] = stage
    if stage:
        stages = sync_status.setdefault("stages", [])
        if not stages or stages[-1].get("stage") != stage:
            stages.append(
                {
                    "stage": stage,
                    "progress": progress,
                    "at": datetime.now().isoformat(),
                }
            )


def update_sync_detail(detail: str) -> None:
    """Stores the current low-level sync action for the dashboard UI."""
    sync_status["current_detail"] = str(detail or "").strip()


def append_sync_log_line(text: str, limit: int = 12) -> None:
    """Keeps a small rolling tail of the active subprocess output."""
    line = str(text or "").strip()
    if not line:
        return
    lines = sync_status.setdefault("current_log", [])
    lines.append(line)
    if len(lines) > limit:
        del lines[:-limit]


def build_finance_sync_steps(days_back: int) -> List[Tuple[List[str], str, bool]]:
    """Build dashboard finance/all sync steps.

    Tuple item: command, UI stage, continue_on_error.
    Product-query analytics feed the visible SKU request report, so that step is
    critical: if it fails, the dashboard must not finish as if fresh data loaded.
    """
    return [
        ([sys.executable, "-m", "src.main", "--mode", "products"], "Обновление каталога товаров (products)", False),
        ([sys.executable, "-m", "src.main", "--mode", "report_postings"], "Обновление заказов и продаж (включая кластеры доставки)", False),
        ([sys.executable, "-m", "src.main", "--mode", "report_returns"], "Обновление отчёта возвратов", True),
        ([sys.executable, "-m", "src.main", "--mode", "returns"], "Обновление возвратов FBS", True),
        ([sys.executable, "-m", "src.main", "--mode", "returns_fbo"], "Обновление возвратов FBO", True),
        ([sys.executable, "-m", "src.main", "--mode", "report_products"], "Обновление отчёта товаров", False),
        ([sys.executable, "-m", "src.main", "--mode", "report_warehouse_stock"], "Обновление отчёта остатков складов", False),
        ([sys.executable, "-m", "src.main", "--mode", "analytics_stocks"], "Обновление аналитики остатков", False),
        ([sys.executable, "-m", "src.main", "--mode", "analytics_data", "--days-back", str(days_back)], "Обновление дневной SKU-статистики", False),
        ([sys.executable, "-m", "src.main", "--mode", "analytics_product_queries", "--days-back", str(days_back)], "Обновление поисковых запросов по SKU", False),
        ([sys.executable, "-m", "src.main", "--mode", "campaigns"], "Обновление рекламных кампаний и статистики", False),
        ([sys.executable, "-m", "src.main", "--mode", "fbs_warehouse_stocks"], "Обновление FBS остатков", False),
        ([sys.executable, "-m", "src.main", "--mode", "analytics_turnover", "--days-back", str(days_back)], "Обновление оборачиваемости", False),
        ([sys.executable, "-m", "src.main", "--mode", "average_delivery_time"], "Обновление времени доставки", False),
        ([sys.executable, "-m", "src.main", "--mode", "realization_v2", "--days-back", str(days_back)], "Обновление реализации", False),
        ([sys.executable, "-m", "src.main", "--mode", "report_compensation"], "Обновление компенсаций", False),
        ([sys.executable, "-m", "src.main", "--mode", "transactions", "--days-back", str(days_back)], "Обновление транзакций", False),
        ([sys.executable, "-m", "src.main", "--mode", "normalize_finance"], "Нормализация финансов", False),
        ([sys.executable, "-m", "src.main", "--mode", "promo"], "Обновление акций", False),
        ([sys.executable, "-m", "src.main", "--mode", "reviews"], "Обновление отзывов", True),
    ]


def build_wb_finance_sync_steps(days_back: int) -> List[Tuple[List[str], str, bool]]:
    """Build WB finance sync steps for the dashboard WB tab."""
    return [
        ([sys.executable, "-m", "src.main", "--mode", "wb_finance_raw", "--days-back", str(days_back)], "ВБ: загрузка финансовой детализации", False),
        ([sys.executable, "-m", "src.main", "--mode", "wb_finance_normalize"], "ВБ: нормализация финансов", False),
        ([sys.executable, "-m", "src.main", "--mode", "wb_finance_daily"], "ВБ: пересборка дневной витрины", False),
        ([sys.executable, "-m", "src.main", "--mode", "wb_stocks"], "WB: sync stocks", False),
        ([sys.executable, "-m", "src.main", "--mode", "wb_advertising", "--days-back", str(days_back)], "WB: sync advertising", False),
    ]


def build_sync_status_response() -> Dict[str, Any]:
    """Returns sync status normalized for UI polling."""
    progress = int(sync_status.get("progress") or 0)
    stage = sync_status.get("stage") or ""
    stages = sync_status.get("stages", [])
    completed_at = sync_status.get("completed_at")
    error = sync_status.get("error")

    if completed_at and not error:
        progress = max(progress, 100)
        if any(str(item.get("stage") or "").strip() == "Завершено" for item in stages):
            stage = "Завершено"

    return {
        "is_running": sync_status["is_running"],
        "progress": progress,
        "stage": stage,
        "current_detail": sync_status.get("current_detail", ""),
        "current_log": sync_status.get("current_log", []),
        "stages": stages,
        "step_results": sync_status.get("step_results", []),
        "started_at": sync_status["started_at"],
        "completed_at": completed_at,
        "error": error,
    }


async def run_sync_step(cmd: list, stage: str, progress: int, continue_on_error: bool = False):
    """Запускает шаг синхронизации и обновляет прогресс."""
    update_sync_status(progress, stage)
    update_sync_detail("Запускаем шаг")
    sync_status["current_log"] = []
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(BASE_DIR),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_chunks: List[str] = []
    stderr_chunks: List[str] = []

    substage_markers: List[Tuple[str, str, int]] = []
    if "report_postings" in cmd:
        substage_markers = [
            ("Starting report_postings sync...", "Заказы и продажи: запрос posting-отчета", progress),
            ("Created seller_postings report", "Заказы и продажи: Ozon готовит posting-отчет", progress + 1),
            ("order delivery clusters sync", "Заказы и продажи: обогащение кластерами доставки", progress + 2),
            ("report_postings sync completed", "Заказы и продажи: завершение шага", progress + 3),
        ]

    last_substage = stage

    async def _read_stream(stream: asyncio.StreamReader, sink: List[str]) -> None:
        nonlocal last_substage
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="ignore")
            sink.append(text)
            append_sync_log_line(text)
            lowered = text.lower()
            if "requesting" in lowered or "запрос" in lowered:
                update_sync_detail("Запрашиваем данные у Ozon")
            elif "created" in lowered and "report" in lowered:
                update_sync_detail("Ozon сформировал отчёт")
            elif "download" in lowered or "скач" in lowered:
                update_sync_detail("Скачиваем отчёт")
            elif "rate limit" in lowered or "code\":8" in lowered:
                update_sync_detail("Ждём из-за лимита запросов Ozon")
            elif "retry" in lowered or "повтор" in lowered:
                update_sync_detail("Повторяем запрос")
            elif "insert" in lowered or "upsert" in lowered or "saved" in lowered or "completed" in lowered:
                update_sync_detail("Сохраняем данные в базу")
            for marker, substage, substage_progress in substage_markers:
                if marker in text and last_substage != substage:
                    update_sync_status(min(95, substage_progress), substage)
                    update_sync_detail(substage)
                    last_substage = substage
                    break

    await asyncio.gather(
        _read_stream(process.stdout, stdout_chunks),
        _read_stream(process.stderr, stderr_chunks),
    )
    await process.wait()

    if process.returncode != 0:
        stderr_text = "".join(stderr_chunks).strip()
        stdout_text = "".join(stdout_chunks).strip()
        error_msg = stderr_text or stdout_text or "Unknown error"
        if continue_on_error:
            update_sync_status(progress, f"{stage} (пропущено: ошибка)")
            return {"ok": False, "error": error_msg}
        raise Exception(f"{stage} failed: {error_msg}")

    return {"ok": True, "output": "".join(stdout_chunks)}


async def sync_ozon_data(request: web.Request) -> web.Response:
    """Запускает синхронизацию данных с Ozon API."""
    import subprocess
    import sys
    import asyncio
    from datetime import datetime
    
    # Проверяем, не запущена ли уже синхронизация
    if sync_status["is_running"]:
        return web.json_response({
            "status": "already_running",
            "message": "Синхронизация уже выполняется",
            "progress": sync_status["progress"],
            "stage": sync_status["stage"]
        })
    lock_acquired = False
    
    try:
        data = await request.json() if request.body_exists else {}
        profile = data.get("profile", "finance")
        days_back = data.get("days_back", 30)

        lock_acquired, lock_info = _acquire_sync_lock()
        if not lock_acquired:
            started_at = lock_info.get("started_at") if isinstance(lock_info, dict) else None
            return web.json_response({
                "status": "already_running",
                "message": "Синхронизация уже выполняется в другом процессе сервера",
                "progress": sync_status["progress"],
                "stage": sync_status["stage"],
                "current_detail": sync_status.get("current_detail", ""),
                "current_log": sync_status.get("current_log", []),
                "started_at": started_at,
            })
        
        # Сбрасываем и запускаем синхронизацию
        reset_sync_status()
        sync_status["is_running"] = True
        sync_status["started_at"] = datetime.now().isoformat()
        update_sync_status(0, "Инициализация")
        
        # Запускаем синхронизацию в фоновом потоке
        async def do_sync():
            try:
                if profile == "wb_finance":
                    steps = build_wb_finance_sync_steps(days_back)
                    steps_count = len(steps)
                    for i, (cmd, stage, continue_on_error) in enumerate(steps, start=1):
                        progress = min(95, int((i / max(steps_count, 1)) * 95))
                        step_started_at = datetime.now().isoformat()
                        step_status = "OK"
                        step_error = ""
                        try:
                            await run_sync_step(cmd, stage, progress, continue_on_error=continue_on_error)
                        except Exception as step_exc:
                            step_status = "ERROR"
                            step_error = str(step_exc)[:500]
                            sync_status.setdefault("step_results", []).append({
                                "stage": stage,
                                "status": step_status,
                                "details": "Ошибка",
                                "error": step_error,
                                "started_at": step_started_at,
                                "at": datetime.now().isoformat(),
                            })
                            raise
                        else:
                            sync_status.setdefault("step_results", []).append({
                                "stage": stage,
                                "status": step_status,
                                "details": "Загружено",
                                "error": step_error,
                                "started_at": step_started_at,
                                "at": datetime.now().isoformat(),
                            })
                elif profile == "report_products":
                    steps = [([sys.executable, "-m", "src.main", "--mode", "report_products"], "Обновление отчета товаров", False)]
                    for cmd, stage, continue_on_error in steps:
                        await run_sync_step(cmd, stage, 95, continue_on_error=continue_on_error)
                        sync_status.setdefault("step_results", []).append({
                            "stage": stage,
                            "status": "OK",
                            "details": "Загружено",
                            "error": "",
                            "started_at": datetime.now().isoformat(),
                            "at": datetime.now().isoformat(),
                        })
                elif profile in {"finance", "all", "all_reports"}:
                    # Полный цикл обновления данных для всех отчётов в дашборде.
                    steps: List[Tuple[List[str], str, bool]] = [
                        ([sys.executable, "-m", "src.main", "--mode", "products"], "Обновление каталога товаров (products)", False),
                        ([sys.executable, "-m", "src.main", "--mode", "report_postings"], "Обновление заказов и продаж (включая кластеры доставки)", False),
                        ([sys.executable, "-m", "src.main", "--mode", "report_returns"], "Обновление отчёта возвратов", True),
                        ([sys.executable, "-m", "src.main", "--mode", "returns"], "Обновление возвратов FBS", True),
                        ([sys.executable, "-m", "src.main", "--mode", "returns_fbo"], "Обновление возвратов FBO", True),
                        ([sys.executable, "-m", "src.main", "--mode", "report_products"], "Обновление отчёта товаров", False),
                        ([sys.executable, "-m", "src.main", "--mode", "report_warehouse_stock"], "Обновление отчёта остатков складов", False),
                        ([sys.executable, "-m", "src.main", "--mode", "analytics_stocks"], "Обновление аналитики остатков", False),
                        ([sys.executable, "-m", "src.main", "--mode", "analytics_data", "--days-back", str(days_back)], "Обновление дневной SKU-статистики", False),
                        ([sys.executable, "-m", "src.main", "--mode", "analytics_product_queries", "--days-back", str(days_back)], "Обновление поисковых запросов по SKU", False),
                        ([sys.executable, "-m", "src.main", "--mode", "campaigns"], "Обновление рекламных кампаний и статистики", False),
                        ([sys.executable, "-m", "src.main", "--mode", "fbs_warehouse_stocks"], "Обновление FBS остатков", False),
                        ([sys.executable, "-m", "src.main", "--mode", "analytics_turnover", "--days-back", str(days_back)], "Обновление оборачиваемости", False),
                        ([sys.executable, "-m", "src.main", "--mode", "average_delivery_time"], "Обновление времени доставки", False),
                        ([sys.executable, "-m", "src.main", "--mode", "realization_v2", "--days-back", str(days_back)], "Обновление реализации", False),
                        ([sys.executable, "-m", "src.main", "--mode", "report_compensation"], "Обновление компенсаций", False),
                        ([sys.executable, "-m", "src.main", "--mode", "transactions", "--days-back", str(days_back)], "Обновление транзакций", False),
                        ([sys.executable, "-m", "src.main", "--mode", "normalize_finance"], "Нормализация финансов", False),
                        ([sys.executable, "-m", "src.main", "--mode", "promo"], "Обновление акций", False),
                        ([sys.executable, "-m", "src.main", "--mode", "reviews"], "Обновление отзывов", True),
                    ]

                    steps = build_finance_sync_steps(days_back)
                    steps_count = len(steps)
                    for i, (cmd, stage, continue_on_error) in enumerate(steps, start=1):
                        progress = min(95, int((i / max(steps_count, 1)) * 95))
                        step_started_at = datetime.now().isoformat()
                        step_status = "OK"
                        step_error = ""
                        try:
                            step_result = await run_sync_step(cmd, stage, progress, continue_on_error=continue_on_error)
                            if continue_on_error and isinstance(step_result, dict) and not step_result.get("ok", True):
                                step_output = str(step_result.get("error", "")).strip()
                                lower_output = step_output.lower()
                                if (
                                    "fresh_recent_sync" in lower_output
                                    or "'skipped': 1" in lower_output
                                    or '"skipped": 1' in lower_output
                                    or ("sync result:" in lower_output and "skipped" in lower_output and "error" not in lower_output)
                                ):
                                    step_status = "SKIPPED"
                                else:
                                    step_status = "ERROR"
                                    step_error = step_output[:500]
                        except Exception as step_exc:
                            step_status = "ERROR"
                            step_error = str(step_exc)[:500]
                            sync_status.setdefault("step_results", []).append({
                                "stage": stage,
                                "status": step_status,
                                "details": "Ошибка",
                                "error": step_error,
                                "started_at": step_started_at,
                                "at": datetime.now().isoformat(),
                            })
                            raise
                        else:
                            step_details = "Загружено"
                            if step_status == "SKIPPED":
                                step_details = "Пропущено"
                            elif step_status == "ERROR":
                                step_details = "Ошибка"
                            sync_status.setdefault("step_results", []).append({
                                "stage": stage,
                                "status": step_status,
                                "details": step_details,
                                "error": step_error,
                                "started_at": step_started_at,
                                "at": datetime.now().isoformat(),
                            })
                else:
                    # Фолбэк для нестандартных профилей.
                    await run_sync_step(
                        [sys.executable, "-m", "src.main", "--mode", "full"],
                        "Полная синхронизация",
                        80,
                    )

                update_sync_status(100, "Завершено")
                sync_status["completed_at"] = datetime.now().isoformat()
                
            except Exception as e:
                sync_status["error"] = str(e)
                update_sync_status(sync_status.get("progress", 0), f"Ошибка: {str(e)[:50]}")
            finally:
                sync_status["is_running"] = False
                _release_sync_lock()
        
        # Запускаем в фоне
        asyncio.create_task(do_sync())
        
        return web.json_response({
            "status": "started",
            "message": "Синхронизация запущена",
            "profile": profile,
            "days_back": days_back,
            "progress": 0,
            "stage": "Инициализация"
        })
    except Exception as e:
        if lock_acquired:
            _release_sync_lock()
        reset_sync_status()
        return web.json_response({
            "status": "error",
            "error": str(e)
        }, status=500)


async def get_sync_status(request: web.Request) -> web.Response:
    """Возвращает текущий статус синхронизации."""
    return web.json_response(build_sync_status_response())


async def get_sync_reports(request: web.Request) -> web.Response:
    """Returns Ozon report freshness registry for Settings > Sync."""
    payload = await build_ozon_sync_reports_payload(request.app["pool"])
    return web.json_response(payload)
