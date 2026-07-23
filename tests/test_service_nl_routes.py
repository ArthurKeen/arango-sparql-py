"""End-to-end tests for the NL → SPARQL FastAPI route surface.

Mirrors the posture of :mod:`tests.test_service_sparql_routes`:

* drives the real ``app`` via :class:`fastapi.testclient.TestClient`,
* mocks the LLM client via ``app.dependency_overrides`` so no real
  network call ever fires,
* monkeypatches the package-level ``ArangoClient`` symbol to a fake
  for the ``/nl-execute`` happy-path test (same fixture pattern as
  the SPARQL routes test),
* asserts on the frozen API contract: response field names, error
  codes, and the 422 envelope shape that the round-3 frontend
  consumes.

The eval-style tests (real LLM, real DB) live behind the
``@pytest.mark.eval`` and ``@pytest.mark.integration`` markers and
are NOT exercised here — this file is the contract gate, not the
quality gate.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import arango_sparql.service as svc
from arango_sparql.nl2sparql import LLMResponse, ScriptedLLMClient
from arango_sparql.service import _sessions, app
from arango_sparql.service.routes.nl import _llm_client_factory

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
"""

GOOD_SPARQL = """
PREFIX : <http://ex.org/>
SELECT ?s ?n WHERE {
  ?s a :Person ;
     :name ?n .
}
LIMIT 5
""".strip()

BAD_SPARQL = "SELECT WHERE { broken"


def _wrap(sparql: str) -> str:
    return f"```sparql\n{sparql}\n```"


def _llm(content: str, *, prompt: int = 100, completion: int = 50) -> LLMResponse:
    return LLMResponse(
        content=content,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


@pytest.fixture(autouse=True)
def _reset_rate_limits(monkeypatch: pytest.MonkeyPatch):
    """Install fresh token buckets per test so rate-limit state can't leak.

    Default NL bucket is 10/min — across the whole test file we'd
    blow past it on the third test in CI. Installing a high-capacity
    fresh bucket on the package object (which ``_resolve_bucket``
    consults) keeps every individual test deterministic.
    """
    from arango_sparql.service.security import _TokenBucket

    monkeypatch.setattr(svc, "_nl_bucket", _TokenBucket(10_000))
    monkeypatch.setattr(svc, "_compute_bucket", _TokenBucket(10_000))
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def override_llm():
    """Helper that swaps the route's LLM client factory with a scripted client.

    Returns a callable that mounts a :class:`ScriptedLLMClient` for
    the duration of the test. We tear down the override on teardown so
    a per-test mock doesn't leak into the next test in the file.
    """
    installed: list[ScriptedLLMClient] = []

    def _install(responses: list[LLMResponse | BaseException], **kwargs: Any) -> ScriptedLLMClient:
        sc = ScriptedLLMClient(responses, latency_ms=0, **kwargs)
        app.dependency_overrides[_llm_client_factory] = lambda: sc
        installed.append(sc)
        return sc

    yield _install

    app.dependency_overrides.pop(_llm_client_factory, None)
    installed.clear()


# ---------------------------------------------------------------------------
# Fake python-arango stack — copied from test_service_sparql_routes for the
# /nl-execute path. The pattern is the contract; do not rebuild it in a way
# that diverges from the SPARQL-route fixture.
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)
        self._iter = iter(self._rows)

    def __iter__(self):
        return self._iter

    def __next__(self):
        return next(self._iter)


class _FakeAql:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db
        self.last_aql: str | None = None
        self.last_bind_vars: dict[str, Any] | None = None

    def execute(self, aql: str, bind_vars: dict[str, Any] | None = None) -> _FakeCursor:
        self.last_aql = aql
        self.last_bind_vars = dict(bind_vars or {})
        return _FakeCursor(self._db.rows)


class _FakeDb:
    def __init__(self, name: str, rows: list[Any] | None = None) -> None:
        self.name = name
        self.rows: list[Any] = rows if rows is not None else [{"s": "Person/1", "n": "Alice"}]
        self.aql = _FakeAql(self)

    def version(self) -> str:
        return "3.12.0"

    def databases(self) -> list[str]:
        return ["_system"]


class _FakeArangoClient:
    instances: list[_FakeArangoClient] = []

    def __init__(self, hosts: str = "") -> None:
        self.hosts = hosts
        self._dbs: dict[str, _FakeDb] = {}
        _FakeArangoClient.instances.append(self)

    def db(self, name: str, username: str | None = None, password: str | None = None) -> _FakeDb:
        if name not in self._dbs:
            self._dbs[name] = _FakeDb(name)
        return self._dbs[name]

    def close(self) -> None:
        pass


@pytest.fixture
def fake_client_factory(monkeypatch: pytest.MonkeyPatch):
    _FakeArangoClient.instances.clear()
    monkeypatch.setattr(svc, "ArangoClient", _FakeArangoClient)
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
# /nl-translate
# ---------------------------------------------------------------------------


