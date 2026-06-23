"""End-to-end tests for the seven schema HTTP routes (PRD §6.4).

Exercises every route via :class:`fastapi.testclient.TestClient`
against the real ``app`` and a fake python-arango stack — no live
database, no real schema_analyzer round-trip. Coverage goals:

* Auth model — every route except ``/schema/summary`` requires a
  session; ``/schema/summary`` works without one.
* Strategy validation — invalid ``strategy`` query string → 422
  with ``E_SCHEMA_STRATEGY_INVALID``.
* Cache behavior — second introspect call returns ``cache_hit=True``
  unless ``force=true``.
* RPT enrichment — bundle with RPT collections surfaces them in the
  ``summary.rpt_collections`` block.
* Drift detection — ``/schema/status`` reports ``no_cache`` →
  ``unchanged`` → ``stats_only`` → ``shape_changed`` as the live
  fingerprints diverge from the cached bundle.
* Invalidate — drops the cached entry; second status call reports
  ``no_cache``.
* Force-reacquire — bypasses cache; returns 503 when both opt-out
  env vars are set to ``false``.
* OpenAPI — every route appears in the generated spec.

The schema-acquire path is monkeypatched at the
:func:`arango_sparql.schema.acquire.acquire_mapping_bundle` level so
each test feeds a deterministic :class:`MappingBundle` to the route
without touching the analyzer or the heuristic detector.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

import arango_sparql.schema.acquire as acquire_mod
import arango_sparql.service as svc
from arango_sparql.schema.cache import SchemaCache
from arango_sparql.schema.fingerprint import compute_bundle_fingerprint
from arango_sparql.service import _sessions, app
from arango_sparql.service.routes import schema as schema_route_mod
from arango_sparql.translate.mapping import MappingBundle, MappingSource

# ---------------------------------------------------------------------------
# Bundle / DB fixtures
# ---------------------------------------------------------------------------


def _bundle_pg(label: str = "Person") -> MappingBundle:
    """A small PG-shaped bundle. Used as the default analyzer
    payload for happy-path tests.
    """

    return MappingBundle(
        conceptual_schema={
            "entities": [
                {"name": label, "labels": [label], "properties": []}
            ],
            "relationships": [],
        },
        physical_mapping={
            "entities": {
                label: {
                    "style": "COLLECTION",
                    "collectionName": label.lower(),
                    "properties": {
                        "name": {"field": "name", "type": "string"},
                        "age": {"field": "age", "type": "number"},
                    },
                }
            },
            "relationships": {},
        },
        metadata={
            "source": "test_fixture",
            "acquisitionTimestamp": "2026-05-01T12:00:00+00:00",
            "warnings": [],
        },
        source=MappingSource(kind="analyzer", notes="test fixture"),
    )


def _bundle_with_rpt() -> MappingBundle:
    return MappingBundle(
        conceptual_schema={"entities": [], "relationships": []},
        physical_mapping={
            "entities": {
                "_triples": {
                    "style": "RPT",
                    "triplesCollection": "_triples",
                    "subjectColumn": "subject_uri",
                    "predicateColumn": "predicate",
                    "objectUriColumn": "object_uri",
                    "objectValueColumn": "object_value",
                    "rptCoverage": 0.95,
                }
            },
            "relationships": {},
        },
        metadata={
            "source": "test_fixture_rpt",
            "warnings": [],
            "detectedPatterns": ["rpt"],
        },
        source=MappingSource(kind="analyzer", notes="rpt fixture"),
    )


def _bundle_with_stats() -> MappingBundle:
    bundle = _bundle_pg()
    new_meta = dict(bundle.metadata)
    new_meta["statistics"] = {
        "collections": {"person": {"count": 42, "is_edge": False}},
        "entities": {"Person": {"estimated_count": 42}},
        "relationships": {},
    }
    return MappingBundle(
        conceptual_schema=bundle.conceptual_schema,
        physical_mapping=bundle.physical_mapping,
        metadata=new_meta,
        owl_turtle=bundle.owl_turtle,
        source=bundle.source,
    )


# ---------------------------------------------------------------------------
# Fake python-arango (mirror of test_service_sparql_routes.py shape)
# ---------------------------------------------------------------------------


class _FakeAql:
    def __init__(self, samples: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._samples = samples or {}
        self.queries_seen: list[tuple[str, dict[str, Any]]] = []

    def execute(
        self, query: str, bind_vars: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self.queries_seen.append((query, dict(bind_vars or {})))
        if not bind_vars:
            return []
        name = bind_vars.get("@col")
        n = int(bind_vars.get("n", 0) or 0)
        return list(self._samples.get(name, []))[:n]


class _FakeDb:
    def __init__(
        self,
        name: str = "test_db",
        samples: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.name = name
        self.aql = _FakeAql(samples)
        self.collections_seen = 0

    def collections(self) -> list[dict[str, Any]]:
        self.collections_seen += 1
        return [{"name": "Person", "system": False, "type": "document"}]

    def version(self) -> str:
        return "3.12.0"


class _FakeArangoClient:
    instances: list[_FakeArangoClient] = []

    def __init__(self, hosts: str = "") -> None:
        self.hosts = hosts
        self._dbs: dict[str, _FakeDb] = {}
        _FakeArangoClient.instances.append(self)

    def db(
        self,
        name: str,
        username: str | None = None,
        password: str | None = None,
    ) -> _FakeDb:
        if name not in self._dbs:
            samples = {
                "Person": [
                    {"_key": str(i), "name": f"p{i}", "age": 20 + i}
                    for i in range(10)
                ],
            }
            self._dbs[name] = _FakeDb(name=name, samples=samples)
        return self._dbs[name]

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch: pytest.MonkeyPatch):
    """Clear the session table and the schema cache around every
    test, and pin the env vars to their defaults so a leaked env
    var from another test or the surrounding shell cannot flap a
    503.
    """

    _sessions.clear()
    schema_route_mod._reset_cache()
    monkeypatch.delenv("SCHEMA_ANALYZER_REQUIRED", raising=False)
    monkeypatch.delenv("ARANGO_SPARQL_ALLOW_HEURISTIC", raising=False)
    yield
    for s in list(_sessions.values()):
        try:
            s.client.close()
        except Exception:
            pass
    _sessions.clear()
    schema_route_mod._reset_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def fake_arango(monkeypatch: pytest.MonkeyPatch):
    _FakeArangoClient.instances.clear()
    monkeypatch.setattr(svc, "ArangoClient", _FakeArangoClient)
    monkeypatch.setenv(
        "ARANGO_SPARQL_CONNECT_ALLOWED_HOSTS", "localhost,127.0.0.1"
    )
    return _FakeArangoClient


@pytest.fixture
def session_token(client: TestClient, fake_arango: type) -> str:
    resp = client.post(
        "/connect",
        json={
            "url": "http://localhost:8529",
            "database": "test_db",
            "username": "root",
            "password": "<test-stub-pw>",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


@pytest.fixture
def stub_acquire(monkeypatch: pytest.MonkeyPatch):
    """Replace :func:`acquire_mapping_bundle` with a controllable
    stub so route tests do not hit the analyzer or the heuristic.

    Returns a tuple ``(set_bundle, get_calls)`` so a test can pin
    the bundle the next call returns and assert on what arguments
    the route forwarded.
    """

    state: dict[str, Any] = {
        "bundle": _bundle_pg(),
        "calls": [],
        "raise_exc": None,
    }

    def fake_acquire(
        db: Any,
        *,
        include_owl: bool = False,
        strategy: str = "auto",
        force_refresh: bool = False,
        graph_name: str | None = None,
        now: Any = None,
    ) -> MappingBundle:
        state["calls"].append(
            {
                "db_name": getattr(db, "name", None),
                "include_owl": include_owl,
                "strategy": strategy,
                "force_refresh": force_refresh,
                "graph_name": graph_name,
            }
        )
        if state["raise_exc"] is not None:
            raise state["raise_exc"]
        return state["bundle"]

    monkeypatch.setattr(
        schema_route_mod, "acquire_mapping_bundle", fake_acquire
    )
    return state


# ---------------------------------------------------------------------------
# /schema/introspect
# ---------------------------------------------------------------------------


def test_introspect_requires_session(client: TestClient) -> None:
    resp = client.get("/schema/introspect")
    assert resp.status_code == 401


def test_introspect_happy_path(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    resp = client.get(
        "/schema/introspect",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cache_hit"] is False
    assert body["mapping"]["physicalMapping"]["entities"]["Person"]["style"] == "COLLECTION"
    assert body["summary"]["entity_count"] == 1
    assert body["summary"]["entities"][0]["label"] == "Person"
    assert body["source"]["kind"] == "analyzer"
    assert body["elapsed_ms"] >= 0


def test_introspect_second_call_is_cache_hit(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    headers = {"X-Arango-Session": session_token}
    first = client.get("/schema/introspect", headers=headers)
    assert first.status_code == 200
    assert first.json()["cache_hit"] is False

    second = client.get("/schema/introspect", headers=headers)
    assert second.status_code == 200
    assert second.json()["cache_hit"] is True
    # Still only one acquire call — second was served from cache.
    assert len(stub_acquire["calls"]) == 1


def test_introspect_force_bypasses_cache(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    headers = {"X-Arango-Session": session_token}
    client.get("/schema/introspect", headers=headers)
    resp = client.get(
        "/schema/introspect?force=true", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["cache_hit"] is False
    assert len(stub_acquire["calls"]) == 2
    assert stub_acquire["calls"][1]["force_refresh"] is True


def test_introspect_forwards_session_graph_scope(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    """A bound named-graph scope reaches acquisition and gets its own
    cache slot (so it doesn't collide with the unscoped bundle)."""
    _sessions[session_token].graph_name = "social"
    headers = {"X-Arango-Session": session_token}
    resp = client.get("/schema/introspect", headers=headers)
    assert resp.status_code == 200
    assert stub_acquire["calls"][-1]["graph_name"] == "social"

    # An unscoped introspect on the same db must NOT be served the scoped
    # entry (distinct cache key), so it triggers a fresh acquire.
    _sessions[session_token].graph_name = None
    resp2 = client.get("/schema/introspect", headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["cache_hit"] is False
    assert stub_acquire["calls"][-1]["graph_name"] is None


def test_introspect_strategy_invalid_returns_422(
    client: TestClient, session_token: str
) -> None:
    resp = client.get(
        "/schema/introspect?strategy=bogus",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "E_SCHEMA_STRATEGY_INVALID"


@pytest.mark.parametrize("strategy", ["auto", "analyzer", "heuristic"])
def test_introspect_strategy_forwarded_to_acquire(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
    strategy: str,
) -> None:
    resp = client.get(
        f"/schema/introspect?strategy={strategy}",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200
    assert stub_acquire["calls"][-1]["strategy"] == strategy


def test_introspect_analyzer_missing_returns_503(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    stub_acquire["raise_exc"] = acquire_mod.AnalyzerNotInstalledError()
    resp = client.get(
        "/schema/introspect?strategy=analyzer",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["code"] == "E_ANALYZER_NOT_INSTALLED"
    assert "install_hint" in detail
    assert "arangodb-schema-analyzer" in detail["install_hint"]


def test_introspect_rpt_collections_appear_in_summary(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    stub_acquire["bundle"] = _bundle_with_rpt()
    resp = client.get(
        "/schema/introspect",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200
    rpt = resp.json()["summary"]["rpt_collections"]
    assert len(rpt) == 1
    assert rpt[0]["triplesCollection"] == "_triples"
    assert rpt[0]["subjectColumn"] == "subject_uri"


def test_introspect_warnings_are_surfaced(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    bundle = _bundle_pg()
    new_meta = dict(bundle.metadata)
    new_meta["warnings"] = [
        {"code": "W_SCHEMA_LOW_CONFIDENCE", "message": "review"}
    ]
    stub_acquire["bundle"] = MappingBundle(
        conceptual_schema=bundle.conceptual_schema,
        physical_mapping=bundle.physical_mapping,
        metadata=new_meta,
        source=bundle.source,
    )
    resp = client.get(
        "/schema/introspect",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200
    warnings = resp.json()["warnings"]
    assert any(w["code"] == "W_SCHEMA_LOW_CONFIDENCE" for w in warnings)


# ---------------------------------------------------------------------------
# /schema/properties
# ---------------------------------------------------------------------------


def test_properties_requires_session(client: TestClient) -> None:
    resp = client.get("/schema/properties?collection=Person")
    assert resp.status_code == 401


def test_properties_returns_inferred_catalog(
    client: TestClient, session_token: str
) -> None:
    resp = client.get(
        "/schema/properties?collection=Person&sample_size=5",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["collection"] == "Person"
    assert body["sample_size"] == 5
    props = body["properties"]
    assert "name" in props and props["name"]["type"] == "string"
    assert "age" in props and props["age"]["type"] == "number"


def test_properties_caps_sample_size(
    client: TestClient, session_token: str
) -> None:
    resp = client.get(
        "/schema/properties?collection=Person&sample_size=99999",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200
    assert (
        resp.json()["sample_size"]
        == schema_route_mod._PROPERTIES_SAMPLE_HARD_CAP
    )


def test_properties_missing_collection_returns_422(
    client: TestClient, session_token: str
) -> None:
    # FastAPI will 422 on missing required query param itself.
    resp = client.get(
        "/schema/properties",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 422


def test_properties_unknown_collection_returns_empty(
    client: TestClient, session_token: str
) -> None:
    """Sampling an unknown collection should degrade to an empty
    catalog rather than 5xx — the underlying AQL EXECUTE will
    raise, which the route swallows.
    """

    resp = client.get(
        "/schema/properties?collection=NoSuch",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200
    assert resp.json()["properties"] == {}


# ---------------------------------------------------------------------------
# /schema/summary  (no session required)
# ---------------------------------------------------------------------------


def test_summary_get_returns_projection(client: TestClient) -> None:
    resp = client.request(
        "GET",
        "/schema/summary",
        json={
            "mapping": {
                "physicalMapping": {
                    "entities": {
                        "Person": {
                            "style": "COLLECTION",
                            "collectionName": "person",
                        }
                    },
                    "relationships": {},
                }
            }
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entity_count"] == 1
    assert body["entities"][0]["label"] == "Person"


def test_summary_post_works_too(client: TestClient) -> None:
    """POST is the proxy-friendly alternative for clients whose
    HTTP stack rejects GET bodies. Same result shape as GET.
    """

    resp = client.post(
        "/schema/summary",
        json={
            "mapping": {
                "physicalMapping": {
                    "entities": {
                        "Person": {
                            "style": "COLLECTION",
                            "collectionName": "person",
                        }
                    },
                    "relationships": {},
                }
            }
        },
    )
    assert resp.status_code == 200
    assert resp.json()["entity_count"] == 1


def test_summary_empty_payload_returns_422(client: TestClient) -> None:
    resp = client.post("/schema/summary", json={})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "E_SCHEMA_SUMMARY_EMPTY"


def test_summary_invalid_mapping_returns_422(client: TestClient) -> None:
    resp = client.post(
        "/schema/summary",
        json={"mapping": {"physicalMapping": "not-a-dict"}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "E_MAPPING_INVALID"


def test_summary_does_not_require_session(client: TestClient) -> None:
    """No X-Arango-Session header — should still 200."""

    resp = client.post(
        "/schema/summary",
        json={
            "mapping": {
                "physicalMapping": {"entities": {}, "relationships": {}}
            }
        },
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /schema/statistics
# ---------------------------------------------------------------------------


def test_statistics_requires_session(client: TestClient) -> None:
    resp = client.get("/schema/statistics")
    assert resp.status_code == 401


def test_statistics_unavailable_when_bundle_lacks_block(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    resp = client.get(
        "/schema/statistics",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["statistics"] == {}


def test_statistics_surfaces_when_bundle_carries_block(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    stub_acquire["bundle"] = _bundle_with_stats()
    resp = client.get(
        "/schema/statistics",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["statistics"]["entities"]["Person"]["estimated_count"] == 42
    assert body["last_acquired_at"] == "2026-05-01T12:00:00+00:00"


# ---------------------------------------------------------------------------
# /schema/status
# ---------------------------------------------------------------------------


def test_status_requires_session(client: TestClient) -> None:
    resp = client.get("/schema/status")
    assert resp.status_code == 401


def test_status_no_cache_when_nothing_introspected(
    client: TestClient, session_token: str
) -> None:
    resp = client.get(
        "/schema/status",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "no_cache"
    assert body["unchanged"] is False
    assert body["needs_full_rebuild"] is True
    assert body["cached"]["shape"] is None
    assert body["cached"]["counts"] is None


def test_status_unchanged_after_introspect_with_matching_fingerprints(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After an introspect, the cached fingerprint matches the live
    fingerprint (we monkey-patch both to the same value), so status
    reports unchanged.
    """

    headers = {"X-Arango-Session": session_token}
    client.get("/schema/introspect", headers=headers)
    cached_fp = compute_bundle_fingerprint(stub_acquire["bundle"])
    monkeypatch.setattr(
        schema_route_mod,
        "db_shape_fingerprint",
        lambda _db: cached_fp.shape,
    )
    monkeypatch.setattr(
        schema_route_mod,
        "db_counts_fingerprint",
        lambda _db: cached_fp.counts,
    )
    resp = client.get("/schema/status", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unchanged"
    assert body["unchanged"] is True


def test_status_stats_only_when_counts_drift(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = {"X-Arango-Session": session_token}
    client.get("/schema/introspect", headers=headers)
    cached_fp = compute_bundle_fingerprint(stub_acquire["bundle"])
    # Same shape, different counts → STATS_ONLY drift.
    monkeypatch.setattr(
        schema_route_mod,
        "db_shape_fingerprint",
        lambda _db: cached_fp.shape,
    )
    monkeypatch.setattr(
        schema_route_mod,
        "db_counts_fingerprint",
        lambda _db: "different-counts-fingerprint",
    )
    resp = client.get("/schema/status", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "stats_only"
    assert body["unchanged"] is False
    assert body["needs_full_rebuild"] is False


def test_status_shape_changed_when_topology_differs(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = {"X-Arango-Session": session_token}
    client.get("/schema/introspect", headers=headers)
    monkeypatch.setattr(
        schema_route_mod,
        "db_shape_fingerprint",
        lambda _db: "shape-after-ddl",
    )
    monkeypatch.setattr(
        schema_route_mod,
        "db_counts_fingerprint",
        lambda _db: "counts-after-ddl",
    )
    resp = client.get("/schema/status", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "shape_changed"
    assert body["needs_full_rebuild"] is True


def test_status_degrades_to_unchanged_when_live_fingerprints_unavailable(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the analyzer is missing (live fingerprints return
    ``None``), the route should report ``unchanged`` rather than
    flapping. The cache itself still reflects the cached
    fingerprint so a UI can render a "live drift unavailable" hint
    if it wants to.
    """

    headers = {"X-Arango-Session": session_token}
    client.get("/schema/introspect", headers=headers)
    monkeypatch.setattr(
        schema_route_mod, "db_shape_fingerprint", lambda _db: None
    )
    monkeypatch.setattr(
        schema_route_mod, "db_counts_fingerprint", lambda _db: None
    )
    resp = client.get("/schema/status", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unchanged"


# ---------------------------------------------------------------------------
# /schema/invalidate-cache
# ---------------------------------------------------------------------------


def test_invalidate_requires_session(client: TestClient) -> None:
    resp = client.post("/schema/invalidate-cache")
    assert resp.status_code == 401


def test_invalidate_drops_cached_entry(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    headers = {"X-Arango-Session": session_token}
    client.get("/schema/introspect", headers=headers)
    resp = client.post("/schema/invalidate-cache", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["invalidated"] is True
    assert body["db_name"] == "test_db"
    assert body["persistent_dropped"] is False

    # Next status call should report no_cache because the entry was dropped.
    status = client.get("/schema/status", headers=headers)
    assert status.json()["status"] == "no_cache"


def test_invalidate_returns_false_when_no_cache_entry(
    client: TestClient, session_token: str
) -> None:
    """No prior introspect → no entry to drop → invalidated=False."""

    resp = client.post(
        "/schema/invalidate-cache",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200
    assert resp.json()["invalidated"] is False


# ---------------------------------------------------------------------------
# /schema/force-reacquire
# ---------------------------------------------------------------------------


def test_force_reacquire_requires_session(client: TestClient) -> None:
    resp = client.post("/schema/force-reacquire")
    assert resp.status_code == 401


def test_force_reacquire_bypasses_cache(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    headers = {"X-Arango-Session": session_token}
    client.get("/schema/introspect", headers=headers)
    assert len(stub_acquire["calls"]) == 1

    resp = client.post("/schema/force-reacquire", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["cache_hit"] is False
    assert len(stub_acquire["calls"]) == 2
    assert stub_acquire["calls"][1]["force_refresh"] is True


def test_force_reacquire_503_when_both_opt_outs_off(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRD §6.4 row 7: when both ``SCHEMA_ANALYZER_REQUIRED=false``
    and ``ARANGO_SPARQL_ALLOW_HEURISTIC=false``, no acquisition path
    is available — return 503.
    """

    monkeypatch.setenv("SCHEMA_ANALYZER_REQUIRED", "false")
    monkeypatch.setenv("ARANGO_SPARQL_ALLOW_HEURISTIC", "false")
    resp = client.post(
        "/schema/force-reacquire",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "E_SCHEMA_UNAVAILABLE"


def test_force_reacquire_503_when_analyzer_missing_and_no_fallback(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SCHEMA_ANALYZER_REQUIRED stays true but
    ARANGO_SPARQL_ALLOW_HEURISTIC=false — and the analyzer is
    unimportable. The strategy="auto" path cannot serve any tier.
    """

    monkeypatch.setenv("ARANGO_SPARQL_ALLOW_HEURISTIC", "false")
    monkeypatch.setattr(
        schema_route_mod, "analyzer_available", lambda: False
    )
    resp = client.post(
        "/schema/force-reacquire",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["code"] == "E_ANALYZER_NOT_INSTALLED"
    assert "install_hint" in detail


def test_force_reacquire_invalid_strategy_returns_422(
    client: TestClient, session_token: str
) -> None:
    resp = client.post(
        "/schema/force-reacquire?strategy=bogus",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "E_SCHEMA_STRATEGY_INVALID"


# ---------------------------------------------------------------------------
# /schema/owl — OWL schema-graph projection
# ---------------------------------------------------------------------------


_OWL_TTL = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex: <http://example.org/> .

ex:Person a owl:Class ; rdfs:label "Person" .
ex:Org a owl:Class ; rdfs:comment "An organisation" .
ex:knows a owl:ObjectProperty ; rdfs:domain ex:Person ; rdfs:range ex:Person .
ex:worksAt a owl:ObjectProperty ; rdfs:domain ex:Person ; rdfs:range ex:Org .
ex:name a owl:DatatypeProperty ; rdfs:domain ex:Person ; rdfs:range rdfs:Literal .
"""


def _bundle_with_owl() -> MappingBundle:
    """A bundle carrying an inline OWL ontology so ``/schema/owl`` is
    deterministic: ``mapping_to_turtle`` returns the inline Turtle
    verbatim and ``owl_graph_view`` parses it back into the graph shape.
    """

    return MappingBundle(
        conceptual_schema={"entities": [], "relationships": []},
        physical_mapping={"entities": {}, "relationships": {}},
        metadata={"source": "test_fixture_owl", "warnings": []},
        owl_turtle=_OWL_TTL,
        source=MappingSource(kind="imported_owl", notes="owl fixture"),
    )


def test_owl_requires_session(client: TestClient) -> None:
    resp = client.get("/schema/owl")
    assert resp.status_code == 401


def test_owl_happy_path_projects_classes_and_properties(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    stub_acquire["bundle"] = _bundle_with_owl()
    resp = client.get(
        "/schema/owl",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Classes → nodes (camelCase aliases, matching the frontend shape).
    class_names = {c["localName"] for c in body["classes"]}
    assert class_names == {"Person", "Org"}
    org = next(c for c in body["classes"] if c["localName"] == "Org")
    assert org["comment"] == "An organisation"

    # Object properties → edges; datatype property → bag (kind tag).
    by_name = {p["localName"]: p for p in body["properties"]}
    assert by_name["knows"]["kind"] == "object"
    assert by_name["knows"]["domain"] == ["http://example.org/Person"]
    assert by_name["knows"]["range"] == ["http://example.org/Person"]
    assert by_name["worksAt"]["range"] == ["http://example.org/Org"]
    assert by_name["name"]["kind"] == "datatype"

    # The source TTL rides along for round-tripping into the editor.
    assert "ex:Person" in body["turtle"]
    assert body["source"]["kind"] == "imported_owl"


def test_owl_forwards_include_owl_to_acquire(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    stub_acquire["bundle"] = _bundle_with_owl()
    client.get("/schema/owl", headers={"X-Arango-Session": session_token})
    assert stub_acquire["calls"], "acquire was not called"
    assert stub_acquire["calls"][0]["include_owl"] is True


def test_owl_empty_bundle_returns_empty_graph(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    """An empty database (no entities, no inline OWL) must yield empty
    class/property lists with a 200 — "nothing to draw" is a normal,
    non-error state for the GRAPH tab, not a 404/500.
    """

    stub_acquire["bundle"] = MappingBundle(
        conceptual_schema={"entities": [], "relationships": []},
        physical_mapping={"entities": {}, "relationships": {}},
        metadata={"source": "empty_fixture", "warnings": []},
        source=MappingSource(kind="analyzer", notes="empty"),
    )
    resp = client.get(
        "/schema/owl",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["classes"] == []
    assert body["properties"] == []


def test_owl_strategy_invalid_returns_422(
    client: TestClient, session_token: str
) -> None:
    resp = client.get(
        "/schema/owl?strategy=bogus",
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "E_SCHEMA_STRATEGY_INVALID"


def test_owl_second_call_is_cache_hit(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    stub_acquire["bundle"] = _bundle_with_owl()
    headers = {"X-Arango-Session": session_token}
    first = client.get("/schema/owl", headers=headers)
    assert first.status_code == 200
    second = client.get("/schema/owl", headers=headers)
    assert second.status_code == 200
    # Cache shared with introspect → only one acquire call.
    assert len(stub_acquire["calls"]) == 1


# ---------------------------------------------------------------------------
# OpenAPI surface
# ---------------------------------------------------------------------------


def test_openapi_includes_all_schema_routes(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    paths = set(spec.get("paths", {}).keys())
    expected = {
        "/schema/introspect",
        "/schema/owl",
        "/schema/properties",
        "/schema/summary",
        "/schema/statistics",
        "/schema/status",
        "/schema/invalidate-cache",
        "/schema/force-reacquire",
    }
    missing = expected - paths
    assert not missing, f"missing schema routes in OpenAPI: {missing!r}"


def test_openapi_summary_has_both_get_and_post(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    summary_ops = spec["paths"]["/schema/summary"]
    assert "get" in summary_ops
    assert "post" in summary_ops
    assert summary_ops["get"]["operationId"] != summary_ops["post"]["operationId"]


# ---------------------------------------------------------------------------
# Cache singleton behavior
# ---------------------------------------------------------------------------


def test_cache_singleton_is_a_real_schema_cache() -> None:
    cache = schema_route_mod._resolve_schema_cache()
    assert isinstance(cache, SchemaCache)


def test_reset_cache_drops_every_entry(
    client: TestClient,
    session_token: str,
    stub_acquire: dict[str, Any],
) -> None:
    headers = {"X-Arango-Session": session_token}
    client.get("/schema/introspect", headers=headers)
    cache = schema_route_mod._resolve_schema_cache()
    assert len(cache) == 1
    schema_route_mod._reset_cache()
    assert len(cache) == 0


# ---------------------------------------------------------------------------
# Env-var tracking sanity
# ---------------------------------------------------------------------------


def test_env_vars_default_to_true_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCHEMA_ANALYZER_REQUIRED", raising=False)
    monkeypatch.delenv("ARANGO_SPARQL_ALLOW_HEURISTIC", raising=False)
    assert schema_route_mod._analyzer_required() is True
    assert schema_route_mod._allow_heuristic_fallback() is True


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Explicit-true family → required (the safe default)
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        # Explicit-false family → opt-out honored
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        # Garbage / empty → safe default (PRD §6.3.4 verbose opt-out)
        ("garbage", True),
        ("", True),
        ("   ", True),
        ("maybe", True),
    ],
)
def test_analyzer_required_env_parsing(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("SCHEMA_ANALYZER_REQUIRED", raw)
    assert schema_route_mod._analyzer_required() is expected


def test_public_mode_forces_heuristic_fallback_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ARANGO_SPARQL_PUBLIC_MODE=true should make
    ``_allow_heuristic_fallback`` return False even when
    ARANGO_SPARQL_ALLOW_HEURISTIC=true.
    """

    monkeypatch.setenv("ARANGO_SPARQL_ALLOW_HEURISTIC", "true")
    # ``_PUBLIC_MODE`` is read at import time. The ``arango_sparql.service.app``
    # *attribute* on the parent package resolves to the FastAPI app
    # object (the parent re-exports it for ergonomic
    # ``from arango_sparql.service import app`` imports). The
    # underlying module lives at ``sys.modules["arango_sparql.service.app"]``;
    # patch it there.
    import sys

    app_mod = sys.modules["arango_sparql.service.app"]
    monkeypatch.setattr(app_mod, "_PUBLIC_MODE", True)
    assert schema_route_mod._allow_heuristic_fallback() is False


# ---------------------------------------------------------------------------
# Smoke: env-var leakage between tests
# ---------------------------------------------------------------------------


def test_env_var_leak_canary() -> None:
    """If SCHEMA_ANALYZER_REQUIRED is set in the surrounding shell,
    that leaks into every other test in this file. The autouse
    fixture deletes it; this canary fails loudly if the deletion
    regresses.
    """

    assert os.environ.get("SCHEMA_ANALYZER_REQUIRED") is None
    assert os.environ.get("ARANGO_SPARQL_ALLOW_HEURISTIC") is None
