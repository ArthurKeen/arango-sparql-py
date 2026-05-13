"""OWL/Turtle ↔ :class:`MappingBundle` round-trip.

Two helpers, kept symmetric so a UI's Import/Export button cycle is
information-preserving:

* :func:`turtle_to_mapping` parses an OWL/Turtle ontology produced by
  ``arangodb-schema-analyzer`` (or hand-authored against the same
  ``phys:*`` vocabulary documented in PRD §6.2) into a
  :class:`MappingBundle`. The original Turtle is preserved on the
  bundle's :attr:`MappingBundle.owl_turtle` slot so a downstream
  :func:`SchemaResolver.from_mapping_bundle` call routes through the
  faster inline-OWL path rather than the synthesizer.

* :func:`mapping_to_turtle` is the inverse — when the bundle already
  carries an inline ``owl_turtle`` it is returned verbatim (the
  analyzer's serialisation is the canonical form), otherwise the
  bundle's ``physical_mapping`` is round-tripped through
  :func:`_synthesize_graph_from_bundle` and serialised by ``rdflib``.

Both halves understand the OWL-bomb defences mandated by PRD §8.6
T7 — the route layer is the canonical enforcement site for the byte
ceiling (it gets to short-circuit before the parser ever runs), but
:func:`turtle_to_mapping` defends the post-parse triple cap so a
direct library call cannot bypass it. :class:`OwlBombError` is the
typed escape hatch the route layer translates to ``422
E_OWL_TOO_LARGE``.

Public surface:

* :func:`turtle_to_mapping` — Turtle string → :class:`MappingBundle`
* :func:`mapping_to_turtle` — :class:`MappingBundle` → Turtle string
* :func:`count_triples` — cheap pre-flight triple count for a
  parsed graph; surfaced so the route handler can include the value
  in its log line without re-walking the graph.
* :data:`DEFAULT_MAPPING_IMPORT_MAX_TRIPLES` — module-level default
  cap; the route layer reads :envvar:`MAPPING_IMPORT_MAX_TRIPLES` at
  request time so an operator can tune the limit without a code
  change.
* :class:`OwlBombError` — typed exception with ``code`` matching the
  PRD's stable error code (``E_OWL_TOO_LARGE``).
"""

from __future__ import annotations

import os
from typing import Any

from rdflib import OWL, RDF, RDFS, Graph, Literal, URIRef

from ..errors import SparqlError
from .mapping import (
    MappingBundle,
    MappingError,
    MappingSource,
    is_valid_collection_name,
    is_valid_field_name,
)
from .resolver import (
    _PHYS_NAMESPACES,
    _SYNTHETIC_PHYS_NS,
    _synthesize_graph_from_bundle,
    local_name,
)

__all__ = [
    "DEFAULT_MAPPING_IMPORT_MAX_TRIPLES",
    "MAPPING_IMPORT_MAX_TRIPLES_ENV",
    "OwlBombError",
    "OwlParseError",
    "count_triples",
    "mapping_to_turtle",
    "resolve_max_triples",
    "turtle_to_mapping",
]


# ---------------------------------------------------------------------------
# OWL-bomb defence — post-parse triple cap (PRD §8.6 T7)
# ---------------------------------------------------------------------------
#
# The byte cap (PRD A.2 ``MAPPING_IMPORT_MAX_BYTES``, default 2 MB) is
# enforced at the route boundary before this module ever sees the
# request. The triple cap is enforced *here* so a direct library call
# (e.g. from the OWL-import smoke test or from a future mapping CLI)
# cannot bypass it.

MAPPING_IMPORT_MAX_TRIPLES_ENV: str = "MAPPING_IMPORT_MAX_TRIPLES"
DEFAULT_MAPPING_IMPORT_MAX_TRIPLES: int = 200_000


def resolve_max_triples(override: int | None = None) -> int:
    """Return the active triple cap.

    Precedence: explicit *override* (route handler tests) →
    :envvar:`MAPPING_IMPORT_MAX_TRIPLES` → module default. Garbage
    env values fall through to the default rather than raising —
    a deployment YAML typo must not silently disable the cap (PRD
    §6.3.4 motif applied to OWL-bomb defence).
    """

    if override is not None:
        return max(1, int(override))
    raw = (os.getenv(MAPPING_IMPORT_MAX_TRIPLES_ENV) or "").strip()
    if not raw:
        return DEFAULT_MAPPING_IMPORT_MAX_TRIPLES
    try:
        parsed = int(raw)
        if parsed > 0:
            return parsed
    except ValueError:
        pass
    return DEFAULT_MAPPING_IMPORT_MAX_TRIPLES


