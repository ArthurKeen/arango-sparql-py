"""The persistent schema cache on a real ArangoDB (PRD §6.3.3).

The unit tiers (``tests/schema/test_cache.py``, ``test_persistent_cache.py``)
use an in-memory stand-in for python-arango. This tier proves what that
stand-in cannot: a real analyzed bundle (with its OWL) round-trips through a
real document, the cache's own collection stays out of the next analysis and
the fingerprints, and a database user with read-only rights is refused the
write by the server yet keeps working — and can read what an admin stored.

Run with::

    RUN_INTEGRATION=1 .venv/bin/pytest tests/integration/test_persistent_schema_cache_live.py
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from arango_sparql.schema.acquire import (
    acquire_mapping_bundle,
    analyzer_available,
    db_counts_fingerprint,
    db_shape_fingerprint,
)
from arango_sparql.schema.cache import L2_COLLECTION_NAME, SchemaCache
from arango_sparql.translate.mapping import mapping_to_wire_dict
from tests.integration.conftest import (
    DEFAULT_ARANGO_PASSWORD,
    DEFAULT_ARANGO_URL,
    DEFAULT_ARANGO_USER,
    arangodb_reachable,
    integration_enabled,
    try_boot_arangodb_via_compose,
)
from tests.integration.schema_live_seeds import SEEDS

pytestmark = pytest.mark.integration

_DB = "sparql_l2_cache_live"
_RO_USER = "sparql_l2_cache_reader"
_RO_PASSWORD = "reader-pw"  # local test container only


@pytest.fixture(scope="module")
def client() -> Iterator[Any]:
    if not integration_enabled():
        pytest.skip("set RUN_INTEGRATION=1 to enable integration tests")
    if not arangodb_reachable() and not try_boot_arangodb_via_compose():
        pytest.skip(f"ArangoDB at {DEFAULT_ARANGO_URL} is unreachable and could not be booted")
    from arango import ArangoClient

    c = ArangoClient(hosts=DEFAULT_ARANGO_URL.rstrip("/"))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def db(client: Any) -> Iterator[Any]:
    """A fresh database seeded with the PG model, plus a read-only user on it."""
    sys_db = client.db("_system", username=DEFAULT_ARANGO_USER, password=DEFAULT_ARANGO_PASSWORD)
    if sys_db.has_database(_DB):
        sys_db.delete_database(_DB)
    sys_db.create_database(_DB)
    if sys_db.has_user(_RO_USER):
        sys_db.delete_user(_RO_USER)
    sys_db.create_user(_RO_USER, password=_RO_PASSWORD)
    sys_db.update_permission(_RO_USER, "ro", _DB)
    handle = client.db(_DB, username=DEFAULT_ARANGO_USER, password=DEFAULT_ARANGO_PASSWORD)
    SEEDS["pg"](handle)
    try:
        yield handle
    finally:
        sys_db.delete_user(_RO_USER, ignore_missing=True)
        sys_db.delete_database(_DB, ignore_missing=True)


def _reader(client: Any) -> Any:
    return client.db(_DB, username=_RO_USER, password=_RO_PASSWORD)


def _strategy() -> str:
    return "auto" if analyzer_available() else "heuristic"


def test_a_real_analysis_round_trips_through_the_stored_document(db: Any) -> None:
    bundle = acquire_mapping_bundle(db, strategy=_strategy(), include_owl=True)
    shape, counts = db_shape_fingerprint(db), db_counts_fingerprint(db)
    t0 = datetime.now(UTC).replace(microsecond=0)

    SchemaCache().put(_DB, bundle, db=db, db_shape=shape, db_counts=counts, now=t0)
    restored = SchemaCache().get(_DB, db=db, now=t0 + timedelta(seconds=5))

    assert restored is not None
    # Equal by value. Not byte-for-byte: ArangoDB hands an integral double
    # back as an integer (statistics' ``avg_out_degree: 1.0`` returns as
    # ``1``), which Python compares equal.
    assert mapping_to_wire_dict(restored.bundle) == mapping_to_wire_dict(bundle)
    assert (restored.db_shape, restored.db_counts) == (shape, counts)
    assert restored.acquired_at == t0
    assert restored.is_from_current_code()


def test_the_cache_it_writes_never_reaches_the_next_analysis(db: Any) -> None:
    first = acquire_mapping_bundle(db, strategy=_strategy(), include_owl=True)
    shape, counts = db_shape_fingerprint(db), db_counts_fingerprint(db)

    SchemaCache().put(_DB, first, db=db, db_shape=shape, db_counts=counts)
    assert db.has_collection(L2_COLLECTION_NAME)

    second = acquire_mapping_bundle(db, strategy=_strategy(), include_owl=True)
    assert {s["collectionName"] for s in second.physical_mapping["entities"].values()} == {
        s["collectionName"] for s in first.physical_mapping["entities"].values()
    }
    assert (db_shape_fingerprint(db), db_counts_fingerprint(db)) == (shape, counts)


def test_a_read_only_user_is_refused_the_write_but_keeps_working(client: Any, db: Any) -> None:
    reader = _reader(client)
    bundle = acquire_mapping_bundle(reader, strategy="heuristic")

    cache = SchemaCache()
    entry = cache.put(_DB, bundle, db=reader)  # the server refuses the collection; no exception

    assert cache.get(_DB) is entry
    assert not db.has_collection(L2_COLLECTION_NAME)


def test_a_read_only_user_reads_what_an_admin_stored(client: Any, db: Any) -> None:
    bundle = acquire_mapping_bundle(db, strategy="heuristic")
    SchemaCache().put(_DB, bundle, db=db)

    restored = SchemaCache().get(_DB, db=_reader(client))

    assert restored is not None
    assert mapping_to_wire_dict(restored.bundle) == mapping_to_wire_dict(bundle)


def test_invalidate_deletes_the_stored_document(db: Any) -> None:
    cache = SchemaCache()
    cache.put(_DB, acquire_mapping_bundle(db, strategy="heuristic"), db=db)

    assert cache.invalidate_persistent(db) is True
    assert db.collection(L2_COLLECTION_NAME).get("mapping") is None
    assert SchemaCache().get(_DB, db=db) is None
