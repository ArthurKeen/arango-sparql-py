"""The stored schema survives a restart (PRD §6.3.3 persistent tier).

Before the persistent tier, every redeploy started with an empty cache, so
the Workbench sat on "Analyzing schema…" for the full analysis again (~7 min
for ``IAM`` on prod.demo). These tests drive the real schema routes over a
session whose database also offers python-arango's document API
(``tests/helpers/fake_arango_store.py``). A "restart" drops the in-process
cache (``_reset_cache``) and keeps the database — exactly what a redeploy does.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

import arango_sparql.service as svc
from arango_sparql.schema.cache import L2_COLLECTION_NAME
from arango_sparql.schema.persistent_cache import current_producer
from arango_sparql.service import schema_warm
from arango_sparql.service.routes import schema as schema_route_mod
from tests.helpers.fake_arango_store import FakeDatabase
from tests.test_service_schema_routes import _FakeAql, _FakeArangoClient

# client, stub_acquire and the session/cache isolation come from
# tests/service/conftest.py.


class _StoreDb(FakeDatabase):
    """The route suite's fake database, plus the document API."""

    def __init__(self, name: str, *, read_only: bool = False) -> None:
        super().__init__(name, read_only=read_only)
        self.aql = _FakeAql({})

    def version(self) -> str:
        return "3.12.0"


class _StoreClient(_FakeArangoClient):
    read_only = False

    def db(self, name: str, username: str | None = None, password: str | None = None) -> _StoreDb:  # type: ignore[override]
        if name not in self._dbs:
            self._dbs[name] = _StoreDb(name, read_only=self.read_only)  # type: ignore[assignment]
        return self._dbs[name]  # type: ignore[return-value]


class _Threads:
    """Background warms captured instead of started (see test_schema_warm.py)."""

    def __init__(self) -> None:
        self.pending: list[Callable[[], None]] = []

    def start(self, target: Callable[[], None], name: str) -> None:
        self.pending.append(target)

    def finish_all(self) -> None:
        while self.pending:
            self.pending.pop(0)()


@pytest.fixture
def connect(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., tuple[dict[str, str], _StoreDb]]:
    monkeypatch.setattr(svc, "ArangoClient", _StoreClient)
    monkeypatch.setenv("ARANGO_SPARQL_CONNECT_ALLOWED_HOSTS", "localhost,127.0.0.1")
    monkeypatch.setattr(schema_route_mod, "db_shape_fingerprint", lambda _db: "shape-1")
    monkeypatch.setattr(schema_route_mod, "db_counts_fingerprint", lambda _db: "counts-1")

    def _connect(*, read_only: bool = False) -> tuple[dict[str, str], _StoreDb]:
        monkeypatch.setattr(_StoreClient, "read_only", read_only)
        resp = client.post(
            "/connect",
            json={
                "url": "http://localhost:8529",
                "database": "test_db",
                "username": "root",
                "password": "<pw>",
            },
        )
        assert resp.status_code == 200, resp.text
        token = resp.json()["token"]
        db = svc._sessions[token].db
        return {"X-Arango-Session": token}, db

    return _connect


def _stored(db: _StoreDb) -> dict[str, Any] | None:
    if not db.has_collection(L2_COLLECTION_NAME):
        return None
    return db.collection(L2_COLLECTION_NAME).docs.get("mapping")


def _restart() -> None:
    schema_route_mod._reset_cache()


def test_the_analysis_is_stored_with_its_fingerprints(
    client: TestClient, connect: Callable[..., Any], stub_acquire: dict[str, Any]
) -> None:
    headers, db = connect()
    client.get("/schema/introspect", headers=headers)  # cold: the (inline) warm analyzes

    doc = _stored(db)
    assert doc is not None
    assert (doc["shape_fingerprint"], doc["full_fingerprint"]) == ("shape-1", "counts-1")
    assert doc["producer"] == current_producer()


def test_a_restart_serves_the_stored_schema_without_analyzing(
    client: TestClient, connect: Callable[..., Any], stub_acquire: dict[str, Any]
) -> None:
    headers, _db = connect()
    client.get("/schema/introspect", headers=headers)
    assert len(stub_acquire["calls"]) == 1

    _restart()
    body = client.get("/schema/introspect", headers=headers).json()

    assert body["status"] == "ready"
    assert body["cache_hit"] is True and body["warming"] is False
    assert body["mapping"]["physicalMapping"]["entities"]  # the stored schema, not an empty pending payload
    assert len(stub_acquire["calls"]) == 1  # no second analysis


def test_a_schema_stored_by_other_code_is_served_then_replaced(
    client: TestClient,
    connect: Callable[..., Any],
    stub_acquire: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release that changes how schemas are built must not keep serving the
    previous release's stored analysis just because the database is unchanged."""
    headers, db = connect()
    client.get("/schema/introspect", headers=headers)
    _stored(db)["producer"] = "0123456789abcdef/analyzer=0.13.0"  # type: ignore[index]
    threads = _Threads()
    monkeypatch.setattr(schema_warm, "_start_thread", threads.start)

    _restart()
    body = client.get("/schema/introspect", headers=headers).json()

    assert body["status"] == "ready" and body["warming"] is True  # served while it is replaced
    assert len(stub_acquire["calls"]) == 1 and len(threads.pending) == 1
    threads.finish_all()
    assert len(stub_acquire["calls"]) == 2
    assert _stored(db)["producer"] == current_producer()  # type: ignore[index]


def test_invalidate_cache_drops_the_stored_copy(
    client: TestClient, connect: Callable[..., Any], stub_acquire: dict[str, Any]
) -> None:
    headers, db = connect()
    client.get("/schema/introspect", headers=headers)

    body = client.post("/schema/invalidate-cache", headers=headers).json()

    assert body == {"invalidated": True, "db_name": "test_db", "persistent_dropped": True}
    assert _stored(db) is None
    _restart()
    client.get("/schema/introspect", headers=headers)
    assert len(stub_acquire["calls"]) == 2  # nothing stored → analyzed again


def test_invalidate_after_a_restart_still_drops_the_stored_copy(
    client: TestClient, connect: Callable[..., Any], stub_acquire: dict[str, Any]
) -> None:
    headers, db = connect()
    client.get("/schema/introspect", headers=headers)
    _restart()  # memory is empty; only the stored copy exists

    body = client.post("/schema/invalidate-cache", headers=headers).json()

    assert body["invalidated"] is True and body["persistent_dropped"] is True
    assert _stored(db) is None


def test_a_user_who_cannot_write_still_gets_the_schema(
    client: TestClient, connect: Callable[..., Any], stub_acquire: dict[str, Any]
) -> None:
    headers, db = connect(read_only=True)
    client.get("/schema/introspect", headers=headers)

    body = client.get("/schema/introspect", headers=headers).json()

    assert body["status"] == "ready" and body["cache_hit"] is True
    assert _stored(db) is None
