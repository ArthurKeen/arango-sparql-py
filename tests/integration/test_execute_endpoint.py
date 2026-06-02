"""End-to-end coverage of the ``/execute`` endpoint against a real
ArangoDB instance.

Boots ArangoDB via the repo's ``docker-compose.yml`` (best-effort;
skips cleanly if Docker isn't available), seeds a small ``Person``
collection, drives the FastAPI ``TestClient`` through the full
``/connect → /execute`` flow, and asserts the SPARQL bindings line up
with the seeded rows.

Gated behind the ``integration`` marker (declared in
``pyproject.toml``) and the ``RUN_INTEGRATION=1`` env var so the
default test loop stays fast. Run explicitly with::

    RUN_INTEGRATION=1 .venv/bin/pytest -q -m integration

The test deliberately uses :class:`fastapi.testclient.TestClient`
against the real :data:`arango_sparql.service.app` rather than
spawning a uvicorn process — same wiring as the production server
without the network-cost overhead, and the route-level dependency
chain (rate limit, session lookup, observability) is exercised
identically.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.integration

_ARANGO_HOST = os.getenv("ARANGO_HOST", "localhost")
# Match the host-side mapping in ``docker-compose.yml`` so the
# integration suite boots cleanly on machines where 8529 is already
# bound by another sibling-project container. Override with
# ``ARANGO_PORT`` / ``ARANGO_URL`` to point at an externally-managed
# ArangoDB.
_ARANGO_PORT = int(os.getenv("ARANGO_PORT", "8532"))
_ARANGO_URL = os.getenv("ARANGO_URL", f"http://{_ARANGO_HOST}:{_ARANGO_PORT}")
_ARANGO_USER = os.getenv("ARANGO_USER", "root")
_ARANGO_PASSWORD = os.getenv("ARANGO_PASSWORD", "rootpw")
# Dedicated test DB (never ``_system``); see tests/integration/conftest.py
# for the resolution order. Auto-provisioned by the fixture below.
_ARANGO_DB = os.getenv("ARANGO_TEST_DB") or os.getenv("ARANGO_DB") or "sparql-to-aql"

_TEST_COLLECTION = "Person"

_RUN_INTEGRATION = os.getenv("RUN_INTEGRATION", "").lower() in ("1", "true", "yes")


_PERSON_ONTOLOGY_TTL = """
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix :     <http://example.org/> .

:Person a owl:Class ;
    phys:collectionName "Person" .

:name a owl:DatatypeProperty ;
    rdfs:domain :Person ;
    rdfs:range  xsd:string .

:age a owl:DatatypeProperty ;
    rdfs:domain :Person ;
    rdfs:range  xsd:integer .
