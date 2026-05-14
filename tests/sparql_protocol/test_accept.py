"""Accept-header negotiation tests for the W3C SPARQL Protocol
endpoint (PRD §5.2 result-format negotiation rules 1-4).

Asserts the **end-to-end** behaviour — the route layer actually
honours the parsed Accept header — beyond the unit-level coverage
in :mod:`tests.protocol.test_negotiate`.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from .conftest import ASK_QUERY, CONSTRUCT_QUERY, SELECT_QUERY, set_aql_rows

# ---------------------------------------------------------------------------
# Each supported SELECT media type — explicit ask
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "media_type",
    [
        "application/sparql-results+json",
        "application/sparql-results+xml",
        "text/csv",
        "text/tab-separated-values",
    ],
)
def test_select_explicit_accept_each_supported_type(
    client: TestClient,
    session_token: str,
    media_type: str,
) -> None:
    set_aql_rows(session_token, [{"x": "http://ex.org/Alice"}])
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": media_type,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["Content-Type"].startswith(media_type)


# ---------------------------------------------------------------------------
# Defaults — */* and missing Accept
# ---------------------------------------------------------------------------


def test_select_default_no_accept_header_is_json(
    client: TestClient, session_token: str
) -> None:
    set_aql_rows(session_token, [])
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={"X-Arango-Session": session_token},
    )
    assert resp.headers["Content-Type"].startswith(
        "application/sparql-results+json"
    )


def test_select_wildcard_accept_is_json(
    client: TestClient, session_token: str
) -> None:
    set_aql_rows(session_token, [])
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "*/*",
        },
    )
    assert resp.headers["Content-Type"].startswith(
        "application/sparql-results+json"
    )


# ---------------------------------------------------------------------------
# PRD §5.2 rule 2 — priority-list tie-break
# ---------------------------------------------------------------------------


def test_tie_break_prefers_priority_list_xml_over_csv(
    client: TestClient, session_token: str
) -> None:
    """Header lists CSV first then XML; q-values tie. XML wins
    because it ranks higher in the priority list. Canonical
    rule-2 example from PRD §5.2.
    """

    set_aql_rows(session_token, [{"x": "http://ex.org/Alice"}])
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": (
                "text/csv;q=0.9,application/sparql-results+xml;q=0.9"
            ),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["Content-Type"].startswith(
        "application/sparql-results+xml"
    )


def test_higher_q_overrides_priority_list(
    client: TestClient, session_token: str
) -> None:
    """A higher q-value always wins, even when the priority list
    would prefer a different type. PRD §5.2 rule 1.
    """

    set_aql_rows(session_token, [{"x": "http://ex.org/A"}])
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": (
                "application/sparql-results+json;q=0.5,text/csv;q=0.9"
            ),
        },
    )
    assert resp.headers["Content-Type"].startswith("text/csv")


# ---------------------------------------------------------------------------
# Wildcards
# ---------------------------------------------------------------------------


def test_application_wildcard_resolves_to_results_json(
    client: TestClient, session_token: str
) -> None:
    set_aql_rows(session_token, [])
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "application/*",
        },
    )
    assert resp.headers["Content-Type"].startswith(
        "application/sparql-results+json"
    )


def test_text_wildcard_resolves_to_csv(
    client: TestClient, session_token: str
) -> None:
    set_aql_rows(session_token, [])
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "text/*",
        },
    )
    assert resp.headers["Content-Type"].startswith("text/csv")


# ---------------------------------------------------------------------------
# 406 — no match
# ---------------------------------------------------------------------------


def test_unsupported_accept_returns_406_with_supported_list(
    client: TestClient, session_token: str
) -> None:
    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "image/png, application/pdf",
        },
    )
    assert resp.status_code == 406
    body = resp.json()
    assert body["code"] == "E_NOT_ACCEPTABLE"
    # Locks PRD §5.2 priority list ordering.
    assert body["supported_types"] == [
        "application/sparql-results+json",
        "application/sparql-results+xml",
        "text/csv",
        "text/tab-separated-values",
    ]
    assert body["query_form"] == "SELECT"
    assert resp.headers.get("Vary") == "Accept"


def test_406_response_content_type_is_application_json(
    client: TestClient, session_token: str
) -> None:
    """PRD §5.2 row 4: the 406 body lists supported types in
    ``Content-Type: application/json`` so spec-compliant clients
    can parse the diagnostic without guessing.
    """

    resp = client.get(
        "/sparql",
        params={"query": SELECT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "image/png",
        },
    )
    assert resp.status_code == 406
    assert resp.headers["Content-Type"].startswith("application/json")


# ---------------------------------------------------------------------------
# ASK — same priority list as SELECT
# ---------------------------------------------------------------------------


def test_ask_default_is_results_json(
    client: TestClient, session_token: str
) -> None:
    set_aql_rows(session_token, [True])
    resp = client.get(
        "/sparql",
        params={"query": ASK_QUERY},
        headers={"X-Arango-Session": session_token},
    )
    assert resp.headers["Content-Type"].startswith(
        "application/sparql-results+json"
    )
    assert json.loads(resp.text) == {"head": {}, "boolean": True}


def test_ask_unsupported_accept_returns_406(
    client: TestClient, session_token: str
) -> None:
    resp = client.get(
        "/sparql",
        params={"query": ASK_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "image/png",
        },
    )
    assert resp.status_code == 406
    body = resp.json()
    assert body["query_form"] == "ASK"
    assert "application/sparql-results+json" in body["supported_types"]


# ---------------------------------------------------------------------------
# CONSTRUCT / DESCRIBE — RDF formats negotiate and serialise
# ---------------------------------------------------------------------------


def test_construct_with_rdf_accept_returns_turtle(
    client: TestClient, session_token: str
) -> None:
    """CONSTRUCT now emits RDF triples — the route negotiates against
    :data:`CONSTRUCT_PRIORITY` (text/turtle / n-triples / rdf+xml /
    ld+json) and the rdflib-backed renderer flattens the visitor's
    per-row triple lists into a Turtle graph.
    """

    # CONSTRUCT_QUERY = ``CONSTRUCT { ?x a ex:Person } WHERE { ?x a ex:Person }``
    # The fake AQL row mirrors what the visitor's ``return_triples``
    # emits: a list of {subject, predicate, object} dicts per binding.
    set_aql_rows(
        session_token,
        [
            [
                {
                    "subject": "http://ex.org/Alice",
                    "predicate": (
                        "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
                    ),
                    "object": "http://ex.org/Person",
                }
            ]
        ],
    )
    resp = client.get(
        "/sparql",
        params={"query": CONSTRUCT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "text/turtle",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["Content-Type"].startswith("text/turtle")
    assert "Alice" in resp.text
    assert "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>" in resp.text or (
        ":Person" in resp.text or "Person" in resp.text
    )


def test_construct_with_select_only_accept_returns_406(
    client: TestClient, session_token: str
) -> None:
    """CONSTRUCT result formats are RDF (turtle / nt / rdf+xml /
    json-ld). A SELECT-results-only Accept is a no-match for
    CONSTRUCT and must 406 *before* the route hits the visitor.
    """

    resp = client.get(
        "/sparql",
        params={"query": CONSTRUCT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "application/sparql-results+json",
        },
    )
    assert resp.status_code == 406
    body = resp.json()
    assert body["query_form"] == "CONSTRUCT"
    assert body["supported_types"] == [
        "text/turtle",
        "application/n-triples",
        "application/rdf+xml",
        "application/ld+json",
    ]
