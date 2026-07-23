"""Unit tests for :mod:`arango_sparql.schema.cache` (PRD §6.3.3).

Covers:

* Basic put/get/invalidate round-trip semantics.
* TTL expiry via injectable clock (no ``time.sleep``).
* TTL=0 = "never expire by age" sentinel.
* Env-var TTL override (``SCHEMA_MAPPING_CACHE_TTL_SECONDS``).
* Drift detection through :class:`FingerprintDrift`.
* Status snapshot does not consume the entry.
* L2 stub no-op is honoured (no exceptions, no state changes).
* Multiple instances do not share L1 state.

Tests do not touch a live ArangoDB — the cache layer is purely in-
process in v0.x. The L2 persistent layer (PRD §6.3.3 row 2) is a
documented stub and tested as such; integration tests for the L2
layer will land with the slice that implements it.
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from arango_sparql.schema.cache import (
    DEFAULT_TTL_SECONDS,
    L2_COLLECTION_NAME,
    TTL_ENV_VAR,
    CachedEntry,
    CacheStatus,
    SchemaCache,
)
from arango_sparql.schema.fingerprint import (
    FingerprintDrift,
    bundle_counts_fingerprint,
    bundle_shape_fingerprint,
    compute_bundle_fingerprint,
)
from arango_sparql.translate.mapping import MappingBundle, MappingSource

# ---------------------------------------------------------------------------
# Bundle fixture helpers
# ---------------------------------------------------------------------------


def _bundle(*, with_counts: int = 1, label: str = "Person") -> MappingBundle:
    """Build a minimal MappingBundle suitable for cache tests.

    *with_counts* lets callers vary the counts payload independently
    of the shape so we can exercise STATS_ONLY drift without
    touching the conceptual schema.
    """

    return MappingBundle(
        conceptual_schema={
            "entities": [{"name": label, "labels": [label], "properties": []}],
            "relationships": [],
        },
        physical_mapping={
            "entities": {label: {"style": "COLLECTION", "collectionName": label.lower()}},
            "relationships": {},
        },
        metadata={
            "source": "test_cache_fixture",
            "statistics": {
                "collections": {label.lower(): {"count": with_counts}},
            },
        },
        source=MappingSource(kind="heuristic", notes="cache test"),
    )


# ---------------------------------------------------------------------------
# Defaults & constants
# ---------------------------------------------------------------------------


def test_default_ttl_matches_prd_appendix_a5() -> None:
    """PRD Appendix A.5 pins L1 TTL at 3600 seconds."""

    assert DEFAULT_TTL_SECONDS == 3600


def test_l2_collection_name_matches_prd_section_633() -> None:
    """PRD §6.3.3 row 2 pins the L2 collection name."""

    assert L2_COLLECTION_NAME == "arango_sparql_schema_cache"


def test_ttl_env_var_constant_is_documented_name() -> None:
    """Catch silent renames of the env var the docs reference."""

    assert TTL_ENV_VAR == "SCHEMA_MAPPING_CACHE_TTL_SECONDS"


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_cache_default_construction_uses_default_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env var, no explicit TTL → DEFAULT_TTL_SECONDS."""

    monkeypatch.delenv(TTL_ENV_VAR, raising=False)
    cache = SchemaCache()
    assert cache.ttl_seconds == DEFAULT_TTL_SECONDS
    assert cache.l2_collection_name == L2_COLLECTION_NAME
    assert len(cache) == 0


def test_cache_explicit_ttl_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ttl_seconds always wins over the env var."""

    monkeypatch.setenv(TTL_ENV_VAR, "9999")
    cache = SchemaCache(ttl_seconds=42)
    assert cache.ttl_seconds == 42


def test_cache_env_ttl_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env var resolves on construction."""

    monkeypatch.setenv(TTL_ENV_VAR, "120")
    cache = SchemaCache()
    assert cache.ttl_seconds == 120


def test_cache_env_ttl_garbage_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unparseable env var → default. No exception bubbles."""

    monkeypatch.setenv(TTL_ENV_VAR, "not-a-number")
    cache = SchemaCache()
    assert cache.ttl_seconds == DEFAULT_TTL_SECONDS


def test_cache_negative_env_ttl_clamps_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative TTL is clamped — guard against silent never-evict."""

    monkeypatch.setenv(TTL_ENV_VAR, "-30")
    cache = SchemaCache()
    assert cache.ttl_seconds == 0


