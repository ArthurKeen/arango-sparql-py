"""Issue #59 — an unbound value inside SUM / AVG / MIN / MAX / GROUP_CONCAT.

SPARQL evaluates these over the group's values; an unbound one is an error,
and the whole result is an error, projected as unbound (SPARQL 1.1 §18.5.1).
AQL's aggregates skip null, so they returned a number (or a string) instead.
A variable bound by a *required* triple is different: a stored ``null`` there
is no triple at all, which skipping already matches, and must stay so.

Live because the bug is about what ArangoDB returns. Every query also runs
through pyoxigraph on the same data, the W3C ground truth, and the answers
must agree. Gated like the rest of the integration suite (``RUN_INTEGRATION=1``).
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
_PEOPLE = "Issue59People"

_TTL = f"""
@prefix :     <{EX}> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:mappingStyle "COLLECTION" ; phys:collectionName "{_PEOPLE}" .
"""

# b has no age; d *stores* ``age: null`` — no triple on the RDF side.
_DOCS = [
    {"_key": "a", "_uri": EX + "a", "dept": "eng", "age": 30},
    {"_key": "b", "_uri": EX + "b", "dept": "eng"},
    {"_key": "c", "_uri": EX + "c", "dept": "ops", "age": 40},
    {"_key": "d", "_uri": EX + "d", "dept": "ops", "age": None},
    {"_key": "e", "_uri": EX + "e", "dept": "hr", "age": 50},
]

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
        lines.append(f'<{d["_uri"]}> a <{EX}Person> ; <{EX}dept> "{d["dept"]}" .')
        if d.get("age") is not None:
            lines.append(f"<{d['_uri']}> <{EX}age> {d['age']} .")
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
        # The issue's repro: b (and d) leave ?a unbound, so each is unbound.
        ("SELECT (SUM(?a) AS ?s) WHERE { ?p a :Person OPTIONAL { ?p :age ?a } }", [{}]),
        (
            "SELECT (AVG(?a) AS ?v) (MIN(?a) AS ?m) (MAX(?a) AS ?x) (GROUP_CONCAT(?a) AS ?g) "
            "WHERE { ?p a :Person OPTIONAL { ?p :age ?a } }",
            [{}],
        ),
        # Per group: eng (b) and ops (d) are unbound, hr keeps its sum.
        (
            "SELECT ?d (SUM(?a) AS ?s) WHERE { ?p a :Person ; :dept ?d OPTIONAL { ?p :age ?a } } GROUP BY ?d",
            [{"d": "eng"}, {"d": "ops"}, {"d": "hr", "s": 50}],
        ),
        # Through a BIND, an expression argument, a UNION gap, VALUES UNDEF.
        ("SELECT (SUM(?b) AS ?s) WHERE { ?p a :Person OPTIONAL { ?p :age ?a } BIND(?a + 1 AS ?b) }", [{}]),
        ("SELECT (SUM(?a * 2) AS ?s) WHERE { ?p a :Person OPTIONAL { ?p :age ?a } }", [{}]),
        (
            'SELECT (SUM(?a) AS ?s) WHERE { { ?p a :Person ; :age ?a } UNION { ?p a :Person ; :dept "eng" } }',
            [{}],
        ),
        ("SELECT (SUM(?v) AS ?s) WHERE { VALUES ?v { 1 2 UNDEF } }", [{}]),
        # Controls, already right. A required ?a skips d's stored null (no
        # triple), COUNT and SAMPLE skip unbound values, and over no rows SUM
        # is still 0 (#49).
        (
            "SELECT ?d (SUM(?a) AS ?s) WHERE { ?p a :Person ; :dept ?d ; :age ?a } GROUP BY ?d",
            [{"d": "eng", "s": 30}, {"d": "ops", "s": 40}, {"d": "hr", "s": 50}],
        ),
        ("SELECT (COUNT(?a) AS ?c) WHERE { ?p a :Person OPTIONAL { ?p :age ?a } }", [{"c": 3}]),
        ('SELECT (SUM(?a) AS ?s) WHERE { ?p a :Person ; :dept "none" OPTIONAL { ?p :age ?a } }', [{"s": 0}]),
    ],
)
def test_aggregate_with_an_unbound_value_matches_oxigraph(
    db: Any, oxi_store: Any, body: str, expected: list[dict[str, Any]]
) -> None:
    rows = _arango(db, body)
    assert _multiset(rows) == _multiset(expected)
    assert _multiset(rows) == _multiset(_oxigraph(oxi_store, body))
