"""The catalog model: schema analysis never blocks a request.

``service/schema_warm.py`` (ported from arango-cypher-py's catalog/warm.py)
moves the multi-minute analyzer off the request path. These tests replace the
suite-wide inline-warm seam (``tests/conftest.py``) with a *captured* thread,
so each case decides exactly when the background analysis "finishes" and can
assert what a request sees before and after.

Regression context: on prod.demo a cold ``/schema/introspect`` for ``IAM``
blocked for ~7 min (and ~11.6 min for the ``IAM_DEMO`` scope, which re-ran the
full analysis), leaving the Workbench on "Loading schema…".
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arango_sparql.service import _sessions, schema_warm
from arango_sparql.service.routes import schema as schema_route_mod
from arango_sparql.translate.mapping import MappingError
from tests.test_service_schema_routes import _bundle_pg

# Fixtures (client, session_token, stub_acquire, …) come from
# tests/service/conftest.py, shared with tests/test_service_schema_routes.py.

SELECT_QUERY = "PREFIX ex: <http://example.org/> SELECT ?p WHERE { ?p a ex:Person }"


class _Threads:
    """Background warms captured instead of started."""

    def __init__(self) -> None:
        self.pending: list[Callable[[], None]] = []

    def start(self, target: Callable[[], None], name: str) -> None:
        self.pending.append(target)

    def finish_all(self) -> None:
        while self.pending:
            self.pending.pop(0)()


@pytest.fixture
def threads(monkeypatch: pytest.MonkeyPatch) -> _Threads:
    t = _Threads()
    monkeypatch.setattr(schema_warm, "_start_thread", t.start)
    return t


def _headers(token: str) -> dict[str, str]:
    return {"X-Arango-Session": token}


def test_cold_introspect_answers_pending_at_once_then_ready(
    client: TestClient, session_token: str, stub_acquire: dict[str, Any], threads: _Threads
) -> None:
    resp = client.get("/schema/introspect", headers=_headers(session_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["warming"] is True
    assert body["mapping"] == {}
    assert body["warnings"][0]["code"] == "SCHEMA_PENDING"
    # The analyzer has NOT run on the request path.
    assert stub_acquire["calls"] == []
    assert len(threads.pending) == 1

    threads.finish_all()
    assert [c["graph_name"] for c in stub_acquire["calls"]] == [None]
    assert stub_acquire["calls"][0]["include_owl"] is True

    ready = client.get("/schema/introspect", headers=_headers(session_token)).json()
    assert ready["status"] == "ready"
    assert ready["warming"] is False
    assert ready["cache_hit"] is True
    assert ready["summary"]["entity_count"] >= 1


def test_polling_while_warming_never_starts_a_second_analysis(
    client: TestClient, session_token: str, stub_acquire: dict[str, Any], threads: _Threads
) -> None:
    for _ in range(5):
        assert client.get("/schema/introspect", headers=_headers(session_token)).json()["status"] == "pending"
    assert len(threads.pending) == 1


def test_a_graph_scope_is_ready_as_soon_as_the_database_is(
    client: TestClient, session_token: str, stub_acquire: dict[str, Any], threads: _Threads
) -> None:
    client.get("/schema/introspect", headers=_headers(session_token))
    threads.finish_all()
    _sessions[session_token].graph_name = "social"
    body = client.get("/schema/introspect", headers=_headers(session_token)).json()
    assert body["status"] == "ready"
    assert len(stub_acquire["calls"]) == 1  # no second analysis for the scope
    assert threads.pending == []


def test_expired_schema_is_served_while_it_refreshes_in_the_background(
    client: TestClient, session_token: str, stub_acquire: dict[str, Any], threads: _Threads
) -> None:
    stale = _bundle_pg("StalePerson")
    schema_route_mod._resolve_schema_cache().put("test_db", stale, now=datetime.now(UTC) - timedelta(days=2))
    stub_acquire["bundle"] = _bundle_pg("FreshPerson")

    body = client.get("/schema/introspect", headers=_headers(session_token)).json()
    assert body["status"] == "ready"
    assert body["warming"] is True
    assert "StalePerson" in str(body["mapping"])
    assert len(threads.pending) == 1

    threads.finish_all()
    fresh = client.get("/schema/introspect", headers=_headers(session_token)).json()
    assert fresh["warming"] is False
    assert "FreshPerson" in str(fresh["mapping"])


def test_refresh_keeps_serving_the_current_schema_until_the_new_one_lands(
    client: TestClient, session_token: str, stub_acquire: dict[str, Any], threads: _Threads
) -> None:
    schema_route_mod._resolve_schema_cache().put("test_db", _bundle_pg("OldPerson"))
    stub_acquire["bundle"] = _bundle_pg("NewPerson")

    body = client.get("/schema/introspect?force=true", headers=_headers(session_token)).json()
    assert body["status"] == "ready"
    assert body["warming"] is True
    assert body["cache_hit"] is False
    assert "OldPerson" in str(body["mapping"])
    assert stub_acquire["calls"] == []

    threads.finish_all()
    done = client.get("/schema/introspect", headers=_headers(session_token)).json()
    assert done["warming"] is False
    assert "NewPerson" in str(done["mapping"])


def test_a_failed_analysis_is_reported_once_then_retried(
    client: TestClient, session_token: str, stub_acquire: dict[str, Any], threads: _Threads
) -> None:
    stub_acquire["raise_exc"] = MappingError("bad bundle shape from the analyzer")
    assert client.get("/schema/introspect", headers=_headers(session_token)).json()["status"] == "pending"
    threads.finish_all()

    # Reported to the next caller with the route's usual mapping (422 + code)…
    failed = client.get("/schema/introspect", headers=_headers(session_token))
    assert failed.status_code == 422
    assert failed.json()["detail"]["code"]

    # …and the one after that retries instead of polling a dead warm forever.
    stub_acquire["raise_exc"] = None
    retry = client.get("/schema/introspect", headers=_headers(session_token)).json()
    assert retry["status"] == "pending"
    threads.finish_all()
    assert client.get("/schema/introspect", headers=_headers(session_token)).json()["status"] == "ready"


def test_owl_and_statistics_answer_pending_on_a_cold_database(
    client: TestClient, session_token: str, stub_acquire: dict[str, Any], threads: _Threads
) -> None:
    owl = client.get("/schema/owl", headers=_headers(session_token)).json()
    assert owl["status"] == "pending"
    assert owl["classes"] == []
    stats = client.get("/schema/statistics", headers=_headers(session_token)).json()
    assert stats["status"] == "pending"
    assert stats["available"] is False
    assert len(threads.pending) == 1  # one warm serves every endpoint
    assert stub_acquire["calls"] == []


def test_translate_never_waits_for_a_cold_analysis(
    client: TestClient, session_token: str, stub_acquire: dict[str, Any], threads: _Threads
) -> None:
    from tests.test_service_sparql_routes import ONTOLOGY_TTL
    from tests.test_service_sparql_routes import SELECT_QUERY as TRANSLATE_QUERY

    resp = client.post(
        "/translate",
        json={"sparql": TRANSLATE_QUERY, "ontology_ttl": ONTOLOGY_TTL},
        headers=_headers(session_token),
    )
    assert resp.status_code == 200, resp.text
    assert stub_acquire["calls"] == []  # enrichment skipped, not awaited
    assert len(threads.pending) == 1  # …but the warm was started


def test_sparql_protocol_answers_503_retry_after_while_analyzing(
    client: TestClient, session_token: str, stub_acquire: dict[str, Any], threads: _Threads
) -> None:
    resp = client.get("/sparql", params={"query": SELECT_QUERY}, headers=_headers(session_token))
    assert resp.status_code == 503
    assert resp.json()["code"] == "E_SCHEMA_UNAVAILABLE"
    assert resp.headers.get("Retry-After") == "30"
    assert stub_acquire["calls"] == []


def test_schedule_warm_is_deduped_and_releases_its_slot(threads: _Threads) -> None:
    class _Db:
        name = "dedupe_db"

    assert schema_warm.schedule_warm(_Db()) is True
    assert schema_warm.schedule_warm(_Db()) is False
    assert schema_warm.is_warming("dedupe_db") is True
    # The captured warm fails (no real database) — the slot is released and
    # the failure recorded for the next request.
    threads.finish_all()
    assert schema_warm.is_warming("dedupe_db") is False
    assert schema_warm.take_error("dedupe_db") is not None
    assert schema_warm.take_error("dedupe_db") is None


def test_schedule_warm_skips_a_handle_without_a_name(threads: _Threads) -> None:
    class _Nameless:
        @property
        def name(self) -> str:
            raise RuntimeError("closed")

    assert schema_warm.schedule_warm(_Nameless()) is False
    assert threads.pending == []
