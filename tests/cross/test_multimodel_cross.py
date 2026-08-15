"""Cross-validation across storage models: PG, LPG, RPT, and DOCUMENT.

The PG-only :mod:`tests.cross.test_bgp_select_cross` proves the
translator's bindings match pyoxigraph for the collection-per-class
model. But the translator emits *structurally different* AQL for each
ArangoDB storage model (PRD §6.1–6.2):

- **PG** (``COLLECTION``): one collection per class, properties inline
  on the document — ``FOR d IN @@Person``.
- **LPG** (``LABEL`` / ``GENERIC_WITH_TYPE``): a shared collection with
  a ``typeField`` discriminator — ``FOR d IN @@vertices FILTER
  d.type == "Person"``.
- **RPT** (``RPT`` / ``RPT_EDGE``, the legacy Foxx ``_triples`` table):
  every triple is a row, so a multi-triple BGP becomes a self-join over
  the triples collection keyed on ``subject_uri``, reading objects via
  ``COALESCE(object_uri, object_value)``.
- **DOCUMENT**: a plain document collection without a class discriminator;
  its read pattern is intentionally equivalent to ``COLLECTION``.

This is exactly where the real correctness risk lives: the same SPARQL
must produce the same bindings regardless of how the data is physically
stored. We assert that here by running each model's translated AQL
through the shared interpreter against a model-shaped mock store, and
comparing every model's output to the *same* pyoxigraph ground truth.

The model-shaped mock stores and the pyoxigraph dataset are all derived from
one :data:`PEOPLE` source of truth, so they cannot drift apart and
silently weaken the cross-check.
"""

from __future__ import annotations

from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.aql_interp import run_aql_subset
from tests.helpers.oxi import (
    assert_bindings_equal,
    assert_bindings_equal_ordered,
    drop_null_bindings,
    normalize_oxi_row,
    oxi_bindings,
)

oxi = pytest.importorskip("pyoxigraph", reason="pyoxigraph required for cross tests")

EX = "http://ex.org/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
PERSON_CLASS = EX + "Person"
PROJECT_CLASS = EX + "Project"

# ----------------------------------------------------------------------
# Single source of truth — every store and the RDF dataset derive from
# this so PG / LPG / hybrid / RPT and pyoxigraph describe identical
# facts. The cases here cover the BGP / FILTER / DISTINCT / ORDER-BY
# core plus same-subject-chain cross-class joins (Project ⋈ Person via
# the ``owner`` object property). Cross-*subject* OPTIONAL (left-join)
# under RPT remains tracked under the deferred ADR-0002.
# ----------------------------------------------------------------------
PEOPLE: list[dict[str, Any]] = [
    {"local": "alice", "name": "Alice", "age": 30, "dept": "eng"},
    {"local": "bob", "name": "Bob", "age": 42, "dept": "eng"},
    {"local": "carol", "name": "Carol", "age": 30, "dept": "ops"},
]

# A second class with a datatype property (``title``) and an
# *object* property (``owner`` → a Person IRI). This is what makes the
# hybrid (PG + LPG) cross-check meaningful: a query that joins a
# PG-mapped class to an LPG-mapped class must still produce the same
# bindings as the pure models. ``p4`` has no owner so the join drops it
# (matching pyoxigraph dropping the unbound-object solution).
PROJECTS: list[dict[str, Any]] = [
    {"local": "p1", "title": "Apollo", "owner": "alice"},
    {"local": "p2", "title": "Beacon", "owner": "bob"},
    {"local": "p3", "title": "Catalyst", "owner": "alice"},
    {"local": "p4", "title": "Orphan", "owner": None},
]

# Datatype properties carried by every person, with their EX predicate
# IRI and the Python type the value takes in a document / triples row.
_DATA_PROPS = ("name", "age", "dept")


