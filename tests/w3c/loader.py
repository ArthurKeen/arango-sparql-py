"""W3C RDF data → ArangoDB loader for the live-execution harness.

The W3C SPARQL 1.1 DAWG corpus ships data as ``.ttl`` / ``.nt`` /
``.nq`` files. The translator targets a flattened document model
(``_uri`` field + literal-attribute fan-out per subject), so this
loader walks each input file and:

* Groups triples by subject IRI (bnode subjects are skipped today —
  the translator can't reach them through ``?s :p ?o`` patterns
  without extra plumbing).
* Flattens literal-valued predicates into doc attributes whose key
  is the local name of the predicate IRI (so ``ex:age`` → ``age``).
* Preserves object-property triples (subject + predicate + IRI object)
  as ArangoDB edge collections in the ``document_edge`` profile or
  ``object_uri`` rows in the ``rpt`` profile.
* Detects the implicit class via ``rdf:type`` triples and ALSO
  routes typed subjects into per-class collections (collection name
  = ``<prefix><LocalName>``). Untyped or doubly-stored subjects
  always live in the default collection so queries that don't carry
  a type pattern still find them.
* Generates a tiny OWL ontology TTL that maps each detected class
  IRI to its physical collection — exactly the shape
  :func:`arango_sparql.translate.resolver.SchemaResolver.from_turtle`
  expects.

The loader is pyoxigraph-based (already a dev dependency) so it
reads the same RDF formats the cross-validation tests use; this
keeps the W3C harness's "what's loaded" story consistent with the
``pyoxigraph`` ground truth.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Match the AQL identifier rules the builder enforces. Collection
# names that don't satisfy this are silently dropped from the
# per-class fan-out (the doc still lives in the default collection,
# so the SELECT query path stays intact).
_AQL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOCAL_NAME_RE = re.compile(r"[#/]([^#/]+)$")

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

# Hard cap on ArangoDB collection name length. The driver actually
# allows 256, but we keep some headroom so the per-class suffix can
# always be appended without truncating the prefix mid-test.
_MAX_COLLECTION_NAME_LEN = 250
StorageProfile = Literal["document_edge", "rpt"]


@dataclass(frozen=True)
class ObjectTriple:
    """A named-node RDF triple retained for a physical edge/RPT row.

    The original loader discarded these triples because its flattened
    Document profile could not represent them. The visitor has since gained
    dedicated/generic edge traversal and RPT readers, so dropping them made
    the live harness under-measure shipped capability.
    """

    subject: str
    predicate: str
    object: str


@dataclass(frozen=True)
class LiteralTriple:
    """A literal RDF triple including the RDF term metadata the flat model loses."""

    subject: str
    predicate: str
    value: Any
    language: str | None
    datatype: str | None


def _local_name(iri: str) -> str:
    """Return the local part of an IRI (after the last ``#`` or ``/``)."""
    match = _LOCAL_NAME_RE.search(iri)
    if match:
        return match.group(1)
    return iri


def _subject_key(iri: str) -> str:
    """Return a deterministic ArangoDB document key for an RDF subject IRI."""

    return hashlib.sha256(iri.encode("utf-8")).hexdigest()[:24]


def _edge_suffix(predicate_iri: str) -> str:
    """Return a collision-resistant, AQL-safe edge collection suffix."""

    local = re.sub(r"[^A-Za-z0-9_]+", "_", _local_name(predicate_iri)).strip("_")
    local = local or "predicate"
    if local[0].isdigit():
        local = f"p_{local}"
    return f"edge_{local}_{hashlib.sha256(predicate_iri.encode('utf-8')).hexdigest()[:8]}"


def _format_for(path: Path) -> Any:
    """Map a file suffix to the matching pyoxigraph ``RdfFormat``.

    Mirrors :func:`tests.helpers.oxi._format_for` to keep the format
    matrix consistent across the harness. Local copy (vs. import)
    keeps this loader self-contained — the W3C harness can ship even
    if the cross-validation helpers move.
    """
    import pyoxigraph as oxi

    suffix = path.suffix.lower()
    if suffix in {".ttl", ".turtle"}:
        return oxi.RdfFormat.TURTLE
    if suffix == ".nt":
        return oxi.RdfFormat.N_TRIPLES
    if suffix == ".nq":
        return oxi.RdfFormat.N_QUADS
    if suffix == ".trig":
        return oxi.RdfFormat.TRIG
    if suffix in {".rdf", ".xml"}:
        return oxi.RdfFormat.RDF_XML
    raise ValueError(f"unsupported RDF file suffix: {suffix!r}")


def _set_attr(doc: dict[str, Any], attr: str, value: Any) -> None:
    """Set ``doc[attr] = value``, promoting to a list on collision.

    SPARQL semantics treat ``ex:p`` with two object literals as two
    separate triples; the translator's flattened doc model can only
    represent a single value per attribute, so a multi-valued
    predicate becomes a Python list. Queries that read it will
    diverge from the SPARQL ground truth — that's exactly the
    divergence the live-execution harness exists to surface, captured
    via xfail in the comparator step.
    """
    if attr.startswith("_"):
        # Reserve the underscore prefix for our metadata (``_uri``).
        # A predicate whose local name collides ('_id', etc.) would
        # otherwise silently shadow the doc identifier and produce
        # confusing comparison failures.
        attr = f"a{attr}"
    if attr in doc:
        existing = doc[attr]
        if isinstance(existing, list):
            existing.append(value)
        else:
            doc[attr] = [existing, value]
    else:
        doc[attr] = value


def _literal_to_python(literal: Any) -> Any:
    """Coerce a pyoxigraph ``Literal`` to the Python primitive AQL
    will round-trip via JSON.

    pyoxigraph 0.3+ exposes a ``Literal.value`` attribute (lexical
    form) and a ``.datatype`` (a ``NamedNode``). We delegate the
    XSD → Python coercion to the same map the SRX parser uses so the
    loader and the comparator stay symmetric — a divergence between
    the two sides has to come from AQL execution, not from a
    type-mapping mismatch in our test harness.
    """
    text = literal.value
    datatype = getattr(literal, "datatype", None)
    if datatype is None:
        return text
    dt_iri = datatype.value
    if dt_iri == "http://www.w3.org/2001/XMLSchema#boolean":
        return text.strip().lower() == "true"
    if dt_iri in (
        "http://www.w3.org/2001/XMLSchema#integer",
        "http://www.w3.org/2001/XMLSchema#int",
        "http://www.w3.org/2001/XMLSchema#long",
        "http://www.w3.org/2001/XMLSchema#short",
        "http://www.w3.org/2001/XMLSchema#byte",
        "http://www.w3.org/2001/XMLSchema#nonNegativeInteger",
        "http://www.w3.org/2001/XMLSchema#nonPositiveInteger",
        "http://www.w3.org/2001/XMLSchema#positiveInteger",
        "http://www.w3.org/2001/XMLSchema#negativeInteger",
        "http://www.w3.org/2001/XMLSchema#unsignedLong",
        "http://www.w3.org/2001/XMLSchema#unsignedInt",
        "http://www.w3.org/2001/XMLSchema#unsignedShort",
        "http://www.w3.org/2001/XMLSchema#unsignedByte",
    ):
        try:
            return int(text)
        except ValueError:
            return text
    if dt_iri in (
        "http://www.w3.org/2001/XMLSchema#decimal",
        "http://www.w3.org/2001/XMLSchema#double",
        "http://www.w3.org/2001/XMLSchema#float",
    ):
        try:
            return float(text)
        except ValueError:
            return text
    return text


def _collect_subjects(
    paths: list[Path],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, set[str]],
    set[str],
    set[str],
    list[ObjectTriple],
    list[LiteralTriple],
    int,
]:
    """Walk every file in *paths* and return:

    * ``docs[subject_iri]`` — flattened literal attributes, keyed by
      the subject's IRI;
    * ``types[subject_iri]`` — the set of class IRIs declared via
      ``rdf:type`` for that subject;
    * ``datatype_predicates`` — every predicate IRI we flattened into
      a literal attribute, so the ontology can declare it as an
      ``owl:DatatypeProperty`` (the resolver's ``attribute_uri_map``
      needs the declarations to bind ``?p`` to IRIs — PRD §6.6);
    * ``object_predicates`` — predicates whose named-node objects are
      retained as ArangoDB edge collections in the ``document_edge``
      profile;
    * ``object_triples`` / ``literal_triples`` — lossless-enough rows used
      by edge and RPT materialisation. Literal metadata is retained even
      where the document profile cannot yet query it;
    * the count of bnode triples we could not represent.
    """
    import pyoxigraph as oxi

    docs: dict[str, dict[str, Any]] = {}
    types: dict[str, set[str]] = {}
    datatype_predicates: set[str] = set()
    object_predicates: set[str] = set()
    object_triples: list[ObjectTriple] = []
    literal_triples: list[LiteralTriple] = []
    skipped_bnode_triples = 0

    for path in paths:
        if not path.is_file():
            logger.debug("loader: skipping missing data file %s", path)
            continue
        store = oxi.Store()
        try:
            store.load(path.read_bytes(), _format_for(path))
        except Exception as exc:  # noqa: BLE001 — surface and continue
            logger.warning("loader: failed to load %s: %s", path, exc)
            continue

        for quad in store:
            subject = quad.subject
            predicate = quad.predicate
            obj = quad.object
            # Bnode subjects don't have a stable IRI we can hand to
            # the translator (which keys on ``_uri``), and the
            # current visitor doesn't expose them anyway. Drop them
            # rather than invent an IRI.
            if not isinstance(subject, oxi.NamedNode):
                continue
            subj_iri = subject.value
            pred_iri = predicate.value

            if pred_iri == RDF_TYPE and isinstance(obj, oxi.NamedNode):
                types.setdefault(subj_iri, set()).add(obj.value)
                continue

            if isinstance(obj, oxi.Literal):
                attr = _local_name(pred_iri)
                value = _literal_to_python(obj)
                doc = docs.setdefault(subj_iri, {"_key": _subject_key(subj_iri), "_uri": subj_iri})
                _set_attr(doc, attr, value)
                datatype_predicates.add(pred_iri)
                language = getattr(obj, "language", None)
                datatype = getattr(obj, "datatype", None)
                literal_triples.append(
                    LiteralTriple(
                        subject=subj_iri,
                        predicate=pred_iri,
                        value=value,
                        language=getattr(language, "value", language),
                        datatype=getattr(datatype, "value", datatype),
                    )
                )
                continue

            if isinstance(obj, oxi.NamedNode):
                # Preserve IRI→IRI triples as genuine RDF edges. Never
                # flatten them to strings: that would silently change both
                # join and multiplicity semantics.
                docs.setdefault(subj_iri, {"_key": _subject_key(subj_iri), "_uri": subj_iri})
                docs.setdefault(
                    obj.value,
                    {"_key": _subject_key(obj.value), "_uri": obj.value},
                )
                object_predicates.add(pred_iri)
                object_triples.append(ObjectTriple(subj_iri, pred_iri, obj.value))
                continue

            if isinstance(obj, oxi.BlankNode):
                # A blank-node object is still a valid RDF term and must
                # participate in expression/aggregate error semantics. The
                # document profile cannot traverse it as a vertex yet, but a
                # stable lexical carrier preserves its presence and identity
                # within this loaded dataset.
                attr = _local_name(pred_iri)
                value = f"_:{obj.value}"
                doc = docs.setdefault(
                    subj_iri,
                    {"_key": _subject_key(subj_iri), "_uri": subj_iri},
                )
                _set_attr(doc, attr, value)
                datatype_predicates.add(pred_iri)
                continue

            # Unknown RDF term kind.
            skipped_bnode_triples += 1

    # Make sure every typed subject has a doc entry, even if all its
    # outgoing predicates were object properties; the per-class
    # collection still wants ``_uri`` so type-pattern queries can
    # see the membership.
    for subj_iri in types:
        docs.setdefault(subj_iri, {"_key": _subject_key(subj_iri), "_uri": subj_iri})

    return (
        docs,
        types,
        datatype_predicates,
        object_predicates,
        object_triples,
        literal_triples,
        skipped_bnode_triples,
    )


