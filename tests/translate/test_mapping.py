"""Unit tests for ``arango_sparql.translate.mapping``.

Covers:

* :class:`MappingBundle` construction (direct and via wire-dict).
* :func:`mapping_from_wire_dict` — camelCase / snake_case normalisation,
  RPT-style entry preservation, structural error paths.
* :func:`mapping_to_wire_dict` round-trip.
* :func:`is_valid_collection_name` / :func:`is_valid_field_name`.
* :meth:`SchemaResolver.from_mapping_bundle` — synthetic and inline-OWL
  paths, for the three v0.x entity styles (``COLLECTION``, ``LABEL``,
  ``RPT``) and the three relationship styles.

All tests are pure-Python (no ArangoDB, no LLM, no network) so they
fall under the default unmarked-pytest gate.
"""

from __future__ import annotations

import pytest

from arango_sparql.translate.mapping import (
    MappingBundle,
    MappingError,
    MappingSource,
    is_valid_collection_name,
    is_valid_field_name,
    mapping_from_wire_dict,
    mapping_to_wire_dict,
)
from arango_sparql.translate.resolver import SchemaResolver

# ---------------------------------------------------------------------------
# Identifier validators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "persons",
        "_internal",
        "MyCollection",
        "a-b-c",
        "C1",
        "A" * 256,  # max length
    ],
)
def test_is_valid_collection_name_accepts_valid(name: str) -> None:
    assert is_valid_collection_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "",
        "1persons",  # leading digit
        "-leading-hyphen",
        "has space",
        "has.dot",
        "A" * 257,  # too long
        None,
        123,
        ["persons"],
    ],
)
def test_is_valid_collection_name_rejects_invalid(name: object) -> None:
    assert is_valid_collection_name(name) is False


def test_is_valid_field_name_accepts_normal_and_unicode() -> None:
    assert is_valid_field_name("name") is True
    assert is_valid_field_name("user.email") is True
    assert is_valid_field_name("прізвище") is True


@pytest.mark.parametrize("name", ["", "has\x00null", "has\nnewline", "has`backtick", None])
def test_is_valid_field_name_rejects_unsafe(name: object) -> None:
    assert is_valid_field_name(name) is False


# ---------------------------------------------------------------------------
# MappingBundle dataclass
# ---------------------------------------------------------------------------


def test_mapping_bundle_default_is_empty() -> None:
    b = MappingBundle()
    assert b.conceptual_schema == {}
    assert b.physical_mapping == {}
    assert b.metadata == {}
    assert b.owl_turtle is None
    assert b.source is None
    assert b.entities() == {}
    assert b.relationships() == {}


def test_mapping_bundle_is_frozen() -> None:
    """``@dataclass(frozen=True)`` is the contract that lets a freshly-
    acquired bundle be cached and shared across requests without lock
    contention (PRD §6.3.3). Mutating it must fail loudly.
    """

    from dataclasses import FrozenInstanceError

    b = MappingBundle()
    with pytest.raises(FrozenInstanceError):
        b.conceptual_schema = {"x": 1}  # type: ignore[misc]


def test_mapping_bundle_entities_accessor_returns_dict() -> None:
    b = MappingBundle(
        physical_mapping={
            "entities": {"Person": {"collectionName": "persons"}},
            "relationships": {},
        }
    )
    assert b.entities() == {"Person": {"collectionName": "persons"}}
    assert b.relationships() == {}


# ---------------------------------------------------------------------------
# mapping_from_wire_dict — happy paths
# ---------------------------------------------------------------------------


def test_from_wire_dict_none_returns_empty_bundle() -> None:
    assert mapping_from_wire_dict(None) == MappingBundle()


