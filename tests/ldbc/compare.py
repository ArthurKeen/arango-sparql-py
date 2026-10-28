"""Compare an ArangoDB result with the oracle's, per the manifest (plan §5.3).

Values are canonicalised by the W3C comparator's rule
(``tests/w3c/srx_parser.py``: numbers fold to 12 significant digits, so an
AQL ``5`` equals an RDF ``5.0`` and float noise is ignored, while ``"5"``
still differs from ``5``). On top of that:

* ``GROUP_CONCAT`` columns are compared as sets of their parts: SPARQL
  leaves the order of the concatenated values undefined.
* ``empty_group_row``: for a query whose top level is a ``GROUP BY``, one row
  with nothing bound equals no rows. Over an empty input the W3C suite
  (``aggregates/agg-empty-group``) and this translator return one empty
  solution; pyoxigraph returns none.
* ``xsd:dateTime`` values are compared as instants: pyoxigraph returns the
  canonical lexical form (``…38.9Z``) where the data and AQL keep the stored
  one (``…38.900Z``). Equal instants match; different ones still differ.
* Ordering: ``total`` compares row by row; ``keys`` compares the sequence of
  the sort-key columns and the rows as a multiset (the ORDER BY does not
  determine the order of tied rows); ``none`` compares multisets.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from tests.w3c.srx_parser import _canonical_value

Ordering = Literal["total", "keys", "none"]

#: How many differing rows a mismatch message shows.
_SHOWN = 5

#: An xsd:dateTime lexical form with a time zone (all LDBC timestamps are UTC).
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def _canonical(value: Any) -> Any:
    """:func:`_canonical_value`, plus dateTime strings as UTC instants."""
    if isinstance(value, str) and _DATETIME.match(value):
        instant = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        return ("datetime", instant.isoformat())
    return _canonical_value(value)


@dataclass(frozen=True)
class Comparison:
    ok: bool
    message: str = ""


def _canonical_row(row: dict[str, Any], concat_columns: Sequence[str], separator: str) -> tuple[Any, ...]:
    items: list[tuple[str, Any]] = []
    for key, value in row.items():
        if value is None:
            continue  # unbound: the oracle omits it, AQL returns null
        if key in concat_columns and isinstance(value, str):
            parts = sorted(p for p in value.split(separator) if p != "")
            items.append((key, ("concat", tuple(parts))))
        else:
            items.append((key, _canonical(value)))
    return tuple(sorted(items))


def _drop_empty_group_row(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """W3C's one empty solution of an empty grouped aggregate, as no rows."""
    if len(rows) == 1 and all(v is None for v in rows[0].values()):
        return []
    return rows


def _show(rows: Sequence[tuple[Any, ...]]) -> str:
    shown = [dict(r) for r in rows[:_SHOWN]]
    more = f" (+{len(rows) - _SHOWN} more)" if len(rows) > _SHOWN else ""
    return f"{shown}{more}"


def compare(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    *,
    ordering: Ordering,
    order_keys: Sequence[str] = (),
    concat_columns: Sequence[str] = (),
    concat_separator: str = ", ",
    empty_group_row: bool = False,
) -> Comparison:
    if empty_group_row:
        expected, actual = (_drop_empty_group_row(rows) for rows in (expected, actual))
    exp = [_canonical_row(r, concat_columns, concat_separator) for r in expected]
    act = [_canonical_row(r, concat_columns, concat_separator) for r in actual]
    if len(exp) != len(act):
        missing = sorted(set(exp) - set(act), key=repr)
        extra = sorted(set(act) - set(exp), key=repr)
        return Comparison(
            False,
            f"expected {len(exp)} rows, got {len(act)}; missing {_show(missing)}; unexpected {_show(extra)}",
        )
    if ordering == "total":
        for index, (e, a) in enumerate(zip(exp, act, strict=True)):
            if e != a:
                return Comparison(False, f"row {index} differs: expected {dict(e)}, got {dict(a)}")
        return Comparison(True)
    if ordering == "keys":

        def keys(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
            return [tuple(_canonical(r.get(k)) for k in order_keys) for r in rows]

        if keys(expected) != keys(actual):
            return Comparison(False, f"sort-key sequence {list(order_keys)} differs")
    if sorted(exp, key=repr) != sorted(act, key=repr):
        missing = sorted(set(exp) - set(act), key=repr)
        extra = sorted(set(act) - set(exp), key=repr)
        return Comparison(False, f"rows differ: missing {_show(missing)}; unexpected {_show(extra)}")
    return Comparison(True)
