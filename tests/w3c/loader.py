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
* Skips object-property triples (subject + predicate + IRI object) —
  the translator doesn't yet emit edge traversals, so loading those
  would only inflate collection size for no gain. Each skip is
  logged so the operator can spot a query that depends on edges.
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

import logging
import re
from pathlib import Path
from typing import Any

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


def _local_name(iri: str) -> str:
    """Return the local part of an IRI (after the last ``#`` or ``/``)."""
    match = _LOCAL_NAME_RE.search(iri)
    if match:
        return match.group(1)
    return iri


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
    int,
]:
    """Walk every file in *paths* and return:

    * ``docs[subject_iri]`` — flattened literal attributes, keyed by
      the subject's IRI;
    * ``types[subject_iri]`` — the set of class IRIs declared via
      ``rdf:type`` for that subject;
    * the count of object-property triples we skipped (logged for
      operator visibility, returned so callers can surface it as a
      warning).
    """
    import pyoxigraph as oxi

    docs: dict[str, dict[str, Any]] = {}
    types: dict[str, set[str]] = {}
    skipped_obj_triples = 0

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
                doc = docs.setdefault(subj_iri, {"_uri": subj_iri})
                _set_attr(doc, attr, value)
                continue

            if isinstance(obj, oxi.NamedNode):
                # Object property — needs an edge collection in AQL,
                # which the translator doesn't emit yet. Surface the
                # gap rather than load these triples into a flattened
                # attribute slot (a Skolemized "store the IRI as a
                # string" would silently change SPARQL semantics).
                skipped_obj_triples += 1
                continue

            # Bnode object: same story as bnode subject above.
            skipped_obj_triples += 1

    # Make sure every typed subject has a doc entry, even if all its
    # outgoing predicates were object properties; the per-class
    # collection still wants ``_uri`` so type-pattern queries can
    # see the membership.
    for subj_iri in types:
        docs.setdefault(subj_iri, {"_uri": subj_iri})

    return docs, types, skipped_obj_triples


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


def _drop_and_create(db: Any, name: str) -> Any:
    """Idempotent ``drop && create`` for a collection.

    Tests share a single ArangoDB instance, so a previous run that
    wedged mid-teardown can leave a stale collection behind; the
    drop-then-create dance gives every test a clean slate without
    paying for a full database reset.
    """
    if db.has_collection(name):
        db.delete_collection(name)
    return db.create_collection(name)


def load_w3c_data_to_arango(
    db: Any,
    data_paths: list[Path],
    collection_prefix: str,
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
    if not _AQL_IDENT_RE.match(collection_prefix.rstrip("_") or "x"):
        # The prefix joins with the suffix to form an AQL identifier,
        # so the same regex applies. Reject early with a clear error
        # rather than failing later in the AQL builder.
        raise ValueError(f"collection_prefix {collection_prefix!r} is not a valid AQL identifier prefix")

    docs_by_subject, types_by_subject, skipped = _collect_subjects(data_paths)

    default_coll = _safe_collection_name(collection_prefix, "Document")
    if default_coll is None:
        raise ValueError(f"composed default collection name from prefix {collection_prefix!r} is invalid")

    coll_handle = _drop_and_create(db, default_coll)
    docs_to_insert = list(docs_by_subject.values())
    if docs_to_insert:
        coll_handle.insert_many(docs_to_insert)

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
        per_class = _drop_and_create(db, coll_name)
        members = [
            dict(docs_by_subject[s]) for s, classes in types_by_subject.items() if class_iri in classes
        ]
        if members:
            per_class.insert_many(members)
        collection_map[class_iri] = coll_name

    ontology_ttl = _build_ontology_ttl(collection_map)
    if skipped:
        logger.info(
            "loader: skipped %d object-property/bnode triples for prefix %s "
            "(translator does not emit edge traversals yet)",
            skipped,
            collection_prefix,
        )
    return ontology_ttl, collection_map


def _build_ontology_ttl(collection_map: dict[str, str]) -> str:
    """Render the minimal OWL TTL the schema resolver needs.

    The output uses the canonical ``arango.solutions/phys#`` namespace
    that :class:`SchemaResolver` recognizes as the physical-mapping
    annotation. Every entry mirrors the shape
    ``arango-schema-mapper`` would emit, so the same ontology format
    that drives production also drives the test harness.
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
        lines.append(f'<{class_iri}> a owl:Class ; phys:collectionName "{coll}" .')
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
