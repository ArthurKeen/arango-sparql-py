"""Tests for :mod:`arango_sparql.service.protocol.service_description`.

The Service Description Turtle is parsed back with rdflib and
asserted against the RDF graph rather than substring-checked, so a
Turtle indentation tweak doesn't break tests.
"""

from __future__ import annotations

from rdflib import Graph, Namespace, URIRef

from arango_sparql.service.protocol.service_description import (
    SD_NAMESPACE,
    render_service_description,
)
from arango_sparql.translate.mapping import MappingBundle

SD = Namespace(SD_NAMESPACE)
DCTERMS = Namespace("http://purl.org/dc/terms/")


def _parse(turtle: str) -> Graph:
    g = Graph()
    g.parse(data=turtle, format="turtle")
    return g


def test_renders_valid_turtle_without_a_bundle() -> None:
    """No bundle ⇒ default-graph-only Service Description (used
    when the schema layer hasn't acquired a mapping yet).
    """

    body = render_service_description(
        endpoint_url="http://localhost:8000/sparql",
    )
    g = _parse(body)
    services = list(g.subjects(predicate=SD.endpoint))
    assert len(services) == 1


def test_endpoint_iri_round_trips() -> None:
    body = render_service_description(
        endpoint_url="https://example.org/api/sparql",
    )
    g = _parse(body)
    endpoints = list(g.objects(predicate=SD.endpoint))
    assert URIRef("https://example.org/api/sparql") in endpoints


def test_supported_language_includes_sparql11_query() -> None:
    body = render_service_description(endpoint_url="http://localhost/sparql")
    g = _parse(body)
    langs = list(g.objects(predicate=SD.supportedLanguage))
    assert SD.SPARQL11Query in langs


def test_supported_language_does_not_include_update() -> None:
    """SPARQL Update is intentionally absent — the endpoint will
    reject Update bodies with 405. Advertising support would mislead
    spec-compliant clients into wasting a round trip.
    """

    body = render_service_description(endpoint_url="http://localhost/sparql")
    g = _parse(body)
    langs = list(g.objects(predicate=SD.supportedLanguage))
    assert SD.SPARQL11Update not in langs


def test_features_include_union_default_graph_and_dereferences() -> None:
    body = render_service_description(endpoint_url="http://localhost/sparql")
    g = _parse(body)
    features = set(g.objects(predicate=SD.feature))
    assert SD.UnionDefaultGraph in features
    assert SD.DereferencesURIs in features
    # Federation deliberately missing per PRD §5.3.
    assert SD.BasicFederatedQuery not in features


def test_result_formats_advertised_for_select_and_construct() -> None:
    body = render_service_description(endpoint_url="http://localhost/sparql")
    g = _parse(body)
    formats = {str(o) for o in g.objects(predicate=SD.resultFormat)}
    assert "http://www.w3.org/ns/formats/SPARQL_Results_JSON" in formats
    assert "http://www.w3.org/ns/formats/SPARQL_Results_XML" in formats
    assert "http://www.w3.org/ns/formats/SPARQL_Results_CSV" in formats
    assert "http://www.w3.org/ns/formats/SPARQL_Results_TSV" in formats
    assert "http://www.w3.org/ns/formats/Turtle" in formats
    assert "http://www.w3.org/ns/formats/N-Triples" in formats
    assert "http://www.w3.org/ns/formats/RDF_XML" in formats
    assert "http://www.w3.org/ns/formats/JSON-LD" in formats


def test_dataset_has_default_graph() -> None:
    body = render_service_description(endpoint_url="http://localhost/sparql")
    g = _parse(body)
    datasets = list(g.objects(predicate=SD.defaultDataset))
    assert len(datasets) == 1
    default_graphs = list(g.objects(subject=datasets[0], predicate=SD.defaultGraph))
    assert len(default_graphs) == 1


def test_named_graphs_sourced_from_bundle_entities_and_relationships() -> None:
    bundle = MappingBundle(
        physical_mapping={
            "entities": {
                "Person": {"style": "COLLECTION", "collectionName": "Person"},
                "Org": {"style": "COLLECTION", "collectionName": "Org"},
            },
            "relationships": {
                "knows": {
                    "style": "DEDICATED_COLLECTION",
                    "edgeCollectionName": "knows",
                    "fromEntity": "Person",
                    "toEntity": "Person",
                }
            },
        }
    )
    body = render_service_description(
        endpoint_url="http://localhost/sparql",
        bundle=bundle,
    )
    g = _parse(body)
    named_graph_iris = {str(o) for o in g.objects(predicate=SD.name)}
    assert "urn:arango-sparql:graph:Person" in named_graph_iris
    assert "urn:arango-sparql:graph:Org" in named_graph_iris
    assert "urn:arango-sparql:graph:knows" in named_graph_iris


def test_no_named_graphs_when_bundle_has_no_entities() -> None:
    body = render_service_description(
        endpoint_url="http://localhost/sparql",
        bundle=MappingBundle(),
    )
    g = _parse(body)
    named = list(g.objects(predicate=SD.name))
    assert named == []


def test_named_graphs_sorted_for_stable_etag() -> None:
    """Stable Turtle output is required for proxy etag caching."""

    bundle = MappingBundle(
        physical_mapping={
            "entities": {
                "B": {"collectionName": "B"},
                "A": {"collectionName": "A"},
                "C": {"collectionName": "C"},
            },
        }
    )
    body_1 = render_service_description(endpoint_url="http://localhost/sparql", bundle=bundle)
    body_2 = render_service_description(endpoint_url="http://localhost/sparql", bundle=bundle)
    assert body_1 == body_2
    # And the entries appear in lexicographic order in the source.
    assert body_1.find("graph:A") < body_1.find("graph:B") < body_1.find("graph:C")


def test_dcterms_description_links_to_prd() -> None:
    body = render_service_description(endpoint_url="http://localhost/sparql")
    g = _parse(body)
    descriptions = list(g.objects(predicate=DCTERMS.description))
    assert len(descriptions) == 1
    assert "PRD.md" in str(descriptions[0])


def test_rpt_triples_collection_appears_as_named_graph() -> None:
    bundle = MappingBundle(
        physical_mapping={
            "entities": {
                "Triples": {
                    "style": "RPT",
                    "triplesCollection": "rdf_triples",
                }
            }
        }
    )
    body = render_service_description(
        endpoint_url="http://localhost/sparql",
        bundle=bundle,
    )
    g = _parse(body)
    iris = {str(o) for o in g.objects(predicate=SD.name)}
    assert "urn:arango-sparql:graph:rdf_triples" in iris
