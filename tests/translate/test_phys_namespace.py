"""Regression tests for physical-IRI (``phys:``) namespace acceptance.

The analyzers (``arango-schema-analyzer``) emit their physical annotations
under ``http://arangodb.com/schema/physical#`` by default
(``DEFAULT_OWL_PHYSICAL_IRI``). The resolver must accept that spelling — for
a while it only accepted the older ``arango.solutions/phys#`` /
``arango-schema-mapper/phys#`` spellings, so real analyzer output resolved to
the local-name fallback instead of the annotated collection. This locks the
canonical spelling in and keeps the back-compat spellings working.
"""

from __future__ import annotations

import pytest
from rdflib import OWL, RDF, Graph, Literal, Namespace, URIRef

from arango_sparql.translate.resolver import SchemaResolver

BASE = Namespace("http://example.org/schema#")

# The spelling the analyzers actually emit, plus the historical spellings.
ANALYZER_PHYS = "http://arangodb.com/schema/physical#"
LEGACY_SOLUTIONS_PHYS = "https://arango.solutions/phys#"
LEGACY_MAPPER_PHYS = "https://arango-schema-mapper.example.org/phys#"


def _ontology(phys_iri: str) -> Graph:
    """A one-class ontology annotating ``:Person`` with ``phys:collectionName``."""
    phys = Namespace(phys_iri)
    g = Graph()
    g.add((BASE.Person, RDF.type, OWL.Class))
    g.add((BASE.Person, phys.collectionName, Literal("people")))
    return g


@pytest.mark.parametrize(
    "phys_iri",
    [ANALYZER_PHYS, LEGACY_SOLUTIONS_PHYS, LEGACY_MAPPER_PHYS],
)
def test_collection_name_resolves_for_every_phys_spelling(phys_iri: str) -> None:
    resolver = SchemaResolver(ontology=_ontology(phys_iri))
    resolved = resolver.resolve_class(URIRef(BASE.Person))
    assert resolved.collection == "people"


def test_analyzer_namespace_is_canonical_first_entry() -> None:
    from arango_sparql.translate.resolver import _PHYS_NAMESPACES, _SYNTHETIC_PHYS_NS

    assert str(_PHYS_NAMESPACES[0]) == ANALYZER_PHYS
    # The synthesizer aligns with what analyzers emit.
    assert str(_SYNTHETIC_PHYS_NS) == ANALYZER_PHYS
