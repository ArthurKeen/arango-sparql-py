"""Issue #51 — a type pattern on an already-bound variable joins to that binding.

A subject variable can have a value before its type pattern is reached: the
outer row's identity inside a MINUS / EXISTS probe, a VALUES row, a BIND. The
type pattern opened ``FOR doc IN @@<class>`` with no join to that value, so the
query ranged over every document of the class — a MINUS that removed every
row when any one matched, a VALUES row repeated once per document. No error.

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
_OWNERS = "Issue51Owners"

_TTL = f"""
@prefix :     <{EX}> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Owner a owl:Class ; phys:mappingStyle "COLLECTION" ; phys:collectionName "{_OWNERS}" .
"""

# Only o1 has a tag; o3 has a name but no tag.
_DOCS = [
    {"_key": "o1", "_uri": EX + "o1", "tag": "x", "name": "One"},
    {"_key": "o2", "_uri": EX + "o2", "name": "Two"},
    {"_key": "o3", "_uri": EX + "o3", "name": "Three"},
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
    if handle.has_collection(_OWNERS):
        handle.delete_collection(_OWNERS)
    handle.create_collection(_OWNERS).insert_many(_DOCS)
    yield handle
    handle.delete_collection(_OWNERS)


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    lines = []
    for d in _DOCS:
        lines.append(f"<{d['_uri']}> a <{EX}Owner> .")
        for attr in ("tag", "name"):
            if attr in d:
                lines.append(f'<{d["_uri"]}> <{EX}{attr}> "{d[attr]}" .')
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


def _o(*keys: str) -> list[dict[str, Any]]:
    return [{"o": EX + k} for k in keys]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # The issue's repro: o1's tag removed every owner (it returned none).
        ("SELECT ?o WHERE { ?o a :Owner . MINUS { ?o a :Owner ; :tag ?t } }", _o("o2", "o3")),
        ("SELECT ?o WHERE { ?o a :Owner . FILTER NOT EXISTS { ?o a :Owner ; :tag ?t } }", _o("o2", "o3")),
        # EXISTS kept every owner when any one matched.
        ("SELECT ?o WHERE { ?o a :Owner . FILTER EXISTS { ?o a :Owner ; :tag ?t } }", _o("o1")),
        # A VALUES row or a BIND came back once per owner.
        ("SELECT ?o WHERE { VALUES ?o { :o1 :o3 } ?o a :Owner }", _o("o1", "o3")),
        ("SELECT ?o WHERE { BIND(:o2 AS ?o) ?o a :Owner }", _o("o2")),
        # A value that is not an Owner matches nothing.
        ("SELECT ?o WHERE { VALUES ?o { :nobody } ?o a :Owner }", []),
        # Control, already right: a plain typed scan with nothing bound first.
        ("SELECT ?o WHERE { ?o a :Owner }", _o("o1", "o2", "o3")),
    ],
)
def test_typed_subject_joins_its_prior_binding(
    db: Any, oxi_store: Any, body: str, expected: list[dict[str, Any]]
) -> None:
    rows = _arango(db, body)
    assert _multiset(rows) == _multiset(expected)
    assert _multiset(rows) == _multiset(_oxigraph(oxi_store, body))