def test_cache_empty_env_ttl_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty string env var falls back to default."""

    monkeypatch.setenv(TTL_ENV_VAR, "")
    cache = SchemaCache()
    assert cache.ttl_seconds == DEFAULT_TTL_SECONDS


def test_cache_explicit_l2_collection_name_is_kept() -> None:
    """Operators can override the L2 collection name (e.g. for
    multi-tenant deployments using one ArangoDB cluster).
    """

    cache = SchemaCache(l2_collection_name="custom_cache")
    assert cache.l2_collection_name == "custom_cache"


# ---------------------------------------------------------------------------
# put / get round-trip
# ---------------------------------------------------------------------------


def test_get_on_empty_cache_returns_none() -> None:
    cache = SchemaCache()
    assert cache.get("any_db") is None
    assert not cache.has_fresh_entry("any_db")


def test_put_then_get_returns_same_bundle() -> None:
    cache = SchemaCache()
    bundle = _bundle()
    entry = cache.put("db1", bundle)
    assert isinstance(entry, CachedEntry)
    fetched = cache.get("db1")
    assert fetched is not None
    assert fetched.bundle is bundle
    assert fetched.fingerprint == entry.fingerprint
    assert fetched.acquired_at == entry.acquired_at


def test_put_returns_freshly_computed_fingerprint() -> None:
    """When no fingerprint is supplied, put computes one — and the
    returned entry's fingerprint matches a fresh compute over the
    same bundle.
    """

    cache = SchemaCache()
    bundle = _bundle()
    entry = cache.put("db1", bundle)
    expected_shape = bundle_shape_fingerprint(bundle)
    expected_counts = bundle_counts_fingerprint(bundle)
    assert entry.fingerprint.shape == expected_shape
    assert entry.fingerprint.counts == expected_counts


def test_put_accepts_supplied_fingerprint_without_recomputation() -> None:
    """A pre-computed fingerprint is accepted verbatim."""

    cache = SchemaCache()
    bundle = _bundle()
    fp = compute_bundle_fingerprint(bundle, now=datetime(2026, 1, 1, tzinfo=UTC))
    entry = cache.put("db1", bundle, fingerprint=fp)
    assert entry.fingerprint is fp


def test_put_overwrites_previous_entry() -> None:
    cache = SchemaCache()
    cache.put("db1", _bundle(label="A"))
    cache.put("db1", _bundle(label="B"))
    entry = cache.get("db1")
    assert entry is not None
    assert entry.bundle.conceptual_schema["entities"][0]["name"] == "B"


def test_put_with_explicit_now_stamps_acquired_at() -> None:
    """An injectable clock keeps tests deterministic."""

    cache = SchemaCache()
    when = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    entry = cache.put("db1", _bundle(), now=when)
    assert entry.acquired_at == when


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------


def test_entry_age_is_computed_against_injectable_clock() -> None:
    when = datetime(2025, 1, 1, tzinfo=UTC)
    entry = CachedEntry(
        bundle=_bundle(),
        fingerprint=compute_bundle_fingerprint(_bundle(), now=when),
        acquired_at=when,
    )
    later = when + timedelta(seconds=120)
    assert entry.age(now=later) == timedelta(seconds=120)


def test_entry_is_expired_after_ttl() -> None:
    when = datetime(2025, 1, 1, tzinfo=UTC)
    entry = CachedEntry(
        bundle=_bundle(),
        fingerprint=compute_bundle_fingerprint(_bundle(), now=when),
        acquired_at=when,
    )
    assert not entry.is_expired(ttl_seconds=60, now=when)
    assert not entry.is_expired(ttl_seconds=60, now=when + timedelta(seconds=59))
    assert entry.is_expired(ttl_seconds=60, now=when + timedelta(seconds=60))
    assert entry.is_expired(ttl_seconds=60, now=when + timedelta(seconds=600))


def test_entry_with_ttl_zero_never_expires_by_age() -> None:
    """TTL=0 sentinel: only invalidation drops the entry."""

    when = datetime(2020, 1, 1, tzinfo=UTC)
    entry = CachedEntry(
        bundle=_bundle(),
        fingerprint=compute_bundle_fingerprint(_bundle(), now=when),
        acquired_at=when,
    )
    far_future = datetime(2099, 1, 1, tzinfo=UTC)
    assert not entry.is_expired(ttl_seconds=0, now=far_future)


def test_get_evicts_expired_entry_and_returns_none() -> None:
    cache = SchemaCache(ttl_seconds=60)
    when = datetime(2025, 1, 1, tzinfo=UTC)
    cache.put("db1", _bundle(), now=when)
    assert cache.get("db1", now=when) is not None
    expired_at = when + timedelta(seconds=300)
    assert cache.get("db1", now=expired_at) is None
    # Probing again should remain a miss — eviction was sticky.
    assert cache.get("db1", now=expired_at) is None


def test_has_fresh_entry_mirrors_get() -> None:
    cache = SchemaCache(ttl_seconds=60)
    when = datetime(2025, 1, 1, tzinfo=UTC)
    cache.put("db1", _bundle(), now=when)
    assert cache.has_fresh_entry("db1", now=when)
    assert not cache.has_fresh_entry("db1", now=when + timedelta(seconds=120))


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


def test_invalidate_drops_existing_entry_and_returns_true() -> None:
    cache = SchemaCache()
    cache.put("db1", _bundle())
    assert cache.invalidate("db1") is True
    assert cache.get("db1") is None


def test_invalidate_returns_false_for_unknown_db() -> None:
    cache = SchemaCache()
    assert cache.invalidate("unknown") is False


def test_clear_drops_every_entry_and_returns_count() -> None:
    cache = SchemaCache()
    cache.put("db1", _bundle())
    cache.put("db2", _bundle())
    cache.put("db3", _bundle())
    removed = cache.clear()
    assert removed == 3
    assert len(cache) == 0


# ---------------------------------------------------------------------------
# Status snapshot
# ---------------------------------------------------------------------------


def test_status_for_unknown_db_reports_no_entry() -> None:
    cache = SchemaCache()
    status = cache.status("missing")
    assert isinstance(status, CacheStatus)
    assert status.db_name == "missing"
    assert status.has_entry is False
    assert status.age_seconds is None
    assert status.fingerprint is None
    assert status.is_expired is False
    assert status.ttl_seconds == cache.ttl_seconds


def test_status_for_known_db_reports_entry_metadata() -> None:
    cache = SchemaCache(ttl_seconds=60)
    bundle = _bundle()
    when = datetime(2025, 1, 1, tzinfo=UTC)
    entry = cache.put("db1", bundle, now=when)
    status = cache.status("db1")
    assert status.has_entry is True
    assert status.fingerprint == entry.fingerprint
    assert status.ttl_seconds == 60
    assert status.age_seconds is not None
    assert status.age_seconds >= 0


def test_status_does_not_evict_expired_entry() -> None:
    """Status is a peek — should not mutate state. PRD §6.4 needs
    this so /schema/status can report ``stats_only`` without
    triggering its own re-acquire.
    """

    cache = SchemaCache(ttl_seconds=60)
    when = datetime(2025, 1, 1, tzinfo=UTC)
    cache.put("db1", _bundle(), now=when)
    # The status snapshot uses default `now` (real wall clock), so
    # we cannot use it to assert expired-status without time travel.
    # Instead we verify it does not evict by re-checking len() after
    # a status call.
    cache.status("db1")
    assert len(cache) == 1


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------


def test_drift_unchanged_when_fingerprints_match() -> None:
    cache = SchemaCache()
    bundle = _bundle()
    cache.put("db1", bundle)
    fp = compute_bundle_fingerprint(bundle)
    assert cache.drift("db1", fp) is FingerprintDrift.UNCHANGED


def test_drift_stats_only_when_counts_change() -> None:
    cache = SchemaCache()
    cache.put("db1", _bundle(with_counts=1))
    fp_after = compute_bundle_fingerprint(_bundle(with_counts=99))
    assert cache.drift("db1", fp_after) is FingerprintDrift.STATS_ONLY


def test_drift_shape_changed_when_entities_differ() -> None:
    cache = SchemaCache()
    cache.put("db1", _bundle(label="A"))
    fp_after = compute_bundle_fingerprint(_bundle(label="Different"))
    assert cache.drift("db1", fp_after) is FingerprintDrift.SHAPE_CHANGED


def test_drift_returns_none_for_unknown_db() -> None:
    cache = SchemaCache()
    fp = compute_bundle_fingerprint(_bundle())
    assert cache.drift("missing", fp) is None


# ---------------------------------------------------------------------------
# Multi-instance isolation
# ---------------------------------------------------------------------------


def test_separate_cache_instances_have_separate_l1_state() -> None:
    """Critical for tests: a leak across instances would let earlier
    tests bleed cache state into later ones.
    """

    cache_a = SchemaCache()
    cache_b = SchemaCache()
    cache_a.put("db1", _bundle())
    assert cache_a.get("db1") is not None
    assert cache_b.get("db1") is None


# ---------------------------------------------------------------------------
# L2 stub semantics
# ---------------------------------------------------------------------------


def test_l2_read_stub_returns_none() -> None:
    """L2 stub: empty cache + L2 stub returns None → get returns
    None without raising. Verifies the documented no-op.
    """

    cache = SchemaCache()
    # Reach into the protected hook intentionally — its no-op-ness
    # is part of the documented contract for v0.x.
    assert cache._read_from_l2("any") is None


def test_l2_write_stub_does_not_raise() -> None:
    cache = SchemaCache()
    bundle = _bundle()
    fp = compute_bundle_fingerprint(bundle)
    entry = CachedEntry(bundle=bundle, fingerprint=fp, acquired_at=datetime.now(UTC))
    # Should never raise — the stub is a documented no-op.
    cache._persist_to_l2("any", entry)
    cache._invalidate_l2("any")


def test_l2_hydration_path_when_subclass_overrides_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a subclass (or future implementation) overrides
    :meth:`_read_from_l2`, the L1 should hydrate from it on miss
    and serve subsequent reads from L1 directly.
    """

    cache = SchemaCache()
    bundle = _bundle()
    fp = compute_bundle_fingerprint(bundle)
    entry = CachedEntry(bundle=bundle, fingerprint=fp, acquired_at=datetime.now(UTC))
    calls: list[str] = []

    def fake_read(db_name: str) -> CachedEntry | None:
        calls.append(db_name)
        return entry if db_name == "db1" else None

    monkeypatch.setattr(cache, "_read_from_l2", fake_read)
    fetched = cache.get("db1")
    assert fetched is entry
    # Second call should hit L1 directly — L2 is not consulted again.
    cache.get("db1")
    assert calls == ["db1"]


