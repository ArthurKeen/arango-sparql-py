"""Cross-validation: BGP + SELECT (+ FILTER) semantics against pyoxigraph.

We treat ``pyoxigraph`` as the W3C-compliant ground truth (per
``.cursor/rules/200-testing.mdc``) and assert that the bindings our
translator would produce — when AQL is "executed" against an in-memory
mock store derived from the same triples — match what pyoxigraph
returns for the same SPARQL query.

We do **not** require a live ArangoDB for this test (those go under
the ``integration`` marker). Instead, we run the translated AQL
against a tiny pure-Python AQL-subset interpreter that understands
exactly the FOR / FILTER / LIMIT / RETURN shape the visitor emits
today. As the visitor grows new clauses (LET, COLLECT, joins) the
interpreter grows alongside, *or* this test gets re-pointed at
python-arango behind the ``cross`` + ``integration`` markers.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.oxi import (
    assert_bindings_equal,
    assert_bindings_equal_ordered,
    oxi_bindings,
)

oxi = pytest.importorskip("pyoxigraph", reason="pyoxigraph required for cross tests")

# A toy ontology + dataset that exercises BGP / type / property / literal-filter
# behavior end-to-end. Same triples are loaded into pyoxigraph (as RDF)
# and into the mock store (as ArangoDB-like docs).
ONTOLOGY_TTL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Person  a owl:Class ; phys:collectionName "Person" .
:Project a owl:Class ; phys:collectionName "Project" .
"""

DATA_TTL = """
@prefix : <http://ex.org/> .
:alice a :Person ; :name "Alice" ; :age 30 ; :dept "eng" ; :email "alice@example.com" .
:bob   a :Person ; :name "Bob"   ; :age 42 ; :dept "eng" ; :phone "+1-555-0123" .
:carol a :Person ; :name "Carol" ; :age 30 ; :dept "ops" .

:p1 a :Project ; :title "Apollo"   ; :owner :alice .
:p2 a :Project ; :title "Beacon"   ; :owner :bob .
:p3 a :Project ; :title "Catalyst" ; :owner :alice .
:p4 a :Project ; :title "Orphan" .
"""

# The same data, in the document shape the translator's AQL expects.
# ``_uri`` is the convention used by the legacy translator and adopted
# by ``visit_BGP``. Note ``email`` / ``phone`` are deliberately
# missing on some rows so OPTIONAL cross-validation exercises
# null-binding behaviour against pyoxigraph; ``dept`` is set on every
# row so GROUP BY cross-validation has stable group keys to compare.
ARANGO_DOCS: dict[str, list[dict[str, Any]]] = {
    "Person": [
        {
            "_uri": "http://ex.org/alice",
            "name": "Alice",
            "age": 30,
            "dept": "eng",
            "email": "alice@example.com",
        },
        {
            "_uri": "http://ex.org/bob",
            "name": "Bob",
            "age": 42,
            "dept": "eng",
            "phone": "+1-555-0123",
        },
        {"_uri": "http://ex.org/carol", "name": "Carol", "age": 30, "dept": "ops"},
    ],
    # ``Project`` exists for join cross-validation against pyoxigraph.
    # ``owner`` is deliberately missing on ``p4`` so a join via
    # ``?prj :owner ?p`` filters it out — same row pyoxigraph drops via
    # the unbound predicate.
    "Project": [
        {"_uri": "http://ex.org/p1", "title": "Apollo", "owner": "http://ex.org/alice"},
        {"_uri": "http://ex.org/p2", "title": "Beacon", "owner": "http://ex.org/bob"},
        {"_uri": "http://ex.org/p3", "title": "Catalyst", "owner": "http://ex.org/alice"},
        {"_uri": "http://ex.org/p4", "title": "Orphan"},
    ],
}


