"""End-to-end tests for the FastAPI route surface in
``arango_sparql.service``.

Mirrors ``arango-cypher-py``'s ``test_service_*.py`` posture:

* drives the real ``app`` via :class:`fastapi.testclient.TestClient`,
* monkeypatches the package-level ``ArangoClient`` symbol to a tiny
  fake so ``/connect`` issues a real session without touching a live
  database,
* exercises happy / error paths for every endpoint the SPARQL backend
  exposes today (``/health``, ``/translate``, ``/validate``,
  ``/execute``, ``/execute-aql``, ``/connect``, ``/disconnect``,
  ``/connect/defaults``).

Subagent-built ``service/security.py`` carries the documented hook
``monkeypatch.setattr("arango_sparql.service.ArangoClient", FakeClient)``
— this file is the contract that proves it.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

import arango_sparql.service as svc
from arango_sparql.service import _sessions, app

ONTOLOGY_TTL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

:Person a owl:Class ;
    phys:collectionName "Person" .

:name a owl:DatatypeProperty ;
    rdfs:domain :Person ;
    rdfs:range <http://www.w3.org/2001/XMLSchema#string> .

:age a owl:DatatypeProperty ;
    rdfs:domain :Person ;
    rdfs:range <http://www.w3.org/2001/XMLSchema#integer> .
"""

SELECT_QUERY = """
PREFIX : <http://ex.org/>
SELECT ?s ?n WHERE {
  ?s a :Person ;
     :name ?n .
}
LIMIT 5
"""


@pytest.fixture(autouse=True)
def _isolate_sessions():
    """Clear the in-process session table around every test."""
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


# ---------------------------------------------------------------------------
# Fake python-arango stack — small enough to be obviously correct, big
# enough to exercise the cursor materialisation + bind-var plumbing in
# the route handlers.
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[Any], *, profile: dict[str, Any] | None = None) -> None:
        self._rows = list(rows)
        self._iter = iter(self._rows)
        # Surfaced as ``cursor.profile()`` to match python-arango's
        # contract on ``aql.execute(query, profile=2)``. ``None`` means
        # "this cursor was not profiled" — the route layer must guard
        # the call site.
        self._profile = profile

    def __iter__(self):
        return self._iter

    def __next__(self):
        return next(self._iter)

    def profile(self) -> dict[str, Any] | None:
        return self._profile


# Synthetic AQL ``EXPLAIN`` plan returned by the fake driver. Mirrors the
# shape an operator sees in the ArangoDB UI — enough keys for a UI to
# render against without pulling in the full driver contract.
_FAKE_PLAN: dict[str, Any] = {
    "nodes": [
        {"type": "SingletonNode", "id": 1},
        {"type": "EnumerateCollectionNode", "id": 2, "collection": "Person"},
        {"type": "ReturnNode", "id": 3},
    ],
    "rules": ["use-indexes"],
    "collections": [{"name": "Person", "type": "read"}],
    "estimatedCost": 42.0,
    "estimatedNrItems": 1,
}


# Synthetic ``cursor.profile()`` result. ``profile=2`` (full) emits both
# per-stage timings and counters; we only need enough keys to assert the
# route forwards the blob verbatim.
_FAKE_PROFILE: dict[str, Any] = {
    "initializing": 0.0001,
    "parsing": 0.0002,
    "optimizing-plan": 0.0005,
    "executing": 0.0123,
    "finalizing": 0.0001,
}


