"""W3C SPARQL 1.1 Protocol — endpoint helpers (PRD §5.2).

This package implements the spec-compliant ``/sparql`` HTTP surface
(GET + POST) as four small, independently-testable helpers:

* :mod:`.negotiate` — :rfc:`9110` §12.5.1 quality-value parsing for
  the ``Accept`` header, plus the per-query-form priority list that
  determines tie-breaks and the default media type when ``*/*`` is
  offered.
* :mod:`.update_detect` — strips comments and the SPARQL prologue
  (``BASE`` / ``PREFIX``) and inspects the leading keyword to flag
  SPARQL Update operations (``INSERT`` / ``DELETE`` / ``LOAD`` /
  ``CLEAR`` / ``CREATE`` / ``DROP`` / ``COPY`` / ``MOVE`` / ``ADD``)
  that must surface as ``405 Method Not Allowed`` with the typed
  code ``E_UPDATE_UNSUPPORTED``.
* :mod:`.results` — W3C SPARQL Results serialisers in the four
  formats the priority list mandates (JSON, XML, CSV, TSV) plus the
  ASK ``boolean`` variants of each.
* :mod:`.service_description` — builds the Turtle Service
  Description body returned by ``GET /sparql`` (no query) — the
  ``sd:availableGraphs`` set is sourced from the active
  :class:`MappingBundle`.

The :mod:`arango_sparql.service.routes.protocol` route module wires
these helpers into a single FastAPI handler.
"""
