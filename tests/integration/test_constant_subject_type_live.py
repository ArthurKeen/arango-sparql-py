"""``<iri> a :C`` matches that one document, on a real ArangoDB, against pyoxigraph.

Before the fix, the PG / LPG type pattern opened ``FOR doc IN @@C`` and
never filtered it on the constant subject, so ``<x> a :Person`` matched
every person (one row each) instead of x alone; the subject's property
triples were then refused or read elsewhere. Found by the LDBC harness
(PRD §13.6). Gated like the rest of the integration suite
(``RUN_INTEGRATION=1``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.oxi import assert_bindings_equal, normalize_oxi_row, oxi_bindings
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

_PEOPLE = "ConstSubjectPeople"
_LPG = "ConstSubjectNodes"
_EX = "http://example.org/"
_PEOPLE_ROWS = [("alice", "Alice"), ("bob", "Bob"), ("carol", "Carol")]

_PG_TTL = f"""
@prefix : <{_EX}> . @prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:collectionName "{_PEOPLE}" .
:name a owl:DatatypeProperty .
"""
_LPG_TTL = f"""
@prefix : <{_EX}> . @prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:collectionName "{_LPG}" ; phys:mappingStyle "LABEL" ;
    phys:typeField "type" ; phys:typeValue "Person" .
:Project a owl:Class ; phys:collectionName "{_LPG}" ; phys:mappingStyle "LABEL" ;
    phys:typeField "type" ; phys:typeValue "Project" .
:name a owl:DatatypeProperty .
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
    for name in (_PEOPLE, _LPG):
        if handle.has_collection(name):
            handle.delete_collection(name)
    handle.create_collection(_PEOPLE).insert_many(
        [{"_key": k, "_uri": _EX + k, "name": n} for k, n in _PEOPLE_ROWS]
    )
    handle.create_collection(_LPG).insert_many(
        [{"_key": k, "_uri": _EX + k, "type": "Person", "name": n} for k, n in _PEOPLE_ROWS]
        + [{"_key": "p1", "_uri": _EX + "p1", "type": "Project", "name": "Apollo"}]
    )
    yield handle
    for name in (_PEOPLE, _LPG):
        handle.delete_collection(name)


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    store = oxi.Store()
    ttl = "\n".join(f'<{_EX}{k}> a <{_EX}Person> ; <{_EX}name> "{n}" .' for k, n in _PEOPLE_ROWS)
    ttl += f'\n<{_EX}p1> a <{_EX}Project> ; <{_EX}name> "Apollo" .'
    store.load(ttl.encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


_CASES = [
    "SELECT ?n WHERE { <http://example.org/alice> a :Person ; :name ?n }",
    "SELECT ?n WHERE { <http://example.org/p1> a :Person ; :name ?n }",
    "SELECT ?s ?n WHERE { <http://example.org/nobody> a :Person . ?s a :Person ; :name ?n }",
    "SELECT ?s WHERE { <http://example.org/bob> a :Person . ?s a :Person }",
]


@pytest.mark.parametrize("ttl", [_PG_TTL, _LPG_TTL], ids=["pg", "lpg"])
@pytest.mark.parametrize("body", _CASES)
def test_constant_subject_type_pattern_matches_oxigraph(db: Any, oxi_store: Any, ttl: str, body: str) -> None:
    sparql = f"PREFIX : <{_EX}>\n{body}"
    res = translate(sparql, resolver=SchemaResolver(ontology=SchemaResolver.from_turtle(ttl).ontology))
    db.aql.validate(res.aql)
    actual = list(db.aql.execute(res.aql, bind_vars=res.bind_vars))
    assert_bindings_equal([normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)], actual)