class _FakeAql:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db
        self.last_aql: str | None = None
        self.last_bind_vars: dict[str, Any] | None = None
        # Most-recent ``profile`` kw on ``.execute()`` — assert in tests
        # that ``/profile`` forwards ``profile=2`` to the driver.
        self.last_profile: Any = None
        # Most-recent call to ``.explain()`` — used by ``/explain``
        # tests to verify the route forwarded the AQL + bind vars.
        self.last_explain_aql: str | None = None
        self.last_explain_bind_vars: dict[str, Any] | None = None

    def execute(
        self,
        aql: str,
        bind_vars: dict[str, Any] | None = None,
        *,
        profile: Any = None,
    ) -> _FakeCursor:
        self.last_aql = aql
        self.last_bind_vars = dict(bind_vars or {})
        self.last_profile = profile
        # python-arango only attaches a profile blob to the cursor when
        # the caller asked for one; mirror that so tests can verify the
        # route handles the "no profile" branch as well.
        profile_blob = _FAKE_PROFILE if profile else None
        return _FakeCursor(self._db.rows, profile=profile_blob)

    def explain(
        self,
        aql: str,
        bind_vars: dict[str, Any] | None = None,
        *,
        all_plans: bool = False,
        opt_rules: Any = None,
    ) -> dict[str, Any]:
        self.last_explain_aql = aql
        self.last_explain_bind_vars = dict(bind_vars or {})
        return dict(_FAKE_PLAN)


class _FakeDb:
    def __init__(self, name: str, rows: list[Any] | None = None) -> None:
        self.name = name
        self.rows: list[Any] = rows if rows is not None else [{"s": "u/1", "n": "Alice"}]
        self.aql = _FakeAql(self)

    def version(self) -> str:
        return "3.12.0"

    def databases(self) -> list[str]:
        return ["_system", "myapp"]


class _FakeArangoClient:
    """Stand-in for :class:`arango.ArangoClient`.

    Records every ``.db()`` call so a test can assert the route layer
    forwarded the requested database name. Constructor accepts any
    ``hosts=`` and stores it for assertion.
    """

    instances: list[_FakeArangoClient] = []

    def __init__(self, hosts: str = "") -> None:
        self.hosts = hosts
        self.db_calls: list[tuple[str, str | None, str | None]] = []
        self._dbs: dict[str, _FakeDb] = {}
        _FakeArangoClient.instances.append(self)

    def db(
        self,
        name: str,
        username: str | None = None,
        password: str | None = None,
    ) -> _FakeDb:
        self.db_calls.append((name, username, password))
        if name not in self._dbs:
            self._dbs[name] = _FakeDb(name)
        return self._dbs[name]

    def close(self) -> None:
        pass


@pytest.fixture
def fake_client_factory(monkeypatch: pytest.MonkeyPatch):
    """Patch the package-level ``ArangoClient`` so /connect uses the fake."""
    _FakeArangoClient.instances.clear()
    monkeypatch.setattr(svc, "ArangoClient", _FakeArangoClient)
    # Allow our localhost fake to pass the SSRF guard regardless of how
    # ARANGO_SPARQL_CONNECT_ALLOWED_HOSTS is configured in the
    # operator's shell when they run pytest.
    monkeypatch.setenv("ARANGO_SPARQL_CONNECT_ALLOWED_HOSTS", "localhost,127.0.0.1")
    return _FakeArangoClient


def _connect_session(client: TestClient) -> str:
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
# /health
# ---------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str) and body["version"]


# ---------------------------------------------------------------------------
# /translate — the parse+visit+emit happy/error paths.
# ---------------------------------------------------------------------------


