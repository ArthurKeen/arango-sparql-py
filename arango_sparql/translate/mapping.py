"""MappingBundle wire-format primitives.

Defines the JSON wire shape that ``arango-sparql-py`` uses to represent
a customer's physical schema (collections, edge collections, RPT
triple-store columns). Mirrors the shape produced by
``arangodb-schema-analyzer`` (PyPI, optional ``[analyzer]`` extra) and
consumed by the sister project ``arango-cypher-py``, extended with RPT
(RDF triple-store layout) entries that the sister project does not
have.

Three primary symbols:

* :class:`MappingBundle` — frozen dataclass; the in-process
  representation of a schema mapping. Immutable after construction so a
  freshly-acquired mapping can be cached and shared between requests
  without lock contention.
* :func:`mapping_from_wire_dict` — JSON-dict → :class:`MappingBundle`
  normaliser. Accepts both ``camelCase`` (the analyzer's native shape)
  and ``snake_case`` at the top level and at the entity-spec level.
* :func:`mapping_to_wire_dict` — inverse, emitting the canonical
  camelCase shape for routes that return a mapping (e.g.
  ``/schema/mapping``).

Plus two identifier validators (:func:`is_valid_collection_name`,
:func:`is_valid_field_name`) and the typed :class:`MappingError`
exception.

When the foundational ``arango-query-core`` package is published to
PyPI (see PRD §18 glossary), this module is replaced by a single
``from arango_query_core import MappingBundle, ...`` line; behaviour
and wire shape remain identical. Until then this is the local
authoritative copy.

See also: PRD §6.3 "Schema layer" for the consumer contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from ..errors import SparqlError

# ---------------------------------------------------------------------------
# Typed error
# ---------------------------------------------------------------------------


class MappingError(SparqlError):
    """Raised when a wire-format mapping bundle is structurally invalid.

    Stable error code: ``E_MAPPING_INVALID``. Route layer translates
    this to HTTP 422 with the sanitized message; never wraps the
    underlying exception object so client error responses cannot leak
    backend internals (PRD §8.4 "Error redaction").
    """

    code = "E_MAPPING_INVALID"


# ---------------------------------------------------------------------------
# Identifier validation
# ---------------------------------------------------------------------------


# ArangoDB collection-name grammar (server-enforced; documented at
# https://docs.arangodb.com/3.11/concepts/data-model/collections/#collection-names):
#
#   * first char: ASCII letter or underscore
#   * subsequent chars: ASCII letter, digit, underscore, or hyphen
#   * total length: 1..256 chars
#
# We re-validate on the client side so a malformed bundle is rejected
# at the route boundary rather than at AQL emit time, where the error
# would be far less actionable.
_COLLECTION_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,255}$")

# ArangoDB attribute names are far more permissive (any Unicode string
# except a few sentinel values); we only reject control characters and
# backticks so the AQL builder can safely emit ``doc.\`<field>\``` even
# for fields that contain dots or hyphens. 1..256 chars matches the
# server's practical limit.
_FIELD_NAME_RE = re.compile(r"^[^\x00-\x1f`]{1,256}$")


def is_valid_collection_name(name: object) -> bool:
    """Return ``True`` iff *name* is a non-empty string that matches
    ArangoDB's collection-name grammar.

    Non-string inputs return ``False`` (do not raise) so this can be
    used as a Pydantic ``@validator`` body without try/except gymnastics.
    """

    return isinstance(name, str) and bool(_COLLECTION_NAME_RE.match(name))


def is_valid_field_name(name: object) -> bool:
    """Return ``True`` iff *name* is a string we will safely quote into
    an AQL property accessor.

    The check is intentionally lenient: ArangoDB itself accepts any
    Unicode string as an attribute name. We reject only the cases that
    would let an attacker break out of an ```` `quoted` `` literal in
    emitted AQL.
    """

    return isinstance(name, str) and bool(_FIELD_NAME_RE.match(name))


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


# Entity styles understood by the SPARQL translator:
#
# * ``COLLECTION`` — one document collection per entity type (the
#   "vanilla PG" pattern in PRD §6.2 Table 6.2.A).
# * ``LABEL``      — single document collection discriminated by a type
#   field (the "labelled PG / LPG" pattern). Carries
#   ``typeField`` and ``typeValue``.
# * ``RPT``        — entity is stored as rows in a triples collection
#   (the RDF-triples-pattern; SPARQL-specific extension over the sister
#   project). Carries ``triplesCollection`` and the four column-name
#   overrides.
# * ``DOCUMENT``   — entity is embedded inside another document (e.g. a
#   ``Person.address`` sub-document). Reserved; not emitted in v0.x.
EntityStyle = Literal["COLLECTION", "LABEL", "RPT", "DOCUMENT"]

# Relationship styles understood by the translator:
#
# * ``DEDICATED_COLLECTION`` — one edge collection per relationship type
#   (vanilla PG).
# * ``GENERIC_WITH_TYPE``    — single edge collection discriminated by
#   a type field (LPG-style relationships).
# * ``RPT_EDGE``             — relationship is materialised as triples
#   in the entity's triples collection (RPT-specific).
RelationshipStyle = Literal["DEDICATED_COLLECTION", "GENERIC_WITH_TYPE", "RPT_EDGE"]

# How the mapping was obtained. Consumed by the UI's "schema source"
# badge and by the request-level guard that decides whether to refuse
# heuristic mappings (PRD §6.3.4 ``ARANGO_SPARQL_ALLOW_HEURISTIC``).
#
# * ``imported_csi`` — pushed in as a forward CSI v1 interchange document
#   produced by a schema-mapping tool (e.g. r2g). Trusted like
#   ``imported_owl`` / ``analyzer`` (deterministic, schema-derived), not
#   refused by the heuristic guard. See :mod:`arango_sparql.translate.csi`.
MappingSourceKind = Literal[
    "analyzer", "heuristic", "manual", "imported_owl", "imported_csi"
]


# ---------------------------------------------------------------------------
# MappingBundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MappingSource:
    """Provenance of a :class:`MappingBundle`.

    Carried alongside the bundle so the schema route can advertise *how*
    the mapping was obtained (the UI renders an "analyzer-backed" badge
    for ``analyzer`` vs. a warning for ``heuristic``).
    """

    kind: MappingSourceKind
    notes: str | None = None


@dataclass(frozen=True)
class MappingBundle:
    """Immutable wire-format mapping bundle.

    Two halves:

    * ``conceptual_schema`` — high-level entity and relationship types
      (analyzer's view of the customer's schema). Used by NL prompts
      and the UI's catalogue view. The translator does **not** read
      this; it is metadata for humans and LLMs.
    * ``physical_mapping``  — collection names, discriminator fields,
      RPT column overrides. **This** is what
      :class:`~arango_sparql.translate.resolver.SchemaResolver` reads
      when emitting AQL.

    Plus optional ``owl_turtle`` (an inline OWL ontology, when the
    customer has imported one via ``/mapping/import-owl``) and
    ``source`` (provenance).

    Python attributes use ``snake_case``; the JSON wire shape uses
    ``camelCase``. Use :func:`mapping_from_wire_dict` and
    :func:`mapping_to_wire_dict` to convert.
    """

    conceptual_schema: dict[str, Any] = field(default_factory=dict)
    physical_mapping: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    owl_turtle: str | None = None
    source: MappingSource | None = None

    def entities(self) -> dict[str, dict[str, Any]]:
        """Convenience accessor — return ``physical_mapping["entities"]``
        as a dict, or an empty dict when the bundle has no entity
        mappings yet.
        """

        out = self.physical_mapping.get("entities", {}) if self.physical_mapping else {}
        return out if isinstance(out, dict) else {}

    def relationships(self) -> dict[str, dict[str, Any]]:
        """Convenience accessor — return
        ``physical_mapping["relationships"]`` as a dict, or an empty
        dict when the bundle has no relationship mappings yet.
        """

        out = (
            self.physical_mapping.get("relationships", {})
            if self.physical_mapping
            else {}
        )
        return out if isinstance(out, dict) else {}


# ---------------------------------------------------------------------------
# Wire-shape normaliser
# ---------------------------------------------------------------------------


# Recognised top-level keys. Both spellings (camelCase from the
# analyzer / from the wire; snake_case from Python callers) collapse
# onto the canonical snake_case form before we hand the bundle to
# :class:`MappingBundle`.
_TOP_LEVEL_ALIASES: dict[str, str] = {
    "conceptualSchema": "conceptual_schema",
    "conceptual_schema": "conceptual_schema",
    "physicalMapping": "physical_mapping",
    "physical_mapping": "physical_mapping",
    "metadata": "metadata",
    "owlTurtle": "owl_turtle",
    "owl_turtle": "owl_turtle",
    "source": "source",
}

# Recognised entity- and relationship-spec field aliases. When the wire
# shape carries one spelling we normalise to the analyzer-canonical
# camelCase form before storing inside ``physical_mapping``. This way
# downstream consumers (resolver, route layer) only ever see camelCase
# field names, regardless of which spelling the caller used.
_SPEC_FIELD_ALIASES: dict[str, str] = {
    "collection_name": "collectionName",
    "edge_collection_name": "edgeCollectionName",
    "type_field": "typeField",
    "type_value": "typeValue",
    "triples_collection": "triplesCollection",
    "subject_column": "subjectColumn",
    "predicate_column": "predicateColumn",
    "object_uri_column": "objectUriColumn",
    "object_value_column": "objectValueColumn",
    "tenant_field": "tenantField",
    "tenant_entity": "tenantEntity",
    "from_entity": "fromEntity",
    "to_entity": "toEntity",
}

_VALID_SOURCE_KINDS = {
    "analyzer",
    "heuristic",
    "manual",
    "imported_owl",
    "imported_csi",
}


def _normalise_top_level(d: dict[str, Any]) -> dict[str, Any]:
    """Translate any known top-level alias to its canonical snake_case
    form. Unknown keys pass through (forward-compat with future
    analyzer additions). Raises :class:`MappingError` when both
    spellings of the same canonical key are present — that would be
    ambiguous.
    """

    out: dict[str, Any] = {}
    for k, v in d.items():
        canonical = _TOP_LEVEL_ALIASES.get(k, k)
        if canonical in out and canonical != k:
            raise MappingError(
                f"Mapping bundle has duplicate {canonical!r} key (both "
                f"camelCase and snake_case spellings present)"
            )
        if canonical in out:
            raise MappingError(
                f"Mapping bundle has duplicate {canonical!r} key"
            )
        out[canonical] = v
    return out


def _normalise_spec(spec: Any, *, label: str, where: str) -> dict[str, Any]:
    """Normalise a single entity- or relationship-spec dict.

    *label* and *where* are used both for diagnostic messages and to
    pick context-specific aliases. The sister project's LPG fixtures
    spell the edge-collection field as bare ``"collectionName"`` on a
    relationship spec (rather than ``"edgeCollectionName"``); since the
    field unambiguously means *edge* collection there, we normalise it
    to the canonical SPARQL spelling. Doing so keeps the wire-shape
    portable between the two projects without forcing the sister to
    rewrite its fixtures.
    """

    if not isinstance(spec, dict):
        raise MappingError(
            f"physicalMapping.{where}[{label!r}] must be a dict, got "
            f"{type(spec).__name__!r}"
        )
    is_relationship = where == "relationships"
    out: dict[str, Any] = {}
    for k, v in spec.items():
        if is_relationship and k in {"collectionName", "collection_name"}:
            canonical = "edgeCollectionName"
        else:
            # ``k`` arrives as ``Any`` (the spec dict is caller JSON);
            # pin it to ``str`` so the alias lookup types cleanly.
            canonical = _SPEC_FIELD_ALIASES.get(str(k), str(k))
        if canonical in out:
            raise MappingError(
                f"physicalMapping.{where}[{label!r}] has duplicate "
                f"{canonical!r} field (both spellings present)"
            )
        out[canonical] = v
    return out


def _normalise_physical_mapping(pm: Any) -> dict[str, Any]:
    if not isinstance(pm, dict):
        raise MappingError(
            f"physicalMapping must be a dict, got {type(pm).__name__!r}"
        )

    entities = pm.get("entities") or {}
    relationships = pm.get("relationships") or {}

    if not isinstance(entities, dict):
        raise MappingError(
            f"physicalMapping.entities must be a dict, got "
            f"{type(entities).__name__!r}"
        )
    if not isinstance(relationships, dict):
        raise MappingError(
            f"physicalMapping.relationships must be a dict, got "
            f"{type(relationships).__name__!r}"
        )

    out: dict[str, Any] = {
        "entities": {
            label: _normalise_spec(spec, label=label, where="entities")
            for label, spec in entities.items()
        },
        "relationships": {
            rtype: _normalise_spec(spec, label=rtype, where="relationships")
            for rtype, spec in relationships.items()
        },
    }
    # Preserve any forward-compat top-level fields (e.g. shardFamilies)
    # without renaming them — we do not know their internal shape, so
    # tampering would be unsafe.
    for k, v in pm.items():
        if k not in {"entities", "relationships"}:
            out.setdefault(k, v)
    return out


def _normalise_source(d: Any) -> MappingSource:
    if not isinstance(d, dict):
        raise MappingError(
            f"source must be a dict, got {type(d).__name__!r}"
        )
    kind = d.get("kind")
    if kind not in _VALID_SOURCE_KINDS:
        raise MappingError(
            f"source.kind must be one of "
            f"{sorted(_VALID_SOURCE_KINDS)!r}, got {kind!r}"
        )
    notes = d.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise MappingError(
            f"source.notes must be a string, got {type(notes).__name__!r}"
        )
    return MappingSource(kind=kind, notes=notes)


def mapping_from_wire_dict(d: dict[str, Any] | None) -> MappingBundle:
    """Build a :class:`MappingBundle` from the JSON wire shape.

    Accepts both the analyzer-native ``camelCase`` shape and
    ``snake_case`` aliases at the top level and at the entity-spec
    level. Returns an empty bundle when *d* is ``None`` — used by route
    handlers that treat "no mapping provided" as a soft default
    (heuristic acquisition kicks in downstream).

    Raises :class:`MappingError` when the structure is malformed
    (e.g. ``physicalMapping`` is not a dict, ``source.kind`` is not
    one of the recognised provenance values). Unknown top-level and
    spec-level keys are preserved verbatim so the bundle round-trips
    through future analyzer schema versions.
    """

    if d is None:
        return MappingBundle()
    if not isinstance(d, dict):
        raise MappingError(
            f"Mapping bundle must be a dict, got {type(d).__name__!r}"
        )

    top = _normalise_top_level(d)

    conceptual_raw = top.get("conceptual_schema") or {}
    if not isinstance(conceptual_raw, dict):
        raise MappingError(
            f"conceptualSchema must be a dict, got "
            f"{type(conceptual_raw).__name__!r}"
        )

    physical_raw = top.get("physical_mapping")
    physical: dict[str, Any]
    if physical_raw is None or physical_raw == {}:
        physical = {"entities": {}, "relationships": {}}
    else:
        physical = _normalise_physical_mapping(physical_raw)

    metadata = top.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise MappingError(
            f"metadata must be a dict, got {type(metadata).__name__!r}"
        )

    owl_turtle = top.get("owl_turtle")
    if owl_turtle is not None and not isinstance(owl_turtle, str):
        raise MappingError(
            f"owlTurtle must be a string, got {type(owl_turtle).__name__!r}"
        )

    source_raw = top.get("source")
    source = _normalise_source(source_raw) if source_raw is not None else None

    return MappingBundle(
        conceptual_schema=conceptual_raw,
        physical_mapping=physical,
        metadata=metadata,
        owl_turtle=owl_turtle,
        source=source,
    )


def mapping_to_wire_dict(bundle: MappingBundle) -> dict[str, Any]:
    """Inverse of :func:`mapping_from_wire_dict` — emit the canonical
    camelCase JSON wire shape from a :class:`MappingBundle`.

    Only emits the optional keys (``owlTurtle``, ``source``) when they
    are present, so the round-trip is exact for bundles that don't
    carry them.
    """

    out: dict[str, Any] = {
        "conceptualSchema": bundle.conceptual_schema,
        "physicalMapping": bundle.physical_mapping,
        "metadata": bundle.metadata,
    }
    if bundle.owl_turtle is not None:
        out["owlTurtle"] = bundle.owl_turtle
    if bundle.source is not None:
        src: dict[str, Any] = {"kind": bundle.source.kind}
        if bundle.source.notes is not None:
            src["notes"] = bundle.source.notes
        out["source"] = src
    return out


__all__ = [
    "EntityStyle",
    "MappingBundle",
    "MappingError",
    "MappingSource",
    "MappingSourceKind",
    "RelationshipStyle",
    "is_valid_collection_name",
    "is_valid_field_name",
    "mapping_from_wire_dict",
    "mapping_to_wire_dict",
]
