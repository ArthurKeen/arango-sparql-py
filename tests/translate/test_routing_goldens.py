"""AQL shapes for routing subjects without type patterns.

Semantics are cross-checked against pyoxigraph in
``tests/cross/test_routing_cross.py``; these pin what the translator emits:
``ANY`` for an ``owl:SymmetricProperty``, ``INBOUND`` from a known object,
an ``rdfs:range`` lookup for a constant object, and an ``rdfs:domain``
collection (with an any-of label test for an LPG union, and a ``UNION`` of
the collections for a PG one: plan decision D9).
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


def test_a_pg_union_domain_reads_each_collection() -> None:
    # Plan D9: ?m is a Post or a Comment, so both collections are scanned and
    # the traversal starts from either kind of document.
    assert _aql(PG_ONTOLOGY, "SELECT ?m ?n WHERE { ?m :hasCreator ?p . ?p :firstName ?n }") == (
        "FOR doc3 IN UNION(\n"
        "(\n"
        "  FOR doc1 IN @@c1_Comment\n"
        "  RETURN doc1\n"
        "),\n"
        "(\n"
        "  FOR doc2 IN @@c2_Post\n"
        "  RETURN doc2\n"
        ")\n"
        ")\n"
        "FOR v4, e5 IN OUTBOUND doc3 @@c3_has_creator\n"
        'FILTER HAS(v4, "firstName")\n'
        "RETURN { m: NOT_NULL(doc3._uri, doc3._id), n: v4.firstName }"
    )


def test_a_constant_subject_in_a_pg_union_domain_is_looked_up_in_each_collection() -> None:
    # The indexed _uri match sits inside each arm, so each collection's index
    # serves it, instead of filtering the union of two full scans.
    assert _aql(PG_ONTOLOGY, "SELECT ?c WHERE { :comm1 :content ?c }") == (
        "FOR doc3 IN UNION(\n"
        "(\n"
        "  FOR doc1 IN @@c1_Comment\n"
        "  FILTER (doc1._uri == @_p1_uri || (doc1._uri == null && doc1._id == @_p1_uri))\n"
        "  RETURN doc1\n"
        "),\n"
        "(\n"
        "  FOR doc2 IN @@c2_Post\n"
        "  FILTER (doc2._uri == @_p1_uri || (doc2._uri == null && doc2._id == @_p1_uri))\n"
        "  RETURN doc2\n"
        ")\n"
        ")\n"
        'FILTER HAS(doc3, "content")\n'
        "RETURN { c: doc3.content }"
    )


def test_a_pg_union_row_counts_as_a_scanned_document() -> None:
    # Every union row is a whole document, never null, so COUNT(?m) counts
    # rows (issue #46) as it would for a single-collection scan.
    aql = _aql(PG_ONTOLOGY, "SELECT (COUNT(?m) AS ?n) WHERE { ?m :content ?c }")
    assert aql.endswith("COLLECT WITH COUNT INTO agg4\nRETURN { n: agg4 }")
