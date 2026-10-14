"""``GET /examples`` — verified examples mined from the connected database.

Ported from arango-cypher-py's ``service/routes/examples.py`` (PRD §7.7).
Written offline by ``arango-sparql-py mine-examples`` into the database's
``arango_sparql_examples`` collection; served here without an LLM, so a
deployment with no API key still shows them. Scoped to the session's database
and, when one is bound, its named graph.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from arango.exceptions import ArangoError
from fastapi import Depends, HTTPException, Query

from ...query_mining.store import MAX_LIST, collection_name, list_examples
from ..app import app
from ..observability import log_endpoint_timing
from ..security import _get_session, _sanitize_error, _Session

logger = logging.getLogger("arango_sparql.service.routes.examples")


@app.get("/examples")
def mined_examples(
    limit: int = Query(50, ge=1, le=MAX_LIST),
    session: _Session = Depends(_get_session),
) -> dict[str, Any]:
    """The session database's verified examples: question, SPARQL, the AQL
    that was verified, and where the example came from."""
    t0 = time.perf_counter()
    graph = getattr(session, "graph_name", None)
    try:
        examples = list_examples(session.db, graph=graph, limit=limit)
    except ArangoError as exc:
        logger.warning("listing mined examples from %s failed: %s", collection_name(), exc)
        log_endpoint_timing("/examples", round((time.perf_counter() - t0) * 1000, 1), status="error")
        raise HTTPException(
            status_code=502,
            detail={
                "error": _sanitize_error(f"Could not read {collection_name()}: {exc}"),
                "code": "E_EXAMPLES_UNAVAILABLE",
            },
        ) from exc
    log_endpoint_timing(
        "/examples",
        round((time.perf_counter() - t0) * 1000, 1),
        examples=len(examples),
        graph=graph or "",
    )
    return {"examples": examples}
