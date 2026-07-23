"""Cross-validation for object-property *edge-collection traversal*.

:mod:`tests.cross.test_multimodel_cross` already cross-checks PG / LPG /
RPT, but it maps the ``owner`` object property as an **inline attribute**
(``doc.owner = <iri>``), so the join there is a value equality, not a
graph traversal. The translator's *other* object-property lowering —
``FOR v, e IN OUTBOUND <s> @@edgeColl`` for collection-backed edges
(PRD §6.1, golden corpus ``tests/translate/edge_traversal.yml``) — was
only ever verified by golden AQL strings, never by executing it and
comparing bindings to the W3C ground truth.

This module closes that gap. The *same* ``owner`` facts are stored two
ways the golden corpus distinguishes:

- **DEDICATED_COLLECTION** (PG-typed edge): one ``owner`` edge
  collection → bare ``OUTBOUND``.
- **GENERIC_WITH_TYPE** (LPG-typed edge): a shared ``edges`` collection
  with a ``type`` discriminator → ``OUTBOUND`` + ``FILTER e.type == …``.

Both must yield the bindings pyoxigraph produces for the identical
``Project ⋈ Person`` join, proving the traversal emitter — and the
interpreter's OUTBOUND execution — are W3C-faithful regardless of edge
style. All three views (two stores + the RDF dataset) derive from one
:data:`FACTS` source of truth so they cannot silently drift apart.
"""

from __future__ import annotations

from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.aql_interp import run_aql_subset
from tests.helpers.oxi import (
    assert_bindings_equal,
    drop_null_bindings,
    normalize_oxi_row,
    oxi_bindings,
)

oxi = pytest.importorskip("pyoxigraph", reason="pyoxigraph required for cross tests")

EX = "http://ex.org/"

# Single source of truth. Each person carries a datatype property
# (``name``); each project carries a datatype property (``title``) and
# an ``owner`` *object* property pointing at a person IRI. ``p4`` is an
# orphan (no owner) so the traversal must drop it — exactly as
# pyoxigraph drops the solution with an unbound object.
PEOPLE = [
    {"local": "alice", "name": "Alice", "age": 30},
    {"local": "bob", "name": "Bob", "age": 42},
    {"local": "carol", "name": "Carol", "age": 30},
]
PROJECTS = [
    {"local": "p1", "title": "Apollo", "owner": "alice"},
    {"local": "p2", "title": "Beacon", "owner": "bob"},
    {"local": "p3", "title": "Catalyst", "owner": "alice"},
    {"local": "p4", "title": "Orphan", "owner": None},
]


def _data_ttl() -> str:
    lines = ["@prefix : <http://ex.org/> ."]
    for p in PEOPLE:
        lines.append(f':{p["local"]} a :Person ; :name "{p["name"]}" ; :age {p["age"]} .')
    for pr in PROJECTS:
        triple = f':{pr["local"]} a :Project ; :title "{pr["title"]}"'
        if pr["owner"]:
            triple += f" ; :owner :{pr['owner']}"
        lines.append(triple + " .")
    return "\n".join(lines)


# ---------------------------------------------------------------- stores
# Vertices carry a physical ``_id`` handle (``<coll>/<key>``) and the
# semantic ``_uri`` the translator projects; edges reference vertices by
# those handles via ``_from`` / ``_to`` — the shape ArangoDB traverses.


def _pg_dedicated_docs() -> dict[str, list[dict[str, Any]]]:
    # PG: collection-per-class vertices + a dedicated ``owner`` edge
    # collection (no discriminator).
    persons = [
        {"_id": f"Person/{p['local']}", "_uri": EX + p["local"], "name": p["name"], "age": p["age"]}
        for p in PEOPLE
    ]
    projects = [
        {"_id": f"Project/{pr['local']}", "_uri": EX + pr["local"], "title": pr["title"]} for pr in PROJECTS
    ]
    owner_edges = [
        {"_from": f"Project/{pr['local']}", "_to": f"Person/{pr['owner']}"} for pr in PROJECTS if pr["owner"]
    ]
    return {"Person": persons, "Project": projects, "owner": owner_edges}