# ----------------------------------------------------------------------
# Tiny AQL-subset interpreter — covers exactly what the visitor emits
# today: FOR / FILTER / LIMIT / RETURN. FILTER expressions are
# transpiled to a constrained Python subset and ``eval``-ed against a
# whitelisted namespace; AQL builtins (REGEX_TEST, CONTAINS, …) are
# bound to Python equivalents.
# ----------------------------------------------------------------------
_FOR_RE = re.compile(r"FOR\s+(\w+)\s+IN\s+@@(\w+)")
_FILTER_RE = re.compile(r"FILTER\s+(.+)$")
_LET_RE = re.compile(r"LET\s+(\w+)\s*=\s*(.+)$")
_RETURN_RE = re.compile(r"RETURN(?:\s+(DISTINCT))?\s+\{\s*(.+?)\s*\}\s*$")
_LIMIT_RE = re.compile(r"LIMIT\s+(?:(\d+)\s*,\s*)?(\d+)")
_SORT_RE = re.compile(r"SORT\s+(.+)$")
# COLLECT clause patterns the visitor emits today:
#   COLLECT WITH COUNT INTO <c>
#   COLLECT k1 = e1[, k2 = e2 …] WITH COUNT INTO <c>
#   COLLECT k1 = e1[, k2 = e2 …] AGGREGATE a = f(e)[, …]
#   COLLECT AGGREGATE a = f(e)[, …]
# Parsed by splitting on ``WITH COUNT INTO`` and ``AGGREGATE`` rather
# than a single regex — keeps the parser readable as the grammar grows.
_COLLECT_RE = re.compile(r"^COLLECT\b")
# Projection pairs come in two shapes:
#   ``key: alias.attr``  — straight document attribute (every BGP triple)
#   ``key: bvN``         — LET-bound (BIND) alias minted by the visitor
# The unified form captures the value as one group and lets the
# interpreter decide which lookup to perform.
_PAIR_RE = re.compile(r"(\w+)\s*:\s*(\w+(?:\.\w+)?)")
_DOC_ATTR_RE = re.compile(r"\b(doc\d+)\.(\w+)\b")
# Post-rewrite namespace identifier shape — used by the eval namespace
# default-dict to recognise a missing-doc-attribute lookup as a
# null-binding (AQL parity) rather than a programmer error.
_DOC_ATTR_NAMESPACE_RE = re.compile(r"^doc\d+__\w+$")
_BIND_RE = re.compile(r"@(_\w+)")
# C-style ternary the visitor emits inside OPTIONAL+FILTER LETs:
#   ``(<cond> ? <true> : <false>)``
# Python's conditional is ``<true> if <cond> else <false>``, so we
# textually rewrite. The false branch in our visitor output is always a
# bare identifier or ``null`` (we never emit a complex false branch
# yet), which keeps this regex unambiguous; nested ternaries would need
# a depth-aware parser.
_TERNARY_RE = re.compile(r"\((.+?) \? (.+?) : (\w+(?:\.\w+)?|null)\)")


def _regex_test(text: Any, pattern: str, case_insensitive: bool) -> bool:
    if text is None:
        return False
    flags = re.IGNORECASE if case_insensitive else 0
    return bool(re.search(pattern, str(text), flags))


def _contains(haystack: Any, needle: str) -> bool:
    if haystack is None:
        return False
    return needle in str(haystack)


def _starts_with(s: Any, prefix: str) -> bool:
    return s is not None and str(s).startswith(prefix)


def _ends_with(s: Any, suffix: str) -> bool:
    return s is not None and str(s).endswith(suffix)


def _to_string(v: Any) -> str | None:
    return None if v is None else str(v)


# Whitelisted name → callable / value for the FILTER eval namespace.
# Anything not in this map and not a doc attribute / bind var triggers
# an explicit AssertionError so we notice when the visitor emits AQL
# the interpreter doesn't yet understand.
_AQL_BUILTINS: dict[str, Any] = {
    "REGEX_TEST": _regex_test,
    "CONTAINS": _contains,
    "STARTS_WITH": _starts_with,
    "ENDS_WITH": _ends_with,
    "TO_STRING": _to_string,
    # ``HAS(doc, "attr")`` — predicate-existence guard the visitor
    # emits for every variable-object BGP triple (``?s :p ?o``).
    # SPARQL §18.5 semantics: a required triple ``(s, p, o)`` only
    # matches when the predicate is actually present on the subject,
    # so missing-attribute documents must be excluded from the result.
    # The eval namespace registers each alias as the raw doc dict
    # so this builtin can do a real key-presence check; the rest of
    # the AQL rewriter still expands ``doc.attr`` to the flat
    # ``<alias>__<attr>`` form used elsewhere.
    "HAS": lambda doc, attr: isinstance(doc, dict) and attr in doc,
    "LOWER": lambda v: None if v is None else str(v).lower(),
    "UPPER": lambda v: None if v is None else str(v).upper(),
    "LENGTH": lambda v: None if v is None else len(v),
    "IS_STRING": lambda v: isinstance(v, str),
    "IS_NUMBER": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "IS_BOOL": lambda v: isinstance(v, bool),
    "true": True,
    "false": False,
    "null": None,
}


def _split_top_level_commas(s: str) -> list[str]:
    """Split *s* on commas at the top paren depth.

    Used to tear apart COLLECT key lists and AGGREGATE function lists
    where a function argument may itself contain commas
    (``CONCAT_SEPARATOR(", ", doc.name)``). Naive ``s.split(",")``
    would mis-split those.
    """
    out: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(s[start:i].strip())
            start = i + 1
    out.append(s[start:].strip())
    return [p for p in out if p]


