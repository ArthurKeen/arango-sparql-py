"""Schema HTTP surface — PRD §6.4 (seven routes).

Mirror of ``arango_cypher.service.routes.schema`` with two
adaptations called out in :mod:`arango_sparql.service.models`:

* RPT collections get their own breakout in the conceptual summary
  because the SPARQL planner's RPT translator routes off ``style="RPT"``
  (the sister project's planner is Cypher-only and does not need
  the breakout).
* The cache is keyed by ``db.name`` rather than the sister's
  ``(db.name, key="mapping")`` tuple — Slice 5's
  :class:`~arango_sparql.schema.cache.SchemaCache` already does the
  per-db scoping so we don't re-thread the cache_key through every
  route signature.

A single :class:`~arango_sparql.schema.cache.SchemaCache` instance
lives at module scope as the process-wide L1 cache. Test isolation
is via :func:`_reset_cache` (called from ``conftest.py`` fixtures
and from individual tests via the public ``invalidate``-route).
"""

from __future__ import annotations

import logging as _log
import os
import time
from typing import Any

from fastapi import Depends, HTTPException

from ...schema.acquire import (
    ANALYZER_INSTALL_HINT,
    AnalyzerNotInstalledError,
    Strategy,
    acquire_mapping_bundle,
    analyzer_available,
    db_counts_fingerprint,
    db_shape_fingerprint,
)
from ...schema.cache import SchemaCache
from ...schema.fingerprint import BundleFingerprint, FingerprintDrift
from ...translate.mapping import (
    MappingBundle,
    MappingError,
    mapping_from_wire_dict,
    mapping_to_wire_dict,
)
from ...translate.owl import (
    OwlBombError,
    OwlParseError,
    mapping_to_turtle,
    owl_graph_view,
)
from ..app import app
from ..models import (
    OwlSchemaResponse,
    SchemaFingerprintBlock,
    SchemaForceReacquireResponse,
    SchemaIntrospectResponse,
    SchemaInvalidateCacheResponse,
    SchemaPropertiesResponse,
    SchemaStatisticsResponse,
    SchemaStatusResponse,
    SchemaSummaryRequest,
    SchemaSummaryResponse,
)
from ..observability import log_endpoint_timing
from ..security import (
    _check_compute_rate_limit,
    _get_session,
    _sanitize_error,
    _Session,
)

logger = _log.getLogger("arango_sparql.service.routes.schema")

# ---------------------------------------------------------------------------
# Process-wide cache singleton
# ---------------------------------------------------------------------------
#
# Per Slice 5 cache.py docstring: "every consumer that needs caching
# gets a SchemaCache instance from the FastAPI app factory". This is
# that instance for the route layer. Tests reset it via
# :func:`_reset_cache`; the underlying L1 dict has its own
# :class:`threading.Lock` so concurrent uvicorn worker threads do not
# need a route-side lock.

_schema_cache: SchemaCache = SchemaCache()


def _reset_cache() -> None:
    """Test hook: drop the process-wide cache so a fresh test does
    not see entries written by an earlier test. Safe to call in
    production too — equivalent to invalidating every connected DB.
    """

    _schema_cache.clear()


def _resolve_schema_cache() -> SchemaCache:
    """Return the live :class:`SchemaCache` instance.

    Looked up via this indirection (rather than imported by name)
    so a test can monkey-patch the module-level binding for one
    case without altering the real cache for the rest of the suite.
    """

    return _schema_cache


# ---------------------------------------------------------------------------
# Per-request env-var resolution (PRD §6.3.4)
# ---------------------------------------------------------------------------
#
# Both env vars are read on *every* request rather than cached at
# import time. This is deliberate: an operator can flip a feature
# flag mid-deployment (e.g. via a rolling restart of the surrounding
# control plane) without restarting the service. The per-request
# read costs ~200 ns and is dominated by the rate-limit + session
# dependency chain that runs before this point.


def _allow_heuristic_fallback() -> bool:
    """Read ``ARANGO_SPARQL_ALLOW_HEURISTIC`` — defaults to ``True``,
    forced ``False`` in :data:`arango_sparql.service.app._PUBLIC_MODE`.

    Opt-out is deliberately verbose (PRD §6.3.4): only an explicit
    known-false value (``false``/``0``/``no``) disables fallback.
    Empty / unrecognised values stay at the safe ``True`` default so
    a typo in deployment YAML does not silently degrade the surface.
    """

    from ..app import _PUBLIC_MODE

    if _PUBLIC_MODE:
        return False
    raw = (os.getenv("ARANGO_SPARQL_ALLOW_HEURISTIC") or "").strip().lower()
    return raw not in ("false", "0", "no")


