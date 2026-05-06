"""Cheapest possible smoke test — proves the package imports and
the FastAPI app object exists. Mirror of
``arango-cypher-py/tests/test_packaging_smoke.py``.
"""

from __future__ import annotations


def test_package_imports() -> None:
    import arango_sparql

    assert arango_sparql.__version__


def test_service_app_constructs() -> None:
    from arango_sparql.service import app

    assert app.title == "Arango SPARQL Transpiler"


def test_health_route_registered() -> None:
    from fastapi.testclient import TestClient

    from arango_sparql.service import app

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_parse_sparql_roundtrip() -> None:
    """Confirm rdflib is wired correctly and our parser wrapper returns
    a :class:`ParsedSparql` with an algebra root carrying a ``.name``
    attribute plus the explicit projection list captured from the
    parse tree."""
    from arango_sparql.translate.parser import parse_sparql

    parsed = parse_sparql("SELECT ?s WHERE { ?s ?p ?o }")
    assert hasattr(parsed.algebra, "name")
    assert parsed.explicit_projection is not None
    assert [str(v) for v in parsed.explicit_projection] == ["s"]
