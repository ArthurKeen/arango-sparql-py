"""TDD behavior tests for `convert_ck25.py` (07.1-05 Task 2, NL-BENCH-02).

Written test-first (RED-then-GREEN): these fail with an `ImportError` until
`convert_ck25.py` lands. Once it does, they pin the four behaviors the plan
requires:

1. Every emitted (kept) case constructs as a valid `CorpusCase`.
2. `ontology.ttl` resolves via `SchemaResolver.from_turtle`; every class
   referenced by any KEPT question's `classes:` manifest carries
   `phys:collectionName`, and every object property referenced by any KEPT
   question's `properties:` manifest carries `phys:edgeCollectionName`
   (the A3 phys:-completeness audit, driven by the manifest fields
   directly rather than re-deriving usage from the SPARQL text).
3. `filter_log` kept + dropped == total input questions -- no silent
   truncation (D-06).
4. Determinism: two independent `build_corpus()` runs produce the identical
   surviving case set.

Also pins the Tampering mitigation (T-07.1-02): the converter module source
never calls `yaml.load(` directly (only `yaml.safe_load`/`safe_dump`).

Default-path (no `RUN_EVAL`/network gate) -- this only exercises the
deterministic parser/resolver/translate path against the vendored, checked-in
raw CK25 files, never a live LLM provider.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import RDF, Graph, URIRef
from rdflib.namespace import OWL

from arango_sparql.translate.resolver import SchemaResolver
from tests.nl2sparql.eval.runner import CorpusCase
from tests.nl2sparql.eval.vendored.ck25.convert_ck25 import (
    CONVERTER_PATH,
    PV_NS,
    build_corpus,
    load_questions,
)


def test_every_kept_case_constructs_as_a_valid_corpus_case() -> None:
    """Every case `build_corpus()` emits is a well-formed `CorpusCase` -- the
    load-time gate (gold-must-parse) that `runner._load_corpus` enforces on
    every corpus file must never trip on a CK25 survivor."""
    _ontology_ttl, kept, _dropped = build_corpus()
    assert kept, "expected at least one surviving CK25 case"
    for case in kept:
        CorpusCase(**case)


def test_ontology_resolves_and_every_kept_manifest_term_is_phys_annotated() -> None:
    """`ontology.ttl` resolves via `SchemaResolver.from_turtle`, and every
    class / object property referenced by any KEPT question's
    `classes:`/`properties:` manifest carries the `phys:` annotation the
    resolver needs (A3 audit checklist -- driven by the manifest fields,
    not re-derived from the SPARQL text)."""
    ontology_ttl, kept, _dropped = build_corpus()
    SchemaResolver.from_turtle(ontology_ttl)  # must not raise

    graph = Graph()
    graph.parse(data=ontology_ttl, format="turtle")

    questions_by_id = {q["id"]: q for q in load_questions()}
    kept_ids = {int(case["name"].rsplit("-", 1)[-1]) for case in kept}

    missing: list[str] = []
    for qid in kept_ids:
        question = questions_by_id[qid]
        for class_ref in question.get("classes", []):
            if not class_ref.startswith(":"):
                continue
            term = URIRef(PV_NS + class_ref[1:])
            has_collection_name = any(
                str(pred).endswith("collectionName") for pred, _obj in graph.predicate_objects(term)
            )
            if not has_collection_name:
                missing.append(f"Q{qid}: class {class_ref!r} missing phys:collectionName")
        for prop_ref in question.get("properties", []):
            if not prop_ref.startswith(":"):
                continue
            term = URIRef(PV_NS + prop_ref[1:])
            if (term, RDF.type, OWL.ObjectProperty) not in graph:
                continue  # a datatype property -- no edge annotation expected
            has_edge_collection_name = any(
                str(pred).endswith("edgeCollectionName") for pred, _obj in graph.predicate_objects(term)
            )
            if not has_edge_collection_name:
                missing.append(f"Q{qid}: object property {prop_ref!r} missing phys:edgeCollectionName")

    assert not missing, "phys:-completeness audit failed:\n" + "\n".join(missing)


def test_filter_log_kept_plus_dropped_equals_total_no_silent_truncation() -> None:
    """D-06: every input question ends up kept or dropped -- never silently
    dropped without a logged reason."""
    questions = load_questions()
    _ontology_ttl, kept, dropped = build_corpus()
    assert len(kept) + len(dropped) == len(questions)


def test_conversion_is_deterministic_across_two_runs() -> None:
    """Two independent `build_corpus()` runs produce the identical surviving
    case set -- no run-to-run flakiness in the D-06 filter."""
    _ontology_a, kept_a, dropped_a = build_corpus()
    _ontology_b, kept_b, dropped_b = build_corpus()
    assert {case["name"] for case in kept_a} == {case["name"] for case in kept_b}
    assert len(dropped_a) == len(dropped_b)


def test_converter_never_calls_yaml_load_directly() -> None:
    """Tampering mitigation (T-07.1-02): the converter must only ever use
    `yaml.safe_load`/`yaml.safe_dump`, never the unsafe `yaml.load(`."""
    source = Path(CONVERTER_PATH).read_text()
    assert "yaml.load(" not in source