def _analyzer_required() -> bool:
    """Read ``SCHEMA_ANALYZER_REQUIRED`` — defaults to ``True``.

    Same deliberately-verbose opt-out semantics as
    :func:`_allow_heuristic_fallback` above. Garbage values stay
    at the safe default.
    """

    raw = (os.getenv("SCHEMA_ANALYZER_REQUIRED") or "").strip().lower()
    return raw not in ("false", "0", "no")


# ---------------------------------------------------------------------------
# Bundle → summary projection
# ---------------------------------------------------------------------------


def _summary_from_bundle(bundle: MappingBundle) -> dict[str, Any]:
    """Project a :class:`MappingBundle` into the UI-friendly summary
    shape PRD §6.4 row 3 documents.

    Keeps the projection deterministic — entities and relationships
    are sorted by name so two callers (UI, planner-cache) receive
    byte-identical output for the same bundle.
    """

    physical = bundle.physical_mapping or {}
    entities_raw = physical.get("entities") or {}
    relationships_raw = physical.get("relationships") or {}

    entities: list[dict[str, Any]] = []
    rpt_collections: list[dict[str, Any]] = []
    for label in sorted(entities_raw):
        spec = entities_raw[label]
        if not isinstance(spec, dict):
            continue
        style = spec.get("style", "COLLECTION")
        entry: dict[str, Any] = {
            "label": label,
            "style": style,
            "collection": spec.get("collectionName")
            or spec.get("triplesCollection"),
            "property_count": _count_properties(spec),
        }
        if style == "LABEL":
            entry["typeField"] = spec.get("typeField")
            entry["typeValue"] = spec.get("typeValue")
        if style == "RPT":
            rpt_collections.append(
                {
                    "label": label,
                    "triplesCollection": spec.get("triplesCollection"),
                    "subjectColumn": spec.get("subjectColumn"),
                    "predicateColumn": spec.get("predicateColumn"),
                    "objectUriColumn": spec.get("objectUriColumn"),
                    "objectValueColumn": spec.get("objectValueColumn"),
                    "rptCoverage": spec.get("rptCoverage"),
                }
            )
        entities.append(entry)

    relationships: list[dict[str, Any]] = []
    for rtype in sorted(relationships_raw):
        spec = relationships_raw[rtype]
        if not isinstance(spec, dict):
            continue
        relationships.append(
            {
                "type": rtype,
                "style": spec.get("style", "DEDICATED_COLLECTION"),
                "edgeCollection": spec.get("edgeCollectionName"),
                "domain": spec.get("domain") or spec.get("fromEntity"),
                "range": spec.get("range") or spec.get("toEntity"),
                "typeField": spec.get("typeField"),
                "typeValue": spec.get("typeValue"),
            }
        )

    return {
        "entities": entities,
        "relationships": relationships,
        "rpt_collections": rpt_collections,
        "entity_count": len(entities),
        "relationship_count": len(relationships),
    }


def _count_properties(spec: dict[str, Any]) -> int:
    """Number of mapped properties for an entity spec, or ``0`` when
    the spec carries no ``properties`` block.
    """

    props = spec.get("properties")
    if isinstance(props, dict):
        return len(props)
    if isinstance(props, list):
        return len(props)
    return 0


def _bundle_source_dict(bundle: MappingBundle) -> dict[str, Any] | None:
    """Render :attr:`MappingBundle.source` as a plain dict for the
    JSON response, or ``None`` when no provenance was stamped.
    """

    if bundle.source is None:
        return None
    return {"kind": bundle.source.kind, "notes": bundle.source.notes}


def _last_acquired_at(bundle: MappingBundle) -> str | None:
    """Pull the acquisition timestamp from bundle metadata. Two
    keys are checked because the heuristic builder uses
    ``timestamp`` while the acquire layer stamps ``acquisitionTimestamp``.
    """

    metadata = bundle.metadata or {}
    return (
        metadata.get("acquisitionTimestamp")
        or metadata.get("timestamp")
        or metadata.get("acquiredAt")
    )


# ---------------------------------------------------------------------------
# Cache-aware acquisition orchestration
# ---------------------------------------------------------------------------


