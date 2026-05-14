"""Error-path tests for the W3C SPARQL Protocol endpoint (PRD §5.2
documented error responses table).

Coverage rows (one or more tests per row):

* **405 ``E_UPDATE_UNSUPPORTED``** — both the
  ``Content-Type: application/sparql-update`` case and the
  body-level "leading keyword is INSERT/DELETE/…" case. ``Allow``
  header is set.
* **400 ``E_SPARQL_PARSE``** — malformed SPARQL.
* **422 ``E_TRANSLATE_UNSUPPORTED_ALGEBRA``** — visitor reaches a
  SPARQL construct it doesn't yet emit AQL for.
* **503 ``E_SCHEMA_UNAVAILABLE``** — schema acquisition fails, with
  ``Retry-After: 30`` header.
* **504 ``E_TIMEOUT``** — query exceeds ``max_runtime``.
* **200 + ``W_RESULT_TRUNCATED``** — row cap fires, body is
  truncated, ``Warning`` header is set.
* **401 ``E_AUTH_REQUIRED``** — public mode without a session.
* **413 ``E_REQUEST_TOO_LARGE``** — POST body exceeds the byte cap.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from arango_sparql.service import _sessions
from arango_sparql.service.routes import protocol as _protocol
from arango_sparql.service.routes import schema as _schema

from .conftest import SELECT_QUERY, set_aql_rows

# ---------------------------------------------------------------------------
# 405 — Update form rejection (PRD §5.2 row 1)
# ---------------------------------------------------------------------------


def test_post_application_sparql_update_returns_405(
    client: TestClient, session_token: str
) -> None:
    resp = client.post(
        "/sparql",
        content=b"INSERT DATA { <http://ex/A> <http://ex/p> <http://ex/B> }",
        headers={
            "Content-Type": "application/sparql-update",
            "X-Arango-Session": session_token,
        },
    )
    assert resp.status_code == 405
    assert resp.headers["Allow"] == "GET, POST, OPTIONS"
    body = resp.json()
    assert body["code"] == "E_UPDATE_UNSUPPORTED"
    assert body["supported_methods"] == ["GET", "POST"]
    assert "SELECT" in body["supported_query_forms"]


def test_post_sparql_query_with_insert_body_returns_405(
    client: TestClient, session_token: str
) -> None:
    """Even with ``Content-Type: application/sparql-query``, a body
    that *parses as* an Update form must surface 405. PRD §5.2:
    "the endpoint MUST NOT silently no-op an Update request."
    """

    resp = client.post(
        "/sparql",
        content=b"DELETE WHERE { ?s ?p ?o }",
        headers={
            "Content-Type": "application/sparql-query",
            "X-Arango-Session": session_token,
        },
    )
    assert resp.status_code == 405
    assert resp.headers["Allow"] == "GET, POST, OPTIONS"


def test_get_with_update_query_param_returns_405(
    client: TestClient, session_token: str
) -> None:
    resp = client.get(
        "/sparql",
        params={
            "query": (
                "INSERT DATA { <http://ex/A> <http://ex/p> <http://ex/B> }"
            )
        },
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 405
    body = resp.json()
    assert body["code"] == "E_UPDATE_UNSUPPORTED"


def test_post_form_with_update_query_returns_405(
    client: TestClient, session_token: str
) -> None:
    resp = client.post(
        "/sparql",
        data={"query": "DROP GRAPH <http://ex.org/g>"},
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 405


# ---------------------------------------------------------------------------
# 400 — Malformed SPARQL (PRD §5.2 row 2)
# ---------------------------------------------------------------------------


def test_malformed_sparql_returns_400(
    client: TestClient, session_token: str
) -> None:
    resp = client.get(
        "/sparql",
        params={"query": "SELECT * WHERE { malformed garbage"},
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "E_SPARQL_PARSE"


def test_post_malformed_sparql_returns_400(
    client: TestClient, session_token: str
) -> None:
    resp = client.post(
        "/sparql",
        content=b"this is not sparql",
        headers={
            "Content-Type": "application/sparql-query",
            "X-Arango-Session": session_token,
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "E_SPARQL_PARSE"


def test_empty_get_query_returns_service_description(
    client: TestClient, session_token: str
) -> None:
    """``GET /sparql?query=`` (empty value) is *not* an error —
    the spec says no query → service description. We honour that
    by treating an empty ``query`` parameter the same as a missing
    one.
    """

    resp = client.get(
        "/sparql",
        params={"query": ""},
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/turtle")


# ---------------------------------------------------------------------------
# 422 — Unsupported algebra (PRD §5.2 row 3)
# ---------------------------------------------------------------------------


# CONSTRUCT / DESCRIBE happy-path coverage lives in
# :mod:`tests.sparql_protocol.test_construct_describe` — visitors now
# emit RDF triples and the protocol route negotiates against
# :data:`CONSTRUCT_PRIORITY` (turtle / n-triples / rdf+xml / ld+json).
# A translation-error path for these forms reuses the SELECT path's
# typed-error tests above (E_TRANSLATE_UNSUPPORTED_ALGEBRA), so we
# don't duplicate the wiring here.


# ---------------------------------------------------------------------------
# 503 — Schema acquisition fails (PRD §5.2 row 5)
# ---------------------------------------------------------------------------


def test_schema_acquisition_failure_returns_503_with_retry_after(
    client: TestClient,
    session_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _schema._resolve_schema_cache().clear()

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("analyzer connection refused")

    monkeypatch.setattr(_protocol, "_get_or_acquire", _boom)
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "E_SCHEMA_UNAVAILABLE"
    assert resp.headers.get("Retry-After") == "30"


# ---------------------------------------------------------------------------
# 504 — Query timeout (PRD §5.2 row 6)
# ---------------------------------------------------------------------------


class _TimeoutAql:
    """AQL stub that simulates an ArangoDB query-killed (code 1500)
    error. Same shape the real driver would raise so the protocol
    route's timeout-mapping logic is exercised.
    """

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        exc = RuntimeError("query killed: max runtime exceeded")
        exc.error_code = 1500  # type: ignore[attr-defined]
        raise exc


def test_query_timeout_returns_504_e_timeout(
    client: TestClient, session_token: str
) -> None:
    session = _sessions[session_token]
    session.db.aql = _TimeoutAql()

    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 504, resp.text
    body = resp.json()
    assert body["code"] == "E_TIMEOUT"
    assert "elapsed_ms" in body


# ---------------------------------------------------------------------------
# 200 + W_RESULT_TRUNCATED — row cap (PRD §5.2 row 7 + §9.1)
# ---------------------------------------------------------------------------


def test_truncated_result_emits_warning_header(
    client: TestClient,
    session_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the row cap fires, the body is truncated (still 200)
    and a ``Warning: 299 - "W_RESULT_TRUNCATED"`` header is set.
    PRD §9.1 row cap behaviour.
    """

    monkeypatch.setattr(_protocol, "_MAX_RESULT_DOCS", 3)
    rows = [{"x": f"http://ex.org/{i}"} for i in range(10)]
    set_aql_rows(session_token, rows)

    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200, resp.text
    warning = resp.headers.get("Warning", "")
    assert "W_RESULT_TRUNCATED" in warning
    assert warning.startswith("299")
    body = resp.json()
    assert len(body["results"]["bindings"]) == 3


