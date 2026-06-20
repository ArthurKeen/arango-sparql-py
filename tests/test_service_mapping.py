"""Unit tests for :mod:`arango_sparql.service.mapping` — the request →
:class:`SchemaResolver` adapter, with a focus on the analyzer-bundle merge.

The merge lets a user query a class they declared inline (so the IRI
matches their SPARQL) while inheriting the physical mapping the
arango-schema-analyzer discovered for the connected database — by local
name. Inline annotations always win; the bundle only fills gaps. These
tests pin that contract without going through HTTP.
"""

from __future__ import annotations

from types import SimpleNamespace

from arango_sparql.service.mapping import _resolver_from_request
from arango_sparql.translate.mapping import MappingBundle, MappingSource

OWL_PREFIXES = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix ex: <http://example.org/> .
"""

PERSON_IRI = "http://example.org/Person"
KNOWS_IRI = "http://example.org/knows"


def _req(ontology_ttl: str | None = None, mapping: dict | None = None):
    """A minimal stand-in for the translate/execute request model."""
    return SimpleNamespace(ontology_ttl=ontology_ttl, mapping=mapping)


def _bundle(
    entities: dict | None = None, relationships: dict | None = None
) -> MappingBundle:
    return MappingBundle(
        conceptual_schema={"entities": [], "relationships": []},
        physical_mapping={
            "entities": entities or {},
            "relationships": relationships or {},
        },
        metadata={"warnings": []},
        source=MappingSource(kind="analyzer", notes="merge test"),
    )


# ---------------------------------------------------------------------------
# Baseline (no bundle) — unchanged behaviour
# ---------------------------------------------------------------------------


def test_without_bundle_unannotated_class_falls_back_with_warning() -> None:
    ttl = OWL_PREFIXES + "ex:Person a owl:Class .\n"
    resolver = _resolver_from_request(_req(ontology_ttl=ttl))
    resolved = resolver.resolve_class(PERSON_IRI)
    # Local-name fallback + the advisory the user was puzzled about.
    assert resolved.collection == "Person"
    assert any(
        w["code"] == "W_SCHEMA_DEFAULT_COLLECTION" for w in resolver.warnings
    )


# ---------------------------------------------------------------------------
# Merge fills gaps
# ---------------------------------------------------------------------------


def test_merge_fills_collection_name_no_warning() -> None:
    """A class declared inline without phys:collectionName inherits the
    analyzer-discovered collection name — and the fallback warning is gone.
    """
    ttl = OWL_PREFIXES + "ex:Person a owl:Class .\n"
    bundle = _bundle(
        entities={"Person": {"style": "COLLECTION", "collectionName": "people"}}
    )
    resolver = _resolver_from_request(_req(ontology_ttl=ttl), analyzer_bundle=bundle)
    resolved = resolver.resolve_class(PERSON_IRI)
    assert resolved.collection == "people"
    assert not any(
        w["code"] == "W_SCHEMA_DEFAULT_COLLECTION" for w in resolver.warnings
    )


def test_merge_fills_edge_collection_for_object_property() -> None:
    ttl = OWL_PREFIXES + "ex:knows a owl:ObjectProperty .\n"
    bundle = _bundle(
        relationships={
            "knows": {
                "style": "DEDICATED_COLLECTION",
                "edgeCollectionName": "knows_edges",
            }
        }
    )
    resolver = _resolver_from_request(_req(ontology_ttl=ttl), analyzer_bundle=bundle)
    resolved = resolver.resolve_property(KNOWS_IRI)
    assert resolved.edge_collection == "knows_edges"


def test_merge_fills_rpt_style_and_triples_collection() -> None:
    ttl = OWL_PREFIXES + "ex:Person a owl:Class .\n"
    bundle = _bundle(
        entities={
            "Person": {
                "style": "RPT",
                "triplesCollection": "_triples",
                "subjectColumn": "subject_uri",
            }
        }
    )
    resolver = _resolver_from_request(_req(ontology_ttl=ttl), analyzer_bundle=bundle)
    resolved = resolver.resolve_class(PERSON_IRI)
    assert resolved.style == "RPT"
    # RPT classes resolve their "collection" to the triples table.
    assert resolved.collection == "_triples"
    assert resolved.subject_column == "subject_uri"


# ---------------------------------------------------------------------------
# Inline wins
# ---------------------------------------------------------------------------


def test_inline_annotation_overrides_bundle() -> None:
    """When the inline ontology already declares phys:collectionName, the
    analyzer bundle must not clobber it."""
    ttl = (
        OWL_PREFIXES
        + 'ex:Person a owl:Class ; phys:collectionName "InlineWins" .\n'
    )
    bundle = _bundle(
        entities={"Person": {"style": "COLLECTION", "collectionName": "people"}}
    )
    resolver = _resolver_from_request(_req(ontology_ttl=ttl), analyzer_bundle=bundle)
    assert resolver.resolve_class(PERSON_IRI).collection == "InlineWins"


# ---------------------------------------------------------------------------
# Local-name match only — undeclared classes are not injected
# ---------------------------------------------------------------------------


def test_merge_only_enriches_declared_classes() -> None:
    """The merge enriches IRIs the user *declared* inline (so their query
    matches); a class present only in the bundle is not conjured into the
    graph under the user's namespace."""
    ttl = OWL_PREFIXES + "ex:Person a owl:Class .\n"
    bundle = _bundle(
        entities={
            "Person": {"style": "COLLECTION", "collectionName": "people"},
            "Org": {"style": "COLLECTION", "collectionName": "orgs"},
        }
    )
    resolver = _resolver_from_request(_req(ontology_ttl=ttl), analyzer_bundle=bundle)
    # Person (declared) resolves to the discovered collection.
    assert resolver.resolve_class(PERSON_IRI).collection == "people"
    # Org (bundle-only) is not declared owl:Class in the user's namespace.
    from arango_sparql.errors import SchemaResolutionError

    try:
        resolver.resolve_class("http://example.org/Org")
    except SchemaResolutionError:
        pass
    else:  # pragma: no cover - assertion shape
        raise AssertionError("bundle-only class must not resolve")


def test_no_inline_ontology_with_bundle_is_empty_graph() -> None:
    """No inline ontology → empty graph; the bundle has no inline IRIs to
    enrich, so resolution degrades exactly as before (open-world)."""
    bundle = _bundle(
        entities={"Person": {"style": "COLLECTION", "collectionName": "people"}}
    )
    resolver = _resolver_from_request(_req(), analyzer_bundle=bundle)
    # A property IRI degrades to local-name (unchanged baseline behaviour).
    assert resolver.resolve_property("http://example.org/name").attribute == "name"