def _strategy_or_422(value: str) -> Strategy:
    """Coerce a query-string ``strategy`` into the Literal type the
    acquire layer accepts. Maps invalid values to a 422 with a
    stable code so the UI can surface the supported set.
    """

    if value not in ("auto", "analyzer", "heuristic"):
        raise HTTPException(
            status_code=422,
            detail={
                "error": (
                    f"strategy must be 'auto', 'analyzer', or "
                    f"'heuristic', got {value!r}"
                ),
                "code": "E_SCHEMA_STRATEGY_INVALID",
            },
        )
    return value  # type: ignore[return-value]


def _scoped_cache_key(db_name: str, graph_name: str | None) -> str:
    """Cache key for *db_name* under an optional named-graph scope.

    Unscoped (``graph_name is None``) keeps the bare ``db_name`` so the
    full-DB bundle stays addressable by every existing caller. A scoped
    view gets a distinct ``"<db>::graph::<name>"`` slot so binding /
    unbinding a graph never serves the wrong bundle from L1.
    """
    if not graph_name:
        return db_name
    return f"{db_name}::graph::{graph_name}"


def _get_or_acquire(
    db: Any,
    *,
    force: bool,
    strategy: Strategy,
    include_owl: bool = False,
    graph_name: str | None = None,
) -> tuple[MappingBundle, bool]:
    """Return ``(bundle, cache_hit)`` for *db*. When *force* is true
    or no fresh entry exists, runs ``acquire_mapping_bundle`` and
    repopulates the cache.

    Cache-key is ``db.name`` (plus a ``::graph::<name>`` suffix when a
    named-graph scope is bound) — the analyzer's exclude-collections
    invariant in :func:`db_shape_fingerprint` already protects us
    against the L2 cache-self-loop case (PRD §6.3.3).
    """

    cache = _resolve_schema_cache()
    db_name = getattr(db, "name", "") or ""
    cache_key = _scoped_cache_key(db_name, graph_name)
    if not force and db_name:
        entry = cache.get(cache_key)
        # Re-acquire on an OWL request the cached bundle can't satisfy: the
        # cache key doesn't encode ``include_owl``, so a bundle first cached
        # without OWL would otherwise starve the UI's ontology auto-fill.
        if entry is not None and not (include_owl and entry.bundle.owl_turtle is None):
            return entry.bundle, True

    bundle = acquire_mapping_bundle(
        db,
        include_owl=include_owl,
        strategy=strategy,
        force_refresh=force,
        graph_name=graph_name,
    )
    if db_name:
        cache.put(cache_key, bundle)
    return bundle, False


def _enforce_force_reacquire_policy() -> None:
    """PRD §6.3.4 row 4 — when both opt-outs are off, ``/schema/force-
    reacquire`` cannot serve any path and must return 503. Done here
    so the policy is enforced at the route boundary, not buried
    inside the acquire layer (which has no way to surface a 503).
    """

    if not _analyzer_required() and not _allow_heuristic_fallback():
        raise HTTPException(
            status_code=503,
            detail={
                "error": (
                    "Schema acquisition is disabled in this deployment. "
                    "Both SCHEMA_ANALYZER_REQUIRED and "
                    "ARANGO_SPARQL_ALLOW_HEURISTIC are set to false; "
                    "no acquisition path is available. Push a mapping "
                    "via POST /mapping/import-owl instead."
                ),
                "code": "E_SCHEMA_UNAVAILABLE",
            },
        )


# ---------------------------------------------------------------------------
# 1) GET /schema/introspect
# ---------------------------------------------------------------------------


