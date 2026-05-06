"""W3C SPARQL 1.1 DAWG harness runner.

Mirrors :mod:`tests.tck.runner` from ``arango-cypher-py``. Enumerates
W3C manifests via ``rdflib`` and yields one parametrized pytest case
per ``mf:Manifest`` entry.

The W3C corpus itself is **not** vendored in this repo — fetch it with
``scripts/fetch_w3c.sh`` (or set ``W3C_SPARQL_TESTS_DIR`` to point at an
existing checkout). Tests are skipped with a clear message if the
corpus is missing rather than failing the suite.

Manifest grammar (per the DAWG test-manifest vocabulary)
--------------------------------------------------------

A leaf manifest TTL declares one ``mf:Manifest`` whose ``mf:entries``
points at an RDF list of test IRIs. Each test carries:

* ``rdf:type`` — one of ``mf:QueryEvaluationTest``,
  ``mf:PositiveSyntaxTest11``, ``mf:NegativeSyntaxTest11``,
  plus the SPARQL 1.1 update / protocol / service-description /
  CSV variants that we recognize but treat as out-of-scope today.
* ``mf:name`` — human-readable label.
* ``mf:action`` — either a query file IRI (syntax tests) or a
  blank node carrying ``qt:query`` + ``qt:data`` (+ optional
  ``qt:graphData`` for named graphs).
* ``mf:result`` — for evaluation tests, an .srx / .srj / .ttl
  expected-results file.

Relative IRIs (``<agg01.rq>``) resolve against the manifest's own
``file://`` URI when ``rdflib`` parses with ``source=...``; we strip
the ``file://`` prefix to map back to filesystem paths.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from rdflib import RDF, Graph, Namespace, URIRef
from rdflib.collection import Collection

logger = logging.getLogger(__name__)

W3C_ENV_VAR = "W3C_SPARQL_TESTS_DIR"
DEFAULT_W3C_DIR = Path(__file__).parent / "data"

MF = Namespace("http://www.w3.org/2001/sw/DataAccess/tests/test-manifest#")
QT = Namespace("http://www.w3.org/2001/sw/DataAccess/tests/test-query#")

# Test types we surface to the harness. Anything else is recorded but
# returned with ``test_type="<LocalName>"`` so the harness can decide to
# skip / xfail on it explicitly rather than silently dropping coverage
# numbers.
QUERY_EVAL = "QueryEvaluationTest"
POS_SYNTAX_11 = "PositiveSyntaxTest11"
NEG_SYNTAX_11 = "NegativeSyntaxTest11"

# Test types that are explicitly out of scope for the SPARQL→AQL
# transpiler today but we still want to count in the coverage report.
OUT_OF_SCOPE_TYPES = frozenset(
    {
        "UpdateEvaluationTest",
        "PositiveUpdateSyntaxTest11",
        "NegativeUpdateSyntaxTest11",
        "ProtocolTest",
        "ServiceDescriptionTest",
        "CSVResultFormatTest",
    }
)


@dataclass
class W3CTestCase:
    """One row from a W3C manifest, normalized for pytest consumption."""

    iri: str
    """Full test IRI (e.g. ``http://.../aggregates/manifest#agg01``)."""

    name: str
    """Human-readable label from ``mf:name``."""

    test_type: str
    """Local name of ``rdf:type`` (e.g. ``QueryEvaluationTest``)."""

    manifest_path: Path
    """The ``manifest.ttl`` this case was discovered in."""

    query_path: Path | None = None
    data_paths: list[Path] = field(default_factory=list)
    graph_data_paths: list[Path] = field(default_factory=list)
    expected_path: Path | None = None

    @property
    def short_id(self) -> str:
        """Stable identifier for pytest parametrize ids — manifest
        directory + IRI fragment so the test name is recognizable in
        ``pytest -v`` output without colliding across directories."""
        frag = self.iri.rsplit("#", 1)[-1] if "#" in self.iri else self.iri.rsplit("/", 1)[-1]
        return f"{self.manifest_path.parent.name}/{frag}"


# ---------------------------------------------------------------------------
# Corpus discovery
# ---------------------------------------------------------------------------


def w3c_corpus_root() -> Path | None:
    """Return the root of the W3C corpus, or ``None`` when absent.

    Prefers the ``W3C_SPARQL_TESTS_DIR`` env override (useful when the
    corpus lives outside the repo) and falls back to the gitignored
    ``tests/w3c/data/`` directory populated by ``scripts/fetch_w3c.sh``.
    """
    override = os.environ.get(W3C_ENV_VAR)
    if override:
        p = Path(override).expanduser()
        return p if p.is_dir() else None
    return DEFAULT_W3C_DIR if DEFAULT_W3C_DIR.is_dir() else None


def discover_manifests(root: Path) -> list[Path]:
    """Return every ``manifest.ttl`` under *root*, sorted for deterministic
    iteration (so pytest collection order is stable across runs).

    The top-level ``manifest-all.ttl`` is intentionally excluded — it
    only carries ``mf:include`` pointers and no test entries, and
    walking the leaf manifests directly is both simpler and faster.
    """
    return sorted(p for p in root.rglob("manifest.ttl") if p.is_file())


# ---------------------------------------------------------------------------
# Per-manifest enumeration
# ---------------------------------------------------------------------------


def iter_manifest_cases(root: Path) -> Iterator[W3CTestCase]:
    """Walk every ``manifest.ttl`` under *root* and yield test cases."""
    for manifest_path in discover_manifests(root):
        try:
            yield from _iter_one_manifest(manifest_path)
        except Exception as exc:  # noqa: BLE001 — surface and continue
            # Manifests sometimes have inconsistent serialization (e.g.
            # service-description carries optional Turtle 1.1 features
            # that older rdflib versions choke on). Don't kill the whole
            # discovery walk — log and move on so coverage is partial
            # but honest.
            logger.warning("failed to load manifest %s: %s", manifest_path, exc)
            continue


def _iter_one_manifest(manifest_path: Path) -> Iterator[W3CTestCase]:
    graph = Graph()
    graph.parse(source=str(manifest_path), format="turtle")

    manifest_iri = URIRef(manifest_path.resolve().as_uri())
    entries_node = graph.value(manifest_iri, MF.entries)
    if entries_node is None:
        return  # ``manifest-all.ttl`` and similar — nothing to enumerate

    for test_iri in Collection(graph, entries_node):
        if not isinstance(test_iri, URIRef):
            continue
        case = _build_case(graph, test_iri, manifest_path)
        if case is not None:
            yield case


def _build_case(
    graph: Graph,
    test_iri: URIRef,
    manifest_path: Path,
) -> W3CTestCase | None:
    type_iri = graph.value(test_iri, RDF.type)
    if not isinstance(type_iri, URIRef):
        return None
    test_type = _local_name(type_iri)

    name_term = graph.value(test_iri, MF.name)
    name = str(name_term) if name_term is not None else _local_name(test_iri)

    action = graph.value(test_iri, MF.action)
    expected_term = graph.value(test_iri, MF.result)
    expected_path = _term_to_path(expected_term)

    query_path: Path | None = None
    data_paths: list[Path] = []
    graph_data_paths: list[Path] = []

    if isinstance(action, URIRef):
        # Syntax tests use mf:action with a direct query file IRI.
        query_path = _term_to_path(action)
    elif action is not None:
        # Evaluation tests use a blank node carrying qt:query / qt:data.
        query_term = graph.value(action, QT.query)
        query_path = _term_to_path(query_term)
        for data_term in graph.objects(action, QT.data):
            path = _term_to_path(data_term)
            if path is not None:
                data_paths.append(path)
        for graph_term in graph.objects(action, QT.graphData):
            path = _term_to_path(graph_term)
            if path is not None:
                graph_data_paths.append(path)

    return W3CTestCase(
        iri=str(test_iri),
        name=name,
        test_type=test_type,
        manifest_path=manifest_path,
        query_path=query_path,
        data_paths=data_paths,
        graph_data_paths=graph_data_paths,
        expected_path=expected_path,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local_name(iri: URIRef | str) -> str:
    text = str(iri)
    for sep in ("#", "/"):
        if sep in text:
            return text.rsplit(sep, 1)[-1]
    return text


def _term_to_path(term: object) -> Path | None:
    """Convert a ``file://`` URIRef into a filesystem path.

    Anything that isn't a ``file://`` URIRef — e.g. a literal, a blank
    node, an ``http://`` IRI we can't fetch — returns ``None`` so the
    harness can decide whether the test is still runnable.
    """
    if not isinstance(term, URIRef):
        return None
    parsed = urlparse(str(term))
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path))


