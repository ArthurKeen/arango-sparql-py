"""OWL/Turtle import + export endpoints — PRD §6.4 rows 8 & 9.

Two routes:

* ``POST /mapping/import-owl`` — parse a posted OWL/Turtle ontology
  into a :class:`MappingBundle` and return its wire-dict form. Both
  raw ``text/turtle`` and JSON ``{turtle, source_notes?}`` request
  bodies are accepted (raw wins when both are present). Imported
  bundles are stamped with ``source.kind = "imported_owl"`` so
  downstream telemetry can distinguish them from analyzer- or
  heuristic-acquired bundles.

* ``POST /mapping/export-owl`` — serialise a :class:`MappingBundle`
  (or an already-Turtle string the caller wants to round-trip) back
  to OWL/Turtle. Honors ``Accept: text/turtle`` to return a raw
  ``text/turtle`` response (the AOE / Microsoft Ontology Playground
  integration path documented in PRD §11.3); defaults to a JSON
  envelope ``{turtle, mime_type, triple_count}`` for clients that
  prefer programmatic consumption.

OWL-bomb defences (PRD §8.6 T7):

1. Byte ceiling — the route reads the raw request body, sizes it,
   and short-circuits with ``413`` when the body exceeds
   :envvar:`MAPPING_IMPORT_MAX_BYTES` (default 2 MB).
2. Triple cap — applied inside :func:`turtle_to_mapping`; surfaces
   here as a 422 with the typed code ``E_OWL_TOO_LARGE``.
3. Pydantic ``max_length`` on the ``turtle`` field of
   :class:`OwlImportRequest` provides the JSON-envelope mirror of
   the byte ceiling so a hostile JSON body still hits the cap
   before the parser runs.
"""

from __future__ import annotations

import json
import logging as _log
import os
import time
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from ...errors import SparqlError
from ...translate.mapping import (
    MappingError,
    mapping_from_wire_dict,
    mapping_to_wire_dict,
)
from ...translate.owl import (
    OwlBombError,
    OwlParseError,
    count_triples,
    mapping_to_turtle,
    turtle_to_mapping,
)
from ..app import app
from ..models import (
    _DEFAULT_MAPPING_IMPORT_MAX_BYTES,
    OwlExportRequest,
    OwlExportResponse,
    OwlImportResponse,
)
from ..observability import log_endpoint_timing
from ..security import (
    _check_compute_rate_limit,
    _get_session,
    _sanitize_error,
    _Session,
)

logger = _log.getLogger("arango_sparql.service.routes.mapping")


# ---------------------------------------------------------------------------
# Per-request env-var resolution (PRD A.2 ``MAPPING_IMPORT_MAX_BYTES``)
# ---------------------------------------------------------------------------
#
# Read on every request rather than cached at import time so an
# operator can flip the cap mid-deployment without a restart. The
# per-request cost is dominated by the rate-limit + session
# dependency chain that runs before this point. Garbage values fall
# through to the default rather than raising — a deployment YAML
# typo must not silently disable the cap (PRD §6.3.4 motif applied
# to OWL-bomb defence).

MAPPING_IMPORT_MAX_BYTES_ENV: str = "MAPPING_IMPORT_MAX_BYTES"


def _resolve_max_bytes() -> int:
    """Return the active byte ceiling for ``/mapping/import-owl``."""

    raw = (os.getenv(MAPPING_IMPORT_MAX_BYTES_ENV) or "").strip()
    if not raw:
        return _DEFAULT_MAPPING_IMPORT_MAX_BYTES
    try:
        parsed = int(raw)
        if parsed > 0:
            return parsed
    except ValueError:
        pass
    return _DEFAULT_MAPPING_IMPORT_MAX_BYTES


# ---------------------------------------------------------------------------
# Body extraction + content-negotiation
# ---------------------------------------------------------------------------


