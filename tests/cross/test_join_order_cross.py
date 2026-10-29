"""Cross-validation: the join order that avoids rescanning (LDBC IC10's plan).

Three changes make a BGP read each collection once where it can:

* a type pattern on a subject already read is a class test on that document,
  not a second scan joined back to it;
* an edge-backed triple whose object is known (a document, or a constant or
  value looked up in the property's ``rdfs:range``) walks backwards from it
  before the subject's type pattern scans every member of the class; with no
  range to look the object up in, the type pattern still comes first;
* a ``MINUS`` / ``EXISTS`` probe reads the outer query's documents instead of
  scanning their collections again.

Each query runs against the same facts as RDF (pyoxigraph) and as PG and LPG
documents (the AQL interpreter); both stores derive from the tables below.
"""

from __future__ import annotations

from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.aql_interp import run_aql_subset
from tests.helpers.oxi import assert_bindings_equal, drop_null_bindings, normalize_oxi_row, oxi_bindings

oxi = pytest.importorskip("pyoxigraph", reason="pyoxigraph required for cross tests")

EX = "http://ex.org/"
PEOPLE = {"alice": "Alice", "bob": "Bob", "carol": "Carol"}
CITIES = ["paris", "rome"]
#: message -> (class, creator)
MESSAGES = {"post1": ("Post", "bob"), "comm1": ("Comment", "bob"), "post2": ("Post", "alice")}
KNOWS = [("alice", "bob"), ("carol", "alice")]
LOCATED = [("alice", "paris"), ("bob", "paris"), ("carol", "rome")]
#: An edge with no domain or range declared.
FOLLOWS = [("alice", "bob"), ("carol", "bob")]


def _data_ttl() -> str:
    lines = ["@prefix : <http://ex.org/> ."]
    lines += [f':{p} a :Person ; :firstName "{n}" .' for p, n in PEOPLE.items()]
    lines += [f":{c} a :City ." for c in CITIES]
    lines += [f":{m} a :{cls}, :Message ; :hasCreator :{p} ." for m, (cls, p) in MESSAGES.items()]
    lines += [f":{a} :knows :{b} ." for a, b in KNOWS]
    lines += [f":{p} :isLocatedIn :{c} ." for p, c in LOCATED]
    lines += [f":{a} :follows :{b} ." for a, b in FOLLOWS]
    return "\n".join(lines)


def _edge(coll: str, src: str, dst: str, **extra: Any) -> dict[str, Any]:
    return {"_id": f"{coll}/{src}-{dst}", "_from": src, "_to": dst, **extra}


def _pg_docs() -> dict[str, list[dict[str, Any]]]:
    def ref(name: str) -> str:
        if name in PEOPLE:
            return f"Person/{name}"
        if name in CITIES:
            return f"City/{name}"
        return f"{MESSAGES[name][0]}/{name}"

    docs: dict[str, list[dict[str, Any]]] = {
        "Person": [{"_id": f"Person/{p}", "_uri": EX + p, "firstName": n} for p, n in PEOPLE.items()],
        "City": [{"_id": f"City/{c}", "_uri": EX + c} for c in CITIES],
        "Post": [],
        "Comment": [],
    }
    for m, (cls, _p) in MESSAGES.items():
        docs[cls].append({"_id": f"{cls}/{m}", "_uri": EX + m})
    docs["has_creator"] = [_edge("has_creator", ref(m), ref(p)) for m, (_c, p) in MESSAGES.items()]
    docs["knows"] = [_edge("knows", ref(a), ref(b)) for a, b in KNOWS]
    docs["is_located_in"] = [_edge("is_located_in", ref(p), ref(c)) for p, c in LOCATED]
    docs["follows"] = [_edge("follows", ref(a), ref(b)) for a, b in FOLLOWS]
    return docs


def _lpg_docs() -> dict[str, list[dict[str, Any]]]:
    nodes = [
        {"_id": f"nodes/{p}", "_uri": EX + p, "labels": ["Person"], "firstName": n} for p, n in PEOPLE.items()
    ]
    nodes += [{"_id": f"nodes/{c}", "_uri": EX + c, "labels": ["City"]} for c in CITIES]
    nodes += [
        {"_id": f"nodes/{m}", "_uri": EX + m, "labels": [cls, "Message"]} for m, (cls, _p) in MESSAGES.items()
    ]
    edges = [
        _edge("edges", f"nodes/{m}", f"nodes/{p}", type="HAS_CREATOR") for m, (_c, p) in MESSAGES.items()
    ]
    edges += [_edge("edges", f"nodes/{a}", f"nodes/{b}", type="KNOWS") for a, b in KNOWS]
    edges += [_edge("edges", f"nodes/{p}", f"nodes/{c}", type="IS_LOCATED_IN") for p, c in LOCATED]
    edges += [_edge("edges", f"nodes/{a}", f"nodes/{b}", type="FOLLOWS") for a, b in FOLLOWS]
    return {"nodes": nodes, "edges": edges}


_COMMON = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
:firstName a owl:DatatypeProperty ; rdfs:domain :Person .
"""
PG_ONTOLOGY = (
    _COMMON
    + """
:Person a owl:Class ; phys:collectionName "Person" .
:City a owl:Class ; phys:collectionName "City" .
:Post a owl:Class ; phys:collectionName "Post" .
:Comment a owl:Class ; phys:collectionName "Comment" .
:Message a owl:Class ; phys:mappingStyle "UNION" .
:Post rdfs:subClassOf :Message .
:Comment rdfs:subClassOf :Message .
:hasCreator a owl:ObjectProperty ; phys:edgeCollectionName "has_creator" ;
    rdfs:domain :Message ; rdfs:range :Person .
