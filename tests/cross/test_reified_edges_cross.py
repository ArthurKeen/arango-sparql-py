"""Cross-validation: relationships that carry data (LDBC plan decision D8).

RDF reifies a relationship with data as a node — LDBC writes
``?p :knows ?k . ?k :hasPerson ?f ; :creationDate ?d`` — while a property
graph stores it as an edge with attributes. The mapping says so with
``phys:reifiedEdge`` on the entry predicate (``:knows``: its object is the
edge) and ``phys:edgeTargetOf`` on the exit predicates (``:hasPerson``: from
the edge to its ``_to`` vertex). The edge keeps the node's IRI as ``_uri``.

Each query runs against the same facts as RDF (pyoxigraph) and as PG and LPG
documents (the AQL interpreter); the stores derive from :data:`KNOWS` and
:data:`LIKES`, so they cannot drift.
"""

from __future__ import annotations

from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.errors import UnsupportedSparqlError
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.aql_interp import run_aql_subset
from tests.helpers.oxi import assert_bindings_equal, drop_null_bindings, normalize_oxi_row, oxi_bindings

oxi = pytest.importorskip("pyoxigraph", reason="pyoxigraph required for cross tests")

EX = "http://ex.org/"
PEOPLE = {"alice": "Alice", "bob": "Bob", "carol": "Carol"}
MESSAGES = {"post1": "Post", "comm1": "Comment"}
#: (node, person, person, creationDate)
KNOWS = [("k1", "alice", "bob", 2010), ("k2", "carol", "alice", 2011)]
#: (node, person, message, creationDate)
LIKES = [("l1", "bob", "post1", 2012), ("l2", "bob", "comm1", 2013), ("l3", "alice", "post1", 2014)]


def _data_ttl() -> str:
    lines = ["@prefix : <http://ex.org/> ."]
    lines += [f':{p} a :Person ; :firstName "{n}" .' for p, n in PEOPLE.items()]
    lines += [f":{m} a :{cls} ." for m, cls in MESSAGES.items()]
    for node, a, b, date in KNOWS:
        lines.append(f":{a} :knows :{node} . :{node} :hasPerson :{b} ; :creationDate {date} .")
    for node, p, m, date in LIKES:
        exit_ = "hasPost" if MESSAGES[m] == "Post" else "hasComment"
        lines.append(f":{p} :likes :{node} . :{node} :{exit_} :{m} ; :creationDate {date} .")
    return "\n".join(lines)


def _pg_docs() -> dict[str, list[dict[str, Any]]]:
    return {
        "Person": [{"_id": f"Person/{p}", "_uri": EX + p, "firstName": n} for p, n in PEOPLE.items()],
        "Post": [{"_id": f"Post/{m}", "_uri": EX + m} for m, c in MESSAGES.items() if c == "Post"],
        "Comment": [{"_id": f"Comment/{m}", "_uri": EX + m} for m, c in MESSAGES.items() if c == "Comment"],
        "knows": [
            {
                "_id": f"knows/{k}",
                "_uri": EX + k,
                "_from": f"Person/{a}",
                "_to": f"Person/{b}",
                "creationDate": d,
            }
            for k, a, b, d in KNOWS
        ],
        "likes": [
            {
                "_id": f"likes/{k}",
                "_uri": EX + k,
                "_from": f"Person/{p}",
                "_to": f"{MESSAGES[m]}/{m}",
                "creationDate": d,
            }
            for k, p, m, d in LIKES
        ],
    }


def _lpg_docs() -> dict[str, list[dict[str, Any]]]:
    nodes = [
        {"_id": f"nodes/{p}", "_uri": EX + p, "labels": ["Person"], "firstName": n} for p, n in PEOPLE.items()
    ]
    nodes += [{"_id": f"nodes/{m}", "_uri": EX + m, "labels": [c, "Message"]} for m, c in MESSAGES.items()]
    edges = [
        {
            "_id": f"edges/{k}",
            "_uri": EX + k,
            "_from": f"nodes/{a}",
            "_to": f"nodes/{b}",
            "type": "KNOWS",
            "creationDate": d,
        }
        for k, a, b, d in KNOWS
    ] + [
        {
            "_id": f"edges/{k}",
            "_uri": EX + k,
            "_from": f"nodes/{p}",
            "_to": f"nodes/{m}",
            "type": "LIKES",
            "creationDate": d,
        }
        for k, p, m, d in LIKES
    ]
    return {"nodes": nodes, "edges": edges}


_COMMON = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
:firstName a owl:DatatypeProperty ; rdfs:domain :Person .
:creationDate a owl:DatatypeProperty .
:hasPerson a owl:ObjectProperty ; phys:edgeTargetOf :knows ; rdfs:range :Person .
:hasPost a owl:ObjectProperty ; phys:edgeTargetOf :likes ; rdfs:range :Post .
:hasComment a owl:ObjectProperty ; phys:edgeTargetOf :likes ; rdfs:range :Comment .
:hasOrganisation a owl:ObjectProperty ; phys:edgeTargetOf :workAt .
"""
PG_ONTOLOGY = (
    _COMMON
    + """
