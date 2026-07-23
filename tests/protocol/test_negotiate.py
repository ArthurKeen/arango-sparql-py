"""Tests for :mod:`arango_sparql.service.protocol.negotiate`.

Covers PRD §5.2 result-format negotiation rules 1-4:

1. Highest q-value wins.
2. Ties broken by *priority list* order, not header order.
3. ``*/*`` resolves to the priority list's first compatible entry.
4. No-match ⇒ ``None`` (route layer turns this into 406 with
   the supported-list body).
"""

from __future__ import annotations

import pytest

from arango_sparql.service.protocol.negotiate import (
    ASK_PRIORITY,
    CONSTRUCT_PRIORITY,
    SELECT_PRIORITY,
    QueryForm,
    negotiate_media_type,
    parse_accept_header,
    priority_for_form,
    supported_types_for_form,
)

# ---------------------------------------------------------------------------
# Priority lists are stable
# ---------------------------------------------------------------------------


def test_select_priority_list_is_locked_per_prd() -> None:
    """Reordering this list silently changes the default response
    type for ``Accept: */*``. Lock it down so PRD §5.2 stays
    enforceable.
    """

    assert SELECT_PRIORITY == (
        "application/sparql-results+json",
        "application/sparql-results+xml",
        "text/csv",
        "text/tab-separated-values",
    )


def test_ask_priority_matches_select_priority() -> None:
    assert ASK_PRIORITY == SELECT_PRIORITY


def test_construct_priority_list_is_locked() -> None:
    assert CONSTRUCT_PRIORITY == (
        "text/turtle",
        "application/n-triples",
        "application/rdf+xml",
        "application/ld+json",
    )


@pytest.mark.parametrize(
    "form,expected",
    [
        (QueryForm.SELECT, SELECT_PRIORITY),
        (QueryForm.ASK, ASK_PRIORITY),
        (QueryForm.CONSTRUCT, CONSTRUCT_PRIORITY),
        (QueryForm.DESCRIBE, CONSTRUCT_PRIORITY),
    ],
)
def test_priority_for_form_dispatches_correctly(form: QueryForm, expected: tuple[str, ...]) -> None:
    assert priority_for_form(form) == expected


def test_supported_types_for_form_returns_list_copy() -> None:
    """``supported_types_for_form`` returns a *list* the route
    layer can include in a 406 body. Mutating the returned list
    must not affect the canonical priority tuple.
    """

    out = supported_types_for_form(QueryForm.SELECT)
    assert out == list(SELECT_PRIORITY)
    out.append("evil/type")
    assert "evil/type" not in SELECT_PRIORITY


# ---------------------------------------------------------------------------
# parse_accept_header
# ---------------------------------------------------------------------------


def test_parse_empty_accept_returns_wildcard() -> None:
    """No header / empty header / whitespace-only header all
    resolve to ``*/*`` so the negotiator falls through to the
    priority list.
    """

    for accept in [None, "", "   "]:
        offers = parse_accept_header(accept)
        assert len(offers) == 1
        assert offers[0].media_type == "*/*"
        assert offers[0].q == 1.0


def test_parse_default_q_is_one() -> None:
    offers = parse_accept_header("application/sparql-results+json")
    assert len(offers) == 1
    assert offers[0].q == 1.0


def test_parse_explicit_q_value() -> None:
    offers = parse_accept_header("text/csv;q=0.5, application/sparql-results+json;q=0.9")
    # Sorted by descending q.
    assert offers[0].media_type == "application/sparql-results+json"
    assert offers[0].q == 0.9
    assert offers[1].media_type == "text/csv"
    assert offers[1].q == 0.5


def test_parse_filters_q_zero() -> None:
    """RFC 9110 §12.5.1: ``q=0`` means "not acceptable"."""

    offers = parse_accept_header("text/csv;q=0, application/sparql-results+json")
    assert len(offers) == 1
    assert offers[0].media_type == "application/sparql-results+json"


def test_parse_clamps_out_of_range_q() -> None:
    offers = parse_accept_header("text/csv;q=2.5, application/sparql-results+json;q=-0.1")
    # ``q=-0.1`` ⇒ clamped to 0 ⇒ filtered out.
    # ``q=2.5`` ⇒ clamped to 1.0 ⇒ kept.
    assert len(offers) == 1
    assert offers[0].media_type == "text/csv"
    assert offers[0].q == 1.0


def test_parse_malformed_q_treated_as_one() -> None:
    """RFC 9110 says a recipient MAY treat malformed q as 1.0;
    we do that to avoid silently dropping reasonable requests.
    """

    offers = parse_accept_header("text/csv;q=banana")
    assert len(offers) == 1
    assert offers[0].q == 1.0


def test_parse_lowercases_media_type() -> None:
    """Media types are case-insensitive per RFC 9110 §8.3."""

    offers = parse_accept_header("APPLICATION/SPARQL-Results+JSON")
    assert offers[0].media_type == "application/sparql-results+json"


