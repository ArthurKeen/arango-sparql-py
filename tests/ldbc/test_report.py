"""Unit tests for :mod:`tests.ldbc.report` on a hand-built results document."""

from __future__ import annotations

import pytest

from tests.ldbc import report

pytestmark = pytest.mark.ldbc


def _cell(
    op: str, form: str, layout: str, translation: str, execution: str = "", oracle: str = "", detail: str = ""
):
    return {
        "op": op,
        "form": form,
        "layout": layout,
        "translation": translation,
        "translation_detail": detail if translation != "translates" else "",
        "execution": execution,
        "oracle": oracle,
        "live_detail": detail if translation == "translates" else "",
    }


def _data() -> dict:
    timing = {"n": 3, "p50": 0.0123, "p95": 0.02, "p99": 0.02, "mean": 0.013, "max": 0.02}
    return {
        "environment": {
            "generated_at": "2026-10-24T00:00:00+00:00",
            "scale_factor": "0.1",
            "data_sha256": "ab" * 32,
            "git_sha": "c0ffee" * 7,
            "arangodb_version": "3.12.9",
            "arangodb_engine": "rocksdb",
            "platform": "test",
            "cpu_count": 4,
            "warmup": 1,
            "repeats": 3,
            "aql_max_runtime_s": 120.0,
            "aql_memory_limit": 1 << 30,
        },
        "ledger": [
            _cell("interactive-short-1", "verbatim", "rpt", "translates", "executes", "matches_oracle"),
            _cell(
                "interactive-short-1", "verbatim", "lpg", "fails_closed", detail="E_SCHEMA_RESOLVE x: 'a' ?b"
            ),
            _cell(
                "interactive-short-1", "verbatim", "pg", "fails_closed", detail="E_SCHEMA_RESOLVE x: 'c' ?d"
            ),
            _cell(
                "interactive-complex-2",
                "verbatim",
                "rpt",
                "translates",
                "executes",
                "translates_wrong",
                "diff",
            ),
            _cell("interactive-complex-2", "verbatim", "lpg", "translates", "timeout", "", "slow"),
            _cell("interactive-complex-2", "verbatim", "pg", "out_of_subset", detail="vendor"),
        ],
        "entries": [
            {
                "op": "interactive-short-1",
                "form": "verbatim",
                "layout": "rpt",
                "server": timing,
                "translate_warm": timing,
                "rows": [1, 1, 1],
                "plan": {"full_scans": 0},
                "oracle_seconds": [0.002],
            }
        ],
    }


def test_the_disclaimer_is_always_there() -> None:
    assert report.DISCLAIMER in report.render(_data())


def test_states_and_timings_are_rendered_with_their_outcome() -> None:
    text = report.render(_data())
    assert "| IS1 | verbatim | ✅ correct · 12.3 ms | ⛔ refused | ⛔ refused | 2.0 |" in text
    assert "❌ wrong answer" in text and "⏱ timeout" in text and "— not standard" in text
    assert "| IS1 | verbatim | RDF (triples) | ✅ correct | 12.3 | 12.3 | 20.0 | 1, 1, 1 | 0 |" in text


def test_failure_reasons_are_grouped_without_per_query_names() -> None:
    text = report.render(_data())
    assert "| ⛔ refused: E_SCHEMA_RESOLVE x: '…' ?… | 2: IS1/LPG, IS1/PG |" in text


def test_final_state_prefers_the_earliest_failure() -> None:
    assert report.final_state(_cell("x", "f", "rpt", "fails_closed")) == "fails_closed"
    assert report.final_state(_cell("x", "f", "rpt", "translates", "timeout")) == "timeout"
    assert report.final_state(_cell("x", "f", "rpt", "translates", "executes", "")) == "oracle_unavailable"


def test_comparison_shows_speedups_and_outcome_changes() -> None:
    def live(seconds: list[float], oracle: str) -> dict:
        cell = _cell("interactive-short-1", "verbatim", "rpt", "translates", "executes", oracle)
        return {"cells": [{**cell, "aql_seconds": seconds}]}

    text = report.render_comparison(
        live([1.5, 1.5, 1.5, 0.0], "matches_oracle"),
        live([0.003, 0.003, 0.003, 0.0], "matches_oracle"),
        title="T",
    )
    assert "| IS1 | verbatim | RDF (triples) | 1,500 | 3.0 | 500× | ✅ correct |" in text


def test_comparison_lists_only_cells_in_both_runs_and_the_current_outcome() -> None:
    before = {
        "cells": [
            {
                **_cell(
                    "interactive-short-1", "verbatim", "rpt", "translates", "executes", "translates_wrong"
                ),
                "aql_seconds": [1.0, 0.0],
            }
        ]
    }
    after = {
        "cells": [
            {
                **_cell("interactive-short-1", "verbatim", "rpt", "translates", "executes", "matches_oracle"),
                "aql_seconds": [0.5, 0.0],
            },
            {
                **_cell("interactive-short-1", "verbatim", "pg", "translates", "executes", "matches_oracle"),
                "aql_seconds": [0.1, 0.0],
            },
        ]
    }
    text = report.render_comparison(before, after, title="T")
    assert "| IS1 | verbatim | RDF (triples) | 1,000 | 500 | 2× | ✅ correct |" in text
    assert "| PG |" not in text
    assert "wrong answer" not in text
