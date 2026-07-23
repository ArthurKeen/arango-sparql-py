"""Golden tests for SPARQL 1.1 variable-predicate triples (``?s ?p ?o``).

The bulk of the corpus lives in :mod:`variable_predicate.yml` next to
this file — every case there runs against an empty resolver
(default-collection fallback), which is the configuration the 46
unbound-subject W3C tests sit on.

A separate block of resolver-driven Python tests below covers the
two emission shapes that need a populated resolver to exercise:

* **RPT-bound subject** — the W3C-spec-correct branch. Triples-table
  scan with the predicate column projected as ``?p`` directly and
  the standard ``NOT_NULL(object_uri, object_value)`` shape for
  ``?o``. Different enough from the YAML cases that pinning the
  exact AQL inline keeps the diff easy to read.
* **PG-class-bound subject** — same ATTRIBUTES fan-out as the
  default-collection case but reads from the typed class's
  collection. The CARVE-OUT still applies: ``?p`` binds to the
  attribute name, not the predicate IRI. Lifting that is the
  attribute-name-to-IRI follow-up slice (PRD §6.6 Variable
  predicates row).

These tests are the regression net for the slice that bumped W3C
query-evaluation coverage from 17.0 % to 27.3 %.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arango_sparql.api import translate
from arango_sparql.translate.mapping import MappingBundle, MappingSource
from arango_sparql.translate.resolver import SchemaResolver

GOLDEN_PATH = Path(__file__).parent / "variable_predicate.yml"


def _load_supported() -> list[tuple[str, str, str, str, dict]]:
    """Return ``(name, ontology_ttl, sparql, expected_aql,
    expected_bind_vars)`` per variable-predicate golden."""
    data = yaml.safe_load(GOLDEN_PATH.read_text())
    ttl = data["ontology"]
    out: list[tuple[str, str, str, str, dict]] = []
    for case in data["cases"]:
        out.append(
            (
                case["name"],
                # Cases that exercise the attribute→IRI reverse map
                # need declared datatype properties; a per-case
                # ``ontology`` overrides the file-level default.
                case.get("ontology", ttl),
                case["sparql"],
                case["expected_aql"].rstrip("\n"),
                case["expected_bind_vars"],
            )
        )
    return out


@pytest.mark.parametrize(
    "name, ontology_ttl, sparql, expected_aql, expected_bind_vars",
    _load_supported(),
    ids=[c[0] for c in _load_supported()],
)
def test_variable_predicate_golden(
    name: str,
    ontology_ttl: str,
    sparql: str,
    expected_aql: str,
    expected_bind_vars: dict,
) -> None:
    """Variable-predicate triples produce the exact AQL the golden
    declares.

    Pinning the AQL byte-for-byte protects against three classes of
    regression:

    1. A change to ``ATTRIBUTES(doc, true)`` (e.g. dropping the
       second argument) would surface here because the FILTER on
       ``_uri`` would either be redundant or insufficient.
    2. A change to the alias numbering scheme would shift every
       ``doc<N>`` / ``k<N>`` / ``agg<N>`` token; the explicit
       expected AQL catches this without us needing to write a
       semantic comparator.
    3. The bind-name suffix (``_sys_attrs``) is part of the public
       AQL surface — if a future refactor renames it the
       downstream pyArango client's bind dict would change shape
       silently. Pinning it here makes that an explicit decision.
    """
    resolver = SchemaResolver.from_turtle(ontology_ttl, default_collection="Document")
    result = translate(sparql, resolver=resolver)
    assert result.aql == expected_aql, (
        f"AQL mismatch for {name!r}:\n--- expected ---\n{expected_aql}\n--- actual ---\n{result.aql}"
    )
    assert result.bind_vars == expected_bind_vars, (
        f"bind_vars mismatch for {name!r}:\n"
        f"--- expected ---\n{expected_bind_vars}\n"
        f"--- actual ---\n{result.bind_vars}"
    )


# ---------------------------------------------------------------------------
# Resolver-driven cases.
#
# These live in Python (not YAML) because the per-case ontology /
# physical-mapping inputs are richer than the YAML harness's single
# ``ontology`` field. The two interactions they cover are the only
# variable-predicate emission shapes that aren't exercised by the
# default-collection corpus.
# ---------------------------------------------------------------------------


_PG_PERSON_OWL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ;
    phys:collectionName "Person" .
"""


_RPT_TRIPLES_OWL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Triples a owl:Class ;
    phys:mappingStyle "RPT" ;
    phys:triplesCollection "_triples" .
