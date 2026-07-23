"""Deterministic fingerprints over a :class:`MappingBundle` (PRD §6.3.3).

Two fingerprints, both 64-character lowercase hex SHA-256 digests:

* **Shape** (:func:`bundle_shape_fingerprint`) — covers only the
  structural skeleton: entity labels, styles, collection names,
  discriminator fields, RPT column overrides, edge collections,
  ``shardFamilies``, and the multitenancy strategy. Excludes
  *anything* that changes between two analyzer runs against the
  same physical schema (timestamps, confidence scores, transient
  warnings). Two bundles with the same shape fingerprint emit
  identical AQL for identical SPARQL inputs.

* **Counts** (:func:`bundle_counts_fingerprint`) — shape inputs
  *plus* ``metadata.statistics`` (per-collection row counts,
  in/out degree, selectivity) *plus*
  ``metadata.analyzedCollectionCounts``. Two bundles with the same
  shape but different counts fingerprints describe the same
  schema with different data volume — the translator's AQL is
  unchanged, but the planner's *cost model* is stale.

Used by the schema cache (PRD §6.3.3) and by ``/schema/status``
(§6.4) to distinguish three drift states:

* **Unchanged** — both fingerprints match the cached values; the
  cached bundle is fully fresh.
* **Stats-only** — shape matches but counts diverge; AQL is fine,
  emit ``W_SCHEMA_DRIFT_STATS``.
* **Shape-changed** — shape diverges; the cached bundle is stale,
  emit ``W_SCHEMA_DRIFT_SHAPE`` and trigger re-acquire.

Determinism is non-negotiable: a process that recomputes the
fingerprint must produce byte-identical output on every machine,
regardless of dict insertion order, Python version, or locale.
We achieve this via canonical JSON serialisation
(`sort_keys=True`, no whitespace) and explicit field-list
projections — *never* `json.dumps(bundle.physical_mapping)` of
the whole dict (which would leak forward-compat fields into the
hash).

The fingerprint payload format is versioned. When the projection
list changes (e.g. a new physical annotation becomes
shape-relevant), bump :data:`FINGERPRINT_PAYLOAD_VERSION` so old
cached fingerprints invalidate cleanly rather than silently
matching against new bundles. PRD §6.3.3 stat warnings rely on
this contract — see ``test_fingerprint_version_change_invalidates``
in the test suite.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from arango_sparql.translate.mapping import MappingBundle

# Bump this constant when the *meaning* of the fingerprint payload
# changes (a field is added or removed from the canonical shape /
# counts projection). Two fingerprints computed under different
# versions will never compare equal, so a cached fingerprint from
# v1 cannot accidentally satisfy a v2 freshness check.
FINGERPRINT_PAYLOAD_VERSION: int = 1


# ---------------------------------------------------------------------------
# Field projection lists
# ---------------------------------------------------------------------------


# Entity-level fields that go into the *shape* fingerprint, in the
# canonical order. The order is part of the wire contract — changing
# it requires a payload-version bump. We use a fixed tuple rather
# than a set to make the contract auditable at a glance.
_SHAPE_ENTITY_FIELDS: tuple[str, ...] = (
    "style",
    "collectionName",
    "typeField",
    "typeValue",
    "triplesCollection",
    "subjectColumn",
    "predicateColumn",
    "objectUriColumn",
    "objectValueColumn",
    "tenantField",
    "tenantEntity",
)

_SHAPE_RELATIONSHIP_FIELDS: tuple[str, ...] = (
    "style",
    "edgeCollectionName",
    "typeField",
    "typeValue",
    "fromEntity",
    "toEntity",
    "triplesCollection",
)

# Top-level metadata keys that go into the *counts* fingerprint
# only. ``statistics`` is the analyzer's per-collection cardinality
# block; ``analyzedCollectionCounts`` is the aggregate
# (``{documentCollections: 4, edgeCollections: 1}``-shaped) view
# the LPG/PG fixtures carry. Both legitimately change when data
# volume changes — and *only* when data volume changes.
_COUNTS_METADATA_KEYS: tuple[str, ...] = (
    "statistics",
    "analyzedCollectionCounts",
)

# Top-level metadata keys that are intentionally *excluded* from
# every fingerprint. Listed here so the projection is auditable and
# so a future contributor cannot quietly hash a transient field.
# (Kept as a sentinel; not directly consumed by the projection
# functions below.)
_FINGERPRINT_EXCLUDED_KEYS: frozenset[str] = frozenset(
    {
        "timestamp",
        "confidence",
        "warnings",
        "repairAttempts",
        "assumptions",
        "detectedPatterns",
        "model",
        "provider",
        "usedBaseline",
        "reviewRequired",
    }
)


# ---------------------------------------------------------------------------
# Canonical payload builders
# ---------------------------------------------------------------------------


def _project_spec(spec: dict[str, Any] | None, field_order: tuple[str, ...]) -> list[tuple[str, Any]]:
    """Project a single entity- or relationship-spec dict onto the
    canonical field order, dropping keys that are not in the
    projection and preserving ``None`` for keys that the spec
    omits. Returns a list of ``(key, value)`` pairs so iteration
    order is part of the canonical form.
    """

    if spec is None:
        return [(name, None) for name in field_order]
    return [(name, spec.get(name)) for name in field_order]


def _project_entities(physical: dict[str, Any]) -> list[list[Any]]:
    """Project ``physicalMapping.entities`` onto the shape-canonical
    form. Sorted by entity label so dict insertion order cannot
    affect the hash.
    """

    entities = physical.get("entities") or {}
    if not isinstance(entities, dict):
        return []
    return [[label, _project_spec(spec, _SHAPE_ENTITY_FIELDS)] for label, spec in sorted(entities.items())]


def _project_relationships(physical: dict[str, Any]) -> list[list[Any]]:
    """Project ``physicalMapping.relationships`` onto the shape-
    canonical form. Sorted by relationship type for the same reason
    as :func:`_project_entities`.
    """

    relationships = physical.get("relationships") or {}
    if not isinstance(relationships, dict):
        return []
    return [
        [rtype, _project_spec(spec, _SHAPE_RELATIONSHIP_FIELDS)]
        for rtype, spec in sorted(relationships.items())
    ]


def _project_shard_families(physical: dict[str, Any]) -> list[list[str]]:
    """Project ``physicalMapping.shardFamilies`` onto a deterministic
    nested list. Each family is sorted internally; the outer list
    is sorted by the first member of each family.
    """

    raw = physical.get("shardFamilies")
    if not raw:
        return []
    if not isinstance(raw, list):
        return []
    families: list[list[str]] = []
    for family in raw:
        if not isinstance(family, list):
            continue
        families.append(sorted(str(m) for m in family))
    families.sort(key=lambda f: (f[0] if f else "", f))
    return families


def _project_multitenancy(metadata: dict[str, Any]) -> dict[str, Any]:
    """Extract only the multitenancy strategy descriptor from
    ``metadata.multitenancy``. The ``tenantScope`` per-entity block
    is intentionally *not* included here — it is already projected
    via the ``tenantField`` / ``tenantEntity`` fields on each entity
    spec, so hashing it twice would only inflate the payload.
    """

    mt = metadata.get("multitenancy")
    if not isinstance(mt, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("strategy", "tenantRootEntity", "tenantKeyAttribute"):
        value = mt.get(key)
        if value is not None:
            out[key] = value
    return out


def _shape_payload(bundle: MappingBundle) -> dict[str, Any]:
    """Build the deterministic shape-payload dict for hashing.

    Returns a plain dict that downstream code immediately serialises
    via :func:`json.dumps` with ``sort_keys=True``. The dict is not
    intended for direct consumption — it is an implementation
    detail of the fingerprint hash and may change in any
    payload-version bump.
    """

    physical = bundle.physical_mapping if isinstance(bundle.physical_mapping, dict) else {}
    metadata = bundle.metadata if isinstance(bundle.metadata, dict) else {}
    return {
        "version": FINGERPRINT_PAYLOAD_VERSION,
        "kind": "shape",
        "entities": _project_entities(physical),
        "relationships": _project_relationships(physical),
        "shardFamilies": _project_shard_families(physical),
        "multitenancy": _project_multitenancy(metadata),
    }


def _counts_payload(bundle: MappingBundle) -> dict[str, Any]:
    """Build the deterministic counts-payload dict for hashing.

    Extends the shape payload with the two count-bearing metadata
    blocks (``statistics``, ``analyzedCollectionCounts``). The
    presence of the ``"kind": "counts"`` discriminator means a
    shape fingerprint and a counts fingerprint *over the same
    bundle* never collide, even if the bundle has zero statistics.
    """

    payload = _shape_payload(bundle)
    payload["kind"] = "counts"
    metadata = bundle.metadata if isinstance(bundle.metadata, dict) else {}
    counts: dict[str, Any] = {}
    for key in _COUNTS_METADATA_KEYS:
        value = metadata.get(key)
        if value is not None:
            counts[key] = value
    payload["counts"] = counts
    return payload


# ---------------------------------------------------------------------------
# Hash primitives
# ---------------------------------------------------------------------------


def _hash_payload(payload: dict[str, Any]) -> str:
    """Serialise *payload* to canonical JSON and return its hex
    SHA-256 digest.

    ``sort_keys=True`` ensures dict-iteration order does not affect
    the hash. ``separators=(",", ":")`` strips every byte of
    incidental whitespace so the canonical form is the smallest
    legal JSON representation of the payload.
    """

    serialised = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_fallback,
    )
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


def _json_fallback(obj: Any) -> Any:
    """Last-resort JSON serialiser for objects the canonical encoder
    cannot otherwise handle. We support tuples (collapse to lists)
    and frozensets / sets (collapse to sorted lists) so callers can
    hand us hashable containers. Anything else surfaces as a
    :class:`TypeError` rather than silently becoming a non-canonical
    string.
    """

    if isinstance(obj, tuple):
        return list(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=repr)
    raise TypeError(
        f"Cannot canonically serialise value of type {type(obj).__name__!r} into a fingerprint payload"
    )


# ---------------------------------------------------------------------------
# Public API — fingerprint computation
# ---------------------------------------------------------------------------


def bundle_shape_fingerprint(bundle: MappingBundle) -> str:
    """Return the 64-char hex SHA-256 *shape* fingerprint of *bundle*.

    See module docstring for what "shape" covers vs. what it omits.
    The result is stable across processes, machines, and Python
    versions — it depends only on the bundle's structural content.
    """

    return _hash_payload(_shape_payload(bundle))


def bundle_counts_fingerprint(bundle: MappingBundle) -> str:
    """Return the 64-char hex SHA-256 *counts* fingerprint of *bundle*.

    Includes everything in :func:`bundle_shape_fingerprint` plus the
    counts-bearing metadata blocks. Two bundles with the same shape
    but different statistics produce different counts fingerprints.
    """

    return _hash_payload(_counts_payload(bundle))


# ---------------------------------------------------------------------------
# Drift indicator + dataclass
# ---------------------------------------------------------------------------


class FingerprintDrift(Enum):
    """How two fingerprints relate to each other.

    Maps directly to the three states ``/schema/status`` reports
    (PRD §6.4): ``UNCHANGED`` (no warning), ``STATS_ONLY`` (emit
    ``W_SCHEMA_DRIFT_STATS``), and ``SHAPE_CHANGED`` (emit
    ``W_SCHEMA_DRIFT_SHAPE`` and trigger re-acquire).
    """

    UNCHANGED = "unchanged"
    STATS_ONLY = "stats_only"
    SHAPE_CHANGED = "shape_changed"


@dataclass(frozen=True)
class BundleFingerprint:
    """Both fingerprints of a bundle, plus the moment they were taken.

    Frozen so a cached fingerprint cannot drift via mutation. Carries
    its own payload-version stamp so a cached fingerprint produced
    under an older :data:`FINGERPRINT_PAYLOAD_VERSION` reports as
    ``SHAPE_CHANGED`` against any current bundle — preventing a
    stale-cache silent hit after a fingerprint-format change.
    """

    shape: str
    counts: str
    payload_version: int
    computed_at: datetime

    def drift_from(self, other: BundleFingerprint) -> FingerprintDrift:
        """Compare *self* (current) against *other* (cached) and
        return the drift category.

        Differing :attr:`payload_version` is treated as a shape
        change so a stale-cache hit is impossible across versions.
        """

        if self.payload_version != other.payload_version:
            return FingerprintDrift.SHAPE_CHANGED
        if self.shape != other.shape:
            return FingerprintDrift.SHAPE_CHANGED
        if self.counts != other.counts:
            return FingerprintDrift.STATS_ONLY
        return FingerprintDrift.UNCHANGED

    def matches(self, other: BundleFingerprint) -> bool:
        """Convenience — ``True`` iff both fingerprints AND the
        payload version match (so the cached bundle is fully fresh).
        """

        return self.drift_from(other) is FingerprintDrift.UNCHANGED


def compute_bundle_fingerprint(bundle: MappingBundle, *, now: datetime | None = None) -> BundleFingerprint:
    """Compute both fingerprints in one pass.

    *now* is injectable so tests can pin the timestamp; production
    callers leave it ``None`` and get :func:`datetime.now` in UTC.
    """

    when = now if now is not None else datetime.now(UTC)
    return BundleFingerprint(
        shape=bundle_shape_fingerprint(bundle),
        counts=bundle_counts_fingerprint(bundle),
        payload_version=FINGERPRINT_PAYLOAD_VERSION,
        computed_at=when,
    )


__all__ = [
    "BundleFingerprint",
    "FINGERPRINT_PAYLOAD_VERSION",
    "FingerprintDrift",
    "bundle_counts_fingerprint",
    "bundle_shape_fingerprint",
    "compute_bundle_fingerprint",
]
