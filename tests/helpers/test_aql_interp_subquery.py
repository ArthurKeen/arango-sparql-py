"""Unit tests for the interpreter's correlated ``LENGTH((subquery))``.

MINUS / NOT EXISTS / EXISTS all lower to the same probe shape::

    LET <p> = LENGTH((
      FOR doc2 IN @@coll
      FILTER doc2._uri == doc1._uri   # correlated with the outer row
      LIMIT 1
      RETURN 1
    ))
    FILTER <p> {== 0 | > 0}

The probe is *correlated* — the inner FILTER references the outer
document — and projects a scalar (``RETURN 1``). These two shapes are
new to the interpreter, so they get direct coverage here independent of
the translator.
"""

from __future__ import annotations

from typing import Any

from tests.helpers.aql_interp import run_aql_subset

EX = "http://ex.org/"


def _doc(local: str, **attrs: Any) -> dict[str, Any]:
    return {"_uri": EX + local, **attrs}


def test_not_exists_probe_keeps_rows_without_a_match() -> None:
    # MINUS / NOT EXISTS shape: keep outer rows whose probe count == 0.
    aql = (
        "FOR doc1 IN @@c1\n"
        "LET p = LENGTH((\n"
        "  FOR doc2 IN @@c2\n"
        "  FILTER doc2._uri == doc1._uri\n"
        "  FILTER doc2.hidden == @_flag\n"
        "  LIMIT 1\n"
        "  RETURN 1\n"
        "))\n"
        "FILTER p == 0\n"
        "RETURN { s: doc1._uri }"
    )
    docs = {
        "people": [_doc("alice"), _doc("bob"), _doc("carol")],
        "hidden": [_doc("bob", hidden=True)],
    }
    rows = run_aql_subset(aql, {"@c1": "people", "@c2": "hidden", "_flag": True}, docs)
    assert sorted(r["s"] for r in rows) == [EX + "alice", EX + "carol"]


def test_exists_probe_keeps_rows_with_a_match() -> None:
    # EXISTS shape: keep outer rows whose probe count > 0.
    aql = (
        "FOR doc1 IN @@c1\n"
        "LET p = LENGTH((\n"
        "  FOR doc2 IN @@c2\n"
        "  FILTER doc2._uri == doc1._uri\n"
        "  LIMIT 1\n"
        "  RETURN 1\n"
        "))\n"
        "FILTER p > 0\n"
        "RETURN { s: doc1._uri }"
    )
    docs = {
        "people": [_doc("alice"), _doc("bob"), _doc("carol")],
        "flagged": [_doc("bob"), _doc("carol")],
    }
    rows = run_aql_subset(aql, {"@c1": "people", "@c2": "flagged"}, docs)
    assert sorted(r["s"] for r in rows) == [EX + "bob", EX + "carol"]


def test_probe_count_is_capped_by_inner_limit() -> None:
    # Two inner matches but LIMIT 1 caps the count at 1 — the probe only
    # needs existence, and the cap keeps LENGTH ∈ {0, 1}.
    aql = (
        "FOR doc1 IN @@c1\n"
        "LET p = LENGTH((\n"
        "  FOR doc2 IN @@c2\n"
        "  FILTER doc2.owner == doc1._uri\n"
        "  LIMIT 1\n"
        "  RETURN 1\n"
        "))\n"
        "RETURN { s: doc1._uri, n: p }"
    )
    docs = {
        "people": [_doc("alice")],
        "projects": [_doc("p1", owner=EX + "alice"), _doc("p2", owner=EX + "alice")],
    }
    rows = run_aql_subset(aql, {"@c1": "people", "@c2": "projects"}, docs)
    assert rows == [{"s": EX + "alice", "n": 1}]
