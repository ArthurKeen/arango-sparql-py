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
  — desugared to a UNION of zero-or-more single-path arms (bounded
  by :data:`PROPERTY_PATH_MAX_DEPTH`); see :func:`_emit_mul_path`.
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

from collections.abc import Callable
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

# SPARQL property paths are unbounded; ArangoDB has no single AQL
# construct for "follow document attribute p forever". We desugar
# ``:p*`` / ``:p+`` / ``:p?`` to a UNION of fixed-length paths
# (0..N hops) so each arm reuses the existing SequencePath / edge
# emitters. N is configurable per deployment via
# ``SchemaResolver.property_path_max_depth`` (default below).
PROPERTY_PATH_MAX_DEPTH: int = 10


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
        # Desugar to a UNION of single-triple BGPs — semantics are
        # identical (SPARQL 1.1 §18.4) and we share the two-phase
        # union emitter so the AQL shape matches an explicit
        # ``{ ?s :p ?o } UNION { ?s :q ?o }``. The union helper
        # re-dispatches each arm through ``_emit_triple`` so PG /
        # LPG / default-collection / RPT branches all compose.
        from .union_paths import emit_alternative_path

        emit_alternative_path(visitor, subject, predicate, obj)
        return
    if isinstance(predicate, MulPath):
        _emit_mul_path(visitor, subject, predicate, obj)
        return
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


def _emit_mul_path(
    visitor: AlgebraVisitor,
    subject: Any,
    predicate: MulPath,
    obj: Any,
) -> None:
    """Expand ``:p*`` / ``:p+`` / ``:p?`` to a bounded UNION of path lengths.

    SPARQL 1.1 §18.4 defines the modifiers as repetition of the inner
    path: ``+`` (one or more), ``*`` (zero or more), ``?`` (zero or one).

    We lower each to ``UNION(arm_0, arm_1, …, arm_N)`` where ``arm_k`` is
    the inner path repeated ``k`` times (``arm_0`` for ``*`` / ``?`` is the
    identity binding ``?o ≡ ?s``). Each non-identity arm calls
    :func:`emit_path_triple` (or :meth:`AlgebraVisitor._emit_triple` for a
    plain ``URIRef`` leaf) so PG edge traversals, LPG discriminators, and
    default-collection joins compose without duplicating traversal logic.

    ``N`` is :attr:`SchemaResolver.property_path_max_depth` (default
    :data:`PROPERTY_PATH_MAX_DEPTH`). Paths longer than ``N`` are not matched.
    """
    mod = predicate.mod
    if mod not in ("*", "+", "?"):
        raise UnsupportedSparqlError(
            f"property path modifier {mod!r} is not supported"
        )

    inner = predicate.path
    if isinstance(inner, (AlternativePath, MulPath, NegatedPath)):
        raise UnsupportedSparqlError(
            f"nested property path {type(inner).__name__!r} inside "
            f"MulPath (':p{mod}') is not supported"
        )

    if isinstance(subject, Variable) and str(subject) in visitor.state.var_to_rpt_class:
        raise UnsupportedSparqlError(
            "property paths on RPT-mapped subjects are not yet supported"
        )

    max_depth = visitor.resolver.property_path_max_depth
    if mod == "?":
        min_len, max_len = 0, 1
    elif mod == "+":
        min_len, max_len = 1, max_depth
    else:  # mod == "*"
        min_len, max_len = 0, max_depth

    arm_drivers: list[Callable[[AlgebraVisitor], None]] = []
    if min_len == 0:
        arm_drivers.append(
            lambda v, s=subject, o=obj: _emit_zero_hop_path(v, s, o)
        )
    for length in range(max(1, min_len), max_len + 1):
        expanded = _repeat_inner_path(inner, length)
        arm_drivers.append(
            lambda v, s=subject, p=expanded, o=obj: _emit_expanded_path_arm(
                v, s, p, o
            )
        )

    if len(arm_drivers) == 1:
        arm_drivers[0](visitor)
        return

    from .union_paths import _emit_union_of_arms

    _emit_union_of_arms(visitor, arm_drivers)


def _emit_zero_hop_path(
    visitor: AlgebraVisitor,
    subject: Any,
    obj: Any,
) -> None:
    """Identity arm for ``:p*`` / ``:p?`` — bind ``?o`` to the same URI as ``?s``."""
    subject_alias = visitor._ensure_subject_alias(subject)
    uri_expr = f"{subject_alias}._uri"
    if isinstance(obj, Variable):
        o_name = str(obj)
        existing = visitor.state.var_to_expr.get(o_name)
        if existing is None:
            visitor.state.var_to_expr[o_name] = uri_expr
        elif existing != uri_expr:
            visitor.builder.filter_raw(f"{uri_expr} == {existing}")
        return
    if isinstance(obj, URIRef):
        bind = visitor.builder.bind(str(obj), hint="uri")
        visitor.builder.filter_eq(uri_expr, bind)
        return
    raise UnsupportedSparqlError(
        f"zero-hop property path object term type {type(obj).__name__!r} "
        f"is not supported"
    )


def _emit_expanded_path_arm(
    visitor: AlgebraVisitor,
    subject: Any,
    expanded: Any,
    obj: Any,
) -> None:
    """Emit one UNION arm: a fixed-length expansion of the inner path."""
    if isinstance(expanded, Path):
        emit_path_triple(visitor, subject, expanded, obj)
        return
    if isinstance(expanded, URIRef):
        visitor._emit_triple((subject, expanded, obj))
        return
    raise UnsupportedSparqlError(
        f"repeated path inner type {type(expanded).__name__!r} is not supported"
    )


def _repeat_inner_path(inner: Any, count: int) -> Any:
    """Repeat *inner* *count* times as a path (URIRef or SequencePath)."""
    if count < 1:
        raise ValueError(f"path repeat count must be >= 1, got {count}")
    if isinstance(inner, URIRef):
        if count == 1:
            return inner
        return SequencePath(*([inner] * count))
    if isinstance(inner, SequencePath):
        if count == 1:
            return inner
        parts: list[Any] = []
        for _ in range(count):
            parts.extend(inner.args)
        return SequencePath(*parts)
    if isinstance(inner, InvPath):
        if count == 1:
            return inner
        return SequencePath(*([inner] * count))
    raise UnsupportedSparqlError(
        f"cannot repeat property path inner type {type(inner).__name__!r}"
    )


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
