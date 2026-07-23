"""Golden tests for cross-shard broadcast (PRD §6.5.3).

Sharding is the third half-step of the §6.5 trio (the other two —
multi-tenancy and the per-entity ``tenantScope`` filter — are
exercised by ``test_translate_multitenancy_goldens.py``). Every FOR
over a member of a declared ``shardFamilies`` expands into a
``FOR <alias> IN UNION_DISTINCT(…)`` fan-out, and the rendered query
carries a leading ``WITH @@shard1, @@shard2, …`` so the cluster
optimiser locks the family at parse time.

The goldens live in ``sharding.yml`` next to this file. Unlike the
other golden modules, the per-case fixture is a full
:class:`MappingBundle` (not just OWL/Turtle) because ``shardFamilies``
is part of the bundle wire shape, not the OWL annotations.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arango_sparql.api import translate
from arango_sparql.translate.mapping import MappingBundle, MappingSource
from arango_sparql.translate.resolver import SchemaResolver

GOLDEN_PATH = Path(__file__).parent / "sharding.yml"


def _load_cases() -> list[tuple[str, dict, str, str | None, str, dict]]:
    """Return ``(name, mapping_bundle_dict, sparql, tenant_id,
    expected_aql, expected_bind_vars)`` per golden case."""

    data = yaml.safe_load(GOLDEN_PATH.read_text())
    out: list[tuple[str, dict, str, str | None, str, dict]] = []
    for case in data["cases"]:
        out.append(
            (
                case["name"],
                case["mapping_bundle"],
                case["sparql"],
                case.get("tenant_id"),
                case["expected_aql"].rstrip("\n"),
                case["expected_bind_vars"],
            )
        )
    return out


@pytest.mark.parametrize(
    "name, bundle_dict, sparql, tenant_id, expected_aql, expected_bind_vars",
    _load_cases(),
    ids=[c[0] for c in _load_cases()],
)
def test_sharding_golden(
    name: str,
    bundle_dict: dict,
    sparql: str,
    tenant_id: str | None,
    expected_aql: str,
    expected_bind_vars: dict,
) -> None:
    bundle = MappingBundle(
        physical_mapping=bundle_dict.get("physical_mapping", {}),
        owl_turtle=bundle_dict.get("owl_turtle"),
        source=MappingSource(kind="manual"),
    )
    resolver = SchemaResolver.from_mapping_bundle(bundle)
    result = translate(sparql, resolver=resolver, tenant_id=tenant_id)
    assert result.aql == expected_aql, (
        f"AQL mismatch for {name!r}:\n--- expected ---\n{expected_aql}\n--- actual ---\n{result.aql}"
    )
    assert result.bind_vars == expected_bind_vars, (
        f"bind_vars mismatch for {name!r}:\n"
        f"--- expected ---\n{expected_bind_vars}\n"
        f"--- actual ---\n{result.bind_vars}"
    )


# ---------------------------------------------------------------------------
# Resolver-level sanity checks (independent of the golden corpus so a
# regression in the resolver surfaces with a more pointed failure than
# "AQL mismatch in fixture N"). These exercise the invariants
# ``SchemaResolver.__post_init__`` and ``_project_shard_families``
# guarantee: deterministic ordering, idempotent re-resolution, and
# rejection of an ambiguous (multi-family) collection.
# ---------------------------------------------------------------------------


def _make_bundle(shard_families: list[list[str]], owl_ttl: str) -> MappingBundle:
    return MappingBundle(
        physical_mapping={
            "entities": {},
            "relationships": {},
            "shardFamilies": shard_families,
        },
        owl_turtle=owl_ttl,
        source=MappingSource(kind="manual"),
    )


_TWO_SHARD_OWL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Triples a owl:Class ;
    phys:mappingStyle "RPT" ;
    phys:triplesCollection "_triples_us" .
"""


def test_resolver_attaches_shard_family_to_class() -> None:
    """A class whose physical collection is named in a shardFamilies
    entry has the (sorted) family tuple attached to its ResolvedClass."""
    bundle = _make_bundle(
        [["_triples_us", "_triples_eu"]],
        _TWO_SHARD_OWL,
    )
    resolver = SchemaResolver.from_mapping_bundle(bundle)
    resolved = resolver.resolve_class("http://ex.org/Triples")
    assert resolved.shard_family == ("_triples_eu", "_triples_us")


def test_resolver_leaves_shard_family_none_for_unsharded_class() -> None:
    """A class whose collection is NOT named in any family stays
    ``shard_family=None`` — the visitor emits a plain FOR."""
    bundle = _make_bundle(
        [["other_collection_a", "other_collection_b"]],
        _TWO_SHARD_OWL,
    )
    resolver = SchemaResolver.from_mapping_bundle(bundle)
    resolved = resolver.resolve_class("http://ex.org/Triples")
    assert resolved.shard_family is None


def test_resolver_rejects_collection_in_two_families() -> None:
    """A physical collection that appears in two families is
    structurally ambiguous — the resolver refuses at construction
    time with a typed error rather than emit one fan-out or the
    other arbitrarily."""
    from arango_sparql.errors import SchemaResolutionError

    bundle = _make_bundle(
        [
            ["_triples_us", "_triples_eu"],
            ["_triples_us", "_triples_apac"],
        ],
        _TWO_SHARD_OWL,
    )
    with pytest.raises(SchemaResolutionError) as exc_info:
        SchemaResolver.from_mapping_bundle(bundle)
    assert "_triples_us" in str(exc_info.value)
    assert "shardFamilies" in str(exc_info.value)


def test_resolver_handles_empty_shard_families() -> None:
    """No ``shardFamilies`` ⇒ resolver behaves identically to a
    single-shard deployment — the FOR stays plain. Verifies the
    backwards-compat carve-out so legacy bundles keep working."""
    bundle = MappingBundle(
        physical_mapping={"entities": {}, "relationships": {}},
        owl_turtle=_TWO_SHARD_OWL,
        source=MappingSource(kind="manual"),
    )
    resolver = SchemaResolver.from_mapping_bundle(bundle)
    assert resolver.shard_families == ()
    resolved = resolver.resolve_class("http://ex.org/Triples")
    assert resolved.shard_family is None