def _safe_collection_name(prefix: str, suffix: str) -> str | None:
    """Compose a collection name and reject anything ArangoDB / AQL
    would refuse.

    The AQL builder validates collection names against a strict
    identifier regex (see :data:`_AQL_IDENT_RE`); a name that fails
    the check would crash :func:`arango_sparql.translate.builder.AqlQueryBuilder.bind_collection`
    at run time. Returning ``None`` lets the caller fall back to the
    default collection cleanly.
    """
    name = f"{prefix}{suffix}"
    if not _AQL_IDENT_RE.match(name):
        return None
    if len(name) > _MAX_COLLECTION_NAME_LEN:
        return None
    return name


def _drop_and_create(db: Any, name: str, *, edge: bool = False) -> Any:
    """Idempotent ``drop && create`` for a collection.

    Tests share a single ArangoDB instance, so a previous run that
    wedged mid-teardown can leave a stale collection behind; the
    drop-then-create dance gives every test a clean slate without
    paying for a full database reset.
    """
    if db.has_collection(name):
        db.delete_collection(name)
    return db.create_collection(name, edge=edge) if edge else db.create_collection(name)


def load_w3c_data_to_arango(
    db: Any,
    data_paths: list[Path],
    collection_prefix: str,
    *,
    storage_profile: StorageProfile = "document_edge",
) -> tuple[str, dict[str, str]]:
    """Load *data_paths* into ArangoDB collections and return
    ``(ontology_ttl, collection_map)``.

    Parameters
    ----------
    db:
        A ``python-arango`` ``StandardDatabase`` already authenticated
        against the target ArangoDB instance.
    data_paths:
        One or more RDF files (``.ttl`` / ``.nt`` / …). Missing files
        are logged and skipped — the harness treats a missing data
        file as a partial-load condition rather than a crash so a
        corpus surprise produces a clean xfail.
    collection_prefix:
        Per-test prefix that namespaces every created collection (so
        parallel test runs don't collide). Must be a valid AQL
        identifier prefix, e.g. ``"w3c_functions_contains01_"``.
    storage_profile:
        ``"document_edge"`` is the existing flattened-document profile,
        now augmented with ArangoDB edge collections for IRI→IRI triples.
        ``"rpt"`` stores every named-subject triple in one RPT collection
        and maps every detected class to it. The profiles are deliberately
        explicit: they have different fidelity/performance trade-offs and
        their live coverage must not be conflated.

    Returns
    -------
    ontology_ttl:
        OWL TTL declaring every detected class with its physical
        collection. Pass straight to
        :meth:`SchemaResolver.from_turtle`.
    collection_map:
        ``class_iri → physical_collection_name``. Empty when the
        dataset has no ``rdf:type`` triples we can map.
    """
    if storage_profile not in ("document_edge", "rpt"):
        raise ValueError(f"unsupported W3C storage profile: {storage_profile!r}")
    if not _AQL_IDENT_RE.match(collection_prefix.rstrip("_") or "x"):
        # The prefix joins with the suffix to form an AQL identifier,
        # so the same regex applies. Reject early with a clear error
        # rather than failing later in the AQL builder.
        raise ValueError(f"collection_prefix {collection_prefix!r} is not a valid AQL identifier prefix")

    (
        docs_by_subject,
        types_by_subject,
        datatype_predicates,
        object_predicates,
        object_triples,
        literal_triples,
        skipped_bnodes,
    ) = _collect_subjects(data_paths)

    default_coll = _safe_collection_name(collection_prefix, "Document")
    if default_coll is None:
        raise ValueError(f"composed default collection name from prefix {collection_prefix!r} is invalid")

    collection_map: dict[str, str] = {}
    # Every distinct class IRI we saw on at least one ``rdf:type``
    # triple becomes a candidate per-class collection.
    distinct_classes: set[str] = set()
    for class_iris in types_by_subject.values():
        distinct_classes.update(class_iris)

    for class_iri in sorted(distinct_classes):
        suffix = _local_name(class_iri)
        coll_name = _safe_collection_name(collection_prefix, suffix)
        if coll_name is None:
            logger.debug(
                "loader: class IRI %r has no AQL-safe collection name (suffix=%r); skipping",
                class_iri,
                suffix,
            )
            continue
        collection_map[class_iri] = coll_name

    if storage_profile == "rpt":
        triples_coll = _rpt_collection_name(collection_prefix)
        _materialize_rpt(
            db,
            default_coll,
            docs_by_subject,
            types_by_subject,
            object_triples,
            literal_triples,
            triples_coll,
        )
        ontology_ttl = _build_ontology_ttl(
            collection_map,
            datatype_predicates,
            mapping_style="RPT",
            triples_collection=triples_coll,
        )
    else:
        _materialize_document_edge(
            db,
            default_coll,
            docs_by_subject,
            collection_map,
            types_by_subject,
            object_predicates,
            object_triples,
            collection_prefix,
        )
        edge_collections = {
            predicate: _edge_collection_name(collection_prefix, predicate) for predicate in object_predicates
        }
        ontology_ttl = _build_ontology_ttl(
            collection_map,
            datatype_predicates,
            edge_collections=edge_collections,
        )

    if skipped_bnodes:
        logger.info(
            "loader: skipped %d triples with blank-node terms for prefix %s",
            skipped_bnodes,
            collection_prefix,
        )
    return ontology_ttl, collection_map


