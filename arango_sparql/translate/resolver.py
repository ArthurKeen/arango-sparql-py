"""URI → ArangoDB collection / property resolution.

Wraps an in-memory ``rdflib.Graph`` loaded from the OWL/Turtle ontology
that ``arango-schema-mapper`` produces. The mapper's emitter
(``references/arango-schema-mapper/schema_analyzer/owl_export.py``)
attaches three annotation properties under the ``phys:`` namespace:

- ``phys:collectionName "..."`` on every ``owl:Class`` → ArangoDB
  document collection name.
- ``phys:edgeCollectionName "..."`` on every ``owl:ObjectProperty`` →
  ArangoDB edge collection name.
- ``phys:typeField`` / ``phys:typeValue`` for hybrid (multi-class)
  collections — used to emit ``FILTER doc.<typeField> == <typeValue>``.

The resolver normalizes both spellings of the physical IRI seen in the
wild (``arango.solutions/phys#`` and the legacy
``arango-schema-mapper/phys#``) so callers do not have to care which
mapper version produced the ontology.

Visitors call into this resolver rather than touching the ontology
graph directly so the lookup surface stays narrow and cacheable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from rdflib import OWL, RDF, RDFS, Graph, Literal, Namespace, URIRef

from ..errors import SchemaResolutionError

if TYPE_CHECKING:
    from .mapping import MappingBundle

# Both physical-IRI spellings the mapper has shipped historically. We
# accept either so an ontology produced by an older mapper still
# resolves.
_PHYS_NAMESPACES = (
    Namespace("https://arango.solutions/phys#"),
    Namespace("https://arango-schema-mapper.example.org/phys#"),
)
_LOCAL_NAME_RE = re.compile(r"[#/]([^#/]+)$")

# Synthetic-IRI namespace used by :meth:`SchemaResolver.from_mapping_bundle`
# when a :class:`~arango_sparql.translate.mapping.MappingBundle` carries no
# inline OWL ontology. The choice of ``urn:`` rather than a resolvable
# ``https:`` IRI is deliberate — the synthesized concepts are not meant to
# be dereferenced, only to give the resolver a stable IRI per entity/
# relationship label.
_SYNTHETIC_CONCEPT_NS = Namespace("urn:arango-sparql:concept#")
# Canonical ``phys:*`` namespace used by the synthesizer. Matches the
# first entry in :data:`_PHYS_NAMESPACES` so the resolver's lookup logic
# (which accepts either spelling) finds the annotations on the first
# probe.
_SYNTHETIC_PHYS_NS = _PHYS_NAMESPACES[0]


def local_name(iri: URIRef | str) -> str:
    """Return the local part of an IRI (after the last ``#`` or ``/``).

    Matches the behavior of the legacy
    ``references/arango-sparql/src/lib/uri-resolver.js`` ``extractPropertyName``
    so unmapped property IRIs degrade to the same physical attribute name.
    """
    text = str(iri)
    match = _LOCAL_NAME_RE.search(text)
    if match:
        return match.group(1)
    return text


@dataclass
class ResolvedClass:
    """An OWL class resolved to its ArangoDB physical collection."""

    iri: str
    collection: str
    type_field: str | None = None
    type_value: str | None = None


@dataclass
class ResolvedProperty:
    """An OWL property resolved to its ArangoDB physical attribute or
    edge collection (depending on whether it is a datatype or object
    property)."""

    iri: str
    attribute: str
    is_object_property: bool = False
    edge_collection: str | None = None
    domain_iri: str | None = None
    range_iri: str | None = None


@dataclass
class SchemaResolver:
    """Resolve SPARQL IRIs against the loaded OWL ontology.

    The ontology is treated as immutable after load; if the schema can
    change at runtime, build a new ``SchemaResolver`` and swap atomically
    rather than mutating this one.
    """

    ontology: Graph
    default_collection: str = "Document"
    _class_cache: dict[str, ResolvedClass] = field(default_factory=dict)
    _property_cache: dict[str, ResolvedProperty] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    """Schema-mapping advisories accumulated during resolution.

    Each entry is a ``{"code", "message", ...}`` dict. The
    ``W_SCHEMA_*`` code prefix marks the entry as a schema-mapping
    warning so :class:`arango_sparql.api.TranslateResult` can split it
    out into its own ``schema_warnings`` projection for the UI to
    render in a dedicated sidebar.

    De-duplicated by ``(code, IRI)`` so a query that references the same
    unmapped predicate ten times emits one advisory rather than ten.
    """
    _warned_keys: set[tuple[str, str]] = field(default_factory=set)

    @classmethod
    def from_turtle(cls, ttl: str, *, default_collection: str = "Document") -> SchemaResolver:
        """Convenience constructor — parse *ttl* into a fresh ``rdflib.Graph``."""
        graph = Graph()
        if ttl:
            graph.parse(data=ttl, format="turtle")
        return cls(ontology=graph, default_collection=default_collection)

    @classmethod
    def from_mapping_bundle(
        cls,
        bundle: MappingBundle,
        *,
        default_collection: str = "Document",
    ) -> SchemaResolver:
        """Build a resolver from a :class:`~arango_sparql.translate.mapping.MappingBundle`.

        Two paths, picked at runtime:

        1. **Inline OWL** — when ``bundle.owl_turtle`` is set (the typical
           analyzer output, see PRD §6.3.1), we parse it directly. The
           analyzer is responsible for embedding ``phys:*`` annotations on
           every ``owl:Class`` and ``owl:ObjectProperty`` so the resolver
           can dereference them without further work.

        2. **Synthetic** — when no inline OWL is present (heuristic or
           hand-authored mappings; see PRD §6.3.2), we synthesize a
           minimal ``rdflib.Graph`` from ``bundle.physical_mapping``.
           Each entity becomes one ``owl:Class`` with a
           ``urn:arango-sparql:concept#<Label>`` IRI; each relationship
           becomes one ``owl:ObjectProperty``. Physical annotations
           (``collectionName``, ``edgeCollectionName``, ``typeField`` /
           ``typeValue``, RPT column overrides) are attached as
           ``phys:*`` literals so the existing ``resolve_class`` /
           ``resolve_property`` paths work unchanged.

        Callers that need IRIs in their own namespace (e.g. SPARQL
        queries using ``http://customer.example/onto#``) should supply
        an OWL ontology via ``bundle.owl_turtle`` rather than rely on
        the synthetic ``urn:`` namespace.
        """

        if bundle.owl_turtle:
            return cls.from_turtle(
                bundle.owl_turtle, default_collection=default_collection
            )
        graph = _synthesize_graph_from_bundle(bundle)
        return cls(ontology=graph, default_collection=default_collection)

    # ------------------------------------------------------------------
    # Class resolution
    # ------------------------------------------------------------------
    def resolve_class(self, iri: URIRef | str) -> ResolvedClass:
        key = str(iri)
        cached = self._class_cache.get(key)
        if cached is not None:
            return cached
        ref = URIRef(key)
        if (ref, RDF.type, OWL.Class) not in self.ontology:
            raise SchemaResolutionError(f"class IRI {key!r} is not declared owl:Class in the ontology")
        physical = self._physical_string(ref, "collectionName")
        if physical is None:
            # Class is declared in the ontology but the mapper did not
            # attach a ``phys:collectionName`` annotation. We degrade to
            # the IRI's local name (matching the legacy translator) but
            # surface a schema-warning so the operator can fix the
            # mapping rather than chase a phantom collection name later.
            collection = local_name(ref)
            self._warn_schema(
                code="W_SCHEMA_DEFAULT_COLLECTION",
                message=(
                    f"class {key!r} has no phys:collectionName annotation; "
                    f"falling back to local-name collection {collection!r}"
                ),
                iri=key,
                class_iri=key,
                default_collection=collection,
            )
        else:
            collection = physical
        type_field = self._physical_string(ref, "typeField")
        type_value = self._physical_string(ref, "typeValue")
        resolved = ResolvedClass(iri=key, collection=collection, type_field=type_field, type_value=type_value)
        self._class_cache[key] = resolved
        return resolved

    # ------------------------------------------------------------------
    # Property resolution
    # ------------------------------------------------------------------
    def resolve_property(self, iri: URIRef | str) -> ResolvedProperty:
        key = str(iri)
        cached = self._property_cache.get(key)
        if cached is not None:
            return cached
        ref = URIRef(key)
        is_object = (ref, RDF.type, OWL.ObjectProperty) in self.ontology
        is_datatype = (ref, RDF.type, OWL.DatatypeProperty) in self.ontology
        if not (is_object or is_datatype):
            # Unmapped property — degrade to local-name attribute access.
            # This matches the legacy translator's behavior for any
            # predicate IRI not present in the ontology and keeps simple
            # SPARQL queries working against a freshly-mapped schema
            # before a full ontology has been authored. Surface a
            # schema-warning so the operator (and the UI's schema-
            # warnings sidebar) can see the silently-degraded resolution.
            fallback_attribute = local_name(ref)
            self._warn_schema(
                code="W_SCHEMA_UNMAPPED_IRI",
                message=(
                    f"property IRI {key!r} is not declared in the ontology; "
                    f"falling back to local-name attribute {fallback_attribute!r}"
                ),
                iri=key,
                fallback=fallback_attribute,
            )
            resolved = ResolvedProperty(iri=key, attribute=fallback_attribute)
            self._property_cache[key] = resolved
            return resolved

        edge_collection = self._physical_string(ref, "edgeCollectionName") if is_object else None
        domain_iri = self._first_object(ref, RDFS.domain)
        range_iri = self._first_object(ref, RDFS.range)
        attribute = local_name(ref)
        resolved = ResolvedProperty(
            iri=key,
            attribute=attribute,
            is_object_property=is_object,
            edge_collection=edge_collection,
            domain_iri=domain_iri,
            range_iri=range_iri,
        )
        self._property_cache[key] = resolved
        return resolved

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _warn_schema(self, *, code: str, message: str, iri: str, **extra: Any) -> None:
        """Append a schema-mapping advisory, deduplicated by ``(code, iri)``.

        Visitors call into the resolver once per triple, so a query like
        ``?a :unknown ?b . ?c :unknown ?d`` would otherwise produce two
        identical warnings. The ``_warned_keys`` guard collapses them
        into one without losing information — operators see the
        unmapped IRI exactly once per translate.
        """
        key = (code, iri)
        if key in self._warned_keys:
            return
        self._warned_keys.add(key)
        self.warnings.append({"code": code, "message": message, "iri": iri, **extra})

    def _physical_string(self, subject: URIRef, predicate_local: str) -> str | None:
        """Return the literal value of ``phys:<predicate_local>`` on
        ``subject``, regardless of which physical-IRI spelling the
        mapper used."""
        for ns in _PHYS_NAMESPACES:
            value = self.ontology.value(subject=subject, predicate=ns[predicate_local])
            if value is not None:
                return str(value)
        return None

    def _first_object(self, subject: URIRef, predicate: URIRef) -> str | None:
        value = self.ontology.value(subject=subject, predicate=predicate)
        return str(value) if value is not None else None


# ---------------------------------------------------------------------------
# MappingBundle → synthetic rdflib.Graph
# ---------------------------------------------------------------------------


# Physical-annotation predicates we project from MappingBundle entity- and
# relationship-spec dicts into the synthesized graph. The mapping is
# intentionally explicit (not a generic `for k, v in spec.items()`) so a
# future bundle field with the same camelCase shape but a different
# semantic does not accidentally leak into the ontology. Add a row here
# when extending the bundle wire shape.
_BUNDLE_ENTITY_ANNOTATIONS: tuple[tuple[str, str], ...] = (
    ("typeField", "typeField"),
    ("typeValue", "typeValue"),
    ("triplesCollection", "triplesCollection"),
    ("subjectColumn", "subjectColumn"),
    ("predicateColumn", "predicateColumn"),
    ("objectUriColumn", "objectUriColumn"),
    ("objectValueColumn", "objectValueColumn"),
    ("tenantField", "tenantField"),
)

_BUNDLE_RELATIONSHIP_ANNOTATIONS: tuple[tuple[str, str], ...] = (
    ("typeField", "typeField"),
    ("typeValue", "typeValue"),
    ("triplesCollection", "triplesCollection"),
)


def _synthetic_iri(label: str) -> URIRef:
    """Return a stable ``urn:arango-sparql:concept#<Label>`` IRI.

    Percent-encodes characters that are not valid in an IRI fragment so
    labels with spaces, slashes, or other punctuation (rare but possible
    when the analyzer surfaces a customer's literal label) still produce
    a round-trippable IRI.
    """

    return URIRef(str(_SYNTHETIC_CONCEPT_NS) + quote(label, safe=""))


def _synthesize_graph_from_bundle(bundle: MappingBundle) -> Graph:
    """Build a minimal rdflib graph carrying the bundle's physical
    mapping as ``phys:*`` annotations on synthesized ``owl:Class`` and
    ``owl:ObjectProperty`` resources.

    The output is *not* a faithful OWL ontology — it carries no
    ``rdfs:label`` strings and no domain/range axioms beyond what the
    bundle itself declares. Its sole purpose is to give the resolver a
    graph it can read using its existing lookup paths.
    """

    g = Graph()
    for label, spec in bundle.entities().items():
        if not isinstance(label, str) or not label:
            continue
        iri = _synthetic_iri(label)
        g.add((iri, RDF.type, OWL.Class))

        style = str(spec.get("style") or "COLLECTION")
        # For RPT entities the resolver-visible "collection" is the
        # triples table itself — that is where the engine reads rows.
        collection = spec.get("collectionName")
        if style == "RPT" and not collection:
            collection = spec.get("triplesCollection") or "_triples"
        if collection:
            g.add(
                (iri, _SYNTHETIC_PHYS_NS["collectionName"], Literal(str(collection)))
            )
        g.add((iri, _SYNTHETIC_PHYS_NS["mappingStyle"], Literal(style)))

        for src_key, phys_local in _BUNDLE_ENTITY_ANNOTATIONS:
            value = spec.get(src_key)
            if value is None:
                continue
            g.add((iri, _SYNTHETIC_PHYS_NS[phys_local], Literal(str(value))))

    for rtype, spec in bundle.relationships().items():
        if not isinstance(rtype, str) or not rtype:
            continue
        iri = _synthetic_iri(rtype)
        g.add((iri, RDF.type, OWL.ObjectProperty))

        edge_collection = spec.get("edgeCollectionName")
        style = str(spec.get("style") or "DEDICATED_COLLECTION")
        # RPT_EDGE relationships ride the entity's triples table; if no
        # explicit edge collection is provided, fall back to the
        # bundle's triples collection (if any).
        if style == "RPT_EDGE" and not edge_collection:
            edge_collection = spec.get("triplesCollection") or "_triples"
        if edge_collection:
            g.add(
                (
                    iri,
                    _SYNTHETIC_PHYS_NS["edgeCollectionName"],
                    Literal(str(edge_collection)),
                )
            )
        g.add((iri, _SYNTHETIC_PHYS_NS["mappingStyle"], Literal(style)))

        from_entity = spec.get("fromEntity")
        to_entity = spec.get("toEntity")
        if isinstance(from_entity, str) and from_entity:
            g.add((iri, RDFS.domain, _synthetic_iri(from_entity)))
        if isinstance(to_entity, str) and to_entity:
            g.add((iri, RDFS.range, _synthetic_iri(to_entity)))

        for src_key, phys_local in _BUNDLE_RELATIONSHIP_ANNOTATIONS:
            value = spec.get(src_key)
            if value is None:
                continue
            g.add((iri, _SYNTHETIC_PHYS_NS[phys_local], Literal(str(value))))

    return g
