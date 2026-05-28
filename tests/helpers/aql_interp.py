"""Tiny AQL-subset interpreter for cross-validation tests.

Cross-validation tests treat ``pyoxigraph`` as the W3C ground truth and
assert that the AQL our translator emits — when "executed" against an
in-memory document store derived from the same triples — yields the
same bindings. Rather than stand up a live ArangoDB for every case
(those go under the ``integration`` marker), we run the translated AQL
through this pure-Python interpreter that understands exactly the
clause shapes the visitor emits today:

    FOR / FILTER / LET / COLLECT / SORT / LIMIT / RETURN

It is deliberately *not* a general AQL engine. The grammar grows only
when the visitor learns to emit a new clause, and every extension is
covered by a cross test so the interpreter cannot silently diverge from
real AQL semantics.

This module is the single home for the interpreter so every
cross-validation module (PG-only ``test_bgp_select_cross`` and the
multi-model ``test_multimodel_cross``) shares one implementation —
mirroring how :mod:`tests.helpers.oxi` centralises the pyoxigraph side.
"""

from __future__ import annotations

import re
from typing import Any

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
# WITH-clause prelude the RPT/sharded translator emits to declare the
# collections a traversal touches: ``WITH @@c1, @@c2 RETURN …`` — the
# interpreter treats it as a no-op (it does not need collection
# declarations to resolve ``FOR … IN @@coll`` bind vars).
_WITH_RE = re.compile(r"^WITH\b")
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


def _regex_test(text: Any, pattern: str, case_insensitive: bool = False) -> bool:
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


def _coalesce(*args: Any) -> Any:
    """AQL ``COALESCE`` — first non-null argument, else null.

    The RPT translator emits ``COALESCE(t.object_uri, t.object_value)``
    everywhere it reads a triple's object, because in a triples table a
    given object is stored in exactly one of the two columns (URI vs
    literal) and the other is null.
    """
    for a in args:
        if a is not None:
            return a
    return None


# Whitelisted name → callable / value for the eval namespace. Anything
# not in this map and not a doc attribute / bind var triggers an
# explicit error so we notice when the visitor emits AQL the
# interpreter doesn't yet understand.
_AQL_BUILTINS: dict[str, Any] = {
    "REGEX_TEST": _regex_test,
    "CONTAINS": _contains,
    "STARTS_WITH": _starts_with,
    "ENDS_WITH": _ends_with,
    "TO_STRING": _to_string,
    "COALESCE": _coalesce,
    # ``HAS(doc, "attr")`` — predicate-existence guard the visitor
    # emits for every variable-object BGP triple (``?s :p ?o``).
    # SPARQL §18.5 semantics: a required triple ``(s, p, o)`` only
    # matches when the predicate is actually present on the subject,
    # so missing-attribute documents must be excluded from the result.
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

    Used to tear apart COLLECT key lists, AGGREGATE function lists, and
    RETURN projection bodies where a function argument may itself
    contain commas (``COALESCE(t.object_uri, t.object_value)``). Naive
    ``s.split(",")`` would mis-split those.
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
        # ``doc1__foo``).
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

    Top-level-comma splitting (not a naive ``split(",")``) so a sort key
    that is itself a function call with comma-separated arguments —
    ``COALESCE(t.object_uri, t.object_value) DESC`` under the RPT model
    — is kept whole. The trailing whitespace-delimited token is the
    direction; everything before it is the (possibly parenthesised)
    expression.
    """
    out: list[tuple[str, str]] = []
    for raw in _split_top_level_commas(clause):
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
    """Execute the FOR/FILTER/LET/COLLECT/SORT/LIMIT/RETURN subset of
    AQL the visitor emits today against an in-memory document store.

    Multi-FOR queries are nested left-to-right (Cartesian product, like
    a real AQL plan); shared-variable equality FILTERs collapse that to
    the intended join. This is exactly how a multi-triple BGP lowers
    under both the PG/LPG model (one FOR per class) and the RPT model
    (one FOR per triple, self-joined on ``subject_uri``).

    LET (BIND) bindings are evaluated once per row in source order and
    appear as plain identifiers in subsequent FILTER / SORT / RETURN
    expressions — same scoping rules as AQL.
    """
    lines = [line for line in aql.splitlines() if line.strip()]
    fors: list[tuple[str, str]] = []
    body_steps: list[tuple[str, str, str | None]] = []
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
        if seen_collect:
            post_collect_steps.append(step)
        else:
            body_steps.append(step)

    for line in lines:
        if _WITH_RE.match(line):
            # ``WITH @@c1, @@c2`` collection prelude — no-op for the
            # interpreter; the FOR lines that follow carry the bind
            # vars we actually resolve against.
            continue
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
            # Projection values can be bare doc attributes
            # (``doc1.name``), LET aliases (``bv1``), or full
            # expressions (``COALESCE(doc2.object_uri,
            # doc2.object_value)`` under RPT). Split on top-level
            # commas so a function-call value's internal comma does not
            # tear the pair apart, then partition each pair on its
            # *first* colon (an expression never contains a top-level
            # colon in our emitted AQL — the only colon is the
            # key/value separator).
            for pair in _split_top_level_commas(m.group(2)):
                key, _, value_expr = pair.partition(":")
                return_pairs.append((key.strip(), value_expr.strip()))
        else:  # pragma: no cover
            raise AssertionError(f"interpreter cannot handle AQL line: {line!r}")

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
        keys = collect_keys or []
        aggregates = collect_aggregates or []
        groups: dict[tuple[Any, ...], list[tuple[dict, dict]]] = {}
        for env, let_env in envs:
            key_tuple = tuple(_eval_expr(expr, env, bind_vars, let_env) for _, expr in keys)
            groups.setdefault(key_tuple, []).append((env, let_env))
        new_envs: list[tuple[dict, dict]] = []
        try:
            sorted_keys = sorted(groups.keys())
        except TypeError:
            sorted_keys = list(groups.keys())
        for key_tuple in sorted_keys:
            members = groups[key_tuple]
            new_let: dict[str, Any] = {}
            for (alias, _), value in zip(keys, key_tuple, strict=True):
                new_let[alias] = value
            for alias, agg_expr in aggregates:
                func, _, paren_arg = agg_expr.partition("(")
                arg = paren_arg.rstrip(")")
                values = [_eval_expr(arg, env, bind_vars, let_env) for env, let_env in members]
                new_let[alias] = _aggregate_apply(func.strip(), values)
            if collect_count_into is not None:
                new_let[collect_count_into] = len(members)
            new_envs.append(({}, new_let))
        envs = new_envs

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
        for expr, direction in reversed(sort_keys):
            envs.sort(
                key=lambda pair, e=expr: _eval_expr(e, pair[0], bind_vars, pair[1]),
                reverse=(direction == "DESC"),
            )

    rows: list[dict[str, Any]] = []
    for env, let_env in envs:
        row: dict[str, Any] = {}
        for key, value_expr in return_pairs:
            row[key] = _eval_expr(value_expr, env, bind_vars, let_env)
        rows.append(row)

    if return_distinct:
        seen: set[tuple] = set()
        deduped: list[dict[str, Any]] = []
        for r in rows:
            dedup_key = tuple(sorted(r.items()))
            if dedup_key not in seen:
                seen.add(dedup_key)
                deduped.append(r)
        rows = deduped
    if limit is not None:
        offset, count = limit
        rows = rows[offset : offset + count]
    return rows
