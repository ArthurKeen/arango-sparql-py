"""Unit tests for the harness's small pieces: params, compare, oracle guard, mappings."""

from __future__ import annotations

import pyoxigraph as ox
import pytest

from arango_sparql.api import translate
from arango_sparql.errors import SchemaResolutionError
from tests.ldbc import compare, mappings, oracle, params, vocab

pytestmark = pytest.mark.ldbc


# -- params -------------------------------------------------------------------


def test_person_ids_are_padded_like_the_reference_and_message_ids_are_not() -> None:
    assert params.format_value("personId", "933") == "00000000000000000933"
    assert params.format_value("messageId", "1030792359243") == "1030792359243"


def test_dates_are_utc_with_an_explicit_zone() -> None:
    assert params.format_value("maxDate", "1354060800000") == "2012-11-28T00:00:00.000Z"
    assert params.format_value("startDate", "1291161600123") == "2010-12-01T00:00:00.123Z"


def test_splice_requires_every_placeholder_and_refuses_quote_breaking_values() -> None:
    assert params.splice('?p :name "%firstName%"', {"firstName": "Ian"}) == '?p :name "Ian"'
    with pytest.raises(params.ParamsError, match="no value"):
        params.splice("%personId% %firstName%", {"personId": "1"})
    with pytest.raises(params.ParamsError, match="break out"):
        params.format_value("firstName", 'x" } DROP')


# -- compare ------------------------------------------------------------------


def test_total_ordering_compares_row_by_row() -> None:
    a = [{"x": 1}, {"x": 2}]
    assert compare.compare(a, a, ordering="total").ok
    assert not compare.compare(a, list(reversed(a)), ordering="total").ok
    assert compare.compare(a, list(reversed(a)), ordering="none").ok


def test_key_ordering_allows_ties_to_reorder_but_not_keys() -> None:
    expected = [{"k": 2, "v": "a"}, {"k": 2, "v": "b"}, {"k": 1, "v": "c"}]
    tied = [expected[1], expected[0], expected[2]]
    assert compare.compare(expected, tied, ordering="keys", order_keys=["k"]).ok
    wrong = [expected[2], expected[0], expected[1]]
    assert not compare.compare(expected, wrong, ordering="keys", order_keys=["k"]).ok


def test_numbers_fold_and_strings_do_not_and_nulls_are_unbound() -> None:
    assert compare.compare([{"n": 5}], [{"n": 5.0}], ordering="none").ok
    assert not compare.compare([{"n": 5}], [{"n": "5"}], ordering="none").ok
    assert compare.compare([{"n": 5}], [{"n": 5, "o": None}], ordering="none").ok


def test_group_concat_columns_compare_as_sets() -> None:
    assert compare.compare([{"e": "a, b"}], [{"e": "b, a"}], ordering="none", concat_columns=["e"]).ok
    assert not compare.compare([{"e": "a, b"}], [{"e": "b, a"}], ordering="none").ok


def test_row_count_mismatch_is_reported() -> None:
    result = compare.compare([{"x": 1}], [], ordering="none")
    assert not result.ok and "expected 1 rows, got 0" in result.message


# -- oracle guard -------------------------------------------------------------


def test_the_oracle_refuses_service_but_not_a_comment_that_mentions_it() -> None:
    store = ox.Store()
    with pytest.raises(oracle.OracleRefused):
        oracle.run(store, "SELECT * WHERE { SERVICE <http://example.org/s> { ?a ?b ?c } }")
    assert oracle.run(store, "# replaced SERVICE with a UNION\nSELECT * WHERE { ?a ?b ?c }").rows == []


def test_terms_become_the_values_arangodb_returns() -> None:
    assert oracle.term_to_python(ox.NamedNode("http://x/a")) == "http://x/a"
    assert oracle.term_to_python(ox.Literal("7", datatype=ox.NamedNode(vocab.XSD + "long"))) == 7
    assert oracle.term_to_python(
        ox.Literal("2010-01-01T00:00:00.000Z", datatype=ox.NamedNode(vocab.XSD + "dateTime"))
    ) == ("2010-01-01T00:00:00.000Z")


# -- mappings -----------------------------------------------------------------


