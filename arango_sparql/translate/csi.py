"""CSI v1 (Conceptual Schema Interchange) → :class:`MappingBundle` adapter.

``CSI v1`` is the cross-tool interchange for a conceptual model plus its
ArangoDB physical mapping. A *forward* producer (e.g. r2g) emits it from a
relational→graph migration config; the *reverse* producer is
``arango-schema-analyzer``. Both write the same three-block contract:
``{conceptualModel, arangoPhysicalMapping, provenance}``.

This adapter lets the SPARQL→AQL translator consume a CSI document directly:
it maps the CSI physical block onto the bundle's ``physicalMapping`` (which is
already almost the same shape — collection/edge names + optional type
discriminators) and enriches each relationship spec with the ``fromEntity`` /
``toEntity`` endpoints declared in the CSI conceptual model. The CSI conceptual
model becomes the bundle's ``conceptualSchema`` (the human/LLM view), and the
CSI provenance is preserved verbatim under ``metadata["csiProvenance"]``.

See contextual-data-fabric ADR-0001 / M5 implementation-plan WP-A3, and the
forward producer in r2g (``src/r2g/csi.py``).
"""

from __future__ import annotations

from typing import Any

from .mapping import MappingBundle, MappingError, mapping_from_wire_dict

# The source.kind we stamp on bundles imported from a CSI document — trusted
# like ``imported_owl`` (deterministic, schema-derived), never refused by the
# heuristic guard.
CSI_SOURCE_KIND = "imported_csi"

# Relationship-spec fields we lift from the CSI *conceptual* model onto the
# *physical* relationship spec so the translator knows each edge's endpoints.
_ENDPOINT_FIELDS = ("fromEntity", "toEntity")


def _require_dict(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MappingError(f"CSI {what} must be an object, got {type(value).__name__!r}")
    return value


def mapping_bundle_from_csi(csi: dict[str, Any] | None) -> MappingBundle:
    """Build a :class:`MappingBundle` from a ``CSI v1`` document.

    Args:
        csi: a parsed ``CSI v1`` document (``{csiVersion, conceptualModel,
            arangoPhysicalMapping, provenance}``). ``None`` yields an empty
            bundle (mirrors :func:`mapping_from_wire_dict`).

    Returns:
        A :class:`MappingBundle` whose ``physicalMapping`` drives the AQL
        translator, ``conceptualSchema`` carries the CSI conceptual model, and
        ``source.kind == "imported_csi"``.

    Raises:
        MappingError: when the document is not a recognisable ``CSI v1`` shape.
    """
    if csi is None:
        return MappingBundle()
    csi = _require_dict(csi, "document")

    version = csi.get("csiVersion")
    if version != "1":
        raise MappingError(f"unsupported csiVersion {version!r} (expected '1')")

    conceptual = _require_dict(csi.get("conceptualModel") or {}, "conceptualModel")
    physical = _require_dict(
        csi.get("arangoPhysicalMapping") or {}, "arangoPhysicalMapping"
    )

    csi_entities = _require_dict(
        physical.get("entities") or {}, "arangoPhysicalMapping.entities"
    )
    csi_relationships = _require_dict(
        physical.get("relationships") or {}, "arangoPhysicalMapping.relationships"
    )

    # Endpoint lookup from the conceptual model: {relationshipType: {from,to}}.
    endpoints: dict[str, dict[str, Any]] = {}
    for rel in conceptual.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        rtype = rel.get("type")
        if rtype is None:
            continue
        # CSI conceptual endpoints (``fromEntity``/``toEntity``) share their
        # spelling with the bundle's canonical relationship-spec fields, so
        # they copy across by name.
        endpoints[rtype] = {
            field_name: rel[field_name]
            for field_name in _ENDPOINT_FIELDS
            if rel.get(field_name) is not None
        }

    # Physical entities pass through verbatim — the CSI physical entity spec
    # (style/collectionName/typeField/typeValue) is already bundle-shaped.
    entities_out: dict[str, Any] = {
        name: _require_dict(spec, f"arangoPhysicalMapping.entities[{name!r}]").copy()
        for name, spec in csi_entities.items()
    }

    # Physical relationships pass through + get endpoints grafted on.
    relationships_out: dict[str, Any] = {}
    for rtype, spec in csi_relationships.items():
        merged = _require_dict(
            spec, f"arangoPhysicalMapping.relationships[{rtype!r}]"
        ).copy()
        for field_name, value in endpoints.get(rtype, {}).items():
            merged.setdefault(field_name, value)
        relationships_out[rtype] = merged

    provenance = csi.get("provenance")
    producer = None
    if isinstance(provenance, dict):
        producer = provenance.get("producer")
    notes = f"forward CSI v1 from {producer}" if producer else "imported CSI v1"

    wire: dict[str, Any] = {
        "conceptualSchema": conceptual,
        "physicalMapping": {
            "entities": entities_out,
            "relationships": relationships_out,
        },
        "metadata": {"csiProvenance": provenance} if provenance is not None else {},
        "source": {"kind": CSI_SOURCE_KIND, "notes": notes},
    }
    # Round-trip through the canonical normaliser so spec aliases, duplicate
    # detection and source-kind validation apply uniformly.
    return mapping_from_wire_dict(wire)