def _edge_collection_name(prefix: str, predicate_iri: str) -> str:
    name = _safe_collection_name(prefix, _edge_suffix(predicate_iri))
    if name is None:  # pragma: no cover - fixed-length hashed suffix is safe
        raise ValueError(f"cannot name edge collection for {predicate_iri!r}")
    return name


def _rpt_collection_name(prefix: str) -> str:
    name = _safe_collection_name(prefix, "Triples")
    if name is None:  # pragma: no cover - fixed suffix is always safe
        raise ValueError(f"cannot name RPT collection with prefix {prefix!r}")
    return name


def _materialize_document_edge(
    db: Any,
    default_coll: str,
    docs_by_subject: dict[str, dict[str, Any]],
    collection_map: dict[str, str],
    types_by_subject: dict[str, set[str]],
    object_predicates: set[str],
    object_triples: list[ObjectTriple],
    collection_prefix: str,
) -> None:
    """Materialize the flattened Document profile plus real edge collections.

    Every subject is stored in the default collection and in every declared
    class collection. Edges are emitted once per possible source replica so a
    traversal works whether the visitor starts from a type-bound class alias
    or an untyped default-document alias. Targets deliberately point to the
    default replica: subsequent class constraints join by ``_uri`` and
    continue to preserve SPARQL bindings without multiplying target edges.
    """

    default_handle = _drop_and_create(db, default_coll)
    if docs_by_subject:
        default_handle.insert_many(list(docs_by_subject.values()))

    for class_iri, coll_name in collection_map.items():
        per_class = _drop_and_create(db, coll_name)
        members = [
            dict(docs_by_subject[subject])
            for subject, classes in types_by_subject.items()
            if class_iri in classes and subject in docs_by_subject
        ]
        if members:
            per_class.insert_many(members)

    for predicate in object_predicates:
        edge_coll = _edge_collection_name(collection_prefix, predicate)
        edge_handle = _drop_and_create(db, edge_coll, edge=True)
        rows: list[dict[str, str]] = []
        for triple in object_triples:
            if triple.predicate != predicate:
                continue
            target = docs_by_subject[triple.object]
            target_id = f"{default_coll}/{target['_key']}"
            source_collections = [default_coll]
            source_collections.extend(
                collection_map[class_iri]
                for class_iri in types_by_subject.get(triple.subject, set())
                if class_iri in collection_map
            )
            source = docs_by_subject[triple.subject]
            rows.extend(
                {
                    "_from": f"{source_coll}/{source['_key']}",
                    "_to": target_id,
                }
                for source_coll in source_collections
            )
        if rows:
            edge_handle.insert_many(rows)