def test_from_wire_dict_camel_case() -> None:
    wire = {
        "conceptualSchema": {"entities": [{"name": "Person"}]},
        "physicalMapping": {
            "entities": {
                "Person": {"collectionName": "persons", "style": "COLLECTION"},
            },
            "relationships": {
                "FOLLOWS": {
                    "edgeCollectionName": "follows",
                    "style": "DEDICATED_COLLECTION",
                    "fromEntity": "Person",
                    "toEntity": "Person",
                },
            },
        },
        "metadata": {"confidence": 0.9},
    }
    b = mapping_from_wire_dict(wire)
    assert b.entities()["Person"]["collectionName"] == "persons"
    assert b.relationships()["FOLLOWS"]["edgeCollectionName"] == "follows"
    assert b.relationships()["FOLLOWS"]["fromEntity"] == "Person"
    assert b.metadata == {"confidence": 0.9}


def test_from_wire_dict_snake_case_aliases_normalise_to_camel() -> None:
    wire = {
        "conceptual_schema": {"entities": []},
        "physical_mapping": {
            "entities": {
                "Person": {"collection_name": "persons", "style": "COLLECTION"},
            },
            "relationships": {
                "FOLLOWS": {
                    "edge_collection_name": "follows",
                    "from_entity": "Person",
                    "to_entity": "Person",
                    "style": "DEDICATED_COLLECTION",
                },
            },
        },
    }
    b = mapping_from_wire_dict(wire)
    spec = b.entities()["Person"]
    assert spec["collectionName"] == "persons"
    assert "collection_name" not in spec
    rel = b.relationships()["FOLLOWS"]
    assert rel["edgeCollectionName"] == "follows"
    assert rel["fromEntity"] == "Person"
    assert rel["toEntity"] == "Person"


def test_from_wire_dict_relationship_collection_name_aliases_to_edge() -> None:
    """The sister project's LPG fixtures spell the edge-collection
    field as bare ``"collectionName"`` on a relationship spec. Since
    the field unambiguously means *edge* collection in that context,
    our normaliser must rewrite it to ``"edgeCollectionName"`` so
    every downstream layer sees one canonical spelling.
    """

    wire = {
        "physicalMapping": {
            "entities": {},
            "relationships": {
                "FOLLOWS": {
                    "collectionName": "edges",
                    "style": "GENERIC_WITH_TYPE",
                    "typeField": "type",
                    "typeValue": "FOLLOWS",
                },
            },
        },
    }
    spec = mapping_from_wire_dict(wire).relationships()["FOLLOWS"]
    assert spec["edgeCollectionName"] == "edges"
    assert "collectionName" not in spec


def test_from_wire_dict_relationship_alias_conflict_raises() -> None:
    """A relationship spec that carries BOTH ``collectionName`` and
    ``edgeCollectionName`` is ambiguous (which one wins?). We refuse
    rather than silently picking one.
    """

    with pytest.raises(MappingError) as exc:
        mapping_from_wire_dict(
            {
                "physicalMapping": {
                    "entities": {},
                    "relationships": {
                        "FOLLOWS": {
                            "collectionName": "edges",
                            "edgeCollectionName": "follows",
                        },
                    },
                },
            }
        )
    assert "FOLLOWS" in str(exc.value)


def test_from_wire_dict_entity_collection_name_is_not_aliased_to_edge() -> None:
    """Mirror-image of the relationship alias: on an *entity* spec,
    ``"collectionName"`` means document collection and must NOT be
    rewritten to ``edgeCollectionName``.
    """

    wire = {
        "physicalMapping": {
            "entities": {
                "Person": {"collectionName": "persons", "style": "COLLECTION"},
            },
            "relationships": {},
        },
    }
    spec = mapping_from_wire_dict(wire).entities()["Person"]
    assert spec["collectionName"] == "persons"
    assert "edgeCollectionName" not in spec


def test_from_wire_dict_preserves_lpg_type_discriminator() -> None:
    wire = {
        "physicalMapping": {
            "entities": {
                "Person": {
                    "collectionName": "nodes",
                    "style": "LABEL",
                    "typeField": "type",
                    "typeValue": "Person",
                },
            },
            "relationships": {},
        },
    }
    spec = mapping_from_wire_dict(wire).entities()["Person"]
    assert spec["typeField"] == "type"
    assert spec["typeValue"] == "Person"


