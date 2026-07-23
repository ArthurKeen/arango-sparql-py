"""Cross-validation for ``MINUS`` containing ``OPTIONAL`` (ADR-0002
Problem 2 — the W3C ``negation/full-minuend`` and ``part-minuend``
cases).

These are the subtle ones: an ``OPTIONAL`` inside ``MINUS`` re-binds a
variable that the outer side already bound, so per SPARQL §18.2.5.2 the
optional triple is a *conditional add* (a compatibility test), and per
§8.3.4 an inner row only removes an outer row when it shares **at least
one bound** variable (the disjoint-domain exemption). Getting that truth
table right by hand is exactly the risk ADR-0002 flagged, so we pin it
against the W3C ground truth via pyoxigraph rather than goldens alone.

Both the document store and the pyoxigraph store are derived from the
*same* W3C ``.ttl`` data file, so they describe identical facts. Subjects
are routed to a collection by their ``rdf:type`` (``:Min`` → ``Min``,
``:Sub`` → ``Sub``, untyped → the default ``Document``) so the PG
ontology's type patterns resolve to real, type-filtered ``FOR`` loops —
the permissive Document model drops the ``?a a :Min`` filter, which would
make ``part-minuend``'s outer loop scan unrelated subjects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import rdflib

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.aql_interp import run_aql_subset
from tests.helpers.oxi import (
    assert_bindings_equal_ordered,
    drop_null_bindings,
    load_store,
    normalize_oxi_row,
    oxi_bindings,
)

oxi = pytest.importorskip("pyoxigraph", reason="pyoxigraph required for cross tests")

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
EX = "http://example/"
_NEG_DIR = Path(__file__).resolve().parents[1] / "w3c" / "data" / "sparql11-test-suite" / "negation"

# Maps the two named classes to dedicated PG collections; every other
# subject (the untyped ``?a`` rows in full-minuend) lands in Document.
_TYPE_TO_COLLECTION = {EX + "Min": "Min", EX + "Sub": "Sub"}

ONTOLOGY = """
@prefix : <http://example/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Min a owl:Class ; phys:collectionName "Min" .
:Sub a owl:Class ; phys:collectionName "Sub" .
"""


def _localname(iri: str) -> str:
    for sep in ("#", "/"):
        if sep in iri:
            return iri.rsplit(sep, 1)[1]
    return iri


def _docs_from_ttl(ttl_path: Path) -> dict[str, list[dict[str, Any]]]:
    """Build a PG document store from a W3C data file.

    One document per subject; each non-type triple becomes an attribute
    keyed by the predicate's local name (object IRIs stored as their
    bare string, literals as their Python value). The subject's
    ``rdf:type`` chooses its collection so the translator's type patterns
    resolve to the matching ``FOR`` loop.
    """
    graph = rdflib.Graph()
    graph.parse(ttl_path, format="turtle")

    collection_of: dict[str, str] = {}
    attrs_of: dict[str, dict[str, Any]] = {}
    for s, p, o in graph:
        subject = str(s)
        if str(p) == RDF_TYPE:
            collection_of[subject] = _TYPE_TO_COLLECTION.get(str(o), "Document")
            continue
        value = str(o) if isinstance(o, rdflib.URIRef) else o.toPython()
        attrs_of.setdefault(subject, {})[_localname(str(p))] = value

    docs: dict[str, list[dict[str, Any]]] = {}
    subjects = set(collection_of) | set(attrs_of)
    for subject in subjects:
        collection = collection_of.get(subject, "Document")
        docs.setdefault(collection, []).append({"_uri": subject, **attrs_of.get(subject, {})})
    return docs


CASES = [
    pytest.param("full-minuend", id="full_minuend"),
    pytest.param("part-minuend", id="part_minuend"),
]

# The W3C corpus is gitignored and only fetched by the corpus-aware CI jobs
# (w3c-coverage / integration, via scripts/fetch_w3c.sh) — not the unit `test`
# job. Skip cleanly when it is absent instead of raising FileNotFoundError.
pytestmark = pytest.mark.skipif(
    not _NEG_DIR.exists(),
    reason="W3C corpus not on disk; run scripts/fetch_w3c.sh",
)


@pytest.mark.cross
@pytest.mark.parametrize("name", CASES)
def test_minus_optional_matches_oxigraph(name: str) -> None:
    query = (_NEG_DIR / f"{name}.rq").read_text(encoding="utf-8")
    data_path = _NEG_DIR / f"{name}.ttl"

    result = translate(query, resolver=SchemaResolver.from_turtle(ONTOLOGY))
    actual = [
        drop_null_bindings(r) for r in run_aql_subset(result.aql, result.bind_vars, _docs_from_ttl(data_path))
    ]

    store = load_store([data_path])
    expected = [normalize_oxi_row(r) for r in oxi_bindings(store, query)]

    # Both queries ORDER BY ?a, so ordering is part of the contract.
    assert_bindings_equal_ordered(expected, actual)
