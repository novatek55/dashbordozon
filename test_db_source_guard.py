import pytest

from src.db_source_guard import DatabaseSourceError, classify_database_url, validate_database_source


def test_classifies_sqlite_file_as_local() -> None:
    info = classify_database_url("sqlite+aiosqlite:///./ozon_analytics.db")

    assert info.kind == "sqlite"
    assert info.is_local is True
    assert info.host is None


def test_rejects_sqlite_in_server_mode_without_override() -> None:
    with pytest.raises(DatabaseSourceError, match="DB_SOURCE_MODE=server"):
        validate_database_source(
            "sqlite+aiosqlite:///./ozon_analytics.db",
            source_mode="server",
            allow_local_database=False,
        )


def test_rejects_localhost_postgres_in_server_mode_without_override() -> None:
    with pytest.raises(DatabaseSourceError, match="localhost"):
        validate_database_source(
            "postgresql+asyncpg://user:pass@127.0.0.1:5432/ozon_analytics",
            source_mode="server",
            allow_local_database=False,
        )


def test_allows_localhost_when_explicitly_allowed() -> None:
    info = validate_database_source(
        "postgresql+asyncpg://user:pass@127.0.0.1:5432/ozon_analytics",
        source_mode="server",
        allow_local_database=True,
    )

    assert info.is_local is True


def test_rejects_unexpected_host_when_expected_host_is_set() -> None:
    with pytest.raises(DatabaseSourceError, match="expected host"):
        validate_database_source(
            "postgresql+asyncpg://user:pass@db.example.com:5432/ozon_analytics",
            source_mode="server",
            allow_local_database=False,
            expected_host="prod-db.example.com",
        )


def test_allows_remote_postgres_in_server_mode() -> None:
    info = validate_database_source(
        "postgresql+asyncpg://user:pass@prod-db.example.com:5432/ozon_analytics",
        source_mode="server",
        allow_local_database=False,
        expected_host="prod-db.example.com",
    )

    assert info.kind == "postgresql"
    assert info.host == "prod-db.example.com"
    assert info.is_local is False
