"""Schema-mapping cache (PRD §6.3.3).

Two-tier persistence per the spec:

* **L1 — In-process LRU** (``_mapping_cache`` module dict, keyed by
  ``db.name``). TTL ``SCHEMA_MAPPING_CACHE_TTL_SECONDS`` (env var,
  default 3600 s — Appendix A.5).
* **L2 — Persistent** (``arango_sparql_schema_cache`` collection in
  the customer's own DB, keyed by ``(db.name, key="mapping")``,
  until invalidated). **Stubbed in v0.x** — :meth:`SchemaCache.put`
  and :meth:`SchemaCache.get` only touch L1. The L2 hooks
  (:meth:`SchemaCache._persist_to_l2`, :meth:`SchemaCache._read_from_l2`)
  are explicit no-ops with the contract documented so the follow-up
  slice can implement them without changing this module's public
  API. PRD §6.3.3 acknowledges L2 as a separate concern.

Drift detection uses Slice 3's bundle-side fingerprints
(:func:`compute_bundle_fingerprint`). When the analyzer is reachable,
the live ``schema_analyzer.fingerprint_physical_shape`` /
``fingerprint_physical_counts`` give a cheaper "should I re-acquire?"
probe; that probe lives in :mod:`arango_sparql.schema.acquire` since
it is part of the acquisition policy. This module owns only the
*storage* contract — the orchestration layer (Slice 5b ``acquire.py``,
Slice 7 routes) decides when to read or write.

Public surface (deliberately small):

* :class:`SchemaCache` — the cache itself.
* :class:`CachedEntry` — what's stored per ``db.name``.
* :data:`DEFAULT_TTL_SECONDS` — pinned default for sentinel tests.
* :data:`L2_COLLECTION_NAME` — the future L2 collection name (kept
  as a constant so route layer can also reference it).

The cache is intentionally *not* a singleton: every consumer that
needs caching gets a :class:`SchemaCache` instance from the FastAPI
app factory (Slice 6). Multiple instances coexist cleanly because
they each own their own L1 dict — required for tests that need
isolated cache state per case.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from arango_sparql.schema.fingerprint import (
    BundleFingerprint,
    FingerprintDrift,
    compute_bundle_fingerprint,
)
from arango_sparql.translate.mapping import MappingBundle

# PRD Appendix A.5 default. Held here as the single source of truth
# so a future env-var lookup mismatch surfaces as a sentinel-test
# failure rather than as silent cache flapping in production.
DEFAULT_TTL_SECONDS: int = 3600

# PRD §6.3.3 row 2 names the L2 collection. Constant exported so
# the (future) cache cleanup route, the metrics layer, and the
# fingerprint exclusion list (cache-collection-self-loop guard;
# see sister project `_shape_fingerprint`) can all reference one
# definition.
L2_COLLECTION_NAME: str = "arango_sparql_schema_cache"

# Env var keyword for the TTL override. Held as a constant so the
# Appendix A.5 row, the unit tests, and the runtime resolution all
# point at the same string.
TTL_ENV_VAR: str = "SCHEMA_MAPPING_CACHE_TTL_SECONDS"


@dataclass(frozen=True)
class CachedEntry:
    """One cache slot's payload.

    Carries the bundle, its fingerprint at acquisition time, and the
    UTC timestamp the entry was stored. ``acquired_at`` drives TTL
    eviction; ``fingerprint`` lets callers compare against a freshly-
    computed fingerprint to detect schema drift without re-reading
    the bundle.
    """

    bundle: MappingBundle
    fingerprint: BundleFingerprint
    acquired_at: datetime

    def age(self, *, now: datetime | None = None) -> timedelta:
        """Return how long ago this entry was acquired. Injectable
        clock for tests; defaults to UTC now.
        """

        when = now if now is not None else datetime.now(UTC)
        return when - self.acquired_at

    def is_expired(self, *, ttl_seconds: int, now: datetime | None = None) -> bool:
        """Return ``True`` iff this entry has aged past *ttl_seconds*.

        TTL=0 means *never expire by age* — the entry is only ever
        evicted by explicit invalidation or by fingerprint drift. This
        matches the L2 "until invalidated" semantics PRD §6.3.3 row 2
        promises and gives operators a clean way to disable the L1
        TTL when they have other invalidation guarantees.
        """

        if ttl_seconds <= 0:
            return False
        return self.age(now=now).total_seconds() >= ttl_seconds


class SchemaCache:
    """Two-tier schema-mapping cache (PRD §6.3.3).

    L1 (in-process) is fully implemented. L2 (persistent ArangoDB
    collection) is a documented stub — its read/write methods exist
    but no-op until the follow-up slice lands.

    Thread safety: the L1 dict is guarded by a per-instance
    ``threading.Lock``. FastAPI route handlers run on a single
    asyncio event loop in the default Uvicorn worker, so contention
    is negligible; the lock is here for the
    multi-thread / sync-thread-pool case where uvicorn workers may
    spawn worker threads for blocking analyzer calls.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int | None = None,
        l2_collection_name: str = L2_COLLECTION_NAME,
    ) -> None:
        self._ttl_seconds = ttl_seconds if ttl_seconds is not None else _resolve_ttl_from_env()
        self._l2_collection_name = l2_collection_name
        self._l1: dict[str, CachedEntry] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def get(
        self,
        db_name: str,
        *,
        now: datetime | None = None,
    ) -> CachedEntry | None:
        """Return the cached entry for *db_name*, or ``None`` on miss
        or expiry.

        Expired entries are evicted on read so a stale slot does not
        linger across the next put. The L2 read is a no-op stub —
        a future slice will hydrate L1 from L2 here.
        """

        with self._lock:
            entry = self._l1.get(db_name)
            if entry is None:
                # Future: hydrate L1 from L2. For now the L2 stub
                # always returns None, so we just return the miss
                # signal directly.
                hydrated = self._read_from_l2(db_name)
                if hydrated is not None:
                    self._l1[db_name] = hydrated
                    entry = hydrated
                else:
                    return None
            if entry.is_expired(ttl_seconds=self._ttl_seconds, now=now):
                self._l1.pop(db_name, None)
                return None
            return entry

    def has_fresh_entry(
        self,
        db_name: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Convenience predicate — ``True`` iff :meth:`get` would
        return non-``None``. Useful for the route-layer "should I
        even probe live fingerprints?" gate.
        """

        return self.get(db_name, now=now) is not None

    def status(self, db_name: str) -> CacheStatus:
        """Return a snapshot of cache state for *db_name* without
        consuming or evicting the entry. Used by ``/schema/status``
        (PRD §6.4) to populate its drift-report payload before any
        re-acquisition decision.
        """

        with self._lock:
            entry = self._l1.get(db_name)
            return CacheStatus(
                db_name=db_name,
                has_entry=entry is not None,
                age_seconds=(entry.age().total_seconds() if entry is not None else None),
                is_expired=(entry.is_expired(ttl_seconds=self._ttl_seconds) if entry is not None else False),
                fingerprint=(entry.fingerprint if entry is not None else None),
                ttl_seconds=self._ttl_seconds,
            )

    # ------------------------------------------------------------------
    # Write paths
    # ------------------------------------------------------------------

    def put(
        self,
        db_name: str,
        bundle: MappingBundle,
        *,
        fingerprint: BundleFingerprint | None = None,
        now: datetime | None = None,
    ) -> CachedEntry:
        """Store *bundle* under *db_name*. Returns the stored
        :class:`CachedEntry` for the caller's convenience.

        *fingerprint* is computed via :func:`compute_bundle_fingerprint`
        when not supplied — callers that already have a fingerprint
        in hand (e.g. from a freshness check) should pass it through
        to avoid redundant SHA-256 work.
        """

        when = now if now is not None else datetime.now(UTC)
        fp = fingerprint if fingerprint is not None else compute_bundle_fingerprint(bundle, now=when)
        entry = CachedEntry(bundle=bundle, fingerprint=fp, acquired_at=when)
        with self._lock:
            self._l1[db_name] = entry
            # Future: persist to L2 here. The stub records nothing.
            self._persist_to_l2(db_name, entry)
        return entry

    def invalidate(self, db_name: str) -> bool:
        """Drop the cached entry for *db_name* from both tiers.

        Returns ``True`` iff an L1 entry was actually removed
        (callers can use this to surface a 404-shaped "no cache
        entry to invalidate" message). The L2 invalidation is best-
        effort — the stub returns silently.
        """

        with self._lock:
            removed = self._l1.pop(db_name, None) is not None
            self._invalidate_l2(db_name)
        return removed

    def clear(self) -> int:
        """Drop *every* cached entry from L1. Returns the count of
        evicted entries. Used by the test suite and by the
        ``ops/warm-cache.py`` script (PRD §15.4) when prepping a
        clean cache state. L2 untouched (stub).
        """

        with self._lock:
            count = len(self._l1)
            self._l1.clear()
        return count

    # ------------------------------------------------------------------
    # Drift comparison
    # ------------------------------------------------------------------

    def drift(
        self,
        db_name: str,
        current_fingerprint: BundleFingerprint,
    ) -> FingerprintDrift | None:
        """Compare *current_fingerprint* against the cached one for
        *db_name*. Returns the drift category, or ``None`` when there
        is no cached entry to compare against.

        Wraps :meth:`BundleFingerprint.drift_from` so callers do not
        need to crack open :class:`CachedEntry` themselves.
        """

        entry = self.get(db_name)
        if entry is None:
            return None
        return current_fingerprint.drift_from(entry.fingerprint)

    # ------------------------------------------------------------------
    # L2 stubs (deliberately no-op until the follow-up slice)
    # ------------------------------------------------------------------

    def _read_from_l2(self, db_name: str) -> CachedEntry | None:
        """Future: read from the persistent ``arango_sparql_schema_cache``
        collection. Currently a documented no-op so the get/put
        contract is identical with or without an L2 implementation.
        Tests can monkeypatch this to simulate an L2 hit.
        """

        return None

    def _persist_to_l2(self, db_name: str, entry: CachedEntry) -> None:
        """Future: serialise *entry* into the persistent collection.
        Stub no-op for v0.x.
        """

        return None

    def _invalidate_l2(self, db_name: str) -> None:
        """Future: delete *db_name*'s row from the persistent
        collection. Stub no-op for v0.x.
        """

        return None

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def ttl_seconds(self) -> int:
        """The effective TTL for L1 entries in this cache instance."""

        return self._ttl_seconds

    @property
    def l2_collection_name(self) -> str:
        """The name of the (future) L2 collection. Exposed so route
        layer can reference it without re-importing the constant.
        """

        return self._l2_collection_name

    def __len__(self) -> int:
        with self._lock:
            return len(self._l1)


@dataclass(frozen=True)
class CacheStatus:
    """Read-only snapshot of cache state for one ``db.name``.

    Returned by :meth:`SchemaCache.status` and consumed by the
    ``/schema/status`` route (PRD §6.4) to populate its drift report.
    """

    db_name: str
    has_entry: bool
    age_seconds: float | None
    is_expired: bool
    fingerprint: BundleFingerprint | None
    ttl_seconds: int


# ---------------------------------------------------------------------------
# Env-var resolution
# ---------------------------------------------------------------------------


def _resolve_ttl_from_env() -> int:
    """Read :data:`TTL_ENV_VAR` from the environment, falling back to
    :data:`DEFAULT_TTL_SECONDS` when unset or unparseable.

    Negative values are clamped to 0 (which means "no TTL eviction").
    Done here rather than at the dataclass level so env-var hijinks
    can never silently produce a negative-TTL cache.
    """

    raw = os.environ.get(TTL_ENV_VAR)
    if raw is None or raw == "":
        return DEFAULT_TTL_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS
    return max(0, value)


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "L2_COLLECTION_NAME",
    "TTL_ENV_VAR",
    "CacheStatus",
    "CachedEntry",
    "SchemaCache",
]
