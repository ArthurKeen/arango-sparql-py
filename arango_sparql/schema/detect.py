"""Heuristic schema detector (PRD §6.3.1).

The **fallback** path for ``MappingBundle`` acquisition. The canonical
path is the analyzer-backed ``arango_sparql.schema.acquire`` (PRD
§6.3.2); this module exists for two reasons:

1. **Dev-loop ergonomics** — running ``arango-sparql-py`` against a
   playground database without first installing the analyzer extra
   (``pip install arango-sparql-py[analyzer]``) should still produce
   *something* the translator can chew on, even if the resulting
   bundle's confidence is low and review is required.
2. **Per-request fallback** — when ``ARANGO_SPARQL_ALLOW_HEURISTIC=true``
   (PRD §6.3.4 default), a transient analyzer failure mid-flight
   degrades to a heuristic bundle for that request rather than
   returning ``503``.

The detector samples up to ``sample_size`` documents per non-system
collection and applies the rules from PRD §6.3.1 in this order:

1. **RPT pattern** — a collection looks RPT-shaped if ≥ 80 % of
   sampled docs carry all three of ``subject_uri`` / ``predicate`` /
   (``object_uri`` ∨ ``object_value``), OR the collection is named
   exactly ``_triples`` (legacy Foxx convention; PRD §6.2 RPT row).
2. **PG vs LPG discriminator** — tier-1 fields (``type``, ``_type``,
   ``entityType``) qualify on the 80 %-coverage rule alone. Tier-2
   fields (``label``, ``labels``, ``kind``) additionally require
   ≤ 32 distinct values, a low-cardinality ratio (distinct / sampled
   ≤ 0.5), and class-like value strings (``[A-Za-z0-9_-]+``).
3. **Edge classification** — same tier-1 / tier-2 rules against
   ``{type, relation, relType, _type}`` to distinguish typed
   (``GENERIC_WITH_TYPE``) from dedicated (``DEDICATED_COLLECTION``)
   edge collections.
4. **Aggregate** — per-collection signals are tallied; all-PG ⇒
   ``"pg"``, all-LPG ⇒ ``"lpg"``, all-RPT ⇒ ``"rpt"``, mixed ⇒
   ``"hybrid"``.

Output bundles always carry ``source.kind = "heuristic"``,
``metadata.confidence = 0.1``, ``metadata.reviewRequired = true``,
``metadata.usedBaseline = true``, and ``metadata.detectedPatterns``
populated with the closed tag set from PRD §6.3.1.

Database adapter contract: every public function takes a
``StandardDatabase``-shaped object exposing two members:

* ``db.collections() -> list[dict]`` returning the python-arango shape
  ``{"name": str, "system": bool, "type": "document"|"edge", ...}``.
* ``db.aql.execute(query, bind_vars) -> Iterable[dict]`` for sampling.

Tests use a minimal duck-typed mock; the live integration with
``StandardDatabase`` lives in :mod:`arango_sparql.schema.acquire`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from arango_sparql.translate.mapping import (
    MappingBundle,
    is_valid_collection_name,
    mapping_from_wire_dict,
)

# Sample-size cap from PRD §6.3.1 step 1. Held as a constant so the
# acquire layer can pass it through to /schema/introspect query
# parameters in a future slice without redeclaring it.
DEFAULT_SAMPLE_SIZE: int = 20

# Coverage threshold from PRD §6.3.1 — a candidate field "qualifies"
# when ≥ COVERAGE_THRESHOLD of sampled documents carry it (with a
# parsable, class-like value for tier-2).
COVERAGE_THRESHOLD: float = 0.80

# Tier-2 cardinality cap from PRD §6.3.1 step 3. A field with > 32
# distinct values is treated as free text, not a discriminator.
TIER_2_MAX_DISTINCT: int = 32

# Tier-2 cardinality *ratio* cap. Even with ≤ 32 distinct values, a
# field is rejected if `distinct / sampled` is too high — that pattern
# is closer to "every doc has a unique value" than "every doc has one
# of a small fixed set". PRD §6.3.1 says "low-cardinality ratio" without
# pinning the threshold; 0.5 is the canonical sister-project value.
TIER_2_MAX_RATIO: float = 0.5

# Tier-1 entity discriminator candidates (in priority order). PRD
# §6.3.1 step 3 lists these explicitly.
_TIER_1_ENTITY_DISCRIMINATORS: tuple[str, ...] = ("type", "_type", "entityType")
_TIER_2_ENTITY_DISCRIMINATORS: tuple[str, ...] = ("label", "labels", "kind")

# Edge-collection discriminator candidates (PRD §6.3.1 step 4). All
# treated as tier-1 for our purposes — relationship-level
# discriminators are far less likely to be free-text.
_EDGE_DISCRIMINATORS: tuple[str, ...] = ("type", "relation", "relType", "_type")

# Class-like value regex from PRD §6.3.1 step 3. Anchored, ASCII-only;
# rejects spaces, dots, slashes (typical of free-text values).
_CLASS_LIKE_VALUE_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_-]+$")

# RPT column conventions in priority order. The first tuple matches
# the legacy Foxx `_triples` shape (PRD §6.2 RPT row,
# `references/arango-sparql/src/lib/rpt-translator.js`); the second
# accepts the bare-noun spelling some downstream tooling uses. The
# canonical analyzer output is the legacy shape.
_RPT_CANDIDATE_SHAPES: tuple[dict[str, str], ...] = (
    {
        "subject": "subject_uri",
        "predicate": "predicate",
        "object_uri": "object_uri",
        "object_value": "object_value",
    },
    {
        "subject": "subject",
        "predicate": "predicate",
        "object_uri": "object_uri",
        "object_value": "object_value",
    },
)

# Legacy Foxx convention: a collection literally named ``_triples``
# is RPT, even if our heuristic sample misses (e.g. it's empty).
_LEGACY_TRIPLES_COLLECTION_NAME: str = "_triples"

# detectedPatterns tag dictionary — closed set per PRD §6.3.1.
_PATTERN_TAG_FOR_STYLE: dict[str, str] = {
    "COLLECTION": "PG_ENTITY_COLLECTION",
    "LABEL": "LPG_LABEL",
    "RPT": "RPT_TRIPLES",
    "DEDICATED_COLLECTION": "PG_DEDICATED_EDGE",
    "GENERIC_WITH_TYPE": "LPG_GENERIC_EDGE",
    "RPT_EDGE": "RPT_OBJECT_PROPERTY",
}

SchemaType = Literal["pg", "lpg", "rpt", "hybrid", "unknown"]


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RptDetectionResult:
    """Outcome of running RPT pattern detection against one collection.

    Carries the column overrides the synthesizer needs to populate the
    bundle's RPT entry. ``coverage_ratio`` is what fraction of sampled
    docs satisfied the matching shape (≥ 0.80 by the time
    ``is_rpt`` is ``True``); ``reasons`` records the path the
    classifier took so an operator can diagnose a "why did you
    classify this as RPT?" question without re-running the heuristic.
    """

    collection: str
    is_rpt: bool
    triples_collection: str | None = None
    subject_column: str = "subject_uri"
    predicate_column: str = "predicate"
    object_uri_column: str = "object_uri"
    object_value_column: str = "object_value"
    coverage_ratio: float = 0.0
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CollectionClassification:
    """Per-collection conclusion the heuristic walker reaches.

    Used internally by :func:`build_heuristic_mapping` to assemble
    the bundle; exposed publicly so the upcoming
    ``/schema/properties`` route can render the per-collection
    confidence breakdown without re-running the heuristic.
    """

    name: str
    is_edge: bool
    style: Literal[
        "COLLECTION",
        "LABEL",
        "RPT",
        "DEDICATED_COLLECTION",
        "GENERIC_WITH_TYPE",
        "RPT_EDGE",
        "UNKNOWN",
    ]
    type_field: str | None = None
    type_values: frozenset[str] = field(default_factory=frozenset)
    rpt: RptDetectionResult | None = None
    sampled_docs: int = 0


# ---------------------------------------------------------------------------
# Database-adapter helpers (duck-typed against python-arango)
# ---------------------------------------------------------------------------


def _list_user_collections(db: Any) -> list[tuple[str, bool]]:
    """Return the non-system collections in *db* as ``(name, is_edge)``
    pairs.

    Tolerates two shapes from python-arango: ``type`` as the string
    ``"document"`` / ``"edge"`` (current shape) or as the integer
    ``2`` / ``3`` (older shape). Filters out anything with
    ``system=True`` *except* a collection literally named
    ``_triples`` — that one is the legacy Foxx RPT bucket and we
    *want* to detect it.
    """

    rows = db.collections()
    out: list[tuple[str, bool]] = []
    for row in rows:
        name = row.get("name") if isinstance(row, dict) else None
        if not isinstance(name, str):
            continue
        if not is_valid_collection_name(name):
            continue
        is_system = bool(row.get("system")) if isinstance(row, dict) else False
        if is_system and name != _LEGACY_TRIPLES_COLLECTION_NAME:
            continue
        ctype = row.get("type") if isinstance(row, dict) else None
        is_edge = ctype == "edge" or ctype == 3
        out.append((name, is_edge))
    out.sort(key=lambda pair: pair[0])
    return out


def _sample_collection(db: Any, name: str, sample_size: int) -> list[dict[str, Any]]:
    """Return up to *sample_size* documents from collection *name*.

    Uses the ``@@col`` collection-bind-var so python-arango
    server-side-validates the collection identifier — *we still*
    re-validate at the boundary in :func:`_list_user_collections`
    so a bad name never reaches AQL emit. ``LIMIT @n`` is a
    parameter, not a literal, for the same reason.
    """

    if sample_size <= 0:
        return []
    cursor = db.aql.execute(
        "FOR doc IN @@col LIMIT @n RETURN doc",
        bind_vars={"@col": name, "n": int(sample_size)},
    )
    return [doc for doc in cursor if isinstance(doc, dict)]


# ---------------------------------------------------------------------------
# RPT pattern detection (PRD §6.3.1 step 2)
# ---------------------------------------------------------------------------


def _classify_rpt_from_sample(
    name: str, sample: list[dict[str, Any]]
) -> RptDetectionResult:
    """Apply the RPT-pattern-matching rules to a sampled collection.

    Returns a populated :class:`RptDetectionResult` regardless of the
    outcome. ``is_rpt`` distinguishes a positive match; ``reasons``
    records why (or why not) for later forensic display.
    """

    reasons: list[str] = []

    # Path 1: named ``_triples`` is RPT by legacy convention. Only
    # promote when the sample is non-empty so a freshly-created empty
    # ``_triples`` collection doesn't anchor the entire detector to
    # RPT.
    if name == _LEGACY_TRIPLES_COLLECTION_NAME and sample:
        cols = _RPT_CANDIDATE_SHAPES[0]
        return RptDetectionResult(
            collection=name,
            is_rpt=True,
            triples_collection=name,
            subject_column=cols["subject"],
            predicate_column=cols["predicate"],
            object_uri_column=cols["object_uri"],
            object_value_column=cols["object_value"],
            coverage_ratio=1.0,
            reasons=("name == _triples (legacy Foxx convention)",),
        )

    if not sample:
        reasons.append("no documents sampled (empty collection?)")
        return RptDetectionResult(collection=name, is_rpt=False, reasons=tuple(reasons))

    n = len(sample)
    best: tuple[float, dict[str, str]] | None = None
    for cols in _RPT_CANDIDATE_SHAPES:
        matches = sum(1 for d in sample if _matches_rpt_columns(d, cols))
        ratio = matches / n
        if best is None or ratio > best[0]:
            best = (ratio, cols)
        if ratio >= COVERAGE_THRESHOLD:
            return RptDetectionResult(
                collection=name,
                is_rpt=True,
                triples_collection=name,
                subject_column=cols["subject"],
                predicate_column=cols["predicate"],
                object_uri_column=cols["object_uri"],
                object_value_column=cols["object_value"],
                coverage_ratio=ratio,
                reasons=(
                    f"≥{int(COVERAGE_THRESHOLD * 100)}% of sampled docs match "
                    f"shape {cols!r} (matched {matches}/{n})",
                ),
            )

    best_ratio, best_cols = best if best else (0.0, _RPT_CANDIDATE_SHAPES[0])
    reasons.append(
        f"best candidate {best_cols!r} matched only {best_ratio:.0%} of sampled "
        f"docs (need ≥{int(COVERAGE_THRESHOLD * 100)}%)"
    )
    return RptDetectionResult(
        collection=name,
        is_rpt=False,
        coverage_ratio=best_ratio,
        reasons=tuple(reasons),
    )


def _matches_rpt_columns(doc: dict[str, Any], cols: dict[str, str]) -> bool:
    """Check whether *doc* carries the four RPT-shape columns.

    A doc qualifies when the subject and predicate columns are both
    present (with non-``None`` values) AND at least one of the two
    object columns is present. Empty-string values count — they're
    legal RDF literals — but ``None`` values do not.
    """

    if doc.get(cols["subject"]) is None:
        return False
    if doc.get(cols["predicate"]) is None:
        return False
    obj_uri = doc.get(cols["object_uri"])
    obj_val = doc.get(cols["object_value"])
    return obj_uri is not None or obj_val is not None


def detect_rpt_pattern(
    db: Any, *, sample_size: int = DEFAULT_SAMPLE_SIZE
) -> dict[str, RptDetectionResult]:
    """Run RPT detection against every non-system collection in *db*
    (plus the legacy ``_triples`` system bucket if present).

    Returns a dict keyed by collection name. Callers are free to
    inspect the full result map; :func:`build_heuristic_mapping`
    reads only the entries with ``is_rpt=True``.
    """

    out: dict[str, RptDetectionResult] = {}
    for name, _is_edge in _list_user_collections(db):
        sample = _sample_collection(db, name, sample_size)
        out[name] = _classify_rpt_from_sample(name, sample)
    return out


# ---------------------------------------------------------------------------
# Discriminator detection (PRD §6.3.1 steps 3 & 4)
# ---------------------------------------------------------------------------


def _flatten_discriminator_values(values: list[Any]) -> list[str]:
    """Flatten a tier-2 discriminator's raw values into a list of
    string labels.

    Handles the ``"labels": ["Person", "Manager"]`` case where the
    field carries a list-of-strings rather than a scalar. Drops
    ``None`` and non-string items so a single bogus document does
    not corrupt the whole field's classification.
    """

    out: list[str] = []
    for v in values:
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str) and item:
                    out.append(item)
        elif isinstance(v, str) and v:
            out.append(v)
    return out


def _detect_discriminator(
    sample: list[dict[str, Any]],
    *,
    tier_1_fields: tuple[str, ...],
    tier_2_fields: tuple[str, ...],
) -> tuple[str, frozenset[str]] | None:
    """Apply tier-1 / tier-2 discriminator rules from PRD §6.3.1.

    Returns ``(field_name, distinct_values)`` for the first qualifying
    candidate, or ``None`` when no field qualifies. Tier-1 fields are
    tested before tier-2; within each tier the order in
    ``tier_1_fields`` / ``tier_2_fields`` is preserved (so callers
    can express priority).
    """

    if not sample:
        return None
    n = len(sample)

    for field_name in tier_1_fields:
        present = [d[field_name] for d in sample if field_name in d]
        if len(present) / n < COVERAGE_THRESHOLD:
            continue
        values = frozenset(_flatten_discriminator_values(present))
        if not values:
            continue
        return field_name, values

    for field_name in tier_2_fields:
        present = [d[field_name] for d in sample if field_name in d]
        if len(present) / n < COVERAGE_THRESHOLD:
            continue
        flat = _flatten_discriminator_values(present)
        values = frozenset(flat)
        if not values:
            continue
        if len(values) > TIER_2_MAX_DISTINCT:
            continue
        if len(values) / n > TIER_2_MAX_RATIO:
            continue
        if not all(_CLASS_LIKE_VALUE_RE.match(v) for v in values):
            continue
        return field_name, values

    return None


# ---------------------------------------------------------------------------
# Per-collection classification
# ---------------------------------------------------------------------------


def _classify_collection(
    name: str, is_edge: bool, sample: list[dict[str, Any]]
) -> CollectionClassification:
    """Apply all PRD §6.3.1 rules to a single collection's sample,
    returning the per-collection conclusion.
    """

    sampled = len(sample)

    if not is_edge:
        rpt = _classify_rpt_from_sample(name, sample)
        if rpt.is_rpt:
            return CollectionClassification(
                name=name,
                is_edge=False,
                style="RPT",
                rpt=rpt,
                sampled_docs=sampled,
            )
        disc = _detect_discriminator(
            sample,
            tier_1_fields=_TIER_1_ENTITY_DISCRIMINATORS,
            tier_2_fields=_TIER_2_ENTITY_DISCRIMINATORS,
        )
        if disc is not None:
            return CollectionClassification(
                name=name,
                is_edge=False,
                style="LABEL",
                type_field=disc[0],
                type_values=disc[1],
                sampled_docs=sampled,
            )
        return CollectionClassification(
            name=name,
            is_edge=False,
            style="COLLECTION",
            sampled_docs=sampled,
        )

    # Edge collection: no RPT path (RPT_EDGE rides on a doc collection
    # named ``_triples``; an actual edge collection's entries are
    # neither subject/predicate/object triples nor relationship rows
    # of an RPT bucket). Apply only the tier-1 discriminator to the
    # edge candidates.
    disc = _detect_discriminator(
        sample,
        tier_1_fields=_EDGE_DISCRIMINATORS,
        tier_2_fields=(),
    )
    if disc is not None:
        return CollectionClassification(
            name=name,
            is_edge=True,
            style="GENERIC_WITH_TYPE",
            type_field=disc[0],
            type_values=disc[1],
            sampled_docs=sampled,
        )
    return CollectionClassification(
        name=name,
        is_edge=True,
        style="DEDICATED_COLLECTION",
        sampled_docs=sampled,
    )


def _classify_all(
    db: Any, *, sample_size: int
) -> list[CollectionClassification]:
    """Classify every user collection in *db*. Output is sorted by
    collection name so two runs against the same database produce
    bit-identical classifications, simplifying caching.
    """

    out: list[CollectionClassification] = []
    for name, is_edge in _list_user_collections(db):
        sample = _sample_collection(db, name, sample_size)
        out.append(_classify_collection(name, is_edge, sample))
    return out


# ---------------------------------------------------------------------------
# Top-level classification (PRD §6.3.1 step 5)
# ---------------------------------------------------------------------------


def classify_schema(
    db: Any, *, sample_size: int = DEFAULT_SAMPLE_SIZE
) -> SchemaType:
    """Return the database's overall schema-shape classification.

    One of ``"pg"``, ``"lpg"``, ``"rpt"``, ``"hybrid"``, or
    ``"unknown"`` (no non-system collections found, or every
    collection is unclassifiable). Aggregation rules per PRD §6.3.1
    step 5: same style across every collection ⇒ that style; mixed
    styles ⇒ ``"hybrid"``.
    """

    classifications = _classify_all(db, sample_size=sample_size)
    if not classifications:
        return "unknown"

    entity_styles = {c.style for c in classifications if not c.is_edge}
    edge_styles = {c.style for c in classifications if c.is_edge}

    is_pg = entity_styles <= {"COLLECTION"} and edge_styles <= {
        "DEDICATED_COLLECTION"
    }
    is_lpg = entity_styles <= {"LABEL"} and edge_styles <= {"GENERIC_WITH_TYPE"}
    is_rpt = entity_styles <= {"RPT"} and not edge_styles

    if is_pg and entity_styles:
        return "pg"
    if is_lpg and entity_styles:
        return "lpg"
    if is_rpt and entity_styles:
        return "rpt"
    if not entity_styles and not edge_styles:
        return "unknown"
    return "hybrid"


# ---------------------------------------------------------------------------
# Bundle assembly (PRD §6.3.1 final paragraph)
# ---------------------------------------------------------------------------


def _entity_spec_from_classification(c: CollectionClassification) -> dict[str, Any]:
    """Build the per-entity ``physicalMapping.entities[label]`` dict
    from a per-collection classification. Mirrors what the analyzer
    would emit for each of the three styles so downstream consumers
    cannot tell which producer built the bundle.
    """

    if c.style == "RPT":
        rpt = c.rpt
        if rpt is None or rpt.triples_collection is None:
            return {"style": "RPT", "triplesCollection": c.name}
        return {
            "style": "RPT",
            "triplesCollection": rpt.triples_collection,
            "subjectColumn": rpt.subject_column,
            "predicateColumn": rpt.predicate_column,
            "objectUriColumn": rpt.object_uri_column,
            "objectValueColumn": rpt.object_value_column,
        }
    if c.style == "COLLECTION":
        return {"style": "COLLECTION", "collectionName": c.name}
    if c.style == "LABEL":
        # LPG: one collection holds many entity types. The "label"
        # for the bundle entry is the *type value*, not the
        # collection name. The caller is responsible for emitting
        # one entity per distinct type value.
        return {
            "style": "LABEL",
            "collectionName": c.name,
            "typeField": c.type_field,
        }
    return {"style": "COLLECTION", "collectionName": c.name}


def _emit_entities(classifications: list[CollectionClassification]) -> dict[str, Any]:
    """Project entity classifications into ``physicalMapping.entities``.

    For ``COLLECTION`` and ``RPT`` styles, one entity per collection
    keyed by the collection name. For ``LABEL`` style, one entity
    per distinct discriminator value, all sharing the underlying
    collection.
    """

    entities: dict[str, Any] = {}
    for c in classifications:
        if c.is_edge:
            continue
        if c.style == "LABEL":
            for type_value in sorted(c.type_values):
                if not type_value:
                    continue
                entities[type_value] = {
                    "style": "LABEL",
                    "collectionName": c.name,
                    "typeField": c.type_field,
                    "typeValue": type_value,
                }
        else:
            entities[c.name] = _entity_spec_from_classification(c)
    return entities


def _emit_relationships(
    classifications: list[CollectionClassification],
) -> dict[str, Any]:
    """Project edge classifications into
    ``physicalMapping.relationships``.

    For ``DEDICATED_COLLECTION`` style, one relationship per edge
    collection keyed by the collection name. For ``GENERIC_WITH_TYPE``
    style, one relationship per distinct discriminator value, all
    sharing the underlying edge collection.

    ``fromEntity`` / ``toEntity`` are left as ``"Any"`` — inferring
    them requires sampling ``_from`` / ``_to`` and dereferencing them
    through the entity classifications, which is intentionally out
    of scope for the v0.x heuristic detector (PRD §6.3.1's "no
    cross-collection inference" guarantee). The analyzer fills
    these when present.
    """

    relationships: dict[str, Any] = {}
    for c in classifications:
        if not c.is_edge:
            continue
        if c.style == "GENERIC_WITH_TYPE":
            for type_value in sorted(c.type_values):
                if not type_value:
                    continue
                relationships[type_value] = {
                    "style": "GENERIC_WITH_TYPE",
                    "edgeCollectionName": c.name,
                    "typeField": c.type_field,
                    "typeValue": type_value,
                    "fromEntity": "Any",
                    "toEntity": "Any",
                }
        else:
            # DEDICATED_COLLECTION (or fallback UNKNOWN treated as
            # dedicated): the relationship type IS the collection
            # name.
            relationships[c.name] = {
                "style": "DEDICATED_COLLECTION",
                "edgeCollectionName": c.name,
                "fromEntity": "Any",
                "toEntity": "Any",
            }
    return relationships


def _emit_conceptual(physical: dict[str, Any]) -> dict[str, Any]:
    """Build the conceptual half of the bundle from the assembled
    physical half. Each entity surfaces as a ``{name, labels:
    [name], properties: []}`` shape (matching the sister project's
    fixture conventions). Each relationship gets a
    ``{type, fromEntity, toEntity, properties: []}`` entry.
    """

    entities = [
        {"name": name, "labels": [name], "properties": []}
        for name in sorted(physical.get("entities", {}))
    ]
    rels = []
    for rtype, spec in sorted(physical.get("relationships", {}).items()):
        rels.append(
            {
                "type": rtype,
                "fromEntity": spec.get("fromEntity", "Any"),
                "toEntity": spec.get("toEntity", "Any"),
                "properties": [],
            }
        )
    return {"entities": entities, "properties": [], "relationships": rels}


def _emit_detected_pattern_tags(
    classifications: list[CollectionClassification],
) -> list[str]:
    """Project the per-collection style decisions onto the closed
    detectedPatterns tag set from PRD §6.3.1. Order is the canonical
    PG → LPG → RPT progression so two runs over the same
    classifications produce identical metadata.
    """

    tags: set[str] = set()
    for c in classifications:
        tag = _PATTERN_TAG_FOR_STYLE.get(c.style)
        if tag is not None:
            tags.add(tag)
    canonical_order = (
        "PG_ENTITY_COLLECTION",
        "LPG_LABEL",
        "RPT_TRIPLES",
        "PG_DEDICATED_EDGE",
        "LPG_GENERIC_EDGE",
        "RPT_OBJECT_PROPERTY",
    )
    return [t for t in canonical_order if t in tags]


def build_heuristic_mapping(
    db: Any,
    *,
    schema_type: SchemaType | str = "auto",
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    now: datetime | None = None,
) -> MappingBundle:
    """Build a heuristic :class:`MappingBundle` from the live database.

    Matches the PRD §6.3.1 signature with one ergonomic concession:
    *schema_type* defaults to ``"auto"`` (compute it via
    :func:`classify_schema` internally) so test code and the dev
    loop can call this without an upstream classification step.
    Production callers in ``acquire.py`` will pass an explicit
    *schema_type* derived from the analyzer's verdict.

    The returned bundle always carries:

    * ``source.kind = "heuristic"``
    * ``metadata.confidence = 0.1`` (PRD §6.3.1)
    * ``metadata.reviewRequired = True``
    * ``metadata.usedBaseline = True``
    * ``metadata.detectedPatterns`` from the closed PRD tag set
    * ``metadata.warnings`` containing ``W_SCHEMA_HEURISTIC_FALLBACK``
      so the consumer's UI / log layer can surface the low-confidence
      provenance without re-deriving it from ``source.kind``.

    *now* is injectable so tests can pin ``metadata.timestamp``.
    """

    classifications = _classify_all(db, sample_size=sample_size)
    if schema_type == "auto":
        # Reuse the classifications we already computed instead of
        # walking the DB twice.
        schema_type = _aggregate_classification(classifications)

    physical_entities = _emit_entities(classifications)
    physical_rels = _emit_relationships(classifications)
    physical_mapping = {
        "entities": physical_entities,
        "relationships": physical_rels,
    }
    conceptual = _emit_conceptual(physical_mapping)
    timestamp = (now if now is not None else datetime.now(UTC)).isoformat()

    doc_count = sum(1 for c in classifications if not c.is_edge)
    edge_count = sum(1 for c in classifications if c.is_edge)
    metadata: dict[str, Any] = {
        "confidence": 0.1,
        "reviewRequired": True,
        "usedBaseline": True,
        "timestamp": timestamp,
        "detectedPatterns": _emit_detected_pattern_tags(classifications),
        "analyzedCollectionCounts": {
            "documentCollections": doc_count,
            "edgeCollections": edge_count,
        },
        "assumptions": [
            (
                "Heuristic detector — physical mapping inferred from "
                f"{sample_size}-doc per-collection samples, not from an "
                "OWL ontology. Cross-collection relationships left as "
                "fromEntity/toEntity = 'Any'."
            ),
        ],
        "warnings": [
            {
                "code": "W_SCHEMA_HEURISTIC_FALLBACK",
                "message": (
                    "Mapping was derived heuristically rather than from "
                    "arangodb-schema-analyzer; review before using for "
                    "production workloads (PRD §6.3.1)."
                ),
            }
        ],
        "schemaType": schema_type,
    }

    wire = {
        "conceptualSchema": conceptual,
        "physicalMapping": physical_mapping,
        "metadata": metadata,
        "source": {
            "kind": "heuristic",
            "notes": (
                f"Built by arango_sparql.schema.detect "
                f"(sample_size={sample_size}, schema_type={schema_type!r})"
            ),
        },
    }
    return mapping_from_wire_dict(wire)


def _aggregate_classification(
    classifications: list[CollectionClassification],
) -> SchemaType:
    """Internal helper — aggregate per-collection classifications into
    the overall schema-shape verdict. Factored out of
    :func:`classify_schema` so :func:`build_heuristic_mapping` can
    reuse the verdict without re-walking the database.
    """

    if not classifications:
        return "unknown"
    entity_styles = {c.style for c in classifications if not c.is_edge}
    edge_styles = {c.style for c in classifications if c.is_edge}
    is_pg = entity_styles <= {"COLLECTION"} and edge_styles <= {"DEDICATED_COLLECTION"}
    is_lpg = entity_styles <= {"LABEL"} and edge_styles <= {"GENERIC_WITH_TYPE"}
    is_rpt = entity_styles <= {"RPT"} and not edge_styles
    if is_pg and entity_styles:
        return "pg"
    if is_lpg and entity_styles:
        return "lpg"
    if is_rpt and entity_styles:
        return "rpt"
    if not entity_styles and not edge_styles:
        return "unknown"
    return "hybrid"


__all__ = [
    "COVERAGE_THRESHOLD",
    "CollectionClassification",
    "DEFAULT_SAMPLE_SIZE",
    "RptDetectionResult",
    "SchemaType",
    "TIER_2_MAX_DISTINCT",
    "TIER_2_MAX_RATIO",
    "build_heuristic_mapping",
    "classify_schema",
    "detect_rpt_pattern",
]
