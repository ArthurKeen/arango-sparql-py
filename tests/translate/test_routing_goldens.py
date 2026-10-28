"""AQL shapes for routing subjects without type patterns.

Semantics are cross-checked against pyoxigraph in
``tests/cross/test_routing_cross.py``; these pin what the translator emits:
``ANY`` for an ``owl:SymmetricProperty``, ``INBOUND`` from a known object,
an ``rdfs:range`` lookup for a constant object, and an ``rdfs:domain``
collection (with an any-of label test for an LPG union).
"""

from __future__ import annotations

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.cross.test_routing_cross import LPG_ONTOLOGY, PG_ONTOLOGY


def _aql(ttl: str, body: str) -> str:
    resolver = SchemaResolver(
        ontology=SchemaResolver.from_turtle(ttl).ontology, strict_subject_resolution=True
    )
    return translate("PREFIX : <http://ex.org/>\n" + body, resolver=resolver).aql


def test_a_symmetric_property_from_a_domain_routed_constant_traverses_any() -> None:
    assert _aql(PG_ONTOLOGY, "SELECT ?f WHERE { :alice :directKnows ?f }") == (
        "FOR doc1 IN @@c1_Person\n"
        "FILTER (doc1._uri == @_p1_uri || (doc1._uri == null && doc1._id == @_p1_uri))\n"
        "FOR v2, e3 IN ANY doc1 @@c2_knows\n"
        "RETURN { f: NOT_NULL(v2._uri, v2._id) }"
    )


def test_a_constant_object_is_looked_up_in_its_range_and_walked_inbound() -> None:
    assert _aql(PG_ONTOLOGY, "SELECT ?m WHERE { ?m :hasCreator :bob }") == (
        "FOR doc1 IN @@c1_Person\n"
        "FILTER (doc1._uri == @_p1_uri || (doc1._uri == null && doc1._id == @_p1_uri))\n"
        "FOR v2, e3 IN INBOUND doc1 @@c2_has_creator\n"
        # The forward plan reads ?m from hasCreator's domain (Post ∪ Comment).
        "FILTER (IS_SAME_COLLECTION(@_p2_coll, v2) || IS_SAME_COLLECTION(@_p3_coll, v2))\n"
        "RETURN { m: NOT_NULL(v2._uri, v2._id) }"
    )


def test_a_reverse_walk_keeps_only_the_subjects_collections() -> None:
    """W3C ``sparqldl-07``: a resource stored in two collections, each copy with
    its own edges, must not be reached twice by walking an edge backwards."""
    resolver = SchemaResolver.from_turtle(PG_ONTOLOGY)
    result = translate(
        "PREFIX : <http://ex.org/>\nSELECT ?m WHERE { ?m :hasCreator :bob }", resolver=resolver
    )
    assert {result.bind_vars["_p2_coll"], result.bind_vars["_p3_coll"]} == {"Comment", "Post"}


def test_an_lpg_union_domain_is_an_any_of_label_test() -> None:
    aql = _aql(LPG_ONTOLOGY, "SELECT ?c WHERE { VALUES ?m { :post2 } ?m :content ?c }")
    assert "FOR doc" in aql and "IN @@c1_nodes" in aql
    assert "(@_p" in aql and " IN doc" in aql and " || " in aql
