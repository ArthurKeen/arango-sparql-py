"""Drift checking: a schema past its TTL is re-analyzed only if the database changed.

PRD §6.3.3's two-fingerprint policy (``routes/schema.py::_revalidate_or_rebuild``,
the slow path of arango-cypher-py's ``get_mapping``). Before it, every TTL
expiry (hourly) re-ran the full analysis in the background — ~7 minutes of
analyzer load on prod.demo's ``IAM`` for a database that had not changed.

Each case seeds an analysis stored two hours ago (past the 1 h TTL) with known
live fingerprints, sets what the database reports now, and drives
``/schema/introspect``: the stale schema is always served at once, and the
background warm (captured, then run) does only the work the drift warrants.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arango_sparql.schema.cache import L2_COLLECTION_NAME
from arango_sparql.service import schema_warm
from arango_sparql.service.routes import schema as schema_route_mod
from arango_sparql.translate.mapping import MappingBundle
from tests.service.test_schema_persistent_cache import _StoreClient, _StoreDb
from tests.test_service_schema_routes import _bundle_pg

# client, stub_acquire and the session/cache isolation come from
# tests/service/conftest.py.

_TWO_HOURS_AGO = datetime.now(UTC) - timedelta(hours=2)


class _Threads:
    def __init__(self) -> None:
        self.pending: list[Callable[[], None]] = []

    def start(self, target: Callable[[], None], name: str) -> None:
        self.pending.append(target)

    def finish_all(self) -> None:
        while self.pending:
            self.pending.pop(0)()


@pytest.fixture
def world(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, stub_acquire: dict[str, Any]
) -> dict[str, Any]:
    """A connected session whose database holds an analysis stored 2 h ago."""
    import arango_sparql.service as svc

    monkeypatch.setattr(svc, "ArangoClient", _StoreClient)
    monkeypatch.setenv("ARANGO_SPARQL_CONNECT_ALLOWED_HOSTS", "localhost,127.0.0.1")
    live = {"shape": "shape-A", "counts": "counts-A"}
    monkeypatch.setattr(schema_route_mod, "db_shape_fingerprint", lambda _db: live["shape"])
    monkeypatch.setattr(schema_route_mod, "db_counts_fingerprint", lambda _db: live["counts"])
    recounts: list[MappingBundle] = []

    def fake_refresh_statistics(_db: Any, bundle: MappingBundle) -> MappingBundle:
        recounts.append(bundle)
        return MappingBundle(
            conceptual_schema=bundle.conceptual_schema,
            physical_mapping=bundle.physical_mapping,
            metadata={**bundle.metadata, "statistics": {"recounted": True}},
            owl_turtle=bundle.owl_turtle,
            source=bundle.source,
        )

    monkeypatch.setattr(schema_route_mod, "refresh_statistics", fake_refresh_statistics)
    threads = _Threads()
    monkeypatch.setattr(schema_warm, "_start_thread", threads.start)

    resp = client.post(
        "/connect",
        json={"url": "http://localhost:8529", "database": "test_db", "username": "root", "password": "<pw>"},
    )
    token = resp.json()["token"]
    db: _StoreDb = svc._sessions[token].db
    stored = _bundle_pg()
    schema_route_mod._resolve_schema_cache().put(
        "test_db", stored, db=db, db_shape="shape-A", db_counts="counts-A", now=_TWO_HOURS_AGO
    )
    return {
        "headers": {"X-Arango-Session": token},
        "db": db,
        "live": live,
        "threads": threads,
        "analyses": stub_acquire["calls"],
        "recounts": recounts,
        "stored": stored,
    }


def _introspect(client: TestClient, world: dict[str, Any], **params: Any) -> dict[str, Any]:
    return client.get("/schema/introspect", headers=world["headers"], params=params).json()


def _doc(world: dict[str, Any]) -> dict[str, Any]:
    return world["db"].collection(L2_COLLECTION_NAME).docs["mapping"]


def _entry() -> Any:
    return schema_route_mod._resolve_schema_cache().peek("test_db")


def test_an_unchanged_database_is_not_reanalyzed(client: TestClient, world: dict[str, Any]) -> None:
    body = _introspect(client, world)
    assert body["status"] == "ready" and body["warming"] is True  # stale schema served at once

    world["threads"].finish_all()

    assert world["analyses"] == [] and world["recounts"] == []
    entry, expired = _entry()
    assert entry.bundle is world["stored"] and not expired  # kept, TTL restarted
    assert datetime.fromisoformat(_doc(world)["updated_at"]) > _TWO_HOURS_AGO  # stored copy re-stamped


def test_new_rows_recount_statistics_without_reanalyzing(client: TestClient, world: dict[str, Any]) -> None:
    world["live"]["counts"] = "counts-B"

    _introspect(client, world)
    world["threads"].finish_all()

    assert world["analyses"] == []
    assert world["recounts"] == [world["stored"]]
    entry, expired = _entry()
    assert entry.bundle.metadata["statistics"] == {"recounted": True} and not expired
    assert entry.bundle.physical_mapping == world["stored"].physical_mapping  # mapping kept
    assert _doc(world)["full_fingerprint"] == "counts-B"
    assert _doc(world)["bundle"]["metadata"]["statistics"] == {"recounted": True}


def test_a_structural_change_reanalyzes(client: TestClient, world: dict[str, Any]) -> None:
    world["live"]["shape"], world["live"]["counts"] = "shape-B", "counts-B"

    _introspect(client, world)
    world["threads"].finish_all()

    assert len(world["analyses"]) == 1
    assert (_doc(world)["shape_fingerprint"], _doc(world)["full_fingerprint"]) == ("shape-B", "counts-B")


def test_a_schema_stored_without_fingerprints_is_reanalyzed(
    client: TestClient, world: dict[str, Any]
) -> None:
    """Nothing to compare (e.g. analyzed while the analyzer was missing)."""
    schema_route_mod._resolve_schema_cache().put(
        "test_db", world["stored"], db=world["db"], db_shape=None, db_counts=None, now=_TWO_HOURS_AGO
    )

    _introspect(client, world)
    world["threads"].finish_all()

    assert len(world["analyses"]) == 1


def test_a_fresh_schema_triggers_no_check(client: TestClient, world: dict[str, Any]) -> None:
    schema_route_mod._resolve_schema_cache().put(
        "test_db", world["stored"], db=world["db"], db_shape="shape-A", db_counts="counts-A"
    )

    body = _introspect(client, world)

    assert body["warming"] is False and world["threads"].pending == []


def test_refresh_during_a_check_still_reanalyzes(client: TestClient, world: dict[str, Any]) -> None:
    """ "Refresh schema" must not be answered by a check that found nothing."""
    _introspect(client, world)  # starts the drift check (captured, not yet run)
    assert schema_warm.is_warming("test_db")

    body = _introspect(client, world, force=True)
    assert body["warming"] is True
    world["threads"].finish_all()

    assert len(world["analyses"]) == 1  # the check found no drift; the refresh re-analyzed anyway
    assert not schema_warm.is_warming("test_db")


def test_a_check_in_flight_absorbs_repeated_ttl_polls(client: TestClient, world: dict[str, Any]) -> None:
    for _ in range(3):
        _introspect(client, world)
    assert len(world["threads"].pending) == 1
    world["threads"].finish_all()
    assert world["analyses"] == []