# ---------------------------------------------------------------------------
# Thread safety smoke test
# ---------------------------------------------------------------------------


def test_concurrent_put_and_get_does_not_corrupt_state() -> None:
    """Coarse stress: 8 threads × 200 puts each. We do not assert
    ordering, only that no exception escapes and the final state
    is internally consistent (every key has either a real entry
    or no entry).
    """

    cache = SchemaCache()
    errors: list[BaseException] = []

    def hammer(thread_id: int) -> None:
        try:
            for i in range(200):
                key = f"db{thread_id % 4}"
                cache.put(key, _bundle(label=f"L{i}"))
                cache.get(key)
                if i % 50 == 0:
                    cache.invalidate(key)
        except BaseException as exc:  # pragma: no cover — failure path
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"thread errors: {errors!r}"
    # Every remaining key should resolve to a real entry, not garbage.
    for key in ("db0", "db1", "db2", "db3"):
        entry = cache.get(key)
        if entry is not None:
            assert isinstance(entry.bundle, MappingBundle)


# ---------------------------------------------------------------------------
# Env var leakage between tests
# ---------------------------------------------------------------------------


def test_env_var_state_is_isolated_between_constructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: setting TTL_ENV_VAR before constructing instance A,
    then unsetting it before constructing instance B, gives each
    instance its own resolved TTL.
    """

    monkeypatch.setenv(TTL_ENV_VAR, "777")
    cache_a = SchemaCache()
    monkeypatch.delenv(TTL_ENV_VAR)
    cache_b = SchemaCache()
    assert cache_a.ttl_seconds == 777
    assert cache_b.ttl_seconds == DEFAULT_TTL_SECONDS


def _absent_env(name: str) -> Any:
    """Helper to confirm the env var is genuinely absent at module
    import time. Used by the next test to catch a CI worker that
    leaks ``SCHEMA_MAPPING_CACHE_TTL_SECONDS=...`` into our process.
    """

    return os.environ.get(name)


def test_module_import_does_not_persist_env_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TTL_ENV_VAR, raising=False)
    assert _absent_env(TTL_ENV_VAR) is None
    cache = SchemaCache()
    assert cache.ttl_seconds == DEFAULT_TTL_SECONDS