@pytest.mark.parametrize("layout", ["rpt", "lpg", "pg"])
def test_committed_mapping_matches_the_generator(layout: str) -> None:
    assert mappings.path(layout).read_text() == mappings.render(layout), (  # type: ignore[arg-type]
        "regenerate with python -m tests.ldbc.mappings --write"
    )


@pytest.mark.parametrize("layout", ["lpg", "pg"])
def test_unmappable_relationships_fail_closed_in_pg_and_lpg(layout: str) -> None:
    query = (
        "PREFIX snvoc: <http://www.ldbc.eu/ldbc_socialnet/1.0/vocabulary/> "
        "SELECT ?k WHERE { ?p a snvoc:Person ; snvoc:knows ?k }"
    )
    with pytest.raises(SchemaResolutionError, match="no phys:edgeCollectionName"):
        translate(query, resolver=mappings.resolver(layout))  # type: ignore[arg-type]


@pytest.mark.parametrize("layout", ["rpt", "lpg", "pg"])
def test_a_typed_scan_translates_in_every_layout(layout: str) -> None:
    query = (
        "PREFIX snvoc: <http://www.ldbc.eu/ldbc_socialnet/1.0/vocabulary/> "
        "SELECT ?n WHERE { ?p a snvoc:Person ; snvoc:firstName ?n }"
    )
    assert "FOR" in translate(query, resolver=mappings.resolver(layout)).aql  # type: ignore[arg-type]


# -- execution outcomes --------------------------------------------------------


def _aql_error(error_code: int, status_code: int) -> Exception:
    """A real python-arango AQLQueryExecuteError, built the way the driver builds one."""
    from arango.exceptions import AQLQueryExecuteError
    from arango.request import Request
    from arango.response import Response

    response = Response("post", "http://x/_api/cursor", {}, status_code, "error", "{}")
    response.error_code = error_code
    response.error_message = "boom"
    return AQLQueryExecuteError(response, Request("post", "/_api/cursor"))


def test_execution_failures_are_classified_by_arangodb_error_number() -> None:
    from tests.ldbc import ledger

    assert ledger.execution_status(_aql_error(1500, 410)) == "timeout"
    assert ledger.execution_status(_aql_error(32, 500)) == "resource_limit"
    assert ledger.execution_status(_aql_error(1203, 404)) == "execution_error"


def test_a_lost_connection_is_not_recorded_as_a_query_outcome() -> None:
    from tests.ldbc import ledger

    with pytest.raises(ConnectionAbortedError):
        ledger.execution_status(ConnectionAbortedError("Can't connect to host(s) within limit (3)"))


def test_a_query_the_driver_cannot_send_is_an_execution_error() -> None:
    from tests.ldbc import ledger

    unserialisable = TypeError("Object of type timedelta is not JSON serializable")
    assert ledger.execution_status(unserialisable) == "execution_error"


def test_datetimes_compare_as_instants_not_text() -> None:
    oracle_form = [{"d": "2012-05-29T02:13:38.9Z"}]
    assert compare.compare(oracle_form, [{"d": "2012-05-29T02:13:38.900Z"}], ordering="none").ok
    assert compare.compare(
        [{"d": "2012-05-29T02:13:38Z"}], [{"d": "2012-05-29T03:13:38+01:00"}], ordering="none"
    ).ok
    assert not compare.compare(oracle_form, [{"d": "2012-05-29T02:13:38.901Z"}], ordering="none").ok
    assert compare.compare([{"d": "1986-07-15"}], [{"d": "1986-07-15"}], ordering="none").ok


def test_constant_date_sums_are_folded_before_pyoxigraph_sees_them() -> None:
    folded = oracle.fold_date_arithmetic(
        '?d < "2012-06-01T00:00:00.000Z"^^xsd:dateTime + "P28D"^^xsd:duration && ?x'
    )
    assert folded == '?d < "2012-06-29T00:00:00.000Z"^^xsd:dateTime && ?x'
    # Across a leap day, and untouched when either side is not a constant.
    assert '"2012-03-01T00:00:00.000Z"' in oracle.fold_date_arithmetic(
        '"2012-02-28T00:00:00.000Z"^^xsd:dateTime + "P2D"^^xsd:duration'
    )
    untouched = '?start + "P1D"^^xsd:duration'
    assert oracle.fold_date_arithmetic(untouched) == untouched