async def _read_import_body(request: Request, max_bytes: int) -> tuple[str, str | None]:
    """Pull the raw bytes off *request*, enforce the byte ceiling,
    and decode into a ``(turtle, source_notes)`` tuple.

    Returns ``(turtle, source_notes)`` where ``source_notes`` is
    drawn from a JSON envelope (``{turtle, source_notes}``) when
    that shape is present, else ``None``.

    Raises ``HTTPException(413, ...)`` when the body exceeds
    *max_bytes*; ``HTTPException(422, ...)`` when neither a JSON
    envelope nor a UTF-8 decodable body is present.
    """

    raw = await request.body()
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "error": (
                    f"OWL import body of {len(raw)} bytes exceeds the "
                    f"{MAPPING_IMPORT_MAX_BYTES_ENV} cap of {max_bytes} "
                    "bytes."
                ),
                "code": "E_OWL_TOO_LARGE",
                "max_bytes": max_bytes,
                "actual_bytes": len(raw),
            },
        )
    if not raw:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "empty request body; supply Turtle or JSON envelope",
                "code": "E_OWL_EMPTY_BODY",
            },
        )

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    # JSON envelope wins when explicit. ``application/json`` and the
    # legacy ``application/x-www-form-urlencoded`` (some fetch
    # polyfills default to that) both go through the JSON parser
    # because the JSON envelope is the documented JSON-shaped form.
    if content_type in ("application/json", ""):
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("turtle"), str):
            turtle = payload["turtle"]
            if not turtle:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "JSON envelope 'turtle' field is empty",
                        "code": "E_OWL_EMPTY_BODY",
                    },
                )
            notes = payload.get("source_notes")
            return turtle, notes if isinstance(notes, str) else None
        if content_type == "application/json":
            # JSON content-type but unrecognised body shape — fail
            # loudly rather than silently falling back to "treat the
            # JSON bytes as Turtle".
            raise HTTPException(
                status_code=422,
                detail={
                    "error": ("JSON body must be {turtle: str, source_notes?: str}"),
                    "code": "E_OWL_BAD_JSON",
                },
            )

    # Treat any non-JSON content as raw Turtle. ``text/turtle`` is
    # the documented happy path; ``text/plain`` and unset content
    # types (curl's default) are accepted too.
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": (
                    "request body is not valid UTF-8; supply Turtle as UTF-8 or wrap it in a JSON envelope"
                ),
                "code": "E_OWL_NOT_UTF8",
            },
        ) from exc


def _wants_turtle_response(request: Request) -> bool:
    """Inspect the ``Accept`` header for a Turtle-ish media type.

    ``text/turtle`` and ``application/x-turtle`` are both honoured
    (the latter is the legacy spelling some clients still use).
    Anything else — including the default ``*/*`` — falls through
    to the JSON envelope so a curious browser doesn't see raw
    Turtle when it expected JSON.
    """

    accept = request.headers.get("accept", "").lower()
    if not accept or accept == "*/*":
        return False
    return "text/turtle" in accept or "application/x-turtle" in accept


# ---------------------------------------------------------------------------
# 1) POST /mapping/import-owl
# ---------------------------------------------------------------------------


