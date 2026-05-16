"""Property-path expansion (SPARQL 1.1 §9 / §18.4).

The Algebra visitor's main triple dispatcher
(:meth:`arango_sparql.translate.visitor.AlgebraVisitor._emit_triple`)
treats the ``predicate`` slot of an rdflib triple as either
``URIRef`` (the common ``?s :name ?n`` shape) or ``Variable`` (the
``?s ?p ?o`` cross-collection-UNION case). Property paths
(``rdflib.paths.Path`` instances — ``SequencePath`` / ``InvPath`` /
``AlternativePath`` / ``MulPath`` / ``NegatedPath``) need a separate,
recursive expansion pass before they can reach the main dispatch
table.

Unlike most of the translation work in this package, property paths
are **not** ported from the legacy ``arango-sparql`` Foxx service.
That project's ``pgt-translator.js`` / ``rpt-translator.js`` files
never branch on path types — the legacy translator silently rejects
any predicate that isn't a fixed IRI. So this module is a
green-field implementation against the W3C SPARQL 1.1 spec (§18.4
"SPARQL Algebra", "Property Paths"), cross-validated against
``pyoxigraph`` as the reference triplestore via ``tests/cross/``.
Because there is no legacy code to mirror, every non-obvious
rewriting choice is documented inline so future readers can match
the spec line-by-line.

Slices land incrementally; this module currently implements the two
that are pure desugarings (no new builder or resolver work):

* :func:`emit_path_triple` — public dispatcher; called from
  :meth:`AlgebraVisitor._emit_triple` when the predicate slot is a
  ``Path`` instance.
* :func:`_emit_sequence_path` — ``?s :p/:q/.../:pN ?o`` expands to
  N triples joined by N-1 fresh intermediate variables, each of
  which is re-dispatched through ``_emit_triple`` so PG / LPG /
  default-collection branches all compose naturally.
* :func:`_emit_inverse_path` — ``?s ^path ?o`` swaps subject and
  object and recurses on the inner path (which may itself be a
  ``Path``, e.g. ``^(:p/:q)`` is an inverse-of-sequence).

The remaining three path operators raise
:class:`~arango_sparql.errors.UnsupportedSparqlError` with stable,
operator-grep-friendly messages so XFAIL buckets in the W3C
coverage report stay clean and the next slice has an obvious target:

* :class:`rdflib.paths.AlternativePath` (``:p|:q``)
  — needs a Union-shaped rewrite or per-row predicate-set FILTER;
  deferred until the visitor learns to inject Union sub-patterns
  outside the visit_Union path.
* :class:`rdflib.paths.MulPath` (``:p*`` / ``:p+`` / ``:p?``)
  — needs ArangoDB graph traversal (``FOR v IN min..max OUTBOUND``);
  largest single remaining property-path bucket in the W3C corpus.
* :class:`rdflib.paths.NegatedPath` (``!:p``)
  — needs a "predicate-not-in" filter; the LPG/PG shape requires
  enumerating every property, which is exponential. RPT-only
  implementation might land first.

Intermediate variables minted by sequence expansion use the sigil
``_path_<n>`` so they cannot collide with user-supplied variable
names (every variable that's ever shown in projection / SELECT
output came from the user, and ``_path_*`` is never emitted into a
projection). rdflib's ``Variable`` accepts leading underscores at
construction; the existing visitor never minted underscore-prefixed
variables before this commit so the sigil is private by
construction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rdflib import URIRef, Variable
from rdflib.paths import (
    AlternativePath,
    InvPath,
    MulPath,
    NegatedPath,
    Path,
    SequencePath,
)

from ..errors import UnsupportedSparqlError

if TYPE_CHECKING:
    from .visitor import AlgebraVisitor


def emit_path_triple(
    visitor: AlgebraVisitor,
    subject: Any,
    predicate: Path,
    obj: Any,
) -> None:
    """Dispatch a triple whose ``predicate`` slot is a ``Path``.

    Routes to the per-type expander. Unknown path types raise
    :class:`UnsupportedSparqlError` carrying the path class name so
    the operator can grep ``UnsupportedSparql:`` in the W3C XFAIL
    report and find the bucket without re-reading rdflib's source.

    Path instances can nest arbitrarily — ``^(:p/:q)`` is an
    ``InvPath`` whose ``.arg`` is a ``SequencePath``; ``:p+/:q*`` is
    a ``SequencePath`` whose ``.args`` are two ``MulPath`` — so each
    expander recurses through :meth:`AlgebraVisitor._emit_triple`,
    which re-enters this dispatcher when it encounters another
    ``Path`` in the predicate slot. That keeps the rewrite rules
    flat and inspectable.
    """

    if isinstance(predicate, SequencePath):
        _emit_sequence_path(visitor, subject, predicate, obj)
        return
    if isinstance(predicate, InvPath):
        _emit_inverse_path(visitor, subject, predicate, obj)
        return
    if isinstance(predicate, AlternativePath):
        raise UnsupportedSparqlError(
            "alternative property paths (':p|:q') are not yet supported"
        )
    if isinstance(predicate, MulPath):
        raise UnsupportedSparqlError(
            f"transitive property paths (':p{predicate.mod}') are not yet supported"
        )
    if isinstance(predicate, NegatedPath):
        raise UnsupportedSparqlError(
            "negated property paths ('!:p') are not yet supported"
        )
    raise UnsupportedSparqlError(
        f"property path type {type(predicate).__name__!r} is not supported"
    )


def _emit_sequence_path(
    visitor: AlgebraVisitor,
    subject: Any,
    predicate: SequencePath,
    obj: Any,
) -> None:
    """Expand ``?s :p1/:p2/.../:pN ?o`` to N triples joined by N-1 fresh vars.

    Per SPARQL 1.1 §18.4, the SequencePath rewrite is purely
    syntactic::

        ?s p1/p2/p3 ?o   ≡   ?s p1 ?_path_1 .
                              ?_path_1 p2 ?_path_2 .
                              ?_path_2 p3 ?o .

    We mint a fresh intermediate variable per inner join point
    (N-1 of them) and dispatch each (s, p_i, o_i) fragment back
    through :meth:`AlgebraVisitor._emit_triple`. The visitor's
    existing per-triple cases handle PG (``?s :name ?n``), LPG
    (``object_property`` edges), and the default-collection
    fallback the W3C harness relies on — no new code paths are
    introduced.

    RPT subjects are rejected with a typed error: an RPT-mapped
    subject's intermediate variables need to *also* be RPT-bound
    so each step reads from the triples table, which requires
    propagating :class:`~arango_sparql.translate.resolver.ResolvedClass`
    through ``state.var_to_rpt_class``. The plumbing is straight-
    forward but adds enough surface area to deserve its own slice;
    until then, RPT + property-paths surfaces a clean
    ``E_UNSUPPORTED_NODE``-class error rather than silently
    emitting wrong AQL (cf. ``.cursor/rules/comprehensiveness-over-
    simplification.mdc`` — "no swallowed errors").
    """

    steps = list(predicate.args)
    if len(steps) < 2:
        # Defensive: rdflib should always synthesise a SequencePath
        # with ≥ 2 steps (a single-step "sequence" would collapse to
        # its sole element at parse time). If a future rdflib version
        # ever hands us a degenerate one-step or zero-step sequence,
        # fall back to either the single-step recurse or a typed
        # error rather than crash with an IndexError downstream.
        if len(steps) == 1:
            visitor._emit_triple((subject, steps[0], obj))
            return
        raise UnsupportedSparqlError(
            "SequencePath has no steps; expected at least one"
        )
    if isinstance(subject, Variable) and str(subject) in visitor.state.var_to_rpt_class:
        raise UnsupportedSparqlError(
            "property paths on RPT-mapped subjects are not yet supported"
        )

    current_subject: Any = subject
    for index, step in enumerate(steps):
        is_last = index == len(steps) - 1
        current_object: Any = obj if is_last else visitor._fresh_path_var()
        visitor._emit_triple((current_subject, step, current_object))
        current_subject = current_object


def _emit_inverse_path(
    visitor: AlgebraVisitor,
    subject: Any,
    predicate: InvPath,
    obj: Any,
) -> None:
    """Expand ``?s ^path ?o`` to ``?o path ?s``.

    SPARQL 1.1 §18.4 specifies ``^path`` as the inverse — the same
    set of bindings as ``path``, but with subject and object swapped.
    The rewrite is one line; we recurse via
    :meth:`AlgebraVisitor._emit_triple` so a nested path (e.g.
    ``^(:p/:q)`` whose ``.arg`` is a ``SequencePath``) re-enters the
    dispatcher at the appropriate handler.

    Note: the inner path can be a ``URIRef`` (the common case,
    ``^:knows``) or another ``Path`` instance (``^(:p/:q)`` is an
    ``InvPath`` of a ``SequencePath``). We don't branch on the inner
    type here — the visitor's case-analysis does that on re-entry.
    """

    inner: Any = predicate.arg
    visitor._emit_triple((obj, inner, subject))


# ---------------------------------------------------------------------------
# Re-exports for downstream callers (the visitor only ever imports
# ``emit_path_triple``; the other names are exported so tests and any
# future inline-expansion site can short-circuit a known case without
# round-tripping through the dispatch).
# ---------------------------------------------------------------------------

__all__ = [
    "emit_path_triple",
    # Useful for isinstance checks in tests / introspection.
    "Path",
    "SequencePath",
    "InvPath",
    "AlternativePath",
    "MulPath",
    "NegatedPath",
]


# ``URIRef`` is imported for the type hints above; rdflib's static
# analyzer flags unused imports otherwise. The runtime cost is nil
# since rdflib is already loaded by the visitor.
_URIREF_FOR_TYPING = URIRef
