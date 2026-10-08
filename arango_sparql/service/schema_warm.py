"""Background schema warming — keep the analyzer off the request path.

Schema analysis is expensive: on prod.demo the analyzer's physical scan of the
``IAM`` database takes ~7 minutes, and before this module every cold
``/schema/introspect`` (and every translate that enriches from the schema) ran
that analysis *inline*, so the Workbench sat on "Loading schema…" for minutes.

Ported from ``arango-cypher-py``'s ``arango_cypher/catalog/warm.py`` (commits
2570086 / 4e48943): on a cache miss — or an expired entry, or an explicit
"Refresh schema" — the request path calls :func:`schedule_warm`, which starts
**one** background analysis per database using the live, already-authenticated
session handle, and returns immediately. The endpoint answers ``pending`` (or
serves the stale bundle with ``warming: true``) and the client retries.

Deviations from the cypher module, both deliberate:

* **Keyed per database, not per (database, graph).** This service always
  analyzes the whole database and derives a named-graph scope by filtering the
  cached bundle (``schema/graph_scope.py``), so one warm serves every scope —
  picking a graph never triggers a second multi-minute analysis.
* **Lives in the service layer.** The cache this fills is the route layer's
  ``SchemaCache`` (``routes/schema.py``), so the builder is imported from
  there lazily rather than from a library-level ``get_mapping``.

Concurrency: warms are deduped by an in-flight set guarded by a lock, so a
polling client cannot pile up redundant analyzer passes. The warm reuses the
session's ``StandardDatabase`` handle; sync FastAPI endpoints already share it
across threads. If the session is evicted mid-warm and its client closed, the
warm fails, is logged, and the next request re-triggers it.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("arango_sparql.service")

_inflight: set[str] = set()
# The last warm failure per database, surfaced once by the next request (see
# :func:`take_error`) so a broken analysis is reported instead of leaving the
# client polling ``pending`` forever.
_errors: dict[str, BaseException] = {}
_lock = threading.Lock()


def _start_thread(target: Callable[[], None], name: str) -> None:
    """Run *target* on a daemon thread. Indirected so a test can run warms
    inline (deterministically) while every production warm stays async."""
    threading.Thread(target=target, name=name, daemon=True).start()


def is_warming(db_name: str) -> bool:
    """Whether a background warm is in flight for *db_name*."""
    with _lock:
        return db_name in _inflight


def take_error(db_name: str) -> BaseException | None:
    """Pop and return the last background-warm failure for *db_name*.

    One-shot: the request that receives it reports it; the next request
    retries the analysis (a transient failure must not stick).
    """
    with _lock:
        return _errors.pop(db_name, None)


def schedule_warm(db: Any, *, strategy: str = "auto") -> bool:
    """Start a one-shot background (re)analysis of *db* if none is running.

    Returns ``True`` when a new warm started, ``False`` when one for the same
    database was already in flight (deduped) or the database name could not
    be resolved. Never raises — warming is best-effort; a failure is logged and
    the next request schedules it again.
    """
    try:
        db_name = db.name
    except Exception:  # noqa: BLE001 — a handle without a resolvable name is unusable
        logger.warning("schedule_warm: could not resolve the database name; skipping")
        return False
    if not db_name:
        return False

    with _lock:
        if db_name in _inflight:
            return False
        _inflight.add(db_name)
        _errors.pop(db_name, None)

    def _run() -> None:
        # Lazy: the builder lives in the route module, which imports this one.
        from .routes.schema import _build_and_cache_full_bundle

        try:
            logger.info("schema warm starting (background): db=%s strategy=%s", db_name, strategy)
            _build_and_cache_full_bundle(db, strategy=strategy)
            logger.info("schema warm complete: db=%s", db_name)
        except Exception as exc:  # noqa: BLE001 — recorded and surfaced by the next request
            logger.warning("schema warm failed: db=%s", db_name, exc_info=True)
            with _lock:
                _errors[db_name] = exc
        finally:
            with _lock:
                _inflight.discard(db_name)

    _start_thread(_run, f"schema-warm-{db_name}")
    return True


def _reset_for_tests() -> None:
    """Forget every in-flight marker (test isolation only)."""
    with _lock:
        _inflight.clear()
        _errors.clear()
