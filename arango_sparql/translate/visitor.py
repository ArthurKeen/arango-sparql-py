"""Algebra visitor — one ``visit_<NodeType>`` method per rdflib Algebra
node. Unknown nodes raise :class:`UnsupportedSparqlError`.

See ``.cursor/skills/sparql-to-aql/SKILL.md`` for the porting recipe;
each method here corresponds to a function in the legacy
``references/arango-sparql/src/lib/*-translator.js`` files.

This module owns the per-query *binding state* (SPARQL variable →
AQL expression, SPARQL variable → physical document alias). The AQL
builder stays SPARQL-agnostic; everything SPARQL-specific lives here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from rdflib import RDF, Literal, URIRef, Variable

from ..errors import AqlEmitError, SchemaResolutionError, UnsupportedSparqlError
from .builder import AqlQueryBuilder
from .resolver import ResolvedClass, SchemaResolver

logger = logging.getLogger(__name__)


@dataclass
class _BindingState:
    """Per-query SPARQL→AQL binding tables.

    Mirrors the ``variableMappings`` / ``documentMappings`` /
    ``fromClauses`` trio in the legacy
    ``references/arango-sparql/src/lib/pgt-translator.js`` so the port
    of each new visitor reads as a structural translation, not a
    rewrite.
    """

    var_to_expr: dict[str, str] = field(default_factory=dict)
    """SPARQL variable name → AQL expression that produces its value
    (e.g. ``?s`` → ``"doc1._uri"``, ``?n`` → ``"doc1.name"``)."""

    var_to_doc_alias: dict[str, str] = field(default_factory=dict)
    """SPARQL variable name → AQL FOR-loop alias whose document
    represents the SPARQL subject (only set for variables we ever bind
    to a physical document)."""

    doc_to_collection: dict[str, str] = field(default_factory=dict)
    """AQL alias → physical collection name. Used to detect duplicate
    FOR clauses and to drive future joins."""

    var_to_rpt_class: dict[str, ResolvedClass] = field(default_factory=dict)
    """SPARQL subject variable → :class:`ResolvedClass` for the RPT
    triples table that backs this subject's class.

    Populated by :meth:`AlgebraVisitor._emit_triple` when a type
    pattern resolves to ``style == "RPT"``. Subsequent property
    triples on the same variable use this entry to (a) know they
    must dispatch through the RPT triple-store reader (PRD §6.6 RPT
    row) and (b) read the per-class column overrides — different
    customer schemas rename the four ``subject_uri`` / ``predicate``
    / ``object_uri`` / ``object_value`` columns and the override
    must travel with the SUBJECT's class, not be re-derived per
    property triple. Variables bound by PG / LPG patterns are absent
    from this dict — that's how :meth:`_emit_triple` distinguishes a
    PG ``?s :p ?o`` from an RPT ``?s :p ?o``.
    """

    projection_vars: list[Variable] = field(default_factory=list)
    """The Project node's PV list, captured by ``visit_SelectQuery`` /
    ``visit_Project`` and consumed by ``_emit_projection``."""

    distinct: bool = False


@dataclass
class AlgebraVisitor:
    """Walk an rdflib SPARQL Algebra tree and emit AQL via the builder."""

    builder: AqlQueryBuilder
    resolver: SchemaResolver
    explicit_projection: list[Variable] | None = None
    """Projection variables in their declared order, or ``None`` when the
    query used ``SELECT *``. Captured upstream by
    :func:`arango_sparql.translate.parser.parse_sparql` because the
    rdflib Algebra's ``Project.PV`` collapses into a non-deterministic
    set-iteration order. When ``None``, ``_emit_projection`` falls back
    to the visitor's own deterministic variable-binding order."""

    state: _BindingState = field(default_factory=_BindingState)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def visit(self, node: Any) -> Any:
        name = getattr(node, "name", None)
        if name is None:
            raise UnsupportedSparqlError(f"node has no .name attribute: {type(node).__name__}")
        method = getattr(self, f"visit_{name}", None)
        if method is None:
            return self.visit_unknown(node)
        return method(node)

    def visit_unknown(self, node: Any) -> Any:
        raise UnsupportedSparqlError(
            f"SPARQL Algebra node {node.name!r} is not implemented yet (see .cursor/skills/sparql-to-aql/SKILL.md)"
        )

    # ------------------------------------------------------------------
    # Top-level query nodes
    # ------------------------------------------------------------------
    def visit_SelectQuery(self, node: Any) -> Any:
        # rdflib wraps the body in Project (and possibly Distinct/Slice/etc.).
        # We just delegate; the inner visitors set ``state.projection_vars``
        # and emit the RETURN.
        self.visit(node.p)

    # ------------------------------------------------------------------
    # ASK — SELECT LIMIT 1 wrapped in LENGTH(...) > 0
    # ------------------------------------------------------------------
    def visit_AskQuery(self, node: Any) -> Any:
        # rdflib wraps the BGP in Project even for ASK (the projection
        # is just discarded). We deliberately bypass the Project so we
        # don't emit a stray RETURN { ... }, and instead drive the inner
        # pattern (BGP / FILTER / OPTIONAL / …) directly. The builder's
        # ASK mode then wraps the whole body in ``RETURN LENGTH(<inner>)
        # > 0`` at finalize() time — same recipe as the legacy
        # ``aql-translator.js#translateQuery`` ASK branch (``ASK is
        # essentially SELECT LIMIT 1``).
        inner = node.p
        if getattr(inner, "name", None) == "Project":
            inner = inner.p
        self.visit(inner)
        # LIMIT 1 short-circuits the cursor — we only need to know
        # whether *any* row matches, not how many.
        if self.builder._limit_clause is None:  # noqa: SLF001 - own builder
            self.builder.limit(1)
        self.builder.return_scalar("1")
        self.builder.set_ask_mode()

    def visit_Project(self, node: Any) -> Any:
        # Prefer the explicit declaration order captured upstream from
        # the parsed query. When the query was ``SELECT *``,
        # ``explicit_projection`` is ``None`` and we deliberately leave
        # ``projection_vars`` empty so ``_emit_projection`` falls into
        # the deterministic insertion-order branch — ``Project.PV`` for
        # ``SELECT *`` is a set-iteration order that varies across
        # Python runs (PYTHONHASHSEED randomization).
        if self.explicit_projection is not None:
            self.state.projection_vars = list(self.explicit_projection)
        self.visit(node.p)
        self._emit_projection()

    def visit_Distinct(self, node: Any) -> Any:
        self.state.distinct = True
        self.visit(node.p)

    def visit_Slice(self, node: Any) -> Any:
        # Visit the inner pattern first so FOR/FILTER/SORT come before LIMIT.
        # The Project wrapper (when present) will emit RETURN after we return.
        self.visit(node.p)
        start = int(getattr(node, "start", 0) or 0)
        length = getattr(node, "length", None)
        if length is None:
            # SPARQL 1.1 LIMIT/OFFSET is grammar-required to set both;
            # an offset-only query (no LIMIT) is rare but legal — punt
            # until we hit a real corpus example.
            raise UnsupportedSparqlError("SPARQL OFFSET without LIMIT is not yet supported")
        self.builder.limit(int(length), offset=start)

    # ------------------------------------------------------------------
    # FILTER — wraps an inner pattern with a boolean expression
    # ------------------------------------------------------------------
    def visit_Filter(self, node: Any) -> Any:
        # Visit the inner pattern first so all FORs are open and
        # ``var_to_expr`` is populated; rdflib places Filter ABOVE BGP
        # so this naturally emits FILTER after the FOR clauses, which
        # is the correct AQL evaluation order for cross-FOR filters.
        self.visit(node.p)
        aql_expr = self._translate_expr(node.expr)
        self.builder.filter_raw(aql_expr)

    # ------------------------------------------------------------------
    # OPTIONAL — LeftJoin(p1=required, p2=optional, expr=<inner FILTER>)
    # ------------------------------------------------------------------
    def visit_LeftJoin(self, node: Any) -> Any:
        # Visit the required side first so every alias / variable that
        # the optional side references is in scope.
        self.visit(node.p1)

        p2 = node.p2
        if getattr(p2, "name", None) != "BGP":
            # OPTIONAL { OPTIONAL { … } } and OPTIONAL { ?s :p ?o
            # FILTER(?o) UNION ?s :q ?o } both land here; defer until
            # we have a corpus example so we don't ship an
            # ad-hoc semantics for them.
            raise UnsupportedSparqlError(
                f"OPTIONAL whose body is {p2.name!r} (not a plain BGP) is not yet supported"
            )

        # Walk the optional triples once to collect (var, source_expr)
        # pairs, *without* mutating ``var_to_expr`` yet — the inner
        # FILTER (if any) needs the optional vars in scope to translate
        # but we want to install the final (possibly conditional)
        # bindings after we've decided whether the OPTIONAL block needs
        # an all-or-nothing gate.
        #
        # ``new_bindings`` carries one entry per optional binding; the
        # source expression is either an attribute path
        # (``doc1.email``) for datatype properties or a LET alias
        # (``opt2``) for object properties. The downstream emitter does
        # not need to distinguish the two.
        new_bindings: list[tuple[str, str]] = []  # (sparql_var, source_expr)
        seen_vars: set[str] = set()
        for triple in getattr(p2, "triples", []) or []:
            s, p, o = triple
            # OPTIONAL semantics in AQL only stay simple when the
            # OPTIONAL block doesn't open a new FOR — the AQL "join" is
            # then either an attribute lookup on a doc we've already
            # opened (datatype property) or a single-step OUTBOUND
            # subquery from that doc (object property). Cross-subject
            # OPTIONAL (which would need a real subquery of the form
            # ``LET o = (FOR x IN coll FILTER … RETURN x)[0]``) is the
            # legacy ``aql-translator.js#processOptionalPatterns``
            # branch we'll port when there's a corpus need.
            if not isinstance(s, Variable) or str(s) not in self.state.var_to_doc_alias:
                raise UnsupportedSparqlError(
                    "OPTIONAL whose subject is not already bound by the required side "
                    "is not yet supported (cross-subject LEFT JOIN needs a subquery emitter)"
                )
            if not isinstance(p, URIRef):
                raise UnsupportedSparqlError("OPTIONAL with a variable predicate is not supported")
            if not isinstance(o, Variable):
                # OPTIONAL { ?s :p "literal" } would mean "test for
                # existence of this exact triple", which AQL can't
                # express without a subquery. Refuse for now.
                raise UnsupportedSparqlError("OPTIONAL with a non-variable object is not yet supported")
            o_name = str(o)
            if o_name in self.state.var_to_expr:
                # The var was already bound by p1 — OPTIONAL re-binding
                # an in-scope variable would shift semantics from LEFT
                # JOIN toward INNER JOIN; rdflib should have lifted
                # that triple out of the LeftJoin, but defend in case.
                raise UnsupportedSparqlError(
                    f"OPTIONAL re-binds variable ?{o_name} that's already bound by the required side"
                )
            prop = self.resolver.resolve_property(p)
            subject_alias = self.state.var_to_doc_alias[str(s)]
            if prop.is_object_property:
                # Object-property OPTIONAL: emit a LET subquery that
                # follows the edge once and returns the target's
                # ``_uri``, or ``null`` if no edge matches. The LET
                # alias becomes the binding's source expression — same
                # downstream treatment as an attribute path.
                if prop.edge_collection is None:
                    raise SchemaResolutionError(
                        f"object property {prop.iri!r} in OPTIONAL has no "
                        f"phys:edgeCollectionName annotation; the OWL ontology "
                        f"must declare which ArangoDB edge collection backs "
                        f"this relationship (PRD §6.2)"
                    )
                let_alias = self.builder.fresh_alias(prefix="opt")
                if prop.mapping_style == "GENERIC_WITH_TYPE":
                    self.builder.let_outbound_first_uri(
                        let_alias,
                        start_alias=subject_alias,
                        edge_collection=prop.edge_collection,
                        type_field=prop.type_field,
                        type_value=prop.type_value,
                    )
                else:
                    self.builder.let_outbound_first_uri(
                        let_alias,
                        start_alias=subject_alias,
                        edge_collection=prop.edge_collection,
                    )
                new_bindings.append((o_name, let_alias))
            else:
                attr_path = f"{subject_alias}.{prop.attribute}"
                new_bindings.append((o_name, attr_path))
            seen_vars.add(o_name)

        if not new_bindings:
            # Empty OPTIONAL (e.g. only a FILTER, no triples) is a
            # no-op under our same-subject restriction — return cleanly
            # rather than emit dead AQL.
            return

        expr = getattr(node, "expr", None)
        has_filter = expr is not None and getattr(expr, "name", "") != "TrueFilter"

        # Fast path: a single new binding, no inner FILTER. For both
        # attribute-path bindings (``doc.attr`` returns null when the
        # attribute is missing — SPARQL's "unbound" already) and edge
        # LETs (the LET evaluates to null when the subquery returned no
        # rows), the source expression is itself the right semantics —
        # no extra null-coalescing needed.
        if not has_filter and len(new_bindings) == 1:
            var, source_expr = new_bindings[0]
            self.state.var_to_expr[var] = source_expr
            return

        # Multi-binding or filtered OPTIONAL needs the all-or-nothing
        # match condition. SPARQL semantics: the OPTIONAL block matches
        # as a unit, so if *any* triple in the group fails (or the
        # inner FILTER rejects the candidate), *every* var the block
        # would have bound becomes unbound.
        #
        # The null-check predicate works for both attribute paths and
        # edge LET aliases — both evaluate to ``null`` when the
        # underlying datum is missing, so ``<expr> != null`` is the
        # uniform "this binding matched" probe.
        null_checks = [f"{source_expr} != null" for _, source_expr in new_bindings]
        if has_filter:
            # Translate the FILTER with the optional vars resolving to
            # their source expressions (the FILTER references them by
            # SPARQL name); we'll rebind to the per-binding LET aliases
            # immediately after.
            for var, source_expr in new_bindings:
                self.state.var_to_expr[var] = source_expr
            aql_filter = self._translate_expr(expr)
            match_terms = [*null_checks, aql_filter]
        else:
            match_terms = null_checks
        match_expr = " && ".join(match_terms)
        if len(match_terms) > 1:
            match_expr = f"({match_expr})"
        for var, source_expr in new_bindings:
            alias = self.builder.fresh_alias(prefix="opt")
            self.builder.let(alias, f"({match_expr} ? {source_expr} : null)")
            self.state.var_to_expr[var] = alias

    # ------------------------------------------------------------------
    # BIND / agg-result rename — Extend(p=inner, var=?v, expr=<sparql expr>)
    # ------------------------------------------------------------------
    def visit_Extend(self, node: Any) -> Any:
        # Visit the inner pattern first so every variable referenced by
        # the BIND expression is already in ``var_to_expr``. rdflib
        # always nests BIND beneath any pattern that produced its inputs
        # (the SPARQL grammar enforces this), so this ordering matches
        # the legal scope.
        self.visit(node.p)
        var = getattr(node, "var", None)
        if var is None:
            raise UnsupportedSparqlError("BIND node is missing its target variable")
        var_name = str(var)
        expr = node.expr

        # Pure rename short-circuit: ``Extend(expr=?other, var=?new)``.
        # rdflib emits this shape after every ``AggregateJoin`` to map
        # synthetic ``__agg_N__`` results onto the user's projection
        # aliases (e.g. ``__agg_1__`` → ``?c``), and SPARQL also allows
        # ``BIND(?other AS ?new)`` for plain aliasing. Re-pointing
        # ``var_to_expr`` is enough — emitting a LET would just shadow
        # the existing binding with no semantic change.
        #
        # The overwrite is intentional: rdflib's post-aggregation rename
        # of ``?d`` (group key) deliberately replaces the pre-COLLECT
        # ``?d → doc1.dept`` mapping with the new ``?d → grp1`` (the
        # COLLECT key alias). The pre-COLLECT alias is out of scope
        # after COLLECT, so the overwrite matches AQL scoping.
        if isinstance(expr, Variable) and str(expr) in self.state.var_to_expr:
            self.state.var_to_expr[var_name] = self.state.var_to_expr[str(expr)]
            return

        if var_name in self.state.var_to_expr:
            # SPARQL forbids re-binding a variable already in scope —
            # rdflib should have raised, but defend in case the algebra
            # was hand-built (e.g. via the algebra module directly).
            raise UnsupportedSparqlError(
                f"BIND target ?{var_name} is already bound by the surrounding pattern"
            )
        aql_expr = self._translate_expr(expr)
        # Mint a fresh AQL identifier rather than reusing the SPARQL
        # name verbatim — ``?type`` and similar AQL-reserved words would
        # otherwise blow up at execution. The ``bv_`` (bind variable)
        # prefix keeps it visually distinct from FOR-loop aliases
        # (``doc1``, ``doc2``…) in the rendered query.
        alias = self.builder.fresh_alias(prefix="bv")
        self.builder.let(alias, aql_expr)
        self.state.var_to_expr[var_name] = alias

    # ------------------------------------------------------------------
    # GROUP BY + aggregates — AggregateJoin(A=[…aggs…], p=Group(p=BGP, expr=keys|None))
    # ------------------------------------------------------------------
    # Map rdflib ``Aggregate_<Name>_`` → AQL aggregate function. SAMPLE
    # is handled separately because rdflib synthesises a Sample for
    # every GROUP BY key (it's the SPARQL spec's convenience for
    # "any value within the group, since they're all equal anyway"),
    # and we route those into COLLECT key aliases instead of AQL
    # aggregate functions.
    # The class names rdflib uses (e.g. ``Aggregate_Count``) — note no
    # trailing underscore, despite ``pprintAlgebra`` rendering them as
    # ``Aggregate_Count_{...}`` (the ``_{`` is a separator before the
    # attribute-dict repr, not part of the name).
    _SPARQL_AGG_TO_AQL = {
        "Aggregate_Count": "COUNT",
        "Aggregate_Sum": "SUM",
        "Aggregate_Avg": "AVG",
        "Aggregate_Min": "MIN",
        "Aggregate_Max": "MAX",
        "Aggregate_GroupConcat": "CONCAT_SEPARATOR",
    }

    def visit_AggregateJoin(self, node: Any) -> Any:
        group = node.p
        if getattr(group, "name", None) != "Group":
            # rdflib always wraps an AggregateJoin in a Group (even
            # ungrouped queries get ``Group(expr=None)``); a different
            # inner node would mean a SPARQL shape we haven't seen.
            raise UnsupportedSparqlError(f"AggregateJoin inner is {group.name!r}, expected 'Group'")

        # Visit the BGP / OPTIONAL / FILTER chain so every variable the
        # aggregates and group keys reference is bound in var_to_expr.
        self.visit(group.p)

        # Pre-allocate the COLLECT-key aliases for every Aggregate_Sample_
        # (one per GROUP BY variable). We do this in a first pass so the
        # main pass can see those bindings already in place when it
        # emits the AGGREGATE list — important for nested expressions
        # like ``COUNT(?d)`` over a grouping variable ``?d``.
        keys: list[tuple[str, str]] = []  # (alias, expression) for COLLECT
        sample_remap: dict[str, str] = {}  # __agg_N__ for samples → key alias
        for agg in node.A:
            if agg.name != "Aggregate_Sample":
                continue
            sparql_var = str(agg.vars)
            attr_expr = self.state.var_to_expr.get(sparql_var)
            if attr_expr is None:
                raise UnsupportedSparqlError(f"GROUP BY references unbound variable ?{sparql_var}")
            key_alias = self.builder.fresh_alias(prefix="grp")
            keys.append((key_alias, attr_expr))
            sample_remap[str(agg.res)] = key_alias
            # Also rebind the user-facing var: post-COLLECT it must
            # reference the COLLECT key alias, not the (now out-of-scope)
            # FOR alias's attribute.
            self.state.var_to_expr[sparql_var] = key_alias
            self.state.var_to_expr[str(agg.res)] = key_alias

        # Real aggregates (everything except Aggregate_Sample_).
        aggregates: list[tuple[str, str]] = []  # (alias, "AGG(expr)")
        count_into: str | None = None
        non_sample_aggs = [a for a in node.A if a.name != "Aggregate_Sample"]

        # Fast-path: a single COUNT (any var, no DISTINCT) collapses to
        # the AQL idiom ``WITH COUNT INTO <c>`` — more readable than
        # ``AGGREGATE c = COUNT(<expr>)`` and the planner treats them
        # identically.
        is_count_shorthand = (
            len(non_sample_aggs) == 1
            and non_sample_aggs[0].name == "Aggregate_Count"
            and non_sample_aggs[0].distinct != "DISTINCT"
        )

        for agg in non_sample_aggs:
            agg_var = str(agg.res)
            distinct = agg.distinct == "DISTINCT"
            agg_alias = self.builder.fresh_alias(prefix="agg")

            if is_count_shorthand:
                count_into = agg_alias
                self.state.var_to_expr[agg_var] = agg_alias
                continue

            aql_func = self._SPARQL_AGG_TO_AQL.get(agg.name)
            if aql_func is None:
                raise UnsupportedSparqlError(f"SPARQL aggregate {agg.name!r} is not yet supported")

            arg_expr = self._aggregate_arg_expr(agg)

            if agg.name == "Aggregate_Count":
                # Generic COUNT path (mixed with other aggregates, or
                # COUNT DISTINCT). ``COUNT_DISTINCT`` is an AQL builtin.
                func = "COUNT_DISTINCT" if distinct else "COUNT"
                aggregates.append((agg_alias, f"{func}({arg_expr})"))
            elif agg.name == "Aggregate_GroupConcat":
                # SPARQL GROUP_CONCAT defaults to a single-space
                # separator; the user can override via ``SEPARATOR=…``,
                # which rdflib stores as an rdflib ``Literal`` on
                # ``agg.separator``. AQL's CONCAT_SEPARATOR takes the
                # separator first, then the value list. Push the
                # separator through ``_term_to_python`` so it goes into
                # the bind-vars dict as a plain string (rdflib Literals
                # don't JSON-encode cleanly).
                raw_sep = agg.get("separator", " ")
                separator = _term_to_python(raw_sep) if raw_sep is not None else " "
                sep_bind = self.builder.bind(separator, hint="sep")
                aggregates.append((agg_alias, f"CONCAT_SEPARATOR({sep_bind}, {arg_expr})"))
            else:
                if distinct:
                    # SUM / AVG / MIN / MAX with DISTINCT: AQL doesn't
                    # have native DISTINCT-aggregates, so we'd need to
                    # rewrite into a COLLECT … INTO subquery. Refuse
                    # for now so the operator notices.
                    raise UnsupportedSparqlError(
                        f"DISTINCT is only supported on COUNT aggregates; got DISTINCT inside {agg.name!r}"
                    )
                aggregates.append((agg_alias, f"{aql_func}({arg_expr})"))

            self.state.var_to_expr[agg_var] = agg_alias

        # Ungrouped queries with no aggregates would just be a COLLECT
        # producing one row of nothing — that's a SPARQL shape we don't
        # see in practice (``SELECT ?x WHERE { … } GROUP BY ?x`` always
        # has at least the Aggregate_Sample_ for ?x). Defer until we
        # have a real corpus example.
        if not keys and not aggregates and count_into is None:
            raise UnsupportedSparqlError(
                "AggregateJoin with neither GROUP BY keys nor aggregates is not supported"
            )

        self.builder.collect(keys=keys, aggregates=aggregates, count_into=count_into)

    def _aggregate_arg_expr(self, agg: Any) -> str:
        """Translate the argument of a SPARQL aggregate to an AQL expression.

        Handles three input shapes rdflib produces:
          * ``vars == '*'`` (only legal for COUNT(*)) → AQL ``1``
          * ``vars`` is a Variable → look up via ``var_to_expr``
          * ``vars`` is a richer expression node → fall through to the
            FILTER expression translator.
        """
        v = agg.vars
        if v == "*":
            return "1"
        if isinstance(v, Variable):
            mapped = self.state.var_to_expr.get(str(v))
            if mapped is None:
                raise UnsupportedSparqlError(f"aggregate references unbound variable ?{v}")
            return mapped
        return self._translate_expr(v)

    # ------------------------------------------------------------------
    # ORDER BY — list of OrderConditions, each with expr + direction
    # ------------------------------------------------------------------
    def visit_OrderBy(self, node: Any) -> Any:
        # Visit the inner pattern first so every variable referenced by
        # the order conditions is already bound in ``var_to_expr``. The
        # builder buffers SORT clauses separately from body clauses and
        # finalize() always renders them in the canonical body→SORT
        # →LIMIT→RETURN order, so we don't need to think about whether
        # rdflib placed OrderBy below Project (above LIMIT) or above
        # Project (below LIMIT).
        self.visit(node.p)
        conditions = list(getattr(node, "expr", []) or [])
        if not conditions:
            return
        for cond in conditions:
            inner = getattr(cond, "expr", None)
            if inner is None:
                raise UnsupportedSparqlError("ORDER BY condition is missing its expression")
            aql_expr = self._translate_expr(inner)
            order = (getattr(cond, "order", None) or "ASC").upper()
            if order not in ("ASC", "DESC"):
                raise UnsupportedSparqlError(
                    f"ORDER BY direction {order!r} is not supported (expected ASC or DESC)"
                )
            self.builder.sort(aql_expr, descending=(order == "DESC"))

    # ------------------------------------------------------------------
    # JOIN — Join(p1, p2). Visit both sides; the BGP emitter's
    # already-bound-variable detection (in ``_emit_triple`` /
    # ``_bind_subject``) turns shared variables into AQL equality
    # FILTERs, so the AQL plan ends up with one FOR per FOR-eligible
    # pattern and the cross-product gets pruned to the SPARQL join.
    # ------------------------------------------------------------------
    def visit_Join(self, node: Any) -> Any:
        self.visit(node.p1)
        self.visit(node.p2)

    # ------------------------------------------------------------------
    # BGP — the heart of every SELECT
    # ------------------------------------------------------------------
    def visit_BGP(self, node: Any) -> Any:
        triples = list(getattr(node, "triples", []) or [])
        # Order matters here: a type pattern (``?s a :Person``) carries
        # the strongest hint about which physical collection ``?s`` lives
        # in, so we want to visit those first and bind ``?s`` to the
        # right alias before any sibling property triple opens a fallback
        # FOR over the default collection. ``rdflib.algebra.translateQuery``
        # may reorder triples for join optimization, undoing the user's
        # declaration order; this re-sort is the visitor's defense.
        for triple in sorted(triples, key=_triple_priority):
            self._emit_triple(triple)

    # ------------------------------------------------------------------
    # Internal: triple-pattern emission
    # ------------------------------------------------------------------
    def _emit_triple(self, triple: tuple[Any, Any, Any]) -> None:
        s, p, o = triple

        # Case 1 — type pattern: ``?s a :Person`` (or ``<uri> a :Person``).
        # Mirrors PGTTranslator.isTypePattern in pgt-translator.js: open a
        # FOR over the class's physical collection and bind ?s to <alias>._uri.
        # RPT-style classes branch into the triple-store reader (PRD §6.6).
        if isinstance(p, URIRef) and p == RDF.type and isinstance(o, URIRef):
            resolved = self.resolver.resolve_class(o)
            if resolved.style == "RPT":
                self._emit_rpt_type_pattern(s, o, resolved)
                return
            alias = self._open_collection(resolved.collection)
            if resolved.type_field and resolved.type_value:
                # Hybrid (multi-class) collection: the mapper emits a
                # discriminator field; gate the FOR with it so we don't
                # bleed unrelated documents into the result set.
                bind = self.builder.bind(resolved.type_value, hint=resolved.type_field)
                self.builder.filter_eq(f"{alias}.{resolved.type_field}", bind)
            self._bind_subject(s, alias)
            return

        # Case 2 — predicate is a fixed IRI (the common ``?s :name ?n`` shape).
        if isinstance(p, URIRef):
            # RPT subjects route through the triple-store reader. We
            # check ``var_to_rpt_class`` BEFORE resolving the property
            # because RPT property triples don't open a per-property
            # FOR — every read is from the same triples table — and
            # we don't want to materialise an UNMAPPED-IRI warning
            # for predicates that exist only as triple-store
            # ``predicate`` column values.
            if (
                isinstance(s, Variable)
                and str(s) in self.state.var_to_rpt_class
            ):
                self._emit_rpt_property_triple(s, p, o, triple)
                return
            prop = self.resolver.resolve_property(p)
            if prop.is_object_property:
                self._emit_edge_triple(s, prop, o, triple)
                return
            alias = self._ensure_subject_alias(s)
            attr_path = f"{alias}.{prop.attribute}"
            if isinstance(o, Variable):
                existing = self.state.var_to_expr.get(str(o))
                if existing is None:
                    self._record_var_expr(o, attr_path)
                elif existing != attr_path:
                    # The variable is already bound by an earlier
                    # triple to a different AQL expression — turn the
                    # implicit SPARQL join into an explicit AQL
                    # equality FILTER so the cross-product gets pruned.
                    # This is what makes multi-subject BGPs and ``Join``
                    # nodes correct: without the FILTER the engine
                    # would happily return the full Cartesian product.
                    self.builder.filter_raw(f"{attr_path} == {existing}")
                # else: the same expression is already bound — the
                # triple just re-states what we already knew, no-op.
            elif isinstance(o, (Literal, URIRef)):
                bind = self.builder.bind(_term_to_python(o), hint=prop.attribute)
                self.builder.filter_eq(attr_path, bind)
            else:
                raise UnsupportedSparqlError(
                    f"object term type {type(o).__name__!r} is not supported in triple {triple!r}"
                )
            return

        # Case 3 — variable predicate (``?s ?p ?o``). The legacy translator
        # emits an expensive UNION across every collection; we refuse for
        # now so we don't ship a footgun, and surface a clear error code
        # until a deliberate implementation lands.
        if isinstance(p, Variable):
            raise UnsupportedSparqlError(
                "variable predicates (?p) require multi-collection UNION; not yet supported"
            )

        raise UnsupportedSparqlError(
            f"unsupported triple shape: subject={type(s).__name__}, "
            f"predicate={type(p).__name__}, object={type(o).__name__}"
        )

    # ------------------------------------------------------------------
    # Internal: alias / FOR-clause management
    # ------------------------------------------------------------------
    def _open_collection(self, collection: str) -> str:
        """Mint a fresh alias and emit ``FOR <alias> IN <collection>``.

        The legacy code dedupes by ``fromClauses`` set; we mint a fresh
        alias per call for now (the optimizer in ArangoDB collapses
        identical FORs in the common case). Deduplication can land
        alongside multi-triple BGP join optimization.
        """
        alias = self.builder.fresh_alias()
        self.builder.for_(alias, collection)
        self.state.doc_to_collection[alias] = collection
        return alias

    def _emit_rpt_type_pattern(
        self,
        subject: Any,
        class_iri: URIRef,
        resolved: ResolvedClass,
    ) -> None:
        """Emit the AQL for ``?s a :C`` against an RPT-style class.

        RPT (RDF Property Triples, PRD §6.1 / §6.6) stores every
        triple as a row in a denormalised ``_triples``-style
        collection with ``subject_uri`` / ``predicate`` /
        ``object_uri`` / ``object_value`` columns (overridable per
        class via ``phys:*Column``). A type pattern ``?s a :C``
        becomes a row scan with two equality FILTERs — one on the
        ``rdf:type`` predicate IRI and one on the class IRI in the
        ``object_uri`` column — and binds ``?s`` to the row's
        ``subject_uri`` value so subsequent property triples on the
        same variable can read the same triples table.

        Mirrors the legacy ``rpt-translator.js`` ``translateSelectRPT``
        loop's first branch.
        """
        triples_alias = self._open_collection(resolved.collection)
        rdf_type_bind = self.builder.bind(str(RDF.type), hint="rdftype")
        class_bind = self.builder.bind(str(class_iri), hint="cls")
        self.builder.filter_eq(
            f"{triples_alias}.{resolved.predicate_column}", rdf_type_bind
        )
        self.builder.filter_eq(
            f"{triples_alias}.{resolved.object_uri_column}", class_bind
        )
        if isinstance(subject, Variable):
            name = str(subject)
            subj_expr = f"{triples_alias}.{resolved.subject_column}"
            existing_alias = self.state.var_to_doc_alias.get(name)
            existing_expr = self.state.var_to_expr.get(name)
            if existing_alias is None and existing_expr is None:
                # First binding — record the RPT context AND the
                # subject-URI expression so a follow-up property
                # triple knows it's RPT-bound.
                self.state.var_to_doc_alias[name] = triples_alias
                self.state.var_to_expr[name] = subj_expr
                self.state.var_to_rpt_class[name] = resolved
                return
            # ``?s`` was already bound by an earlier triple — could
            # be a sibling type pattern (multi-class subject) or a
            # PG/RPT mixed binding. Emit an equality FILTER so the
            # cross-product collapses to the SPARQL join.
            existing_uri_expr = (
                f"{existing_alias}._uri"
                if existing_alias and name not in self.state.var_to_rpt_class
                else existing_expr
            )
            if existing_uri_expr != subj_expr:
                self.builder.filter_raw(f"{subj_expr} == {existing_uri_expr}")
            # If the prior binding was PG (no rpt_class entry) and
            # this one is RPT, record the RPT class too so subsequent
            # property triples on this var dispatch through the RPT
            # reader. The PG side already has its own FOR open and
            # the equality FILTER joins them — exactly the §3.4
            # mixed-model BGP join.
            self.state.var_to_rpt_class.setdefault(name, resolved)
            return
        if isinstance(subject, URIRef):
            uri_bind = self.builder.bind(str(subject), hint="uri")
            self.builder.filter_eq(
                f"{triples_alias}.{resolved.subject_column}", uri_bind
            )
            return
        raise UnsupportedSparqlError(
            f"RPT type pattern subject term type {type(subject).__name__!r} "
            f"is not supported"
        )

    def _emit_rpt_property_triple(
        self,
        subject: Variable,
        predicate: URIRef,
        obj: Any,
        triple: tuple[Any, Any, Any],
    ) -> None:
        """Emit the AQL for ``?s :p ?o`` where ``?s`` is RPT-bound.

        Opens a fresh FOR over the same triples table, FILTERs by the
        predicate IRI and joins on the subject URI captured in
        :attr:`_BindingState.var_to_expr`. Object binding follows the
        legacy ``rpt-translator.js`` shape:

        * Variable object → bind to
          ``COALESCE(t.object_uri, t.object_value)`` so the same var
          can later be joined against either a URI or a literal
          column from another triple.
        * IRI object → equality FILTER on either ``object_uri`` or
          ``object_value`` (legacy permissively matched both columns
          to handle datasets where the loader stored URIs in the
          value column).
        * Literal object → equality FILTER on ``object_value`` only,
          since RDF literals never live in the URI column.
        """
        rpt_class = self.state.var_to_rpt_class[str(subject)]
        triples_alias = self._open_collection(rpt_class.collection)
        # Record the per-class metadata so the OBJECT variable, if
        # bound here, joins through the same column overrides the
        # subject's class declared.
        pred_bind = self.builder.bind(str(predicate), hint="pred")
        self.builder.filter_eq(
            f"{triples_alias}.{rpt_class.predicate_column}", pred_bind
        )
        # Join on subject URI: the subject's value lives in
        # ``var_to_expr`` already (set by the type pattern emitter).
        subj_expr = self.state.var_to_expr.get(str(subject))
        if subj_expr is None:
            raise AqlEmitError(
                f"RPT property triple references unbound subject ?{subject}"
            )
        new_subj_expr = f"{triples_alias}.{rpt_class.subject_column}"
        if subj_expr != new_subj_expr:
            self.builder.filter_raw(f"{new_subj_expr} == {subj_expr}")
        # OBJECT — variable / IRI / literal each take a different shape.
        coalesce_expr = (
            f"COALESCE({triples_alias}.{rpt_class.object_uri_column}, "
            f"{triples_alias}.{rpt_class.object_value_column})"
        )
        if isinstance(obj, Variable):
            o_name = str(obj)
            existing = self.state.var_to_expr.get(o_name)
            if existing is None:
                self.state.var_to_expr[o_name] = coalesce_expr
            elif existing != coalesce_expr:
                # Object var was bound by a prior triple to a
                # different expression (PG ``doc._uri``, another RPT
                # COALESCE on a different alias, or an attribute
                # lookup); emit equality FILTER so the cross-product
                # gets pruned to the SPARQL join — same recipe as
                # the PG path.
                self.builder.filter_raw(f"{coalesce_expr} == {existing}")
            return
        if isinstance(obj, URIRef):
            uri_bind = self.builder.bind(str(obj), hint="obj")
            # Match either column — datasets that loaded URIs into
            # ``object_value`` (rare but observed in legacy fixtures)
            # still bind correctly. Mirrors the legacy
            # ``rpt-translator.js`` OR-filter.
            self.builder.filter_raw(
                f"({triples_alias}.{rpt_class.object_uri_column} == {uri_bind} "
                f"|| {triples_alias}.{rpt_class.object_value_column} == {uri_bind})"
            )
            return
        if isinstance(obj, Literal):
            val_bind = self.builder.bind(_term_to_python(obj), hint="obj")
            self.builder.filter_eq(
                f"{triples_alias}.{rpt_class.object_value_column}", val_bind
            )
            return
        raise UnsupportedSparqlError(
            f"RPT property triple object term type {type(obj).__name__!r} "
            f"is not supported (triple {triple!r})"
        )

    def _emit_edge_triple(
        self,
        subject: Any,
        prop: Any,
        obj: Any,
        triple: tuple[Any, Any, Any],
    ) -> None:
        """Emit an AQL traversal for an object-property triple.

        Implements the PRD §6.1 relationship styles:

        * ``DEDICATED_COLLECTION`` (PG-typed edge) — one edge collection
          per relationship type → ``FOR v, e IN OUTBOUND <s> @@edgeColl``.
        * ``GENERIC_WITH_TYPE`` (LPG-typed edge) — shared edge collection
          discriminated by ``phys:typeField`` / ``phys:typeValue`` →
          the same traversal plus ``FILTER e.<typeField> == @<typeValue>``.

        ``RPT_EDGE`` (RDF triple-store object property) is NOT routed
        here — it goes through the ``_triples`` reader once the RPT
        emitter lands (PRD §6.6 RPT row, tracked separately).

        The traversal target vertex's ``_uri`` is bound to the SPARQL
        object the same way the BGP entity reader binds subject ``_uri``,
        so a chain like ``?a :knows ?b . ?b a :Person ; :name ?n``
        joins on ``?b._uri`` automatically via the existing
        :meth:`_bind_subject` machinery — no new join logic needed.
        """
        if prop.edge_collection is None:
            raise SchemaResolutionError(
                f"object property {prop.iri!r} has no phys:edgeCollectionName "
                f"annotation; the OWL ontology must declare which ArangoDB "
                f"edge collection backs this relationship (PRD §6.2)"
            )

        subject_alias = self._ensure_subject_alias(subject)
        v_alias = self.builder.fresh_alias(prefix="v")
        e_alias = self.builder.fresh_alias(prefix="e")
        self.builder.for_traversal(
            v_alias, e_alias, subject_alias, prop.edge_collection
        )
        # ``GENERIC_WITH_TYPE`` shares one edge collection across many
        # relationship types; the discriminator FILTER is what keeps an
        # ``?a :knows ?b`` traversal from also returning ``:worksAt``
        # / ``:livesIn`` rows that happen to ride the same collection.
        if (
            prop.mapping_style == "GENERIC_WITH_TYPE"
            and prop.type_field
            and prop.type_value
        ):
            bind = self.builder.bind(prop.type_value, hint=prop.type_field)
            self.builder.filter_eq(f"{e_alias}.{prop.type_field}", bind)
        # We track the edge alias on the builder for the rare query that
        # references the edge document itself; ``v_alias`` is the
        # traversal vertex and is what binds to the SPARQL object.
        self.state.doc_to_collection[v_alias] = prop.edge_collection
        target_uri_expr = f"{v_alias}._uri"

        if isinstance(obj, Variable):
            o_name = str(obj)
            existing_alias = self.state.var_to_doc_alias.get(o_name)
            existing_expr = self.state.var_to_expr.get(o_name)
            if existing_alias is None and existing_expr is None:
                # First time we see ``?o`` — treat ``v_alias`` as ``?o``'s
                # subject document so a follow-up ``?o a :Person`` /
                # ``?o :name ?n`` reuses this alias instead of opening a
                # new (unrelated) FOR over the default collection.
                self._bind_subject(obj, v_alias)
                return
            # Object var already bound (typically by a prior type
            # pattern like ``?b a :Person`` placed BEFORE the edge
            # triple, or by another edge that landed on ``?b``). Emit
            # an equality filter on ``_uri`` so the cross-product gets
            # pruned to the SPARQL join semantics, mirroring the
            # ``_bind_subject`` branch for repeat type patterns.
            existing_uri_expr = (
                f"{existing_alias}._uri" if existing_alias else existing_expr
            )
            if existing_uri_expr != target_uri_expr:
                self.builder.filter_raw(
                    f"{target_uri_expr} == {existing_uri_expr}"
                )
            return

        if isinstance(obj, URIRef):
            bind = self.builder.bind(str(obj), hint="uri")
            self.builder.filter_eq(target_uri_expr, bind)
            return

        # SPARQL technically allows literal objects on object properties
        # (``?s :rel "foo"``), but RDF semantics make the triple match
        # iff the literal IS the IRI — vanishingly rare in practice and
        # ill-defined for our document model. Refuse for now.
        raise UnsupportedSparqlError(
            f"object property {prop.iri!r} with non-IRI object is not supported "
            f"(triple {triple!r})"
        )

    def _ensure_subject_alias(self, subject: Any) -> str:
        """Return the AQL alias whose document is *subject*, opening a
        fresh FOR over the default collection if we haven't seen this
        subject before.

        For a URI subject (``<http://...>``) we open a default-collection
        FOR and add an ``_uri`` filter — same shape as the legacy
        ``pattern.subject.termType === 'NamedNode'`` branch.
        """
        if isinstance(subject, Variable):
            existing = self.state.var_to_doc_alias.get(str(subject))
            if existing is not None:
                return existing
            alias = self._open_collection(self.resolver.default_collection)
            self._bind_subject(subject, alias)
            return alias
        if isinstance(subject, URIRef):
            alias = self._open_collection(self.resolver.default_collection)
            bind = self.builder.bind(str(subject), hint="uri")
            self.builder.filter_eq(f"{alias}._uri", bind)
            return alias
        raise UnsupportedSparqlError(f"subject term type {type(subject).__name__!r} is not supported")

    def _bind_subject(self, subject: Any, alias: str) -> None:
        """Record that *alias*'s document represents the SPARQL *subject*.

        If the subject Variable is already bound to a *different* alias
        (which happens when two type patterns hit the same variable —
        e.g. ``?s a :Person . ?s a :Employee``), emit an equality
        FILTER to enforce the multi-class constraint rather than
        silently dropping the second alias's relationship to the
        variable. Otherwise the second FOR would float free of the
        first and the engine would emit the Cartesian product.
        """
        if not isinstance(subject, Variable):
            return
        name = str(subject)
        existing_alias = self.state.var_to_doc_alias.get(name)
        if existing_alias is None:
            self.state.var_to_doc_alias[name] = alias
            self.state.var_to_expr.setdefault(name, f"{alias}._uri")
            return
        if existing_alias == alias:
            return
        self.builder.filter_raw(f"{alias}._uri == {existing_alias}._uri")

    def _record_var_expr(self, var: Variable, expr: str) -> None:
        # First binding wins, matching legacy semantics: a variable that
        # appears in two triples gets its first-seen expression and any
        # later occurrence is enforced via FILTER (handled when we wire
        # multi-triple BGP joins).
        self.state.var_to_expr.setdefault(str(var), expr)

    # ------------------------------------------------------------------
    # FILTER expression translation
    # ------------------------------------------------------------------
    # SPARQL → AQL operator map for RelationalExpression. SPARQL uses
    # ``=`` / ``!=`` for equality; AQL needs ``==`` / ``!=``.
    _RELATIONAL_OP_MAP = {
        "=": "==",
        "!=": "!=",
        "<": "<",
        "<=": "<=",
        ">": ">",
        ">=": ">=",
    }

    def _translate_expr(self, expr: Any) -> str:
        """Translate a SPARQL Algebra expression node to an AQL expression
        string.

        Returns a parenthesized expression suitable for use in a
        ``FILTER`` clause. Every literal goes through
        :meth:`AqlQueryBuilder.bind` so AQL never sees inlined values.
        Mirrors the legacy ``filter-translator.js``'s
        ``translateFilterExpression`` / ``translateFilterTerm`` /
        ``translateFilterFunction`` switch.
        """
        # ----- Leaf terms -------------------------------------------------
        if isinstance(expr, Variable):
            mapped = self.state.var_to_expr.get(str(expr))
            if mapped is None:
                raise UnsupportedSparqlError(
                    f"FILTER references unbound variable ?{expr}; the BGP "
                    f"never bound it. Are you missing a triple pattern?"
                )
            return mapped
        if isinstance(expr, URIRef):
            return self.builder.bind(str(expr), hint="uri")
        if isinstance(expr, Literal):
            return self.builder.bind(_term_to_python(expr))

        name = getattr(expr, "name", None)
        if name is None:
            raise UnsupportedSparqlError(f"FILTER expression has no .name attribute: {type(expr).__name__}")

        # ----- Boolean composition ---------------------------------------
        if name == "ConditionalAndExpression":
            parts = [self._translate_expr(expr.expr)]
            for other in expr.other:
                parts.append(self._translate_expr(other))
            return "(" + " && ".join(parts) + ")"
        if name == "ConditionalOrExpression":
            parts = [self._translate_expr(expr.expr)]
            for other in expr.other:
                parts.append(self._translate_expr(other))
            return "(" + " || ".join(parts) + ")"

        # ----- Unary -----------------------------------------------------
        if name == "UnaryNot":
            return f"!({self._translate_expr(expr.expr)})"
        if name == "UnaryMinus":
            return f"(-{self._translate_expr(expr.expr)})"
        if name == "UnaryPlus":
            return self._translate_expr(expr.expr)

        # ----- Relational ------------------------------------------------
        if name == "RelationalExpression":
            op = expr.op
            if op in self._RELATIONAL_OP_MAP:
                left = self._translate_expr(expr.expr)
                right = self._translate_expr(expr.other)
                return f"({left} {self._RELATIONAL_OP_MAP[op]} {right})"
            if op in ("IN", "NOT IN"):
                left = self._translate_expr(expr.expr)
                # ``other`` for IN/NOT IN is a list of expressions to test
                # against; render as an inline AQL list.
                items = [self._translate_expr(item) for item in expr.other]
                aql_op = "IN" if op == "IN" else "NOT IN"
                return f"({left} {aql_op} [{', '.join(items)}])"
            raise UnsupportedSparqlError(f"unsupported relational operator in FILTER: {op!r}")

        # ----- Arithmetic ------------------------------------------------
        if name == "AdditiveExpression":
            return self._chain_binary(expr.expr, expr.op, expr.other)
        if name == "MultiplicativeExpression":
            return self._chain_binary(expr.expr, expr.op, expr.other)

        # ----- SPARQL builtins -------------------------------------------
        if name == "Builtin_BOUND":
            # AQL convention: a missing/null attribute returns null.
            # ``BOUND(?v)`` is true iff the binding is non-null.
            return f"({self._translate_expr(expr.arg)} != null)"
        if name == "Builtin_STR":
            return f"TO_STRING({self._translate_expr(expr.arg)})"
        if name == "Builtin_LCASE":
            return f"LOWER({self._translate_expr(expr.arg)})"
        if name == "Builtin_UCASE":
            return f"UPPER({self._translate_expr(expr.arg)})"
        if name == "Builtin_STRLEN":
            return f"LENGTH({self._translate_expr(expr.arg)})"
        if name == "Builtin_REGEX":
            text = self._translate_expr(expr.text)
            pattern = self._translate_expr(expr.pattern)
            # SPARQL passes regex flags as a string ("i" / "s" / "m" / "x");
            # AQL's REGEX_TEST takes a single boolean for case-insensitive.
            # Map ``i`` → caseInsensitive=true; warn on flags we cannot
            # express so the operator knows what was lost.
            flags_node = expr.get("flags")
            flag_str = ""
            if flags_node is not None:
                flag_str = flags_node.toPython() if isinstance(flags_node, Literal) else str(flags_node)
            case_insensitive = "true" if "i" in flag_str.lower() else "false"
            unsupported_flags = set(flag_str.lower()) - {"i", ""}
            if unsupported_flags:
                self.builder.warn(
                    code="W_REGEX_FLAGS_DROPPED",
                    message=(
                        f"REGEX flags {''.join(sorted(unsupported_flags))!r} are not "
                        f"supported by AQL REGEX_TEST and were ignored"
                    ),
                )
            return f"REGEX_TEST({text}, {pattern}, {case_insensitive})"
        if name == "Builtin_CONTAINS":
            return f"CONTAINS({self._translate_expr(expr.arg1)}, {self._translate_expr(expr.arg2)})"
        if name == "Builtin_STRSTARTS":
            return f"STARTS_WITH({self._translate_expr(expr.arg1)}, {self._translate_expr(expr.arg2)})"
        if name == "Builtin_STRENDS":
            return f"ENDS_WITH({self._translate_expr(expr.arg1)}, {self._translate_expr(expr.arg2)})"
        if name == "Builtin_isLiteral":
            # In our document model every value is either a primitive
            # (literal) or an _uri reference; treat non-string-IRI shapes
            # as literals. This is approximate; a real implementation
            # needs RDF-style typing.
            return f"(IS_STRING({self._translate_expr(expr.arg)}) || IS_NUMBER({self._translate_expr(expr.arg)}) || IS_BOOL({self._translate_expr(expr.arg)}))"

        raise UnsupportedSparqlError(
            f"FILTER expression node {name!r} is not yet supported (see "
            f"references/arango-sparql/src/lib/filter-translator.js for the "
            f"legacy implementation)"
        )

    def _chain_binary(self, head: Any, ops: list[str], tail: list[Any]) -> str:
        """Render an AdditiveExpression or MultiplicativeExpression.

        rdflib stores these with ``.expr`` (head), ``.op`` (a list of
        operator strings parallel to ``.other``), and ``.other`` (a list
        of subsequent operands). E.g. ``?a + 1 - 2`` becomes
        ``head=?a, ops=['+', '-'], tail=[1, 2]``.
        """
        result = self._translate_expr(head)
        for op, operand in zip(ops, tail, strict=True):
            result = f"({result} {op} {self._translate_expr(operand)})"
        return result

    # ------------------------------------------------------------------
    # Internal: projection / RETURN
    # ------------------------------------------------------------------
    def _emit_projection(self) -> None:
        if not self.state.projection_vars:
            # ``SELECT *`` lands here with an empty PV — fall back to
            # every variable we bound during BGP traversal so the query
            # still produces a useful result. Order is insertion order
            # of var_to_expr to keep the output stable.
            keys = list(self.state.var_to_expr.keys())
        else:
            keys = [str(v) for v in self.state.projection_vars]
        mapping: list[tuple[str, str]] = []
        for key in keys:
            expr = self.state.var_to_expr.get(key)
            if expr is None:
                raise AqlEmitError(
                    f"projection variable ?{key} was never bound by the BGP; "
                    f"the SPARQL query selects a variable that doesn't appear "
                    f"in WHERE."
                )
            mapping.append((key, expr))
        self.builder.return_object(mapping, distinct=self.state.distinct)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _triple_priority(triple: tuple[Any, Any, Any]) -> tuple[int, int]:
    """Sort key for BGP triples — lower = visit earlier.

    Order:
      0. Type patterns (``?s a :Class``) — bind subjects to physical
         collections first.
      1. Triples whose subject is a Variable — these can reuse a prior
         alias when sorted after a type pattern bound the same subject.
      2. Triples whose subject is a URI — they always open a fresh FOR.
    """
    s, p, o = triple
    if isinstance(p, URIRef) and p == RDF.type and isinstance(o, URIRef):
        primary = 0
    elif isinstance(s, Variable):
        primary = 1
    else:
        primary = 2
    # Stable secondary so two triples with the same primary preserve
    # rdflib's order — keeps golden output deterministic.
    return (primary, 0)


def _term_to_python(term: Any) -> Any:
    """Convert an rdflib Literal/URIRef into a JSON-safe Python value.

    Literals use ``Literal.toPython()`` so xsd:integer/xsd:decimal/
    xsd:dateTime round-trip through ArangoDB as the right primitive
    type rather than as their lexical form.
    """
    if isinstance(term, Literal):
        return term.toPython()
    if isinstance(term, URIRef):
        return str(term)
    return term
