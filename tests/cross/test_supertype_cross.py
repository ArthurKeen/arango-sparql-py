"""Cross-validation: supertype and label-array classes (LDBC plan decision D9).

``?m a :Message`` against the same facts in three stores:

* PG, ``Message`` a ``UNION`` supertype of the ``Post`` and ``Comment``
  collections (arango-query-core's style);
* LPG, ``Message`` a ``MULTI_LABEL`` class: one label in each node's
  ``labels`` array (arango-query-core's style; the analyzer's ``LABELS`` is
  the same field set);
* pyoxigraph, where every message is typed ``:Message`` explicitly.

Before the fix the PG form scanned a ``Message`` collection that does not
exist (no rows) and the LPG form had no label test (every node). The data
comes from :mod:`tests.cross.test_routing_cross`, so the stores agree.
"""

from __future__ import annotations

from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.cross.test_routing_cross import MESSAGES, _data_ttl, _lpg_docs, _pg_docs
from tests.helpers.aql_interp import run_aql_subset
from tests.helpers.oxi import assert_bindings_equal, drop_null_bindings, normalize_oxi_row, oxi_bindings

oxi = pytest.importorskip("pyoxigraph", reason="pyoxigraph required for cross tests")

_COMMON = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
:firstName a owl:DatatypeProperty ; rdfs:domain :Person .
:content a owl:DatatypeProperty ; rdfs:domain :Message .
"""
PG_ONTOLOGY = (
    _COMMON
    + """
:Person a owl:Class ; phys:collectionName "Person" .
:Post a owl:Class ; phys:collectionName "Post" .
:Comment a owl:Class ; phys:collectionName "Comment" .
:Message a owl:Class ; phys:mappingStyle "UNION" .
:Post rdfs:subClassOf :Message .
:Comment rdfs:subClassOf :Message .
:hasCreator a owl:ObjectProperty ; phys:edgeCollectionName "has_creator" ; rdfs:domain :Message ;
    rdfs:range :Person .
"""
)
LPG_ONTOLOGY = (
    _COMMON
    + """
:Person a owl:Class ; phys:collectionName "nodes" ; phys:mappingStyle "LABEL" ;
    phys:typeField "labels" ; phys:typeFieldIsArray "true" ; phys:typeValue "Person" .
:Message a owl:Class ; phys:collectionName "nodes" ; phys:mappingStyle "MULTI_LABEL" ;
    phys:labelsField "labels" ; phys:labelValue "Message" .
:hasCreator a owl:ObjectProperty ; phys:edgeCollectionName "edges" ; phys:mappingStyle "GENERIC_WITH_TYPE" ;
    phys:typeField "type" ; phys:typeValue "HAS_CREATOR" ; rdfs:domain :Message ; rdfs:range :Person .
"""
)
MODELS = [
    pytest.param(PG_ONTOLOGY, _pg_docs, id="pg-union"),
    pytest.param(LPG_ONTOLOGY, _lpg_docs, id="lpg-multi-label"),
]


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    typed = "\n".join(f"<http://ex.org/{m}> a <http://ex.org/Message> ." for m, *_ in MESSAGES)
    store = oxi.Store()
    store.load((_data_ttl() + "\n" + typed).encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


CASES = [
    pytest.param("SELECT ?m WHERE { ?m a :Message }", id="every_message"),
    pytest.param("SELECT ?m ?c WHERE { ?m a :Message ; :content ?c }", id="type_and_attribute"),
    pytest.param(
        "SELECT ?m ?n WHERE { ?m a :Message ; :hasCreator ?p . ?p :firstName ?n }", id="type_then_edge"
    ),
    pytest.param("SELECT ?m WHERE { ?m a :Message ; :hasCreator :bob }", id="type_and_constant_object"),
    pytest.param("SELECT ?c WHERE { :comm1 a :Message ; :content ?c }", id="constant_subject"),
    pytest.param("SELECT (COUNT(?m) AS ?n) WHERE { ?m a :Message }", id="count"),
    pytest.param("SELECT ?c WHERE { ?m :content ?c }", id="supertype_domain"),
]


@pytest.mark.cross
@pytest.mark.parametrize("ttl,docs", MODELS)
@pytest.mark.parametrize("body", CASES)
def test_supertypes_match_oxigraph(oxi_store: Any, ttl: str, docs: Any, body: str) -> None:
    sparql = "PREFIX : <http://ex.org/>\n" + body
    expected = [normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert expected, "the case must have a non-empty answer to prove anything"
    resolver = SchemaResolver(
        ontology=SchemaResolver.from_turtle(ttl).ontology, strict_subject_resolution=True
    )
    result = translate(sparql, resolver=resolver)
    actual = [drop_null_bindings(r) for r in run_aql_subset(result.aql, result.bind_vars, docs())]
    assert_bindings_equal(expected, actual)