def _data_ttl() -> str:
    lines = ["@prefix : <http://ex.org/> ."]
    for p in PEOPLE:
        lines.append(
            f':{p["local"]} a :Person ; :name "{p["name"]}" ; :age {p["age"]} ; :dept "{p["dept"]}" .'
        )
    for pr in PROJECTS:
        triple = f':{pr["local"]} a :Project ; :title "{pr["title"]}"'
        if pr["owner"]:
            triple += f" ; :owner :{pr['owner']}"
        lines.append(triple + " .")
    return "\n".join(lines)


def _pg_person_doc(p: dict[str, Any]) -> dict[str, Any]:
    return {"_uri": EX + p["local"], **{k: p[k] for k in _DATA_PROPS}}


def _lpg_person_doc(p: dict[str, Any]) -> dict[str, Any]:
    return {"_uri": EX + p["local"], "type": "Person", **{k: p[k] for k in _DATA_PROPS}}


def _project_doc(pr: dict[str, Any], *, type_field: bool) -> dict[str, Any]:
    doc: dict[str, Any] = {"_uri": EX + pr["local"], "title": pr["title"]}
    if type_field:
        doc["type"] = "Project"
    if pr["owner"]:
        doc["owner"] = EX + pr["owner"]
    return doc


def _pg_docs() -> dict[str, list[dict[str, Any]]]:
    # Pure PG: one collection per class, properties inline.
    return {
        "Person": [_pg_person_doc(p) for p in PEOPLE],
        "Project": [_project_doc(pr, type_field=False) for pr in PROJECTS],
    }


def _document_docs() -> dict[str, list[dict[str, Any]]]:
    # DOCUMENT uses plain class collections with no discriminator, matching
    # COLLECTION's physical rows while exercising the explicit style dispatch.
    return _pg_docs()


def _lpg_docs() -> dict[str, list[dict[str, Any]]]:
    # Pure LPG: every class shares the ``vertices`` collection and is
    # distinguished only by a ``type`` discriminator field.
    return {
        "vertices": [_lpg_person_doc(p) for p in PEOPLE]
        + [_project_doc(pr, type_field=True) for pr in PROJECTS]
    }


def _hybrid_pg_lpg_docs() -> dict[str, list[dict[str, Any]]]:
    # Hybrid PG + LPG: ``Person`` is mapped LPG (shared ``vertices``
    # collection + ``type`` discriminator) while ``Project`` is mapped
    # PG (its own ``Project`` collection). A cross-class join therefore
    # straddles the two physical styles in a single query — the case
    # this fixture exists to validate.
    return {
        "vertices": [_lpg_person_doc(p) for p in PEOPLE],
        "Project": [_project_doc(pr, type_field=False) for pr in PROJECTS],
    }


def _rpt_docs() -> dict[str, list[dict[str, Any]]]:
    # One row per triple in a single ``_triples`` collection, mirroring
    # the legacy Foxx layout: IRI objects land in ``object_uri``,
    # literal objects in ``object_value``. The absent column is simply
    # omitted (the interpreter reads a missing doc attribute as null,
    # matching AQL), so ``COALESCE(object_uri, object_value)`` recovers
    # the object regardless of which column holds it.
    rows: list[dict[str, Any]] = []
    for p in PEOPLE:
        subject = EX + p["local"]
        rows.append({"subject_uri": subject, "predicate": RDF_TYPE, "object_uri": PERSON_CLASS})
        for prop in _DATA_PROPS:
            rows.append(
                {
                    "subject_uri": subject,
                    "predicate": EX + prop,
                    "object_value": p[prop],
                }
            )
    for pr in PROJECTS:
        subject = EX + pr["local"]
        rows.append({"subject_uri": subject, "predicate": RDF_TYPE, "object_uri": PROJECT_CLASS})
        rows.append({"subject_uri": subject, "predicate": EX + "title", "object_value": pr["title"]})
        if pr["owner"]:
            # Object property → IRI object lands in ``object_uri``.
            rows.append(
                {
                    "subject_uri": subject,
                    "predicate": EX + "owner",
                    "object_uri": EX + pr["owner"],
                }
            )
    return {"_triples": rows}