def test_parse_drops_empty_chunks() -> None:
    """Trailing commas and double commas appear in the wild —
    ignore them rather than crash.
    """

    offers = parse_accept_header(",text/csv,,application/sparql-results+json,,")
    assert {o.media_type for o in offers} == {
        "text/csv",
        "application/sparql-results+json",
    }


def test_parse_ignores_unknown_parameters() -> None:
    """``charset=`` / ``level=`` are ignored — the negotiator only
    cares about q-value.
    """

    offers = parse_accept_header("text/csv;charset=utf-8;q=0.7;level=1")
    assert offers[0].media_type == "text/csv"
    assert offers[0].q == 0.7


# ---------------------------------------------------------------------------
# negotiate_media_type — happy path
# ---------------------------------------------------------------------------


def test_negotiate_explicit_match() -> None:
    chosen, _ = negotiate_media_type("application/sparql-results+xml", QueryForm.SELECT)
    assert chosen == "application/sparql-results+xml"


def test_negotiate_no_accept_returns_first_priority() -> None:
    """Empty header ⇒ ``*/*`` ⇒ priority list's first entry."""

    for accept in [None, "", "*/*"]:
        chosen, _ = negotiate_media_type(accept, QueryForm.SELECT)
        assert chosen == SELECT_PRIORITY[0]


def test_negotiate_top_level_wildcard_matches_priority_first() -> None:
    """``application/*`` matches the first ``application/...``
    entry in the priority list.
    """

    chosen, _ = negotiate_media_type("application/*", QueryForm.SELECT)
    assert chosen == "application/sparql-results+json"


def test_negotiate_top_level_wildcard_text() -> None:
    chosen, _ = negotiate_media_type("text/*", QueryForm.SELECT)
    # Both ``text/csv`` and ``text/tab-separated-values`` start with
    # ``text/`` — priority order picks ``text/csv``.
    assert chosen == "text/csv"


# ---------------------------------------------------------------------------
# negotiate_media_type — PRD §5.2 rule 2 (priority-list tie-breaking)
# ---------------------------------------------------------------------------


def test_tie_breaking_uses_priority_list_order_not_header_order() -> None:
    """Header lists CSV first then XML; q-values tie. The priority
    list ranks XML above CSV, so XML wins. This is the canonical
    test from PRD §5.2.
    """

    chosen, _ = negotiate_media_type(
        "text/csv;q=0.9,application/sparql-results+xml;q=0.9",
        QueryForm.SELECT,
    )
    assert chosen == "application/sparql-results+xml"


def test_tie_breaking_priority_holds_with_three_way_tie() -> None:
    chosen, _ = negotiate_media_type(
        ("text/tab-separated-values;q=0.5,text/csv;q=0.5,application/sparql-results+xml;q=0.5"),
        QueryForm.SELECT,
    )
    # XML is earliest of the three in the priority list.
    assert chosen == "application/sparql-results+xml"


def test_higher_q_beats_priority_list() -> None:
    """A higher-q offer always beats a higher-priority one — q-value
    dominates priority. PRD §5.2 rule 1.
    """

    chosen, _ = negotiate_media_type(
        "application/sparql-results+json;q=0.5,text/csv;q=0.9",
        QueryForm.SELECT,
    )
    assert chosen == "text/csv"


# ---------------------------------------------------------------------------
# negotiate_media_type — no match (PRD §5.2 rule 4)
# ---------------------------------------------------------------------------


def test_no_match_returns_none() -> None:
    """No supported type matches ⇒ ``None``; the route layer turns
    that into 406 with the supported-list body.
    """

    chosen, offers = negotiate_media_type("image/png, application/pdf", QueryForm.SELECT)
    assert chosen is None
    # Offers are still returned so the route can include them in
    # diagnostics.
    assert {o.media_type for o in offers} == {"image/png", "application/pdf"}


def test_no_match_for_construct_when_only_select_offered() -> None:
    """``application/sparql-results+json`` is not an RDF format,
    so a CONSTRUCT request that *only* offers it should 406.
    """

    chosen, _ = negotiate_media_type("application/sparql-results+json", QueryForm.CONSTRUCT)
    assert chosen is None


# ---------------------------------------------------------------------------
# Form-specific defaults
# ---------------------------------------------------------------------------


def test_construct_default_is_turtle() -> None:
    chosen, _ = negotiate_media_type("*/*", QueryForm.CONSTRUCT)
    assert chosen == "text/turtle"


def test_describe_default_is_turtle() -> None:
    chosen, _ = negotiate_media_type(None, QueryForm.DESCRIBE)
    assert chosen == "text/turtle"


def test_ask_default_is_results_json() -> None:
    chosen, _ = negotiate_media_type(None, QueryForm.ASK)
    assert chosen == "application/sparql-results+json"