def _parse_collect_clause(
    rest: str,
) -> tuple[
    list[tuple[str, str]],  # keys: (alias, expression)
    list[tuple[str, str]],  # aggregates: (alias, "FUNC(arg)")
    str | None,  # count_into alias
]:
    """Parse the body of a ``COLLECT …`` line into structured pieces.

    Accepts (after the leading ``COLLECT`` keyword):
      * ``""``                                   → no-op COLLECT
      * ``"WITH COUNT INTO c"``                  → count_into = "c"
      * ``"k1 = e1, k2 = e2"``                   → keys list, no aggregates
      * ``"AGGREGATE a = f(e), b = g(e)"``       → aggregates list, no keys
      * combinations of the above.
    """
    keys: list[tuple[str, str]] = []
    aggregates: list[tuple[str, str]] = []
    count_into: str | None = None
    rest = rest.strip()
    if "AGGREGATE" in rest:
        head, _, agg_part = rest.partition("AGGREGATE")
        rest = head.strip()
        for chunk in _split_top_level_commas(agg_part):
            alias, _, expr = chunk.partition("=")
            aggregates.append((alias.strip(), expr.strip()))
    if "WITH COUNT INTO" in rest:
        head, _, count_alias = rest.partition("WITH COUNT INTO")
        rest = head.strip()
        count_into = count_alias.strip()
    if rest:
        for chunk in _split_top_level_commas(rest):
            alias, _, expr = chunk.partition("=")
            keys.append((alias.strip(), expr.strip()))
    return keys, aggregates, count_into


def _aggregate_apply(func: str, values: list[Any]) -> Any:
    """Apply an AQL aggregate function name to a list of row values.

    Mirrors AQL semantics for null handling: ``MIN`` / ``MAX`` /
    ``AVG`` / ``SUM`` skip nulls; ``COUNT`` counts every row regardless
    of value (matching the AQL spec, which is a known divergence from
    SPARQL's ``COUNT(?x)`` "count bound rows" semantics — the visitor
    accepts that for now and the cross test exercises the AQL side).
    """
    non_null = [v for v in values if v is not None]
    if func == "COUNT":
        return len(values)
    if func == "COUNT_DISTINCT":
        return len({v for v in values if v is not None})
    if func == "SUM":
        return sum(non_null) if non_null else 0
    if func == "AVG":
        return sum(non_null) / len(non_null) if non_null else None
    if func == "MIN":
        return min(non_null) if non_null else None
    if func == "MAX":
        return max(non_null) if non_null else None
    raise AssertionError(f"interpreter does not implement aggregate {func!r}")


def _eval_expr(
    expr: str,
    env: dict[str, dict[str, Any]],
    bind_vars: dict[str, Any],
    let_env: dict[str, Any] | None = None,
) -> Any:
    """Evaluate an AQL value expression against the row environment.

    Performs a textual rewrite to a Python-compatible expression then
    ``eval``s it against a restricted namespace. Restrictions:

    - ``doc<N>.<attr>`` → ``doc<N>__<attr>`` (a flat namespace key).
    - ``@_p<N>`` → ``_p<N>`` (Python identifier).
    - ``&&`` → ``and``; ``||`` → ``or``; bare ``!`` → ``not ``.
    - ``null`` is mapped via the namespace to ``None``.

    LET-bound aliases (``bvN``) are valid Python identifiers and resolve
    via ``let_env`` without rewriting.

    Anything else (function calls, comparison operators, parentheses)
    is already valid Python. Returns the raw evaluated value — callers
    that need a boolean (FILTER) cast it themselves.
    """

    # ``_NullDefault`` makes missing ``doc<N>__<attr>`` lookups resolve
    # to ``None`` — that's AQL semantics: ``doc.foo`` on a doc without
    # ``foo`` returns null, never raises. Critical for OPTIONAL where
    # we project ``doc1.email`` against rows that have no ``email``.
    class _NullDefault(dict):
        def __missing__(self, key: str) -> Any:
            if _DOC_ATTR_NAMESPACE_RE.match(key):
                return None
            raise NameError(f"name {key!r} is not defined")

    namespace: dict[str, Any] = _NullDefault(_AQL_BUILTINS)
    for alias, doc in env.items():
        # Register the doc dict under its bare alias so HAS(doc1, "attr")
        # and similar dict-accepting builtins resolve correctly. The
        # flat ``<alias>__<attr>`` shape stays the primary access path
        # because the rest of the rewriter expects it (``doc1.foo`` →
        # ``doc1__foo``); HAS() is the only builtin in our matrix today
        # that takes a doc reference, not a doc attribute.
        namespace[alias] = doc
        for attr, value in doc.items():
            namespace[f"{alias}__{attr}"] = value
    namespace.update(bind_vars)
    if let_env:
        namespace.update(let_env)
    py = _TERNARY_RE.sub(r"((\2) if (\1) else \3)", expr)
    py = _DOC_ATTR_RE.sub(lambda m: f"{m.group(1)}__{m.group(2)}", py)
    py = _BIND_RE.sub(lambda m: m.group(1), py)
    py = py.replace("&&", " and ").replace("||", " or ")
    py = re.sub(r"!(?=\()", " not ", py)
    return eval(py, {"__builtins__": {}}, namespace)  # noqa: S307 - test-only