@app.post("/mapping/import-owl", response_model=OwlImportResponse)
async def import_owl(
    request: Request,
    _: None = Depends(_check_compute_rate_limit),
    session: _Session = Depends(_get_session),
) -> OwlImportResponse:
    """Parse a posted OWL/Turtle ontology into a
    :class:`MappingBundle` and return its wire-dict form.

    Body:

    * ``Content-Type: text/turtle`` — raw Turtle bytes.
    * ``Content-Type: application/json`` — ``{turtle: str,
      source_notes?: str}`` envelope.

    Per PRD §8.6 T7 the byte ceiling and the post-parse triple cap
    are both enforced; both surface with the typed code
    ``E_OWL_TOO_LARGE`` (413 for bytes, 422 for triples) so a UI
    can render one error path for both.
    """

    t0 = time.perf_counter()
    max_bytes = _resolve_max_bytes()
    turtle, notes = await _read_import_body(request, max_bytes)

    # Stamp the session-effective ``source_notes`` so a downstream
    # audit log can pin "tenant X imported this OWL at time Y" even
    # when the JSON envelope doesn't carry an explicit notes field.
    effective_notes = notes or f"imported via /mapping/import-owl by {session.token[:8]}…"

    try:
        bundle = turtle_to_mapping(turtle, source_notes=effective_notes)
    except OwlBombError as exc:
        log_endpoint_timing(
            "/mapping/import-owl",
            round((time.perf_counter() - t0) * 1000, 1),
            status="error",
            code=exc.code,
            bytes=len(turtle.encode("utf-8")),
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": _sanitize_error(str(exc)),
                "code": exc.code,
            },
        ) from exc
    except OwlParseError as exc:
        log_endpoint_timing(
            "/mapping/import-owl",
            round((time.perf_counter() - t0) * 1000, 1),
            status="error",
            code=exc.code,
            bytes=len(turtle.encode("utf-8")),
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": _sanitize_error(str(exc)),
                "code": exc.code,
            },
        ) from exc
    except SparqlError as exc:
        # Defensive catch-all for any other typed translation error
        # the OWL importer surfaces (currently none, but the
        # ``SparqlError`` hierarchy is the documented contract).
        raise HTTPException(
            status_code=422,
            detail={
                "error": _sanitize_error(str(exc)),
                "code": exc.code,
            },
        ) from exc

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    triple_count = (bundle.metadata or {}).get("tripleCount", 0)
    warnings = (bundle.metadata or {}).get("warnings") or []

    log_endpoint_timing(
        "/mapping/import-owl",
        elapsed_ms,
        bytes=len(turtle.encode("utf-8")),
        triple_count=triple_count,
        entities=len(bundle.entities()),
        relationships=len(bundle.relationships()),
        warnings=len(warnings),
    )
    return OwlImportResponse(
        accepted=True,
        mapping=mapping_to_wire_dict(bundle),
        triple_count=int(triple_count),
        warnings=warnings,
        source={
            "kind": bundle.source.kind if bundle.source else None,
            "notes": bundle.source.notes if bundle.source else None,
        }
        if bundle.source
        else None,
        elapsed_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# 2) POST /mapping/export-owl
# ---------------------------------------------------------------------------


@app.post("/mapping/export-owl", response_model=None)
def export_owl(
    request: Request,
    req: OwlExportRequest,
    _: None = Depends(_check_compute_rate_limit),
    session: _Session = Depends(_get_session),
) -> OwlExportResponse | PlainTextResponse:
    """Serialise a :class:`MappingBundle` (or an already-Turtle blob)
    back to OWL/Turtle.

    Content-negotiated:

    * ``Accept: text/turtle`` (or ``application/x-turtle``) → raw
      Turtle response (``Content-Type: text/turtle``).
    * Anything else → JSON envelope ``{turtle, mime_type,
      triple_count, elapsed_ms}``.

    Either ``mapping`` (a :class:`MappingBundle` wire dict) or
    ``ontology_ttl`` (a Turtle blob the caller wants to round-trip
    through the synthesizer) must be supplied — 422 when both are
    empty.
    """

    if not req.mapping and not req.ontology_ttl:
        raise HTTPException(
            status_code=422,
            detail={
                "error": ("Either 'mapping' (wire dict) or 'ontology_ttl' (Turtle string) must be supplied."),
                "code": "E_OWL_EXPORT_EMPTY",
            },
        )

    t0 = time.perf_counter()
    try:
        if req.mapping:
            bundle = mapping_from_wire_dict(req.mapping)
        else:
            # ontology_ttl path — round-trip the Turtle through the
            # importer so the export can normalise prefixes and
            # surface the parsed triple count. The triple cap still
            # applies (PRD §8.6 T7 motif).
            bundle = turtle_to_mapping(req.ontology_ttl or "")
    except OwlBombError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": _sanitize_error(str(exc)),
                "code": exc.code,
            },
        ) from exc
    except OwlParseError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": _sanitize_error(str(exc)),
                "code": exc.code,
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

    try:
        turtle = mapping_to_turtle(bundle)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": _sanitize_error(f"failed to serialise OWL: {exc}"),
                "code": "E_OWL_EXPORT_FAILED",
            },
        ) from exc

    # Compute triple_count from the synthesised graph by re-importing
    # the just-serialised Turtle. Cheap (the graph we just built has
    # at most a few thousand triples), and lets us return one number
    # the caller can render as a "1.2k triples" badge regardless of
    # which input shape they supplied.
    try:
        from rdflib import Graph

        roundtrip = Graph()
        roundtrip.parse(data=turtle, format="turtle")
        triple_count = count_triples(roundtrip)
    except Exception:
        # Should never happen — we just produced this Turtle — but
        # don't fail the export over a count we can't compute.
        triple_count = 0

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    wants_turtle = _wants_turtle_response(request)

    log_endpoint_timing(
        "/mapping/export-owl",
        elapsed_ms,
        format="turtle" if wants_turtle else "json",
        triple_count=triple_count,
        bytes=len(turtle.encode("utf-8")),
        session=session.token[:8] + "…" if session and session.token else "unknown",
    )

    if wants_turtle:
        return PlainTextResponse(
            content=turtle,
            media_type="text/turtle",
            headers={"X-Triple-Count": str(triple_count)},
        )
    return OwlExportResponse(
        turtle=turtle,
        mime_type="text/turtle",
        triple_count=triple_count,
        elapsed_ms=elapsed_ms,
    )


__all__ = [
    "MAPPING_IMPORT_MAX_BYTES_ENV",
    "_resolve_max_bytes",
]


# Suppress the static-analysis "unused import" warning on typing
# helpers that show up only in annotations of the module-level
# helpers above (they're imported for documentation, not runtime).
_ = Any
