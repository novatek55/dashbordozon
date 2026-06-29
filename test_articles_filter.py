import asyncio
import json
from types import SimpleNamespace

from src.dashboard.routes.orders import get_articles


class _FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *params):
        self.calls.append((sql, params))
        return [
            {"offer_id": "124 раздвижной"},
            {"offer_id": "124_1"},
        ]


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def test_articles_current_products_source_filters_archived_and_invisible_products():
    conn = _FakeConnection()
    request = SimpleNamespace(
        query={"source": "current_products", "query": "124", "limit": "20"},
        app={"pool": _FakePool(conn)},
    )

    response = asyncio.run(get_articles(request))

    assert response.status == 200
    payload = json.loads(response.text)
    assert payload["items"] == ["124 раздвижной", "124_1"]

    sql, params = conn.calls[0]
    sql_lower = " ".join(sql.lower().split())
    assert "from products" in sql_lower
    assert "coalesce(is_visible, true) is true" in sql_lower
    assert "archived" in sql_lower
    assert "autoarchived" in sql_lower
    assert "deleted" in sql_lower
    assert params == ("%124%",)
