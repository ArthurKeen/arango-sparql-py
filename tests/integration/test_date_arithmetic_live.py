"""``xsd:dateTime ± xsd:duration`` on a real ArangoDB, against pyoxigraph.

The translation goldens (``tests/translate/test_translate_datetime_arithmetic.py``)
pin the AQL; this checks what ArangoDB's ``DATE_ADD`` / ``DATE_SUBTRACT``
actually return for it, which the in-memory interpreter cannot. Every
answer must equal pyoxigraph's on the same data (the W3C ground truth).
Gated like the rest of the integration suite (``RUN_INTEGRATION=1``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.oxi import assert_bindings_equal, drop_null_bindings, normalize_oxi_row, oxi_bindings
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

_EVENTS = "DateArithmeticEvents"
_TTL = f"""
@prefix :     <http://example.org/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Event a owl:Class ; phys:collectionName "{_EVENTS}" .
:at a owl:DatatypeProperty .
:n a owl:DatatypeProperty .
"""
# Around a 30-day window from 2010-12-01: before it, inside it (both ends),
# on the excluded end, and after it. Timestamps use the LDBC form (ms + Z).
_EVENT = "http://example.org/event/"
_DOCS = [
    {"_key": key, "_uri": _EVENT + key, "at": at, "n": n}
    for key, at, n in (
        ("before", "2010-11-30T23:59:59.999Z", 1),
        ("start", "2010-12-01T00:00:00.000Z", 2),
        ("inside", "2010-12-15T12:00:00.000Z", 3),
        ("last", "2010-12-30T23:59:59.999Z", 4),
        ("end", "2010-12-31T00:00:00.000Z", 5),
    )
]
_PREFIX = "PREFIX : <http://example.org/>\nPREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"


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
    if handle.has_collection(_EVENTS):
        handle.delete_collection(_EVENTS)
    handle.create_collection(_EVENTS).insert_many(_DOCS)
    yield handle
    handle.delete_collection(_EVENTS)


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    store = oxi.Store()
    triples = "\n".join(
        f"<{d['_uri']}> a <http://example.org/Event> ; "
        f'<http://example.org/at> "{d["at"]}"^^<http://www.w3.org/2001/XMLSchema#dateTime> ; '
        f"<http://example.org/n> {d['n']} ."
        for d in _DOCS
    )
    store.load(triples.encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


def _arango(db: Any, body: str) -> list[dict[str, Any]]:
    res = translate(_PREFIX + body, resolver=SchemaResolver.from_turtle(_TTL))
    db.aql.validate(res.aql)
    return [drop_null_bindings(r) for r in db.aql.execute(res.aql, bind_vars=res.bind_vars)]


def _oxigraph(store: Any, body: str) -> list[dict[str, Any]]:
    return [normalize_oxi_row(r) for r in oxi_bindings(store, _PREFIX + body)]


_START = '"2010-12-01T00:00:00.000Z"^^xsd:dateTime'


@pytest.mark.parametrize(
    "body",
    [
        # The LDBC IC3 / IC4 window: [start, start + 30 days).
        f'SELECT ?x WHERE {{ ?x a :Event ; :at ?at . FILTER(?at >= {_START} && ?at < {_START} + "P30D"^^xsd:duration) }}',
        # duration + date, and a dayTimeDuration with hours.
        f'SELECT ?x WHERE {{ ?x a :Event ; :at ?at . FILTER(?at < "PT36H"^^xsd:dayTimeDuration + {_START}) }}',
        # date - duration.
        f'SELECT ?x WHERE {{ ?x a :Event ; :at ?at . FILTER(?at >= {_START} - "P1D"^^xsd:duration) }}',
        # number + duration is a type error in SPARQL: ?e stays unbound.
        'SELECT ?x ?e WHERE { ?x a :Event ; :n ?n . BIND(?n + "P1D"^^xsd:duration AS ?e) }',
    ],
)
def test_date_arithmetic_matches_oxigraph(db: Any, oxi_store: Any, body: str) -> None:
    expected = _oxigraph(oxi_store, body)
    assert expected, "the case must have a non-empty answer to prove anything"
    assert_bindings_equal(expected, _arango(db, body))