def _eval_filter(
    expr: str,
    env: dict[str, dict[str, Any]],
    bind_vars: dict[str, Any],
    let_env: dict[str, Any] | None = None,
) -> bool:
    return bool(_eval_expr(expr, env, bind_vars, let_env))


def _split_sort_keys(clause: str) -> list[tuple[str, str]]:
    """Split ``"a DESC, b ASC"`` into ``[("a", "DESC"), ("b", "ASC")]``.

    Naive comma split is fine here because the visitor never emits
    function calls inside SORT (the grammar would allow it; we just
    don't translate that yet). When ``visit_OrderBy`` learns to emit
    parenthesised expressions with embedded commas (e.g. ``(IF(?a,
    ?b, ?c))``), this helper grows a paren-depth tracker.
    """
    out: list[tuple[str, str]] = []
    for raw in clause.split(","):
        raw = raw.strip()
        # Last whitespace-delimited token is ASC|DESC; everything
        # before is the expression (preserving any internal spaces).
        head, _, direction = raw.rpartition(" ")
        if direction not in ("ASC", "DESC"):
            raise AssertionError(f"sort key missing ASC/DESC: {raw!r}")
        out.append((head.strip(), direction))
    return out


def run_aql_subset(
    aql: str,
    bind_vars: dict[str, Any],
    docs: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Execute the FOR/FILTER/LET/SORT/LIMIT/RETURN subset of AQL the
    visitor emits today against an in-memory document store. Multi-FOR
    queries are nested left-to-right (Cartesian product, like a real
    AQL plan).

    LET (BIND) bindings are evaluated once per row in source order and
    appear as plain identifiers in subsequent FILTER / SORT / RETURN
    expressions — same scoping rules as AQL.
    """
    lines = [line for line in aql.splitlines() if line.strip()]
    fors: list[tuple[str, str]] = []
    # body_steps preserves the source-order interleaving of FILTER and
    # LET — required because a LET'd alias must be in scope for any
    # subsequent FILTER, and a FILTER may need to short-circuit before
    # a LET that references a never-bound attribute.
    body_steps: list[tuple[str, str, str | None]] = []
    # COLLECT splits the row stream: everything before runs per-row
    # over the FOR cross-product; everything after runs per-group
    # over the aggregated rows. The visitor never emits more than one
    # COLLECT today (AQL also forbids it without a sub-query wrapper),
    # so a single optional clause is enough.
    collect_keys: list[tuple[str, str]] | None = None
    collect_aggregates: list[tuple[str, str]] | None = None
    collect_count_into: str | None = None
    post_collect_steps: list[tuple[str, str, str | None]] = []
    sort_keys: list[tuple[str, str]] = []
    limit: tuple[int, int] | None = None
    return_distinct = False
    return_pairs: list[tuple[str, str]] = []
    seen_collect = False

    def _push_step(step: tuple[str, str, str | None]) -> None:
        # Route FILTER / LET into the right bucket: anything emitted
        # after a COLLECT line targets the post-collect grouped-row
        # stream and references the COLLECT's output aliases only.
        if seen_collect:
            post_collect_steps.append(step)
        else:
            body_steps.append(step)

    for line in lines:
        if m := _FOR_RE.match(line):
            if seen_collect:  # pragma: no cover
                raise AssertionError(f"FOR after COLLECT not supported: {line!r}")
            alias, coll_var = m.groups()
            fors.append((alias, bind_vars[f"@{coll_var}"]))
        elif _COLLECT_RE.match(line):
            if seen_collect:  # pragma: no cover
                raise AssertionError(f"second COLLECT not supported: {line!r}")
            collect_keys, collect_aggregates, collect_count_into = _parse_collect_clause(
                line[len("COLLECT") :]
            )
            seen_collect = True
        elif m := _LET_RE.match(line):
            alias, expr = m.groups()
            _push_step(("LET", expr.strip(), alias))
        elif m := _FILTER_RE.match(line):
            _push_step(("FILTER", m.group(1).strip(), None))
        elif m := _SORT_RE.match(line):
            sort_keys.extend(_split_sort_keys(m.group(1).strip()))
        elif m := _LIMIT_RE.match(line):
            offset, count = m.groups()
            limit = (int(offset or "0"), int(count))
        elif m := _RETURN_RE.match(line):
            return_distinct = bool(m.group(1))
            for pair in _PAIR_RE.finditer(m.group(2)):
                return_pairs.append(pair.groups())
        else:  # pragma: no cover
            raise AssertionError(f"interpreter cannot handle AQL line: {line!r}")
    # Defer projection until after SORT so SORT keys can reference
    # attributes that aren't in the projection. Each accumulated env
    # carries (doc_env, let_env) so SORT/RETURN can read either.
    envs: list[tuple[dict[str, dict[str, Any]], dict[str, Any]]] = []

    def recurse(idx: int, env: dict[str, dict[str, Any]]) -> None:
        if idx == len(fors):
            let_env: dict[str, Any] = {}
            for kind, expr, alias in body_steps:
                if kind == "FILTER":
                    if not _eval_filter(expr, env, bind_vars, let_env):
                        return
                else:  # LET
                    assert alias is not None
                    let_env[alias] = _eval_expr(expr, env, bind_vars, let_env)
            envs.append((env, let_env))
            return
        alias, collection = fors[idx]
        for doc in docs.get(collection, []):
            recurse(idx + 1, {**env, alias: doc})

    recurse(0, {})

    if seen_collect:
        # Group the per-row envs by their COLLECT key tuple, then for
        # each group build a synthetic env whose ``let_env`` carries
        # the key aliases and aggregate aliases. The pre-COLLECT FOR
        # bindings are deliberately dropped — post-COLLECT they're out
        # of scope (matching AQL semantics), and the projection /
        # SORT / FILTER must reference only the COLLECT's outputs.
        keys = collect_keys or []
        aggregates = collect_aggregates or []
        groups: dict[tuple[Any, ...], list[tuple[dict, dict]]] = {}
        for env, let_env in envs:
            key_tuple = tuple(_eval_expr(expr, env, bind_vars, let_env) for _, expr in keys)
            groups.setdefault(key_tuple, []).append((env, let_env))
        new_envs: list[tuple[dict, dict]] = []
        # Sort groups by key tuple so the post-COLLECT row order is
        # deterministic for tests; SORT (if present) re-orders below.
        # ``None`` keys (unbound) sort last to match AQL.
        try:
            sorted_keys = sorted(groups.keys())
        except TypeError:
            # Heterogeneous key types (e.g. mixing str and int); fall
            # back to insertion order, which Python dicts preserve.
            sorted_keys = list(groups.keys())
        for key_tuple in sorted_keys:
            members = groups[key_tuple]
            new_let: dict[str, Any] = {}
            for (alias, _), value in zip(keys, key_tuple, strict=True):
                new_let[alias] = value
            for alias, agg_expr in aggregates:
                # Parse "FUNC(arg)" — single argument for now (the
                # CONCAT_SEPARATOR(sep, val) two-arg form would need a
                # second branch when GROUP_CONCAT lands in cross
                # tests, but the goldens cover it standalone).
                func, _, paren_arg = agg_expr.partition("(")
                arg = paren_arg.rstrip(")")
                values = [_eval_expr(arg, env, bind_vars, let_env) for env, let_env in members]
                new_let[alias] = _aggregate_apply(func.strip(), values)
            if collect_count_into is not None:
                new_let[collect_count_into] = len(members)
            new_envs.append(({}, new_let))
        envs = new_envs

        # Run post-COLLECT FILTER / LET steps over the grouped rows.
        filtered_envs: list[tuple[dict, dict]] = []
        for env, let_env in envs:
            keep = True
            for kind, expr, alias in post_collect_steps:
                if kind == "FILTER":
                    if not _eval_filter(expr, env, bind_vars, let_env):
                        keep = False
                        break
                else:  # LET
                    assert alias is not None
                    let_env[alias] = _eval_expr(expr, env, bind_vars, let_env)
            if keep:
                filtered_envs.append((env, let_env))
        envs = filtered_envs

    if sort_keys:
        # Stable, multi-key sort applied right-to-left so the leftmost
        # SORT key wins ties (Python's sort is stable). DESC is encoded
        # by reversing the natural order of the key column.
        for expr, direction in reversed(sort_keys):
            envs.sort(
                key=lambda pair, e=expr: _eval_expr(e, pair[0], bind_vars, pair[1]),
                reverse=(direction == "DESC"),
            )

    rows: list[dict[str, Any]] = []
    for env, let_env in envs:
        row: dict[str, Any] = {}
        for key, value_expr in return_pairs:
            if "." in value_expr:
                alias, attr = value_expr.split(".", 1)
                row[key] = env[alias].get(attr)
            else:
                # LET-bound alias (``bvN``) — produced by visit_Extend.
                row[key] = let_env.get(value_expr)
        rows.append(row)

    if return_distinct:
        seen: set[tuple] = set()
        deduped: list[dict[str, Any]] = []
        for r in rows:
            key = tuple(sorted(r.items()))
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        rows = deduped
    if limit is not None:
        offset, count = limit
        rows = rows[offset : offset + count]
    return rows


# ----------------------------------------------------------------------
# Helpers — load shared data into pyoxigraph and into the mock store.
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def oxi_store() -> Any:
    store = oxi.Store()
    store.load(DATA_TTL.encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


def _normalize_oxi_row(row: dict[str, str]) -> dict[str, Any]:
    """pyoxigraph stringifies values like ``"Alice"`` (with quotes) for
    Literals and ``http://...`` for IRIs. Strip the lexical envelope so
    the comparison sees plain Python values matching what the AQL
    interpreter returns."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if v.startswith('"'):
            # Literal in N-Triples lexical form: "Alice" or "30"^^xsd:integer
            inner = v.split('"^^')[0].strip('"')
            try:
                out[k] = int(inner)
            except ValueError:
                out[k] = inner
        elif v.startswith("<") and v.endswith(">"):
            out[k] = v[1:-1]
        else:
            out[k] = v
    return out


def _normalize_arango_row(row: dict[str, Any]) -> dict[str, Any]:
    # Drop None bindings so OPTIONAL parity holds against pyoxigraph,
    # which simply omits unbound variables from each solution row
    # (see ``oxi_bindings`` — ``term is None`` → ``continue``). For
    # non-OPTIONAL queries every projected attribute is bound so this
    # is a no-op.
    return {k: v for k, v in row.items() if v is not None}


# ----------------------------------------------------------------------
# Cases
# ----------------------------------------------------------------------
CASES = [
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person }",
        id="type_pattern",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s ?n WHERE { ?s a :Person ; :name ?n }",
        id="type_plus_property",
    ),
    pytest.param(
        'PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person ; :name "Alice" }',
        id="literal_filter",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person ; :age 30 }",
        id="integer_literal_filter",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT DISTINCT ?n WHERE { ?s a :Person ; :name ?n }",
        id="distinct_projection",
    ),
    # ----- FILTER cases ---------------------------------------------------
    pytest.param(
        'PREFIX : <http://ex.org/> SELECT ?s ?n WHERE { ?s a :Person ; :name ?n . FILTER(?n = "Alice") }',
        id="filter_equality",
    ),
    pytest.param(
        'PREFIX : <http://ex.org/> SELECT ?s ?n WHERE { ?s a :Person ; :name ?n . FILTER(?n != "Bob") }',
        id="filter_not_equals",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s ?a WHERE { ?s a :Person ; :age ?a . FILTER(?a > 30) }",
        id="filter_gt",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s ?a WHERE { ?s a :Person ; :age ?a . FILTER(?a >= 30 && ?a <= 40) }",
        id="filter_range_and",
    ),
    pytest.param(
        'PREFIX : <http://ex.org/> SELECT ?s ?n WHERE { ?s a :Person ; :name ?n . FILTER(?n = "Alice" || ?n = "Bob") }',
        id="filter_or",
    ),
    pytest.param(
        'PREFIX : <http://ex.org/> SELECT ?s ?n WHERE { ?s a :Person ; :name ?n . FILTER(REGEX(?n, "^A")) }',
        id="filter_regex",
    ),
    pytest.param(
        'PREFIX : <http://ex.org/> SELECT ?s ?n WHERE { ?s a :Person ; :name ?n . FILTER(REGEX(?n, "^a", "i")) }',
        id="filter_regex_case_insensitive",
    ),
    pytest.param(
        'PREFIX : <http://ex.org/> SELECT ?s ?n WHERE { ?s a :Person ; :name ?n . FILTER(CONTAINS(?n, "li")) }',
        id="filter_contains",
    ),
    pytest.param(
        'PREFIX : <http://ex.org/> SELECT ?s ?n WHERE { ?s a :Person ; :name ?n . FILTER(STRSTARTS(?n, "Al")) }',
        id="filter_strstarts",
    ),
]