def _hybrid_pg_rpt_docs() -> dict[str, list[dict[str, Any]]]:
    """Project as PG documents joined to Person rows in the RPT table."""

    return {
        "Project": [_project_doc(pr, type_field=False) for pr in PROJECTS],
        "_triples": [
            row
            for row in _rpt_docs()["_triples"]
            if row["subject_uri"] in {EX + person["local"] for person in PEOPLE}
        ],
    }


PG_ONTOLOGY = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Person a owl:Class ; phys:collectionName "Person" .
:Project a owl:Class ; phys:collectionName "Project" .
"""

DOCUMENT_ONTOLOGY = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Person a owl:Class ;
    phys:collectionName "Person" ;
    phys:mappingStyle "DOCUMENT" .
:Project a owl:Class ;
    phys:collectionName "Project" ;
    phys:mappingStyle "DOCUMENT" .
"""

LPG_ONTOLOGY = """
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
"""

# Hybrid: ``Person`` mapped LPG (shared ``vertices`` + discriminator),
# ``Project`` mapped PG (its own collection). This is the PG+LPG blend
# the user asked us to cover — the schema analyzer detects pure-PG /
# pure-LPG algorithmically and falls back to an LLM for exactly this
# kind of blended mapping (see arango-cypher-py's analyzer); here we
# consume the already-classified blend and prove the *translation* is
# binding-equivalent to the pure models.
HYBRID_PG_LPG_ONTOLOGY = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Person a owl:Class ;
    phys:collectionName "vertices" ;
    phys:mappingStyle "LABEL" ;
    phys:typeField "type" ;
    phys:typeValue "Person" .
:Project a owl:Class ; phys:collectionName "Project" .
"""

RPT_ONTOLOGY = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Person a owl:Class ;
    phys:mappingStyle "RPT" ;
    phys:triplesCollection "_triples" ;
    phys:subjectColumn "subject_uri" ;
    phys:predicateColumn "predicate" ;
    phys:objectUriColumn "object_uri" ;
    phys:objectValueColumn "object_value" .
:Project a owl:Class ;
    phys:mappingStyle "RPT" ;
    phys:triplesCollection "_triples" ;
    phys:subjectColumn "subject_uri" ;
    phys:predicateColumn "predicate" ;
    phys:objectUriColumn "object_uri" ;
    phys:objectValueColumn "object_value" .
"""

HYBRID_PG_RPT_ONTOLOGY = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Person a owl:Class ;
    phys:mappingStyle "RPT" ;
    phys:triplesCollection "_triples" .
