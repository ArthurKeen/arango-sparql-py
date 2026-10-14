"""Issue #18 — a document's identity is ``_uri`` when present, else ``_id``.

Emission-level guard for the fast suite; ``tests/integration/
test_subject_identity_live.py`` proves the behaviour on a real ArangoDB
(distinct non-null subjects, the correct ``COUNT(DISTINCT ?s)``, joins across
edges, constants).
"""

from __future__ import annotations

from arango_sparql.api import translate
from arango_sparql.translate.builder import subject_in, subject_matches, subject_ref
from arango_sparql.translate.resolver import SchemaResolver

_TTL = """
@prefix :     <http://example.org/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix phys: <https://arango.solutions/phys#> .
:Asset a owl:Class ; phys:mappingStyle "COLLECTION" ; phys:collectionName "Assets" .
:Owner a owl:Class ; phys:mappingStyle "COLLECTION" ; phys:collectionName "Owners" .
:owns a owl:ObjectProperty ; rdfs:domain :Owner ; rdfs:range :Asset ; phys:edgeCollectionName "owns" .
"""


def _aql(body: str, **kw) -> str:
    resolver = SchemaResolver.from_turtle(_TTL, **kw)
    return translate("PREFIX : <http://example.org/>\n" + body, resolver=resolver).aql


def test_identity_falls_back_to_id() -> None:
    assert subject_ref("doc1") == "NOT_NULL(doc1._uri, doc1._id)"


def test_constant_matching_is_exact_and_index_friendly() -> None:
    # A document with a _uri is never matched by its _id; both sides stay
    # plain attribute comparisons the optimizer can use.
    assert subject_matches("d", "@p") == "(d._uri == @p || (d._uri == null && d._id == @p))"
    assert subject_in("d", "[@a]") == "(d._uri IN [@a] || (d._uri == null && d._id IN [@a]))"


def test_projected_subject_uses_the_identity() -> None:
    assert "RETURN { s: NOT_NULL(doc1._uri, doc1._id) }" in _aql("SELECT ?s WHERE { ?s a :Asset }")


def test_edge_target_and_bound_object_compare_the_same_identity() -> None:
    aql = _aql("SELECT ?o ?a WHERE { ?a a :Asset . ?o a :Owner ; :owns ?a . }")
    # The traversal vertex is joined to the already-bound ?a with the SAME
    # identity expression on both sides — not _uri on one and _id on the other.
    assert "FILTER NOT_NULL(v3._uri, v3._id) == NOT_NULL(doc1._uri, doc1._id)" in aql


def test_constant_subject_uses_the_exact_match_form() -> None:
    aql = _aql("SELECT ?n WHERE { <Assets/a2> :name ?n . }", default_collection="Assets")
    assert "._uri == @" in aql and "._uri == null && " in aql and "._id == @" in aql