def _materialize_rpt(
    db: Any,
    default_coll: str,
    docs_by_subject: dict[str, dict[str, Any]],
    types_by_subject: dict[str, set[str]],
    object_triples: list[ObjectTriple],
    literal_triples: list[LiteralTriple],
    triples_coll: str,
) -> None:
    """Materialize a lossless-named-node RPT profile for W3C live tests."""

    # Keep a default collection for untyped queries that still route through
    # the visitor's Document fallback. The RPT profile is selected only for
    # class-bound paths by the resolver's class mappings.
    default_handle = _drop_and_create(db, default_coll)
    if docs_by_subject:
        default_handle.insert_many(list(docs_by_subject.values()))

    triple_handle = _drop_and_create(db, triples_coll)
    rows: list[dict[str, Any]] = []
    for subject, class_iris in types_by_subject.items():
        for class_iri in class_iris:
            rows.append(
                {
                    "subject_uri": subject,
                    "predicate": RDF_TYPE,
                    "object_uri": class_iri,
                    "object_value": None,
                }
            )
    rows.extend(
        {
            "subject_uri": triple.subject,
            "predicate": triple.predicate,
            "object_uri": triple.object,
            "object_value": None,
        }
        for triple in object_triples
    )
    rows.extend(
        {
            "subject_uri": triple.subject,
            "predicate": triple.predicate,
            "object_uri": None,
            "object_value": triple.value,
            # Preserve term metadata for the RPT profile even though current
            # visitor projections only consume the four legacy columns.
            "object_language": triple.language,
            "object_datatype": triple.datatype,
        }
        for triple in literal_triples
    )
    if rows:
        triple_handle.insert_many(rows)


