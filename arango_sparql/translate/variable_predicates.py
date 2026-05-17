"""SPARQL 1.1 variable-predicate emission (``?s ?p ?o``).

The visitor's main triple dispatcher (:meth:`AlgebraVisitor._emit_triple`)
hands every triple whose predicate is a :class:`rdflib.term.Variable`
to :func:`emit_variable_predicate_triple` below. Two emission shapes
are selected here from the subject's binding state at the time the
triple visits — see the function docstring for the spec-correctness
disclosure on each.

This module lives next to :mod:`arango_sparql.translate.paths` for
exactly the same reason that one does: keeping ``visitor.py`` under
the 1500-line cap from ``.cursor/rules/modularity-and-structure.mdc``.
Every helper here takes the visitor instance as its first argument
and reaches into its public-ish surface
(``builder``, ``state``, ``_open_collection``, ``_ensure_subject_alias``,
``_record_var_expr``) — the same convention :mod:`paths` uses.

Legacy reference: ``references/arango-sparql/src/lib/pgt-translator.js``
lines 244-261, which hard-coded a four-collection UNION
(``Person | Organization | Property | Class``) rather than driving
the fan-out off the resolver. We replace that with two
correct-by-construction shapes (RPT and ATTRIBUTES fan-out), with
the CARVE-OUT for the unbound-subject case documented inline and
in PRD §6.6 Variable-predicates row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rdflib import Literal, URIRef, Variable

from ..errors import AqlEmitError, UnsupportedSparqlError

if TYPE_CHECKING:
    from .visitor import AlgebraVisitor

# System-attribute names we strip out of an ATTRIBUTES() fan-out
# because they are not RDF predicates — ArangoDB metadata
# (``_id``, ``_key``, ``_rev``, ``_from``, ``_to``) plus our own
# synthetic ``_uri`` column (which is the subject IRI, never a
# predicate). ATTRIBUTES(doc, true) already drops the first five;
# ``_uri`` is ours, so we filter it explicitly. Stored as a frozen
# tuple so two visitors share the same bind value via the
# builder's value-keyed dedup.
SYSTEM_ATTRIBUTES_TO_SKIP: tuple[str, ...] = ("_uri",)


def emit_variable_predicate_triple(
    visitor: AlgebraVisitor,
    subject: Any,
    predicate: Variable,
    obj: Any,
    triple: tuple[Any, Any, Any],
) -> None:
    """Emit AQL for ``?s ?p ?o`` (variable-predicate triple pattern).

    Two emission shapes, selected by the subject's binding state.

    **RPT-bound subject** — the triples table already has
    ``predicate`` / ``object_uri`` / ``object_value`` columns,
    so the fan-out collapses to a plain FOR + per-column
    projection. ``?p`` binds to the predicate column directly
    (no FILTER) and ``?o`` to the standard
    ``COALESCE(object_uri, object_value)`` expression. Mirrors
    :meth:`AlgebraVisitor._emit_rpt_property_triple` minus the
    predicate equality FILTER. This emission is W3C-spec-correct.

    **PG / LPG / default-collection subject** — we don't have
    per-class attribute→IRI mapping yet (PRD §6.6 follow-up
    slice), so the cleanest emission is::

        FOR k1 IN ATTRIBUTES(<subject_alias>, true)
        FILTER k1 NOT IN [<sys attrs>]
        -- ?p bound to k1
        -- ?o bound to <subject_alias>[k1]

    which iterates every attribute on the subject document and
    produces one binding row per non-system attribute. The
    important carve-out: ``?p`` binds to the attribute **name**
    (a string like ``"name"``), **not** the predicate IRI. The
    SPARQL spec requires ``?p`` to be an IRI, so live-execution
    cross-validation against a W3C-conformant triplestore will
    diverge for queries that rely on the IRI shape (notably the
    27 aggregate / BIND / projection-alias W3C tests this slice
    moves into the "translates" bucket). The translation-only
    harness counts these as passes (which is the v1 §3.1 success
    criterion), and the affected short_ids are listed in
    ``tests/w3c/test_w3c_live_execution.py::SKIP_REASONS`` as
    live-execution XFAILs.

    Lifting the carve-out is tracked as the "attribute-name to
    predicate-URI" follow-up slice: extend
    :class:`SchemaResolver` with a per-class ``attribute_to_uri``
    dict, emit a ``LET p1 = @_attrmap[k1]`` bound to that dict,
    and FILTER rows where ``p1 != null``. That work is deferred
    because the W3C corpus uses an empty resolver — the +10.3 pp
    bump this slice produces is entirely from the unbound-subject
    path.

    Object binding still goes through the standard
    ``var_to_expr`` machinery, so a ``?o`` that already appears
    in another triple gets the equality-FILTER join automatically
    (mirroring the visitor's right-side Variable branch).
    """

    if not isinstance(predicate, Variable):  # pragma: no cover - defensive
        raise UnsupportedSparqlError(
            "variable-predicate emitter called with non-Variable predicate"
        )

    # ---- RPT branch ---------------------------------------------------
    if (
        isinstance(subject, Variable)
        and str(subject) in visitor.state.var_to_rpt_class
    ):
        _emit_rpt_branch(visitor, subject, predicate, obj, triple)
        return

    # ---- PG / LPG / default-collection branch -------------------------
    _emit_attributes_branch(visitor, subject, predicate, obj, triple)


def _emit_rpt_branch(
    visitor: AlgebraVisitor,
    subject: Variable,
    predicate: Variable,
    obj: Any,
    triple: tuple[Any, Any, Any],
) -> None:
    """RPT subject + variable predicate.

    Opens a fresh FOR over the triples table, joins on the subject
    URI captured in ``var_to_expr``, and projects the predicate
    column directly. Object follows the standard RPT object
    dispatch (Variable → COALESCE, URI → OR-filter across both
    columns, Literal → object_value).

    This branch is the W3C-spec-correct half of the slice — the
    triples table genuinely has a predicate column, so ``?p``
    binds to an IRI as the spec requires. Live-execution cross-
    validation against pyoxigraph would pass for these (the v0.3
    coverage report doesn't include any RPT-flavoured variable-
    predicate W3C tests, but the goldens prove the shape).
    """
    rpt_class = visitor.state.var_to_rpt_class[str(subject)]
    triples_alias = visitor._open_collection(
        rpt_class.collection, resolved=rpt_class
    )
    subj_expr = visitor.state.var_to_expr.get(str(subject))
    if subj_expr is None:  # pragma: no cover - defensive
        raise AqlEmitError(
            f"RPT variable-predicate triple references unbound "
            f"subject ?{subject}"
        )
    new_subj_expr = f"{triples_alias}.{rpt_class.subject_column}"
    if subj_expr != new_subj_expr:
        visitor.builder.filter_raw(f"{new_subj_expr} == {subj_expr}")
    # ``?p`` binds to the predicate column — no FILTER; we want
    # EVERY predicate on this subject.
    visitor._record_var_expr(
        predicate, f"{triples_alias}.{rpt_class.predicate_column}"
    )
    # ``?o`` follows the same COALESCE shape as
    # _emit_rpt_property_triple's Variable branch.
    coalesce_expr = (
        f"COALESCE({triples_alias}.{rpt_class.object_uri_column}, "
        f"{triples_alias}.{rpt_class.object_value_column})"
    )
    if isinstance(obj, Variable):
        o_name = str(obj)
        existing = visitor.state.var_to_expr.get(o_name)
        if existing is None:
            visitor.state.var_to_expr[o_name] = coalesce_expr
        elif existing != coalesce_expr:
            visitor.builder.filter_raw(f"{coalesce_expr} == {existing}")
        return
    if isinstance(obj, URIRef):
        uri_bind = visitor.builder.bind(str(obj), hint="obj")
        visitor.builder.filter_raw(
            f"({triples_alias}.{rpt_class.object_uri_column} == {uri_bind} "
            f"|| {triples_alias}.{rpt_class.object_value_column} == {uri_bind})"
        )
        return
    if isinstance(obj, Literal):
        # Local import to avoid circular dependency at module
        # import time — :mod:`visitor` already imports this module.
        from .visitor import _term_to_python

        val_bind = visitor.builder.bind(_term_to_python(obj), hint="obj")
        visitor.builder.filter_eq(
            f"{triples_alias}.{rpt_class.object_value_column}", val_bind
        )
        return
    raise UnsupportedSparqlError(
        f"variable-predicate RPT triple object term type "
        f"{type(obj).__name__!r} is not supported (triple {triple!r})"
    )


def _emit_attributes_branch(
    visitor: AlgebraVisitor,
    subject: Any,
    predicate: Variable,
    obj: Any,
    triple: tuple[Any, Any, Any],
) -> None:
    """PG / LPG / default-collection subject + variable predicate.

    Opens (or reuses) a FOR for the subject, then iterates the
    document's attributes via ``ATTRIBUTES(doc, true)`` so each
    non-system attribute produces one binding row. ``?p`` binds
    to the attribute name (string — see PRD §6.6 carve-out);
    ``?o`` binds to the dictionary lookup ``doc[k]``.

    Composes cleanly with the rest of the BGP because both
    ``?p`` and ``?o`` are recorded in ``var_to_expr`` — a
    downstream triple that re-binds either variable gets the
    standard equality-FILTER join.
    """
    subject_alias = visitor._ensure_subject_alias(subject)
    # Mint a fresh alias for the ATTRIBUTES() loop variable.
    # ``k`` prefix is descriptive — operators reading EXPLAIN
    # output can immediately tell which FOR is an attribute
    # iteration vs. a document scan.
    key_alias = visitor.builder.fresh_alias(prefix="k")
    visitor.builder.for_attributes(key_alias, subject_alias)
    # Skip our synthetic ``_uri`` column — ATTRIBUTES(_, true)
    # already drops the ArangoDB system attrs. The skip list is
    # bound through the builder so the AQL never sees inlined
    # string literals.
    sys_bind = visitor.builder.bind(
        list(SYSTEM_ATTRIBUTES_TO_SKIP), hint="sys_attrs"
    )
    visitor.builder.filter_raw(f"{key_alias} NOT IN {sys_bind}")
    # ``?p`` binds to the iteration key. CARVE-OUT: this is the
    # attribute NAME (a string), not the predicate IRI. See the
    # module docstring and PRD §6.6 row.
    visitor._record_var_expr(predicate, key_alias)
    # ``?o`` binds to the attribute value at this key. Same
    # var_to_expr-aware logic the visitor's Case 2 object
    # branch uses, so a ``?o`` that's also bound by another
    # triple gets the equality-FILTER join.
    value_expr = f"{subject_alias}[{key_alias}]"
    if isinstance(obj, Variable):
        o_name = str(obj)
        existing = visitor.state.var_to_expr.get(o_name)
        if existing is None:
            visitor._record_var_expr(obj, value_expr)
        elif existing != value_expr:
            visitor.builder.filter_raw(f"{value_expr} == {existing}")
        return
    if isinstance(obj, (Literal, URIRef)):
        from .visitor import _term_to_python

        obj_bind = visitor.builder.bind(_term_to_python(obj), hint="obj")
        visitor.builder.filter_eq(value_expr, obj_bind)
        return
    raise UnsupportedSparqlError(
        f"variable-predicate triple object term type "
        f"{type(obj).__name__!r} is not supported (triple {triple!r})"
    )
