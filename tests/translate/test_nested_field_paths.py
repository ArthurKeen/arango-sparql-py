"""A mapping says whether a dotted name is one attribute or a nested path (issue #72).

``address.city`` can mean the attribute ``city`` inside the object
``address``, or one attribute whose name is exactly ``address.city``. Until
#72 a mapping had one string and could not say which; 0.4.1 read every name
literally, so nested values were unreachable. Now ``field`` (and
``phys:attributeName``) stay literal, and ``path`` (``phys:attributePath``)
names a nested value one segment per level. The AQL shapes are pinned in
``nested_paths.yml``; this file covers how mappings are read and refused.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.errors import SchemaResolutionError, UnsupportedSparqlError
from arango_sparql.translate.builder import AqlQueryBuilder, attribute_path_ref
from arango_sparql.translate.csi import mapping_bundle_from_csi
from arango_sparql.translate.mapping import MappingError, mapping_from_wire_dict, property_field_path
from arango_sparql.translate.resolver import SchemaResolver

_CONCEPT = "urn:arango-sparql:concept#"
_PREFIX = f"PREFIX : <{_CONCEPT}>\n"


def _csi(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "csiVersion": "1",
        "conceptualModel": {"entities": [{"name": "Person"}], "relationships": []},
        "arangoPhysicalMapping": {
            "entities": {
                "Person": {"style": "COLLECTION", "collectionName": "people", "properties": properties}
            },
            "relationships": {},
        },
    }


def _resolver(properties: dict[str, Any]) -> SchemaResolver:
    return SchemaResolver.from_mapping_bundle(mapping_bundle_from_csi(_csi(properties)))


# ---------------------------------------------------------------------------
# property_field_path — the one reader of a property spec
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ({"field": "address.city"}, ("address.city",)),
        ({"path": ["address", "city"]}, ("address", "city")),
        ({"path": ["meta", "x.y"]}, ("meta", "x.y")),
        ({"path": ["address"]}, ("address",)),
        ({}, None),
        ({"field": ""}, None),
        ("not a dict", None),
    ],
)
def test_property_field_path_reads_each_form(spec: object, expected: tuple[str, ...] | None) -> None:
    assert property_field_path("city", spec) == expected


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"field": "address.city", "path": ["address", "city"]}, "both 'field' and 'path'"),
        ({"path": []}, "non-empty list"),
        ({"path": "address.city"}, "non-empty list"),
        ({"path": ["address", ""]}, "not a valid field name"),
        ({"path": ["address", "ci`ty"]}, "not a valid field name"),
        ({"path": ["address", 7]}, "not a valid field name"),
    ],
)
def test_property_field_path_refuses_a_malformed_spec(spec: dict[str, Any], message: str) -> None:
    with pytest.raises(MappingError, match=message):
        property_field_path("city", spec)


def test_wire_bundle_with_both_forms_is_refused_at_the_boundary() -> None:
    wire = {
        "physicalMapping": {
            "entities": {
                "Person": {
                    "collectionName": "people",
                    "properties": {"city": {"field": "address.city", "path": ["address", "city"]}},
                }
            },
            "relationships": {},
        }
    }
    with pytest.raises(MappingError, match="both 'field' and 'path'"):
        mapping_from_wire_dict(wire)


def test_csi_with_a_malformed_path_is_refused_on_import() -> None:
    with pytest.raises(MappingError, match="non-empty list"):
        mapping_bundle_from_csi(_csi({"city": {"path": []}}))


# ---------------------------------------------------------------------------
# Resolver — CSI ``path`` and OWL ``phys:attributePath``
# ---------------------------------------------------------------------------


def test_csi_path_resolves_to_a_nested_property() -> None:
    prop = _resolver({"city": {"path": ["address", "city"]}}).resolve_property(_CONCEPT + "city")
    assert prop.nested_path == ("address", "city")
    assert prop.field_path == ("address", "city")
    assert prop.attribute == "address"


def test_csi_field_with_a_dot_stays_one_literal_attribute() -> None:
    prop = _resolver({"city": {"field": "address.city"}}).resolve_property(_CONCEPT + "city")
    assert prop.nested_path is None
    assert prop.field_path == ("address.city",)


def test_one_segment_path_reads_like_a_field() -> None:
    by_path = _resolver({"home": {"path": ["address"]}}).resolve_property(_CONCEPT + "home")
    by_field = _resolver({"home": {"field": "address"}}).resolve_property(_CONCEPT + "home")
    assert by_path.field_path == by_field.field_path == ("address",)
    assert by_path.nested_path is None


_TTL_HEAD = """
@prefix :     <http://ex.org/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:collectionName "people" .
"""


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        ("phys:attributePath 'address.city'", "malformed phys:attributePath"),
        ("phys:attributePath '[]'", "malformed phys:attributePath"),
        ("phys:attributePath '[\"address\", 3]'", "malformed phys:attributePath"),
        (
            'phys:attributePath \'["address", "city"]\' ; phys:attributeName "address.city"',
            "both phys:attributeName and phys:attributePath",
        ),
    ],
)
def test_malformed_owl_attribute_path_is_refused_not_ignored(annotation: str, message: str) -> None:
    # Falling back to the local name would read ``doc.city``, a field that
    # does not exist, and answer empty without a word.
    resolver = SchemaResolver.from_turtle(_TTL_HEAD + f":city a owl:DatatypeProperty ; {annotation} .\n")
    with pytest.raises(SchemaResolutionError, match=message):
        resolver.resolve_property("http://ex.org/city")


def test_nested_properties_lists_only_nested_ones() -> None:
    resolver = _resolver(
        {
            "city": {"path": ["address", "city"]},
            "dotted": {"field": "address.city"},
            "name": {"field": "name"},
        }
    )
    assert resolver.nested_properties() == (_CONCEPT + "city",)
    assert _resolver({"name": {"field": "name"}}).nested_properties() == ()


def test_attribute_uri_map_leaves_out_nested_properties() -> None:
    # Keyed by its top-level attribute ``address``, a nested property would
    # bind ?p to city's IRI and ?o to the whole address object.
    uri_map = _resolver(
        {"city": {"path": ["address", "city"]}, "name": {"field": "name"}}
    ).attribute_uri_map()
    assert uri_map == {"name": _CONCEPT + "name"}


# ---------------------------------------------------------------------------
# Translation — attribute scans refuse rather than drop nested values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "what"),
    [
        (
            "SELECT ?k ?v WHERE { ?p a :Person ; ?k ?v }",
            "a variable predicate (?s ?p ?o) is not yet supported",
        ),
        ("SELECT ?v WHERE { ?p a :Person ; !:name ?v }", "a negated property path is not yet supported"),
    ],
)
def test_attribute_scan_is_refused_while_a_property_is_nested(query: str, what: str) -> None:
    resolver = _resolver({"city": {"path": ["address", "city"]}, "name": {"field": "name"}})
    with pytest.raises(UnsupportedSparqlError, match=re.escape(what)) as excinfo:
        translate(_PREFIX + query, resolver=resolver)
    assert _CONCEPT + "city" in str(excinfo.value)


@pytest.mark.parametrize(
    "query",
    ["SELECT ?k ?v WHERE { ?p a :Person ; ?k ?v }", "SELECT ?v WHERE { ?p a :Person ; !:name ?v }"],
)
def test_attribute_scan_still_works_with_only_literal_dotted_names(query: str) -> None:
    resolver = _resolver({"dotted": {"field": "address.city"}, "name": {"field": "name"}})
    assert "ATTRIBUTES(" in translate(_PREFIX + query, resolver=resolver).aql


def test_nested_property_translates_end_to_end_from_csi() -> None:
    result = translate(
        _PREFIX + "SELECT ?c WHERE { ?p a :Person ; :city ?c }",
        resolver=_resolver({"city": {"path": ["address", "city"]}}),
    )
    assert 'IS_OBJECT(doc1.address) && HAS(doc1.address, "city")' in result.aql
    assert "c: doc1.address.city" in result.aql


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def test_attribute_path_ref_quotes_each_segment_on_its_own() -> None:
    assert attribute_path_ref("doc1", ("name",)) == "doc1.name"
    assert attribute_path_ref("doc1", ("address", "city")) == "doc1.address.city"
    assert attribute_path_ref("doc1", ("meta", "x.y")) == "doc1.meta.`x.y`"
    assert attribute_path_ref("doc1", ("address.city",)) == "doc1.`address.city`"


def test_attribute_path_ref_refuses_an_empty_path() -> None:
    with pytest.raises(ValueError):
        attribute_path_ref("doc1", ())


def test_attribute_exists_keeps_the_flat_shape_for_one_level() -> None:
    builder = AqlQueryBuilder()
    assert builder.attribute_exists("doc1", ("name",)) == 'HAS(doc1, "name")'
    # A non-identifier name is bound, never spliced (bound value pinned in
    # nested_paths.yml, case literal_dotted_name_unchanged).
    assert builder.attribute_exists("doc1", ("address.city",)) == "HAS(doc1, @_p1_attr)"


def test_attribute_exists_requires_every_enclosing_level_to_be_an_object() -> None:
    assert AqlQueryBuilder().attribute_exists("doc1", ("a", "b", "c")) == (
        'IS_OBJECT(doc1.a) && IS_OBJECT(doc1.a.b) && HAS(doc1.a.b, "c")'
    )


def test_attribute_exists_refuses_an_empty_path() -> None:
    with pytest.raises(ValueError):
        AqlQueryBuilder().attribute_exists("doc1", ())