@app.get("/schema/introspect", response_model=SchemaIntrospectResponse)
def schema_introspect(
    force: bool = False,
    strategy: str = "auto",
    include_owl: bool = False,
    _: None = Depends(_check_compute_rate_limit),
    session: _Session = Depends(_get_session),
) -> SchemaIntrospectResponse:
    """Live schema acquisition. Respects the L1 cache unless
    ``force=true``. ``strategy`` ∈ ``{auto, analyzer, heuristic}``;
    invalid values are 422.

    When ``include_owl=true`` the acquired mapping carries its inline
    OWL/Turtle in ``mapping.owlTurtle`` so the UI can auto-populate the
    ontology editor on connect — without it, callers would have to hand
    the ontology back in on every ``/translate``.
    """

    typed_strategy = _strategy_or_422(strategy)
    t0 = time.perf_counter()

    try:
        bundle, cache_hit = _get_or_acquire(
            session.db,
            force=force,
            strategy=typed_strategy,
            include_owl=include_owl,
            graph_name=getattr(session, "graph_name", None),
        )
    except AnalyzerNotInstalledError as exc:
        # Explicit ``strategy=analyzer`` on a deployment without the
        # analyzer extra. This is operator error (the request asked
        # for a path the deployment doesn't offer) — return 503 with
        # the install hint so the UI can render an actionable banner.
        log_endpoint_timing(
            "/schema/introspect",
            round((time.perf_counter() - t0) * 1000, 1),
            status="error",
            code="E_ANALYZER_NOT_INSTALLED",
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": _sanitize_error(str(exc)),
                "code": "E_ANALYZER_NOT_INSTALLED",
                "install_hint": ANALYZER_INSTALL_HINT,
            },
        ) from exc
    except MappingError as exc:
        # Bundle wire-shape validation failed — the analyzer
        # produced something we cannot consume. Surface as 422 with
        # the typed code so the UI can distinguish from transport-
        # layer errors.
        raise HTTPException(
            status_code=422,
            detail={
                "error": _sanitize_error(str(exc)),
                "code": exc.code,
            },
        ) from exc

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    summary = _summary_from_bundle(bundle)
    warnings = (bundle.metadata or {}).get("warnings") or []
    log_endpoint_timing(
        "/schema/introspect",
        elapsed_ms,
        force=force,
        strategy=typed_strategy,
        cache_hit=cache_hit,
        entities=summary["entity_count"],
        relationships=summary["relationship_count"],
        warnings=len(warnings),
        source=(bundle.source.kind if bundle.source is not None else "unknown"),
    )
    return SchemaIntrospectResponse(
        mapping=mapping_to_wire_dict(bundle),
        summary=summary,
        warnings=warnings,
        source=_bundle_source_dict(bundle),
        cache_hit=cache_hit,
        elapsed_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# 1b) GET /schema/owl
# ---------------------------------------------------------------------------


@app.get("/schema/owl", response_model=OwlSchemaResponse)
def schema_owl(
    force: bool = False,
    strategy: str = "auto",
    _: None = Depends(_check_compute_rate_limit),
    session: _Session = Depends(_get_session),
) -> OwlSchemaResponse:
    """OWL schema-graph projection of the connected database.

    Acquires (or reuses the cached) :class:`MappingBundle` for the
    session's database, serialises it to OWL/Turtle via
    :func:`mapping_to_turtle`, and projects it into the
    ``{classes, properties}`` shape the UI's GRAPH tab renders (the same
    shape the frontend's client-side ``n3`` parser produces for in-editor
    ontologies). Acquisition, cache, and ``strategy`` semantics mirror
    ``/schema/introspect``.
    """

    typed_strategy = _strategy_or_422(strategy)
    t0 = time.perf_counter()

    try:
        bundle, cache_hit = _get_or_acquire(
            session.db,
            force=force,
            strategy=typed_strategy,
            include_owl=True,
            graph_name=getattr(session, "graph_name", None),
        )
    except AnalyzerNotInstalledError as exc:
        log_endpoint_timing(
            "/schema/owl",
            round((time.perf_counter() - t0) * 1000, 1),
            status="error",
            code="E_ANALYZER_NOT_INSTALLED",
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": _sanitize_error(str(exc)),
                "code": "E_ANALYZER_NOT_INSTALLED",
                "install_hint": ANALYZER_INSTALL_HINT,
            },
        ) from exc
    except MappingError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": _sanitize_error(str(exc)), "code": exc.code},
        ) from exc

    try:
        turtle = mapping_to_turtle(bundle)
        view = owl_graph_view(turtle)
    except (OwlParseError, OwlBombError) as exc:
        # The Turtle we just serialised could not be re-parsed into the
        # graph view — this is an internal-consistency failure, but it is
        # driven by the (possibly customer-supplied) ontology, so surface
        # it as a 422 with the typed code rather than a 500.
        log_endpoint_timing(
            "/schema/owl",
            round((time.perf_counter() - t0) * 1000, 1),
            status="error",
            code=exc.code,
        )
        raise HTTPException(
            status_code=422,
            detail={"error": _sanitize_error(str(exc)), "code": exc.code},
        ) from exc
    except MappingError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": _sanitize_error(str(exc)), "code": exc.code},
        ) from exc

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    warnings = (bundle.metadata or {}).get("warnings") or []
    log_endpoint_timing(
        "/schema/owl",
        elapsed_ms,
        force=force,
        strategy=typed_strategy,
        cache_hit=cache_hit,
        classes=len(view["classes"]),
        properties=len(view["properties"]),
        warnings=len(warnings),
        source=(bundle.source.kind if bundle.source is not None else "unknown"),
    )
    return OwlSchemaResponse(
        classes=view["classes"],
        properties=view["properties"],
        turtle=turtle,
        source=_bundle_source_dict(bundle),
        warnings=warnings,
        elapsed_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# 2) GET /schema/properties
# ---------------------------------------------------------------------------

# Cap on the per-call sample size. Keeps a malicious / sloppy caller
# from asking for millions of docs in one round-trip; the underlying
# AQL still uses LIMIT @n so the server-side cost is bounded too.
# Held as a module constant so an operator can monkey-patch one
# value rather than scattering magic numbers.
_PROPERTIES_SAMPLE_HARD_CAP: int = 1000


@app.get("/schema/properties", response_model=SchemaPropertiesResponse)
def schema_properties(
    collection: str,
    sample_size: int = 100,
    _: None = Depends(_check_compute_rate_limit),
    session: _Session = Depends(_get_session),
) -> SchemaPropertiesResponse:
    """Per-collection inferred property catalog. Samples up to
    *sample_size* documents (capped at 1000) and returns
    ``{<attr>: {field, type, required}}``.
    """

    if not collection or not isinstance(collection, str):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "collection query parameter is required",
                "code": "E_SCHEMA_BAD_COLLECTION",
            },
        )
    capped = max(1, min(sample_size, _PROPERTIES_SAMPLE_HARD_CAP))

    t0 = time.perf_counter()
    properties = _sample_properties(session.db, collection, capped)
    log_endpoint_timing(
        "/schema/properties",
        round((time.perf_counter() - t0) * 1000, 1),
        collection=collection,
        sample_size=capped,
        properties=len(properties),
    )
    return SchemaPropertiesResponse(
        collection=collection,
        sample_size=capped,
        properties=properties,
    )