def test_translate_happy_path(client: TestClient) -> None:
    resp = client.post(
        "/translate",
        json={"sparql": SELECT_QUERY, "ontology_ttl": ONTOLOGY_TTL},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "FOR " in body["aql"]
    assert "@@" in body["aql"]
    assert isinstance(body["bind_vars"], dict)
    assert body["elapsed_ms"] is None or body["elapsed_ms"] >= 0


def test_translate_parse_error_returns_422(client: TestClient) -> None:
    resp = client.post("/translate", json={"sparql": "SELECT WHERE { broken"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "E_SPARQL_PARSE"
    assert "error" in detail


def test_translate_unsupported_feature_returns_422(client: TestClient) -> None:
    # CONSTRUCT isn't ported yet — make sure the visitor's typed
    # UnsupportedSparqlError reaches the client as a 422 with the
    # documented stable code, not an opaque 500. (Update this case as
    # the visitor learns more constructs; the assertion that matters
    # is the error envelope, not the specific feature.)
    sparql = """
    PREFIX : <http://ex.org/>
    CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }
    """
    resp = client.post(
        "/translate",
        json={"sparql": sparql, "ontology_ttl": ONTOLOGY_TTL},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "E_SPARQL_UNSUPPORTED"


def test_translate_malformed_ontology_returns_422(client: TestClient) -> None:
    resp = client.post(
        "/translate",
        json={"sparql": SELECT_QUERY, "ontology_ttl": "this is not turtle :{("},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "E_SCHEMA_RESOLVE"


def test_translate_oversized_sparql_rejected_at_pydantic(client: TestClient) -> None:
    # _MAX_SPARQL_LENGTH is 100k; a 200k payload should be rejected by
    # the Pydantic field bound before the parser ever sees it.
    resp = client.post("/translate", json={"sparql": "SELECT ?s WHERE { ?s ?p ?o } " + "x" * 200_000})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /validate — parse-only, no AQL emission.
# ---------------------------------------------------------------------------


def test_validate_valid(client: TestClient) -> None:
    resp = client.post("/validate", json={"sparql": SELECT_QUERY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["errors"] == []


def test_validate_invalid(client: TestClient) -> None:
    resp = client.post("/validate", json={"sparql": "this isn't sparql"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert body["errors"]
    assert body["errors"][0]["code"] == "E_SPARQL_PARSE"


# ---------------------------------------------------------------------------
# /execute — requires a session, drives the fake python-arango stack.
# ---------------------------------------------------------------------------


def test_execute_without_session_is_401(client: TestClient) -> None:
    resp = client.post(
        "/execute",
        json={"sparql": SELECT_QUERY, "ontology_ttl": ONTOLOGY_TTL},
    )
    assert resp.status_code == 401


def test_execute_happy_path(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    token = _connect_session(client)
    resp = client.post(
        "/execute",
        json={"sparql": SELECT_QUERY, "ontology_ttl": ONTOLOGY_TTL},
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bindings"] == [{"s": "u/1", "n": "Alice"}]
    assert "FOR " in body["aql"]
    # The route forwards bind_vars verbatim — confirm they round-trip
    # through the fake AQL execute.
    fake = fake_client_factory.instances[-1]
    fake_db = fake._dbs["_system"]
    assert fake_db.aql.last_aql == body["aql"]
    assert fake_db.aql.last_bind_vars == body["bind_vars"]


def test_execute_translation_error_returns_422_before_db(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    token = _connect_session(client)
    resp = client.post(
        "/execute",
        json={"sparql": "SELECT WHERE { still broken", "ontology_ttl": ONTOLOGY_TTL},
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "E_SPARQL_PARSE"
    # Translation failed → AQL execute must not have been called on
    # the fake db.
    fake = fake_client_factory.instances[-1]
    fake_db = fake._dbs["_system"]
    assert fake_db.aql.last_aql is None


def _seed_fake_db_rows(
    monkeypatch: pytest.MonkeyPatch,
    fake_factory: type[_FakeArangoClient],
    rows: list[Any],
) -> None:
    """Pre-bind the rows that the next ``client.db("_system")`` call returns.

    Necessary because the route layer caches the db reference inside the
    session at /connect time — mutating ``fake._dbs[name]`` after the
    session is already minted has no effect on subsequent /execute
    calls. We monkeypatch the fake's ``__init__`` so any
    ``_FakeArangoClient`` constructed by /connect during this test
    seeds its initial ``_FakeDb`` with the requested rows.
    """
    original_db = _FakeArangoClient.db

    def patched_db(self: _FakeArangoClient, name: str, **kwargs: Any) -> _FakeDb:
        if name not in self._dbs:
            self._dbs[name] = _FakeDb(name, rows=list(rows))
        self.db_calls.append((name, kwargs.get("username"), kwargs.get("password")))
        return self._dbs[name]

    monkeypatch.setattr(_FakeArangoClient, "db", patched_db)
    fake_factory.instances.clear()
    return original_db


def test_execute_truncates_at_max_result_docs(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("arango_sparql.service.routes.sparql._MAX_RESULT_DOCS", 3)
    _seed_fake_db_rows(
        monkeypatch,
        fake_client_factory,
        [{"s": f"u/{i}", "n": f"row{i}"} for i in range(10)],
    )

    token = _connect_session(client)
    resp = client.post(
        "/execute",
        json={"sparql": SELECT_QUERY, "ontology_ttl": ONTOLOGY_TTL},
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["bindings"]) == 3
    assert body["truncated"] is True
    assert any(w["code"] == "W_RESULT_TRUNCATED" for w in body["warnings"])


# ---------------------------------------------------------------------------
# /execute-aql — raw AQL pass-through, no SPARQL parsing.
# ---------------------------------------------------------------------------


def test_execute_aql_happy_path(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_fake_db_rows(
        monkeypatch,
        fake_client_factory,
        [{"name": "raw"}, {"name": "result"}],
    )
    token = _connect_session(client)
    resp = client.post(
        "/execute-aql",
        json={
            "aql": "FOR doc IN @@c RETURN { name: doc.name }",
            "bind_vars": {"@c": "Person"},
        },
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"] == [{"name": "raw"}, {"name": "result"}]
    assert body["aql"].startswith("FOR doc IN")
    assert body["bind_vars"] == {"@c": "Person"}
    fake = fake_client_factory.instances[-1]
    fake_db = fake._dbs["_system"]
    assert fake_db.aql.last_bind_vars == {"@c": "Person"}


def test_execute_aql_without_session_is_401(client: TestClient) -> None:
    resp = client.post(
        "/execute-aql",
        json={"aql": "RETURN 1", "bind_vars": {}},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /connect, /disconnect, /connect/defaults — session lifecycle.
# ---------------------------------------------------------------------------


def test_connect_creates_session(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    resp = client.post(
        "/connect",
        json={
            "url": "http://localhost:8529",
            "database": "_system",
            "username": "root",
            "password": "x",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["token"], str) and body["token"]
    assert "_system" in body["databases"]
    assert body["token"] in _sessions


def test_connect_rejects_malformed_url(client: TestClient) -> None:
    # The Pydantic _url_shape validator runs before the SSRF guard —
    # a non-http scheme should land as a 422.
    resp = client.post(
        "/connect",
        json={
            "url": "ftp://example.com",
            "database": "_system",
            "username": "root",
            "password": "x",
        },
    )
    assert resp.status_code == 422


def test_disconnect_drops_session(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    token = _connect_session(client)
    assert token in _sessions
    resp = client.post("/disconnect", headers={"X-Arango-Session": token})
    assert resp.status_code == 200
    assert resp.json()["status"] == "disconnected"
    assert token not in _sessions


def test_connect_defaults_returns_env_shape(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARANGO_URL", "http://example.invalid:8529")
    monkeypatch.setenv("ARANGO_DB", "fixtures")
    monkeypatch.setenv("ARANGO_USER", "ronnie")
    monkeypatch.setenv("ARANGO_PASSWORD", "should_not_leak")
    monkeypatch.delenv("ARANGO_SPARQL_EXPOSE_DEFAULTS_PASSWORD", raising=False)

    resp = client.get("/connect/defaults")
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"] == "http://example.invalid:8529"
    assert body["database"] == "fixtures"
    assert body["username"] == "ronnie"
    # Password is hidden by default — the env-var leak mitigation.
    assert body["password"] == ""


def test_connect_defaults_can_expose_password(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARANGO_PASSWORD", "<test-stub-pw>")
    monkeypatch.setenv("ARANGO_SPARQL_EXPOSE_DEFAULTS_PASSWORD", "1")
    resp = client.get("/connect/defaults")
    assert resp.status_code == 200
    assert resp.json()["password"] == "<test-stub-pw>"
    # Reset for hygiene — autouse fixtures don't cover env vars.
    os.environ.pop("ARANGO_SPARQL_EXPOSE_DEFAULTS_PASSWORD", None)


def test_connect_defaults_canonical_password_wins_over_legacy(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both env vars set → canonical wins, no deprecation warning fires."""
    from arango_sparql import _env

    _env._reset_warning_state_for_tests()
    monkeypatch.setenv("ARANGO_PASSWORD", "canonical")
    monkeypatch.setenv("ARANGO_PASS", "should-be-ignored")
    monkeypatch.setenv("ARANGO_SPARQL_EXPOSE_DEFAULTS_PASSWORD", "1")
    import warnings as _w

    with _w.catch_warnings(record=True) as captured:
        _w.simplefilter("always")
        resp = client.get("/connect/defaults")
    assert resp.status_code == 200
    assert resp.json()["password"] == "canonical"
    assert not any(
        issubclass(w.category, DeprecationWarning) and "ARANGO_PASS" in str(w.message) for w in captured
    ), "canonical path must not log a deprecation"
    os.environ.pop("ARANGO_SPARQL_EXPOSE_DEFAULTS_PASSWORD", None)
    # Avoid leaving the warning state primed from this test so the next
    # test's deprecation assertion sees a clean once-per-process slate.
    _env._reset_warning_state_for_tests()


def test_connect_defaults_legacy_password_logs_deprecation(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only ``ARANGO_PASS`` set → value used, deprecation log line emitted."""
    import logging

    from arango_sparql import _env

    _env._reset_warning_state_for_tests()
    monkeypatch.delenv("ARANGO_PASSWORD", raising=False)
    monkeypatch.setenv("ARANGO_PASS", "legacy-only")
    monkeypatch.setenv("ARANGO_SPARQL_EXPOSE_DEFAULTS_PASSWORD", "1")
    with caplog.at_level(logging.WARNING, logger="arango_sparql"):
        resp = client.get("/connect/defaults")
    assert resp.status_code == 200
    assert resp.json()["password"] == "legacy-only"
    assert any(
        "ARANGO_PASS is deprecated" in rec.message and "connect_defaults" in rec.message
        for rec in caplog.records
    ), "legacy path must log a once-per-process deprecation tagged with the caller"
    os.environ.pop("ARANGO_SPARQL_EXPOSE_DEFAULTS_PASSWORD", None)
    _env._reset_warning_state_for_tests()


# ---------------------------------------------------------------------------
# /translate — schema-warnings projection (referenced IRI not in ontology)
# ---------------------------------------------------------------------------


def test_translate_emits_schema_warning_for_unmapped_property(client: TestClient) -> None:
    """A predicate IRI not declared in the ontology must surface as a
    ``W_SCHEMA_UNMAPPED_IRI`` advisory in both ``warnings`` and the
    convenience ``schema_warnings`` projection."""
    sparql = """
    PREFIX : <http://ex.org/>
    SELECT ?s ?email WHERE {
      ?s a :Person ;
         :email ?email .
    }
    """
    resp = client.post(
        "/translate",
        json={"sparql": sparql, "ontology_ttl": ONTOLOGY_TTL},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The warning must appear in both surfaces — the schema_warnings
    # projection is a duplicated view, not a replacement.
    schema_codes = [w["code"] for w in body["schema_warnings"]]
    all_codes = [w["code"] for w in body["warnings"]]
    assert "W_SCHEMA_UNMAPPED_IRI" in schema_codes
    assert "W_SCHEMA_UNMAPPED_IRI" in all_codes
    # The IRI + fallback are part of the structured payload so a UI can
    # render the unmapped IRI inline without scraping the message text.
    unmapped = [w for w in body["schema_warnings"] if w["code"] == "W_SCHEMA_UNMAPPED_IRI"][0]
    assert unmapped["iri"] == "http://ex.org/email"
    assert unmapped["fallback"] == "email"


def test_translate_no_schema_warnings_for_clean_ontology(client: TestClient) -> None:
    """A query whose every IRI is declared in the ontology yields an empty
    ``schema_warnings`` list — proves the projection isn't surfacing
    spurious advisories."""
    resp = client.post(
        "/translate",
        json={"sparql": SELECT_QUERY, "ontology_ttl": ONTOLOGY_TTL},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schema_warnings"] == []


# ---------------------------------------------------------------------------
# Multi-tenancy header forwarding (PRD §6.5.1)
# ---------------------------------------------------------------------------

# Ontology with one tenant-scoped class and one cross-tenant class.
# Re-declared locally so the routes are tested through the public
# request shape rather than the in-process visitor — the route layer
# is what consumes the ``X-Tenant-Id`` header and the
# ``ARANGO_SPARQL_DEFAULT_TENANT`` env fallback.
TENANT_ONTOLOGY_TTL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Person a owl:Class ;
    phys:collectionName "Person" ;
    phys:tenantField "tenant_id" ;
    phys:tenantEntity "Org" .

:ExternalAudit a owl:Class ;
    phys:collectionName "ExternalAudit" ;
    phys:tenantField "audit_tenant" ;
    phys:tenantEntity "ExternalOrg" .
"""

TENANT_SELECT_QUERY = """
PREFIX : <http://ex.org/>
SELECT ?s WHERE { ?s a :Person . }
"""

CROSS_TENANT_SELECT_QUERY = """
PREFIX : <http://ex.org/>
SELECT ?s ?a WHERE {
  ?s a :Person .
  ?a a :ExternalAudit .
}
"""


def test_translate_forwards_tenant_header_into_aql(client: TestClient) -> None:
    """``/translate`` must thread ``X-Tenant-Id`` through to the visitor
    so the emitted AQL carries ``FILTER doc.tenant_id == @<bind>`` and
    the bind value is the header's tenant id verbatim."""
    resp = client.post(
        "/translate",
        json={"sparql": TENANT_SELECT_QUERY, "ontology_ttl": TENANT_ONTOLOGY_TTL},
        headers={"X-Tenant-Id": "tenant-bravo"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "FILTER doc1.tenant_id == @" in body["aql"], body["aql"]
    tenant_binds = [k for k, v in body["bind_vars"].items() if v == "tenant-bravo"]
    assert tenant_binds, body["bind_vars"]


def test_translate_without_tenant_header_for_scoped_class_is_422(
    client: TestClient,
) -> None:
    """Tenant-scoped class + no ``X-Tenant-Id`` and no env fallback ⇒
    ``E_TRANSLATE_CROSS_TENANT_JOIN`` (the route mapping for the
    visitor's ``CrossTenantJoinError``)."""
    # Defensive — clear env so the test isn't dependent on ambient
    # ``ARANGO_SPARQL_DEFAULT_TENANT`` from the operator's shell.
    os.environ.pop("ARANGO_SPARQL_DEFAULT_TENANT", None)
    resp = client.post(
        "/translate",
        json={"sparql": TENANT_SELECT_QUERY, "ontology_ttl": TENANT_ONTOLOGY_TTL},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "E_TRANSLATE_CROSS_TENANT_JOIN"


def test_translate_falls_back_to_env_default_tenant(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no header is set, the route consults
    ``ARANGO_SPARQL_DEFAULT_TENANT`` so single-tenant deployments
    don't need to inject the header on every request."""
    monkeypatch.setenv("ARANGO_SPARQL_DEFAULT_TENANT", "env-tenant")
    resp = client.post(
        "/translate",
        json={"sparql": TENANT_SELECT_QUERY, "ontology_ttl": TENANT_ONTOLOGY_TTL},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "env-tenant" in body["bind_vars"].values(), body["bind_vars"]


def test_translate_header_wins_over_env_default(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Header beats env — so a multi-tenant deployment can still
    target a specific tenant per request even with the env-default
    set as a safety net."""
    monkeypatch.setenv("ARANGO_SPARQL_DEFAULT_TENANT", "env-tenant")
    resp = client.post(
        "/translate",
        json={"sparql": TENANT_SELECT_QUERY, "ontology_ttl": TENANT_ONTOLOGY_TTL},
        headers={"X-Tenant-Id": "header-tenant"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    bind_values = list(body["bind_vars"].values())
    assert "header-tenant" in bind_values
    assert "env-tenant" not in bind_values


def test_translate_cross_tenant_join_returns_422(client: TestClient) -> None:
    """Two classes under different ``phys:tenantEntity`` roots must
    surface ``E_TRANSLATE_CROSS_TENANT_JOIN`` even when the request
    supplies a tenant id — the violation is structural, not a missing
    context."""
    resp = client.post(
        "/translate",
        json={
            "sparql": CROSS_TENANT_SELECT_QUERY,
            "ontology_ttl": TENANT_ONTOLOGY_TTL,
        },
        headers={"X-Tenant-Id": "tenant-alpha"},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "E_TRANSLATE_CROSS_TENANT_JOIN"


def test_execute_forwards_tenant_header_into_aql(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    """``/execute`` must forward ``X-Tenant-Id`` the same way
    ``/translate`` does — verified by inspecting the AQL the fake
    driver receives, since the route's response surfaces the AQL
    verbatim only when the planner hands it back."""
    token = _connect_session(client)
    resp = client.post(
        "/execute",
        json={"sparql": TENANT_SELECT_QUERY, "ontology_ttl": TENANT_ONTOLOGY_TTL},
        headers={"X-Arango-Session": token, "X-Tenant-Id": "tenant-charlie"},
    )
    assert resp.status_code == 200, resp.text
    fake = fake_client_factory.instances[-1]
    fake_db = fake._dbs["_system"]
    assert fake_db.aql.last_aql is not None
    assert "FILTER doc1.tenant_id == @" in fake_db.aql.last_aql
    assert "tenant-charlie" in fake_db.aql.last_bind_vars.values()


def test_execute_cross_tenant_join_returns_422_before_db(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    """Cross-tenant violation must be caught at translate time so the
    DB never sees the query — same posture as
    ``test_execute_translation_error_returns_422_before_db``."""
    token = _connect_session(client)
    resp = client.post(
        "/execute",
        json={
            "sparql": CROSS_TENANT_SELECT_QUERY,
            "ontology_ttl": TENANT_ONTOLOGY_TTL,
        },
        headers={"X-Arango-Session": token, "X-Tenant-Id": "tenant-alpha"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "E_TRANSLATE_CROSS_TENANT_JOIN"
    fake = fake_client_factory.instances[-1]
    fake_db = fake._dbs["_system"]
    assert fake_db.aql.last_aql is None


def test_explain_forwards_tenant_header_into_aql(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    """``/explain`` must thread the tenant header so the planner
    output reflects the tenant-scoped AQL the operator would
    actually run — assert via the fake driver's
    ``last_explain_aql`` capture."""
    token = _connect_session(client)
    resp = client.post(
        "/explain",
        json={"sparql": TENANT_SELECT_QUERY, "ontology_ttl": TENANT_ONTOLOGY_TTL},
        headers={"X-Arango-Session": token, "X-Tenant-Id": "tenant-explain"},
    )
    assert resp.status_code == 200, resp.text
    fake = fake_client_factory.instances[-1]
    fake_db = fake._dbs["_system"]
    assert fake_db.aql.last_explain_aql is not None
    assert "FILTER doc1.tenant_id == @" in fake_db.aql.last_explain_aql
    assert "tenant-explain" in fake_db.aql.last_explain_bind_vars.values()


def test_profile_forwards_tenant_header_into_aql(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    """``/profile`` must thread the tenant header — the profiled AQL
    is the executed AQL, so the fake driver's ``last_aql`` capture
    is the contract surface."""
    token = _connect_session(client)
    resp = client.post(
        "/profile",
        json={"sparql": TENANT_SELECT_QUERY, "ontology_ttl": TENANT_ONTOLOGY_TTL},
        headers={"X-Arango-Session": token, "X-Tenant-Id": "tenant-profile"},
    )
    assert resp.status_code == 200, resp.text
    fake = fake_client_factory.instances[-1]
    fake_db = fake._dbs["_system"]
    assert fake_db.aql.last_aql is not None
    assert "FILTER doc1.tenant_id == @" in fake_db.aql.last_aql
    assert "tenant-profile" in fake_db.aql.last_bind_vars.values()
    assert fake_db.aql.last_profile == 2  # /profile sends profile=2


# ---------------------------------------------------------------------------
# /explain — translate + AQL EXPLAIN
# ---------------------------------------------------------------------------


def test_explain_happy_path(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    token = _connect_session(client)
    resp = client.post(
        "/explain",
        json={"sparql": SELECT_QUERY, "ontology_ttl": ONTOLOGY_TTL},
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # SPARQL is echoed back so the UI can pair the plan with the source.
    assert body["sparql"].strip().startswith("PREFIX")
    assert "FOR " in body["aql"]
    assert isinstance(body["bind_vars"], dict)
    assert body["plan"]["estimatedCost"] == 42.0
    assert body["plan"]["nodes"][0]["type"] == "SingletonNode"
    assert isinstance(body["warnings"], list)
    assert body["translate_ms"] is None or body["translate_ms"] >= 0
    fake = fake_client_factory.instances[-1]
    fake_db = fake._dbs["_system"]
    # The route forwards AQL + bind vars to ``db.aql.explain`` verbatim.
    assert fake_db.aql.last_explain_aql == body["aql"]
    assert fake_db.aql.last_explain_bind_vars == body["bind_vars"]


def test_explain_without_session_is_401(client: TestClient) -> None:
    resp = client.post(
        "/explain",
        json={"sparql": SELECT_QUERY, "ontology_ttl": ONTOLOGY_TTL},
    )
    assert resp.status_code == 401


def test_explain_translation_error_returns_422_before_db(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    token = _connect_session(client)
    resp = client.post(
        "/explain",
        json={"sparql": "SELECT WHERE { still broken", "ontology_ttl": ONTOLOGY_TTL},
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "E_SPARQL_PARSE"
    fake = fake_client_factory.instances[-1]
    fake_db = fake._dbs["_system"]
    # Translation failed → ``db.aql.explain`` must NOT have been called.
    assert fake_db.aql.last_explain_aql is None


# ---------------------------------------------------------------------------
# /profile — translate + AQL execute(profile=2) with cursor materialisation
# ---------------------------------------------------------------------------


def test_profile_happy_path(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    token = _connect_session(client)
    resp = client.post(
        "/profile",
        json={"sparql": SELECT_QUERY, "ontology_ttl": ONTOLOGY_TTL},
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bindings"] == [{"s": "u/1", "n": "Alice"}]
    assert "FOR " in body["aql"]
    # Profile blob is forwarded verbatim.
    assert "executing" in body["profile"]
    assert body["profile"]["executing"] == _FAKE_PROFILE["executing"]
    assert body["truncated"] is False
    # Sanity check: the route forwarded ``profile=2`` to the driver.
    fake = fake_client_factory.instances[-1]
    fake_db = fake._dbs["_system"]
    assert fake_db.aql.last_profile == 2


def test_profile_without_session_is_401(client: TestClient) -> None:
    resp = client.post(
        "/profile",
        json={"sparql": SELECT_QUERY, "ontology_ttl": ONTOLOGY_TTL},
    )
    assert resp.status_code == 401


def test_profile_translation_error_returns_422(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    token = _connect_session(client)
    resp = client.post(
        "/profile",
        json={"sparql": "SELECT WHERE { still broken", "ontology_ttl": ONTOLOGY_TTL},
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "E_SPARQL_PARSE"
    fake = fake_client_factory.instances[-1]
    fake_db = fake._dbs["_system"]
    assert fake_db.aql.last_aql is None


def test_profile_truncates_at_max_result_docs(
    client: TestClient,
    fake_client_factory: type[_FakeArangoClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cursor with more rows than ``_MAX_RESULT_DOCS`` must surface
    ``truncated=True`` plus the ``W_RESULT_TRUNCATED`` advisory."""
    monkeypatch.setattr("arango_sparql.service.routes.sparql._MAX_RESULT_DOCS", 3)
    _seed_fake_db_rows(
        monkeypatch,
        fake_client_factory,
        [{"s": f"u/{i}", "n": f"row{i}"} for i in range(10)],
    )
    token = _connect_session(client)
    resp = client.post(
        "/profile",
        json={"sparql": SELECT_QUERY, "ontology_ttl": ONTOLOGY_TTL},
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["bindings"]) == 3
    assert body["truncated"] is True
    assert any(w["code"] == "W_RESULT_TRUNCATED" for w in body["warnings"])
    # The profile blob still rides along — the truncation is a route-
    # layer concern, not a driver one.
    assert "executing" in body["profile"]
