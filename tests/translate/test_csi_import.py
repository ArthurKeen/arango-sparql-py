"""Tests for the CSI v1 -> MappingBundle adapter (translate/csi.py).

Covers the A1->A3 seam: a forward ``CSI v1`` document (the exact shape r2g's
emitter produces) becomes a :class:`MappingBundle` that the SPARQL->AQL
:class:`SchemaResolver` can resolve classes and object-properties from.
"""

from __future__ import annotations

import pytest
from rdflib import URIRef

from arango_sparql.translate.csi import CSI_SOURCE_KIND, mapping_bundle_from_csi
from arango_sparql.translate.mapping import MappingBundle, MappingError
from arango_sparql.translate.resolver import (
    _SYNTHETIC_CONCEPT_NS,
    SchemaResolver,
)


def _forward_csi() -> dict:
    """A CSI v1 document shaped exactly like r2g's forward emitter output."""
    return {
        "csiVersion": "1",
        "conceptualModel": {
            "entities": [
                {"name": "User", "labels": ["User"], "properties": [{"name": "name"}]},
                {"name": "Order", "labels": ["Order"], "properties": []},
            ],
            "relationships": [
                {"type": "placed_by", "fromEntity": "Order", "toEntity": "User"},
            ],
        },
        "arangoPhysicalMapping": {
            "entities": {
                "User": {"style": "COLLECTION", "collectionName": "User"},
                "Order": {"style": "COLLECTION", "collectionName": "Order"},
            },
            "relationships": {
                "placed_by": {
                    "style": "DEDICATED_COLLECTION",
                    "edgeCollectionName": "placed_by",
                },
            },
        },
        "provenance": {
            "producer": "r2g",
            "producerVersion": "0.2.0",
            "direction": "forward",
            "source": {"kind": "postgresql", "ref": "shop", "fingerprint": None},
            "generatedAt": None,
        },
    }


def test_none_yields_empty_bundle():
    bundle = mapping_bundle_from_csi(None)
    assert isinstance(bundle, MappingBundle)
    assert bundle.entities() == {}
    assert bundle.relationships() == {}


def test_physical_entities_pass_through():
    bundle = mapping_bundle_from_csi(_forward_csi())
    entities = bundle.entities()
    assert set(entities) == {"User", "Order"}
    assert entities["User"]["collectionName"] == "User"
    assert entities["User"]["style"] == "COLLECTION"


def test_relationship_gets_endpoints_from_conceptual_model():
    bundle = mapping_bundle_from_csi(_forward_csi())
    rel = bundle.relationships()["placed_by"]
    assert rel["edgeCollectionName"] == "placed_by"
    assert rel["style"] == "DEDICATED_COLLECTION"
    # fromEntity / toEntity lifted from conceptualModel.relationships.
    assert rel["fromEntity"] == "Order"
    assert rel["toEntity"] == "User"


def test_conceptual_schema_and_provenance_preserved():
    csi = _forward_csi()
    bundle = mapping_bundle_from_csi(csi)
    assert bundle.conceptual_schema == csi["conceptualModel"]
    assert bundle.metadata["csiProvenance"] == csi["provenance"]


def test_source_kind_is_imported_csi_and_trusted():
    bundle = mapping_bundle_from_csi(_forward_csi())
    assert bundle.source is not None
    assert bundle.source.kind == CSI_SOURCE_KIND == "imported_csi"
    assert "r2g" in (bundle.source.notes or "")


def test_resolver_round_trip_class_and_property():
    """A1->A3->resolver: the bundle drives real class/edge resolution."""
    bundle = mapping_bundle_from_csi(_forward_csi())
    resolver = SchemaResolver.from_mapping_bundle(bundle)

    user_iri = URIRef(str(_SYNTHETIC_CONCEPT_NS) + "User")
    assert resolver.resolve_class(user_iri).collection == "User"

    placed_by_iri = URIRef(str(_SYNTHETIC_CONCEPT_NS) + "placed_by")
    prop = resolver.resolve_property(placed_by_iri)
    assert prop.is_object_property is True
    assert prop.edge_collection == "placed_by"


def test_generic_with_type_relationship_preserved():
    csi = _forward_csi()
    csi["arangoPhysicalMapping"]["relationships"]["placed_by"] = {
        "style": "GENERIC_WITH_TYPE",
        "edgeCollectionName": "edges",
        "typeField": "_type",
        "typeValue": "placed_by",
    }
    rel = mapping_bundle_from_csi(csi).relationships()["placed_by"]
    assert rel["typeField"] == "_type"
    assert rel["typeValue"] == "placed_by"
    assert rel["edgeCollectionName"] == "edges"


def test_relationship_without_conceptual_entry_omits_endpoints():
    csi = _forward_csi()
    csi["conceptualModel"]["relationships"] = []  # no endpoint info
    rel = mapping_bundle_from_csi(csi).relationships()["placed_by"]
    assert "fromEntity" not in rel
    assert "toEntity" not in rel
    assert rel["edgeCollectionName"] == "placed_by"


@pytest.mark.parametrize("bad_version", ["2", 1, None, ""])
def test_bad_version_rejected(bad_version):
    csi = _forward_csi()
    csi["csiVersion"] = bad_version
    with pytest.raises(MappingError):
        mapping_bundle_from_csi(csi)


def test_non_dict_rejected():
    with pytest.raises(MappingError):
        mapping_bundle_from_csi([1, 2, 3])  # type: ignore[arg-type]


def test_empty_physical_blocks_ok():
    csi = _forward_csi()
    csi["arangoPhysicalMapping"] = {"entities": {}, "relationships": {}}
    csi["conceptualModel"] = {"entities": [], "relationships": []}
    bundle = mapping_bundle_from_csi(csi)
    assert bundle.entities() == {}
    assert bundle.relationships() == {}
    assert bundle.source is not None and bundle.source.kind == "imported_csi"