"""


def test_variable_predicate_pg_class_bound_subject() -> None:
    """``?s a :Person . ?s ?p ?o`` reads from the Person collection
    (not the default collection) but still uses the ATTRIBUTES
    fan-out shape — the carve-out that ``?p`` binds to the
    attribute name applies here too, identically to the empty-
    resolver case.

    Confirms the dispatcher correctly routes a PG-class-bound
    subject through the ATTRIBUTES branch (not the RPT branch,
    which would mis-emit against a non-triples collection)."""
    resolver = SchemaResolver.from_turtle(_PG_PERSON_OWL)
    result = translate(
        "PREFIX : <http://ex.org/> SELECT ?s ?p ?o WHERE { ?s a :Person . ?s ?p ?o }",
        resolver=resolver,
    )
    assert result.aql == (
        "FOR doc1 IN @@c1_Person\n"
        "FOR k2 IN ATTRIBUTES(doc1, true)\n"
        "FILTER k2 NOT IN @_p1_sys_attrs\n"
        "RETURN { s: doc1._uri, p: k2, o: doc1[k2] }"
    ), result.aql
    assert result.bind_vars == {
        "@c1_Person": "Person",
        "_p1_sys_attrs": ["_graph", "_uri"],
    }


def test_variable_predicate_rpt_bound_subject() -> None:
    """``?s a :Triples . ?s ?p ?o`` (RPT subject) emits the
    W3C-spec-correct shape — the triples table has a ``predicate``
    column so ``?p`` binds to it directly and ``?o`` binds to the
    standard ``NOT_NULL(object_uri, object_value)`` expression.

    Distinct from the ATTRIBUTES branch in three ways: there's no
    ATTRIBUTES iteration, no ``NOT IN [_uri]`` filter, and the
    join is on ``subject_uri`` instead of ``_uri``."""
    bundle = MappingBundle(
        physical_mapping={"entities": {}, "relationships": {}},
        owl_turtle=_RPT_TRIPLES_OWL,
        source=MappingSource(kind="manual"),
    )
    resolver = SchemaResolver.from_mapping_bundle(bundle)
    result = translate(
        "PREFIX : <http://ex.org/> SELECT ?s ?p ?o WHERE { ?s a :Triples . ?s ?p ?o }",
        resolver=resolver,
    )
    assert result.aql == (
        "FOR doc1 IN @@c1__triples\n"
        "FILTER doc1.predicate == @_p1_rdftype\n"
        "FILTER doc1.object_uri == @_p2_cls\n"
        "FOR doc2 IN @@c1__triples\n"
        "FILTER doc2.subject_uri == doc1.subject_uri\n"
        "RETURN { s: doc1.subject_uri, p: doc2.predicate, "
        "o: NOT_NULL(doc2.object_uri, doc2.object_value) }"
    ), result.aql
    assert result.bind_vars == {
        "@c1__triples": "_triples",
        "_p1_rdftype": ("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"),
        "_p2_cls": "http://ex.org/Triples",
    }


def test_variable_predicate_rpt_with_uri_object() -> None:
    """RPT variable-predicate + URI object → equality FILTER
    matching EITHER ``object_uri`` OR ``object_value``, mirroring
    the legacy ``rpt-translator.js`` permissive OR-filter that
    handled datasets where some loaders dropped URIs into the
    value column.

    Pinning this shape protects against a regression where the
    object-column dispatcher silently dropped the OR side and
    started missing rows where URIs lived in object_value."""
    bundle = MappingBundle(
        physical_mapping={"entities": {}, "relationships": {}},
        owl_turtle=_RPT_TRIPLES_OWL,
        source=MappingSource(kind="manual"),
    )
    resolver = SchemaResolver.from_mapping_bundle(bundle)
    result = translate(
        "PREFIX : <http://ex.org/> SELECT ?s ?p WHERE { ?s a :Triples . ?s ?p :alice }",
        resolver=resolver,
    )
    assert "(doc2.object_uri == @_p3_obj || doc2.object_value == @_p3_obj)" in result.aql, result.aql
    assert result.bind_vars["_p3_obj"] == "http://ex.org/alice"


def test_variable_predicate_rpt_with_literal_object() -> None:
    """RPT variable-predicate + literal object → equality FILTER
    on ``object_value`` only (literals never live in the URI
    column). Mirrors the legacy translator's split between URI
    and literal column dispatch."""
    bundle = MappingBundle(
        physical_mapping={"entities": {}, "relationships": {}},
        owl_turtle=_RPT_TRIPLES_OWL,
        source=MappingSource(kind="manual"),
    )
    resolver = SchemaResolver.from_mapping_bundle(bundle)
    result = translate(
        'PREFIX : <http://ex.org/> SELECT ?s ?p WHERE { ?s a :Triples . ?s ?p "Alice" }',
        resolver=resolver,
    )
    assert "doc2.object_value == @_p3_obj" in result.aql, result.aql
    # The URI-column equality must NOT appear for a literal
    # object — that would be a regression where the dispatcher
    # treated the literal as a URI.
    assert "object_uri == @_p3_obj" not in result.aql, result.aql
    assert result.bind_vars["_p3_obj"] == "Alice"