class OwlBombError(SparqlError):
    """Raised when an imported OWL/Turtle document exceeds a configured
    safety bound.

    Carries ``code = "E_OWL_TOO_LARGE"`` so the route layer's existing
    ``{"error": ..., "code": ...}`` envelope picks it up automatically
    via :class:`SparqlError.code`.
    """

    code = "E_OWL_TOO_LARGE"


class OwlParseError(SparqlError):
    """Raised when ``rdflib`` cannot parse the supplied Turtle / OWL.

    Distinct code from :class:`OwlBombError` so a downstream UI can
    distinguish "your ontology is malformed" (``E_OWL_PARSE``) from
    "your ontology is too big" (``E_OWL_TOO_LARGE``).
    """

    code = "E_OWL_PARSE"


# ---------------------------------------------------------------------------
# Cheap helpers
# ---------------------------------------------------------------------------


def count_triples(graph: Graph) -> int:
    """Return the triple count of *graph*.

    Wraps ``len(graph)`` so callers get a stable name even if the
    underlying ``rdflib`` API ever changes (it's been stable for
    ten years, but the indirection is free).
    """

    return len(graph)


# ---------------------------------------------------------------------------
# Turtle → MappingBundle
# ---------------------------------------------------------------------------


# Reverse-lookup map: ``phys:*`` annotation local-name → bundle field
# spelling. Mirrors the analyzer's annotation vocabulary documented
# in PRD §6.2. Two ``phys:*`` namespaces are accepted on read (see
# :data:`resolver._PHYS_NAMESPACES`); both produce the same bundle
# field on import.
_PHYS_TO_ENTITY_FIELD: dict[str, str] = {
    "collectionName": "collectionName",
    "edgeCollectionName": "edgeCollectionName",
    "typeField": "typeField",
    "typeValue": "typeValue",
    "triplesCollection": "triplesCollection",
    "subjectColumn": "subjectColumn",
    "predicateColumn": "predicateColumn",
    "objectUriColumn": "objectUriColumn",
    "objectValueColumn": "objectValueColumn",
    "tenantField": "tenantField",
    "tenantEntity": "tenantEntity",
    "mappingStyle": "style",
}

_PHYS_TO_RELATIONSHIP_FIELD: dict[str, str] = {
    "edgeCollectionName": "edgeCollectionName",
    "typeField": "typeField",
    "typeValue": "typeValue",
    "triplesCollection": "triplesCollection",
    "tenantField": "tenantField",
    "tenantEntity": "tenantEntity",
    "mappingStyle": "style",
}