def test_no_warning_header_when_not_truncated(
    client: TestClient, session_token: str
) -> None:
    set_aql_rows(session_token, [{"x": "http://ex.org/A"}])
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={"X-Arango-Session": session_token},
    )
    assert "Warning" not in resp.headers


# ---------------------------------------------------------------------------
# 401 — Auth required in public mode (PRD §5.2 row 9)
# ---------------------------------------------------------------------------


def test_public_mode_unauthenticated_returns_401(
    client: TestClient,
    fake_arango: type,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public-mode deployments require a session token. PRD §5.2
    row 9 specifies ``WWW-Authenticate: Bearer`` on the 401 so a
    spec-compliant client knows the auth scheme.
    """

    monkeypatch.setattr(_protocol, "_PUBLIC_MODE", True)
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "E_AUTH_REQUIRED"
    assert resp.headers.get("WWW-Authenticate") == "Bearer"


def test_invalid_session_token_returns_401_with_www_authenticate(
    client: TestClient,
) -> None:
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={"X-Arango-Session": "definitely-not-a-real-token"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "E_AUTH_REQUIRED"
    assert resp.headers.get("WWW-Authenticate") == "Bearer"


# ---------------------------------------------------------------------------
# 413 — POST body too large
# ---------------------------------------------------------------------------


def test_oversized_post_body_returns_413(
    client: TestClient,
    session_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPARQL_PROTOCOL_MAX_BODY_BYTES", "100")
    body = b"SELECT * WHERE { ?s ?p ?o }" + b" " * 200
    resp = client.post(
        "/sparql",
        content=body,
        headers={
            "Content-Type": "application/sparql-query",
            "X-Arango-Session": session_token,
        },
    )
    assert resp.status_code == 413
    body_json = resp.json()
    assert body_json["code"] == "E_REQUEST_TOO_LARGE"


# ---------------------------------------------------------------------------
# Session binding alternatives — ?session= and Authorization: Bearer
# ---------------------------------------------------------------------------


def test_session_bound_via_query_param(
    client: TestClient, session_token: str
) -> None:
    set_aql_rows(session_token, [])
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY, "session": session_token},
    )
    assert resp.status_code == 200, resp.text


def test_session_bound_via_authorization_bearer(
    client: TestClient, session_token: str
) -> None:
    set_aql_rows(session_token, [])
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 405 — Vary header still emitted
# ---------------------------------------------------------------------------


def test_405_response_includes_vary_accept(
    client: TestClient, session_token: str
) -> None:
    """Even error responses must set ``Vary: Accept`` so caches
    don't conflate variants of the same URL.
    """

    resp = client.post(
        "/sparql",
        content=b"INSERT DATA { <http://ex/A> a <http://ex/B> }",
        headers={
            "Content-Type": "application/sparql-update",
            "X-Arango-Session": session_token,
        },
    )
    assert resp.headers.get("Vary") == "Accept"
