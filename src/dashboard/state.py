"""Dashboard mutable state — global variables shared across route modules."""
import asyncio
from typing import Any, Dict, List, Optional

from src.dashboard.constants import BASE_DIR

SUPPLY_ACCEPTANCE_CACHE: Dict[str, Dict[str, Any]] = {}

# Chrome CDP state (for auth button in dashboard)
_CHROME_STATE: Dict[str, Any] = {"status": "idle", "message": ""}
_CHROME_TASK: Optional[asyncio.Task] = None

SUPPLY_PLAN_UPLOADED_TARGETS: Dict[str, Dict[str, int]] = {}
SUPPLY_PLAN_UPLOADED_META: Dict[str, Any] = {}

_CROSSDOCK_TARIFFS_CACHE_PATH: Optional[str] = None
_CROSSDOCK_TARIFFS_CACHE_MTIME: Optional[float] = None
_CROSSDOCK_TARIFFS_CACHE_ROWS: List[Dict[str, Any]] = []
_LEGACY_PALLETIZATION_CACHE: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None

# Sync status
sync_status: Dict[str, Any] = {
    "is_running": False,
    "progress": 0,
    "stage": "",
    "stages": [],
    "current_detail": "",
    "current_log": [],
    "started_at": None,
    "completed_at": None,
    "error": None,
}
SYNC_LOCK_PATH = BASE_DIR / "logs" / "dashboard_sync.lock"
