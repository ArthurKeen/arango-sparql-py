"""Unit tests for :meth:`SchemaResolver.attribute_uri_map` — the
reverse (attribute name → predicate IRI) index behind the
variable-predicate carve-out lift (PRD §6.6).

The golden coverage for the emission itself lives in
``variable_predicate.yml`` (``bare_spo_with_declared_datatype_properties``);
this module pins the map-construction rules the emitter relies on:
datatype-properties-only, deterministic collision handling, and the
empty-ontology fallback contract.
"""

from __future__ import annotations

from arango_sparql.translate.resolver import SchemaResolver

_OWL_PREFIX = "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"


def test_maps_datatype_properties_by_local_name() -> None:
    resolver = SchemaResolver.from_turtle(
        _OWL_PREFIX
        + "<http://ex.org/name> a owl:DatatypeProperty .\n"
        + "<http://ex.org/age> a owl:DatatypeProperty .\n"
    )
    assert resolver.attribute_uri_map() == {
        "name": "http://ex.org/name",
        "age": "http://ex.org/age",
    }


def test_object_properties_are_excluded() -> None:
    # Object properties live in edge collections, never in document
    # attributes, so they must not shadow a datatype property's slot
    # in the ATTRIBUTES() fan-out.
    resolver = SchemaResolver.from_turtle(
        _OWL_PREFIX
        + "<http://ex.org/knows> a owl:ObjectProperty .\n"
        + "<http://ex.org/name> a owl:DatatypeProperty .\n"
    )
    assert resolver.attribute_uri_map() == {"name": "http://ex.org/name"}


def test_empty_ontology_yields_empty_map() -> None:
    # The emitter treats an empty map as "mapping unavailable" and
    # falls back to the attribute-name carve-out — it must NOT get a
    # map that filters every row out.
    resolver = SchemaResolver.from_turtle("")
    assert resolver.attribute_uri_map() == {}


def test_local_name_collision_is_deterministic_and_warned() -> None:
    resolver = SchemaResolver.from_turtle(
        _OWL_PREFIX
        + "<http://b.example/name> a owl:DatatypeProperty .\n"
        + "<http://a.example/name> a owl:DatatypeProperty .\n"
    )
    # Lexically-smallest IRI wins, independent of declaration order.
    assert resolver.attribute_uri_map() == {"name": "http://a.example/name"}
    codes = [w["code"] for w in resolver.warnings]
    assert "W_SCHEMA_AMBIGUOUS_ATTRIBUTE" in codes


def test_map_is_cached() -> None:
    resolver = SchemaResolver.from_turtle(_OWL_PREFIX + "<http://ex.org/name> a owl:DatatypeProperty .\n")
    assert resolver.attribute_uri_map() is resolver.attribute_uri_map()
