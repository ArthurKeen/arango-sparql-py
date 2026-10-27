"""Issue #72 — a nested path and a literal dotted name, against real documents.

``phys:attributePath '["address", "city"]'`` reads ``city`` inside the object
``address``; ``phys:attributeName "address.city"`` reads the one attribute
with that exact name. The existence check and the value read must take the
same meaning, so no row is dropped or invented.

Live because the semantics are ArangoDB's: ``IS_OBJECT`` on a list or a
string, a read through a missing level returning null. Every query also runs
through pyoxigraph on the triples the documents stand for, the W3C ground
truth, and the answers must agree. A list where an object is expected counts
as absent (no triple) in this first version. Gated like the rest of the
integration suite (``RUN_INTEGRATION=1``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.oxi import drop_null_bindings, normalize_oxi_row, oxi_bindings
from tests.integration.conftest import (
    DEFAULT_ARANGO_DB,
    DEFAULT_ARANGO_PASSWORD,
    DEFAULT_ARANGO_URL,
    DEFAULT_ARANGO_USER,
    arangodb_reachable,
    ensure_test_database,
    integration_enabled,
    try_boot_arangodb_via_compose,
)

pytestmark = pytest.mark.integration

oxi = pytest.importorskip("pyoxigraph", reason="pyoxigraph is the ground truth for these tests")

EX = "http://example.org/"
_PEOPLE = "Issue72People"

_TTL = f"""
@prefix :     <{EX}> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:mappingStyle "COLLECTION" ; phys:collectionName "{_PEOPLE}" .
:name a owl:DatatypeProperty .
:city a owl:DatatypeProperty ; phys:attributePath '["address", "city"]' .
:dottedCity a owl:DatatypeProperty ; phys:attributeName "address.city" .
"""

# The issue's two shapes (p1, p2), one document holding both (p3), and the
# shapes that must count as "no nested city": a list where the object should
# be (p4), a string (p5), no address (p6), an empty object (p7).
_DOCS: list[dict[str, Any]] = [
    {"_key": "p1", "_uri": EX + "p1", "name": "Ana", "address": {"city": "Lisbon"}},
    {"_key": "p2", "_uri": EX + "p2", "name": "Bo", "address.city": "Porto"},
    {"_key": "p3", "_uri": EX + "p3", "name": "Cy", "address": {"city": "Faro"}, "address.city": "Braga"},
    {"_key": "p4", "_uri": EX + "p4", "name": "Di", "address": [{"city": "Evora"}]},
    {"_key": "p5", "_uri": EX + "p5", "name": "Ed", "address": "Rua Augusta 1"},
    {"_key": "p6", "_uri": EX + "p6", "name": "Fi"},
    {"_key": "p7", "_uri": EX + "p7", "name": "Gu", "address": {}},
]

# What the documents mean as triples, under the mapping above.
_NESTED_CITY = {"p1": "Lisbon", "p3": "Faro"}
_LITERAL_CITY = {"p2": "Porto", "p3": "Braga"}

_PREFIX = f"PREFIX : <{EX}>\n"


@pytest.fixture(scope="module")
def db() -> Iterator[Any]:
    if not integration_enabled():
        pytest.skip("set RUN_INTEGRATION=1 to enable integration tests")
    if not arangodb_reachable() and not try_boot_arangodb_via_compose():
        pytest.skip(f"ArangoDB at {DEFAULT_ARANGO_URL} is unreachable and could not be booted")
    from arango import ArangoClient

    ensure_test_database()
    handle = ArangoClient(hosts=DEFAULT_ARANGO_URL).db(
        DEFAULT_ARANGO_DB, username=DEFAULT_ARANGO_USER, password=DEFAULT_ARANGO_PASSWORD
    )
    if handle.has_collection(_PEOPLE):
        handle.delete_collection(_PEOPLE)
    handle.create_collection(_PEOPLE).insert_many(_DOCS)
    yield handle
    handle.delete_collection(_PEOPLE)


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    lines = []
    for d in _DOCS:
        key = d["_key"]
        lines.append(f'<{d["_uri"]}> a <{EX}Person> ; <{EX}name> "{d["name"]}" .')
        if key in _NESTED_CITY:
            lines.append(f'<{d["_uri"]}> <{EX}city> "{_NESTED_CITY[key]}" .')
        if key in _LITERAL_CITY:
            lines.append(f'<{d["_uri"]}> <{EX}dottedCity> "{_LITERAL_CITY[key]}" .')
    store = oxi.Store()
    store.load("\n".join(lines).encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


def _multiset(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(json.dumps(r, sort_keys=True) for r in rows)


def _arango(db: Any, body: str) -> list[dict[str, Any]]:
    res = translate(_PREFIX + body, resolver=SchemaResolver.from_turtle(_TTL))
    db.aql.validate(res.aql)
    return [drop_null_bindings(r) for r in db.aql.execute(res.aql, bind_vars=res.bind_vars)]


def _oxigraph(store: Any, body: str) -> list[dict[str, Any]]:
    return [normalize_oxi_row(r) for r in oxi_bindings(store, _PREFIX + body)]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # Required nested: only real objects that hold city.
        (
            "SELECT ?n ?c WHERE { ?p a :Person ; :name ?n ; :city ?c }",
            [{"n": "Ana", "c": "Lisbon"}, {"n": "Cy", "c": "Faro"}],
        ),
        # Required literal dotted name: unchanged since 0.4.1.
        (
            "SELECT ?n ?c WHERE { ?p a :Person ; :name ?n ; :dottedCity ?c }",
            [{"n": "Bo", "c": "Porto"}, {"n": "Cy", "c": "Braga"}],
        ),
        # Both meanings on one document, side by side.
        (
            "SELECT ?nested ?literal WHERE { ?p a :Person ; :city ?nested ; :dottedCity ?literal }",
            [{"nested": "Faro", "literal": "Braga"}],
        ),
        # OPTIONAL: every person, nested city where there is one.
        (
            "SELECT ?n ?c WHERE { ?p a :Person ; :name ?n OPTIONAL { ?p :city ?c } }",
            [
                {"n": "Ana", "c": "Lisbon"},
                {"n": "Bo"},
                {"n": "Cy", "c": "Faro"},
                {"n": "Di"},
                {"n": "Ed"},
                {"n": "Fi"},
                {"n": "Gu"},
            ],
        ),
        # A constant object compares the nested value.
        ('SELECT ?n WHERE { ?p a :Person ; :name ?n ; :city "Lisbon" }', [{"n": "Ana"}]),
        ('SELECT ?n WHERE { ?p a :Person ; :name ?n ; :city "Porto" }', []),
        # Counting through OPTIONAL counts bound values only.
        ("SELECT (COUNT(?c) AS ?k) WHERE { ?p a :Person OPTIONAL { ?p :city ?c } }", [{"k": 2}]),
        # OPTIONAL with an inner FILTER.
        (
            'SELECT ?n ?c WHERE { ?p a :Person ; :name ?n OPTIONAL { ?p :city ?c FILTER(?c != "Faro") } }',
            [
                {"n": "Ana", "c": "Lisbon"},
                {"n": "Bo"},
                {"n": "Cy"},
                {"n": "Di"},
                {"n": "Ed"},
                {"n": "Fi"},
                {"n": "Gu"},
            ],
        ),
    ],
)
def test_nested_and_literal_dotted_fields_match_oxigraph(
    db: Any, oxi_store: Any, body: str, expected: list[dict[str, Any]]
) -> None:
    rows = _arango(db, body)
    assert _multiset(rows) == _multiset(expected)
    assert _multiset(rows) == _multiset(_oxigraph(oxi_store, body))
