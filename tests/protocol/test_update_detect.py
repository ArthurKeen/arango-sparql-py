"""Tests for :mod:`arango_sparql.service.protocol.update_detect`.

PRD §5.2 mandates ``405 Method Not Allowed`` with ``E_UPDATE_UNSUPPORTED``
when a SPARQL Update operation reaches ``/sparql``. The route layer
trusts :func:`is_sparql_update` to make that determination for any
body that didn't arrive with ``Content-Type: application/sparql-update``,
so this test file exhaustively covers the keyword set.
"""

from __future__ import annotations

import pytest

from arango_sparql.service.protocol.update_detect import (
    UPDATE_KEYWORDS,
    is_sparql_update,
    strip_prologue_and_comments,
)

# ---------------------------------------------------------------------------
# Read queries — all must be classified as NOT-update.
# ---------------------------------------------------------------------------


_READ_QUERIES = [
    "SELECT * WHERE { ?s ?p ?o }",
    "ASK WHERE { ?s ?p ?o }",
    "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }",
    "DESCRIBE <http://ex.org/Alice>",
    "select ?x where { ?x a <http://ex.org/Person> }",  # lowercase
    "SELECT ?x WHERE { ?x <http://ex.org/insertedAt> ?ts }",  # 'insert' is a substring
    'SELECT ?x WHERE { ?x ?p "INSERT INTO foo" }',  # update keyword inside a string literal
    "PREFIX ex: <http://ex.org/> SELECT * WHERE { ?s ex:p ?o }",
    """
        # this is a header comment that mentions INSERT
        PREFIX ex: <http://ex.org/>
        # PREFIX upd: <http://example.org/update>  -- commented out
        SELECT * WHERE { ?s ?p ?o }
    """,
]


@pytest.mark.parametrize("query", _READ_QUERIES, ids=lambda q: q[:30].strip().replace("\n", " "))
def test_read_query_is_not_update(query: str) -> None:
    assert is_sparql_update(query) is False


# ---------------------------------------------------------------------------
# Update queries — every keyword in UPDATE_KEYWORDS must be flagged.
# ---------------------------------------------------------------------------


def _update_fixture_for(keyword: str) -> str:
    """Build the smallest valid-looking SPARQL Update body that
    starts with *keyword*. The detector only scans the leading
    keyword so we don't need full grammatical correctness.
    """

    bodies = {
        "INSERT": "INSERT DATA { <http://ex.org/Alice> a <http://ex.org/Person> }",
        "DELETE": "DELETE DATA { <http://ex.org/Alice> a <http://ex.org/Person> }",
        "LOAD": "LOAD <http://ex.org/data.ttl> INTO GRAPH <http://ex.org/g>",
        "CLEAR": "CLEAR GRAPH <http://ex.org/g>",
        "CREATE": "CREATE GRAPH <http://ex.org/g>",
        "DROP": "DROP GRAPH <http://ex.org/g>",
        "COPY": "COPY GRAPH <http://ex.org/a> TO GRAPH <http://ex.org/b>",
        "MOVE": "MOVE GRAPH <http://ex.org/a> TO GRAPH <http://ex.org/b>",
        "ADD": "ADD GRAPH <http://ex.org/a> TO GRAPH <http://ex.org/b>",
    }
    return bodies[keyword]


@pytest.mark.parametrize("keyword", UPDATE_KEYWORDS)
def test_each_update_keyword_is_flagged(keyword: str) -> None:
    body = _update_fixture_for(keyword)
    assert is_sparql_update(body) is True


@pytest.mark.parametrize("keyword", UPDATE_KEYWORDS)
def test_each_update_keyword_lowercase_is_flagged(keyword: str) -> None:
    body = _update_fixture_for(keyword).lower()
    assert is_sparql_update(body) is True


def test_with_clause_flagged_as_update() -> None:
    """``WITH <iri> DELETE/INSERT … WHERE …`` is the only valid
    leading-WITH form per SPARQL 1.1 §3.1.3 — and it is an Update.
    """

    body = "WITH <http://ex.org/g> DELETE { ?s ?p ?o } INSERT { ?s ?p ?new } WHERE { ?s ?p ?o }"
    assert is_sparql_update(body) is True


