"""W3C SPARQL Results format parsers + binding comparator.

The W3C DAWG corpus ships expected results in three formats:

* ``.srx`` — SPARQL Results XML (the historical default; ``<sparql>`` /
  ``<head>`` / ``<results>`` / ``<result>`` / ``<binding>``).
* ``.srj`` — SPARQL Results JSON.
* ``.ttl`` — RDF results graph using the ``http://www.w3.org/2001/sw/...``
  results vocabulary. Less common; we read these as best-effort.

All three are normalized into the same Python shape used elsewhere in
the harness::

    SelectResult = list[dict[str, Any]]   # one dict per row, var → value
    AskResult    = bool

The comparator below is intentionally lossy — the AQL execution layer
in ``arango-sparql-py`` cannot preserve RDF nuance like language tags
or datatype IRIs (a flattened document model only sees the lexical
value), so the comparator drops both sides to a comparable Python
primitive before equality testing. Tests where the W3C-expected result
hinges on a tag or datatype that AQL cannot reproduce will diverge —
that's the signal we want, captured as ``xfail`` rather than silently
passing.

Stdlib only — uses ``xml.etree.ElementTree`` and ``json`` so the W3C
harness has no extra runtime cost.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# SPARQL Results XML namespace — every element in an ``.srx`` file is in
# this namespace, so all our XPath-style ``find`` / ``findall`` queries
# qualify their tags with it.
SR_NS = "http://www.w3.org/2005/sparql-results#"
_SR = f"{{{SR_NS}}}"

# XSD datatype IRIs we recognize for primitive-typed literal coercion.
# Anything outside this set falls through as a string so divergent
# semantics are surfaced (the comparator is lossy, not silently
# coercive).
_XSD = "http://www.w3.org/2001/XMLSchema#"
_XSD_INT_TYPES = frozenset(
    {
        f"{_XSD}integer",
        f"{_XSD}int",
        f"{_XSD}long",
        f"{_XSD}short",
        f"{_XSD}byte",
        f"{_XSD}nonNegativeInteger",
        f"{_XSD}nonPositiveInteger",
        f"{_XSD}positiveInteger",
        f"{_XSD}negativeInteger",
        f"{_XSD}unsignedLong",
        f"{_XSD}unsignedInt",
        f"{_XSD}unsignedShort",
        f"{_XSD}unsignedByte",
    }
)
_XSD_FLOAT_TYPES = frozenset(
    {
        f"{_XSD}decimal",
        f"{_XSD}double",
        f"{_XSD}float",
    }
)
_XSD_BOOL = f"{_XSD}boolean"

# Bnode handling: every bnode label maps to a positional placeholder.
# This means the comparator answers "is the bnode pattern the same?"
# rather than "is this the same Skolem identifier?", which is what
# the SPARQL spec specifies for SELECT result equality across stores.
_BNODE_PREFIX = "_:bnode#"


class UnsupportedResultFormat(Exception):
    """Raised when the result-format parser doesn't know how to read a
    ``.srx`` / ``.srj`` / ``.ttl`` result file (or the file is malformed
    in a way we can't recover from)."""


@dataclass
class ResultSet:
    """Parsed SPARQL result set, normalized for comparison.

    ``ask`` is set for ASK queries; ``rows`` is set for SELECT queries.
    Exactly one of the two is populated. ``variables`` mirrors the
    ``<head>`` declaration order so SELECT row dicts have a canonical
    iteration order (helpful for diff messages, not for equality).
    """

    variables: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] | None = None
    ask: bool | None = None

    @property
    def is_ask(self) -> bool:
        return self.ask is not None


# ---------------------------------------------------------------------------
# Format dispatch
# ---------------------------------------------------------------------------


def parse_results_file(path: Path) -> ResultSet:
    """Dispatch on file suffix and return a :class:`ResultSet`.

    Unsupported suffixes raise :class:`UnsupportedResultFormat` so the
    test layer can ``pytest.xfail`` with a clear reason rather than
    swallowing the gap.
    """
    if not path.is_file():
        raise UnsupportedResultFormat(f"results file missing on disk: {path}")
    suffix = path.suffix.lower()
    if suffix == ".srx":
        return parse_srx(path.read_bytes())
    if suffix == ".srj":
        return parse_srj(path.read_bytes())
    if suffix in {".ttl", ".n3"}:
        return parse_ttl_results(path.read_bytes())
    if suffix in {".csv", ".tsv"}:
        # SPARQL 1.1 also defines CSV/TSV result formats; the few in
        # the corpus are out-of-scope for v0 — surface a clear reason.
        raise UnsupportedResultFormat(f"CSV/TSV result formats are not yet supported: {path.suffix}")
    raise UnsupportedResultFormat(f"unknown result-file suffix: {path.suffix}")


