"""Golden tests for multi-tenancy enforcement in the visitor (PRD §6.5.1).

Every BGP triple that opens a FOR over a tenant-scoped class must emit
a ``FILTER doc.<tenantField> == @<tenant_bind>`` predicate so the
result set never crosses tenant boundaries; cross-tenant joins (two
classes whose ``phys:tenantEntity`` values differ) must raise
:class:`CrossTenantJoinError` instead of producing AQL.

The goldens live in ``multitenancy.yml`` next to this file and follow
the same shape as ``edge_traversal.yml`` / ``rpt.yml`` / ``hybrid.yml``:
each case carries a SPARQL query, the per-request ``tenant_id``, and
the expected AQL + ``bind_vars`` shape. Cross-tenant violations are
asserted in dedicated ``test_*_cross_tenant_*`` cases below — keeping
them out of the YAML preserves the YAML's "expected output is a
golden, not an exception" invariant.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arango_sparql.api import translate
from arango_sparql.errors import CrossTenantJoinError
from arango_sparql.translate.resolver import SchemaResolver

GOLDEN_PATH = Path(__file__).parent / "multitenancy.yml"


def _load_cases() -> list[tuple[str, str, str, str, str, dict]]:
    data = yaml.safe_load(GOLDEN_PATH.read_text())
    ttl = data["ontology"]
    out: list[tuple[str, str, str, str, str, dict]] = []
    for case in data["cases"]:
        out.append(
            (
                case["name"],
                ttl,
                case["sparql"],
                case["tenant_id"],
                case["expected_aql"].rstrip("\n"),
                case["expected_bind_vars"],
            )
        )
    return out


@pytest.mark.parametrize(
    "name, ontology_ttl, sparql, tenant_id, expected_aql, expected_bind_vars",
    _load_cases(),
    ids=[c[0] for c in _load_cases()],
)
def test_multitenancy_golden(
    name: str,
    ontology_ttl: str,
    sparql: str,
    tenant_id: str,
    expected_aql: str,
    expected_bind_vars: dict,
) -> None:
    resolver = SchemaResolver.from_turtle(ontology_ttl)
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
# Negative path — these MUST raise rather than emit AQL
# ---------------------------------------------------------------------------


_ONTOLOGY = yaml.safe_load(GOLDEN_PATH.read_text())["ontology"]


def test_cross_tenant_join_is_refused() -> None:
    """``:Person`` (tenantEntity=Org) joined to ``:ExternalAudit``
    (tenantEntity=ExternalOrg) must raise — emitting AQL would let
    the query broadcast across tenant roots.
    """
    sparql = """
    PREFIX : <http://ex.org/>
    SELECT ?s ?a WHERE {
      ?s a :Person .
      ?a a :ExternalAudit .
    }
    """
    resolver = SchemaResolver.from_turtle(_ONTOLOGY)
    with pytest.raises(CrossTenantJoinError) as exc_info:
        translate(sparql, resolver=resolver, tenant_id="tenant-alpha")
    assert exc_info.value.code == "E_TRANSLATE_CROSS_TENANT_JOIN"
    assert "Org" in str(exc_info.value)
    assert "ExternalOrg" in str(exc_info.value)


def test_tenant_scoped_class_without_tenant_id_is_refused() -> None:
    """A query that references a tenant-scoped class but supplies no
    ``tenant_id`` would silently leak rows across tenants — the
    visitor refuses with the same typed error code.
    """
    sparql = """
    PREFIX : <http://ex.org/>
    SELECT ?s WHERE { ?s a :Person . }
    """
    resolver = SchemaResolver.from_turtle(_ONTOLOGY)
    with pytest.raises(CrossTenantJoinError) as exc_info:
        translate(sparql, resolver=resolver, tenant_id=None)
    assert exc_info.value.code == "E_TRANSLATE_CROSS_TENANT_JOIN"
    assert "tenant" in str(exc_info.value).lower()
