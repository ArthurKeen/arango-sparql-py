"""Route tests for ArangoDB named-graph scoping — ``GET /graphs`` and
``POST /session/graph``.

Drives the real ``app`` via :class:`fastapi.testclient.TestClient` with a
fake python-arango stack (same posture as ``test_service_nl_routes``).
Asserts the frozen contract: response shapes, the 404 on an unknown
graph, the clear-scope semantics of ``graphName: null``, and that the
bound name actually lands on the in-memory session.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import arango_sparql.service as svc
from arango_sparql.service import _sessions, app


class _FakeGraph:
    def __init__(self, vertices: list[str], edge_defs: list[dict[str, Any]]) -> None:
        self._v = vertices
        self._e = edge_defs

    def vertex_collections(self) -> list[str]:
        return list(self._v)

    def edge_definitions(self) -> list[dict[str, Any]]:
        return list(self._e)


class _FakeDb:
    def __init__(self, name: str, *, graphs: dict[str, _FakeGraph] | None = None) -> None:
        self.name = name
        self._graphs = graphs or {}

    def version(self) -> str:
        return "3.12.0"

    def databases(self) -> list[str]:
        return ["_system"]

    def has_graph(self, name: str) -> bool:
        return name in self._graphs

    def graph(self, name: str) -> _FakeGraph:
        return self._graphs[name]

    def graphs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, g in self._graphs.items():
            edge_defs = [
                {
                    "collection": ed.get("edge_collection"),
                    "from": ed.get("from_vertex_collections", []),
                    "to": ed.get("to_vertex_collections", []),
                }
                for ed in g.edge_definitions()
            ]
            out.append({"name": name, "edgeDefinitions": edge_defs})
        return out


# A db whose graphs() blows up — exercises the degrade-to-empty path.
class _ExplodingGraphsDb(_FakeDb):
    def graphs(self) -> list[dict[str, Any]]:
        raise RuntimeError("insufficient permissions for graphs()")


def _social_graph() -> dict[str, _FakeGraph]:
    return {
        "social": _FakeGraph(
            ["Person"],
            [
                {
                    "edge_collection": "knows",
                    "from_vertex_collections": ["Person"],
                    "to_vertex_collections": ["Person"],
                }
            ],
        )
    }


class _FakeArangoClient:
    instances: list[_FakeArangoClient] = []
    db_factory: Any = None

    def __init__(self, hosts: str = "") -> None:
        self.hosts = hosts
        self._dbs: dict[str, _FakeDb] = {}
        _FakeArangoClient.instances.append(self)

    def db(self, name: str, username: str | None = None, password: str | None = None) -> _FakeDb:
        if name not in self._dbs:
            self._dbs[name] = _FakeArangoClient.db_factory(name)
        return self._dbs[name]

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _isolate_sessions():
    _sessions.clear()
    yield
    for s in list(_sessions.values()):
        try:
            s.client.close()
        except Exception:
            pass
    _sessions.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def fake_client_factory(monkeypatch: pytest.MonkeyPatch):
    _FakeArangoClient.instances.clear()
    _FakeArangoClient.db_factory = lambda name: _FakeDb(name, graphs=_social_graph())
    monkeypatch.setattr(svc, "ArangoClient", _FakeArangoClient)
    monkeypatch.setenv("ARANGO_SPARQL_CONNECT_ALLOWED_HOSTS", "localhost,127.0.0.1")
    return _FakeArangoClient


def _connect(client: TestClient) -> str:
    resp = client.post(
        "/connect",
        json={
            "url": "http://localhost:8529",
            "database": "_system",
            "username": "root",
            "password": "<test-stub-pw>",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


# ---------------------------------------------------------------------------
# GET /graphs
# ---------------------------------------------------------------------------


def test_list_graphs_without_session_is_401(client: TestClient) -> None:
    assert client.get("/graphs").status_code == 401


def test_list_graphs_returns_named_graphs(
    client: TestClient, fake_client_factory
) -> None:
    token = _connect(client)
    resp = client.get("/graphs", headers={"X-Arango-Session": token})
    assert resp.status_code == 200, resp.text
    graphs = resp.json()["graphs"]
    assert len(graphs) == 1
    g = graphs[0]
    assert g["name"] == "social"
    assert g["edgeCollections"] == ["knows"]
    assert g["vertexCollections"] == ["Person"]
    assert g["collectionCount"] == 2


def test_list_graphs_empty_when_db_has_none(
    client: TestClient, fake_client_factory
) -> None:
    _FakeArangoClient.db_factory = lambda name: _FakeDb(name, graphs={})
    token = _connect(client)
    resp = client.get("/graphs", headers={"X-Arango-Session": token})
    assert resp.status_code == 200, resp.text
    assert resp.json()["graphs"] == []


def test_list_graphs_degrades_when_graphs_call_raises(
    client: TestClient, fake_client_factory
) -> None:
    _FakeArangoClient.db_factory = lambda name: _ExplodingGraphsDb(name, graphs=_social_graph())
    token = _connect(client)
    resp = client.get("/graphs", headers={"X-Arango-Session": token})
    assert resp.status_code == 200, resp.text
    assert resp.json()["graphs"] == []


# ---------------------------------------------------------------------------
# POST /session/graph
# ---------------------------------------------------------------------------


def test_bind_graph_success_sets_session_scope(
    client: TestClient, fake_client_factory
) -> None:
    token = _connect(client)
    resp = client.post(
        "/session/graph",
        json={"graphName": "social"},
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"graph_name": "social", "bound": True}
    # The scope actually landed on the in-memory session.
    assert _sessions[token].graph_name == "social"


def test_bind_unknown_graph_is_404(client: TestClient, fake_client_factory) -> None:
    token = _connect(client)
    resp = client.post(
        "/session/graph",
        json={"graphName": "nope"},
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error"] == "unknown_graph"
    assert detail["graphName"] == "nope"
    # Failed bind leaves the session unscoped.
    assert _sessions[token].graph_name is None


def test_bind_null_clears_scope(client: TestClient, fake_client_factory) -> None:
    token = _connect(client)
    # First bind to a real graph.
    client.post(
        "/session/graph",
        json={"graphName": "social"},
        headers={"X-Arango-Session": token},
    )
    assert _sessions[token].graph_name == "social"
    # Now clear it.
    resp = client.post(
        "/session/graph",
        json={"graphName": None},
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"graph_name": None, "bound": False}
    assert _sessions[token].graph_name is None


def test_bind_graph_without_session_is_401(client: TestClient) -> None:
    resp = client.post("/session/graph", json={"graphName": "social"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# OpenAPI smoke
# ---------------------------------------------------------------------------


def test_openapi_includes_graph_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/graphs" in paths
    assert "/session/graph" in paths
