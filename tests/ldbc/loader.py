"""Load the normalised LDBC dataset into ArangoDB as RPT, LPG or PG.

Two halves:

* :func:`build_graph` turns triples into an :class:`LdbcGraph` — nodes,
  plain edges, and reified relationships folded into edges (plan D8,
  §5.4). It is pure, so the unit tests exercise it without a database.
* :func:`load_layout` writes one layout to a database, verifies the counts
  against the model, and records what it loaded in ``ldbc_meta`` so a
  second call with the same data is a no-op (idempotent).

The RPT layout is written straight from the triples: it *is* the triple
set, so it needs no model.
"""

from __future__ import annotations

import hashlib
import logging
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pyoxigraph as ox

from tests.helpers.oxi import literal_to_python
from tests.ldbc import vocab

logger = logging.getLogger(__name__)

Layout = Literal["rpt", "lpg", "pg"]
LAYOUTS: tuple[Layout, ...] = ("rpt", "lpg", "pg")

#: Bump when what the loader writes changes, so a loaded database is rebuilt.
LOADER_VERSION = 1

#: Documents per ``import_bulk`` call.
BATCH_SIZE = 20_000

#: Not ``_triples`` (the legacy Foxx name): ArangoDB reserves names that
#: start with ``_`` for system collections. The RPT mapping fixture points
#: ``phys:triplesCollection`` here.
RPT_COLLECTION = "triples"
LPG_NODES = "nodes"
LPG_EDGES = "edges"
META_COLLECTION = "ldbc_meta"

#: RDF predicates that are only facts in RDF. ``directKnows`` is a
#: traversal of ``knows`` in PG and LPG (plan D10), not stored data.
RDF_ONLY_PREDICATES = frozenset({vocab.DIRECT_KNOWS})


class LoadError(RuntimeError):
    """The data does not fit the LDBC model, or a load did not verify."""


@dataclass
class Node:
    iri: str
    classes: set[str] = field(default_factory=set)
    attributes: dict[str, list[Any]] = field(default_factory=lambda: defaultdict(list))

    @property
    def pg_class(self) -> vocab.NodeClass:
        """The class whose PG collection holds this node (``PG_CLASS_PRIORITY``)."""
        for name in vocab.PG_CLASS_PRIORITY:
            if name in self.classes:
                return vocab.CLASS_BY_NAME[name]
        raise LoadError(f"node {self.iri} has no concrete LDBC class")

    @property
    def ids(self) -> list[int]:
        return sorted(v for v in self.attributes.get(vocab.SNVOC + "id", []) if isinstance(v, int))

    @property
    def key(self) -> str:
        """``_key``: the LDBC id, else a digest of the IRI (ids are unique per class)."""
        ids = self.ids
        if ids:
            return str(ids[0])
        return hashlib.sha1(self.iri.encode("utf-8")).hexdigest()  # noqa: S324 — a key, not security

    def labels(self) -> list[str]:
        out = set(self.classes)
        for name in self.classes:
            supertype = vocab.CLASS_BY_NAME[name].supertype
            if supertype:
                out.add(supertype)
        return sorted(out)


@dataclass
class Edge:
    name: str  # relationship name, e.g. ``KNOWS``
    pg_collection: str
    source: str
    target: str
    attributes: dict[str, Any] = field(default_factory=dict)
    uri: str | None = None  # the reified node's IRI, when there was one


@dataclass
class LdbcGraph:
    nodes: dict[str, Node]
    edges: list[Edge]
    rdf_only_triples: int = 0

    def class_counts(self) -> Counter[str]:
        return Counter(name for node in self.nodes.values() for name in node.classes)

    def pg_collection_counts(self) -> Counter[str]:
        counts: Counter[str] = Counter(node.pg_class.pg_collection for node in self.nodes.values())
        counts.update(edge.pg_collection for edge in self.edges)
        return counts

    def edge_counts(self) -> Counter[str]:
        return Counter(edge.name for edge in self.edges)


def _attribute(predicate: str) -> str:
    return vocab.local_name(predicate)


