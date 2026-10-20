"""Issue #48 — OPTIONAL over an object property keeps every matching edge.

SPARQL's LeftJoin yields one solution per compatible match of the OPTIONAL
block (SPARQL 1.1 §18.5), and one solution with the block's variables
unbound when nothing matches. The translator followed only the first edge
(``LET o = (FOR v IN OUTBOUND … LIMIT 1 RETURN …)[0]``), so a subject with
two edges lost a solution — and every count or join over it was off.

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
_OWNERS, _PEOPLE, _OWNS, _REL = "Issue48Owners", "Issue48People", "Issue48Owns", "Issue48Rel"

# ``:owns`` is a dedicated edge collection; ``:holds`` shares ``Issue48Rel``
# with other relationships and is told apart by the edge's ``type``.
_TTL = f"""
@prefix :     <{EX}> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Owner  a owl:Class ; phys:mappingStyle "COLLECTION" ; phys:collectionName "{_OWNERS}" .
:Person a owl:Class ; phys:mappingStyle "COLLECTION" ; phys:collectionName "{_PEOPLE}" .
:owns  a owl:ObjectProperty ; phys:edgeCollectionName "{_OWNS}" .
:holds a owl:ObjectProperty ; phys:edgeCollectionName "{_REL}" ;
    phys:typeField "type" ; phys:typeValue "holds" .
"""

# o1 has two edges of each kind, o2 one, o3 none. o2 has no email, so a
# block that also asks for the email never matches it.
_OWNER_DOCS = [
    {"_key": "o1", "_uri": EX + "o1", "email": "one@example.org"},
    {"_key": "o2", "_uri": EX + "o2"},
    {"_key": "o3", "_uri": EX + "o3", "email": "three@example.org"},
]
_PEOPLE_DOCS = [{"_key": k, "_uri": EX + k} for k in ("a", "b", "c")]
_OWNED = [("o1", "a"), ("o1", "b"), ("o2", "c")]
# The "likes" edge shares the collection but is not :holds, so it must not count.
_RELATED = [("o1", "a", "holds"), ("o1", "b", "holds"), ("o2", "c", "holds"), ("o1", "c", "likes")]

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
    for name in (_OWNS, _REL, _OWNERS, _PEOPLE):
        if handle.has_collection(name):
            handle.delete_collection(name)
    handle.create_collection(_OWNERS).insert_many(_OWNER_DOCS)
    handle.create_collection(_PEOPLE).insert_many(_PEOPLE_DOCS)
    handle.create_collection(_OWNS, edge=True).insert_many(
        [{"_from": f"{_OWNERS}/{o}", "_to": f"{_PEOPLE}/{p}"} for o, p in _OWNED]
    )
    handle.create_collection(_REL, edge=True).insert_many(
        [{"_from": f"{_OWNERS}/{o}", "_to": f"{_PEOPLE}/{p}", "type": t} for o, p, t in _RELATED]
    )
    yield handle
    for name in (_OWNS, _REL, _OWNERS, _PEOPLE):
        handle.delete_collection(name)


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    lines = []
    for d in _OWNER_DOCS:
        lines.append(f"<{d['_uri']}> a <{EX}Owner> .")
        if "email" in d:
            lines.append(f'<{d["_uri"]}> <{EX}email> "{d["email"]}" .')
    lines += [f"<{d['_uri']}> a <{EX}Person> ." for d in _PEOPLE_DOCS]
    lines += [f"<{EX}{o}> <{EX}owns> <{EX}{p}> ." for o, p in _OWNED]
    lines += [f"<{EX}{o}> <{EX}holds> <{EX}{p}> ." for o, p, t in _RELATED if t == "holds"]
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


def _o(key: str, **rest: Any) -> dict[str, Any]:
    return {"o": EX + key, **{k: (EX + v if k == "p" else v) for k, v in rest.items()}}


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # The issue's repro: o1's two edges are two solutions (it was one).
        (
            "SELECT ?o ?p WHERE { ?o a :Owner OPTIONAL { ?o :owns ?p } }",
            [_o("o1", p="a"), _o("o1", p="b"), _o("o2", p="c"), _o("o3")],
        ),
        # A typed edge: only ``holds`` edges count, the ``likes`` edge does not.
        (
            "SELECT ?o ?p WHERE { ?o a :Owner OPTIONAL { ?o :holds ?p } }",
            [_o("o1", p="a"), _o("o1", p="b"), _o("o2", p="c"), _o("o3")],
        ),
        # The block matches as a unit: o2 has an edge but no email, so the
        # block does not match and o2 keeps one solution with both unbound.
        (
            "SELECT ?o ?p ?e WHERE { ?o a :Owner OPTIONAL { ?o :owns ?p ; :email ?e } }",
            [
                _o("o1", p="a", e="one@example.org"),
                _o("o1", p="b", e="one@example.org"),
                _o("o2"),
                _o("o3"),
            ],
        ),
        # An inner FILTER rejects edges inside the block: o1 keeps a only.
        (
            "SELECT ?o ?p WHERE { ?o a :Owner OPTIONAL { ?o :owns ?p FILTER(?p != :b) } }",
            [_o("o1", p="a"), _o("o2", p="c"), _o("o3")],
        ),
        # ...and when it rejects every edge of a subject, that subject keeps
        # one unbound solution rather than disappearing.
        (
            "SELECT ?o ?p WHERE { ?o a :Owner OPTIONAL { ?o :owns ?p FILTER(?p = :c) } }",
            [_o("o1"), _o("o2", p="c"), _o("o3")],
        ),
        # Counts over the OPTIONAL now see every edge (with #47's COUNT).
        ("SELECT (COUNT(?p) AS ?c) WHERE { ?o a :Owner OPTIONAL { ?o :owns ?p } }", [{"c": 3}]),
        (
            "SELECT ?o (COUNT(?p) AS ?c) WHERE { ?o a :Owner OPTIONAL { ?o :owns ?p } } GROUP BY ?o",
            [_o("o1", c=2), _o("o2", c=1), _o("o3", c=0)],
        ),
    ],
)
def test_optional_edge_matches_oxigraph(
    db: Any, oxi_store: Any, body: str, expected: list[dict[str, Any]]
) -> None:
    rows = _arango(db, body)
    assert _multiset(rows) == _multiset(expected)
    assert _multiset(rows) == _multiset(_oxigraph(oxi_store, body))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # Issue #52: MINUS removes a pair when *some* edge of an emailed owner
        # matches. o1's pairs both go (the first-edge test kept one).
        (
            "SELECT ?o ?p WHERE { ?o a :Owner ; :owns ?p . MINUS { ?o a :Owner ; :email ?e OPTIONAL { ?o :owns ?p } } }",
            [_o("o2", p="c")],
        ),
        # Every owner x person: o1 keeps only c (no edge to it), o2 has no
        # email so keeps all, and o3 has an email but no edges, so its
        # unbound ?p is compatible with every person and all three go.
        (
            "SELECT ?o ?p WHERE { ?o a :Owner . ?p a :Person . "
            "MINUS { ?o a :Owner ; :email ?e OPTIONAL { ?o :owns ?p } } }",
            [_o("o1", p="c"), _o("o2", p="a"), _o("o2", p="b"), _o("o2", p="c")],
        ),
    ],
)
def test_minus_optional_rebind_matches_oxigraph(
    db: Any, oxi_store: Any, body: str, expected: list[dict[str, Any]]
) -> None:
    rows = _arango(db, body)
    assert _multiset(rows) == _multiset(expected)
    assert _multiset(rows) == _multiset(_oxigraph(oxi_store, body))