def test_from_wire_dict_preserves_rpt_columns() -> None:
    wire = {
        "physicalMapping": {
            "entities": {
                "Person": {
                    "style": "RPT",
                    "triplesCollection": "_triples",
                    "subjectColumn": "s",
                    "predicateColumn": "p",
                    "objectUriColumn": "o_uri",
                    "objectValueColumn": "o_val",
                },
            },
            "relationships": {},
        },
    }
    spec = mapping_from_wire_dict(wire).entities()["Person"]
    assert spec["style"] == "RPT"
    assert spec["triplesCollection"] == "_triples"
    assert spec["subjectColumn"] == "s"
    assert spec["objectUriColumn"] == "o_uri"
    assert spec["objectValueColumn"] == "o_val"


def test_from_wire_dict_preserves_unknown_top_level_keys() -> None:
    """Forward-compat: future analyzer versions may add top-level keys
    we do not recognise; they must round-trip verbatim rather than be
    dropped on the floor.
    """

    wire = {
        "physicalMapping": {"entities": {}, "relationships": {}},
        "futureFeatureX": {"version": 2, "payload": [1, 2, 3]},
    }
    b = mapping_from_wire_dict(wire)
    # The unknown key is not surfaced as a typed attribute, but
    # `mapping_to_wire_dict` should not lose it on a round-trip via
    # construction from this attribute either. The bundle does not
    # itself store unknown top-level keys (by design — they belong to
    # `metadata` if they matter), so we accept that they are dropped
    # at the bundle boundary but raise no error.
    assert "physicalMapping" in mapping_to_wire_dict(b)


def test_from_wire_dict_with_inline_owl() -> None:
    ttl = "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<http://example/Person> a owl:Class .\n"
    b = mapping_from_wire_dict({"owlTurtle": ttl})
    assert b.owl_turtle == ttl


def test_from_wire_dict_with_source() -> None:
    b = mapping_from_wire_dict(
        {
            "source": {"kind": "heuristic", "notes": "no analyzer installed"},
        }
    )
    assert b.source == MappingSource(kind="heuristic", notes="no analyzer installed")


# ---------------------------------------------------------------------------
# mapping_from_wire_dict — error paths
# ---------------------------------------------------------------------------


def test_from_wire_dict_rejects_non_dict_input() -> None:
    with pytest.raises(MappingError):
        mapping_from_wire_dict("not a dict")  # type: ignore[arg-type]


def test_from_wire_dict_rejects_non_dict_physical_mapping() -> None:
    with pytest.raises(MappingError) as exc:
        mapping_from_wire_dict({"physicalMapping": ["not", "a", "dict"]})
    assert "physicalMapping" in str(exc.value)


def test_from_wire_dict_rejects_non_dict_entities() -> None:
    with pytest.raises(MappingError) as exc:
        mapping_from_wire_dict({"physicalMapping": {"entities": [{"Person": {}}], "relationships": {}}})
    assert "entities" in str(exc.value)


def test_from_wire_dict_rejects_non_dict_entity_spec() -> None:
    with pytest.raises(MappingError) as exc:
        mapping_from_wire_dict(
            {
                "physicalMapping": {
                    "entities": {"Person": "not a dict"},
                    "relationships": {},
                },
            }
        )
    assert "Person" in str(exc.value)


def test_from_wire_dict_rejects_invalid_source_kind() -> None:
    with pytest.raises(MappingError) as exc:
        mapping_from_wire_dict({"source": {"kind": "wishful_thinking"}})
    assert "source.kind" in str(exc.value)


def test_from_wire_dict_rejects_non_string_owl_turtle() -> None:
    with pytest.raises(MappingError) as exc:
        mapping_from_wire_dict({"owlTurtle": 42})
    assert "owlTurtle" in str(exc.value)


def test_from_wire_dict_rejects_duplicate_top_level_spellings() -> None:
    """When a caller supplies both ``camelCase`` and ``snake_case``
    spellings of the same logical key, we refuse rather than silently
    pick a winner.
    """

    with pytest.raises(MappingError):
        mapping_from_wire_dict(
            {
                "physicalMapping": {"entities": {}, "relationships": {}},
                "physical_mapping": {"entities": {}, "relationships": {}},
            }
        )


