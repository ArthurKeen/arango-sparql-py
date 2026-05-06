"""Parameterized AQL query builder.

This is the **only** place AQL strings are assembled. Visitors call into
the builder; the builder owns alias minting, bind-variable naming, and
clause ordering. Direct string concatenation of AQL is forbidden — see
``.cursor/rules/100-backend-python.mdc``.

The clause set here is intentionally narrow — it covers what the BGP
+ SELECT path needs (FOR / FILTER / SORT / LIMIT / RETURN). Additional
clauses (LET, COLLECT, nested sub-queries) are added as the matching
visitor methods are ported via the SPARQL→AQL skill recipe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..errors import AqlEmitError

_AQL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _ClauseKind(Enum):
    FOR = 1
    FILTER = 2
    LET = 3
    SORT = 4
    LIMIT = 5
    RETURN = 6
    COLLECT = 7
    RAW = 99


@dataclass
class _Clause:
    kind: _ClauseKind
    text: str


@dataclass
class AqlQueryBuilder:
    """Fluent, parameter-safe AQL builder.

    Mirrors the surface of the legacy
    ``references/arango-sparql/src/lib/aql-query-builder.js`` so the
    porting recipe stays mechanical, while emitting bind-safe AQL using
    ArangoDB's standard ``@var`` (value) and ``@@coll`` (collection)
    placeholder conventions.
    """

    seed_params: dict[str, Any] = field(default_factory=dict)

    _alias_counter: int = 0
    _bind_counter: int = 0
    _coll_counter: int = 0
    _coll_bind_by_name: dict[str, str] = field(default_factory=dict)
    _bind_vars: dict[str, Any] = field(default_factory=dict)
    _body_clauses: list[_Clause] = field(default_factory=list)
    """FOR / FILTER / LET / RAW clauses, in the order the visitor adds them."""
    _sort_keys: list[str] = field(default_factory=list)
    """Pending ``SORT`` keys, joined into a single comma-separated clause
    by :meth:`finalize`. Each entry is a ``"<expr> <ASC|DESC>"`` string
    appended in visit order."""
    _limit_clause: _Clause | None = None
    _return_clause: _Clause | None = None
    _ask_mode: bool = False
    """When set, :meth:`finalize` wraps the assembled body in
    ``RETURN LENGTH(<body>) > 0`` so an ASK query produces a single
    boolean row rather than a SELECT-style projection."""
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        for k, v in self.seed_params.items():
            if k.startswith("_") or k.startswith("@"):
                raise ValueError(f"reserved bind-var name: {k!r}")
            self._bind_vars[k] = v

    # ------------------------------------------------------------------
    # Aliases & bind variables
    # ------------------------------------------------------------------
    def fresh_alias(self, prefix: str = "doc") -> str:
        """Return a unique FOR-loop variable name."""
        if not _AQL_IDENT_RE.match(prefix):
            raise ValueError(f"invalid alias prefix: {prefix!r}")
        self._alias_counter += 1
        return f"{prefix}{self._alias_counter}"

    def bind(self, value: Any, *, hint: str | None = None) -> str:
        """Register *value* as a bind variable and return its placeholder.

        Always use this for any literal or caller-supplied value — never
        inline a Python value into an AQL string.
        """
        self._bind_counter += 1
        suffix = ""
        if hint:
            if not _AQL_IDENT_RE.match(hint):
                raise ValueError(f"invalid bind hint: {hint!r}")
            suffix = f"_{hint}"
        name = f"_p{self._bind_counter}{suffix}"
        self._bind_vars[name] = value
        return f"@{name}"

    def bind_collection(self, collection_name: str) -> str:
        """Register a collection name as a ``@@coll`` bind variable.

        Reuses the same bind name for repeated occurrences of the same
        collection in a single query so ArangoDB plans them as the same
        relation instead of two separate references.
        """
        if not _AQL_IDENT_RE.match(collection_name):
            raise ValueError(f"invalid collection name: {collection_name!r}")
        cached = self._coll_bind_by_name.get(collection_name)
        if cached is not None:
            return f"@@{cached}"
        self._coll_counter += 1
        name = f"c{self._coll_counter}_{collection_name}"
        # AQL collection bind-var names must not start with a digit; the
        # ``c`` prefix above already guarantees that, but we double-check
        # via the ident regex on the full name to catch typos.
        if not _AQL_IDENT_RE.match(name):  # pragma: no cover - defensive
            raise AqlEmitError(f"generated invalid collection bind name {name!r}")
        self._coll_bind_by_name[collection_name] = name
        self._bind_vars[f"@{name}"] = collection_name
        return f"@@{name}"

    # ------------------------------------------------------------------
    # Clause emission
    # ------------------------------------------------------------------
    def for_(self, alias: str, collection: str) -> AqlQueryBuilder:
        """Emit ``FOR <alias> IN <collection>``.

        The collection is registered as a ``@@coll`` bind variable so
        the collection name never gets concatenated into the query
        string directly.
        """
        if not _AQL_IDENT_RE.match(alias):
            raise ValueError(f"invalid FOR alias: {alias!r}")
        coll_ref = self.bind_collection(collection)
        self._body_clauses.append(_Clause(_ClauseKind.FOR, f"FOR {alias} IN {coll_ref}"))
        return self

    def filter_eq(self, lhs: str, rhs_bind_placeholder: str) -> AqlQueryBuilder:
        """Emit ``FILTER <lhs> == <bind>``.

        ``lhs`` is a builder-emitted attribute path (e.g. ``doc1.name``);
        ``rhs_bind_placeholder`` must be the result of a prior
        :meth:`bind` call (i.e. ``@_pN``).
        """
        if not rhs_bind_placeholder.startswith("@"):
            raise AqlEmitError(f"filter_eq RHS must be a bind placeholder, got {rhs_bind_placeholder!r}")
        self._body_clauses.append(_Clause(_ClauseKind.FILTER, f"FILTER {lhs} == {rhs_bind_placeholder}"))
        return self

    def collect(
        self,
        *,
        keys: list[tuple[str, str]] | None = None,
        aggregates: list[tuple[str, str]] | None = None,
        count_into: str | None = None,
    ) -> AqlQueryBuilder:
        """Emit a ``COLLECT`` clause.

        Three idiomatic AQL forms:

        * ``COLLECT k1 = e1, k2 = e2``                  (group only)
        * ``COLLECT [k1 = e1] WITH COUNT INTO c``        (count shorthand)
        * ``COLLECT [k1 = e1] AGGREGATE a = f(e), …``    (full form)

        ``keys`` is the GROUP BY key list — pairs of ``(alias,
        expression)`` — emitted in declaration order. ``aggregates``
        likewise pairs ``(alias, "<AGG>(<expr>)")``. Pass ``count_into``
        for the ``WITH COUNT INTO`` shorthand (only valid when
        ``aggregates`` is empty); pass ``aggregates`` for the full form
        (which can also include ``COUNT``).

        Validation:
        * a query may have at most one ``COLLECT`` (AQL also forbids
          multiples without a sub-query wrapper).
        * post-COLLECT, every prior FOR alias is out of scope — callers
          that emit FILTER / LET / SORT / RETURN after a COLLECT must
          reference only the COLLECT's own output aliases.

        Mirrors the legacy ``aql-query-builder.js#collect`` shape that
        the SPARQL ``AggregateJoin`` visitor needs.
        """
        if any(c.kind == _ClauseKind.COLLECT for c in self._body_clauses):
            raise AqlEmitError("COLLECT already emitted; only one per query")
        if count_into is not None and aggregates:
            raise AqlEmitError("COLLECT cannot combine WITH COUNT INTO and AGGREGATE in the same clause")
        keys = keys or []
        aggregates = aggregates or []
        for alias, _ in keys:
            if not _AQL_IDENT_RE.match(alias):
                raise ValueError(f"invalid COLLECT key alias: {alias!r}")
        for alias, _ in aggregates:
            if not _AQL_IDENT_RE.match(alias):
                raise ValueError(f"invalid COLLECT aggregate alias: {alias!r}")
        if count_into is not None and not _AQL_IDENT_RE.match(count_into):
            raise ValueError(f"invalid COLLECT count_into alias: {count_into!r}")
        parts: list[str] = ["COLLECT"]
        if keys:
            parts.append(", ".join(f"{a} = {e}" for a, e in keys))
        if count_into is not None:
            parts.append(f"WITH COUNT INTO {count_into}")
        elif aggregates:
            parts.append("AGGREGATE " + ", ".join(f"{a} = {expr}" for a, expr in aggregates))
        self._body_clauses.append(_Clause(_ClauseKind.COLLECT, " ".join(parts)))
        return self

    def let(self, alias: str, expression: str) -> AqlQueryBuilder:
        """Emit ``LET <alias> = <expression>``.

        Used by ``visit_Extend`` (BIND) to introduce a SPARQL-bound
        variable as a first-class AQL identifier so subsequent FILTER /
        SORT / RETURN clauses can refer to it by name. Mirrors the
        legacy ``aql-query-builder.js#let``.

        ``expression`` is intentionally raw — callers must pre-bind any
        literals it references via :meth:`bind` before calling.
        """
        if not _AQL_IDENT_RE.match(alias):
            raise ValueError(f"invalid LET alias: {alias!r}")
        self._body_clauses.append(_Clause(_ClauseKind.LET, f"LET {alias} = {expression}"))
        return self

    def filter_raw(self, expression: str) -> AqlQueryBuilder:
        """Emit a raw ``FILTER <expression>``.

        Reserved for visitor methods that compose multi-operand filter
        expressions (FILTER NOT EXISTS, regex, complex boolean trees).
        Callers are responsible for binding every literal that appears
        in *expression* via :meth:`bind` first.
        """
        self._body_clauses.append(_Clause(_ClauseKind.FILTER, f"FILTER {expression}"))
        return self

    def sort(self, expression: str, *, descending: bool = False) -> AqlQueryBuilder:
        """Append one ORDER-BY key to the next ``SORT`` clause.

        Multiple consecutive ``sort()`` calls collapse into a single
        comma-separated AQL ``SORT a DESC, b ASC`` statement (the
        idiomatic form, matching the legacy
        ``aql-query-builder.js#sort`` behavior). Two separate ``SORT``
        clauses would also be valid AQL but they read as if the second
        re-sorts the first's output, so we prefer the canonical join.
        """
        direction = "DESC" if descending else "ASC"
        self._sort_keys.append(f"{expression} {direction}")
        return self

    def limit(self, count: int, *, offset: int = 0) -> AqlQueryBuilder:
        if not isinstance(count, int) or count < 0:
            raise ValueError(f"LIMIT count must be a non-negative int, got {count!r}")
        if not isinstance(offset, int) or offset < 0:
            raise ValueError(f"LIMIT offset must be a non-negative int, got {offset!r}")
        if self._limit_clause is not None:
            raise AqlEmitError("LIMIT already emitted; only one LIMIT per query")
        clause = f"LIMIT {offset}, {count}" if offset else f"LIMIT {count}"
        self._limit_clause = _Clause(_ClauseKind.LIMIT, clause)
        return self

    def return_object(
        self,
        mapping: list[tuple[str, str]],
        *,
        distinct: bool = False,
    ) -> AqlQueryBuilder:
        """Emit a ``RETURN { key: expr, ... }`` clause.

        ``mapping`` is a list of ``(key, expression)`` tuples in the
        order the SPARQL projection requested them. The order is
        preserved deterministically so golden-test diffs stay readable.
        """
        if self._return_clause is not None:
            raise AqlEmitError("RETURN already emitted; only one RETURN per query")
        for key, _ in mapping:
            if not _AQL_IDENT_RE.match(key):
                raise AqlEmitError(f"invalid RETURN key: {key!r}")
        body = ", ".join(f"{k}: {expr}" for k, expr in mapping)
        keyword = "RETURN DISTINCT" if distinct else "RETURN"
        self._return_clause = _Clause(_ClauseKind.RETURN, f"{keyword} {{ {body} }}")
        return self

    def return_scalar(self, expression: str) -> AqlQueryBuilder:
        """Emit ``RETURN <expression>`` — used by AskQuery (returns ``1``)
        and the future ConstructQuery / ValuesQuery emitters.
        """
        if self._return_clause is not None:
            raise AqlEmitError("RETURN already emitted; only one RETURN per query")
        self._return_clause = _Clause(_ClauseKind.RETURN, f"RETURN {expression}")
        return self

    def set_ask_mode(self) -> AqlQueryBuilder:
        """Flip the builder into ASK-output mode.

        The next :meth:`finalize` will wrap the whole assembled query in
        ``RETURN LENGTH(<inner>) > 0`` — the legacy
        ``aql-translator.js`` recipe for ``ASK`` (``ASK is essentially
        SELECT LIMIT 1``).
        """
        self._ask_mode = True
        return self

    def warn(self, *, code: str, message: str, **extra: Any) -> None:
        self.warnings.append({"code": code, "message": message, **extra})

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------
    def finalize(self) -> tuple[str, dict[str, Any]]:
        """Render the assembled clauses into a single AQL string.

        Decouples visitor traversal order from AQL clause order: body
        clauses (FOR/FILTER/LET/RAW) come out in insertion order, then
        SORT, LIMIT, and finally RETURN — regardless of what order the
        Algebra visitor walked them in. This is what lets
        ``visit_Slice`` (which receives LIMIT after the inner Project
        already emitted RETURN) work without contortions.
        """
        if self._return_clause is None:
            raise AqlEmitError("finalize() called without a RETURN; every query must terminate in RETURN")
        if not any(c.kind == _ClauseKind.FOR for c in self._body_clauses):
            raise AqlEmitError("query has no FOR clause; every BGP/SELECT translation needs at least one")
        ordered: list[_Clause] = list(self._body_clauses)
        if self._sort_keys:
            ordered.append(_Clause(_ClauseKind.SORT, "SORT " + ", ".join(self._sort_keys)))
        if self._limit_clause is not None:
            ordered.append(self._limit_clause)
        ordered.append(self._return_clause)
        body = "\n".join(c.text for c in ordered)
        if self._ask_mode:
            # Wrap the assembled body in a LENGTH() probe so the executor
            # gets a single-row boolean result rather than a SPARQL-shaped
            # projection. Mirrors the legacy
            # ``aql-translator.js#translateQuery`` ASK branch.
            indented = "\n".join("  " + line for line in body.splitlines())
            aql = f"RETURN LENGTH(\n{indented}\n) > 0"
        else:
            aql = body
        return aql, dict(self._bind_vars)
