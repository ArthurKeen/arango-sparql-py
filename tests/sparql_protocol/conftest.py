"""Shared fixtures + fakes for the SPARQL Protocol integration tests
(PRD §5.2).

Every test module in this folder gets:

* ``client`` — a :class:`fastapi.testclient.TestClient` against the
  service app.
* ``fake_arango`` — patches ``arango_sparql.service.ArangoClient``
  with the in-process :class:`_FakeArangoClient` so ``/connect``
  succeeds without a real ArangoDB.
* ``session_token`` — opens a real ``/connect`` session against the
  fake driver and seeds the schema cache with :data:`_BUNDLE` so
  ``/sparql`` doesn't need to introspect.
* ``isolate_state`` (autouse) — clears sessions, the schema cache,
  the env-default protocol session, and replaces the compute
  rate-limit bucket with a generous fresh one (matches the same
  trick :mod:`tests.test_service_nl_routes` uses for its NL
  bucket — without it, a /sparql test late in the suite can
  spuriously 429 because tokens were spent by other test files).

Plus the ``set_aql_rows`` helper for injecting cursor data and
the canonical SELECT / ASK / CONSTRUCT query strings the test
modules import.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import arango_sparql.service as svc
from arango_sparql.service import _sessions, app
from arango_sparql.service.routes import protocol as _protocol
from arango_sparql.service.routes import schema as _schema
from arango_sparql.service.security import _TokenBucket
from arango_sparql.translate.mapping import MappingBundle, MappingSource

# ---------------------------------------------------------------------------
# Re-exported query fixtures — every test module imports these.
# ---------------------------------------------------------------------------

SELECT_QUERY = "SELECT ?x WHERE { ?x a <http://ex.org/Person> } LIMIT 10"
ASK_QUERY = "ASK WHERE { ?x a <http://ex.org/Person> }"
CONSTRUCT_QUERY = "CONSTRUCT { ?x a <http://ex.org/Person> } WHERE { ?x a <http://ex.org/Person> }"


# ---------------------------------------------------------------------------
# Fake python-arango — minimal stub.
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Iterable cursor over a fixed row list — what the protocol
    route's ``_materialise`` helper drains.
    """

    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)
        self._iter = iter(self._rows)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iter)


class _FakeAql:
    """Routable AQL stub. Per-test code injects ``rows`` and the
    next ``execute`` call returns them. ``calls`` records every
    (aql, bind_vars, kwargs) so tests can assert against it.
    """

    def __init__(self) -> None:
        self.rows: list[Any] = []
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def execute(
        self,
        query: str,
        bind_vars: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> _FakeCursor:
        self.calls.append((query, dict(bind_vars or {}), dict(kwargs)))
        return _FakeCursor(self.rows)


class _FakeDb:
    def __init__(self, name: str = "test_db") -> None:
        self.name = name
        self.aql = _FakeAql()

    def collections(self) -> list[dict[str, Any]]:
        return []

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
            self._dbs[name] = _FakeDb(name=name)
        return self._dbs[name]

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Schema bundle — small but realistic OWL ontology binding the
# SPARQL IRIs the test queries use to physical collections.
# ---------------------------------------------------------------------------


_OWL_TTL = """
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix ex:   <http://ex.org/> .

ex:Person a owl:Class ;
    phys:collectionName "Person" ;
    phys:mappingStyle "COLLECTION" .

ex:Org a owl:Class ;
    phys:collectionName "Org" ;
    phys:mappingStyle "COLLECTION" .

ex:knows a owl:ObjectProperty ;
    phys:edgeCollectionName "knows" ;
    phys:mappingStyle "DEDICATED_COLLECTION" ;
    rdfs:domain ex:Person ;
    rdfs:range  ex:Person .
"""


BUNDLE = MappingBundle(
    physical_mapping={
        "entities": {
            "Person": {"style": "COLLECTION", "collectionName": "Person"},
            "Org": {"style": "COLLECTION", "collectionName": "Org"},
        },
        "relationships": {
            "knows": {
                "style": "DEDICATED_COLLECTION",
                "edgeCollectionName": "knows",
                "fromEntity": "Person",
                "toEntity": "Person",
            }
        },
    },
    owl_turtle=_OWL_TTL,
    source=MappingSource(kind="manual"),
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolate_state(monkeypatch: pytest.MonkeyPatch):
    _sessions.clear()
    monkeypatch.setattr(_protocol, "_env_default_session", None, raising=False)
    _schema._resolve_schema_cache().clear()
    monkeypatch.setattr(svc, "_compute_bucket", _TokenBucket(10_000))
    monkeypatch.delenv("ARANGO_SPARQL_PUBLIC_MODE", raising=False)
    monkeypatch.delenv("SPARQL_PROTOCOL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("SPARQL_PROTOCOL_MAX_BODY_BYTES", raising=False)
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
def fake_arango(monkeypatch: pytest.MonkeyPatch):
    _FakeArangoClient.instances.clear()
    monkeypatch.setattr(svc, "ArangoClient", _FakeArangoClient)
    monkeypatch.setenv("ARANGO_SPARQL_CONNECT_ALLOWED_HOSTS", "localhost,127.0.0.1")
    return _FakeArangoClient


@pytest.fixture
def session_token(client: TestClient, fake_arango: type) -> str:
    """Issue a real session via /connect against the fake driver
    and seed the schema cache with :data:`BUNDLE` so ``/sparql``
    doesn't need to introspect.
    """

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
    token = resp.json()["token"]
    _schema._resolve_schema_cache().put("test_db", BUNDLE)
    return token


def set_aql_rows(token: str, rows: list[Any]) -> None:
    """Inject the next AQL execute's rows for the session backed
    by *token*. Helper rather than a fixture so individual tests
    can call it multiple times in one body.
    """

    session = _sessions[token]
    session.db.aql.rows = rows
