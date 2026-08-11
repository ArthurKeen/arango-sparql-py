"""Regression tests for W3C result-format dispatch."""

from __future__ import annotations

import pytest

from tests.w3c.srx_parser import (
    UnsupportedResultFormat,
    compare_graph,
    compare_select,
    parse_results_file,
)


def test_construct_graph_turtle_is_parsed_as_rdf_graph() -> None:
    from tests.w3c.test_w3c_live_execution import _LIVE_CASES

    case = next(case for case in _LIVE_CASES if case.short_id == "construct/constructwhere04")
    assert case.expected_path is not None

    result = parse_results_file(case.expected_path)

    assert result.is_graph
    assert result.graph is not None
    assert len(result.graph) > 0

    actual_rows = [
        [
            {
                "subject": str(subject),
                "predicate": str(predicate),
                "object": obj.toPython() if hasattr(obj, "toPython") else str(obj),
            }
            for subject, predicate, obj in result.graph
        ]
    ]
    assert compare_graph(result.graph, actual_rows) == (True, "")


def test_tsv_results_preserve_rdf_terms() -> None:
    from tests.w3c.test_w3c_live_execution import _LIVE_CASES

    case = next(case for case in _LIVE_CASES if case.short_id == "csv-tsv-res/tsv01")
    assert case.expected_path is not None

    result = parse_results_file(case.expected_path)

    assert result.variables == ["s", "p", "o"]
    assert result.rows is not None
    assert result.rows[0] == {
        "s": "http://example.org/s1",
        "p": "http://example.org/p1",
        "o": "http://example.org/s2",
    }
    assert result.rows[3]["o"] == 4
    assert result.rows[4]["o"] == 5.5
    assert str(result.rows[5]["o"]).startswith("_:bnode#")


def test_select_comparison_tolerates_binary_float_noise() -> None:
    assert compare_select(
        [{"sum": 11.1}],
        [{"sum": 11.100000000000001}],
    ) == (True, "")


def test_select_comparison_rejects_meaningful_numeric_difference() -> None:
    ok, _message = compare_select(
        [{"sum": 11.1}],
        [{"sum": 11.1001}],
    )
    assert not ok


def test_select_comparison_treats_blank_nodes_isomorphically() -> None:
    """W3C expected labels (``_:bnode#b0``) must match AQL Skolem hashes."""
    assert compare_select(
        [{"s": "http://example.org/s6", "o": "_:bnode#b0"}],
        [{"s": "http://example.org/s6", "o": "_:eab485a54865c0def6e7be37d9204353"}],
    ) == (True, "")


def test_select_comparison_preserves_blank_node_coreference() -> None:
    """Two vars bound to the same bnode stay equal after remapping."""
    assert compare_select(
        [{"a": "_:bnode#x", "b": "_:bnode#x"}],
        [{"a": "_:same-hash", "b": "_:same-hash"}],
    ) == (True, "")
    ok, _message = compare_select(
        [{"a": "_:bnode#x", "b": "_:bnode#x"}],
        [{"a": "_:hash-a", "b": "_:hash-b"}],
    )
    assert not ok
