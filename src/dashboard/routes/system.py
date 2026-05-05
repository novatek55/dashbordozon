"""Dashboard routes/system.py handlers."""
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime

import asyncpg
from aiohttp import web

from src.config import settings
from src.dashboard.constants import BASE_DIR, MSK
from src.dashboard import state
from src.dashboard.state import sync_status, SYNC_LOCK_PATH
from src.dashboard.helpers import to_asyncpg_dsn
from src.dashboard.routes.finance import ensure_finance_report_tables
from src.dashboard.routes.reviews import ensure_reviews_report_tables


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


async def close_pool(app: web.Application) -> None:
    pool: asyncpg.Pool = app["pool"]
    await pool.close()


def reset_sync_status():
    """Сбрасывает статус синхронизации."""
    sync_status["is_running"] = False
    sync_status["progress"] = 0
    sync_status["stage"] = ""
    sync_status["stages"] = []
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


async def run_sync_step(cmd: list, stage: str, progress: int, continue_on_error: bool = False):
    """Запускает шаг синхронизации и обновляет прогресс."""
    update_sync_status(progress, stage)
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
            for marker, substage, substage_progress in substage_markers:
                if marker in text and last_substage != substage:
                    update_sync_status(min(95, substage_progress), substage)
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
            return error_msg
        raise Exception(f"{stage} failed: {error_msg}")

    return "".join(stdout_chunks)


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
                if profile in {"finance", "all", "all_reports"}:
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

                    steps_count = len(steps)
                    for i, (cmd, stage, continue_on_error) in enumerate(steps, start=1):
                        progress = min(95, int((i / max(steps_count, 1)) * 95))
                        await run_sync_step(cmd, stage, progress, continue_on_error=continue_on_error)
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
    return web.json_response({
        "is_running": sync_status["is_running"],
        "progress": sync_status["progress"],
        "stage": sync_status["stage"],
        "stages": sync_status.get("stages", []),
        "started_at": sync_status["started_at"],
        "completed_at": sync_status["completed_at"],
        "error": sync_status["error"]
    })