def _build_ontology_ttl(
    collection_map: dict[str, str],
    datatype_predicates: set[str] | None = None,
    *,
    edge_collections: dict[str, str] | None = None,
    mapping_style: str | None = None,
    triples_collection: str | None = None,
) -> str:
    """Render the minimal OWL TTL the schema resolver needs.

    The output uses the canonical ``arango.solutions/phys#`` namespace
    that :class:`SchemaResolver` recognizes as the physical-mapping
    annotation. Every entry mirrors the shape
    ``arango-schema-mapper`` would emit, so the same ontology format
    that drives production also drives the test harness.

    Every flattened predicate is an ``owl:DatatypeProperty`` and every
    preserved IRI→IRI predicate is an ``owl:ObjectProperty`` with the
    edge collection required by the traversal visitor. RPT class mappings
    instead share one ``phys:triplesCollection``. This mirrors the
    production resolver contract rather than treating the W3C loader as a
    special AQL path.
    """
    lines = [
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix phys: <https://arango.solutions/phys#> .",
        "",
    ]
    for class_iri in sorted(collection_map):
        coll = collection_map[class_iri]
        # Ontology TTL escaping: collection names are AQL identifiers
        # (validated by ``_safe_collection_name``) so they're safe
        # inside double quotes; class IRIs come straight from the
        # data file and must be wrapped in angle brackets.
        if mapping_style == "RPT":
            if triples_collection is None:
                raise ValueError("RPT ontology needs a triples collection")
            lines.append(
                f'<{class_iri}> a owl:Class ; phys:mappingStyle "RPT" ; '
                f'phys:triplesCollection "{triples_collection}" .'
            )
        else:
            lines.append(f'<{class_iri}> a owl:Class ; phys:collectionName "{coll}" .')
    for pred_iri in sorted(datatype_predicates or ()):
        lines.append(f"<{pred_iri}> a owl:DatatypeProperty .")
    for pred_iri, edge_collection in sorted((edge_collections or {}).items()):
        lines.append(f'<{pred_iri}> a owl:ObjectProperty ; phys:edgeCollectionName "{edge_collection}" .')
    lines.append("")
    return "\n".join(lines)