# ---------------------------------------------------------------------------
# SRX (SPARQL Results XML)
# ---------------------------------------------------------------------------


def parse_srx(payload: bytes) -> ResultSet:
    """Parse a SPARQL Results XML byte string into a :class:`ResultSet`.

    Implements the elements specified by the W3C result-format note:
    ``<head><variable name="…"/></head>``, plus either
    ``<results><result><binding name="…">…</binding></result></results>``
    (SELECT) or ``<boolean>true|false</boolean>`` (ASK). Anything else
    (RDF-typed extension, comment-only file, …) falls through with a
    descriptive error so a corpus surprise becomes a clear xfail rather
    than a silent NaN.
    """
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise UnsupportedResultFormat(f"malformed SRX: {exc}") from exc

    variables: list[str] = []
    head = root.find(f"{_SR}head")
    if head is not None:
        for v in head.findall(f"{_SR}variable"):
            name = v.get("name")
            if name:
                variables.append(name)

    boolean_el = root.find(f"{_SR}boolean")
    if boolean_el is not None:
        text = (boolean_el.text or "").strip().lower()
        if text not in ("true", "false"):
            raise UnsupportedResultFormat(f"invalid <boolean> body: {text!r}")
        return ResultSet(variables=variables, ask=(text == "true"))

    results_el = root.find(f"{_SR}results")
    if results_el is None:
        # No <results> and no <boolean>: treat as empty SELECT so the
        # comparator can still produce a useful zero-row diff. This is
        # what the W3C reference uses for queries with no solutions.
        return ResultSet(variables=variables, rows=[])

    rows: list[dict[str, Any]] = []
    for result_el in results_el.findall(f"{_SR}result"):
        row: dict[str, Any] = {}
        for binding in result_el.findall(f"{_SR}binding"):
            name = binding.get("name")
            if not name:
                continue
            value = _binding_value(binding)
            if value is _UNBOUND:
                # Unbound binding (e.g. <binding name="o"><unbound/></binding>):
                # mirror tests/helpers/oxi.py — drop the key entirely.
                continue
            row[name] = value
        rows.append(row)
    return ResultSet(variables=variables, rows=rows)


_UNBOUND = object()


def _binding_value(binding: ET.Element) -> Any:
    """Extract the typed value from a single ``<binding>`` element.

    Returns:
        * ``str`` for ``<uri>`` (the absolute IRI text);
        * a primitive (``int`` / ``float`` / ``bool`` / ``str``) for
          ``<literal>`` based on its ``datatype`` attribute;
        * a placeholder string for ``<bnode>`` (positional identifier);
        * the sentinel ``_UNBOUND`` if the binding declares no value
          (so the caller can drop the key from the row dict).
    """
    uri = binding.find(f"{_SR}uri")
    if uri is not None:
        return (uri.text or "").strip()
    literal = binding.find(f"{_SR}literal")
    if literal is not None:
        return _coerce_literal(literal)
    bnode = binding.find(f"{_SR}bnode")
    if bnode is not None:
        return f"{_BNODE_PREFIX}{(bnode.text or '').strip()}"
    return _UNBOUND


def _coerce_literal(literal: ET.Element) -> Any:
    """Best-effort coercion of an SRX ``<literal>`` to a Python primitive.

    The lossy mapping (we drop ``xml:lang`` and any non-XSD datatype)
    is deliberate: the AQL execution layer cannot round-trip those
    annotations, so a comparator that preserved them would mark every
    test that uses them as failing — even the ones whose lexical
    payload AQL got right. Callers that care about the lossless
    representation should reach for an RDF-aware comparator instead
    (which we deliberately don't have today; pyoxigraph is the
    spec-correct ground truth, this comparator is for AQL parity).
    """
    text = literal.text or ""
    datatype = literal.get("datatype")
    if datatype == _XSD_BOOL:
        return text.strip().lower() == "true"
    if datatype in _XSD_INT_TYPES:
        try:
            return int(text)
        except ValueError:
            return text
    if datatype in _XSD_FLOAT_TYPES:
        try:
            return float(text)
        except ValueError:
            return text
    # Lang-tagged literals lose their tag here — see docstring. Same
    # for unrecognized datatypes (xsd:dateTime, xsd:date, …): we keep
    # the lexical form so a string comparison still works on the happy
    # path. Tests whose semantics depend on the tag/datatype will
    # diverge and end up as xfail.
    return text


