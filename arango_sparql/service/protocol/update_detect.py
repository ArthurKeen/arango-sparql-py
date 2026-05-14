"""Detect SPARQL 1.1 Update operations in a request body (PRD §5.2).

The protocol endpoint is read-only in v1.x. SPARQL Update operations
(``INSERT`` / ``DELETE`` / ``LOAD`` / ``CLEAR`` / ``CREATE`` / ``DROP``
/ ``COPY`` / ``MOVE`` / ``ADD``) must surface as ``405 Method Not
Allowed`` with the typed code ``E_UPDATE_UNSUPPORTED`` so spec-
compliant clients (Apache Jena ``arq``, rdflib's ``SPARQLWrapper``)
can fail loudly rather than silently no-op.

Two reject conditions trigger a 405 (PRD §5.2):

1. The request's ``Content-Type`` is exactly
   ``application/sparql-update`` — handled at the route layer (the
   header is authoritative; we don't need to parse the body).
2. The body parses as an Update form *despite* arriving via
   ``application/sparql-query`` or ``application/x-www-form-
   urlencoded`` — handled by :func:`is_sparql_update`.

We intentionally do *not* call ``rdflib.plugins.sparql.parser.parseUpdate``
to make the determination — it's significantly slower than a leading-
keyword scan and would add a second parse pass to every read query.
Instead, we strip comments and the SPARQL prologue (``BASE`` / ``PREFIX``
declarations) and inspect the first remaining keyword. False positives
are ruled out by requiring a word boundary after the keyword.

False-negative analysis: a query with ``INSERT`` appearing only inside
a string literal or a comment will *not* be flagged — those don't
appear as the leading keyword. Conversely, a query whose body is
literally the keyword ``INSERT { … } WHERE { … }`` *will* be flagged
because that *is* an Update operation.
"""

from __future__ import annotations

import re

__all__ = [
    "UPDATE_KEYWORDS",
    "is_sparql_update",
    "strip_prologue_and_comments",
]


# All SPARQL 1.1 Update keywords. Ordered by spec section (15.x) for
# easier cross-reference; the order is not significant at runtime.
# The set is exported as a tuple so callers can iterate but not mutate.
UPDATE_KEYWORDS: tuple[str, ...] = (
    "INSERT",  # INSERT DATA, INSERT { … } WHERE { … }
    "DELETE",  # DELETE DATA, DELETE { … } WHERE { … }, DELETE WHERE { … }
    "LOAD",    # LOAD <iri> INTO GRAPH <iri>
    "CLEAR",   # CLEAR { GRAPH <iri> | DEFAULT | NAMED | ALL }
    "CREATE",  # CREATE GRAPH <iri>
    "DROP",    # DROP GRAPH <iri>
    "COPY",    # COPY <src> TO <dst>
    "MOVE",    # MOVE <src> TO <dst>
    "ADD",     # ADD <src> TO <dst>
)


