"""Golden tests for RPT-native cross-subject ``OPTIONAL`` (ADR-0002
Problem 1, Option A).

A *cross-subject* OPTIONAL binds its subject only as a value (the
object of a prior triple), never as a document the translator opened a
``FOR`` over. On every storage model except RPT this is hard (PG/LPG
need ``_uri → collection`` resolution; the default ``Document`` model
loses the predicate-IRI shape — ADR-0002 Options B/C, deferred). On RPT
it is a plain left-join scan of the triples table, which is what
:mod:`arango_sparql.translate.optional_crosssubject` emits.

These live in Python (not the YAML harness) because each case needs a
populated RPT :class:`MappingBundle`, mirroring the resolver-driven
block in :mod:`test_translate_variable_predicate_goldens`.

The AQL is pinned byte-for-byte so three regression classes surface
here:

1. Dropping the ``LENGTH(...) > 0 ? ... : [null]`` pad — that would
   silently turn the LEFT join into an INNER join and drop outer rows
   with no optional match.
2. A change to the ``NOT_NULL(object_uri, object_value)`` object shape
   or the ``subject_uri`` join column.
3. Alias / bind-name renumbering, which the downstream pyArango client's
   bind dict depends on.

Binding parity against a W3C-conformant store is covered separately in
``tests/cross/test_optional_crosssubject_cross.py``.
"""

from __future__ import annotations

import pytest

from arango_sparql.api import translate
from arango_sparql.errors import UnsupportedSparqlError
from arango_sparql.translate.mapping import MappingBundle, MappingSource
from arango_sparql.translate.resolver import SchemaResolver

_RPT_TRIPLES_OWL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Triples a owl:Class ;
    phys:mappingStyle "RPT" ;
    phys:triplesCollection "_triples" .
"""


def _rpt_resolver() -> SchemaResolver:
    bundle = MappingBundle(
        physical_mapping={"entities": {}, "relationships": {}},
        owl_turtle=_RPT_TRIPLES_OWL,
        source=MappingSource(kind="manual"),
    )
    return SchemaResolver.from_mapping_bundle(bundle)


def test_rpt_cross_subject_optional_variable_predicate() -> None:
    """``?s :knows ?o . OPTIONAL { ?o ?p2 ?o2 }`` — the W3C
    ``tsv02`` / ``jsonres02`` shape, on RPT.

    ``?o`` is bound as the *object* of ``:knows`` (a value, never a
    document), so the OPTIONAL is a triples-table scan joined on
    ``subject_uri == <o>``. The variable predicate ``?p2`` projects the
    ``predicate`` column directly — the spec-correct IRI binding RPT
    makes trivial and that the ``Document`` model cannot express."""
    result = translate(
        "PREFIX : <http://ex.org/> "
        "SELECT ?s ?o ?p2 ?o2 WHERE { ?s a :Triples ; :knows ?o . "
        "OPTIONAL { ?o ?p2 ?o2 } }",
        resolver=_rpt_resolver(),
    )
    assert result.aql == (
        "FOR doc1 IN @@c1__triples\n"
        "FILTER doc1.predicate == @_p1_rdftype\n"
        "FILTER doc1.object_uri == @_p2_cls\n"
        "FOR doc2 IN @@c1__triples\n"
        "FILTER doc2.predicate == @_p3_pred\n"
        "FILTER doc2.subject_uri == doc1.subject_uri\n"
        "LET optsub4 = (\n"
        "  FOR doc3 IN @@c2__triples\n"
        "  FILTER doc3.subject_uri == NOT_NULL(doc2.object_uri, doc2.object_value)\n"
        "  RETURN {f0: doc3.predicate, f1: NOT_NULL(doc3.object_uri, doc3.object_value)}\n"
        ")\n"
        "FOR optrow5 IN (LENGTH(optsub4) > 0 ? optsub4 : [null])\n"
        "RETURN { s: doc1.subject_uri, "
        "o: NOT_NULL(doc2.object_uri, doc2.object_value), "
        "p2: optrow5.f0, o2: optrow5.f1 }"
    ), result.aql
    assert result.bind_vars == {
        "@c1__triples": "_triples",
        "@c2__triples": "_triples",
        "_p1_rdftype": "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
        "_p2_cls": "http://ex.org/Triples",
        "_p3_pred": "http://ex.org/knows",
    }


def test_rpt_cross_subject_optional_fixed_predicate() -> None:
    """``?s :knows ?o . OPTIONAL { ?o :email ?email }`` — fixed
    predicate adds a ``FILTER`` on the predicate column inside the
    scan and projects a single object field."""
    result = translate(
        "PREFIX : <http://ex.org/> "
        "SELECT ?s ?o ?email WHERE { ?s a :Triples ; :knows ?o . "
        "OPTIONAL { ?o :email ?email } }",
        resolver=_rpt_resolver(),
    )
    assert result.aql == (
        "FOR doc1 IN @@c1__triples\n"
        "FILTER doc1.predicate == @_p1_rdftype\n"
        "FILTER doc1.object_uri == @_p2_cls\n"
        "FOR doc2 IN @@c1__triples\n"
        "FILTER doc2.predicate == @_p3_pred\n"
        "FILTER doc2.subject_uri == doc1.subject_uri\n"
        "LET optsub4 = (\n"
        "  FOR doc3 IN @@c2__triples\n"
        "  FILTER doc3.subject_uri == NOT_NULL(doc2.object_uri, doc2.object_value)\n"
        "  FILTER doc3.predicate == @_p4_pred\n"
        "  RETURN {f0: NOT_NULL(doc3.object_uri, doc3.object_value)}\n"
        ")\n"
        "FOR optrow5 IN (LENGTH(optsub4) > 0 ? optsub4 : [null])\n"
        "RETURN { s: doc1.subject_uri, "
        "o: NOT_NULL(doc2.object_uri, doc2.object_value), "
        "email: optrow5.f0 }"
    ), result.aql
    assert result.bind_vars == {
        "@c1__triples": "_triples",
        "@c2__triples": "_triples",
        "_p1_rdftype": "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
        "_p2_cls": "http://ex.org/Triples",
        "_p3_pred": "http://ex.org/knows",
        "_p4_pred": "http://ex.org/email",
    }


def test_rpt_cross_subject_optional_non_variable_object_rejected() -> None:
    """``OPTIONAL { ?o :p <const> }`` is an existence test, not a
    binding; the emitter refuses it with a structured error rather
    than guessing the semantics (ADR-0002 — surface, don't guess)."""
    with pytest.raises(UnsupportedSparqlError, match="non-variable object"):
        translate(
            "PREFIX : <http://ex.org/> "
            "SELECT ?s ?o WHERE { ?s a :Triples ; :knows ?o . "
            "OPTIONAL { ?o :email :bob } }",
            resolver=_rpt_resolver(),
        )


def test_non_rpt_cross_subject_optional_still_rejected() -> None:
    """Cross-subject OPTIONAL on the default ``Document`` model is
    untouched by Option A — it still raises the structured rejection
    (ADR-0002 Options B/C remain deferred)."""
    with pytest.raises(UnsupportedSparqlError, match="cross-subject"):
        translate(
            "PREFIX : <http://ex.org/> "
            "SELECT ?s ?o ?p2 ?o2 WHERE { ?s :knows ?o . "
            "OPTIONAL { ?o ?p2 ?o2 } }",
            resolver=SchemaResolver.from_turtle("", default_collection="Document"),
        )