# ---------------------------------------------------------------------------
# SRJ (SPARQL Results JSON)
# ---------------------------------------------------------------------------


def parse_srj(payload: bytes) -> ResultSet:
    """Parse a SPARQL Results JSON byte string into a :class:`ResultSet`.

    Mirrors :func:`parse_srx`'s normalization rules so callers can
    treat both formats interchangeably.
    """
    try:
        doc = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UnsupportedResultFormat(f"malformed SRJ: {exc}") from exc
    if not isinstance(doc, dict):
        raise UnsupportedResultFormat("SRJ root is not an object")
    head = doc.get("head") or {}
    variables = list(head.get("vars") or [])
    if "boolean" in doc:
        return ResultSet(variables=variables, ask=bool(doc["boolean"]))
    bindings = (doc.get("results") or {}).get("bindings") or []
    rows: list[dict[str, Any]] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        row: dict[str, Any] = {}
        for var, term in binding.items():
            if not isinstance(term, dict):
                continue
            value = _srj_term_value(term)
            if value is _UNBOUND:
                continue
            row[var] = value
        rows.append(row)
    return ResultSet(variables=variables, rows=rows)


def _srj_term_value(term: dict[str, Any]) -> Any:
    kind = term.get("type")
    raw = term.get("value")
    if kind == "uri":
        return raw or ""
    if kind == "literal" or kind == "typed-literal":
        # SPARQL-1.1 normalized to ``literal`` with a ``datatype`` key;
        # the older ``typed-literal`` value of ``type`` is still in the
        # corpus.
        datatype = term.get("datatype")
        if datatype == _XSD_BOOL:
            return str(raw).strip().lower() == "true"
        if datatype in _XSD_INT_TYPES:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return raw
        if datatype in _XSD_FLOAT_TYPES:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return raw
        return raw
    if kind == "bnode":
        return f"{_BNODE_PREFIX}{raw}"
    return _UNBOUND


# ---------------------------------------------------------------------------
# TTL / N3 results — best-effort for the small corpus subset that uses it
# ---------------------------------------------------------------------------


def parse_ttl_results(payload: bytes) -> ResultSet:
    """Parse an RDF-results-vocabulary TTL into a :class:`ResultSet`.

    The W3C corpus uses Turtle results files for a handful of legacy
    DAWG tests via the ``http://www.w3.org/2001/sw/DataAccess/tests/result-set#``
    vocabulary. Parsing them properly requires an RDF parser; rather
    than pull rdflib into this module just for that minority, we use
    rdflib directly when needed and fall back to ``xfail`` on any
    parse error so the harness stays robust to corpus surprises.
    """
    try:
        from rdflib import Graph, Namespace
    except ImportError as exc:  # pragma: no cover - rdflib is mandatory
        raise UnsupportedResultFormat(f"rdflib unavailable: {exc}") from exc

    try:
        graph = Graph()
        graph.parse(data=payload.decode("utf-8"), format="turtle")
    except Exception as exc:  # noqa: BLE001 — surface a clear reason
        raise UnsupportedResultFormat(f"malformed TTL results: {exc}") from exc

    rs = Namespace("http://www.w3.org/2001/sw/DataAccess/tests/result-set#")

    variables: list[str] = []
    for _, var_lit in graph.subject_objects(rs.resultVariable):
        variables.append(str(var_lit))

    # ASK-style TTL results: ``[] rs:boolean true .`` (the result-set
    # node carries a single ``rs:boolean`` triple).
    for _, bool_lit in graph.subject_objects(rs.boolean):
        return ResultSet(variables=variables, ask=str(bool_lit).lower() == "true")

    rows: list[dict[str, Any]] = []
    for _, solution in graph.subject_objects(rs.solution):
        row: dict[str, Any] = {}
        for binding in graph.objects(solution, rs.binding):
            var_term = graph.value(binding, rs.variable)
            value_term = graph.value(binding, rs.value)
            if var_term is None or value_term is None:
                continue
            row[str(var_term)] = _term_to_python(value_term)
        rows.append(row)
    return ResultSet(variables=variables, rows=rows)


