"""Database source validation helpers."""
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional

from sqlalchemy.engine import make_url


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}
LOCAL_SOURCE_MODES = {"local_snapshot", "dev_fixture"}
SERVER_SOURCE_MODE = "server"


class DatabaseSourceError(RuntimeError):
    """Raised when DATABASE_URL does not match the configured source policy."""


@dataclass(frozen=True)
class DatabaseSourceInfo:
    """Normalized database location metadata."""

    kind: str
    host: Optional[str]
    database: Optional[str]
    is_local: bool


def classify_database_url(database_url: str) -> DatabaseSourceInfo:
    """Classify a database URL as local or remote without connecting to it."""
    url = make_url(database_url)
    driver = url.get_backend_name()
    host = url.host
    database = url.database

    if driver == "sqlite":
        is_local = True
        kind = "sqlite"
    else:
        normalized_host = (host or "").strip().lower()
        is_local = normalized_host in LOCAL_HOSTS
        kind = driver

    return DatabaseSourceInfo(
        kind=kind,
        host=host,
        database=_display_database_path(database),
        is_local=is_local,
    )


def validate_database_source(
    database_url: str,
    *,
    source_mode: str,
    allow_local_database: bool,
    expected_host: Optional[str] = None,
) -> DatabaseSourceInfo:
    """Validate DATABASE_URL against the configured source mode."""
    normalized_mode = (source_mode or "").strip().lower()
    info = classify_database_url(database_url)

    if normalized_mode not in {SERVER_SOURCE_MODE, *LOCAL_SOURCE_MODES}:
        raise DatabaseSourceError(
            "DB_SOURCE_MODE must be one of: server, local_snapshot, dev_fixture"
        )

    if normalized_mode == SERVER_SOURCE_MODE:
        if expected_host and (info.host or "").strip().lower() != expected_host.strip().lower():
            raise DatabaseSourceError(
                f"DATABASE_URL host '{info.host}' does not match expected host '{expected_host}'."
            )
        if info.is_local and not allow_local_database:
            host_hint = info.host or info.kind
            raise DatabaseSourceError(
                "Refusing to use a localhost/local database while DB_SOURCE_MODE=server "
                f"(detected {host_hint}). Set DB_SOURCE_MODE=dev_fixture for test data, "
                "DB_SOURCE_MODE=local_snapshot for an explicit restored snapshot, or "
                "ALLOW_LOCAL_DATABASE=true for a one-off override."
            )

    return info


def _display_database_path(database: Optional[str]) -> Optional[str]:
    if not database:
        return database
    if "\\" in database:
        return database
    return str(PurePosixPath(database))