def test_update_with_prologue_is_flagged() -> None:
    body = """
        PREFIX ex: <http://ex.org/>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        INSERT DATA { ex:Alice rdf:type ex:Person }
    """
    assert is_sparql_update(body) is True


def test_update_with_comment_before_is_flagged() -> None:
    body = """
        # rationale: bulk-load Alice
        # PREFIX ex: <http://ex.org/>
        PREFIX ex: <http://ex.org/>
        DELETE WHERE { ex:Alice ?p ?o }
    """
    assert is_sparql_update(body) is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "\n\n", "# only a comment\n"],
    ids=["none", "empty", "ws", "newlines", "comment-only"],
)
def test_empty_inputs_are_not_update(value: str | None) -> None:
    """Empty / whitespace-only / comment-only inputs are not
    flagged as Update — the parser will surface ``E_SPARQL_PARSE``
    for those, which is a different status code (400) and a
    different error message.
    """

    assert is_sparql_update(value) is False  # type: ignore[arg-type]


def test_non_string_input_is_not_update() -> None:
    """Defensive: bytes/None/etc. shouldn't crash the detector."""

    assert is_sparql_update(b"INSERT DATA { ... }") is False  # type: ignore[arg-type]
    assert is_sparql_update(None) is False  # type: ignore[arg-type]
    assert is_sparql_update(123) is False  # type: ignore[arg-type]


def test_identifier_prefix_collision_not_flagged() -> None:
    """A query whose leading word *contains* but does not equal an
    update keyword (e.g. ``INSERTABLE``, ``DELETED``) would be
    misclassified by a naïve substring check; the word-boundary
    regex prevents that.
    """

    body = "SELECT ?x WHERE { ?x <http://ex.org/insertable> ?y }"
    assert is_sparql_update(body) is False


# ---------------------------------------------------------------------------
# strip_prologue_and_comments
# ---------------------------------------------------------------------------


def test_strip_removes_single_line_comments() -> None:
    out = strip_prologue_and_comments("# hello\nSELECT * WHERE { ?s ?p ?o }")
    assert out.startswith("SELECT")


def test_strip_removes_multiple_prefix_lines() -> None:
    out = strip_prologue_and_comments(
        """
        PREFIX a: <http://a/>
        PREFIX b: <http://b/>
        BASE <http://base/>
        SELECT * WHERE { ?s ?p ?o }
        """
    )
    assert out.startswith("SELECT")


def test_strip_handles_empty_input() -> None:
    assert strip_prologue_and_comments("") == ""
    assert strip_prologue_and_comments("# only comment") == ""


def test_strip_does_not_eat_hash_inside_iri() -> None:
    """Regression: a naïve ``#[^\\n]*`` comment regex would gobble
    the ``#>`` at the end of a fragment-bearing IRI such as
    ``<http://www.w3.org/2002/07/owl#>``, leaving the rest of the
    query syntactically broken (the ``>`` would be lost). The
    real comment stripper has to skip over IRI references.
    """

    body = "PREFIX owl: <http://www.w3.org/2002/07/owl#>\nSELECT * WHERE { ?s a owl:Class }"
    out = strip_prologue_and_comments(body)
    assert out.startswith("SELECT")
    # The IRI's ``#>`` must survive when present inside the body, too.
    body2 = "SELECT ?x WHERE { ?x a <http://example.org/v#Person> }"
    out2 = strip_prologue_and_comments(body2)
    assert "<http://example.org/v#Person>" in out2


def test_strip_does_not_eat_hash_inside_string_literal() -> None:
    body = 'SELECT ?x WHERE { ?x ?p "foo # bar" }'
    out = strip_prologue_and_comments(body)
    assert '"foo # bar"' in out


def test_update_query_with_hash_in_iri_still_flagged() -> None:
    """End-to-end: the IRI-aware comment stripper keeps Update
    detection working for the common ``PREFIX rdf: <…rdf-syntax-ns#>``
    convention.
    """

    body = (
        "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
        "INSERT DATA { <http://ex/Alice> rdf:type <http://ex/Person> }"
    )
    assert is_sparql_update(body) is True
