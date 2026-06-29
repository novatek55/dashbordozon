"""Print whether the configured DATABASE_URL matches the DB source policy."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import settings
from src.db_source_guard import DatabaseSourceError, validate_database_source


def main() -> int:
    try:
        info = validate_database_source(
            settings.database_url,
            source_mode=settings.db_source_mode,
            allow_local_database=settings.allow_local_database,
            expected_host=settings.expected_db_host,
        )
    except DatabaseSourceError as exc:
        print("DB source: REJECTED")
        print(f"Mode: {settings.db_source_mode}")
        print(f"Reason: {exc}")
        return 1

    print("DB source: OK")
    print(f"Mode: {settings.db_source_mode}")
    print(f"Kind: {info.kind}")
    print(f"Host: {info.host or '<local-file>'}")
    print(f"Database: {info.database or '<unknown>'}")
    print(f"Local: {str(info.is_local).lower()}")
    if info.is_local and settings.db_source_mode == "server":
        print("Note: local database is accepted only because ALLOW_LOCAL_DATABASE=true.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
