"""pyoxigraph helpers — load RDF, run reference SPARQL, compare bindings.

The cross-validation tests under :mod:`tests.cross` use this module
to treat ``pyoxigraph`` as the W3C-compliant ground truth. Per
``.cursor/rules/200-testing.mdc``, individual tests must not import
``pyoxigraph`` directly — they go through these helpers so the
binding-equality semantics stay consistent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import pyoxigraph as oxi
except ImportError:  # pragma: no cover - pyoxigraph is in [dev]
    oxi = None  # type: ignore[assignment]


def load_store(rdf_paths: list[Path]) -> Any:
    """Load one or more RDF files into an in-memory pyoxigraph store."""
    if oxi is None:
        raise RuntimeError("pyoxigraph is not installed; add the [dev] extra")
    store = oxi.Store()
    for path in rdf_paths:
        store.load(path.read_bytes(), _format_for(path))
    return store


def load_store_from_string(ttl: str) -> Any:
    """Convenience helper for inline test data."""
    if oxi is None:
        raise RuntimeError("pyoxigraph is not installed; add the [dev] extra")
    store = oxi.Store()
    store.load(ttl.encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


def _format_for(path: Path) -> Any:
    suffix = path.suffix.lower()
    if oxi is None:  # pragma: no cover
        raise RuntimeError("pyoxigraph is not installed")
    if suffix in {".ttl", ".turtle"}:
        return oxi.RdfFormat.TURTLE
    if suffix in {".nt"}:
        return oxi.RdfFormat.N_TRIPLES
    if suffix in {".nq"}:
        return oxi.RdfFormat.N_QUADS
    if suffix in {".trig"}:
        return oxi.RdfFormat.TRIG
    raise ValueError(f"unsupported RDF file suffix: {suffix!r}")


def oxi_bindings(store: Any, sparql: str) -> list[dict[str, str]]:
    """Run *sparql* against *store* and return SELECT bindings as plain dicts.

    Values are stringified via the term's ``str()`` so equality comparison
    between pyoxigraph and ArangoDB-derived bindings does not depend on
    Python object identity.

    pyoxigraph 0.5 API: ``store.query()`` returns ``QuerySolutions`` which
    exposes ``.variables`` (a list of ``Variable`` objects); each
    ``QuerySolution`` row supports ``__getitem__`` by either a
    ``Variable`` or its name string and yields ``NamedNode | Literal | ...``
    or ``None`` for unbound projection variables.
    """
    result = store.query(sparql)
    variables = list(result.variables)
    rows: list[dict[str, str]] = []
    for solution in result:
        row: dict[str, str] = {}
        for var in variables:
            term = solution[var]
            if term is None:
                continue
            row[var.value] = str(term)
        rows.append(row)
    return rows


def assert_bindings_equal(
    expected: list[dict[str, str]],
    actual: list[dict[str, str]],
) -> None:
    """Order-insensitive bag equality on SELECT bindings."""
    exp = sorted(tuple(sorted(row.items())) for row in expected)
    act = sorted(tuple(sorted(row.items())) for row in actual)
    assert exp == act, f"bindings differ:\n  expected={exp}\n  actual={act}"


def assert_bindings_equal_ordered(
    expected: list[dict[str, str]],
    actual: list[dict[str, str]],
) -> None:
    """List-equality on SELECT bindings — preserves row order.

    Used by cross-validation tests that exercise ORDER BY semantics,
    where the ordering itself is the property under test. For tied
    sort keys (e.g. two rows with the same age) we still want a
    deterministic comparison, so the row dicts are turned into
    sorted-tuple keys before the position-by-position equality check.
    """
    exp = [tuple(sorted(row.items())) for row in expected]
    act = [tuple(sorted(row.items())) for row in actual]
    assert exp == act, f"ordered bindings differ:\n  expected={exp}\n  actual={act}"