def _sample_properties(
    db: Any, collection_name: str, sample_size: int
) -> dict[str, dict[str, Any]]:
    """Sample documents from *collection_name* and infer property
    name + dominant type + required-flag.

    Mirrors the sister project's helper. Underscore-prefixed
    fields (``_key``, ``_id``, ``_rev``, ``_from``, ``_to``) are
    skipped because they are ArangoDB-managed metadata, not
    user-visible attributes.
    """

    try:
        cursor = db.aql.execute(
            "FOR doc IN @@col LIMIT @n RETURN doc",
            bind_vars={"@col": collection_name, "n": sample_size},
        )
        docs = list(cursor)
    except Exception as exc:
        logger.info(
            "sample failed for collection %r: %s", collection_name, exc
        )
        return {}

    if not docs:
        return {}

    field_types: dict[str, dict[str, int]] = {}
    presence: dict[str, int] = {}
    samples: dict[str, Any] = {}

    for doc in docs:
        if not isinstance(doc, dict):
            continue
        for key, val in doc.items():
            if key.startswith("_"):
                continue
            type_buckets = field_types.setdefault(key, {})
            t = _infer_type(val)
            type_buckets[t] = type_buckets.get(t, 0) + 1
            presence[key] = presence.get(key, 0) + 1
            samples.setdefault(key, val)

    out: dict[str, dict[str, Any]] = {}
    total = len(docs)
    for name, types in field_types.items():
        dominant = max(types, key=types.get)  # type: ignore[arg-type]
        out[name] = {
            "field": name,
            "type": dominant,
            "required": presence.get(name, 0) == total,
            "sample": samples.get(name),
        }
    return out


def _infer_type(val: Any) -> str:
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "boolean"
    if isinstance(val, int | float):
        return "number"
    if isinstance(val, str):
        return "string"
    if isinstance(val, list):
        return "array"
    if isinstance(val, dict):
        return "object"
    return "string"


# ---------------------------------------------------------------------------
# 3) GET /schema/summary  (no DB access)
# ---------------------------------------------------------------------------