# Build the keyword-detection regex once at import time. The ``\b``
# anchors guarantee we don't false-positive on identifier prefixes
# (e.g. a property called ``insertedAt``). Case-insensitive because
# SPARQL keywords are case-insensitive per §4.1.
_UPDATE_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(UPDATE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


# Single-line ``# …`` comments — the only comment form SPARQL 1.1
# supports per §4.1.3. Multiline comments do *not* exist in SPARQL.
#
# A naïve ``#[^\n]*`` regex would also gobble the ``#>`` at the end
# of an IRI such as ``<http://www.w3.org/2002/07/owl#>``, so the
# regex must skip over IRI references and string literals before
# considering a ``#`` to be a comment opener. We do that with one
# alternation regex + a substitution function that preserves the
# IRI / string slots verbatim and erases only the comment slot.
_COMMENT_OR_LITERAL_RE = re.compile(
    r"""
    (<[^>]*>)                       # IRI reference  (group 1, kept)
    | ("(?:[^"\\]|\\.)*")           # double-quoted literal (group 2, kept)
    | ('(?:[^'\\]|\\.)*')           # single-quoted literal (group 3, kept)
    | (\#[^\n]*)                    # ``# …`` comment       (group 4, erased)
    """,
    re.VERBOSE,
)


def _strip_comments(text: str) -> str:
    """Remove SPARQL comments without disturbing IRI references or
    string literals (which can legitimately contain ``#``).
    """

    def _swap(match: re.Match[str]) -> str:
        if match.group(4) is not None:
            return ""
        return match.group(0)

    return _COMMENT_OR_LITERAL_RE.sub(_swap, text)

# Prologue declarations — ``BASE <iri>`` and ``PREFIX prefix: <iri>``.
# Both are case-insensitive per the grammar. The prologue can appear
# only at the *start* of a query (before any other keyword), so we
# strip it iteratively from the beginning until it stops matching.
_PROLOGUE_RE = re.compile(
    r"^\s*(?:BASE\s+<[^>]*>|PREFIX\s+[A-Za-z_][A-Za-z0-9_.-]*\s*:\s*<[^>]*>)",
    re.IGNORECASE,
)


def strip_prologue_and_comments(query: str) -> str:
    """Return *query* with comments removed and prologue (``BASE`` /
    ``PREFIX``) stripped from the front.

    The result starts with the first non-prologue keyword (or is
    empty if the query is entirely prologue + whitespace).

    Comments are removed *before* prologue stripping so that a
    commented-out ``# PREFIX foo: …`` is not interpreted as a
    prologue line. The opposite order would leave ``\\n`` separators
    behind that would still parse correctly, but eagerly stripping
    comments first matches what an actual SPARQL parser would see.
    """

    if not query:
        return ""

    cleaned = _strip_comments(query)

    # Iterate so multiple ``PREFIX`` lines are all consumed; the
    # regex anchors at ``^`` so each match peels off one prologue
    # entry at a time. Bounded by the length of *cleaned* to
    # guarantee termination on pathological input.
    max_iters = max(1, len(cleaned))
    for _ in range(max_iters):
        match = _PROLOGUE_RE.match(cleaned)
        if not match:
            break
        cleaned = cleaned[match.end():]

    return cleaned.lstrip()


def is_sparql_update(query: str) -> bool:
    """``True`` when *query*'s leading keyword is a SPARQL Update
    operation (``INSERT`` / ``DELETE`` / etc.).

    The check inspects only the leading word — anything inside a
    ``WHERE`` clause or a string literal is irrelevant. ``False``
    is returned for ``None`` / empty / whitespace-only / comment-
    only inputs because those are not Update queries (they're not
    valid queries at all, but the parser will surface that as
    ``E_SPARQL_PARSE`` — which is a different error code with a
    different status code, so we leave that determination to the
    parser).
    """

    if not isinstance(query, str):
        return False

    body = strip_prologue_and_comments(query)
    if not body:
        return False

    # The first whitespace-delimited token is the operation keyword.
    # ``WITH <iri> DELETE { … } INSERT { … } WHERE { … }`` is a
    # valid Update form per §3.1.3, so ``WITH`` must also flag —
    # but the spec defines ``WITH`` as a *modifier* on a following
    # ``DELETE`` / ``INSERT`` so the keyword regex below matches
    # the inner DELETE/INSERT regardless of leading WITH. Belt and
    # braces: we also detect leading ``WITH`` directly.
    leading = body.split(None, 1)[0]
    if leading.upper() == "WITH":
        # ``WITH <iri> DELETE/INSERT …`` is the only valid use of
        # leading WITH per the grammar; if the *rest* of the body
        # contains an Update keyword anywhere in its first 256
        # chars, it's an Update form.
        rest = body[len("WITH"):]
        return bool(_UPDATE_KEYWORD_RE.search(rest[:256]))

    return leading.upper() in UPDATE_KEYWORDS
