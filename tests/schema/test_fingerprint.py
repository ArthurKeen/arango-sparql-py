"""Unit tests for ``arango_sparql.schema.fingerprint``.

Covers:

* Determinism — same input ⇒ same output, every time, on every process.
* Sensitivity — every shape-relevant field affects the shape
  fingerprint; every counts-relevant field affects the counts
  fingerprint.
* Insensitivity — transient metadata (timestamps, confidence,
  warnings) must *not* affect either fingerprint.
* Drift detection — :meth:`BundleFingerprint.drift_from` reports
  the three states ``/schema/status`` distinguishes.
* Payload-version isolation — a fingerprint computed under an old
  ``FINGERPRINT_PAYLOAD_VERSION`` cannot accidentally match a
  current bundle (defence against silent stale-cache hits).
* Golden regression sentinels — the corpus fixtures have locked-in
  fingerprint values; any change to the projection logic that
  unintentionally alters the hash for unchanged input lights this
  test up red.

All tests are pure-Python (no DB, no LLM, no network) so they run
on the default unmarked-pytest gate.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arango_sparql.schema.fingerprint import (
    FINGERPRINT_PAYLOAD_VERSION,
    BundleFingerprint,
    FingerprintDrift,
    bundle_counts_fingerprint,
    bundle_shape_fingerprint,
    compute_bundle_fingerprint,
)
from arango_sparql.translate.mapping import MappingBundle, mapping_from_wire_dict

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Golden fingerprints for every fixture in PRD §13.3. When the
# projection logic in :mod:`fingerprint` legitimately changes,
# regenerate these via the snippet below and bump
# ``FINGERPRINT_PAYLOAD_VERSION``:
#
#     for name in FIXTURE_NAMES:
#         raw = json.loads((FIXTURES_DIR / f"{name}.export.json").read_text())
#         b = mapping_from_wire_dict(raw)
#         print(name, bundle_shape_fingerprint(b), bundle_counts_fingerprint(b))
#
# Locking the goldens at hex-digest precision makes any silent
# change to the hash payload light up as a clear test failure
# rather than silently invalidating production caches.
GOLDEN_FINGERPRINTS: dict[str, tuple[str, str]] = {
    "pg": (
        "ed5d2b7db77989f5911f900aaa836442012197dbde414621155bcee04c773359",
        "6edd206142b2b997d017a22eff025e9e72b07857ff31e3df52cba6be6478aa8f",
    ),
    "lpg": (
        "d65ed1935c24e90b0741fee4eccda83bdbfcfda91b24317bcec974ee59ea20ba",
        "50ac843c56b38efc28c374be1718cb42223f40832bfa9961064653bc5588b020",
    ),
    "hybrid": (
        "2a9ea7638869c0c327e7b358ff54d0d4ecb628105b16a9d0b7704c46044215e1",
        "2a8ba7baffc547cbb2d8fa821b8ce3b0f9a12cfc1093f5c81fe5cb1cf639b501",
    ),
    "rpt": (
        "37169df774d3f23b0232eb1da3f9d7395b4888c16b3a1e652e35a21d07c4c00d",
        "01eadc6d9c168c6c53366367172d2f56727fa885abe08c6f206fdd27d3d03e1c",
    ),
    "rpt_pg_hybrid": (
        "1e2593008022540b3eb2003ea388e329ea9acb3cd354c8217779c19f13286785",
        "2204623aa63549284442c78d8ff56b295d7da2b4d3c592decd6dd68b5c77e329",
    ),
    "rpt_lpg_hybrid": (
        "4ef331adc800bfd18d64417c03d7160b2abff77c6a7a8d6c6076ae134835bf57",
        "5c31b6f3cfda0dd2a3cc97b2e2abf55d8b5321d38eb668265df1bd6d81971050",
    ),
    "rpt_pg_lpg_hybrid": (
        "c532ea1364a1809d7480c64cc8af040c396682295e82c1f6b8ca861902347b10",
        "45b79c08358a261fc838901d4e5552ba43a08fbbe83fc6172343f9356846ae9c",
    ),
    "multitenant": (
        "b0ea4f958ab9a8b9674c05b8de4b999c0b82afd614b3ef625fe81734bb328597",
        "ec8dfd44905e3058b1d65b3619b7a435f6446080fee01b626fe68ef5375dd449",
    ),
    "sharded": (
        "586a78d219b2d395621332370aa2522401bca415db58f1460791be25a42e8dfc",
        "f94c821744ced1ef47654874b7fef47edea990948032292adb070d4c9e3e6074",
    ),
}


def _load(name: str) -> dict[str, object]:
    return json.loads((FIXTURES_DIR / f"{name}.export.json").read_text("utf-8"))


def _bundle(name: str) -> MappingBundle:
    return mapping_from_wire_dict(_load(name))


# ---------------------------------------------------------------------------
# Format / shape of the output
# ---------------------------------------------------------------------------


def test_shape_fingerprint_is_64_char_hex_lowercase() -> None:
    fp = bundle_shape_fingerprint(_bundle("pg"))
    assert len(fp) == 64
    assert fp == fp.lower()
    int(fp, 16)  # raises if not valid hex


def test_counts_fingerprint_is_64_char_hex_lowercase() -> None:
    fp = bundle_counts_fingerprint(_bundle("pg"))
    assert len(fp) == 64
    assert fp == fp.lower()
    int(fp, 16)


def test_compute_bundle_fingerprint_returns_frozen_dataclass() -> None:
    """``BundleFingerprint`` must be frozen so a cached fingerprint
    cannot drift via mutation. PRD §6.3.3 cache integrity depends on
    this.
    """

    from dataclasses import FrozenInstanceError

    fp = compute_bundle_fingerprint(_bundle("pg"))
    assert fp.shape == GOLDEN_FINGERPRINTS["pg"][0]
    assert fp.counts == GOLDEN_FINGERPRINTS["pg"][1]
    assert fp.payload_version == FINGERPRINT_PAYLOAD_VERSION
    assert isinstance(fp.computed_at, datetime)
    with pytest.raises(FrozenInstanceError):
        fp.shape = "tampered"  # type: ignore[misc]


def test_compute_bundle_fingerprint_uses_utc_by_default() -> None:
    fp = compute_bundle_fingerprint(_bundle("pg"))
    assert fp.computed_at.tzinfo is not None
    # The injected timezone must be UTC (analyzers run in different
    # locales; logs and cache entries must be locale-independent).
    assert fp.computed_at.utcoffset() == UTC.utcoffset(fp.computed_at)


def test_compute_bundle_fingerprint_accepts_injected_clock() -> None:
    pinned = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    fp = compute_bundle_fingerprint(_bundle("pg"), now=pinned)
    assert fp.computed_at == pinned


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(GOLDEN_FINGERPRINTS))
def test_shape_fingerprint_is_deterministic_across_calls(name: str) -> None:
    """Five back-to-back calls must produce byte-identical digests."""

    bundle = _bundle(name)
    digests = {bundle_shape_fingerprint(bundle) for _ in range(5)}
    assert len(digests) == 1


@pytest.mark.parametrize("name", sorted(GOLDEN_FINGERPRINTS))
def test_counts_fingerprint_is_deterministic_across_calls(name: str) -> None:
    bundle = _bundle(name)
    digests = {bundle_counts_fingerprint(bundle) for _ in range(5)}
    assert len(digests) == 1


def test_fingerprint_is_invariant_under_dict_insertion_order() -> None:
    """Two bundles that differ only in dict insertion order must
    produce identical fingerprints. Python preserves insertion
    order for ``dict``, so the canonicalisation (``sort_keys=True``,
    sorted projections) is the only thing standing between us and
    a non-deterministic hash.
    """

    raw = _load("pg")
    bundle_natural = mapping_from_wire_dict(raw)

    # Reverse the entity dict order.
    physical = raw["physicalMapping"]  # type: ignore[index]
    entities = physical["entities"]  # type: ignore[index]
    reversed_entities = dict(reversed(list(entities.items())))
    raw_reversed = copy.deepcopy(raw)
    raw_reversed["physicalMapping"]["entities"] = reversed_entities  # type: ignore[index]

    bundle_reversed = mapping_from_wire_dict(raw_reversed)
    assert bundle_shape_fingerprint(bundle_natural) == bundle_shape_fingerprint(bundle_reversed)
    assert bundle_counts_fingerprint(bundle_natural) == bundle_counts_fingerprint(bundle_reversed)


# ---------------------------------------------------------------------------
# Golden regression sentinels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,goldens", sorted(GOLDEN_FINGERPRINTS.items()))
def test_corpus_fixture_matches_golden_fingerprints(name: str, goldens: tuple[str, str]) -> None:
    """If this lights up red on a PR that did *not* intentionally
    change the fingerprint projection, the change has silently
    invalidated production caches. Roll back, fix, or — if the
    change is deliberate — regenerate the goldens *and* bump
    :data:`FINGERPRINT_PAYLOAD_VERSION`.
    """

    bundle = _bundle(name)
    expected_shape, expected_counts = goldens
    assert bundle_shape_fingerprint(bundle) == expected_shape
    assert bundle_counts_fingerprint(bundle) == expected_counts


def test_every_fixture_has_a_distinct_shape_fingerprint() -> None:
    """If two fixtures collide on shape, our projection logic is
    losing information — the corpus is designed so every fixture
    has a structurally distinct mapping.
    """

    shapes = {name: bundle_shape_fingerprint(_bundle(name)) for name in GOLDEN_FINGERPRINTS}
    assert len(set(shapes.values())) == len(shapes), f"Shape fingerprint collision across fixtures: {shapes}"


# ---------------------------------------------------------------------------
# Sensitivity to shape-relevant fields
# ---------------------------------------------------------------------------


def _mutate(raw: dict, path: list[str], new_value: object) -> dict:
    """Return a deep copy of *raw* with the dotted *path* set to
    *new_value*. Convenience for parametrised mutation tests.
    """

    out = copy.deepcopy(raw)
    target = out
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = new_value
    return out


def test_renaming_a_collection_changes_shape_fingerprint() -> None:
    """Changing a ``collectionName`` is the canonical "the schema
    moved" case — must always change the shape fingerprint.
    """

    raw = _load("pg")
    mutated = _mutate(raw, ["physicalMapping", "entities", "Person", "collectionName"], "people")
    assert bundle_shape_fingerprint(mapping_from_wire_dict(raw)) != bundle_shape_fingerprint(
        mapping_from_wire_dict(mutated)
    )


def test_switching_entity_style_changes_shape_fingerprint() -> None:
    raw = _load("pg")
    mutated = _mutate(raw, ["physicalMapping", "entities", "Person", "style"], "LABEL")
    assert bundle_shape_fingerprint(mapping_from_wire_dict(raw)) != bundle_shape_fingerprint(
        mapping_from_wire_dict(mutated)
    )


def test_adding_a_relationship_changes_shape_fingerprint() -> None:
    raw = _load("pg")
    mutated = copy.deepcopy(raw)
    mutated["physicalMapping"]["relationships"]["BLOCKS"] = {
        "edgeCollectionName": "blocks",
        "style": "DEDICATED_COLLECTION",
        "fromEntity": "User",
        "toEntity": "User",
    }
    assert bundle_shape_fingerprint(mapping_from_wire_dict(raw)) != bundle_shape_fingerprint(
        mapping_from_wire_dict(mutated)
    )


def test_changing_rpt_column_override_changes_shape_fingerprint() -> None:
    """Renaming an RPT column override is a real schema change —
    the translator emits AQL referencing those columns, so the
    emitted query is different.
    """

    raw = _load("rpt")
    mutated = _mutate(
        raw,
        ["physicalMapping", "entities", "Person", "objectValueColumn"],
        "obj_lit",
    )
    assert bundle_shape_fingerprint(mapping_from_wire_dict(raw)) != bundle_shape_fingerprint(
        mapping_from_wire_dict(mutated)
    )


def test_changing_shard_families_changes_shape_fingerprint() -> None:
    raw = _load("sharded")
    mutated = copy.deepcopy(raw)
    mutated["physicalMapping"]["shardFamilies"] = [
        ["_triples_us", "_triples_eu", "_triples_apac", "_triples_aus"]
    ]
    assert bundle_shape_fingerprint(mapping_from_wire_dict(raw)) != bundle_shape_fingerprint(
        mapping_from_wire_dict(mutated)
    )


def test_changing_multitenancy_strategy_changes_shape_fingerprint() -> None:
    raw = _load("multitenant")
    mutated = _mutate(raw, ["metadata", "multitenancy", "strategy"], "database")
    assert bundle_shape_fingerprint(mapping_from_wire_dict(raw)) != bundle_shape_fingerprint(
        mapping_from_wire_dict(mutated)
    )


def test_shardfamilies_ordering_does_not_change_shape_fingerprint() -> None:
    """Internal ordering of a shard family is incidental — three
    physical shards are still three physical shards regardless of
    array order.
    """

    raw = _load("sharded")
    mutated = copy.deepcopy(raw)
    mutated["physicalMapping"]["shardFamilies"] = [["_triples_apac", "_triples_us", "_triples_eu"]]
    assert bundle_shape_fingerprint(mapping_from_wire_dict(raw)) == bundle_shape_fingerprint(
        mapping_from_wire_dict(mutated)
    )


# ---------------------------------------------------------------------------
# Insensitivity to transient metadata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("timestamp", "2030-12-31T23:59:59Z"),
        ("confidence", 0.42),
        ("warnings", ["any number of new warnings"]),
        ("repairAttempts", 7),
        ("assumptions", ["totally different assumptions"]),
        ("detectedPatterns", ["wildly different patterns"]),
        ("model", "gpt-9"),
        ("provider", "future-provider"),
        ("usedBaseline", True),
        ("reviewRequired", True),
    ],
)
def test_transient_metadata_does_not_change_either_fingerprint(field: str, new_value: object) -> None:
    """The whole point of separating shape and counts from the rest
    of the metadata is that *operator-noise* fields (timestamp,
    confidence, warnings) cannot trigger cache invalidations. A
    regression here would flap caches across every analyzer run.
    """

    raw = _load("pg")
    mutated = _mutate(raw, ["metadata", field], new_value)
    original = mapping_from_wire_dict(raw)
    changed = mapping_from_wire_dict(mutated)
    assert bundle_shape_fingerprint(original) == bundle_shape_fingerprint(changed)
    assert bundle_counts_fingerprint(original) == bundle_counts_fingerprint(changed)


def test_source_field_does_not_change_shape_fingerprint() -> None:
    """``source`` is provenance, not schema. A bundle re-acquired
    from the analyzer must still hash-match a previously-cached
    heuristic bundle if the *physical* mapping is identical
    (otherwise we'd needlessly re-emit AQL during the heuristic →
    analyzer transition).
    """

    raw = _load("pg")
    mutated = copy.deepcopy(raw)
    mutated["source"] = {"kind": "analyzer", "notes": "now from analyzer"}
    base = mapping_from_wire_dict(raw)
    with_source = mapping_from_wire_dict(mutated)
    assert bundle_shape_fingerprint(base) == bundle_shape_fingerprint(with_source)
    assert bundle_counts_fingerprint(base) == bundle_counts_fingerprint(with_source)


# ---------------------------------------------------------------------------
# Counts fingerprint specifically
# ---------------------------------------------------------------------------


def test_changing_statistics_changes_counts_but_not_shape() -> None:
    """Adding a ``metadata.statistics`` block (or perturbing an
    existing one) must change the counts fingerprint but leave the
    shape fingerprint untouched. This is the PRD §6.3.3 "stats-only
    drift" case.
    """

    raw = _load("pg")
    mutated = copy.deepcopy(raw)
    mutated["metadata"]["statistics"] = {
        "Person": {"count": 12345, "avgEdgesOut": 4.2},
        "Doc": {"count": 999, "avgEdgesIn": 1.1},
    }
    base = mapping_from_wire_dict(raw)
    changed = mapping_from_wire_dict(mutated)
    assert bundle_shape_fingerprint(base) == bundle_shape_fingerprint(changed)
    assert bundle_counts_fingerprint(base) != bundle_counts_fingerprint(changed)


def test_changing_analyzed_collection_counts_changes_counts_but_not_shape() -> None:
    raw = _load("pg")
    mutated = _mutate(
        raw,
        ["metadata", "analyzedCollectionCounts"],
        {"documentCollections": 42, "edgeCollections": 17},
    )
    base = mapping_from_wire_dict(raw)
    changed = mapping_from_wire_dict(mutated)
    assert bundle_shape_fingerprint(base) == bundle_shape_fingerprint(changed)
    assert bundle_counts_fingerprint(base) != bundle_counts_fingerprint(changed)


def test_shape_and_counts_fingerprints_never_collide_on_same_bundle() -> None:
    """The ``"kind": "shape"`` / ``"kind": "counts"`` discriminator
    in the canonical payload guarantees this even for bundles with
    no statistics block — a bundle whose only counts contribution
    is ``"counts": {}`` must still hash differently from its shape
    fingerprint.
    """

    bundle = MappingBundle(
        physical_mapping={"entities": {}, "relationships": {}},
        metadata={},
    )
    assert bundle_shape_fingerprint(bundle) != bundle_counts_fingerprint(bundle)


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------


def test_drift_from_self_is_unchanged() -> None:
    fp = compute_bundle_fingerprint(_bundle("pg"))
    assert fp.drift_from(fp) is FingerprintDrift.UNCHANGED
    assert fp.matches(fp) is True


def test_drift_stats_only_when_only_counts_change() -> None:
    raw = _load("pg")
    base = compute_bundle_fingerprint(mapping_from_wire_dict(raw))
    mutated_raw = copy.deepcopy(raw)
    mutated_raw["metadata"]["statistics"] = {"Person": {"count": 100}}
    drifted = compute_bundle_fingerprint(mapping_from_wire_dict(mutated_raw))
    assert drifted.drift_from(base) is FingerprintDrift.STATS_ONLY
    assert drifted.matches(base) is False


def test_drift_shape_changed_when_topology_changes() -> None:
    raw = _load("pg")
    base = compute_bundle_fingerprint(mapping_from_wire_dict(raw))
    mutated_raw = _mutate(raw, ["physicalMapping", "entities", "Person", "collectionName"], "people")
    drifted = compute_bundle_fingerprint(mapping_from_wire_dict(mutated_raw))
    assert drifted.drift_from(base) is FingerprintDrift.SHAPE_CHANGED


def test_drift_shape_changed_when_payload_version_mismatch() -> None:
    """A fingerprint cached under an older payload-version stamp
    must report as ``SHAPE_CHANGED`` against any current
    fingerprint, regardless of digest match. Otherwise a
    projection-format change could silently revive stale-cache hits.
    """

    bundle = _bundle("pg")
    current = compute_bundle_fingerprint(bundle)
    cached = BundleFingerprint(
        shape=current.shape,
        counts=current.counts,
        payload_version=FINGERPRINT_PAYLOAD_VERSION + 1,
        computed_at=current.computed_at,
    )
    assert current.drift_from(cached) is FingerprintDrift.SHAPE_CHANGED
    assert current.matches(cached) is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_bundle_has_stable_fingerprints() -> None:
    """An empty bundle is a legitimate state (heuristic detection
    against an empty DB). Both fingerprints must be computable and
    stable; they must also differ from any non-empty bundle.
    """

    empty = MappingBundle()
    fp = compute_bundle_fingerprint(empty)
    assert len(fp.shape) == 64
    assert len(fp.counts) == 64
    assert fp.shape != fp.counts

    pg_fp = compute_bundle_fingerprint(_bundle("pg"))
    assert fp.shape != pg_fp.shape
    assert fp.counts != pg_fp.counts


def test_fingerprint_handles_missing_metadata_gracefully() -> None:
    bundle = MappingBundle(
        physical_mapping={
            "entities": {"Person": {"collectionName": "persons", "style": "COLLECTION"}},
            "relationships": {},
        },
    )
    # No raise, deterministic output
    digest = bundle_shape_fingerprint(bundle)
    assert len(digest) == 64
    assert bundle_shape_fingerprint(bundle) == digest


def test_fingerprint_payload_version_is_positive_int() -> None:
    """Smoke check on the constant; if a future change accidentally
    sets it to a string the dataclass would silently accept it and
    drift_from comparisons would break.
    """

    assert isinstance(FINGERPRINT_PAYLOAD_VERSION, int)
    assert FINGERPRINT_PAYLOAD_VERSION >= 1