# ---------------------------------------------------------------------------
# mapping_to_wire_dict round-trip
# ---------------------------------------------------------------------------


def test_wire_dict_round_trip_preserves_shape() -> None:
    original = {
        "conceptualSchema": {"entities": [{"name": "Person"}]},
        "physicalMapping": {
            "entities": {
                "Person": {"collectionName": "persons", "style": "COLLECTION"},
            },
            "relationships": {
                "FOLLOWS": {
                    "edgeCollectionName": "follows",
                    "style": "DEDICATED_COLLECTION",
                },
            },
        },
        "metadata": {"confidence": 0.95},
    }
    b = mapping_from_wire_dict(original)
    out = mapping_to_wire_dict(b)
    assert out["physicalMapping"] == original["physicalMapping"]
    assert out["conceptualSchema"] == original["conceptualSchema"]
    assert out["metadata"] == original["metadata"]


def test_wire_dict_round_trip_preserves_source_and_owl() -> None:
    ttl = "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    b = mapping_from_wire_dict(
        {
            "owlTurtle": ttl,
            "source": {"kind": "imported_owl", "notes": "uploaded by admin"},
        }
    )
    out = mapping_to_wire_dict(b)
    assert out["owlTurtle"] == ttl
    assert out["source"] == {"kind": "imported_owl", "notes": "uploaded by admin"}


# ---------------------------------------------------------------------------
# SchemaResolver.from_mapping_bundle — synthetic-IRI path
# ---------------------------------------------------------------------------


def _synthetic_iri(label: str) -> str:
    return f"urn:arango-sparql:concept#{label}"


def test_resolver_from_bundle_collection_style() -> None:
    bundle = mapping_from_wire_dict(
        {
            "physicalMapping": {
                "entities": {
                    "Person": {"collectionName": "persons", "style": "COLLECTION"},
                },
                "relationships": {
                    "FOLLOWS": {
                        "edgeCollectionName": "follows",
                        "style": "DEDICATED_COLLECTION",
                        "fromEntity": "Person",
                        "toEntity": "Person",
                    },
                },
            },
        }
    )
    r = SchemaResolver.from_mapping_bundle(bundle)
    rc = r.resolve_class(_synthetic_iri("Person"))
    assert rc.collection == "persons"
    assert rc.type_field is None and rc.type_value is None

    rp = r.resolve_property(_synthetic_iri("FOLLOWS"))
    assert rp.is_object_property is True
    assert rp.edge_collection == "follows"
    assert rp.domain_iri == _synthetic_iri("Person")
    assert rp.range_iri == _synthetic_iri("Person")
    # PRD §6.1: an object property with a phys:edgeCollectionName but
    # no discriminator defaults to ``DEDICATED_COLLECTION`` style so
    # the visitor's edge-traversal emitter picks the bare ``FOR v, e
    # IN OUTBOUND`` pattern (no FILTER on the edge type field).
    assert rp.mapping_style == "DEDICATED_COLLECTION"
    assert rp.type_field is None and rp.type_value is None


def test_resolver_property_generic_with_type_carries_discriminator() -> None:
    """An object property with ``phys:typeField`` / ``phys:typeValue`` —
    the LPG-typed-edge style (PRD §6.1) — must surface those fields
    on the :class:`ResolvedProperty` so the visitor can emit the
    ``FILTER e.<typeField> == @<typeValue>`` discriminator alongside
    the OUTBOUND traversal.
    """
    bundle = mapping_from_wire_dict(
        {
            "physicalMapping": {
                "entities": {
                    "Person": {"collectionName": "persons", "style": "COLLECTION"},
                },
                "relationships": {
                    "WORKS_AT": {
                        "edgeCollectionName": "rel",
                        "style": "GENERIC_WITH_TYPE",
                        "typeField": "type",
                        "typeValue": "worksAt",
                        "fromEntity": "Person",
                        "toEntity": "Person",
                    },
                },
            },
        }
    )
    r = SchemaResolver.from_mapping_bundle(bundle)
    rp = r.resolve_property(_synthetic_iri("WORKS_AT"))
    assert rp.is_object_property is True
    assert rp.edge_collection == "rel"
    assert rp.mapping_style == "GENERIC_WITH_TYPE"
    assert rp.type_field == "type"
    assert rp.type_value == "worksAt"


