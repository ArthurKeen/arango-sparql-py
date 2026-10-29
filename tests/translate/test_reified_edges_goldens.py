"""Relationships that carry data, stored as edges (LDBC plan decision D8).

Semantics are cross-checked against pyoxigraph in
``tests/cross/test_reified_edges_cross.py``; these pin the mapping vocabulary
(``phys:reifiedEdge``, ``phys:edgeTargetOf``), the join order and the AQL: one
traversal per relationship, its node bound to the edge, the exit's vertex
taken from that same traversal.
"""

from __future__ import annotations

import pytest
from rdflib import URIRef, Variable

from arango_sparql.api import translate
from arango_sparql.errors import SchemaResolutionError
from arango_sparql.translate.resolver import SchemaResolver
from arango_sparql.translate.visitor import _order_bgp
from tests.cross.test_reified_edges_cross import PG_ONTOLOGY

EX = "http://ex.org/"


def _aql(body: str) -> str:
    resolver = SchemaResolver(
        ontology=SchemaResolver.from_turtle(PG_ONTOLOGY).ontology, strict_subject_resolution=True
    )
    return translate("PREFIX : <http://ex.org/>\n" + body, resolver=resolver).aql


def test_the_node_binds_the_edge_and_the_exit_its_far_vertex() -> None:
    assert _aql("SELECT ?f ?d WHERE { :alice :knows ?k . ?k :hasPerson ?f ; :creationDate ?d }") == (
        "FOR doc1 IN @@c1_Person\n"
        "FILTER (doc1._uri == @_p1_uri || (doc1._uri == null && doc1._id == @_p1_uri))\n"
        "FOR v2, e3 IN OUTBOUND doc1 @@c2_knows\n"
        'FILTER HAS(e3, "creationDate")\n'
        "FILTER IS_SAME_COLLECTION(@_p2_coll, v2)\n"
        "RETURN { f: NOT_NULL(v2._uri, v2._id), d: e3.creationDate }"
    )


def test_an_exit_range_keeps_only_vertices_of_that_class() -> None:
    # likes reaches a Post or a Comment; hasPost keeps the Posts.
    assert _aql("SELECT ?m WHERE { ?p :likes ?lk . ?lk :hasPost ?m }") == (
        "FOR doc1 IN @@c1_Person\n"
        "FOR v2, e3 IN OUTBOUND doc1 @@c2_likes\n"
        "FILTER IS_SAME_COLLECTION(@_p1_coll, v2)\n"
        "RETURN { m: NOT_NULL(v2._uri, v2._id) }"
    )


def test_relationship_node_triples_wait_for_their_entry() -> None:
    # Written exit first, the entry still comes first: the exit and the
    # attribute triple need the node bound to an edge.
    k, f, d, p = Variable("k"), Variable("f"), Variable("d"), Variable("p")
    exit_ = (k, URIRef(EX + "hasPerson"), f)
    attr = (k, URIRef(EX + "creationDate"), d)
    entry = (p, URIRef(EX + "knows"), k)
    assert _order_bgp([exit_, attr, entry], set(), {"k"}) == [entry, exit_, attr]


def test_the_vocabulary_resolves() -> None:
    resolver = SchemaResolver.from_turtle(
        PG_ONTOLOGY
        + ':hasMember a owl:ObjectProperty ; phys:edgeCollectionName "has_member" ; phys:reifiedEdge "true" .\n'
        ":hasPerson phys:edgeTargetOf :hasMember .\n"
    )
    knows = resolver.resolve_property(EX + "knows")
    assert knows.reified_edge and knows.edge_collection == "knows"
    assert resolver.resolve_property(EX + "hasPerson").edge_target_of == (EX + "hasMember", EX + "knows")
    assert resolver.is_reified_entry(EX + "likes")
    assert not resolver.is_reified_entry(EX + "hasPerson")


def test_a_reified_relationship_needs_an_edge_collection() -> None:
    resolver = SchemaResolver.from_turtle(
        PG_ONTOLOGY + ':studyAt a owl:ObjectProperty ; phys:reifiedEdge "true" .\n'
    )
    with pytest.raises(SchemaResolutionError, match="edgeCollectionName"):
        resolver.resolve_property(EX + "studyAt")
