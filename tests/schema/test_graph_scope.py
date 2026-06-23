"""Unit tests for :mod:`arango_sparql.schema.graph_scope`.

Covers both resolution paths (analyzer membership tags + live DB
lookup), the no-op / unresolvable guards, and the bundle-filtering
contract (entities, relationships, conceptual schema, metadata marker).
"""

from __future__ import annotations

from typing import Any

from arango_sparql.schema.graph_scope import scope_bundle_to_graph
from arango_sparql.translate.mapping import MappingBundle


def _bundle(*, with_tags: bool) -> MappingBundle:
    """Build a two-entity, two-relationship bundle.

    ``g1`` contains Person + knows; ``g2`` contains Widget + owns. When
    *with_tags* is true each spec carries the analyzer's ``graphs``
    membership list; otherwise membership must be resolved live.
    """

    def ent(coll: str, graphs: list[str]) -> dict[str, Any]:
        spec: dict[str, Any] = {"collectionName": coll}
        if with_tags:
            spec["graphs"] = graphs
        return spec

    def rel(coll: str, graphs: list[str]) -> dict[str, Any]:
        spec: dict[str, Any] = {"edgeCollectionName": coll}
        if with_tags:
            spec["graphs"] = graphs
        return spec

    return MappingBundle(
        conceptual_schema={
            "entities": [{"name": "Person"}, {"name": "Widget"}],
            "relationships": [{"type": "knows"}, {"type": "owns"}],
        },
        physical_mapping={
            "entities": {
                "Person": ent("Person", ["g1"]),
                "Widget": ent("Widget", ["g2"]),
            },
            "relationships": {
                "knows": rel("knows", ["g1"]),
                "owns": rel("owns", ["g2"]),
            },
        },
        metadata={"statistics": {"documentCount": 5}},
    )


class _FakeGraph:
    def __init__(self, vertices: list[str], edge_defs: list[dict[str, Any]]) -> None:
        self._v = vertices
        self._e = edge_defs

    def vertex_collections(self) -> list[str]:
        return list(self._v)

    def edge_definitions(self) -> list[dict[str, Any]]:
        return list(self._e)


class _FakeDb:
    def __init__(self, graphs: dict[str, _FakeGraph]) -> None:
        self._graphs = graphs

    def has_graph(self, name: str) -> bool:
        return name in self._graphs

    def graph(self, name: str) -> _FakeGraph:
        return self._graphs[name]


# ---------------------------------------------------------------------------
# No-op / guard cases
# ---------------------------------------------------------------------------


def test_none_graph_name_is_noop() -> None:
    b = _bundle(with_tags=True)
    assert scope_bundle_to_graph(None, b, None) is b


def test_empty_graph_name_is_noop() -> None:
    b = _bundle(with_tags=True)
    assert scope_bundle_to_graph(None, b, "") is b


def test_unresolvable_graph_returns_bundle_unchanged() -> None:
    # No membership tags AND a db that knows no graphs → can't resolve →
    # safer to return the full bundle than an empty one.
    b = _bundle(with_tags=False)
    db = _FakeDb({})
    out = scope_bundle_to_graph(db, b, "g1")
    assert out is b


# ---------------------------------------------------------------------------
# Membership-tag path (analyzer >= 0.8)
# ---------------------------------------------------------------------------


def test_membership_tags_filter_entities_and_relationships() -> None:
    b = _bundle(with_tags=True)
    out = scope_bundle_to_graph(None, b, "g1")
    assert set(out.entities()) == {"Person"}
    assert set(out.relationships()) == {"knows"}


def test_membership_tags_prune_conceptual_schema() -> None:
    b = _bundle(with_tags=True)
    out = scope_bundle_to_graph(None, b, "g1")
    ents = [e["name"] for e in out.conceptual_schema["entities"]]
    rels = [r["type"] for r in out.conceptual_schema["relationships"]]
    assert ents == ["Person"]
    assert rels == ["knows"]


def test_scope_marks_metadata_and_preserves_provenance() -> None:
    b = _bundle(with_tags=True)
    out = scope_bundle_to_graph(None, b, "g2")
    assert out.metadata["graphScope"] == "g2"
    # Original metadata is preserved (not clobbered).
    assert out.metadata["statistics"] == {"documentCount": 5}
    # Original bundle is untouched (frozen dataclass → new instance).
    assert set(b.entities()) == {"Person", "Widget"}


def test_membership_second_graph_selects_other_slice() -> None:
    b = _bundle(with_tags=True)
    out = scope_bundle_to_graph(None, b, "g2")
    assert set(out.entities()) == {"Widget"}
    assert set(out.relationships()) == {"owns"}


# ---------------------------------------------------------------------------
# Live-lookup fallback (analyzer without membership tags)
# ---------------------------------------------------------------------------


def test_live_lookup_used_when_no_tags() -> None:
    b = _bundle(with_tags=False)
    db = _FakeDb(
        {
            "g1": _FakeGraph(
                ["Person"],
                [
                    {
                        "edge_collection": "knows",
                        "from_vertex_collections": ["Person"],
                        "to_vertex_collections": ["Person"],
                    }
                ],
            )
        }
    )
    out = scope_bundle_to_graph(db, b, "g1")
    assert set(out.entities()) == {"Person"}
    assert set(out.relationships()) == {"knows"}


def test_live_lookup_unknown_graph_returns_unchanged() -> None:
    b = _bundle(with_tags=False)
    db = _FakeDb({"g1": _FakeGraph(["Person"], [])})
    out = scope_bundle_to_graph(db, b, "does-not-exist")
    assert out is b