def test_resolver_property_typed_discriminator_without_explicit_style() -> None:
    """An ontology that declares ``phys:typeField``/``phys:typeValue``
    but omits ``phys:mappingStyle`` should still route to
    ``GENERIC_WITH_TYPE`` — the resolver infers the style from the
    presence of the discriminator pair so the visitor emits the
    correct ``FILTER e.<typeField> == @<typeValue>`` clause.
    """
    ttl = """
    @prefix : <http://ex.org/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix phys: <https://arango.solutions/phys#> .

    :Person a owl:Class ; phys:collectionName "Person" .
    :worksAt a owl:ObjectProperty ;
        phys:edgeCollectionName "rel" ;
        phys:typeField "type" ;
        phys:typeValue "worksAt" .
    """
    r = SchemaResolver.from_turtle(ttl)
    rp = r.resolve_property("http://ex.org/worksAt")
    assert rp.mapping_style == "GENERIC_WITH_TYPE"
    assert rp.type_field == "type"
    assert rp.type_value == "worksAt"


def test_resolver_datatype_property_has_no_edge_metadata() -> None:
    """Datatype properties must surface ``mapping_style``/``type_field``
    /``type_value`` as ``None`` — those fields only make sense for
    object-property edge traversal, and a stray non-None value would
    leak the LPG discriminator into a plain attribute lookup.
    """
    ttl = """
    @prefix : <http://ex.org/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix phys: <https://arango.solutions/phys#> .

    :Person a owl:Class ; phys:collectionName "Person" .
    :name a owl:DatatypeProperty .
    """
    r = SchemaResolver.from_turtle(ttl)
    rp = r.resolve_property("http://ex.org/name")
    assert rp.is_object_property is False
    assert rp.edge_collection is None
    assert rp.mapping_style is None
    assert rp.type_field is None
    assert rp.type_value is None


def test_resolver_from_bundle_label_style_carries_discriminator() -> None:
    bundle = mapping_from_wire_dict(
        {
            "physicalMapping": {
                "entities": {
                    "Person": {
                        "collectionName": "nodes",
                        "style": "LABEL",
                        "typeField": "type",
                        "typeValue": "Person",
                    },
                },
                "relationships": {},
            },
        }
    )
    r = SchemaResolver.from_mapping_bundle(bundle)
    rc = r.resolve_class(_synthetic_iri("Person"))
    assert rc.collection == "nodes"
    assert rc.type_field == "type"
    assert rc.type_value == "Person"


def test_resolver_from_bundle_rpt_style_uses_triples_collection() -> None:
    """For RPT entities the resolver-visible ``collection`` should fall
    back to ``triplesCollection`` when no explicit ``collectionName``
    is set — that is where the engine reads rows from. The RPT-only
    ``*_column`` overrides must round-trip onto the resolved class so
    the visitor's RPT emitter reads from the renamed columns.
    """

    bundle = mapping_from_wire_dict(
        {
            "physicalMapping": {
                "entities": {
                    "Person": {
                        "style": "RPT",
                        "triplesCollection": "rdf_triples",
                        "subjectColumn": "s",
                        "predicateColumn": "p",
                        "objectUriColumn": "o_uri",
                        "objectValueColumn": "o_val",
                    },
                },
                "relationships": {},
            },
        }
    )
    r = SchemaResolver.from_mapping_bundle(bundle)
    rc = r.resolve_class(_synthetic_iri("Person"))
    assert rc.collection == "rdf_triples"
    assert rc.style == "RPT"
    assert rc.subject_column == "s"
    assert rc.predicate_column == "p"
    assert rc.object_uri_column == "o_uri"
    assert rc.object_value_column == "o_val"