@pytest.mark.cross
@pytest.mark.parametrize("sparql", CASES)
def test_bgp_select_matches_oxigraph(oxi_store: Any, sparql: str) -> None:
    resolver = SchemaResolver.from_turtle(ONTOLOGY_TTL)
    result = translate(sparql, resolver=resolver)
    actual = [_normalize_arango_row(r) for r in run_aql_subset(result.aql, result.bind_vars, ARANGO_DOCS)]
    expected = [_normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert_bindings_equal(expected, actual)


# ORDER BY cases need a separate parametrize because ordering is the
# property under test — set-equality would mask wrong-order failures.
ORDER_BY_CASES = [
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n WHERE { ?s a :Person ; :name ?n } ORDER BY ?n",
        id="order_by_name_asc",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?a WHERE { ?s a :Person ; :age ?a } ORDER BY DESC(?a)",
        id="order_by_age_desc",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?a WHERE { ?s a :Person ; :name ?n ; :age ?a } "
        "ORDER BY DESC(?a) ?n",
        id="order_by_age_desc_name_asc_tie_break",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n WHERE { ?s a :Person ; :name ?n ; :age ?a } "
        "ORDER BY ?n LIMIT 2 OFFSET 1",
        id="order_by_with_limit_offset",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT DISTINCT ?a WHERE { ?s a :Person ; :age ?a } ORDER BY ?a",
        id="order_by_distinct",
    ),
]