def build_graph(triples: Iterable[ox.Triple]) -> LdbcGraph:
    """Fold the normalised triples into nodes and edges.

    Raises :class:`LoadError` on any predicate or shape the LDBC model does
    not cover, so the PG and LPG layouts can never drop data silently.
    """
    types: dict[str, set[str]] = defaultdict(set)
    literals: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    links: list[tuple[str, str, str]] = []
    rdf_only = 0
    intern = sys.intern

    for triple in triples:
        subject = intern(triple.subject.value)
        predicate = intern(triple.predicate.value)
        obj = triple.object
        if predicate in RDF_ONLY_PREDICATES:
            rdf_only += 1
        elif isinstance(obj, ox.Literal):
            literals[subject][predicate].append(literal_to_python(obj))
        elif predicate == vocab.RDF_TYPE:
            types[subject].add(intern(obj.value))
        elif predicate in vocab.DATATYPE_PROPERTIES:
            # ``snvoc:url`` is IRI-valued but describes the node; store the IRI.
            literals[subject][predicate].append(obj.value)
        else:
            links.append((subject, predicate, intern(obj.value)))

    # Reified relationship nodes: the objects of an entry predicate.
    reified_of: dict[str, tuple[vocab.ReifiedRelationship, str]] = {}
    for subject, predicate, obj in links:
        relationship = vocab.REIFIED_BY_ENTRY.get(predicate)
        if relationship is not None:
            reified_of[obj] = (relationship, subject)

    nodes: dict[str, Node] = {}
    tag_class_nodes = {iri for iri, ts in types.items() if vocab.CLASS_BY_NAME["TagClass"].iri in ts}
    for iri, type_iris in types.items():
        node = Node(iri)
        for type_iri in type_iris:
            if type_iri in vocab.CLASS_BY_IRI:
                node.classes.add(vocab.CLASS_BY_IRI[type_iri].name)
            elif type_iri in tag_class_nodes:
                node.classes.add("Tag")
            elif type_iri not in vocab.SUPERTYPE_IRIS:
                raise LoadError(f"node {iri} has an unknown type {type_iri}")
        if not node.classes:
            raise LoadError(f"node {iri} has no concrete LDBC class (types: {sorted(type_iris)})")
        nodes[iri] = node

    for iri, by_predicate in literals.items():
        if iri in reified_of:
            continue
        node = nodes.get(iri)
        if node is None:
            raise LoadError(f"literal on {iri}, which is neither a typed node nor a relationship node")
        for predicate, values in by_predicate.items():
            if predicate not in vocab.DATATYPE_PROPERTIES:
                raise LoadError(f"unknown datatype property {predicate} on {iri}")
            node.attributes[predicate].extend(values)

    edges: list[Edge] = []
    reified_targets: dict[str, list[str]] = defaultdict(list)
    for subject, predicate, obj in links:
        if predicate in vocab.REIFIED_BY_ENTRY:
            continue
        if subject in reified_of:
            relationship, _source = reified_of[subject]
            if predicate not in relationship.exits:
                raise LoadError(f"{relationship.name} node {subject} has unexpected link {predicate}")
            reified_targets[subject].append(obj)
            continue
        rel = vocab.RELATIONSHIP_BY_IRI.get(predicate)
        if rel is None:
            raise LoadError(f"unknown object property {predicate} ({subject} → {obj})")
        edges.append(Edge(rel.name, rel.pg_collection, subject, obj))

    # Tags point at their tag class through rdf:type; that is the HAS_TYPE edge.
    has_type = vocab.RELATIONSHIP_BY_IRI[vocab.RDF_TYPE]
    for iri, type_iris in types.items():
        for type_iri in type_iris:
            if type_iri in tag_class_nodes:
                edges.append(Edge(has_type.name, has_type.pg_collection, iri, type_iri))

    for node_iri, (relationship, source) in reified_of.items():
        targets = reified_targets.get(node_iri, [])
        if len(targets) != 1:
            raise LoadError(f"{relationship.name} node {node_iri} has {len(targets)} targets, expected 1")
        attributes: dict[str, Any] = {}
        for predicate, values in literals.get(node_iri, {}).items():
            if predicate not in relationship.attributes or len(values) != 1:
                raise LoadError(
                    f"{relationship.name} node {node_iri}: unexpected attribute {predicate}={values}"
                )
            attributes[_attribute(predicate)] = values[0]
        edges.append(
            Edge(relationship.name, relationship.pg_collection, source, targets[0], attributes, uri=node_iri)
        )

    for edge in edges:
        for end in (edge.source, edge.target):
            if end not in nodes:
                raise LoadError(f"{edge.name} edge endpoint {end} is not a node")
    return LdbcGraph(nodes=nodes, edges=edges, rdf_only_triples=rdf_only)


def read_triples(nt_path: Path) -> Iterator[ox.Triple]:
    return ox.parse(path=str(nt_path), format=ox.RdfFormat.N_TRIPLES)


# ---------------------------------------------------------------------------
# Documents per layout
# ---------------------------------------------------------------------------


