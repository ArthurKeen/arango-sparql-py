"""``GET /examples``: verified mined examples served from the session's database.

Ported from arango-cypher-py's ``tests/test_examples_endpoint.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from arango.exceptions import AQLQueryExecuteError
from fastapi.testclient import TestClient

from arango_sparql.query_mining.store import DEFAULT_COLLECTION
from arango_sparql.service import _sessions, app
from arango_sparql.service.security import _Session
from tests.helpers.fake_arango import FakeDb, server_error

_TOKEN = "test-examples-token"


class _Client:
    """``ArangoClient.close()`` — all a session's client is asked for here."""

    def close(self) -> None:
        pass


def _open_session(db: FakeDb, graph: str | None = None) -> dict[str, str]:
    _sessions[_TOKEN] = _Session(token=_TOKEN, db=db, client=_Client(), graph_name=graph)  # type: ignore[arg-type]
    return {"X-Arango-Session": _TOKEN}


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app)
    _sessions.pop(_TOKEN, None)


EXAMPLE = {
    "_key": "mined-1",
    "kind": "mined",
    "question": "Which roles trust each other?",
    "sparql": "SELECT ?a ?b WHERE { ?a :CAN_ASSUME ?b }",
    "graph": "IAM_DEMO",
}


def test_lists_the_sessions_examples_scoped_to_its_graph(client: TestClient) -> None:
    seen: list[dict[str, Any]] = []

    def handler(q: str, b: dict[str, Any]) -> list[Any]:
        seen.append(b)
        return [EXAMPLE]

    headers = _open_session(FakeDb({DEFAULT_COLLECTION: []}, handler=handler), graph="IAM_DEMO")
    resp = client.get("/examples?limit=20", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"examples": [EXAMPLE]}
    assert seen == [{"@c": DEFAULT_COLLECTION, "scope": "IAM_DEMO", "limit": 20}]


def test_a_database_never_mined_has_none(client: TestClient) -> None:
    headers = _open_session(FakeDb({}))
    assert client.get("/examples", headers=headers).json() == {"examples": []}


def test_requires_a_session(client: TestClient) -> None:
    assert client.get("/examples").status_code == 401


def test_the_limit_is_bounded(client: TestClient) -> None:
    headers = _open_session(FakeDb({}))
    assert client.get("/examples?limit=0", headers=headers).status_code == 422
    assert client.get("/examples?limit=201", headers=headers).status_code == 422


def test_a_read_failure_is_a_502_naming_the_collection(client: TestClient) -> None:
    def handler(q: str, b: dict[str, Any]) -> list[Any]:
        raise server_error(AQLQueryExecuteError, 403, 11, "not authorized to execute this request")

    headers = _open_session(FakeDb({DEFAULT_COLLECTION: []}, handler=handler))
    resp = client.get("/examples", headers=headers)
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "E_EXAMPLES_UNAVAILABLE"
    assert DEFAULT_COLLECTION in resp.json()["detail"]["error"]
