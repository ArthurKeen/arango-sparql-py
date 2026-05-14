"""End-to-end happy-path tests for the W3C SPARQL Protocol's
CONSTRUCT and DESCRIBE forms (PRD §5.2 result-format negotiation
paragraph + §13.5 supported-algebra table).

Coverage:

* CONSTRUCT returns RDF in every supported RDF wire format
  (text/turtle, application/n-triples, application/rdf+xml,
  application/ld+json).
* CONSTRUCT default ``Accept`` (``*/*``) resolves to text/turtle —
  the first entry in :data:`CONSTRUCT_PRIORITY`.
* DESCRIBE ``?s`` form emits the document-attribute fan-out the
  visitor builds (``ATTRIBUTES(...)`` sub-FOR) and the renderer
  hydrates each attribute-row dict into a triple.
* DESCRIBE bare ``<iri>`` form (no WHERE) returns the legacy-Foxx
  attribute-fan-out shape.
* Observability headers (``Vary: Accept``, ``X-Aql-Bindings-Count``)
  remain stamped on the RDF responses the same way they are on
  SELECT/ASK.

Fixtures live in :mod:`tests.sparql_protocol.conftest`.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from rdflib import Graph, URIRef

from .conftest import set_aql_rows

# ---------------------------------------------------------------------------
# Helpers — canonical fake-AQL row shapes
# ---------------------------------------------------------------------------

# CONSTRUCT { ?x foaf:name ?n } WHERE { ?x a :Person ; :name ?n }
# would yield one row per matched Person, each carrying a list of
# the (subject, predicate, object) dicts the visitor emits.
_CONSTRUCT_ROWS = [
    [
        {
            "subject": "http://ex.org/Alice",
            "predicate": "http://xmlns.com/foaf/0.1/name",
            "object": "Alice",
        }
    ],
    [
        {
            "subject": "http://ex.org/Bob",
            "predicate": "http://xmlns.com/foaf/0.1/name",
            "object": "Bob",
        }
    ],
]

# DESCRIBE ?s emits a nested attribute-fan-out — each cursor row is
# the sub-FOR's list of attribute triples.
_DESCRIBE_ROWS = [
    [
        {
            "subject": "http://ex.org/Alice",
            "predicate": "name",
            "object": "Alice",
        },
        {
            "subject": "http://ex.org/Alice",
            "predicate": "age",
            "object": 30,
        },
    ]
]


# Same CONSTRUCT/DESCRIBE query strings the visitor's unit tests use,
# so we exercise the full route → translate → execute → render stack.
_CONSTRUCT_QUERY = (
    "PREFIX foaf: <http://xmlns.com/foaf/0.1/> "
    "PREFIX ex: <http://ex.org/> "
    "CONSTRUCT { ?x foaf:name ?x } WHERE { ?x a ex:Person }"
)

_DESCRIBE_VAR_QUERY = (
    "PREFIX ex: <http://ex.org/> "
    "DESCRIBE ?x WHERE { ?x a ex:Person }"
)

_DESCRIBE_BARE_QUERY = "DESCRIBE <http://ex.org/Alice>"


# ---------------------------------------------------------------------------
# CONSTRUCT — all four wire formats
# ---------------------------------------------------------------------------


def test_construct_turtle(
    client: TestClient, session_token: str
) -> None:
    set_aql_rows(session_token, _CONSTRUCT_ROWS)
    resp = client.get(
        "/sparql",
        params={"query": _CONSTRUCT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "text/turtle",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["Content-Type"].startswith("text/turtle")
    # rdflib round-trips the response to assert the graph shape rather
    # than asserting against a brittle string.
    g = Graph()
    g.parse(data=resp.text, format="turtle")
    foaf_name = URIRef("http://xmlns.com/foaf/0.1/name")
    assert (URIRef("http://ex.org/Alice"), foaf_name, None) in (
        (s, p, None) for s, p, _ in g
    )
    assert len(g) == 2  # Alice + Bob


def test_construct_n_triples(
    client: TestClient, session_token: str
) -> None:
    set_aql_rows(session_token, _CONSTRUCT_ROWS)
    resp = client.get(
        "/sparql",
        params={"query": _CONSTRUCT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "application/n-triples",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["Content-Type"].startswith("application/n-triples")
    # N-Triples is line-delimited; every line ends with " ." (RFC's
    # eol form). Two triples → two non-empty lines.
    lines = [
        line for line in resp.text.splitlines() if line.strip() and not line.startswith("#")
    ]
    assert len(lines) == 2
    for line in lines:
        assert line.endswith(" .")
        assert "<http://xmlns.com/foaf/0.1/name>" in line


def test_construct_rdf_xml(
    client: TestClient, session_token: str
) -> None:
    set_aql_rows(session_token, _CONSTRUCT_ROWS)
    resp = client.get(
        "/sparql",
        params={"query": _CONSTRUCT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "application/rdf+xml",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["Content-Type"].startswith("application/rdf+xml")
    # Parse as XML — defensive check that the body is well-formed
    # rather than only testing rdflib's output verbatim.
    root = ET.fromstring(resp.text)
    assert root.tag.endswith("}RDF")


def test_construct_ld_json(
    client: TestClient, session_token: str
) -> None:
    set_aql_rows(session_token, _CONSTRUCT_ROWS)
    resp = client.get(
        "/sparql",
        params={"query": _CONSTRUCT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "application/ld+json",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["Content-Type"].startswith("application/ld+json")
    payload = json.loads(resp.text)
    # rdflib emits either an array of node objects or an
    # ``@graph``-wrapped object; both shapes are valid JSON-LD.
    assert isinstance(payload, (list, dict))


def test_construct_default_accept_returns_turtle(
    client: TestClient, session_token: str
) -> None:
    """``Accept: */*`` and an omitted Accept both resolve to the first
    entry in :data:`CONSTRUCT_PRIORITY` — text/turtle (PRD §5.2
    rule 3).
    """

    set_aql_rows(session_token, _CONSTRUCT_ROWS)
    resp = client.get(
        "/sparql",
        params={"query": _CONSTRUCT_QUERY},
        headers={"X-Arango-Session": session_token},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["Content-Type"].startswith("text/turtle")


def test_construct_stamps_observability_headers(
    client: TestClient, session_token: str
) -> None:
    set_aql_rows(session_token, _CONSTRUCT_ROWS)
    resp = client.get(
        "/sparql",
        params={"query": _CONSTRUCT_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "text/turtle",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("Vary") == "Accept"
    # Bindings count reflects the AQL cursor rows (one per matched
    # Person), not the post-flattening triple count.
    assert resp.headers.get("X-Aql-Bindings-Count") == "2"
    assert "X-Response-Time" in resp.headers


# ---------------------------------------------------------------------------
# DESCRIBE — WHERE-bound variable + bare-IRI shapes
# ---------------------------------------------------------------------------


def test_describe_var_returns_attribute_triples(
    client: TestClient, session_token: str
) -> None:
    """``DESCRIBE ?x WHERE { … }`` emits attribute-fan-out triples;
    the renderer hydrates plain string values into IRIs (when shaped
    like a URI) or literals.
    """

    set_aql_rows(session_token, _DESCRIBE_ROWS)
    resp = client.get(
        "/sparql",
        params={"query": _DESCRIBE_VAR_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "text/turtle",
        },
    )
    assert resp.status_code == 200, resp.text
    g = Graph()
    g.parse(data=resp.text, format="turtle")
    # Two attributes (name, age) → two triples.
    assert len(g) == 2
    # ``name`` (string) → string literal; ``age`` (int) → xsd:integer
    # literal — rdflib infers the datatype from the Python type our
    # renderer passes to ``Literal()``.
    objects = {str(o) for _, _, o in g}
    assert "Alice" in objects
    assert "30" in objects


def test_describe_bare_iri_returns_turtle(
    client: TestClient, session_token: str
) -> None:
    """``DESCRIBE <iri>`` (no WHERE) emits a default-collection FOR;
    the route layer still renders to RDF the same way.
    """

    set_aql_rows(session_token, _DESCRIBE_ROWS)
    resp = client.get(
        "/sparql",
        params={"query": _DESCRIBE_BARE_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "text/turtle",
        },
    )
    assert resp.status_code == 200, resp.text
    g = Graph()
    g.parse(data=resp.text, format="turtle")
    assert len(g) == 2


def test_describe_empty_result(
    client: TestClient, session_token: str
) -> None:
    """A DESCRIBE whose WHERE binds no resources returns an empty
    graph (not a 404). The W3C protocol spec is silent on the
    distinction but every server we've audited (Fuseki, Stardog,
    Virtuoso) treats no-match as ``200`` + empty graph.
    """

    set_aql_rows(session_token, [])
    resp = client.get(
        "/sparql",
        params={"query": _DESCRIBE_VAR_QUERY},
        headers={
            "X-Arango-Session": session_token,
            "Accept": "text/turtle",
        },
    )
    assert resp.status_code == 200, resp.text
    g = Graph()
    g.parse(data=resp.text, format="turtle")
    assert len(g) == 0


# ---------------------------------------------------------------------------
# CONSTRUCT — POST body and form-encoded paths still RDF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type, body",
    [
        ("application/sparql-query", _CONSTRUCT_QUERY),
        (
            "application/x-www-form-urlencoded",
            f"query={_CONSTRUCT_QUERY.replace(' ', '+').replace('<', '%3C').replace('>', '%3E')}",
        ),
    ],
    ids=["sparql-query-body", "form-encoded"],
)
def test_construct_post_returns_turtle(
    client: TestClient,
    session_token: str,
    content_type: str,
    body: str,
) -> None:
    set_aql_rows(session_token, _CONSTRUCT_ROWS)
    resp = client.post(
        "/sparql",
        content=body,
        headers={
            "X-Arango-Session": session_token,
            "Accept": "text/turtle",
            "Content-Type": content_type,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["Content-Type"].startswith("text/turtle")
    g = Graph()
    g.parse(data=resp.text, format="turtle")
    assert len(g) == 2