"""


def _arangodb_reachable(timeout_s: float = 1.0) -> bool:
    """Cheap TCP probe — avoids paying the python-arango client cost
    when ArangoDB isn't running and lets the fixture fall back to a
    ``docker compose up`` attempt.
    """
    try:
        with socket.create_connection((_ARANGO_HOST, _ARANGO_PORT), timeout=timeout_s):
            return True
    except OSError:
        return False


def _try_boot_arangodb_via_compose(*, timeout_s: float = 60.0) -> bool:
    """Best-effort ``docker compose up -d arangodb`` then poll
    ``/_api/version``. Returns ``True`` on a healthy database, ``False``
    if Docker isn't available or the boot times out.
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    compose_yml = os.path.join(repo_root, "docker-compose.yml")
    if not os.path.exists(compose_yml):
        return False
    try:
        subprocess.run(
            ["docker", "compose", "-f", compose_yml, "up", "-d", "arangodb"],
            check=True,
            capture_output=True,
            timeout=30.0,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _arangodb_reachable():
            # ArangoDB accepts TCP before it accepts authenticated
            # requests; sleep briefly to let the auth subsystem warm up.
            time.sleep(2.0)
            return True
        time.sleep(1.0)
    return False


@pytest.fixture(scope="module")
def _live_arango() -> Iterator[None]:
    """Module-scoped guard: skip every test in this file unless we can
    talk to ArangoDB. Boots it via ``docker compose`` on a best-effort
    basis when ``RUN_INTEGRATION=1`` is set.
    """
    if not _RUN_INTEGRATION:
        pytest.skip("set RUN_INTEGRATION=1 to enable integration tests")
    if not _arangodb_reachable():
        if not _try_boot_arangodb_via_compose():
            pytest.skip(f"ArangoDB at {_ARANGO_URL} is unreachable and could not be booted")
    yield


@pytest.fixture(scope="module")
def _seeded_collection(_live_arango: None) -> Iterator[list[dict]]:
    """Drop-and-recreate a small ``Person`` collection seeded with three
    rows. Module-scoped so the round-trip cost is paid once per file
    rather than per test; tests in this module are read-only against
    the seeded data so the shared state is safe.
    """
    from arango import ArangoClient

    from arango_sparql.arango_admin import ensure_database

    client = ArangoClient(hosts=_ARANGO_URL)
    # Provision the dedicated test database if it doesn't exist yet so a
    # fresh ``sparql-to-aql`` works without a manual setup step.
    if _ARANGO_DB != "_system":
        ensure_database(
            client, _ARANGO_DB, username=_ARANGO_USER, password=_ARANGO_PASSWORD
        )
    db = client.db(_ARANGO_DB, username=_ARANGO_USER, password=_ARANGO_PASSWORD)

    if db.has_collection(_TEST_COLLECTION):
        db.delete_collection(_TEST_COLLECTION)
    coll = db.create_collection(_TEST_COLLECTION)

    docs = [
        {"_uri": "http://example.org/alice", "name": "Alice", "age": 30},
        {"_uri": "http://example.org/bob", "name": "Bob", "age": 42},
        {"_uri": "http://example.org/carol", "name": "Carol", "age": 27},
    ]
    coll.insert_many(docs)

    try:
        yield docs
    finally:
        try:
            db.delete_collection(_TEST_COLLECTION)
        except Exception:
            # Best-effort teardown — a failed delete shouldn't mask a
            # real test failure upstream.
            pass
        client.close()


def _connect_session(client) -> str:
    """POST ``/connect`` and return the session token.

    Centralised so each test stays focused on the assertion under
    test rather than the auth handshake.
    """
    resp = client.post(
        "/connect",
        json={
            "url": _ARANGO_URL,
            "database": _ARANGO_DB,
            "username": _ARANGO_USER,
            "password": _ARANGO_PASSWORD,
        },
    )
    assert resp.status_code == 200, f"connect failed: {resp.status_code} {resp.text}"
    payload = resp.json()
    assert payload["token"]
    return payload["token"]


def test_connect_returns_session_token(_seeded_collection: list[dict]) -> None:
    """Smoke test for the auth handshake itself — ``/connect`` returns
    a non-empty token and a databases list visible to the connecting
    user.
    """
    from fastapi.testclient import TestClient

    from arango_sparql.service import app

    client = TestClient(app)
    token = _connect_session(client)
    assert token
    # /connections must show the new session (post-connect, pre-disconnect).
    resp = client.get("/connections")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] >= 1


def test_execute_returns_expected_bindings(_seeded_collection: list[dict]) -> None:
    """Full ``/connect`` → ``/execute`` flow against the seeded
    collection. Asserts:

    * 200 status with the documented response shape.
    * Three bindings (one per seeded ``Person`` row).
    * Each binding carries the ``?s`` (subject URI) and ``?n`` (name)
      slots projected by the SPARQL query.
    * The set of names matches the seeded fixtures (order-insensitive,
      since SPARQL ``SELECT`` semantics are bag/set rather than
      sequence).
    """
    from fastapi.testclient import TestClient

    from arango_sparql.service import app

    client = TestClient(app)
    token = _connect_session(client)

    sparql = "PREFIX : <http://example.org/> SELECT ?s ?n WHERE { ?s a :Person ; :name ?n }"
    resp = client.post(
        "/execute",
        headers={"Authorization": f"Bearer {token}"},
        json={"sparql": sparql, "ontology_ttl": _PERSON_ONTOLOGY_TTL},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["aql"]
    assert isinstance(payload["bind_vars"], dict)
    bindings = payload["bindings"]
    assert len(bindings) == len(_seeded_collection), bindings
    seen_names = {row.get("n") for row in bindings}
    assert seen_names == {row["name"] for row in _seeded_collection}


def test_execute_aql_pass_through(_seeded_collection: list[dict]) -> None:
    """Raw-AQL pass-through ``/execute-aql`` returns the same row count
    when run against the seeded collection without going through the
    SPARQL translator.
    """
    from fastapi.testclient import TestClient

    from arango_sparql.service import app

    client = TestClient(app)
    token = _connect_session(client)

    resp = client.post(
        "/execute-aql",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "aql": f"FOR doc IN {_TEST_COLLECTION} RETURN doc",
            "bind_vars": {},
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert len(payload["results"]) == len(_seeded_collection)
    assert payload["truncated"] is False