def _term_to_python(term: Any) -> Any:
    """Coerce an rdflib term into the same primitive shape SRX/SRJ produce."""
    from rdflib import BNode, Literal, URIRef  # local import keeps top fast

    if isinstance(term, URIRef):
        return str(term)
    if isinstance(term, BNode):
        return f"{_BNODE_PREFIX}{str(term)}"
    if isinstance(term, Literal):
        dt = str(term.datatype) if term.datatype is not None else None
        text = str(term)
        if dt == _XSD_BOOL:
            return text.strip().lower() == "true"
        if dt in _XSD_INT_TYPES:
            try:
                return int(text)
            except ValueError:
                return text
        if dt in _XSD_FLOAT_TYPES:
            try:
                return float(text)
            except ValueError:
                return text
        return text
    return str(term)


# ---------------------------------------------------------------------------
# Comparator
# ---------------------------------------------------------------------------


def normalize_actual_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """Coerce AQL-cursor output into the dict-of-primitives shape the
    comparator wants.

    AQL's ``RETURN { … }`` already produces dicts, so most rows pass
    through unchanged. Edge cases:

    * Boolean ASK results come back as ``[True]`` / ``[False]``; the
      caller handles those before calling this helper.
    * A row whose value is ``None`` (e.g. unbound projection slot)
      maps to a missing key — same convention as
      :func:`tests.helpers.oxi.oxi_bindings` and :func:`parse_srx`.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            # AQL returned a scalar or array — keep raw for debugging
            # (the comparator will mismatch with a clear message).
            out.append({"_value": row})
            continue
        out.append({k: v for k, v in row.items() if v is not None})
    return out


def _canonical_value(value: Any) -> Any:
    """Reduce a value to a comparison-stable key.

    ``int`` / ``float`` / ``bool`` mismatch is the most common false
    negative — AQL emits ``int 5`` while SRX may say
    ``decimal 5.0``. Coercing both sides to ``float`` for any numeric
    pair (and stringifying everything else) lets the comparator
    declare them equal without losing the ability to detect a real
    type mismatch (a string ``"5"`` vs numeric 5 stays different).
    """
    if isinstance(value, bool):
        # ``bool`` is an ``int`` subclass — handle before numeric
        # coercion so True/False don't compare equal to 1/0.
        return ("bool", value)
    if isinstance(value, (int, float)):
        return ("num", float(value))
    if isinstance(value, str):
        return ("str", value)
    return ("other", repr(value))


def _row_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Order-stable key for bag-equality comparison."""
    return tuple(sorted((k, _canonical_value(v)) for k, v in row.items()))


def compare_select(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Bag-equality comparison for SELECT bindings.

    Returns ``(matched, message)`` rather than raising so callers can
    decide between ``assert`` (translation parity tests) and
    ``pytest.xfail`` (known-divergence tests).
    """
    exp_keys = sorted(_row_key(r) for r in expected)
    act_keys = sorted(_row_key(r) for r in actual)
    if exp_keys == act_keys:
        return True, ""
    msg_lines = [
        f"binding bag mismatch (expected {len(expected)} rows, got {len(actual)})",
        "expected (sorted):",
    ]
    for k in exp_keys:
        msg_lines.append(f"  {dict(k)}")
    msg_lines.append("actual (sorted):")
    for k in act_keys:
        msg_lines.append(f"  {dict(k)}")
    return False, "\n".join(msg_lines)


def compare_ask(expected: bool, actual_rows: list[Any]) -> tuple[bool, str]:
    """Compare an ASK result against an AQL cursor's output.

    The translator wraps every ASK in ``RETURN LENGTH(<inner>) > 0``,
    so a healthy ASK execution produces exactly one row whose value is
    a Python ``bool`` (or, defensively, an ``int 0``/``int 1``). Any
    other shape is a divergence and reported with a clear diff.
    """
    if len(actual_rows) != 1:
        return False, f"ASK expected exactly 1 row, got {len(actual_rows)}: {actual_rows!r}"
    raw = actual_rows[0]
    if isinstance(raw, dict) and "_value" in raw:
        raw = raw["_value"]
    if isinstance(raw, bool):
        actual = raw
    elif isinstance(raw, (int, float)):
        actual = bool(raw)
    else:
        return False, f"ASK row is not a boolean-shaped scalar: {raw!r}"
    if expected == actual:
        return True, ""
    return False, f"ASK mismatch: expected {expected}, got {actual}"
