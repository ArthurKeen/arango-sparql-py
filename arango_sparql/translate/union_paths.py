"""SPARQL 1.1 ``UNION`` and ``AlternativePath`` (``:p|:q``) emission.

Both constructs lower to the same AQL recipe: translate each arm in
its own child builder, project every arm's result to the common
union-schema (vars not bound in an arm get ``null``), then iterate
the combined arms with ``FOR <row> IN UNION((arm1), (arm2), …)``.
Each row of the UNION carries a uniform ``{var: value, ...}`` shape
so the outer scope can reference any variable from the union schema
via ``row.<var>`` regardless of which arm produced the row.

Why share the recipe:

* SPARQL ``AlternativePath`` is defined in §18.4 as the union of
  per-arm single-triple BGP solutions — the rewrite is literal:
  ``?s (:p|:q) ?o ≡ UNION({?s :p ?o}, {?s :q ?o})``. Emitting it
  through the same helper as ``Union`` keeps the two cases
  semantically and operationally aligned.
* AQL's ``UNION`` takes N array expressions and returns their
  concatenation. Each arm's child-builder block, wrapped in
  parentheses, is exactly such an expression.

Two-phase translation:

1. **Probe** every arm in a throwaway child to discover which
   variables it binds. Throwaway because we need the *union* of
   per-arm vars BEFORE we can decide each arm's RETURN schema. The
   probe uses :meth:`AqlQueryBuilder.create_child` but never calls
   :meth:`absorb_child`, so the parent's counters don't advance —
   the second-phase children re-spawn with the same seeded
   counters and mint the same aliases (which is harmless because
   the first-phase counterparts were discarded).
2. **Emit** every arm a second time, this time projecting the
   full union schema (each arm RETURNs ``{v1: expr_or_null,
   v2: expr_or_null, …}``). Absorb each into the parent in order;
   the parent's counters advance per absorption so the second
   arm's aliases are disjoint from the first's. Finally emit
   ``FOR <row> IN UNION((arm1_aql), (arm2_aql), …)`` in the
   outer scope and bind ``row.<var>`` into ``var_to_expr`` (with
   equality FILTERs for shared variables, matching the
   ``ToMultiSet`` / ``MINUS`` recipe).

Outer scope variable pre-seeding: each child receives a copy of
the outer's ``var_to_expr`` so a UNION nested inside a BGP that
has outer-bound ``?s`` joins each arm's local ``?s`` to the
outer's expression (via the BGP emitter's existing shared-variable
FILTER recipe). This mirrors how :mod:`minus_exists` propagates
the outer scope into MINUS / EXISTS probes.

Legacy reference: ``references/arango-sparql/src/lib/
pgt-translator.js`` has no ``visit_Union`` and no
``AlternativePath`` handler — both return ``UnsupportedSparqlError``
in the legacy Foxx service. This module is a green-field
implementation against the SPARQL 1.1 spec (§18.5 ``Union``,
§18.4 property paths), cross-validated against pyoxigraph via
``tests/cross/`` once the W3C harness lands the live-execution
runs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rdflib.paths import AlternativePath

if TYPE_CHECKING:
    from .visitor import AlgebraVisitor


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


def emit_union(visitor: AlgebraVisitor, node: Any) -> None:
    """``Union(p1, p2)`` — bag-union of two patterns' binding sets.

    Each arm becomes a child-builder sub-query whose RETURN
    projects every variable in the combined union schema (vars not
    bound in this arm get ``null``). The outer scope iterates the
    AQL ``UNION(…)`` and binds each union variable to ``row.<var>``.
    """
    arms = [node.p1, node.p2]
    drivers = [(lambda v, arm=arm: v.visit(arm)) for arm in arms]
    _emit_union_of_arms(visitor, drivers)


def emit_alternative_path(
    visitor: AlgebraVisitor,
    subject: Any,
    predicate: AlternativePath,
    obj: Any,
) -> None:
    """``?s (:p|:q|…) ?o`` — desugar to a UNION of single-triple BGPs.

    Each arm is a single triple ``?s :p_i ?o`` re-dispatched
    through :meth:`AlgebraVisitor._emit_triple` so PG / LPG /
    default-collection / RPT branches all compose without
    duplicating per-arm logic here. The same
    :func:`_emit_union_of_arms` helper that ``visit_Union`` uses
    drives the two-phase translation, so the AQL shape is
    byte-for-byte identical to a hand-written
    ``{ ?s :p ?o } UNION { ?s :q ?o }``.
    """
    arms = list(predicate.args)
    drivers = [
        (
            lambda v, arm=arm: v._emit_triple(  # noqa: SLF001 - intentional
                (subject, arm, obj)
            )
        )
        for arm in arms
    ]
    _emit_union_of_arms(visitor, drivers)


# ---------------------------------------------------------------------------
# Shared two-phase implementation
# ---------------------------------------------------------------------------


def _emit_union_of_arms(
    visitor: AlgebraVisitor,
    arm_drivers: list[Callable[[AlgebraVisitor], None]],
) -> None:
    """Two-phase UNION emitter — probe arms for vars, then emit
    each arm with the full union-schema projection and combine.

    ``arm_drivers`` is a list of single-argument callables that
    take a child :class:`AlgebraVisitor` and walk one arm into
    it. Decoupling the *what to translate* (driver) from the *how
    to wrap and combine* (this function) keeps the same code path
    serving both ``Union`` (driver = ``cv.visit(arm)``) and
    ``AlternativePath`` (driver = ``cv._emit_triple((s, p_i, o))``)
    without per-caller duplication.
    """
    if len(arm_drivers) < 2:
        # Defensive — AlternativePath always has ≥ 2 arms (otherwise
        # rdflib would have collapsed to the single inner path),
        # Union always has p1 + p2. If a future caller hands us a
        # degenerate single-arm list, fail loudly rather than emit
        # an AQL ``UNION((x))`` that ArangoDB would reject.
        raise ValueError(
            f"UNION requires at least 2 arms, got {len(arm_drivers)}"
        )

    # ---- Phase 1: probe each arm to collect its bound variables -----
    # We need the union of per-arm vars BEFORE we can decide each
    # arm's RETURN schema. The probe spawns + walks a child but
    # never absorbs it, so the parent's counters stay put.
    outer_vars = set(visitor.state.var_to_expr.keys())
    arm_var_sets: list[set[str]] = []
    for driver in arm_drivers:
        probe = _spawn_child(visitor)
        driver(probe)
        arm_var_sets.append(
            set(probe.state.var_to_expr.keys()) - outer_vars
        )

    all_vars = sorted(set().union(*arm_var_sets))
    if not all_vars:
        # An UNION whose arms bind no NEW variables is semantically
        # a "does either arm match anything?" probe — exactly the
        # NOT-EXISTS shape, but as bag-union, so every match in
        # either arm contributes a row. We could emit a degenerate
        # ``UNION((… RETURN 1), (… RETURN 1))`` but the outer
        # scope would have nothing to bind. The W3C corpus doesn't
        # exercise this shape; defer rather than ship empty-row
        # semantics that look like a passthrough.
        raise NotImplementedError(  # pragma: no cover - W3C-unreached
            "UNION whose arms bind no new variables is not yet supported"
        )

    # ---- Phase 2: emit each arm with full-schema projection ---------
    arm_aqls: list[str] = []
    for driver in arm_drivers:
        cv = _spawn_child(visitor)
        driver(cv)
        # Build the per-arm RETURN: vars bound in this arm use
        # their expression; vars NOT bound in this arm fall back
        # to ``null`` so every row of UNION carries the same
        # schema (matches SPARQL semantics where a variable that
        # only appears in one UNION branch is UNDEF in the other
        # branch's rows).
        mapping: list[tuple[str, str]] = []
        for var_name in all_vars:
            expr = cv.state.var_to_expr.get(var_name, "null")
            mapping.append((var_name, expr))
        cv.builder.return_object(mapping)
        arm_aqls.append(visitor.builder.absorb_child(cv.builder))

    # ---- Outer-scope FOR + variable binding -------------------------
    row_alias = visitor.builder.fresh_alias(prefix="row")
    visitor.builder.for_union(row_alias, arm_aqls)

    for var_name in all_vars:
        new_expr = f"{row_alias}.{var_name}"
        existing = visitor.state.var_to_expr.get(var_name)
        if existing is None:
            visitor.state.var_to_expr[var_name] = new_expr
            continue
        if existing != new_expr:
            visitor.builder.filter_raw(f"{new_expr} == {existing}")


def _spawn_child(visitor: AlgebraVisitor) -> AlgebraVisitor:
    """Spawn a child visitor with outer-scope ``var_to_expr``
    pre-seeded.

    Pre-seeding plays the same role as in
    :mod:`minus_exists`: a UNION arm that references an
    outer-bound variable should turn it into an equality FILTER
    against the outer's expression, not anchor a new FOR. The
    BGP emitter's ``_bind_subject`` does the right thing as long
    as ``var_to_expr`` has the outer's binding when the inner
    triple is visited.

    Counter sharing follows the standard
    :meth:`AqlQueryBuilder.create_child` /
    :meth:`absorb_child` protocol — caller is responsible for
    either absorbing the child or discarding it (probe phase).
    """
    # Local import to avoid the visitor ↔ union_paths cycle at
    # module-import time.
    from .visitor import AlgebraVisitor

    child_builder = visitor.builder.create_child()
    cv = AlgebraVisitor(
        builder=child_builder,
        resolver=visitor.resolver,
        tenant_id=visitor.tenant_id,
    )
    cv.state.var_to_expr = dict(visitor.state.var_to_expr)
    return cv