def teardown_collections(db: Any, collection_prefix: str) -> None:
    """Drop every collection whose name starts with *collection_prefix*.

    Looser than tracking the names returned by :func:`load_w3c_data_to_arango`
    on purpose: a crashed test that didn't reach its teardown still
    leaves stale collections behind, and the next ``RUN_INTEGRATION``
    pass should reclaim them automatically.
    """
    try:
        existing = list(db.collections())
    except Exception as exc:  # noqa: BLE001 — best-effort teardown
        logger.warning("teardown: failed to enumerate collections: %s", exc)
        return
    for coll in existing:
        name = coll.get("name") if isinstance(coll, dict) else getattr(coll, "name", None)
        if not name or not name.startswith(collection_prefix):
            continue
        try:
            db.delete_collection(name)
        except Exception as exc:  # noqa: BLE001 — best-effort teardown
            logger.warning("teardown: failed to drop %s: %s", name, exc)


def sanitize_for_collection(name: str) -> str:
    """Normalize a free-form test ID (e.g. ``functions/contains01``)
    into something usable as part of an AQL collection name.

    Replaces every non-alphanumeric byte with ``_`` and prepends an
    ``x`` if the result would otherwise start with a digit. Length
    is bounded by :data:`_MAX_COLLECTION_NAME_LEN` minus headroom
    for the per-class suffix.
    """
    if not name:
        return "x"
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", name)
    cleaned = cleaned.strip("_") or "x"
    if cleaned[0].isdigit():
        cleaned = f"x{cleaned}"
    # Reserve ~50 chars for the per-class suffix; collection name
    # caps at 256 in ArangoDB.
    return cleaned[:200]
