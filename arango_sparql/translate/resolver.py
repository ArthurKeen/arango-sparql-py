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

from rdflib import OWL, RDF, RDFS, Graph, Namespace, URIRef

from ..errors import SchemaResolutionError

if TYPE_CHECKING:
    pass

# Both physical-IRI spellings the mapper has shipped historically. We
# accept either so an ontology produced by an older mapper still
# resolves.
_PHYS_NAMESPACES = (
    Namespace("https://arango.solutions/phys#"),
    Namespace("https://arango-schema-mapper.example.org/phys#"),
)
_LOCAL_NAME_RE = re.compile(r"[#/]([^#/]+)$")


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
