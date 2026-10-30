"""The join order that avoids rescanning (LDBC IC10's plan).

Answers are cross-checked against pyoxigraph in
``tests/cross/test_join_order_cross.py``; these pin the plans: each
collection read once, a type pattern on a document already read becomes a
class test on it, an edge is walked backwards from a known object before
the subject's class is scanned, and ``MINUS`` / ``EXISTS`` probes read the
outer document.
"""

from __future__ import annotations

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.cross.test_join_order_cross import LPG_ONTOLOGY, PG_ONTOLOGY


def _aql(body: str, ttl: str = PG_ONTOLOGY) -> str:
    return translate("PREFIX : <http://ex.org/>\n" + body, resolver=SchemaResolver.from_turtle(ttl)).aql


def test_a_known_object_is_walked_back_from_and_the_type_becomes_a_class_test() -> None:
    # Before: Post scanned, then joined to ?f through an OUTBOUND walk per post.
    assert _aql('SELECT ?m WHERE { ?f a :Person ; :firstName "Bob" . ?m a :Post ; :hasCreator ?f }') == (
        "FOR doc1 IN @@c1_Person\n"
        "FILTER doc1.firstName == @_p1_firstName\n"
        "FOR v2, e3 IN INBOUND doc1 @@c2_has_creator\n"
        "FILTER (IS_SAME_COLLECTION(@_p2_coll, v2) || IS_SAME_COLLECTION(@_p3_coll, v2))\n"
        "FILTER IS_SAME_COLLECTION(@_p4_coll, v2)\n"
        "RETURN { m: NOT_NULL(v2._uri, v2._id) }"
    )


def test_a_supertype_the_domain_filter_already_implies_adds_no_test() -> None:
    # hasCreator's domain keeps ?m to Comment or Post, the members of Message.
    assert _aql("SELECT ?m WHERE { ?m a :Message ; :hasCreator :bob }") == (
        "FOR doc1 IN @@c1_Person\n"
        "FILTER (doc1._uri == @_p1_uri || (doc1._uri == null && doc1._id == @_p1_uri))\n"
        "FOR v2, e3 IN INBOUND doc1 @@c2_has_creator\n"
        "FILTER (IS_SAME_COLLECTION(@_p2_coll, v2) || IS_SAME_COLLECTION(@_p3_coll, v2))\n"
        "RETURN { m: NOT_NULL(v2._uri, v2._id) }"
    )


def test_on_lpg_the_class_test_is_the_label_alone() -> None:
    # The domain filter already keeps ?m in nodes; only Post's label is left.
    aql = _aql("SELECT ?m WHERE { ?m a :Post ; :hasCreator :bob }", LPG_ONTOLOGY)
    assert aql.count("IS_SAME_COLLECTION") == 1
    assert "FILTER @_p5_labels IN v2.labels" in aql


def test_with_no_range_to_find_the_object_in_the_type_pattern_comes_first() -> None:
    # follows declares no range, so :bob cannot be looked up; walking from ?a's
    # class is the plan, not a scan of the default collection.
    assert _aql("SELECT ?a WHERE { ?a a :Person . ?a :follows :bob }") == (
        "FOR doc1 IN @@c1_Person\n"
        "FOR v2, e3 IN OUTBOUND doc1 @@c2_follows\n"
        "FILTER (v2._uri == @_p1_uri || (v2._uri == null && v2._id == @_p1_uri))\n"
        "RETURN { a: NOT_NULL(doc1._uri, doc1._id) }"
    )


def test_a_probe_walks_from_the_outer_document() -> None:
    # Before: the probe scanned Person again and joined it to doc1 on identity.
    assert _aql("SELECT ?p WHERE { ?p a :Person FILTER NOT EXISTS { ?p :knows ?q } }") == (
        "FOR doc1 IN @@c1_Person\n"
        "LET not_exists_probe4 = LENGTH((\n"
        "  FOR v2, e3 IN OUTBOUND doc1 @@c2_knows\n"
        "  LIMIT 1\n"
        "  RETURN 1\n"
        "))\n"
        "FILTER not_exists_probe4 == 0\n"
        "RETURN { p: NOT_NULL(doc1._uri, doc1._id) }"
    )


def test_a_probe_that_only_reads_the_outer_document_gets_a_one_row_scope() -> None:
    assert _aql("SELECT ?p WHERE { ?p a :Person FILTER EXISTS { ?p a :Person ; :firstName ?n } }") == (
        "FOR doc1 IN @@c1_Person\n"
        "LET exists_probe3 = LENGTH((\n"
        '  FILTER HAS(doc1, "firstName")\n'
        "  FOR empty2 IN [1]\n"
        "  LIMIT 1\n"
        "  RETURN 1\n"
        "))\n"
        "FILTER exists_probe3 > 0\n"
        "RETURN { p: NOT_NULL(doc1._uri, doc1._id) }"
    )