def _node_attributes(node: Node) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for predicate, values in node.attributes.items():
        if predicate in vocab.ARRAY_PROPERTIES:
            out[_attribute(predicate)] = sorted(values)
        elif len(values) == 1:
            out[_attribute(predicate)] = values[0]
        else:
            out[_attribute(predicate)] = sorted(values)
    return out


def pg_vertex(node: Node) -> dict[str, Any]:
    return {"_key": node.key, "_uri": node.iri, **_node_attributes(node)}


def lpg_key(node: Node) -> str:
    return f"{node.pg_class.name}_{node.key}"


def lpg_vertex(node: Node) -> dict[str, Any]:
    return {
        "_key": lpg_key(node),
        "_uri": node.iri,
        "type": node.pg_class.name,
        "labels": node.labels(),
        **_node_attributes(node),
    }


def _edge_doc(edge: Edge, from_handle: str, to_handle: str, *, with_type: bool) -> dict[str, Any]:
    doc: dict[str, Any] = {"_from": from_handle, "_to": to_handle, **edge.attributes}
    if edge.uri is not None:
        doc["_uri"] = edge.uri
    if with_type:
        doc["type"] = edge.name
    return doc


def pg_documents(graph: LdbcGraph) -> Iterator[tuple[str, bool, dict[str, Any]]]:
    """``(collection, is_edge, document)`` for the PG layout."""
    for node in graph.nodes.values():
        yield node.pg_class.pg_collection, False, pg_vertex(node)
    for edge in graph.edges:
        source, target = graph.nodes[edge.source], graph.nodes[edge.target]
        yield (
            edge.pg_collection,
            True,
            _edge_doc(
                edge,
                f"{source.pg_class.pg_collection}/{source.key}",
                f"{target.pg_class.pg_collection}/{target.key}",
                with_type=False,
            ),
        )


def lpg_documents(graph: LdbcGraph) -> Iterator[tuple[str, bool, dict[str, Any]]]:
    """``(collection, is_edge, document)`` for the LPG layout."""
    for node in graph.nodes.values():
        yield LPG_NODES, False, lpg_vertex(node)
    for edge in graph.edges:
        source, target = graph.nodes[edge.source], graph.nodes[edge.target]
        yield (
            LPG_EDGES,
            True,
            _edge_doc(
                edge,
                f"{LPG_NODES}/{lpg_key(source)}",
                f"{LPG_NODES}/{lpg_key(target)}",
                with_type=True,
            ),
        )


def rpt_row(triple: ox.Triple) -> dict[str, Any]:
    """One RPT row in the PRD §6.1 shape."""
    obj = triple.object
    if isinstance(obj, ox.Literal):
        return {
            "subject_uri": triple.subject.value,
            "predicate": triple.predicate.value,
            "object_uri": None,
            "object_value": literal_to_python(obj),
        }
    return {
        "subject_uri": triple.subject.value,
        "predicate": triple.predicate.value,
        "object_uri": obj.value,
        "object_value": None,
    }


def rpt_documents(triples: Iterable[ox.Triple]) -> Iterator[tuple[str, bool, dict[str, Any]]]:
    for triple in triples:
        yield RPT_COLLECTION, False, rpt_row(triple)


# ---------------------------------------------------------------------------
# Indexes per layout
# ---------------------------------------------------------------------------

#: ``(collection, fields)`` persistent indexes, mirroring the reference DDL
#: plus ``_uri`` (constant subjects match on it, PRD §6.1).
_PG_VERTEX_INDEXES: dict[str, list[list[str]]] = {
    "person": [["_uri"], ["id"], ["firstName"]],
    "forum": [["_uri"], ["id"]],
    "post": [["_uri"], ["id"], ["creationDate"]],
    "comment": [["_uri"], ["id"], ["creationDate"]],
    "tag": [["_uri"], ["id"], ["name"]],
    "tagclass": [["_uri"], ["id"], ["label"], ["name"]],
    "city": [["_uri"], ["id"], ["name"]],
    "country": [["_uri"], ["id"], ["name"]],
    "continent": [["_uri"], ["id"], ["name"]],
    "university": [["_uri"], ["id"], ["name"]],
    "company": [["_uri"], ["id"], ["name"]],
}
_LPG_INDEXES: dict[str, list[list[str]]] = {
    LPG_NODES: [["_uri"], ["labels[*]"], ["type"], ["id"], ["firstName"], ["name"], ["creationDate"]],
    LPG_EDGES: [["type"], ["_from", "type"], ["_to", "type"]],
}
_RPT_INDEXES: dict[str, list[list[str]]] = {
    RPT_COLLECTION: [
        ["subject_uri", "predicate"],
        ["predicate", "object_uri"],
        ["predicate", "object_value"],
    ],
}