def test_the_fold_gives_what_pyoxigraph_should_have() -> None:
    """Where pyoxigraph does compute the sum, it agrees with the fold."""
    sparql = (
        "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> SELECT ?e WHERE { "
        'BIND("2010-12-01T00:00:00.000Z"^^xsd:dateTime + "P31D"^^xsd:duration AS ?e) }'
    )
    raw = oracle.term_to_python(next(iter(ox.Store().query(sparql)))["e"])
    folded = oracle.run(ox.Store(), sparql).rows[0]["e"]
    assert compare.compare([{"e": raw}], [{"e": folded}], ordering="none").ok


def test_an_empty_grouped_result_matches_w3cs_one_empty_row_only_when_grouped() -> None:
    assert compare.compare([], [{"t": None, "c": None}], ordering="none", empty_group_row=True).ok
    assert not compare.compare([], [{"t": None, "c": None}], ordering="none").ok
    assert not compare.compare([], [{"t": "x", "c": None}], ordering="none", empty_group_row=True).ok


def test_a_timeout_is_cached_and_only_a_longer_limit_retries_it(tmp_path, monkeypatch) -> None:
    calls = []

    def slow(nt_path, sha, sparql, timeout_s):  # noqa: ANN001, ANN202 — same signature as run_isolated
        calls.append(timeout_s)
        raise oracle.OracleTimeout(f"no answer within {timeout_s:.0f}s")

    monkeypatch.setattr(oracle, "run_isolated", slow)
    nt = tmp_path / "normalized.nt"
    for limit in (10.0, 5.0):
        with pytest.raises(oracle.OracleTimeout):
            oracle.cached_run(nt, "sha", "SELECT 1", timeout_s=limit)
    assert calls == [10.0]  # the 5 s attempt was answered from the cache
    with pytest.raises(oracle.OracleTimeout):
        oracle.cached_run(nt, "sha", "SELECT 1", timeout_s=20.0)
    assert calls == [10.0, 20.0]


def test_cached_seconds_reads_answers_but_not_timeouts(tmp_path, monkeypatch) -> None:
    """The bench's pyoxigraph time comes from the same cache key the oracle writes."""
    nt = tmp_path / "normalized.nt"
    assert oracle.cached_seconds(nt, "sha", "SELECT 1") is None

    def answer(nt_path, sha, sparql, timeout_s):  # noqa: ANN001, ANN202 — same signature as run_isolated
        return oracle.OracleResult(["x"], [{"x": 1}], 2.5)

    monkeypatch.setattr(oracle, "run_isolated", answer)
    oracle.cached_run(nt, "sha", "SELECT 1", timeout_s=10.0)
    assert oracle.cached_seconds(nt, "sha", "SELECT 1") == 2.5

    def slow(nt_path, sha, sparql, timeout_s):  # noqa: ANN001, ANN202
        raise oracle.OracleTimeout("no answer")

    monkeypatch.setattr(oracle, "run_isolated", slow)
    with pytest.raises(oracle.OracleTimeout):
        oracle.cached_run(nt, "sha", "SELECT 2", timeout_s=10.0)
    assert oracle.cached_seconds(nt, "sha", "SELECT 2") is None


def test_plan_digest_reads_traversal_indexes() -> None:
    """A TraversalNode lists its indexes as ``{"base": [...], "levels": {...}}``."""
    from tests.ldbc import bench

    traversal = {
        "type": "TraversalNode",
        "indexes": {
            "base": [{"type": "edge", "fields": ["_from"]}],
            "levels": {"2": [{"type": "edge", "fields": ["_to"]}]},
        },
    }
    assert bench._node_indexes(traversal) == [
        {"type": "edge", "fields": ["_from"]},
        {"type": "edge", "fields": ["_to"]},
    ]
    assert bench._node_indexes({"type": "IndexNode", "indexes": [{"type": "persistent"}]}) == [
        {"type": "persistent"}
    ]
    assert bench._node_indexes({"type": "SingletonNode"}) == []