@pytest.mark.cross
@pytest.mark.parametrize("sparql", ORDER_BY_CASES)
def test_order_by_matches_oxigraph(oxi_store: Any, sparql: str) -> None:
    resolver = SchemaResolver.from_turtle(ONTOLOGY_TTL)
    result = translate(sparql, resolver=resolver)
    actual = [_normalize_arango_row(r) for r in run_aql_subset(result.aql, result.bind_vars, ARANGO_DOCS)]
    expected = [_normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert_bindings_equal_ordered(expected, actual)


# BIND cases — visit_Extend lowers ``BIND(<expr> AS ?v)`` to a LET. We
# re-validate the outputs against pyoxigraph to catch any divergence in
# expression semantics (string casing, arithmetic, length, …) between
# our AQL builtins map and SPARQL's reference builtins.
EXTEND_CASES = [
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?upper WHERE { "
        "?s a :Person ; :name ?n . BIND(UCASE(?n) AS ?upper) }",
        id="bind_ucase",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?lower WHERE { "
        "?s a :Person ; :name ?n . BIND(LCASE(?n) AS ?lower) }",
        id="bind_lcase",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?len WHERE { "
        "?s a :Person ; :name ?n . BIND(STRLEN(?n) AS ?len) }",
        id="bind_strlen",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?dbl WHERE { "
        "?s a :Person ; :name ?n ; :age ?a . BIND(?a * 2 AS ?dbl) "
        "FILTER(?dbl >= 60) }",
        id="bind_arith_then_filter",
    ),
]


@pytest.mark.cross
@pytest.mark.parametrize("sparql", EXTEND_CASES)
def test_extend_matches_oxigraph(oxi_store: Any, sparql: str) -> None:
    resolver = SchemaResolver.from_turtle(ONTOLOGY_TTL)
    result = translate(sparql, resolver=resolver)
    actual = [_normalize_arango_row(r) for r in run_aql_subset(result.aql, result.bind_vars, ARANGO_DOCS)]
    expected = [_normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert_bindings_equal(expected, actual)


# OPTIONAL cases — the data set has some persons with email, some with
# phone, some with neither, so the LEFT-JOIN behavior is observable in
# the cross-validation diff (not just the goldens).
LEFTJOIN_CASES = [
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?email WHERE { "
        "?s a :Person ; :name ?n . OPTIONAL { ?s :email ?email } }",
        id="optional_email",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?email ?phone WHERE { "
        "?s a :Person ; :name ?n . "
        "OPTIONAL { ?s :email ?email } "
        "OPTIONAL { ?s :phone ?phone } }",
        id="optional_two_blocks",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?email ?phone WHERE { "
        "?s a :Person ; :name ?n . "
        "OPTIONAL { ?s :email ?email ; :phone ?phone } }",
        id="optional_multi_var_one_block",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?email WHERE { "
        '?s a :Person ; :name ?n . OPTIONAL { ?s :email ?email . FILTER(STRSTARTS(?email, "a")) } }',
        id="optional_with_inner_filter",
    ),
]


@pytest.mark.cross
@pytest.mark.parametrize("sparql", LEFTJOIN_CASES)
def test_leftjoin_matches_oxigraph(oxi_store: Any, sparql: str) -> None:
    resolver = SchemaResolver.from_turtle(ONTOLOGY_TTL)
    result = translate(sparql, resolver=resolver)
    actual = [_normalize_arango_row(r) for r in run_aql_subset(result.aql, result.bind_vars, ARANGO_DOCS)]
    expected = [_normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert_bindings_equal(expected, actual)


# Aggregate cases — verify COUNT / SUM / AVG / MIN / MAX semantics
# against pyoxigraph's W3C-compliant evaluator. The interpreter's
# COLLECT branch has its own subtle null/distinct semantics so this
# is the cleanest place to pin them.
AGGREGATE_CASES = [
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT (COUNT(*) AS ?c) WHERE { ?s a :Person }",
        id="count_star",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT (COUNT(?s) AS ?c) WHERE { ?s a :Person }",
        id="count_var",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT (COUNT(DISTINCT ?d) AS ?c) WHERE { ?s a :Person ; :dept ?d }",
        id="count_distinct_dept",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?d (COUNT(?s) AS ?c) WHERE { ?s a :Person ; :dept ?d } GROUP BY ?d",
        id="group_by_count",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?d (SUM(?a) AS ?tot) (AVG(?a) AS ?avg) "
        "WHERE { ?s a :Person ; :dept ?d ; :age ?a } GROUP BY ?d",
        id="group_by_sum_avg",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?d (MIN(?a) AS ?mn) (MAX(?a) AS ?mx) "
        "WHERE { ?s a :Person ; :dept ?d ; :age ?a } GROUP BY ?d",
        id="group_by_min_max",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?d (COUNT(?s) AS ?c) WHERE { "
        "?s a :Person ; :dept ?d } GROUP BY ?d HAVING (COUNT(?s) > 1)",
        id="group_by_having",
    ),
]


@pytest.mark.cross
@pytest.mark.parametrize("sparql", AGGREGATE_CASES)
def test_aggregate_matches_oxigraph(oxi_store: Any, sparql: str) -> None:
    resolver = SchemaResolver.from_turtle(ONTOLOGY_TTL)
    result = translate(sparql, resolver=resolver)
    actual = [_normalize_arango_row(r) for r in run_aql_subset(result.aql, result.bind_vars, ARANGO_DOCS)]
    expected = [_normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert_bindings_equal(expected, actual)


# Multi-subject BGP / Join cases — verify that shared variables across
# typed FORs become equality FILTERs (effectively an inner join), and
# that disjoint groups produce a real Cartesian product. Both shapes
# need to round-trip through the AQL interpreter and match pyoxigraph.
JOIN_CASES = [
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?p ?o WHERE { "
        "?p a :Person ; :name ?n . ?o a :Project ; :owner ?p }",
        id="multi_subject_single_bgp",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?title WHERE { "
        "{ ?p a :Person ; :name ?n } "
        "{ ?prj a :Project ; :title ?title ; :owner ?p } }",
        id="explicit_join_grouped",
    ),
    pytest.param(
        # Disjoint groups → Cartesian product. With 3 Person rows and
        # 4 Project rows, expect 12 combinations from both sides.
        "PREFIX : <http://ex.org/> SELECT ?n ?title WHERE { "
        "{ ?p a :Person ; :name ?n } "
        "{ ?prj a :Project ; :title ?title } }",
        id="cross_join_disjoint",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?n ?title WHERE { "
        '{ ?p a :Person ; :name ?n . FILTER (STRSTARTS(?n, "A")) } '
        "{ ?prj a :Project ; :title ?title ; :owner ?p } }",
        id="join_with_filter_each_side",
    ),
    pytest.param(
        # 3-way join: Person ↔ Project (via owner) and an extra
        # ``?n`` projection that pulls Person.name through the join.
        "PREFIX : <http://ex.org/> SELECT ?n ?title WHERE { "
        "?p a :Person ; :name ?n . "
        "?prj a :Project ; :owner ?p ; :title ?title } "
        "ORDER BY ?n ?title",
        id="join_with_outer_order",
    ),
]


@pytest.mark.cross
@pytest.mark.parametrize("sparql", JOIN_CASES)
def test_join_matches_oxigraph(oxi_store: Any, sparql: str) -> None:
    resolver = SchemaResolver.from_turtle(ONTOLOGY_TTL)
    result = translate(sparql, resolver=resolver)
    actual = [_normalize_arango_row(r) for r in run_aql_subset(result.aql, result.bind_vars, ARANGO_DOCS)]
    expected = [_normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert_bindings_equal(expected, actual)