def turtle_to_mapping(
    turtle: str,
    *,
    max_triples: int | None = None,
    preserve_owl: bool = True,
    source_notes: str | None = None,
) -> MappingBundle:
    """Parse *turtle* and project it into a :class:`MappingBundle`.

    Steps:

    1. Hand the Turtle to ``rdflib.Graph.parse(format="turtle")``.
       Parse errors are wrapped in :class:`OwlParseError` with the
       PRD's stable ``E_OWL_PARSE`` code.
    2. Enforce the triple cap (PRD §8.6 T7). The default is
       :data:`DEFAULT_MAPPING_IMPORT_MAX_TRIPLES` (200 000); an
       explicit *max_triples* override is honoured by the route
       layer's tests but is otherwise read from the
       :envvar:`MAPPING_IMPORT_MAX_TRIPLES` env var.
    3. Walk every ``owl:Class`` / ``owl:ObjectProperty`` /
       ``owl:DatatypeProperty`` resource and harvest its ``phys:*``
       annotations into the analyzer-canonical
       ``physicalMapping.{entities, relationships}`` shape.
    4. Build a :class:`MappingBundle` with the original Turtle on
       :attr:`MappingBundle.owl_turtle` (if *preserve_owl* is true)
       so the resolver can re-use it without reserialisation, and a
       :class:`MappingSource` tagged ``imported_owl`` with the
       supplied *source_notes*.

    The conceptual half is left empty by design — the analyzer's
    OWL emission is the canonical conceptual schema, and we don't
    want to fabricate one. Downstream callers that need the
    conceptual block can derive it from the OWL or push it via
    a separate API.
    """

    if turtle is None or not isinstance(turtle, str):
        raise OwlParseError("turtle input must be a non-empty string")
    if not turtle.strip():
        # rdflib happily parses ``""`` into an empty graph; from the
        # library's perspective an empty input is a misuse — the
        # caller meant to supply Turtle and supplied nothing. The
        # route layer already enforces this at the body level
        # (``E_OWL_EMPTY_BODY``); raise here so a direct library
        # call surfaces the same shape.
        raise OwlParseError(
            "turtle input is empty; supply at least one prefix "
            "declaration or class statement"
        )

    graph = Graph()
    try:
        graph.parse(data=turtle, format="turtle")
    except Exception as exc:
        raise OwlParseError(f"failed to parse Turtle: {exc}") from exc

    cap = resolve_max_triples(max_triples)
    triples = count_triples(graph)
    if triples > cap:
        raise OwlBombError(
            f"OWL ontology exceeds the {MAPPING_IMPORT_MAX_TRIPLES_ENV} "
            f"cap ({triples} > {cap} triples). Lower the cap, split the "
            "ontology, or push it directly to the analyzer."
        )

    entities, entity_warnings = _entities_from_graph(graph)
    relationships, rel_warnings = _relationships_from_graph(graph)

    metadata: dict[str, Any] = {
        "source": "imported_owl",
        "tripleCount": triples,
    }
    warnings = entity_warnings + rel_warnings
    if warnings:
        metadata["warnings"] = warnings

    bundle = MappingBundle(
        conceptual_schema={"entities": [], "relationships": []},
        physical_mapping={
            "entities": entities,
            "relationships": relationships,
        },
        metadata=metadata,
        owl_turtle=turtle if preserve_owl else None,
        source=MappingSource(
            kind="imported_owl",
            notes=source_notes,
        ),
    )
    return bundle