:knows a owl:ObjectProperty ; phys:edgeCollectionName "knows" ; rdfs:domain :Person ; rdfs:range :Person .
:isLocatedIn a owl:ObjectProperty ; phys:edgeCollectionName "is_located_in" ;
    rdfs:domain :Person ; rdfs:range :City .
:follows a owl:ObjectProperty ; phys:edgeCollectionName "follows" .
"""
)


def _label(cls: str) -> str:
    return (
        f':{cls} a owl:Class ; phys:collectionName "nodes" ; phys:mappingStyle "LABEL" ;\n'
        f'    phys:typeField "labels" ; phys:typeFieldIsArray "true" ; phys:typeValue "{cls}" .\n'
    )


def _typed_edge(prop: str, value: str, tail: str = "") -> str:
    return (
        f':{prop} a owl:ObjectProperty ; phys:edgeCollectionName "edges" ; phys:mappingStyle "GENERIC_WITH_TYPE" ;\n'
        f'    phys:typeField "type" ; phys:typeValue "{value}"{tail} .\n'
    )


LPG_ONTOLOGY = (
    _COMMON
    + "".join(_label(c) for c in ("Person", "City", "Post", "Comment"))
    + ':Message a owl:Class ; phys:collectionName "nodes" ; phys:mappingStyle "MULTI_LABEL" ;\n'
    + '    phys:labelsField "labels" ; phys:labelValue "Message" .\n'
    + _typed_edge("hasCreator", "HAS_CREATOR", " ; rdfs:domain :Message ; rdfs:range :Person")
    + _typed_edge("knows", "KNOWS", " ; rdfs:domain :Person ; rdfs:range :Person")
    + _typed_edge("isLocatedIn", "IS_LOCATED_IN", " ; rdfs:domain :Person ; rdfs:range :City")
    + _typed_edge("follows", "FOLLOWS")
)
MODELS = [pytest.param(PG_ONTOLOGY, _pg_docs, id="pg"), pytest.param(LPG_ONTOLOGY, _lpg_docs, id="lpg")]


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    store = oxi.Store()
    store.load(_data_ttl().encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


def _run(ttl: str, docs: Any, sparql: str) -> list[dict[str, Any]]:
    result = translate(sparql, resolver=SchemaResolver.from_turtle(ttl))
    return [drop_null_bindings(r) for r in run_aql_subset(result.aql, result.bind_vars, docs())]


CASES = [
    # Walks backwards before the type scan.
    pytest.param("SELECT ?p WHERE { ?p a :Person . ?p :isLocatedIn :paris }", id="walk_from_a_constant"),
    pytest.param(
        "SELECT ?m ?f WHERE { VALUES ?f { :bob :alice } ?m a :Message ; :hasCreator ?f }",
        id="walk_from_a_value_then_supertype_test",
    ),
    pytest.param(
        'SELECT ?m WHERE { ?f a :Person ; :firstName "Bob" . ?m a :Post ; :hasCreator ?f }',
        id="walk_from_a_document_then_class_test",
    ),
    pytest.param(
        "SELECT ?p ?f WHERE { ?f a :Person . ?p :knows ?f . ?p a :Person }", id="walk_then_same_class_test"
    ),
    pytest.param("SELECT ?m WHERE { ?m :hasCreator :bob . ?m a :Comment }", id="walk_then_one_member_test"),
    # No range to find :bob in, so ?a's type pattern is read first.
    pytest.param("SELECT ?a WHERE { ?a a :Person . ?a :follows :bob }", id="no_range_type_first"),
    # Probes read the outer document.
    pytest.param("SELECT ?p WHERE { ?p a :Person FILTER NOT EXISTS { ?p :knows ?q } }", id="not_exists_edge"),
    pytest.param(
        "SELECT ?p WHERE { ?p a :Person FILTER EXISTS { ?p :isLocatedIn :rome } }", id="exists_edge_constant"
    ),
    pytest.param(
        "SELECT ?p WHERE { ?p a :Person MINUS { ?p :isLocatedIn :paris } }", id="minus_edge_constant"
    ),
    pytest.param(
        "SELECT ?p WHERE { ?p a :Person FILTER EXISTS { ?p a :Person ; :firstName ?n } }",
        id="exists_class_test_and_attribute",
    ),
    pytest.param(
        "SELECT ?m WHERE { ?m a :Message FILTER NOT EXISTS { ?m a :Comment } }", id="not_exists_member_test"
    ),
]


@pytest.mark.cross
@pytest.mark.parametrize("ttl,docs", MODELS)
@pytest.mark.parametrize("body", CASES)
def test_join_order_matches_oxigraph(oxi_store: Any, ttl: str, docs: Any, body: str) -> None:
    sparql = "PREFIX : <http://ex.org/>\n" + body
    expected = [normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert expected, "the case must have a non-empty answer to prove anything"
    assert_bindings_equal(expected, _run(ttl, docs, sparql))


@pytest.mark.cross
@pytest.mark.parametrize("ttl,docs", MODELS)
@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "SELECT ?m WHERE { ?m :hasCreator :bob . ?m a :City }", id="walked_vertex_of_another_class"
        ),
        pytest.param(
            "SELECT ?p WHERE { ?p a :Person FILTER EXISTS { ?p a :City } }", id="probe_of_another_class"
        ),
    ],
)
def test_a_class_test_that_cannot_match_answers_nothing(
    oxi_store: Any, ttl: str, docs: Any, body: str
) -> None:
    sparql = "PREFIX : <http://ex.org/>\n" + body
    assert list(oxi_bindings(oxi_store, sparql)) == []
    assert _run(ttl, docs, sparql) == []