def _lpg_generic_docs() -> dict[str, list[dict[str, Any]]]:
    # LPG: a shared ``vertices`` collection with a ``type`` discriminator
    # and a shared ``edges`` collection whose ``type`` field names the
    # relationship — the GENERIC_WITH_TYPE shape.
    vertices = [
        {
            "_id": f"vertices/{p['local']}",
            "_uri": EX + p["local"],
            "type": "Person",
            "name": p["name"],
            "age": p["age"],
        }
        for p in PEOPLE
    ] + [
        {
            "_id": f"vertices/{pr['local']}",
            "_uri": EX + pr["local"],
            "type": "Project",
            "title": pr["title"],
        }
        for pr in PROJECTS
    ]
    edges = [
        {"_from": f"vertices/{pr['local']}", "_to": f"vertices/{pr['owner']}", "type": "owner"}
        for pr in PROJECTS
        if pr["owner"]
    ]
    return {"vertices": vertices, "edges": edges}


PG_DEDICATED_ONTOLOGY = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Person a owl:Class ; phys:collectionName "Person" .
:Project a owl:Class ; phys:collectionName "Project" .
:owner a owl:ObjectProperty ; phys:edgeCollectionName "owner" .
"""

LPG_GENERIC_ONTOLOGY = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Person a owl:Class ;
    phys:collectionName "vertices" ;
    phys:mappingStyle "LABEL" ;
    phys:typeField "type" ;
    phys:typeValue "Person" .
:Project a owl:Class ;
    phys:collectionName "vertices" ;
    phys:mappingStyle "LABEL" ;
    phys:typeField "type" ;
    phys:typeValue "Project" .
:owner a owl:ObjectProperty ;
    phys:edgeCollectionName "edges" ;
    phys:typeField "type" ;
    phys:typeValue "owner" .
"""

MODELS = [
    pytest.param(PG_DEDICATED_ONTOLOGY, _pg_dedicated_docs, id="pg_dedicated_edge"),
    pytest.param(LPG_GENERIC_ONTOLOGY, _lpg_generic_docs, id="lpg_generic_edge"),
]

# Join queries that force an OUTBOUND traversal of ``:owner``. These
# mirror the inline-attribute JOIN_CASES in test_multimodel_cross so the
# only difference under test is the physical edge representation.
JOIN_CASES = [
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?prj ?p WHERE { ?prj a :Project ; :owner ?p }",
        id="traverse_to_owner_uri",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?t WHERE { "
        "?prj a :Project ; :title ?t ; :owner ?p . "
        "?p a :Person ; :name ?n }",
        id="project_owner_join_person_name",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?t WHERE { "
        "?prj a :Project ; :title ?t ; :owner ?p . "
        '?p a :Person ; :name ?n . FILTER(?n = "Alice") }',
        id="join_filtered_on_target_name",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?t WHERE { "
        "?prj a :Project ; :title ?t ; :owner ?p . "
        "?p a :Person ; :name ?n ; :age ?a . FILTER(?a > 30) }",
        id="join_filtered_on_target_age",
    ),
]


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    store = oxi.Store()
    store.load(_data_ttl().encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


def _run_model(ontology_ttl: str, docs_factory: Any, sparql: str) -> list[dict[str, Any]]:
    resolver = SchemaResolver.from_turtle(ontology_ttl)
    result = translate(sparql, resolver=resolver)
    docs = docs_factory()
    return [drop_null_bindings(r) for r in run_aql_subset(result.aql, result.bind_vars, docs)]


@pytest.mark.cross
@pytest.mark.parametrize("ontology_ttl,docs_factory", MODELS)
@pytest.mark.parametrize("sparql", JOIN_CASES)
def test_edge_traversal_join_matches_oxigraph(
    oxi_store: Any,
    ontology_ttl: str,
    docs_factory: Any,
    sparql: str,
) -> None:
    actual = _run_model(ontology_ttl, docs_factory, sparql)
    expected = [normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert_bindings_equal(expected, actual)