def test_nl_translate_happy_path(client: TestClient, override_llm) -> None:
    sc = override_llm([_llm(_wrap(GOOD_SPARQL))])
    resp = client.post(
        "/nl-translate",
        json={"nl": "find people with names", "ontology_ttl": ONTOLOGY_TTL},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nl"] == "find people with names"
    assert body["sparql"].startswith("PREFIX")
    assert "FOR " in body["aql"]
    assert isinstance(body["bind_vars"], dict)
    assert body["llm_calls"] == 1
    assert body["repaired"] is False
    assert body["cost_usd"] >= 0.0
    assert body["latency_ms"] >= 0
    # The scripted client must have been called exactly once.
    assert len(sc.calls) == 1


def test_nl_translate_translation_error_returns_422(client: TestClient, override_llm) -> None:
    """Repeated bad SPARQL through the repair loop ends in a 422."""
    override_llm(
        [_llm(_wrap(BAD_SPARQL)), _llm(_wrap(BAD_SPARQL)), _llm(_wrap(BAD_SPARQL))],
    )
    resp = client.post(
        "/nl-translate",
        json={"nl": "find people", "ontology_ttl": ONTOLOGY_TTL, "max_repairs": 2},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] in (
        "W_NL_TRANSLATION_FAILED",
        "E_SPARQL_PARSE",
        "E_SPARQL_UNSUPPORTED",
    )
    # The frozen contract: 422 surfaces the same provenance fields as
    # the 200 path so a UI banner can show "we tried N times" without
    # a second round-trip.
    assert detail["nl"] == "find people"
    assert detail["llm_calls"] == 3
    assert detail["repaired"] is True


def test_nl_translate_repair_succeeds_marks_repaired(client: TestClient, override_llm) -> None:
    """First LLM response is bad SPARQL; second is correct → 200 with repaired=True."""
    override_llm([_llm(_wrap(BAD_SPARQL)), _llm(_wrap(GOOD_SPARQL))])
    resp = client.post(
        "/nl-translate",
        json={"nl": "people with names", "ontology_ttl": ONTOLOGY_TTL, "max_repairs": 2},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["aql"]
    assert body["repaired"] is True
    assert body["llm_calls"] == 2
    # The W_NL_REPAIRED warning must surface so the UI can render the badge.
    assert any(w.get("code") == "W_NL_REPAIRED" for w in body["warnings"])


def test_nl_translate_no_provider_returns_503(client: TestClient) -> None:
    """No LLM provider configured → 503 with the canonical error code."""
    # Mount an override that returns None to exercise the unconfigured path.
    app.dependency_overrides[_llm_client_factory] = lambda: None
    try:
        resp = client.post(
            "/nl-translate",
            json={"nl": "anything", "ontology_ttl": ONTOLOGY_TTL},
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "E_NL_PROVIDER_UNAVAILABLE"
    finally:
        app.dependency_overrides.pop(_llm_client_factory, None)


def test_nl_translate_oversized_nl_rejected_at_pydantic(client: TestClient, override_llm) -> None:
    """The Pydantic ``max_length`` envelope rejects 4001-char NL bodies before the LLM."""
    override_llm([_llm(_wrap(GOOD_SPARQL))])
    resp = client.post(
        "/nl-translate",
        json={"nl": "x" * 5000, "ontology_ttl": ONTOLOGY_TTL},
    )
    assert resp.status_code == 422


def test_nl_translate_empty_nl_rejected_at_pydantic(client: TestClient, override_llm) -> None:
    """``min_length=1`` envelope rejects empty NL bodies."""
    override_llm([_llm(_wrap(GOOD_SPARQL))])
    resp = client.post("/nl-translate", json={"nl": "", "ontology_ttl": ONTOLOGY_TTL})
    assert resp.status_code == 422


def test_nl_translate_malformed_ontology_returns_422(client: TestClient, override_llm) -> None:
    override_llm([_llm(_wrap(GOOD_SPARQL))])
    resp = client.post(
        "/nl-translate",
        json={"nl": "find people", "ontology_ttl": "this is not turtle :{("},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "E_SCHEMA_RESOLVE"


# ---------------------------------------------------------------------------
# /nl-explain
# ---------------------------------------------------------------------------


def test_nl_explain_happy_path(client: TestClient, override_llm) -> None:
    """NL path: translate then explain — two LLM calls, both surface in the response."""
    override_llm(
        [
            _llm(_wrap(GOOD_SPARQL)),
            _llm("This query selects every person and their name."),
        ],
    )
    resp = client.post(
        "/nl-explain",
        json={"nl": "people with names", "ontology_ttl": ONTOLOGY_TTL},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["aql"]
    assert body["explanation"].startswith("This query selects")
    assert body["llm_calls"] == 2


def test_nl_explain_sparql_only_skips_translation_call(client: TestClient, override_llm) -> None:
    """sparql-only path runs the deterministic translator (no LLM call) + 1 explain call."""
    sc = override_llm([_llm("Selects ?s and ?n for every Person.")])
    resp = client.post(
        "/nl-explain",
        json={"sparql": GOOD_SPARQL, "ontology_ttl": ONTOLOGY_TTL},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["aql"]
    assert body["explanation"].startswith("Selects")
    assert body["llm_calls"] == 1
    assert len(sc.calls) == 1


def test_nl_explain_empty_input_is_422(client: TestClient, override_llm) -> None:
    override_llm([_llm("ignored")])
    resp = client.post("/nl-explain", json={"ontology_ttl": ONTOLOGY_TTL})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "E_NL_EXPLAIN_INPUT"


# ---------------------------------------------------------------------------
# /nl-execute
# ---------------------------------------------------------------------------


def test_nl_execute_without_session_is_401(client: TestClient, override_llm) -> None:
    override_llm([_llm(_wrap(GOOD_SPARQL))])
    resp = client.post(
        "/nl-execute",
        json={"nl": "find people", "ontology_ttl": ONTOLOGY_TTL},
    )
    assert resp.status_code == 401


def test_nl_execute_happy_path(
    client: TestClient,
    override_llm,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    override_llm([_llm(_wrap(GOOD_SPARQL))])
    token = _connect_session(client)
    resp = client.post(
        "/nl-execute",
        json={"nl": "find people with names", "ontology_ttl": ONTOLOGY_TTL},
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bindings"] == [{"s": "Person/1", "n": "Alice"}]
    assert "FOR " in body["aql"]
    assert body["truncated"] is False
    assert body["exec_ms"] >= 0
    fake = fake_client_factory.instances[-1]
    fake_db = fake._dbs["_system"]
    assert fake_db.aql.last_aql == body["aql"]
    assert fake_db.aql.last_bind_vars == body["bind_vars"]


def test_nl_execute_translation_failure_returns_422_before_db(
    client: TestClient,
    override_llm,
    fake_client_factory: type[_FakeArangoClient],
) -> None:
    """Repair-exhausted translation must NOT touch the database."""
    override_llm([_llm(_wrap(BAD_SPARQL)), _llm(_wrap(BAD_SPARQL)), _llm(_wrap(BAD_SPARQL))])
    token = _connect_session(client)
    resp = client.post(
        "/nl-execute",
        json={"nl": "find people", "ontology_ttl": ONTOLOGY_TTL, "max_repairs": 2},
        headers={"X-Arango-Session": token},
    )
    assert resp.status_code == 422
    fake = fake_client_factory.instances[-1]
    fake_db = fake._dbs["_system"]
    assert fake_db.aql.last_aql is None, "AQL must not have run on translation failure"


# ---------------------------------------------------------------------------
# /nl-samples
# ---------------------------------------------------------------------------


def test_nl_samples_rule_based_without_provider(client: TestClient) -> None:
    """No LLM provider → 200 with deterministic rule-based suggestions.

    Unlike /nl-translate, suggestions degrade gracefully so the "Ask"
    dropdown is populated the moment a schema is present.
    """
    app.dependency_overrides[_llm_client_factory] = lambda: None
    try:
        resp = client.post("/nl-samples", json={"ontology_ttl": ONTOLOGY_TTL})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["queries"], "rule-based path must yield suggestions"
        assert any("person" in q.lower() for q in body["queries"])
        assert body["elapsed_ms"] >= 0.0
    finally:
        app.dependency_overrides.pop(_llm_client_factory, None)


def test_nl_samples_uses_llm_when_provider_present(client: TestClient, override_llm) -> None:
    sc = override_llm([_llm("Who has a name?\nList every person")])
    resp = client.post("/nl-samples", json={"ontology_ttl": ONTOLOGY_TTL, "count": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["queries"] == ["Who has a name?", "List every person"]
    assert len(sc.calls) == 1


def test_nl_samples_use_llm_false_is_rule_based_even_with_provider(client: TestClient, override_llm) -> None:
    sc = override_llm([_llm("Should not be used")])
    resp = client.post("/nl-samples", json={"ontology_ttl": ONTOLOGY_TTL, "use_llm": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["queries"]
    assert "Should not be used" not in body["queries"]
    # The model must not have been consulted on the rule-based path.
    assert sc.calls == []


def test_nl_samples_empty_ontology_returns_empty_list(client: TestClient) -> None:
    app.dependency_overrides[_llm_client_factory] = lambda: None
    try:
        resp = client.post("/nl-samples", json={})
        assert resp.status_code == 200, resp.text
        assert resp.json()["queries"] == []
    finally:
        app.dependency_overrides.pop(_llm_client_factory, None)


# ---------------------------------------------------------------------------
# OpenAPI smoke — make sure the routes are wired into the schema
# ---------------------------------------------------------------------------


def test_openapi_includes_all_nl_routes(client: TestClient) -> None:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/nl-translate" in paths
    assert "/nl-explain" in paths
    assert "/nl-execute" in paths
    assert "/nl-samples" in paths