:Project a owl:Class ; phys:collectionName "Project" .
"""

# (ontology TTL, mock-store factory). The factory shape keeps each test
# run isolated — no shared mutable store between cases.
MODELS = [
    pytest.param(PG_ONTOLOGY, _pg_docs, id="pg"),
    pytest.param(DOCUMENT_ONTOLOGY, _document_docs, id="document"),
    pytest.param(LPG_ONTOLOGY, _lpg_docs, id="lpg"),
    pytest.param(HYBRID_PG_LPG_ONTOLOGY, _hybrid_pg_lpg_docs, id="hybrid_pg_lpg"),
    pytest.param(RPT_ONTOLOGY, _rpt_docs, id="rpt"),
    pytest.param(HYBRID_PG_RPT_ONTOLOGY, _hybrid_pg_rpt_docs, id="hybrid_pg_rpt"),
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


# ----------------------------------------------------------------------
# BGP / FILTER / DISTINCT cases — order-insensitive bag equality.
# ----------------------------------------------------------------------
BAG_CASES = [
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person }",
        id="type_pattern",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s ?n WHERE { ?s a :Person ; :name ?n }",
        id="type_plus_property",
    ),
    pytest.param(
        'PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person ; :name "Alice" }',
        id="string_literal_filter",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person ; :age 30 }",
        id="integer_literal_filter",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT DISTINCT ?age WHERE { ?s a :Person ; :age ?age }",
        id="distinct_age",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s ?a WHERE { ?s a :Person ; :age ?a . FILTER(?a > 30) }",
        id="filter_gt",
    ),
    pytest.param(
        'PREFIX : <http://ex.org/> SELECT ?s ?n WHERE { ?s a :Person ; :name ?n . FILTER(?n != "Bob") }',
        id="filter_not_equals",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s ?a WHERE { ?s a :Person ; :age ?a . "
        "FILTER(?a >= 30 && ?a <= 40) }",
        id="filter_range_and",
    ),
    pytest.param(
        'PREFIX : <http://ex.org/> SELECT ?s ?n WHERE { ?s a :Person ; :name ?n . FILTER(REGEX(?n, "^A")) }',
        id="filter_regex",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s ?n ?d WHERE { "
        '?s a :Person ; :name ?n ; :dept ?d . FILTER(?d = "eng") }',
        id="three_property_bgp_with_filter",
    ),
]


@pytest.mark.cross
@pytest.mark.parametrize("ontology_ttl,docs_factory", MODELS)
@pytest.mark.parametrize("sparql", BAG_CASES)
def test_bgp_filter_matches_oxigraph_across_models(
    oxi_store: Any,
    ontology_ttl: str,
    docs_factory: Any,
    sparql: str,
) -> None:
    actual = _run_model(ontology_ttl, docs_factory, sparql)
    expected = [normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert_bindings_equal(expected, actual)


# ----------------------------------------------------------------------
# ORDER BY cases — ordering itself is under test, so use list equality.
# ----------------------------------------------------------------------
ORDER_BY_CASES = [
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n WHERE { ?s a :Person ; :name ?n } ORDER BY ?n",
        id="order_by_name_asc",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?a WHERE { ?s a :Person ; :age ?a } ORDER BY DESC(?a)",
        id="order_by_age_desc",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?a WHERE { ?s a :Person ; :name ?n ; :age ?a } "
        "ORDER BY DESC(?a) ?n",
        id="order_by_age_desc_name_asc_tie_break",
    ),
]


@pytest.mark.cross
@pytest.mark.parametrize("ontology_ttl,docs_factory", MODELS)
@pytest.mark.parametrize("sparql", ORDER_BY_CASES)
def test_order_by_matches_oxigraph_across_models(
    oxi_store: Any,
    ontology_ttl: str,
    docs_factory: Any,
    sparql: str,
) -> None:
    actual = _run_model(ontology_ttl, docs_factory, sparql)
    expected = [normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert_bindings_equal_ordered(expected, actual)


# ----------------------------------------------------------------------
# Cross-class join cases — the heart of the hybrid (PG + LPG) check.
# Every case joins ``Project`` to ``Person`` via the ``owner`` object
# property, so under the ``hybrid_pg_lpg`` model the join straddles a
# PG collection (``Project``) and an LPG shared collection (``Person``
# in ``vertices``). Running the *same* cases over pure PG / LPG / RPT
# proves the hybrid emission is binding-equivalent — the join boundary
# is invisible to the result.
# ----------------------------------------------------------------------
JOIN_CASES = [
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?t WHERE { "
        "?prj a :Project ; :title ?t ; :owner ?p . "
        "?p a :Person ; :name ?n }",
        id="project_owner_join_person",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?t WHERE { "
        "?prj a :Project ; :title ?t ; :owner ?p . "
        '?p a :Person ; :name ?n . FILTER(?n = "Alice") }',
        id="join_filtered_on_lpg_side",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?t WHERE { "
        "?prj a :Project ; :title ?t ; :owner ?p . "
        "?p a :Person ; :name ?n ; :age ?a . FILTER(?a > 30) }",
        id="join_filtered_on_lpg_age",
    ),
]


@pytest.mark.cross
@pytest.mark.parametrize("ontology_ttl,docs_factory", MODELS)
@pytest.mark.parametrize("sparql", JOIN_CASES)
def test_cross_class_join_matches_oxigraph_across_models(
    oxi_store: Any,
    ontology_ttl: str,
    docs_factory: Any,
    sparql: str,
) -> None:
    actual = _run_model(ontology_ttl, docs_factory, sparql)
    expected = [normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert_bindings_equal(expected, actual)