def _schema_summary_impl(
    req: SchemaSummaryRequest,
) -> SchemaSummaryResponse:
    """Body of the ``/schema/summary`` GET and POST handlers. Split
    out so the two route registrations share one implementation
    while keeping unique FastAPI operation IDs (which the OpenAPI
    spec requires).
    """

    if not req.mapping and not req.ontology_ttl:
        raise HTTPException(
            status_code=422,
            detail={
                "error": (
                    "Either 'mapping' (wire dict) or 'ontology_ttl' "
                    "(Turtle string) must be supplied."
                ),
                "code": "E_SCHEMA_SUMMARY_EMPTY",
            },
        )

    t0 = time.perf_counter()
    try:
        bundle = _summary_input_to_bundle(req)
    except MappingError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": _sanitize_error(str(exc)),
                "code": exc.code,
            },
        ) from exc
    except Exception as exc:
        # Turtle parse failures land here — surface with a typed
        # code so the UI can distinguish "your TTL is malformed"
        # from "we don't recognise that mapping shape".
        raise HTTPException(
            status_code=422,
            detail={
                "error": _sanitize_error(
                    f"failed to parse mapping input: {exc}"
                ),
                "code": "E_SCHEMA_SUMMARY_PARSE",
            },
        ) from exc

    summary = _summary_from_bundle(bundle)
    log_endpoint_timing(
        "/schema/summary",
        round((time.perf_counter() - t0) * 1000, 1),
        entities=summary["entity_count"],
        relationships=summary["relationship_count"],
        rpt_collections=len(summary["rpt_collections"]),
    )
    return SchemaSummaryResponse(**summary)


@app.get(
    "/schema/summary",
    response_model=SchemaSummaryResponse,
    operation_id="schema_summary_get",
)
def schema_summary_get(
    req: SchemaSummaryRequest,
    _: None = Depends(_check_compute_rate_limit),
) -> SchemaSummaryResponse:
    """Conceptual summary of a client-supplied mapping (GET). **No
    DB access** — the only route in this surface that does not
    require a session. PRD §6.4 row 3 specifies GET-with-body; HTTP
    proxies that strip GET bodies should use the POST form below.
    """

    return _schema_summary_impl(req)


@app.post(
    "/schema/summary",
    response_model=SchemaSummaryResponse,
    operation_id="schema_summary_post",
)
def schema_summary_post(
    req: SchemaSummaryRequest,
    _: None = Depends(_check_compute_rate_limit),
) -> SchemaSummaryResponse:
    """Conceptual summary of a client-supplied mapping (POST). Same
    semantics as the GET form; offered for clients whose HTTP stack
    rejects GET bodies (most JS fetch implementations).
    """

    return _schema_summary_impl(req)


def _summary_input_to_bundle(req: SchemaSummaryRequest) -> MappingBundle:
    """Build a :class:`MappingBundle` from whichever input shape
    *req* supplies. Wire-dict wins when both are present (mirrors
    the precedence the translate route uses for ``ontology_ttl`` vs
    ``mapping``, just inverted because the route's primary input
    here is the JSON wire dict).
    """

    if req.mapping:
        return mapping_from_wire_dict(req.mapping)

    # ontology_ttl path: parse Turtle into a SchemaResolver first,
    # then ask it to synthesize a (small) MappingBundle for the
    # summary projection. Implemented inline to avoid a dependency
    # on the not-yet-built /mapping/import-owl machinery (Slice 7).
    from rdflib import Graph

    from ...translate.resolver import SchemaResolver

    graph = Graph()
    graph.parse(data=req.ontology_ttl or "", format="turtle")
    # We only need the bundle for projection; an empty resolver
    # would lose all the OWL annotations that drive the summary.
    # Round-trip through the resolver's own conceptual surface —
    # currently the conservative path is to wrap the parsed Turtle
    # back into a bundle whose owl_turtle field carries the source.
    SchemaResolver(ontology=graph)  # parse-validation side effect
    return MappingBundle(
        conceptual_schema={"entities": [], "relationships": []},
        physical_mapping={"entities": {}, "relationships": {}},
        metadata={"source": "summary_from_turtle_only"},
        owl_turtle=req.ontology_ttl,
    )


# ---------------------------------------------------------------------------
# 4) GET /schema/statistics
# ---------------------------------------------------------------------------


