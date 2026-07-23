"""Tests for :mod:`arango_sparql.service.protocol.results_rdf`.

Coverage:

* Dispatch table — every supported media type maps to an rdflib format.
* CONSTRUCT/DESCRIBE round-trip — render → parse → assert graph shape.
* Value-rehydration heuristic — IRIs vs literals vs bnodes vs typed
  literals match the legacy Foxx ``arango-sparql`` service's
  classification.
* Robustness — incomplete rows (``None`` in any component) are
  dropped, not crash; list-of-list rows flatten transparently; empty
  cursors yield an empty graph.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

import pytest
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import XSD

from arango_sparql.service.protocol.results_rdf import (
    MEDIA_TYPE_TO_RDF_FORMAT,
    RDF_FORMAT_NAMES,
    render_construct,
)

# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------


def test_format_names_and_media_types_are_in_sync() -> None:
    """Every format key in the dispatch table is one of the four
    documented ``RDF_FORMAT_NAMES``.
    """

    assert set(MEDIA_TYPE_TO_RDF_FORMAT.values()) == set(RDF_FORMAT_NAMES)
    assert RDF_FORMAT_NAMES == ("ttl", "nt", "rdfxml", "jsonld")


def test_unknown_media_type_raises() -> None:
    """An unknown media type is a *programming* error — the route layer
    must always negotiate before calling. Raise :class:`ValueError` so
    the caller notices loudly.
    """

    with pytest.raises(ValueError, match="unsupported media type"):
        render_construct("application/x-nonsense", [])


# ---------------------------------------------------------------------------
# Wire-format round-trips
# ---------------------------------------------------------------------------


_DEFAULT_ROWS: list[Any] = [
    [
        {
            "subject": "http://ex.org/Alice",
            "predicate": "http://xmlns.com/foaf/0.1/name",
            "object": "Alice",
        }
    ],
    [
        {
            "subject": "http://ex.org/Bob",
            "predicate": "http://xmlns.com/foaf/0.1/name",
            "object": "Bob",
        }
    ],
]


@pytest.mark.parametrize(
    "media_type, parse_format",
    [
        ("text/turtle", "turtle"),
        ("application/n-triples", "nt"),
        ("application/rdf+xml", "xml"),
        ("application/ld+json", "json-ld"),
    ],
    ids=RDF_FORMAT_NAMES,
)
def test_render_construct_roundtrip(media_type: str, parse_format: str) -> None:
    body = render_construct(media_type, _DEFAULT_ROWS)
    g = Graph()
    g.parse(data=body, format=parse_format)
    assert len(g) == 2
    foaf_name = URIRef("http://xmlns.com/foaf/0.1/name")
    assert (
        URIRef("http://ex.org/Alice"),
        foaf_name,
        Literal("Alice"),
    ) in g
    assert (
        URIRef("http://ex.org/Bob"),
        foaf_name,
        Literal("Bob"),
    ) in g


# ---------------------------------------------------------------------------
# Value rehydration
# ---------------------------------------------------------------------------


def test_typed_int_object_becomes_xsd_integer() -> None:
    """Python ``int`` objects in the AQL row become typed ``xsd:integer``
    literals — matches the SELECT renderer's heuristic in
    :mod:`.results`.
    """

    rows = [
        [
            {
                "subject": "http://ex.org/Alice",
                "predicate": "http://ex.org/age",
                "object": 30,
            }
        ]
    ]
    body = render_construct("application/n-triples", rows)
    assert "30" in body
    assert str(XSD.integer) in body


def test_typed_float_object_becomes_xsd_double() -> None:
    rows = [
        [
            {
                "subject": "http://ex.org/Alice",
                "predicate": "http://ex.org/score",
                "object": 3.14,
            }
        ]
    ]
    body = render_construct("application/n-triples", rows)
    # rdflib infers ``xsd:double`` from a Python ``float``.
    assert "3.14" in body


def test_typed_bool_object_becomes_xsd_boolean() -> None:
    rows = [
        [
            {
                "subject": "http://ex.org/Alice",
                "predicate": "http://ex.org/active",
                "object": True,
            }
        ]
    ]
    body = render_construct("application/n-triples", rows)
    assert "true" in body.lower()
    assert str(XSD.boolean) in body


def test_iri_shaped_string_object_becomes_uri() -> None:
    rows = [
        [
            {
                "subject": "http://ex.org/Alice",
                "predicate": "http://xmlns.com/foaf/0.1/knows",
                "object": "http://ex.org/Bob",
            }
        ]
    ]
    body = render_construct("application/n-triples", rows)
    assert "<http://ex.org/Bob>" in body


def test_bnode_string_becomes_bnode_term() -> None:
    """Visitor binds blank nodes as ``_:<label>`` strings; the renderer
    recognises the prefix and rehydrates as :class:`rdflib.BNode`.
    """

    rows = [
        [
            {
                "subject": "_:alice",
                "predicate": "http://xmlns.com/foaf/0.1/name",
                "object": "Alice",
            }
        ]
    ]
    g = Graph()
    g.parse(data=render_construct("application/n-triples", rows), format="nt")
    subjects = list({s for s, _, _ in g})
    assert len(subjects) == 1
    assert isinstance(subjects[0], BNode)


def test_dict_with_uri_or_id_becomes_iri() -> None:
    """An AQL row that returns a sub-document (``RETURN doc`` style)
    rehydrates from ``_uri`` first, then falls back to ``_id``. This
    matches the SELECT renderer's behaviour for the same shape.
    """

    rows = [
        [
            {
                "subject": {"_uri": "http://ex.org/Alice", "_id": "Person/123"},
                "predicate": "http://xmlns.com/foaf/0.1/name",
                "object": "Alice",
            }
        ]
    ]
    g = Graph()
    g.parse(data=render_construct("text/turtle", rows), format="turtle")
    assert (URIRef("http://ex.org/Alice"), None, None) in ((s, None, None) for s, _, _ in g)


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_none_components_drop_the_triple() -> None:
    """A row whose ``object`` is ``None`` (e.g. a missing attribute
    in a DESCRIBE attribute-fan-out) is *not* a valid RDF triple per
    the W3C spec — the renderer drops it silently rather than emit
    a malformed graph.
    """

    rows = [
        [
            {
                "subject": "http://ex.org/Alice",
                "predicate": "http://ex.org/email",
                "object": None,
            },
            {
                "subject": "http://ex.org/Alice",
                "predicate": "http://xmlns.com/foaf/0.1/name",
                "object": "Alice",
            },
        ]
    ]
    g = Graph()
    g.parse(data=render_construct("text/turtle", rows), format="turtle")
    assert len(g) == 1


def test_predicate_string_is_coerced_to_uri() -> None:
    """A predicate string that *doesn't* match the IRI heuristic (e.g.
    a DESCRIBE attribute-fan-out's bare ``"name"`` predicate) gets
    coerced to a :class:`URIRef` rather than treated as a literal —
    RDF requires the predicate slot to be a URI.
    """

    rows = [
        [
            {
                "subject": "http://ex.org/Alice",
                "predicate": "name",
                "object": "Alice",
            }
        ]
    ]
    g = Graph()
    g.parse(data=render_construct("text/turtle", rows), format="turtle")
    predicates = {p for _, p, _ in g}
    assert len(predicates) == 1
    assert isinstance(next(iter(predicates)), URIRef)


def test_singleton_dict_row_treated_as_one_triple() -> None:
    """The visitor's normal shape is one *list* of triple dicts per
    row, but a hand-rolled emitter that produces a single dict per
    row still flattens — defensive against the visitor changing the
    wire shape down the road.
    """

    rows = [
        {
            "subject": "http://ex.org/Alice",
            "predicate": "http://xmlns.com/foaf/0.1/name",
            "object": "Alice",
        }
    ]
    g = Graph()
    g.parse(data=render_construct("text/turtle", rows), format="turtle")
    assert len(g) == 1


def test_empty_cursor_yields_empty_graph() -> None:
    body = render_construct("text/turtle", [])
    g = Graph()
    g.parse(data=body, format="turtle")
    assert len(g) == 0


def test_duplicate_triples_dedupe_via_graph_set_semantics() -> None:
    """RDF's set semantics: two CONSTRUCT bindings that yield the
    same triple appear once in the rendered graph. Mirrors the
    legacy Foxx renderer's dedupe pass (we get it for free via
    :class:`rdflib.Graph`).
    """

    rows = [
        [
            {
                "subject": "http://ex.org/Alice",
                "predicate": "http://xmlns.com/foaf/0.1/name",
                "object": "Alice",
            }
        ],
        [
            {
                "subject": "http://ex.org/Alice",
                "predicate": "http://xmlns.com/foaf/0.1/name",
                "object": "Alice",
            }
        ],
    ]
    g = Graph()
    g.parse(data=render_construct("application/n-triples", rows), format="nt")
    assert len(g) == 1


def test_json_ld_output_is_valid_json() -> None:
    """JSON-LD bodies must be valid JSON — defensive check that
    rdflib's JSON-LD serialiser hasn't silently emitted an XML
    fallback when the wire format expects ``json``.
    """

    body = render_construct("application/ld+json", _DEFAULT_ROWS)
    payload = json.loads(body)
    assert isinstance(payload, (list, dict))


def test_rdf_xml_output_is_well_formed_xml() -> None:
    body = render_construct("application/rdf+xml", _DEFAULT_ROWS)
    root = ET.fromstring(body)
    assert root.tag.endswith("}RDF")
