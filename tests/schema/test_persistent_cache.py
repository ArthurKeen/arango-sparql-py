"""Unit tests for :mod:`arango_sparql.schema.persistent_cache` (PRD §6.3.3).

The document store behind the L2 tier, against a python-arango-shaped
in-memory database (``tests/helpers/fake_arango_store.py``). Every failure is
a miss or a ``False`` — the cache is a performance hint, never a reason for a
request to fail.
"""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from arango_sparql.schema import persistent_cache
from arango_sparql.schema.cache import L2_COLLECTION_NAME
from arango_sparql.schema.persistent_cache import (
    CACHE_SCHEMA_VERSION,
    ArangoSchemaCache,
    current_producer,
)
from arango_sparql.translate.mapping import MappingBundle, MappingSource, mapping_to_wire_dict
from tests.helpers.fake_arango_store import FakeDatabase

_T0 = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def _bundle() -> MappingBundle:
    return MappingBundle(
        conceptual_schema={"entities": [{"name": "Person", "labels": ["Person"], "properties": []}]},
        physical_mapping={
            "entities": {"Person": {"style": "COLLECTION", "collectionName": "persons"}},
            "relationships": {},
        },
        metadata={"statistics": {"collections": {"persons": {"count": 3}}}, "warnings": []},
        owl_turtle="@prefix : <http://ex.org/> .\n:Person a <http://www.w3.org/2002/07/owl#Class> .\n",
        source=MappingSource(kind="analyzer", notes="unit"),
    )


def _store() -> ArangoSchemaCache:
    return ArangoSchemaCache(collection_name=L2_COLLECTION_NAME)


def _set(store: ArangoSchemaCache, db: FakeDatabase, *, when: datetime = _T0) -> bool:
    return store.set(
        db,
        bundle=_bundle(),
        shape_fingerprint="shape-1",
        full_fingerprint="counts-1",
        producer=current_producer(),
        updated_at=when,
    )


def test_a_stored_bundle_reads_back_whole() -> None:
    db, store = FakeDatabase(), _store()

    assert _set(store, db) is True
    entry = store.get(db)

    assert entry is not None
    assert mapping_to_wire_dict(entry.bundle) == mapping_to_wire_dict(_bundle())  # OWL + source included
    assert (entry.shape_fingerprint, entry.full_fingerprint) == ("shape-1", "counts-1")
    assert entry.producer == current_producer()
    assert entry.updated_at == _T0


def test_the_collection_is_created_on_first_write_and_reused() -> None:
    db, store = FakeDatabase(), _store()
    assert not db.has_collection(L2_COLLECTION_NAME)

    _set(store, db)
    _set(store, db, when=_T0 + timedelta(hours=1))

    docs = db.collection(L2_COLLECTION_NAME).docs
    assert list(docs) == ["mapping"]
    assert docs["mapping"]["created_at"] == _T0.isoformat()  # first write's stamp survives
    assert docs["mapping"]["updated_at"] == (_T0 + timedelta(hours=1)).isoformat()
    assert docs["mapping"]["schema_version"] == CACHE_SCHEMA_VERSION


def test_touch_restamps_without_rewriting_the_bundle() -> None:
    db, store = FakeDatabase(), _store()
    _set(store, db)
    col = db.collection(L2_COLLECTION_NAME)
    bundle_before = col.docs["mapping"]["bundle"]

    assert store.touch(db, full_fingerprint="counts-2", updated_at=_T0 + timedelta(hours=2)) is True

    doc = col.docs["mapping"]
    assert doc["bundle"] == bundle_before
    assert doc["full_fingerprint"] == "counts-2" and doc["shape_fingerprint"] == "shape-1"
    assert store.get(db).updated_at == _T0 + timedelta(hours=2)  # type: ignore[union-attr]


def test_touch_without_a_stored_copy_reports_false() -> None:
    assert _store().touch(FakeDatabase(), full_fingerprint=None, updated_at=_T0) is False


def test_null_fingerprints_round_trip() -> None:
    """The heuristic tier (no analyzer) has no live fingerprints."""
    db, store = FakeDatabase(), _store()
    store.set(
        db, bundle=_bundle(), shape_fingerprint=None, full_fingerprint=None, producer="p", updated_at=_T0
    )
    entry = store.get(db)
    assert entry is not None and entry.shape_fingerprint is None and entry.full_fingerprint is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", CACHE_SCHEMA_VERSION + 1),
        ("updated_at", "not a timestamp"),
        ("updated_at", None),
        ("bundle", "not a dict"),
        ("bundle", {"physicalMapping": ["not", "a", "dict"]}),
    ],
    ids=["other-format", "bad-timestamp", "no-timestamp", "bundle-not-a-dict", "unreadable-bundle"],
)
def test_a_document_that_cannot_be_trusted_is_a_miss(field: str, value: object) -> None:
    db, store = FakeDatabase(), _store()
    _set(store, db)
    db.collection(L2_COLLECTION_NAME).docs["mapping"][field] = value

    assert store.get(db) is None


def test_failures_are_misses_and_false_never_exceptions() -> None:
    store = _store()
    for db in (FakeDatabase(read_only=True), FakeDatabase(unreachable=True)):
        assert _set(store, db) is False
        assert store.get(db) is None
        assert store.touch(db, full_fingerprint=None, updated_at=_T0) is False
        assert store.invalidate(db) is False


def test_a_handle_without_the_document_api_is_a_miss() -> None:
    """Route tests pass duck-typed handles that only list collections."""

    class _ListOnly:
        name = "x"

        def collections(self) -> list[dict[str, str]]:
            return []

    store = _store()
    assert store.get(_ListOnly()) is None
    assert _set(store, _ListOnly()) is False  # type: ignore[arg-type]


def test_invalidate_deletes_once() -> None:
    db, store = FakeDatabase(), _store()
    _set(store, db)
    assert store.invalidate(db) is True
    assert store.invalidate(db) is False
    assert store.get(db) is None


# ---------------------------------------------------------------------------
# current_producer — what built a bundle
# ---------------------------------------------------------------------------


def test_the_producer_names_the_code_digest_and_the_analyzer() -> None:
    producer = current_producer()
    assert re.fullmatch(r"[0-9a-f]{16}/analyzer=\S+", producer)
    assert current_producer() == producer  # stable for the process


def test_a_change_to_the_acquisition_code_changes_the_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = tmp_path / "producer_probe_mod.py"
    module.write_text("RULE = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(persistent_cache, "_PRODUCER_MODULES", ("producer_probe_mod",))
    compute = current_producer.__wrapped__

    before = compute()
    module.write_text("RULE = 2\n", encoding="utf-8")
    after = compute()

    assert before != after
    sys.modules.pop("producer_probe_mod", None)