@app.get("/schema/statistics", response_model=SchemaStatisticsResponse)
def schema_statistics(
    _: None = Depends(_check_compute_rate_limit),
    session: _Session = Depends(_get_session),
) -> SchemaStatisticsResponse:
    """Surface the analyzer-supplied ``metadata.statistics`` block
    from the cached / freshly-acquired bundle.

    When statistics are absent (heuristic-only bundle, or analyzer
    skipped them), ``available=false`` and an empty ``statistics``
    dict are returned. Live recomputation is deferred to a follow-
    up slice — PRD §6.4 row 4 is satisfied by surfacing the
    block as-is.
    """

    t0 = time.perf_counter()
    try:
        bundle, _hit = _get_or_acquire(
            session.db,
            force=False,
            strategy="auto",
            graph_name=getattr(session, "graph_name", None),
        )
    except AnalyzerNotInstalledError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": _sanitize_error(str(exc)),
                "code": "E_ANALYZER_NOT_INSTALLED",
                "install_hint": ANALYZER_INSTALL_HINT,
            },
        ) from exc

    stats = (bundle.metadata or {}).get("statistics") or {}
    available = bool(
        isinstance(stats, dict) and (stats.get("relationships") or stats.get("entities"))
    )
    log_endpoint_timing(
        "/schema/statistics",
        round((time.perf_counter() - t0) * 1000, 1),
        available=available,
    )
    return SchemaStatisticsResponse(
        statistics=stats if isinstance(stats, dict) else {},
        available=available,
        last_acquired_at=_last_acquired_at(bundle),
    )


# ---------------------------------------------------------------------------
# 5) GET /schema/status
# ---------------------------------------------------------------------------


@app.get("/schema/status", response_model=SchemaStatusResponse)
def schema_status(
    session: _Session = Depends(_get_session),
) -> SchemaStatusResponse:
    """Cheap drift report. Compares the live DB's two analyzer
    fingerprints against the cached bundle's fingerprints (Slice 3
    mappings). When the analyzer extra is missing, falls back to
    the bundle-side fingerprints — less precise but still
    actionable.

    Status values: ``unchanged``, ``stats_only``, ``shape_changed``,
    ``no_cache``.
    """

    t0 = time.perf_counter()
    cache = _resolve_schema_cache()
    db_name = getattr(session.db, "name", "") or ""

    cached_entry = cache.get(db_name) if db_name else None

    current_shape = db_shape_fingerprint(session.db)
    current_counts = db_counts_fingerprint(session.db)

    # When the analyzer's live fingerprints are unavailable (extra
    # missing or transient error), use the cached bundle's own
    # fingerprints as a "shape stable iff cache exists" stand-in.
    # Less precise — it cannot detect drift before the cache hits —
    # but it never reports a false-positive shape change.
    if cached_entry is None:
        status = "no_cache"
        cached_block = SchemaFingerprintBlock()
        unchanged = False
        needs_full = True
    else:
        cached_fp = cached_entry.fingerprint
        cached_block = SchemaFingerprintBlock(
            shape=cached_fp.shape, counts=cached_fp.counts
        )
        if current_shape is None or current_counts is None:
            # Cannot compute live fingerprints — degrade to "we
            # have a cache but cannot tell if it's stale". Treat
            # as unchanged so the UI does not flap, but flag in
            # the log so an operator notices the analyzer-down
            # condition.
            logger.info(
                "schema_status: live fingerprints unavailable; "
                "reporting cache as stable for db=%r",
                db_name,
            )
            status = "unchanged"
            unchanged = True
            needs_full = False
        else:
            current_fp = BundleFingerprint(
                shape=current_shape,
                counts=current_counts,
                payload_version=cached_fp.payload_version,
                computed_at=cached_fp.computed_at,
            )
            drift = current_fp.drift_from(cached_fp)
            status = drift.value
            unchanged = drift is FingerprintDrift.UNCHANGED
            needs_full = drift is FingerprintDrift.SHAPE_CHANGED

    last_acquired = (
        _last_acquired_at(cached_entry.bundle)
        if cached_entry is not None
        else None
    )

    log_endpoint_timing(
        "/schema/status",
        round((time.perf_counter() - t0) * 1000, 1),
        report_status=status,
        unchanged=unchanged,
        has_cache=cached_entry is not None,
    )
    return SchemaStatusResponse(
        status=status,
        unchanged=unchanged,
        needs_full_rebuild=needs_full,
        current=SchemaFingerprintBlock(
            shape=current_shape, counts=current_counts
        ),
        cached=cached_block,
        last_acquired_at=last_acquired,
        db_name=db_name or None,
    )