:Person a owl:Class ; phys:collectionName "Person" .
:Post a owl:Class ; phys:collectionName "Post" .
:Comment a owl:Class ; phys:collectionName "Comment" .
:knows a owl:ObjectProperty ; phys:edgeCollectionName "knows" ; phys:reifiedEdge "true" ; rdfs:domain :Person .
:likes a owl:ObjectProperty ; phys:edgeCollectionName "likes" ; phys:reifiedEdge "true" ; rdfs:domain :Person .
"""
)
LPG_ONTOLOGY = (
    _COMMON
    + """
:Person a owl:Class ; phys:collectionName "nodes" ; phys:mappingStyle "LABEL" ;
    phys:typeField "labels" ; phys:typeFieldIsArray "true" ; phys:typeValue "Person" .
:Post a owl:Class ; phys:collectionName "nodes" ; phys:mappingStyle "LABEL" ;
    phys:typeField "labels" ; phys:typeFieldIsArray "true" ; phys:typeValue "Post" .
:Comment a owl:Class ; phys:collectionName "nodes" ; phys:mappingStyle "LABEL" ;
    phys:typeField "labels" ; phys:typeFieldIsArray "true" ; phys:typeValue "Comment" .
:knows a owl:ObjectProperty ; phys:edgeCollectionName "edges" ; phys:mappingStyle "GENERIC_WITH_TYPE" ;
    phys:typeField "type" ; phys:typeValue "KNOWS" ; phys:reifiedEdge "true" ; rdfs:domain :Person .
:likes a owl:ObjectProperty ; phys:edgeCollectionName "edges" ; phys:mappingStyle "GENERIC_WITH_TYPE" ;
    phys:typeField "type" ; phys:typeValue "LIKES" ; phys:reifiedEdge "true" ; rdfs:domain :Person .
"""
)
MODELS = [pytest.param(PG_ONTOLOGY, _pg_docs, id="pg"), pytest.param(LPG_ONTOLOGY, _lpg_docs, id="lpg")]


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    store = oxi.Store()
    store.load(_data_ttl().encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


def _resolver(ttl: str) -> SchemaResolver:
    return SchemaResolver(ontology=SchemaResolver.from_turtle(ttl).ontology, strict_subject_resolution=True)


def _run(ttl: str, docs: Any, sparql: str) -> list[dict[str, Any]]:
    result = translate(sparql, resolver=_resolver(ttl))
    return [drop_null_bindings(r) for r in run_aql_subset(result.aql, result.bind_vars, docs())]


CASES = [
    pytest.param(
        "SELECT ?f ?d WHERE { :alice :knows ?k . ?k :hasPerson ?f ; :creationDate ?d }",
        id="entry_exit_attribute",
    ),
    pytest.param("SELECT ?p ?k WHERE { ?p :knows ?k }", id="the_node_is_the_edge"),
    pytest.param(
        "SELECT ?p ?f ?d WHERE { ?p :knows ?k . ?k :hasPerson ?f . ?k :creationDate ?d }",
        id="all_friendships",
    ),
    pytest.param(
        "SELECT ?n WHERE { ?p :knows ?k . ?k :hasPerson ?f . ?f :firstName ?n }",
        id="exit_then_vertex_attribute",
    ),
    pytest.param("SELECT ?m WHERE { ?p :likes ?lk . ?lk :hasPost ?m }", id="exit_range_keeps_posts"),
    pytest.param("SELECT ?m WHERE { ?p :likes ?lk . ?lk :hasComment ?m }", id="exit_range_keeps_comments"),
    pytest.param(
        "SELECT ?m ?d WHERE { :bob :likes ?lk . ?lk :hasPost|:hasComment ?m . ?lk :creationDate ?d }",
        id="alternative_exits_in_union_arms",
    ),
    pytest.param(
        "SELECT ?f WHERE { ?p :knows ?k . ?k :hasPerson ?f ; :creationDate ?d FILTER(?d > 2010) }",
        id="filter_on_edge_data",
    ),
    pytest.param("SELECT (COUNT(?lk) AS ?n) WHERE { ?p :likes ?lk }", id="count_relationship_nodes"),
]


@pytest.mark.cross
@pytest.mark.parametrize("ttl,docs", MODELS)
@pytest.mark.parametrize("body", CASES)
def test_reified_relationships_match_oxigraph(oxi_store: Any, ttl: str, docs: Any, body: str) -> None:
    sparql = "PREFIX : <http://ex.org/>\n" + body
    expected = [normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert expected, "the case must have a non-empty answer to prove anything"
    assert_bindings_equal(expected, _run(ttl, docs, sparql))


@pytest.mark.cross
@pytest.mark.parametrize("ttl,docs", MODELS)
def test_an_exit_of_another_relationship_matches_nothing(oxi_store: Any, ttl: str, docs: Any) -> None:
    # A knows node has no hasOrganisation in RDF; the edge must not invent one.
    sparql = "PREFIX : <http://ex.org/>\nSELECT ?o WHERE { ?p :knows ?k . ?k :hasOrganisation ?o }"
    assert list(oxi_bindings(oxi_store, sparql)) == []
    assert _run(ttl, docs, sparql) == []


@pytest.mark.parametrize("ttl", [PG_ONTOLOGY, LPG_ONTOLOGY], ids=["pg", "lpg"])
def test_an_exit_without_its_entry_is_refused(ttl: str) -> None:
    sparql = "PREFIX : <http://ex.org/>\nSELECT ?f WHERE { ?k :hasPerson ?f }"
    with pytest.raises(UnsupportedSparqlError, match="entry predicate"):
        translate(sparql, resolver=_resolver(ttl))
