"""In-memory stand-in for the python-arango document API the schema cache uses.

Mock fidelity (AGENTS.md): every method mirrors the real
``arango.database.StandardDatabase`` / ``arango.collection.StandardCollection``
signature it stands in for (python-arango 8.x), so a call shape the real driver
would reject fails here too. Only the behaviour the persistent schema cache
(``arango_sparql/schema/persistent_cache.py``) relies on is modelled; anything
else raises ``AttributeError`` like an unknown attribute would.

Failure injection: ``FakeDatabase(read_only=True)`` refuses collection
creation and writes (a read-only user); ``unreachable=True`` makes every call
raise (a dropped connection).
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any


class FakeArangoError(Exception):
    """Stands in for python-arango's ``ArangoServerError`` family."""


class FakeCollection:
    def __init__(self, name: str, database: FakeDatabase, *, missing: bool = False) -> None:
        self.name = name
        self._db = database
        self._missing = missing
        self.docs: dict[str, dict[str, Any]] = {}
        self.writes = 0

    def _check_exists(self) -> None:
        self._db._check_reachable()
        if self._missing:
            raise FakeArangoError(f"collection or view not found: {self.name}")

    @staticmethod
    def _key(document: str | dict[str, Any]) -> str:
        return document["_key"] if isinstance(document, dict) else document

    def get(
        self,
        document: str | dict[str, Any],
        rev: str | None = None,
        check_rev: bool = True,
        allow_dirty_read: bool = False,
    ) -> dict[str, Any] | None:
        self._check_exists()
        doc = self.docs.get(self._key(document))
        return copy.deepcopy(doc) if doc is not None else None

    def insert(
        self,
        document: dict[str, Any],
        return_new: bool = False,
        sync: bool | None = None,
        silent: bool = False,
        overwrite: bool = False,
        return_old: bool = False,
        overwrite_mode: str | None = None,
        keep_none: bool | None = None,
        merge: bool | None = None,
        refill_index_caches: bool | None = None,
        version_attribute: str | None = None,
    ) -> bool | dict[str, Any]:
        self._check_exists()
        self._db._check_writable()
        key = document["_key"]
        if key in self.docs and not overwrite:
            raise FakeArangoError(f"unique constraint violated: {self.name}/{key}")
        self.docs[key] = copy.deepcopy(document)
        self.writes += 1
        return True if silent else {"_key": key}

    def update(
        self,
        document: dict[str, Any],
        check_rev: bool = True,
        merge: bool = True,
        keep_none: bool = True,
        return_new: bool = False,
        return_old: bool = False,
        sync: bool | None = None,
        silent: bool = False,
        refill_index_caches: bool | None = None,
        version_attribute: str | None = None,
    ) -> bool | dict[str, Any]:
        self._check_exists()
        self._db._check_writable()
        key = document["_key"]
        if key not in self.docs:
            raise FakeArangoError(f"document not found: {self.name}/{key}")
        self.docs[key].update(copy.deepcopy(document))
        self.writes += 1
        return True if silent else {"_key": key}

    def delete(
        self,
        document: str | dict[str, Any],
        rev: str | None = None,
        check_rev: bool = True,
        ignore_missing: bool = False,
        return_old: bool = False,
        sync: bool | None = None,
        silent: bool = False,
        refill_index_caches: bool | None = None,
    ) -> bool | dict[str, Any]:
        self._check_exists()
        self._db._check_writable()
        key = self._key(document)
        if key not in self.docs:
            if ignore_missing:
                return False
            raise FakeArangoError(f"document not found: {self.name}/{key}")
        del self.docs[key]
        return True if silent else {"_key": key}


class FakeDatabase:
    def __init__(self, name: str = "test_db", *, read_only: bool = False, unreachable: bool = False) -> None:
        self.name = name
        self.read_only = read_only
        self.unreachable = unreachable
        self._collections: dict[str, FakeCollection] = {}

    def _check_reachable(self) -> None:
        if self.unreachable:
            raise FakeArangoError("connection refused")

    def _check_writable(self) -> None:
        self._check_reachable()
        if self.read_only:
            raise FakeArangoError("forbidden: read-only user")

    def has_collection(self, name: str) -> bool:
        self._check_reachable()
        return name in self._collections

    def collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            # python-arango returns a handle for any name; the first call on it fails.
            return FakeCollection(name, self, missing=True)
        return self._collections[name]

    def create_collection(
        self,
        name: str,
        sync: bool = False,
        system: bool = False,
        edge: bool = False,
        user_keys: bool = True,
        key_increment: int | None = None,
        key_offset: int | None = None,
        key_generator: str = "traditional",
        shard_fields: Sequence[str] | None = None,
        shard_count: int | None = None,
        replication_factor: int | None = None,
        shard_like: str | None = None,
        sync_replication: bool | None = None,
        enforce_replication_factor: bool | None = None,
        sharding_strategy: str | None = None,
        smart_join_attribute: str | None = None,
        write_concern: int | None = None,
        schema: dict[str, Any] | None = None,
        computedValues: list[dict[str, Any]] | None = None,  # noqa: N803 — python-arango's spelling
    ) -> FakeCollection:
        self._check_writable()
        if name in self._collections:
            raise FakeArangoError(f"duplicate name: {name}")
        self._collections[name] = FakeCollection(name, self)
        return self._collections[name]

    def collections(self) -> list[dict[str, Any]]:
        self._check_reachable()
        return [{"name": n, "system": False, "type": "document"} for n in sorted(self._collections)]