# ---------------------------------------------------------------------------
# 6) POST /schema/invalidate-cache
# ---------------------------------------------------------------------------


@app.post(
    "/schema/invalidate-cache",
    response_model=SchemaInvalidateCacheResponse,
)
def schema_invalidate_cache(
    _: None = Depends(_check_compute_rate_limit),
    session: _Session = Depends(_get_session),
) -> SchemaInvalidateCacheResponse:
    """Drop the cached mapping for the connected DB. Returns
    ``invalidated=true`` iff an L1 entry was actually evicted.

    L2 (persistent) tier is a stub today — ``persistent_dropped``
    is always ``False`` until the follow-up slice lands. The flag
    is emitted in the response so a future-compatible client can
    reason about it without a version check.
    """

    t0 = time.perf_counter()
    cache = _resolve_schema_cache()
    db_name = getattr(session.db, "name", "") or ""
    invalidated = bool(db_name) and cache.invalidate(db_name)
    log_endpoint_timing(
        "/schema/invalidate-cache",
        round((time.perf_counter() - t0) * 1000, 1),
        invalidated=invalidated,
        db=db_name or "unknown",
    )
    return SchemaInvalidateCacheResponse(
        invalidated=invalidated,
        db_name=db_name,
        persistent_dropped=False,
    )


# ---------------------------------------------------------------------------
# 7) POST /schema/force-reacquire
# ---------------------------------------------------------------------------


@app.post(
    "/schema/force-reacquire",
    response_model=SchemaForceReacquireResponse,
)
def schema_force_reacquire(
    strategy: str = "auto",
    _: None = Depends(_check_compute_rate_limit),
    session: _Session = Depends(_get_session),
) -> SchemaForceReacquireResponse:
    """Drop the cached mapping and rebuild from scratch.

    PRD §6.4 row 7: returns 503 when both
    ``SCHEMA_ANALYZER_REQUIRED=false`` and
    ``ARANGO_SPARQL_ALLOW_HEURISTIC=false`` (no acquisition path
    is available). When the analyzer extra is missing for an
    explicit ``strategy=analyzer`` request, returns 503 with the
    install hint.
    """

    _enforce_force_reacquire_policy()
    typed_strategy = _strategy_or_422(strategy)

    # Honour the per-request fallback gate: if the operator has
    # disabled heuristic fallback AND the analyzer is missing, we
    # cannot serve any path even when ``strategy="auto"`` would
    # normally degrade to heuristic.
    if (
        typed_strategy == "auto"
        and not _allow_heuristic_fallback()
        and not analyzer_available()
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "error": (
                    "schema-analyzer is not installed and heuristic "
                    "fallback is disabled "
                    "(ARANGO_SPARQL_ALLOW_HEURISTIC=false). Install "
                    "the analyzer or re-enable the heuristic fallback."
                ),
                "code": "E_ANALYZER_NOT_INSTALLED",
                "install_hint": ANALYZER_INSTALL_HINT,
            },
        )

    t0 = time.perf_counter()
    try:
        bundle, _hit = _get_or_acquire(
            session.db,
            force=True,
            strategy=typed_strategy,
            graph_name=getattr(session, "graph_name", None),
        )
    except AnalyzerNotInstalledError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": _sanitize_error(str(exc)),
                "code": "E_ANALYZER_NOT_INSTALLED",
                "install_hint": ANALYZER_INSTALL_HINT,
            },
        ) from exc
    except MappingError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": _sanitize_error(str(exc)),
                "code": exc.code,
            },
        ) from exc

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    summary = _summary_from_bundle(bundle)
    warnings = (bundle.metadata or {}).get("warnings") or []
    log_endpoint_timing(
        "/schema/force-reacquire",
        elapsed_ms,
        strategy=typed_strategy,
        entities=summary["entity_count"],
        relationships=summary["relationship_count"],
        warnings=len(warnings),
        source=(bundle.source.kind if bundle.source is not None else "unknown"),
    )
    return SchemaForceReacquireResponse(
        mapping=mapping_to_wire_dict(bundle),
        summary=summary,
        warnings=warnings,
        source=_bundle_source_dict(bundle),
        cache_hit=False,
        elapsed_ms=elapsed_ms,
    )


__all__ = [
    "_PROPERTIES_SAMPLE_HARD_CAP",
    "_reset_cache",
    "_resolve_schema_cache",
]