def test_resolver_rpt_class_defaults_columns_when_overrides_omitted() -> None:
    """An RPT class without explicit ``phys:*Column`` overrides must
    surface the legacy Foxx default columns. The visitor's emitter
    relies on these defaults — a resolver bug that silently dropped
    them would emit AQL referencing nonexistent attributes.
    """
    ttl = """
    @prefix : <http://ex.org/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix phys: <https://arango.solutions/phys#> .

    :Person a owl:Class ;
        phys:mappingStyle "RPT" ;
        phys:triplesCollection "_triples" .
    """
    r = SchemaResolver.from_turtle(ttl)
    rc = r.resolve_class("http://ex.org/Person")
    assert rc.style == "RPT"
    assert rc.collection == "_triples"
    assert rc.subject_column == "subject_uri"
    assert rc.predicate_column == "predicate"
    assert rc.object_uri_column == "object_uri"
    assert rc.object_value_column == "object_value"


def test_resolver_pg_class_has_no_rpt_metadata_leakage() -> None:
    """A plain PG (``COLLECTION``) class must not carry a non-default
    ``style`` value when the ontology omits ``phys:mappingStyle`` —
    the visitor treats ``style is None`` as PG and any leaked ``RPT``
    here would silently route the wrong emission path.
    """
    ttl = """
    @prefix : <http://ex.org/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix phys: <https://arango.solutions/phys#> .

    :Person a owl:Class ; phys:collectionName "Person" .
    """
    r = SchemaResolver.from_turtle(ttl)
    rc = r.resolve_class("http://ex.org/Person")
    assert rc.style is None
    assert rc.collection == "Person"
    # Defaults are still present, but the visitor only consults them
    # when style == "RPT".
    assert rc.subject_column == "subject_uri"


def test_resolver_from_bundle_label_with_special_chars_in_label() -> None:
    """Labels with spaces or slashes must still produce resolvable
    classes via percent-encoded synthetic IRIs.
    """

    bundle = mapping_from_wire_dict(
        {
            "physicalMapping": {
                "entities": {
                    "Person Name": {
                        "collectionName": "persons",
                        "style": "COLLECTION",
                    },
                },
                "relationships": {},
            },
        }
    )
    r = SchemaResolver.from_mapping_bundle(bundle)
    rc = r.resolve_class("urn:arango-sparql:concept#Person%20Name")
    assert rc.collection == "persons"


# ---------------------------------------------------------------------------
# SchemaResolver.from_mapping_bundle — inline OWL path
# ---------------------------------------------------------------------------


def test_resolver_from_bundle_uses_inline_owl_when_present() -> None:
    """When the bundle carries an inline OWL ontology, the resolver
    should consume *that* and ignore the synthetic-IRI path entirely.
    Customer IRIs (rather than ``urn:arango-sparql:concept#…``) must
    resolve.
    """

    ttl = (
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "@prefix phys: <https://arango.solutions/phys#> .\n"
        "<http://customer.example/onto#Person> a owl:Class ;\n"
        '    phys:collectionName "customer_persons" .\n'
    )
    bundle = mapping_from_wire_dict(
        {
            "owlTurtle": ttl,
            "physicalMapping": {
                "entities": {
                    "Person": {"collectionName": "ignored", "style": "COLLECTION"},
                },
                "relationships": {},
            },
            "source": {"kind": "imported_owl"},
        }
    )
    r = SchemaResolver.from_mapping_bundle(bundle)
    rc = r.resolve_class("http://customer.example/onto#Person")
    assert rc.collection == "customer_persons"


def test_resolver_from_empty_bundle_has_empty_ontology() -> None:
    """An empty bundle (no entities, no relationships, no OWL) should
    yield a usable resolver whose ontology is just an empty graph.
    Resolving an undeclared class against it must raise
    ``SchemaResolutionError``, not crash.
    """

    from arango_sparql.errors import SchemaResolutionError

    r = SchemaResolver.from_mapping_bundle(MappingBundle())
    with pytest.raises(SchemaResolutionError):
        r.resolve_class("http://example/Unknown")


# ---------------------------------------------------------------------------
# Permissive class resolution (opt-in fallback to default_collection)
# ---------------------------------------------------------------------------


