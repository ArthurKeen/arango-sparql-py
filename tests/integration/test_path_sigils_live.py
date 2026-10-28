"""Property paths inside and outside a UNION mint distinct intermediates (live).

Each child visitor (UNION arm, OPTIONAL, MINUS / EXISTS probe, sub-SELECT)
used to restart its ``_path_<n>`` counter while being seeded with the outer
scope's bindings, so an arm's ``_path_1`` silently joined to the outer
``_path_1``. LDBC IC6 lost every friend-of-a-friend row that way (PRD
§13.6); here Dan's post disappears. Live because the cross-test interpreter
cannot run ``UNION``; compared with pyoxigraph on the same facts. Gated like
the rest of the integration suite (``RUN_INTEGRATION=1``).
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

EX = "http://example.org/"
_PEOPLE, _POSTS, _KNOWS, _CREATOR = "SigilPeople", "SigilPosts", "SigilKnows", "SigilCreator"
PEOPLE = {"alice": "Alice", "bob": "Bob", "carol": "Carol", "dan": "Dan"}
KNOWS = [("alice", "bob"), ("carol", "alice"), ("bob", "dan")]  # once each, as LDBC stores knows
POSTS = {"post1": "bob", "post2": "dan", "post3": "alice"}

_TTL = f"""
@prefix : <{EX}> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
:Person a owl:Class ; phys:collectionName "{_PEOPLE}" .
:Post a owl:Class ; phys:collectionName "{_POSTS}" .
:firstName a owl:DatatypeProperty ; rdfs:domain :Person .
:directKnows a owl:ObjectProperty, owl:SymmetricProperty ; phys:edgeCollectionName "{_KNOWS}" ;
    rdfs:domain :Person ; rdfs:range :Person .
:hasCreator a owl:ObjectProperty ; phys:edgeCollectionName "{_CREATOR}" ; rdfs:domain :Post ; rdfs:range :Person .
"""


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
    for name in (_PEOPLE, _POSTS, _KNOWS, _CREATOR):
        if handle.has_collection(name):
            handle.delete_collection(name)
    handle.create_collection(_PEOPLE).insert_many(
        [{"_key": k, "_uri": EX + k, "firstName": n} for k, n in PEOPLE.items()]
    )
    handle.create_collection(_POSTS).insert_many([{"_key": p, "_uri": EX + p} for p in POSTS])
    handle.create_collection(_KNOWS, edge=True).insert_many(
        [{"_from": f"{_PEOPLE}/{a}", "_to": f"{_PEOPLE}/{b}"} for a, b in KNOWS]
    )
    handle.create_collection(_CREATOR, edge=True).insert_many(
        [{"_from": f"{_POSTS}/{p}", "_to": f"{_PEOPLE}/{c}"} for p, c in POSTS.items()]
    )
    yield handle
    for name in (_PEOPLE, _POSTS, _KNOWS, _CREATOR):
        handle.delete_collection(name)


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    lines = [f'<{EX}{k}> a <{EX}Person> ; <{EX}firstName> "{n}" .' for k, n in PEOPLE.items()]
    lines += [f"<{EX}{p}> a <{EX}Post> ; <{EX}hasCreator> <{EX}{c}> ." for p, c in POSTS.items()]
    lines += [
        f"<{EX}{a}> <{EX}directKnows> <{EX}{b}> . <{EX}{b}> <{EX}directKnows> <{EX}{a}> ." for a, b in KNOWS
    ]
    store = oxi.Store()
    store.load("\n".join(lines).encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


def test_paths_inside_and_outside_a_union_mint_distinct_intermediates(db: Any, oxi_store: Any) -> None:
    sparql = (
        f"PREFIX : <{EX}>\nSELECT ?m ?cn WHERE {{ ?m a :Post ; :hasCreator/:firstName ?cn ; :hasCreator ?c . "
        "{ :alice :directKnows ?c } UNION { :alice :directKnows/:directKnows ?c FILTER(?c != :alice) } }"
    )
    resolver = SchemaResolver(
        ontology=SchemaResolver.from_turtle(_TTL).ontology, strict_subject_resolution=True
    )
    res = translate(sparql, resolver=resolver)
    actual = [drop_null_bindings(r) for r in db.aql.execute(res.aql, bind_vars=res.bind_vars)]
    expected = [normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert {r["cn"] for r in expected} == {"Bob", "Dan"}  # a friend's post and a friend-of-a-friend's
    assert_bindings_equal(expected, actual)
