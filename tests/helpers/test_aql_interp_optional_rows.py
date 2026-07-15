"""Unit tests for the interpreter's row-list subquery + ``[null]``-padded
FOR-inline (the cross-subject OPTIONAL shape, ADR-0002 Problem 1).

The cross-subject OPTIONAL emitter lowers to::

    LET <opt> = (
      FOR doc2 IN @@coll
      FILTER doc2.subject_uri == <outer>
      RETURN { f0: doc2.predicate, f1: NOT_NULL(...) }
    )
    FOR <row> IN (LENGTH(<opt>) > 0 ? <opt> : [null])
      RETURN { ... <row>.f0 ... <row>.f1 ... }

Two interpreter capabilities are new and exercised directly here,
independent of the translator:

* ``LET <opt> = ( … RETURN {…} )`` binds the alias to the *list of row
  dicts* (a correlated subquery returning objects, not a count).
* ``FOR <row> IN (LENGTH(<opt>) > 0 ? <opt> : [null])`` iterates that
  list, and the ``[null]`` pad yields exactly one row with ``None``
  field reads when the subquery is empty — the property that makes it a
  LEFT join rather than an INNER join.
"""

from __future__ import annotations

from typing import Any

from tests.helpers.aql_interp import run_aql_subset

EX = "http://ex.org/"

# The cross-subject OPTIONAL idiom, parameterised only by the document
# store so each test below feeds different match cardinalities.
_AQL = (
    "FOR doc1 IN @@c1\n"
    "LET optsub2 = (\n"
    "  FOR doc2 IN @@c2\n"
    "  FILTER doc2.subject_uri == doc1.ref\n"
    "  RETURN {f0: doc2.predicate, f1: NOT_NULL(doc2.object_uri, doc2.object_value)}\n"
    ")\n"
    "FOR optrow3 IN (LENGTH(optsub2) > 0 ? optsub2 : [null])\n"
    "RETURN { s: doc1._uri, p2: optrow3.f0, o2: optrow3.f1 }"
)
_BINDS = {"@c1": "subjects", "@c2": "_triples"}


def test_no_match_pads_with_a_single_null_row() -> None:
    # ``erin`` has no triples → the subquery is empty → the [null] pad
    # keeps the outer row with both optional vars unbound (LEFT join).
    docs: dict[str, list[dict[str, Any]]] = {
        "subjects": [{"_uri": EX + "dave", "ref": EX + "erin"}],
        "_triples": [],
    }
    rows = run_aql_subset(_AQL, _BINDS, docs)
    assert rows == [{"s": EX + "dave", "p2": None, "o2": None}]


def test_single_match_binds_the_optional_vars() -> None:
    docs: dict[str, list[dict[str, Any]]] = {
        "subjects": [{"_uri": EX + "alice", "ref": EX + "carol"}],
        "_triples": [
            {
                "subject_uri": EX + "carol",
                "predicate": EX + "email",
                "object_value": "carol@x.org",
            }
        ],
    }
    rows = run_aql_subset(_AQL, _BINDS, docs)
    assert rows == [{"s": EX + "alice", "p2": EX + "email", "o2": "carol@x.org"}]


def test_multiple_matches_fan_out() -> None:
    # ``bob`` has two triples → two output rows for the one outer row,
    # the correct multiset OPTIONAL semantics. Object IRIs come back via
    # COALESCE's object_uri arm, literals via object_value.
    docs: dict[str, list[dict[str, Any]]] = {
        "subjects": [{"_uri": EX + "alice", "ref": EX + "bob"}],
        "_triples": [
            {"subject_uri": EX + "bob", "predicate": EX + "email", "object_value": "b@x"},
            {"subject_uri": EX + "bob", "predicate": EX + "knows", "object_uri": EX + "carol"},
        ],
    }
    rows = run_aql_subset(_AQL, _BINDS, docs)
    assert sorted((r["p2"], r["o2"]) for r in rows) == [
        (EX + "email", "b@x"),
        (EX + "knows", EX + "carol"),
    ]