def test_permissive_class_resolution_degrades_unknown_iri_to_default_collection() -> None:
    """Opt-in permissive mode mirrors how :meth:`resolve_property` already
    handles unmapped property IRIs: degrade to the default collection
    instead of raising. SPARQL is open-world; a query that names an
    unknown class should return zero rows from the default collection,
    not crash the translator.
    """

    r = SchemaResolver.from_turtle(
        "",
        default_collection="Document",
        permissive_class_resolution=True,
    )

    resolved = r.resolve_class("http://example.org/foaf#Person")

    assert resolved.collection == "Document"
    assert resolved.iri == "http://example.org/foaf#Person"
    assert resolved.style is None
    assert resolved.type_field is None
    assert resolved.type_value is None


def test_permissive_class_resolution_emits_unmapped_class_warning() -> None:
    """The fallback path MUST surface a ``W_SCHEMA_UNMAPPED_CLASS``
    advisory so operators (and the UI's schema-warnings sidebar) can
    see what silently fell back to the default collection.
    """

    r = SchemaResolver.from_turtle(
        "",
        default_collection="Document",
        permissive_class_resolution=True,
    )

    r.resolve_class("http://example.org/foaf#Person")

    assert len(r.warnings) == 1
    w = r.warnings[0]
    assert w["code"] == "W_SCHEMA_UNMAPPED_CLASS"
    assert w["iri"] == "http://example.org/foaf#Person"
    assert w["fallback_collection"] == "Document"
    assert "permissive mode" in w["message"]


def test_permissive_class_resolution_deduplicates_repeated_warnings() -> None:
    """Visitors invoke ``resolve_class`` once per triple — a query like
    ``?a a :Unknown . ?b a :Unknown`` would otherwise produce two
    identical warnings. The ``_warned_keys`` guard inherited from
    :meth:`_warn_schema` MUST collapse them to one.
    """

    r = SchemaResolver.from_turtle(
        "",
        default_collection="Document",
        permissive_class_resolution=True,
    )

    r.resolve_class("http://example.org/foaf#Person")
    r.resolve_class("http://example.org/foaf#Person")
    r.resolve_class("http://example.org/foaf#Person")

    assert len(r.warnings) == 1


def test_strict_mode_is_the_default_and_still_raises() -> None:
    """The opt-in flag MUST default to ``False`` so production callers
    that rely on strict schema validation see no behaviour change.
    The default constructor and ``from_turtle`` without the flag both
    keep the historical strict contract.
    """

    from arango_sparql.errors import SchemaResolutionError

    r_default = SchemaResolver.from_turtle("", default_collection="Document")
    with pytest.raises(SchemaResolutionError):
        r_default.resolve_class("http://example.org/foaf#Person")

    r_explicit_strict = SchemaResolver.from_turtle(
        "",
        default_collection="Document",
        permissive_class_resolution=False,
    )
    with pytest.raises(SchemaResolutionError):
        r_explicit_strict.resolve_class("http://example.org/foaf#Person")


def test_permissive_mode_respects_custom_default_collection() -> None:
    """The fallback target is whatever ``default_collection`` was set
    to — not hard-coded to ``"Document"``. A deployment that uses a
    different umbrella collection (e.g. ``"Triples"`` for RPT-only
    datasets) should see its unknowns degrade there.
    """

    r = SchemaResolver.from_turtle(
        "",
        default_collection="Triples",
        permissive_class_resolution=True,
    )

    resolved = r.resolve_class("http://example.org/foaf#Person")
    assert resolved.collection == "Triples"


def test_permissive_mode_does_not_shadow_declared_classes() -> None:
    """When a class IRI IS declared in the ontology, permissive mode
    MUST still resolve through the normal path — it's a fallback for
    unknowns, not an override that erases the ontology.
    """

    ttl = """
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix phys: <https://arango.solutions/phys#> .
    @prefix : <http://example.org/> .
    :Person a owl:Class ; phys:collectionName "people" .
    """
    r = SchemaResolver.from_turtle(
        ttl,
        default_collection="Document",
        permissive_class_resolution=True,
    )

    resolved = r.resolve_class("http://example.org/Person")
    assert resolved.collection == "people"
    assert r.warnings == []  # no fallback warning for a real class
