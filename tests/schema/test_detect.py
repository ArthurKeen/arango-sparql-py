"""Unit tests for :mod:`arango_sparql.schema.detect` (PRD §6.3.1).

Covers all five aggregation rules and every threshold called out in
the spec:

* RPT pattern detection — the 80 % coverage rule on the legacy Foxx
  column shape, the alternative bare-noun shape, and the
  ``_triples`` legacy-name shortcut.
* Tier-1 entity discriminator (``type`` / ``_type`` / ``entityType``)
  — qualifies on coverage alone.
* Tier-2 entity discriminator (``label`` / ``labels`` / ``kind``) —
  qualifies only when the cardinality, ratio, and class-like-value
  guards all hold.
* Edge classification — ``GENERIC_WITH_TYPE`` vs.
  ``DEDICATED_COLLECTION``.
* Schema-shape aggregation — pure PG, pure LPG, pure RPT, hybrid,
  unknown.
* Bundle assembly — confidence, reviewRequired, detectedPatterns
  tag-set discipline, source provenance, metadata.warnings entry.

Tests use a duck-typed ``MockDb`` rather than touching python-arango
or a live database — the live integration lives in
:mod:`arango_sparql.schema.acquire` and is the responsibility of the
next slice's tests. All of these tests run unmarked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from arango_sparql.schema.detect import (
    COVERAGE_THRESHOLD,
    DEFAULT_SAMPLE_SIZE,
    TIER_2_MAX_DISTINCT,
    CollectionClassification,
    RptDetectionResult,
    build_heuristic_mapping,
    classify_schema,
    detect_rpt_pattern,
)
from arango_sparql.schema.fingerprint import compute_bundle_fingerprint
from arango_sparql.translate.mapping import MappingBundle

# ---------------------------------------------------------------------------
# Mock database — duck-typed against python-arango's StandardDatabase
# ---------------------------------------------------------------------------


class _MockAql:
    """Minimal AQL stand-in. Returns whatever was preloaded for the
    requested collection, capped by the ``LIMIT @n`` bind var so we
    can exercise the sampler's cap behaviour faithfully.
    """

    def __init__(self, samples: dict[str, list[dict[str, Any]]]) -> None:
        self.samples = samples
        self.queries_seen: list[tuple[str, dict[str, Any]]] = []

    def execute(
        self, query: str, bind_vars: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self.queries_seen.append((query, dict(bind_vars or {})))
        if not bind_vars:
            return []
        name = bind_vars.get("@col")
        n = int(bind_vars.get("n", 0) or 0)
        docs = list(self.samples.get(name, []))
        return docs[:n]


class MockDb:
    """Bare-minimum substitute for ``arango.database.StandardDatabase``.

    Carries a list of ``{"name", "system", "type"}`` collection rows
    matching the python-arango response shape, plus a mapping from
    collection name to the docs the sampler should see for that
    collection.
    """

    def __init__(
        self,
        collections: list[dict[str, Any]],
        samples: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._collections = collections
        self.aql = _MockAql(samples or {})

    def collections(self) -> list[dict[str, Any]]:
        return list(self._collections)


def _doc_collection(name: str, *, system: bool = False) -> dict[str, Any]:
    return {"name": name, "system": system, "type": "document"}


def _edge_collection(name: str, *, system: bool = False) -> dict[str, Any]:
    return {"name": name, "system": system, "type": "edge"}


# ---------------------------------------------------------------------------
# Sample-builder helpers
# ---------------------------------------------------------------------------


def _make_pg_docs(n: int = 10) -> list[dict[str, Any]]:
    return [{"_key": str(i), "name": f"p{i}", "age": 20 + i} for i in range(n)]


def _make_lpg_tier1_docs(types: list[str], n: int = 10) -> list[dict[str, Any]]:
    """LPG-shaped docs using the tier-1 ``type`` field."""

    return [{"_key": str(i), "type": types[i % len(types)]} for i in range(n)]


def _make_lpg_tier2_docs(labels: list[str], n: int = 10) -> list[dict[str, Any]]:
    """LPG-shaped docs using the tier-2 ``labels`` (list) field."""

    return [{"_key": str(i), "labels": [labels[i % len(labels)]]} for i in range(n)]


def _make_rpt_docs(n: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "subject_uri": f"urn:s/{i}",
            "predicate": "urn:p/knows",
            "object_uri": f"urn:s/{(i + 1) % n}",
        }
        for i in range(n)
    ]


def _make_pg_edge_docs(n: int = 10) -> list[dict[str, Any]]:
    return [{"_from": f"persons/{i}", "_to": f"persons/{i+1}"} for i in range(n)]


def _make_lpg_edge_docs(types: list[str], n: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "_from": f"v/{i}",
            "_to": f"v/{i+1}",
            "type": types[i % len(types)],
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# RPT pattern detection
# ---------------------------------------------------------------------------


def test_rpt_legacy_triples_collection_name_promotes_unconditionally() -> None:
    """The legacy Foxx ``_triples`` collection is RPT by name when
    non-empty, regardless of which exact column conventions its
    docs use. The legacy fixture uses ``subject_uri`` /
    ``predicate`` / ``object_uri`` / ``object_value`` so we project
    those defaults.
    """

    db = MockDb(
        collections=[_doc_collection("_triples", system=True)],
        samples={"_triples": _make_rpt_docs()},
    )
    out = detect_rpt_pattern(db)
    assert "_triples" in out
    rpt = out["_triples"]
    assert rpt.is_rpt is True
    assert rpt.triples_collection == "_triples"
    assert rpt.subject_column == "subject_uri"
    assert rpt.predicate_column == "predicate"
    assert rpt.object_uri_column == "object_uri"
    assert rpt.object_value_column == "object_value"
    assert "name == _triples" in rpt.reasons[0]


def test_rpt_empty_triples_collection_does_not_promote() -> None:
    """An empty ``_triples`` collection should not anchor the
    detector to RPT — without sampled rows we cannot verify the
    column conventions match.
    """

    db = MockDb(
        collections=[_doc_collection("_triples", system=True)],
        samples={"_triples": []},
    )
    rpt = detect_rpt_pattern(db)["_triples"]
    assert rpt.is_rpt is False
    assert rpt.coverage_ratio == 0.0


def test_rpt_user_collection_qualifies_at_eighty_percent_coverage() -> None:
    """A user collection (not named ``_triples``) qualifies as RPT
    when ≥ 80 % of sampled docs carry the column shape. Test with
    9 RPT-shaped docs out of 10 (90 %).
    """

    docs = _make_rpt_docs(9) + [{"_key": "9", "irrelevant": "x"}]
    db = MockDb(
        collections=[_doc_collection("triples_alt")],
        samples={"triples_alt": docs},
    )
    rpt = detect_rpt_pattern(db)["triples_alt"]
    assert rpt.is_rpt is True
    assert rpt.coverage_ratio == pytest.approx(0.9)


def test_rpt_below_threshold_is_not_rpt() -> None:
    """7/10 RPT-shaped docs (70 %) is below the 80 % threshold."""

    docs = _make_rpt_docs(7) + [{"_key": str(i)} for i in range(7, 10)]
    db = MockDb(
        collections=[_doc_collection("mostly_triples")],
        samples={"mostly_triples": docs},
    )
    rpt = detect_rpt_pattern(db)["mostly_triples"]
    assert rpt.is_rpt is False
    assert rpt.coverage_ratio == pytest.approx(0.7)
    assert any("80%" in r or "0.7" in r or "70%" in r for r in rpt.reasons)


def test_rpt_accepts_object_value_only_documents() -> None:
    """A document with only ``object_value`` (literal triple) but no
    ``object_uri`` still qualifies — that's a typed literal in RDF
    and the column shape is satisfied.
    """

    docs = [
        {
            "subject_uri": f"urn:s/{i}",
            "predicate": "urn:p/age",
            "object_value": str(20 + i),
        }
        for i in range(10)
    ]
    db = MockDb(collections=[_doc_collection("triples")], samples={"triples": docs})
    rpt = detect_rpt_pattern(db)["triples"]
    assert rpt.is_rpt is True
    assert rpt.coverage_ratio == 1.0


def test_rpt_treats_none_subject_as_disqualifying() -> None:
    """A doc whose subject column is explicitly ``None`` does not
    count toward coverage — treating ``None`` as "present" would
    let a malformed collection masquerade as RPT.
    """

    docs = [
        {"subject_uri": None, "predicate": "p", "object_value": "v"} for _ in range(10)
    ]
    db = MockDb(collections=[_doc_collection("nulls")], samples={"nulls": docs})
    rpt = detect_rpt_pattern(db)["nulls"]
    assert rpt.is_rpt is False


# ---------------------------------------------------------------------------
# Tier-1 entity discriminator
# ---------------------------------------------------------------------------


def test_tier_1_type_field_promotes_to_lpg() -> None:
    """80 %+ coverage of the tier-1 ``type`` field, with class-like
    string values, classifies the collection as LPG (LABEL style).
    """

    db = MockDb(
        collections=[_doc_collection("vertices")],
        samples={"vertices": _make_lpg_tier1_docs(["Person", "Doc"])},
    )
    assert classify_schema(db) == "lpg"


@pytest.mark.parametrize("field_name", ["type", "_type", "entityType"])
def test_tier_1_all_three_field_names_qualify(field_name: str) -> None:
    docs = [{"_key": str(i), field_name: "Person" if i % 2 else "Doc"} for i in range(10)]
    db = MockDb(
        collections=[_doc_collection("vertices")],
        samples={"vertices": docs},
    )
    assert classify_schema(db) == "lpg"


def test_tier_1_below_threshold_falls_back_to_pg_collection() -> None:
    """50 % coverage of ``type`` is below the 80 % threshold — the
    collection should classify as plain ``COLLECTION`` (PG entity).
    """

    docs = (
        _make_lpg_tier1_docs(["Person"], n=5)
        + [{"_key": str(i + 5)} for i in range(5)]
    )
    db = MockDb(collections=[_doc_collection("nodes")], samples={"nodes": docs})
    assert classify_schema(db) == "pg"


# ---------------------------------------------------------------------------
# Tier-2 entity discriminator (the strict path)
# ---------------------------------------------------------------------------


def test_tier_2_labels_qualifies_with_low_cardinality() -> None:
    """``labels`` carrying 2 distinct class-like values across 10
    docs (cardinality ratio 0.2) qualifies."""

    db = MockDb(
        collections=[_doc_collection("vertices")],
        samples={"vertices": _make_lpg_tier2_docs(["Person", "Doc"])},
    )
    assert classify_schema(db) == "lpg"


def test_tier_2_labels_rejects_free_text_values() -> None:
    """Tier-2 explicitly requires class-like values (the
    ``[A-Za-z0-9_-]+`` regex). A ``labels`` field full of sentences
    must fall back to PG.
    """

    docs = [
        {"_key": str(i), "labels": [f"this is sentence {i}"]} for i in range(10)
    ]
    db = MockDb(collections=[_doc_collection("posts")], samples={"posts": docs})
    assert classify_schema(db) == "pg"


def test_tier_2_labels_rejects_high_cardinality() -> None:
    """Every doc has a unique label → cardinality ratio == 1.0 →
    rejected. Otherwise we'd misclassify e.g. a "title" field as
    a discriminator.
    """

    docs = [{"_key": str(i), "labels": [f"unique_{i}"]} for i in range(10)]
    db = MockDb(collections=[_doc_collection("titled")], samples={"titled": docs})
    assert classify_schema(db) == "pg"


def test_tier_2_labels_rejects_more_than_thirty_two_distinct_values() -> None:
    """The hard cap is 32 distinct values per PRD §6.3.1; build a
    sample with 33 distinct labels and verify rejection.
    """

    docs = [
        {"_key": str(i), "labels": [f"L{i}"]}
        for i in range(TIER_2_MAX_DISTINCT + 1)
    ]
    db = MockDb(collections=[_doc_collection("many")], samples={"many": docs})
    assert classify_schema(db) == "pg"


def test_tier_2_label_string_scalar_also_qualifies() -> None:
    """``labels`` may carry a plain string per doc, not always a
    list. The flatten helper must handle both cases.
    """

    docs = [{"_key": str(i), "labels": "Person" if i % 2 else "Doc"} for i in range(10)]
    db = MockDb(collections=[_doc_collection("v")], samples={"v": docs})
    assert classify_schema(db) == "lpg"


# ---------------------------------------------------------------------------
# Edge classification
# ---------------------------------------------------------------------------


def test_edge_with_type_field_classifies_generic_with_type() -> None:
    db = MockDb(
        collections=[
            _doc_collection("vertices"),
            _edge_collection("edges"),
        ],
        samples={
            "vertices": _make_lpg_tier1_docs(["Person"]),
            "edges": _make_lpg_edge_docs(["FOLLOWS", "LIKES"]),
        },
    )
    bundle = build_heuristic_mapping(db)
    assert "FOLLOWS" in bundle.relationships()
    assert "LIKES" in bundle.relationships()
    follows = bundle.relationships()["FOLLOWS"]
    assert follows["style"] == "GENERIC_WITH_TYPE"
    assert follows["edgeCollectionName"] == "edges"
    assert follows["typeField"] == "type"
    assert follows["typeValue"] == "FOLLOWS"


def test_edge_without_type_field_classifies_dedicated_collection() -> None:
    db = MockDb(
        collections=[
            _doc_collection("persons"),
            _edge_collection("follows"),
        ],
        samples={
            "persons": _make_pg_docs(),
            "follows": _make_pg_edge_docs(),
        },
    )
    bundle = build_heuristic_mapping(db)
    follows = bundle.relationships()["follows"]
    assert follows["style"] == "DEDICATED_COLLECTION"
    assert follows["edgeCollectionName"] == "follows"


def test_edge_relation_field_alias_qualifies() -> None:
    """``relation`` is in the edge-discriminator candidate set per
    PRD §6.3.1 step 4; it must qualify the same as ``type``.
    """

    docs = [
        {"_from": f"v/{i}", "_to": f"v/{i+1}", "relation": "MENTIONS"}
        for i in range(10)
    ]
    db = MockDb(
        collections=[_edge_collection("edges")],
        samples={"edges": docs},
    )
    bundle = build_heuristic_mapping(db)
    assert "MENTIONS" in bundle.relationships()
    assert bundle.relationships()["MENTIONS"]["typeField"] == "relation"


# ---------------------------------------------------------------------------
# Schema-shape aggregation
# ---------------------------------------------------------------------------


def test_classify_schema_pure_pg() -> None:
    db = MockDb(
        collections=[
            _doc_collection("persons"),
            _doc_collection("docs"),
            _edge_collection("authored"),
        ],
        samples={
            "persons": _make_pg_docs(),
            "docs": _make_pg_docs(),
            "authored": _make_pg_edge_docs(),
        },
    )
    assert classify_schema(db) == "pg"


def test_classify_schema_pure_lpg() -> None:
    db = MockDb(
        collections=[
            _doc_collection("vertices"),
            _edge_collection("edges"),
        ],
        samples={
            "vertices": _make_lpg_tier1_docs(["Person", "Doc"]),
            "edges": _make_lpg_edge_docs(["FOLLOWS", "LIKES"]),
        },
    )
    assert classify_schema(db) == "lpg"


def test_classify_schema_pure_rpt() -> None:
    db = MockDb(
        collections=[_doc_collection("_triples", system=True)],
        samples={"_triples": _make_rpt_docs()},
    )
    assert classify_schema(db) == "rpt"


def test_classify_schema_hybrid_pg_plus_lpg() -> None:
    db = MockDb(
        collections=[
            _doc_collection("persons"),
            _doc_collection("vertices"),
        ],
        samples={
            "persons": _make_pg_docs(),
            "vertices": _make_lpg_tier1_docs(["Doc"]),
        },
    )
    assert classify_schema(db) == "hybrid"


def test_classify_schema_hybrid_pg_plus_rpt() -> None:
    db = MockDb(
        collections=[
            _doc_collection("persons"),
            _doc_collection("_triples", system=True),
        ],
        samples={
            "persons": _make_pg_docs(),
            "_triples": _make_rpt_docs(),
        },
    )
    assert classify_schema(db) == "hybrid"


def test_classify_schema_hybrid_all_three_styles() -> None:
    db = MockDb(
        collections=[
            _doc_collection("persons"),
            _doc_collection("vertices"),
            _doc_collection("_triples", system=True),
        ],
        samples={
            "persons": _make_pg_docs(),
            "vertices": _make_lpg_tier1_docs(["Doc"]),
            "_triples": _make_rpt_docs(),
        },
    )
    assert classify_schema(db) == "hybrid"


def test_classify_schema_unknown_when_no_collections() -> None:
    db = MockDb(collections=[])
    assert classify_schema(db) == "unknown"


def test_classify_schema_skips_system_collections_except_triples() -> None:
    """Standard system collections (``_users``, ``_jobs``, etc.) must
    be invisible to the detector. The legacy ``_triples`` is the
    intentional exception.
    """

    db = MockDb(
        collections=[
            {"name": "_users", "system": True, "type": "document"},
            {"name": "_jobs", "system": True, "type": "document"},
            _doc_collection("persons"),
        ],
        samples={"persons": _make_pg_docs()},
    )
    classified = classify_schema(db)
    assert classified == "pg"


def test_classify_schema_ignores_invalid_collection_names() -> None:
    """A collection row with a name that fails the validator (e.g.
    empty or starts with a digit) must be ignored silently — not
    crash the detector.
    """

    db = MockDb(
        collections=[
            {"name": "1bad-start", "system": False, "type": "document"},
            {"name": "", "system": False, "type": "document"},
            _doc_collection("good"),
        ],
        samples={"good": _make_pg_docs()},
    )
    assert classify_schema(db) == "pg"


# ---------------------------------------------------------------------------
# Bundle assembly (end-to-end)
# ---------------------------------------------------------------------------


def test_build_heuristic_mapping_carries_required_metadata() -> None:
    """Every PRD §6.3.1 metadata invariant on the produced bundle."""

    db = MockDb(
        collections=[_doc_collection("persons")],
        samples={"persons": _make_pg_docs()},
    )
    bundle = build_heuristic_mapping(db)
    assert bundle.metadata["confidence"] == 0.1
    assert bundle.metadata["reviewRequired"] is True
    assert bundle.metadata["usedBaseline"] is True
    assert "timestamp" in bundle.metadata
    assert bundle.metadata["analyzedCollectionCounts"] == {
        "documentCollections": 1,
        "edgeCollections": 0,
    }
    assert bundle.source is not None
    assert bundle.source.kind == "heuristic"
    warnings = bundle.metadata["warnings"]
    assert any(w["code"] == "W_SCHEMA_HEURISTIC_FALLBACK" for w in warnings)


def test_build_heuristic_mapping_emits_pg_entity_for_collection_style() -> None:
    db = MockDb(
        collections=[_doc_collection("persons")],
        samples={"persons": _make_pg_docs()},
    )
    bundle = build_heuristic_mapping(db)
    assert bundle.entities() == {
        "persons": {"style": "COLLECTION", "collectionName": "persons"}
    }
    assert bundle.metadata["detectedPatterns"] == ["PG_ENTITY_COLLECTION"]


def test_build_heuristic_mapping_emits_one_entity_per_lpg_label() -> None:
    """LPG ``LABEL`` style produces one bundle entity per distinct
    discriminator value, all sharing the underlying collection.
    """

    db = MockDb(
        collections=[_doc_collection("vertices")],
        samples={"vertices": _make_lpg_tier1_docs(["Person", "Doc", "Note"])},
    )
    bundle = build_heuristic_mapping(db)
    entities = bundle.entities()
    assert set(entities.keys()) == {"Person", "Doc", "Note"}
    for label, spec in entities.items():
        assert spec["style"] == "LABEL"
        assert spec["collectionName"] == "vertices"
        assert spec["typeField"] == "type"
        assert spec["typeValue"] == label


def test_build_heuristic_mapping_emits_rpt_entity_with_legacy_columns() -> None:
    db = MockDb(
        collections=[_doc_collection("_triples", system=True)],
        samples={"_triples": _make_rpt_docs()},
    )
    bundle = build_heuristic_mapping(db)
    spec = bundle.entities()["_triples"]
    assert spec["style"] == "RPT"
    assert spec["triplesCollection"] == "_triples"
    assert spec["subjectColumn"] == "subject_uri"
    assert spec["predicateColumn"] == "predicate"
    assert spec["objectUriColumn"] == "object_uri"
    assert spec["objectValueColumn"] == "object_value"


def test_build_heuristic_mapping_detected_patterns_uses_closed_tag_set() -> None:
    """Every tag in ``metadata.detectedPatterns`` must be from the
    PRD §6.3.1 closed set. No ad-hoc tag may sneak in.
    """

    db = MockDb(
        collections=[
            _doc_collection("persons"),
            _doc_collection("vertices"),
            _doc_collection("_triples", system=True),
            _edge_collection("authored"),
            _edge_collection("edges"),
        ],
        samples={
            "persons": _make_pg_docs(),
            "vertices": _make_lpg_tier1_docs(["Doc"]),
            "_triples": _make_rpt_docs(),
            "authored": _make_pg_edge_docs(),
            "edges": _make_lpg_edge_docs(["LINKS"]),
        },
    )
    bundle = build_heuristic_mapping(db)
    closed_set = {
        "PG_ENTITY_COLLECTION",
        "LPG_LABEL",
        "RPT_TRIPLES",
        "PG_DEDICATED_EDGE",
        "LPG_GENERIC_EDGE",
        "RPT_OBJECT_PROPERTY",
    }
    actual = set(bundle.metadata["detectedPatterns"])
    assert actual <= closed_set, f"Tag set drift: {actual - closed_set}"
    assert actual == {
        "PG_ENTITY_COLLECTION",
        "LPG_LABEL",
        "RPT_TRIPLES",
        "PG_DEDICATED_EDGE",
        "LPG_GENERIC_EDGE",
    }


def test_build_heuristic_mapping_accepts_explicit_schema_type() -> None:
    """The PRD signature requires *schema_type* as a keyword arg; we
    accept ``"auto"`` for ergonomics but explicit values must
    propagate to ``metadata.schemaType``.
    """

    db = MockDb(
        collections=[_doc_collection("persons")],
        samples={"persons": _make_pg_docs()},
    )
    bundle = build_heuristic_mapping(db, schema_type="hybrid")
    assert bundle.metadata["schemaType"] == "hybrid"


def test_build_heuristic_mapping_accepts_injected_clock() -> None:
    pinned = datetime(2026, 5, 12, 16, 30, 0, tzinfo=UTC)
    db = MockDb(collections=[], samples={})
    bundle = build_heuristic_mapping(db, now=pinned)
    assert bundle.metadata["timestamp"] == pinned.isoformat()


def test_build_heuristic_mapping_against_empty_db_is_safe() -> None:
    """An empty database is a legitimate state (fresh deployment).
    The detector must still produce a valid bundle, not raise.
    """

    db = MockDb(collections=[])
    bundle = build_heuristic_mapping(db)
    assert isinstance(bundle, MappingBundle)
    assert bundle.entities() == {}
    assert bundle.relationships() == {}
    assert bundle.metadata["schemaType"] == "unknown"
    # And the bundle must still be fingerprintable.
    fp = compute_bundle_fingerprint(bundle)
    assert len(fp.shape) == 64


def test_build_heuristic_mapping_round_trip_through_wire_dict() -> None:
    """The bundle must be the same shape that
    ``mapping_from_wire_dict`` accepts — which is automatic since
    the assembler routes its output through ``mapping_from_wire_dict``,
    but we test the contract anyway as a regression guard.
    """

    from arango_sparql.translate.mapping import mapping_to_wire_dict

    db = MockDb(
        collections=[
            _doc_collection("persons"),
            _edge_collection("follows"),
        ],
        samples={
            "persons": _make_pg_docs(),
            "follows": _make_pg_edge_docs(),
        },
    )
    bundle = build_heuristic_mapping(db)
    wire = mapping_to_wire_dict(bundle)
    assert "physicalMapping" in wire
    assert "conceptualSchema" in wire
    assert "metadata" in wire


# ---------------------------------------------------------------------------
# Deterministic ordering
# ---------------------------------------------------------------------------


def test_build_heuristic_mapping_is_deterministic() -> None:
    """Two runs against the same mock DB must produce byte-identical
    fingerprints (modulo the timestamp). This is the precondition
    for the schema cache being able to detect that no real change
    occurred.
    """

    pinned = datetime(2026, 1, 1, tzinfo=UTC)

    def fresh_db() -> MockDb:
        return MockDb(
            collections=[
                _doc_collection("vertices"),
                _edge_collection("edges"),
            ],
            samples={
                "vertices": _make_lpg_tier1_docs(["Person", "Doc"]),
                "edges": _make_lpg_edge_docs(["FOLLOWS", "LIKES"]),
            },
        )

    fp_a = compute_bundle_fingerprint(build_heuristic_mapping(fresh_db(), now=pinned))
    fp_b = compute_bundle_fingerprint(build_heuristic_mapping(fresh_db(), now=pinned))
    assert fp_a.shape == fp_b.shape
    assert fp_a.counts == fp_b.counts


# ---------------------------------------------------------------------------
# Sampling cap
# ---------------------------------------------------------------------------


def test_sampler_passes_sample_size_to_aql_limit() -> None:
    """The sampler must honour the ``sample_size`` parameter so a
    huge collection does not blow up cost. We verify by inspecting
    the bind vars the mock AQL saw.
    """

    db = MockDb(
        collections=[_doc_collection("persons")],
        samples={"persons": _make_pg_docs(100)},
    )
    classify_schema(db, sample_size=7)
    sample_queries = [
        bv for q, bv in db.aql.queries_seen if "@col" in bv and bv["@col"] == "persons"
    ]
    assert sample_queries
    assert all(bv["n"] == 7 for bv in sample_queries)


def test_sampler_default_sample_size_is_twenty() -> None:
    """PRD §6.3.1 step 1 pins the default at 20."""

    assert DEFAULT_SAMPLE_SIZE == 20


def test_sampler_zero_sample_size_returns_empty() -> None:
    """``sample_size=0`` is degenerate but must not raise."""

    db = MockDb(
        collections=[_doc_collection("persons")],
        samples={"persons": _make_pg_docs()},
    )
    bundle = build_heuristic_mapping(db, sample_size=0)
    # Zero-sample collection cannot classify; falls back to COLLECTION
    # by the per-collection rules.
    assert "persons" in bundle.entities()


# ---------------------------------------------------------------------------
# Coverage threshold sentinel
# ---------------------------------------------------------------------------


def test_coverage_threshold_constant_matches_prd() -> None:
    """PRD §6.3.1 pins this at 80 %. Any change here is a
    spec-level decision — and a cache-invalidating change."""

    assert COVERAGE_THRESHOLD == 0.80