def _entities_from_graph(
    graph: Graph,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Walk *graph* for ``owl:Class`` resources and harvest their
    ``phys:*`` annotations into the entity-spec wire shape.
    """

    entities: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    for cls_iri in sorted(set(graph.subjects(RDF.type, OWL.Class)), key=str):
        if not isinstance(cls_iri, URIRef):
            continue
        label = local_name(cls_iri)
        if not label:
            continue

        spec: dict[str, Any] = {}
        for phys_local, bundle_field in _PHYS_TO_ENTITY_FIELD.items():
            value = _physical_literal(graph, cls_iri, phys_local)
            if value is None:
                continue
            spec[bundle_field] = value

        # Default style stays "COLLECTION" if no explicit
        # phys:mappingStyle was attached — matches the resolver's
        # tolerance (PRD §6.2 second-paragraph "phys:collectionName
        # alone is enough" semantic).
        style = spec.get("style") or "COLLECTION"
        spec["style"] = style

        # Validate the headline collection name when one is given.
        # Defer the failure (warning, not raise) so a partially-mapped
        # OWL document still imports cleanly — the route layer can
        # decide whether to surface the warning prominently.
        col = spec.get("collectionName") or spec.get("triplesCollection")
        if col is not None and not is_valid_collection_name(col):
            warnings.append(
                {
                    "code": "W_SCHEMA_INVALID_COLLECTION",
                    "message": (
                        f"class {label!r} declares an invalid "
                        f"collectionName {col!r}; the bundle was kept "
                        "but a downstream resolve will fail."
                    ),
                    "iri": str(cls_iri),
                }
            )

        entities[label] = spec

    return entities, warnings


def _relationships_from_graph(
    graph: Graph,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Walk *graph* for ``owl:ObjectProperty`` resources and harvest
    them as relationship specs. ``owl:DatatypeProperty`` resources
    are skipped — they belong on the entity side, attached by the
    schema-mapper as per-class property annotations.
    """

    relationships: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    for prop_iri in sorted(
        set(graph.subjects(RDF.type, OWL.ObjectProperty)), key=str
    ):
        if not isinstance(prop_iri, URIRef):
            continue
        rtype = local_name(prop_iri)
        if not rtype:
            continue

        spec: dict[str, Any] = {}
        for phys_local, bundle_field in _PHYS_TO_RELATIONSHIP_FIELD.items():
            value = _physical_literal(graph, prop_iri, phys_local)
            if value is None:
                continue
            spec[bundle_field] = value

        # Pull rdfs:domain / rdfs:range to fromEntity / toEntity so
        # the planner has the endpoints without needing to follow
        # the ObjectProperty IRI back to its synthesized class.
        for rdfs_pred, bundle_field in (
            (RDFS.domain, "fromEntity"),
            (RDFS.range, "toEntity"),
        ):
            obj = next(graph.objects(prop_iri, rdfs_pred), None)
            if isinstance(obj, URIRef):
                spec[bundle_field] = local_name(obj)

        style = spec.get("style") or "DEDICATED_COLLECTION"
        spec["style"] = style

        edge = spec.get("edgeCollectionName") or spec.get("triplesCollection")
        if edge is not None and not is_valid_collection_name(edge):
            warnings.append(
                {
                    "code": "W_SCHEMA_INVALID_EDGE_COLLECTION",
                    "message": (
                        f"object property {rtype!r} declares an invalid "
                        f"edge collection name {edge!r}; the bundle was "
                        "kept but a downstream resolve will fail."
                    ),
                    "iri": str(prop_iri),
                }
            )

        # Optional sanity check on tenantField / typeField — these
        # are field names, not collection names, so use the field
        # validator. Same defer-rather-than-raise posture.
        for slot in ("tenantField", "typeField"):
            value = spec.get(slot)
            if value is not None and not is_valid_field_name(value):
                warnings.append(
                    {
                        "code": "W_SCHEMA_INVALID_FIELD",
                        "message": (
                            f"object property {rtype!r} declares an "
                            f"invalid {slot} {value!r}; the bundle was "
                            "kept but a downstream resolve will fail."
                        ),
                        "iri": str(prop_iri),
                    }
                )

        relationships[rtype] = spec

    return relationships, warnings


def _physical_literal(
    graph: Graph, subject: URIRef, predicate_local: str
) -> str | None:
    """Lookup a ``phys:<predicate_local>`` literal on *subject*.

    Tolerates both shipped ``phys:`` namespaces — :data:`_PHYS_NAMESPACES`
    is queried in order, returning the first hit so a hand-authored
    OWL using either spelling round-trips cleanly.
    """

    for ns in _PHYS_NAMESPACES:
        obj = next(graph.objects(subject, ns[predicate_local]), None)
        if isinstance(obj, Literal):
            text = str(obj)
            if text:
                return text
    return None


# ---------------------------------------------------------------------------
# MappingBundle → Turtle
# ---------------------------------------------------------------------------


def mapping_to_turtle(
    bundle: MappingBundle | None,
    *,
    rebind_prefixes: bool = True,
) -> str:
    """Serialise *bundle* as an OWL/Turtle string.

    Two paths:

    * If the bundle already carries :attr:`MappingBundle.owl_turtle`
      we return it verbatim. The analyzer's OWL serialisation is
      the canonical form; round-tripping it through rdflib would
      introduce syntactic drift (whitespace, prefix order) that a
      downstream UI's diff view would surface as spurious changes.

    * Otherwise we synthesise a graph via
      :func:`_synthesize_graph_from_bundle` (the same helper the
      resolver uses) and serialise it as Turtle. ``rdflib`` picks
      sensible default prefix bindings; we additionally bind
      ``phys:`` for the synthesizer's annotation namespace so the
      output is human-readable.

    *rebind_prefixes* is a tunable for tests that need a known
    serialisation; production code should leave it at the default.
    """

    if bundle is None:
        raise MappingError("cannot serialise a None bundle")

    if bundle.owl_turtle:
        return bundle.owl_turtle

    graph = _synthesize_graph_from_bundle(bundle)
    if rebind_prefixes:
        graph.bind("phys", _SYNTHETIC_PHYS_NS, replace=True)
        graph.bind("owl", OWL, replace=True)
        graph.bind("rdfs", RDFS, replace=True)
    return graph.serialize(format="turtle")