def indexes_for(layout: Layout) -> dict[str, list[list[str]]]:
    if layout == "rpt":
        return _RPT_INDEXES
    if layout == "lpg":
        return _LPG_INDEXES
    return _PG_VERTEX_INDEXES


# ---------------------------------------------------------------------------
# Writing and verifying
# ---------------------------------------------------------------------------


@dataclass
class LoadReport:
    layout: Layout
    database: str
    data_sha256: str
    skipped: bool
    seconds: float
    counts: dict[str, int]


def expected_counts(layout: Layout, graph: LdbcGraph | None, triple_count: int) -> dict[str, int]:
    """Per-collection document counts the model says *layout* must hold."""
    if layout == "rpt":
        return {RPT_COLLECTION: triple_count}
    assert graph is not None
    if layout == "lpg":
        return {LPG_NODES: len(graph.nodes), LPG_EDGES: len(graph.edges)}
    return dict(graph.pg_collection_counts())


def _recreate(db: Any, name: str, *, edge: bool) -> Any:
    if db.has_collection(name):
        db.delete_collection(name)
    return db.create_collection(name, edge=edge)


def _write(db: Any, documents: Iterable[tuple[str, bool, dict[str, Any]]]) -> dict[str, int]:
    handles: dict[str, Any] = {}
    batches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    written: Counter[str] = Counter()

    def flush(name: str) -> None:
        batch = batches[name]
        if not batch:
            return
        result = handles[name].import_bulk(batch, halt_on_error=True, details=True)
        if result.get("errors"):
            raise LoadError(f"import into {name} reported errors: {result.get('details')}")
        written[name] += result.get("created", 0)
        batch.clear()

    for name, is_edge, doc in documents:
        if name not in handles:
            handles[name] = _recreate(db, name, edge=is_edge)
        batches[name].append(doc)
        if len(batches[name]) >= BATCH_SIZE:
            flush(name)
    for name in list(batches):
        flush(name)
    return dict(written)


def _actual_counts(db: Any, names: Iterable[str]) -> dict[str, int]:
    return {name: db.collection(name).count() for name in names}


def _meta(db: Any) -> dict[str, Any] | None:
    if not db.has_collection(META_COLLECTION):
        return None
    doc = db.collection(META_COLLECTION).get("load")
    return doc if isinstance(doc, dict) else None


def load_layout(
    db: Any,
    layout: Layout,
    nt_path: Path,
    *,
    data_sha256: str,
    triple_count: int,
    force: bool = False,
) -> LoadReport:
    """Load *layout* into *db* from the normalised N-Triples at *nt_path*.

    Skips the work when ``ldbc_meta`` records the same data digest, layout
    and loader version and the collection counts still match. Always ends
    by checking every collection's count against the model.
    """
    started = time.monotonic()
    meta = _meta(db)
    if (
        not force
        and meta is not None
        and meta.get("data_sha256") == data_sha256
        and meta.get("layout") == layout
        and meta.get("loader_version") == LOADER_VERSION
    ):
        expected = meta.get("counts", {})
        if _actual_counts(db, expected) == expected:
            return LoadReport(layout, db.name, data_sha256, True, time.monotonic() - started, expected)

    if db.has_collection(META_COLLECTION):
        db.delete_collection(META_COLLECTION)
    graph = None if layout == "rpt" else build_graph(read_triples(nt_path))
    if layout == "rpt":
        documents = rpt_documents(read_triples(nt_path))
    elif layout == "lpg":
        assert graph is not None
        documents = lpg_documents(graph)
    else:
        assert graph is not None
        documents = pg_documents(graph)
    written = _write(db, documents)

    expected = expected_counts(layout, graph, triple_count)
    actual = _actual_counts(db, expected)
    if actual != expected or written != expected:
        raise LoadError(f"{layout} load did not verify: expected {expected}, wrote {written}, found {actual}")

    for name, field_sets in indexes_for(layout).items():
        for fields in field_sets:
            db.collection(name).add_persistent_index(fields=fields, in_background=False)

    db.create_collection(META_COLLECTION).insert(
        {
            "_key": "load",
            "layout": layout,
            "data_sha256": data_sha256,
            "loader_version": LOADER_VERSION,
            "counts": expected,
        }
    )
    seconds = time.monotonic() - started
    logger.info("loaded %s into %s in %.1fs: %s", layout, db.name, seconds, expected)
    return LoadReport(layout, db.name, data_sha256, False, seconds, expected)
