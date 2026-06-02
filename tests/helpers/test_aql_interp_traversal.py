"""Unit tests for the AQL-subset interpreter's OUTBOUND traversal.

The interpreter (:mod:`tests.helpers.aql_interp`) is exercised end-to-end
by the cross-validation suites, but graph traversal carries enough of its
own moving parts — ``_from`` / ``_to`` handle resolution, the target
vertex / edge binding, the ``GENERIC_WITH_TYPE`` discriminator FILTER —
that it earns direct unit coverage here. Each case feeds the exact AQL
shape the edge-collection visitor emits (see
``tests/translate/edge_traversal.yml``) so the interpreter cannot drift
from the translator's real output.
"""

from __future__ import annotations

from typing import Any

from tests.helpers.aql_interp import run_aql_subset

EX = "http://ex.org/"


def _person(local: str, name: str) -> dict[str, Any]:
    return {"_id": f"Person/{local}", "_uri": EX + local, "name": name}


def _project(local: str, title: str) -> dict[str, Any]:
    return {"_id": f"Project/{local}", "_uri": EX + local, "title": title}


def _edge(frm: str, to: str, **extra: Any) -> dict[str, Any]:
    return {"_from": frm, "_to": to, **extra}


def test_dedicated_edge_traversal_binds_target_vertex_uri() -> None:
    aql = (
        "FOR doc1 IN @@c1_Person\n"
        "FOR v2, e3 IN OUTBOUND doc1 @@c2_knows\n"
        "RETURN { a: doc1._uri, b: v2._uri }"
    )
    docs = {
        "Person": [_person("alice", "Alice"), _person("bob", "Bob")],
        "knows": [_edge("Person/alice", "Person/bob")],
    }
    rows = run_aql_subset(aql, {"@c1_Person": "Person", "@c2_knows": "knows"}, docs)
    assert rows == [{"a": EX + "alice", "b": EX + "bob"}]


def test_traversal_fans_out_over_multiple_edges() -> None:
    aql = (
        "FOR doc1 IN @@c1_Person\n"
        "FOR v2, e3 IN OUTBOUND doc1 @@c2_knows\n"
        "RETURN { a: doc1._uri, b: v2._uri }"
    )
    docs = {
        "Person": [
            _person("alice", "Alice"),
            _person("bob", "Bob"),
            _person("carol", "Carol"),
        ],
        "knows": [
            _edge("Person/alice", "Person/bob"),
            _edge("Person/alice", "Person/carol"),
        ],
    }
    rows = run_aql_subset(aql, {"@c1_Person": "Person", "@c2_knows": "knows"}, docs)
    assert sorted(r["b"] for r in rows) == [EX + "bob", EX + "carol"]
    assert all(r["a"] == EX + "alice" for r in rows)


def test_generic_edge_traversal_filters_on_discriminator() -> None:
    # GENERIC_WITH_TYPE: one shared edge collection, the predicate is a
    # discriminator on the edge — only matching-type edges count.
    aql = (
        "FOR doc1 IN @@c1_Person\n"
        "FOR v2, e3 IN OUTBOUND doc1 @@c2_rel\n"
        "FILTER e3.type == @_p1_type\n"
        "RETURN { a: doc1._uri, b: v2._uri }"
    )
    docs = {
        "Person": [_person("alice", "Alice"), _person("bob", "Bob")],
        "Project": [_project("p1", "Apollo")],
        "rel": [
            _edge("Person/alice", "Person/bob", type="worksWith"),
            _edge("Person/alice", "Project/p1", type="owns"),
        ],
    }
    rows = run_aql_subset(
        aql,
        {"@c1_Person": "Person", "@c2_rel": "rel", "_p1_type": "worksWith"},
        docs,
    )
    assert rows == [{"a": EX + "alice", "b": EX + "bob"}]


def test_dangling_edge_is_dropped() -> None:
    # An edge whose ``_to`` points at no existing vertex yields no row,
    # matching OUTBOUND skipping endpoints it cannot resolve.
    aql = (
        "FOR doc1 IN @@c1_Person\n"
        "FOR v2, e3 IN OUTBOUND doc1 @@c2_knows\n"
        "RETURN { a: doc1._uri, b: v2._uri }"
    )
    docs = {
        "Person": [_person("alice", "Alice")],
        "knows": [_edge("Person/alice", "Person/ghost")],
    }
    rows = run_aql_subset(aql, {"@c1_Person": "Person", "@c2_knows": "knows"}, docs)
    assert rows == []


def test_start_vertex_with_no_outgoing_edges_yields_nothing() -> None:
    aql = (
        "FOR doc1 IN @@c1_Person\n"
        "FOR v2, e3 IN OUTBOUND doc1 @@c2_knows\n"
        "RETURN { a: doc1._uri, b: v2._uri }"
    )
    docs = {
        "Person": [_person("alice", "Alice"), _person("bob", "Bob")],
        "knows": [_edge("Person/bob", "Person/alice")],
    }
    rows = run_aql_subset(aql, {"@c1_Person": "Person", "@c2_knows": "knows"}, docs)
    assert rows == [{"a": EX + "bob", "b": EX + "alice"}]