# ---------------------------------------------------------------------------
# Skip-list parsing
# ---------------------------------------------------------------------------


_SKIP_DEFAULT = Path(__file__).parent / "SKIP_REASONS.md"
# Match a Markdown table row whose first column is a quoted/unquoted
# IRI. We tolerate both ``| http://...#test_1 |`` and bare cells.
_SKIP_ROW_RE = re.compile(r"^\|\s*(?P<iri>[^|]+?)\s*\|", re.MULTILINE)
_SKIP_HEADER_TOKENS = ("Manifest IRI", "Test IRI", "---", "_(none yet)_", "Test name")


def load_skip_iris(skip_path: Path | None = None) -> set[str]:
    """Parse the Markdown skip log at *skip_path* and return the set of
    test IRIs we should bypass.

    The file is loosely parsed — any first-column cell that looks like
    an absolute IRI (``http(s)://...`` or ``file://...``) counts as a
    skip directive. Header rows, separator rows, and the placeholder
    ``_(none yet)_`` entry are ignored.
    """
    path = skip_path or _SKIP_DEFAULT
    if not path.is_file():
        return set()
    skips: set[str] = set()
    for match in _SKIP_ROW_RE.finditer(path.read_text(encoding="utf-8")):
        cell = match.group("iri").strip()
        if not cell:
            continue
        if any(token in cell for token in _SKIP_HEADER_TOKENS):
            continue
        if cell.startswith("http://") or cell.startswith("https://") or cell.startswith("file://"):
            skips.add(cell)
    return skips


# ---------------------------------------------------------------------------
# Convenience wrappers used by the test modules
# ---------------------------------------------------------------------------


def collect_cases(*, types: frozenset[str] | None = None) -> list[W3CTestCase]:
    """Materialize every W3C case under :func:`w3c_corpus_root`.

    Returns an empty list when the corpus is absent so the pytest
    harnesses can ``pytest.skip`` cleanly at module load.

    Parameters
    ----------
    types:
        Optional filter on ``W3CTestCase.test_type``. Pass e.g.
        ``frozenset({QUERY_EVAL})`` to get only evaluation tests.
    """
    root = w3c_corpus_root()
    if root is None:
        return []
    skips = load_skip_iris()
    cases: list[W3CTestCase] = []
    for case in iter_manifest_cases(root):
        if case.iri in skips:
            continue
        if types is not None and case.test_type not in types:
            continue
        cases.append(case)
    cases.sort(key=lambda c: c.short_id)
    return cases
