"""Issue #46 — ``COUNT(?x)`` counts only the solutions where ``?x`` has a value
(and issue #49 — ungrouped ``SUM`` / ``AVG`` over no solutions are 0).

SPARQL's COUNT(expr) skips solutions in which ``expr`` is unbound
(SPARQL 1.1 §18.5.1.2). An unbound variable is null on the AQL side, and AQL's
``COUNT``, ``COUNT_DISTINCT`` and ``COLLECT … WITH COUNT INTO`` all count
nulls, so a count over an OPTIONAL variable, a never-bound variable or a
stored-null attribute used to come back too high.

Live because the bug is about what ArangoDB returns: the in-memory
interpreter the cross tests use dropped nulls from ``COUNT_DISTINCT``, which
ArangoDB does not. Every query also runs through pyoxigraph on the same data —
the W3C ground truth — and the answers must agree. Gated like the rest of the
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

_PEOPLE = "Issue46People"

_TTL = f"""
@prefix :     <http://example.org/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:mappingStyle "COLLECTION" ; phys:collectionName "{_PEOPLE}" .
"""

# Two of five people have an email. Cy's is *stored* as null, which is no
# triple on the RDF side; Di has no dept at all.
_DOCS: list[dict[str, Any]] = [
    {"_key": "ada", "name": "Ada", "dept": "eng", "email": "ada@example.org"},
    {"_key": "ben", "name": "Ben", "dept": "eng"},
    {"_key": "cy", "name": "Cy", "dept": "ops", "email": None},
    {"_key": "di", "name": "Di", "email": "di@example.org"},
    {"_key": "ed", "name": "Ed", "dept": "ops", "email": "ada@example.org"},
]

_PREFIX = "PREFIX : <http://example.org/>\n"


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
    """The same people as RDF; a missing or null attribute is no triple."""
    lines = []
    for d in _DOCS:
        subject = f"<{_PEOPLE}/{d['_key']}>"
        lines.append(f"{subject} a <http://example.org/Person> .")
        for attr in ("name", "dept", "email"):
            if d.get(attr) is not None:
                lines.append(f'{subject} <http://example.org/{attr}> "{d[attr]}" .')
    store = oxi.Store()
    store.load("\n".join(lines).encode("utf-8"), oxi.RdfFormat.TURTLE, base_iri="http://example.org/")
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
        # The issue's shapes. Each counted every row (5) before the fix.
        ("SELECT (COUNT(?e) AS ?c) WHERE { ?p a :Person OPTIONAL { ?p :email ?e } }", [{"c": 3}]),
        ("SELECT (COUNT(?o) AS ?c) WHERE { ?p a :Person }", [{"c": 0}]),
        ("SELECT (COUNT(DISTINCT ?e) AS ?c) WHERE { ?p a :Person OPTIONAL { ?p :email ?e } }", [{"c": 2}]),
        (
            "SELECT (COUNT(?e) AS ?c) (MAX(?n) AS ?m) WHERE { ?p a :Person ; :name ?n OPTIONAL { ?p :email ?e } }",
            [{"c": 3, "m": "Ed"}],
        ),
        # Grouped, including the group whose key is unbound (Di): it counts 0.
        (
            "SELECT ?d (COUNT(?e) AS ?c) WHERE { ?p a :Person OPTIONAL { ?p :dept ?d } "
            "OPTIONAL { ?p :email ?e } } GROUP BY ?d",
            [{"d": "eng", "c": 1}, {"d": "ops", "c": 1}, {"c": 1}],
        ),
        (
            "SELECT ?d (COUNT(?d) AS ?c) WHERE { ?p a :Person OPTIONAL { ?p :dept ?d } } GROUP BY ?d",
            [{"d": "eng", "c": 2}, {"d": "ops", "c": 2}, {"c": 0}],
        ),
        # HAVING reads the DISTINCT count with the null dropped: each dept has
        # one distinct email (counting null as a value made it 2 for both).
        (
            "SELECT ?d WHERE { ?p a :Person ; :dept ?d OPTIONAL { ?p :email ?e } } "
            "GROUP BY ?d HAVING (COUNT(DISTINCT ?e) = 1)",
            [{"d": "eng"}, {"d": "ops"}],
        ),
        # No rows at all: still one row, and 0.
        (
            'SELECT (COUNT(?e) AS ?c) WHERE { ?p a :Person ; :name "Nobody" OPTIONAL { ?p :email ?e } }',
            [{"c": 0}],
        ),
        # Controls that were already right: every row binds the argument.
        ("SELECT (COUNT(*) AS ?c) WHERE { ?p a :Person }", [{"c": 5}]),
        ("SELECT (COUNT(?p) AS ?c) WHERE { ?p a :Person }", [{"c": 5}]),
    ],
)
def test_count_matches_oxigraph(db: Any, oxi_store: Any, body: str, expected: list[dict[str, Any]]) -> None:
    rows = _arango(db, body)
    assert _multiset(rows) == _multiset(expected)
    assert _multiset(rows) == _multiset(_oxigraph(oxi_store, body))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "SELECT ?d (MAX(?d) AS ?m) WHERE { ?p a :Person ; :dept ?d } GROUP BY ?d",
            [
                {"d": "eng", "m": "eng"},
                {"d": "ops", "m": "ops"},
            ],
        ),
        (
            "SELECT ?d (GROUP_CONCAT(?d) AS ?g) WHERE { ?p a :Person ; :dept ?d } GROUP BY ?d",
            [
                {"d": "eng", "g": "eng eng"},
                {"d": "ops", "g": "ops ops"},
            ],
        ),
    ],
)
def test_an_aggregate_of_the_group_key_runs(
    db: Any, oxi_store: Any, body: str, expected: list[dict[str, Any]]
) -> None:
    """Found reviewing #47: the argument read the COLLECT key alias, which
    ArangoDB rejects inside AGGREGATE (ERR 1512)."""
    rows = _arango(db, body)
    assert _multiset(rows) == _multiset(expected)
    assert _multiset(rows) == _multiset(_oxigraph(oxi_store, body))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # Issue #49: SPARQL's Sum and Avg of an empty group are 0; AQL's are
        # null, so these came back unbound. MIN of an empty group is unbound.
        (
            'SELECT (SUM(?n) AS ?s) (AVG(?n) AS ?v) (MIN(?n) AS ?m) WHERE { ?p a :Person ; :name "Nobody" ; :name ?n }',
            [{"s": 0, "v": 0}],
        ),
        # Control, already right: over rows the values come straight through.
        ("SELECT (COUNT(?d) AS ?c) WHERE { ?p a :Person ; :dept ?d }", [{"c": 4}]),
    ],
)
def test_sum_and_avg_over_no_rows_are_zero(
    db: Any, oxi_store: Any, body: str, expected: list[dict[str, Any]]
) -> None:
    rows = _arango(db, body)
    assert _multiset(rows) == _multiset(expected)
    assert _multiset(rows) == _multiset(_oxigraph(oxi_store, body))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # Issue #58: each name has one dept, so the sample is fixed. It used
        # to group on ?d as well, which gave the same rows here only by luck.
        (
            "SELECT ?n (SAMPLE(?d) AS ?x) WHERE { ?p a :Person ; :name ?n ; :dept ?d } GROUP BY ?n",
            [
                {"n": "Ada", "x": "eng"},
                {"n": "Ben", "x": "eng"},
                {"n": "Cy", "x": "ops"},
                {"n": "Ed", "x": "ops"},
            ],
        ),
        # Two Samples of the group key share one COLLECT key; the second read
        # the first's alias and returned garbage for ?d.
        (
            "SELECT ?d (SAMPLE(?d) AS ?x) WHERE { ?p a :Person ; :dept ?d } GROUP BY ?d",
            [{"d": "eng", "x": "eng"}, {"d": "ops", "x": "ops"}],
        ),
    ],
)
def test_sample_matches_oxigraph(db: Any, oxi_store: Any, body: str, expected: list[dict[str, Any]]) -> None:
    rows = _arango(db, body)
    assert _multiset(rows) == _multiset(expected)
    assert _multiset(rows) == _multiset(_oxigraph(oxi_store, body))


def test_an_ungrouped_sample_is_one_row_holding_one_of_the_values(db: Any) -> None:
    """Issue #58: it returned one row per distinct dept."""
    rows = _arango(db, "SELECT (SAMPLE(?d) AS ?x) WHERE { ?p a :Person ; :dept ?d }")
    assert len(rows) == 1 and rows[0]["x"] in {"eng", "ops"}
