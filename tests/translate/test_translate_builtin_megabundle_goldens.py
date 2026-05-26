"""Golden tests for the v0.8 builtin-megabundle slice: 17 FILTER
/ projection builtins (DATATYPE, REPLACE, STRDT, STRLANG,
STRBEFORE, STRAFTER, ENCODE_FOR_URI, COALESCE, ABS/CEIL/FLOOR/
ROUND, NOW/YEAR/MONTH/DAY/HOURS/MINUTES/SECONDS, MD5/SHA1/SHA512,
isURI/isIRI/isBLANK/isNUMERIC) plus the blank-node existential-
variable substitution that ``visit_BGP`` applies.

Coverage moved from 41.5 % to 60.1 % on the W3C DAWG corpus with
this slice (+18.6 pp, +47 newly-passing tests) — the project's
single biggest jump because every previously-failing test that
combined multiple builtins cascaded simultaneously once the whole
set landed.

Two test blocks:

* **YAML goldens** — every shape from the corpus with exact AQL
  + bind-vars assertions.
* **Resolver-driven interactions** — Python tests for the
  BGP-scope isolation of BNode substitution (distinct labels in
  the SAME BGP get distinct internal vars; the SAME label in
  DIFFERENT BGPs gets different internal vars — these are the
  two halves of the §17.4.1.10 / §18.5 scoping rule).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from arango_sparql.api import translate
from arango_sparql.errors import UnsupportedSparqlError
from arango_sparql.translate.resolver import SchemaResolver

GOLDEN_PATH = Path(__file__).parent / "builtin_megabundle.yml"


def _load() -> list[tuple[str, str, str, str, dict]]:
    data = yaml.safe_load(GOLDEN_PATH.read_text())
    ttl = data["ontology"]
    out: list[tuple[str, str, str, str, dict]] = []
    for case in data["cases"]:
        out.append(
            (
                case["name"],
                ttl,
                case["sparql"],
                case["expected_aql"].rstrip("\n"),
                case["expected_bind_vars"],
            )
        )
    return out


@pytest.mark.parametrize(
    "name, ontology_ttl, sparql, expected_aql, expected_bind_vars",
    _load(),
    ids=[c[0] for c in _load()],
)
def test_builtin_megabundle_golden(
    name: str,
    ontology_ttl: str,
    sparql: str,
    expected_aql: str,
    expected_bind_vars: dict,
) -> None:
    """Each golden produces the exact AQL the YAML declares.

    Pinning byte-for-byte protects against:

    1. **Builtin cascade ordering** — DATATYPE in particular has
       a fragile IS_BOOL → IS_NUMBER → IS_STRING cascade where
       reordering would silently change the answer (AQL booleans
       coerce to numbers under IS_NUMBER).
    2. **STRBEFORE / STRAFTER not-found semantics** — the
       ``FIND_FIRST(…) >= 0 ? … : ""`` guard is load-bearing;
       without it AQL's SUBSTRING with a negative start would
       throw or return unexpected substrings.
    3. **BNode existential JOIN** — when the same label appears
       twice in a BGP, the substitution mints ONE Variable and
       the BGP emitter's shared-variable FILTER fires. A
       regression that minted distinct vars per occurrence
       would silently drop the implicit join and over-return.
    """
    resolver = SchemaResolver.from_turtle(
        ontology_ttl, default_collection="Document"
    )
    result = translate(sparql, resolver=resolver)
    assert result.aql == expected_aql, (
        f"AQL mismatch for {name!r}:\n"
        f"--- expected ---\n{expected_aql}\n"
        f"--- actual ---\n{result.aql}"
    )
    assert result.bind_vars == expected_bind_vars, (
        f"bind_vars mismatch for {name!r}:\n"
        f"--- expected ---\n{expected_bind_vars}\n"
        f"--- actual ---\n{result.bind_vars}"
    )


# ---------------------------------------------------------------------------
# BNode scope-isolation tests
# ---------------------------------------------------------------------------


def test_bnode_distinct_labels_same_bgp_no_join() -> None:
    """``?s :p _:b0 . ?x :q _:b1`` — two DIFFERENT BNode labels
    in the SAME BGP. Per spec, each label is its own
    existential, so NO equality FILTER between them. Regression
    coverage for a buggy substitution that hash-maps all
    BNodes to a single variable.
    """
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    result = translate(
        "PREFIX : <http://ex.org/> SELECT ?s ?x WHERE { "
        "?s :p _:b0 . ?x :q _:b1 "
        "}",
        resolver=resolver,
    )
    # Two FORs (one per subject); no JOIN FILTER tying the two
    # BNode existentials together. Predicate-existence ``FILTER
    # HAS(doc, "attr")`` lines DO appear (SPARQL semantics require
    # the triple's predicate to exist on the document), but those
    # are per-subject existence guards — never cross-FOR joins.
    # The original "no FILTER at all" assertion was too coarse;
    # tighten it instead to forbid equality JOINs across the two
    # aliases.
    assert result.aql.count("FOR doc") == 2
    join_pattern = re.compile(r"FILTER\s+doc\d\.\w+\s*==\s*doc\d\.\w+")
    assert not join_pattern.search(result.aql), (
        "distinct BNode labels must not introduce a JOIN FILTER between "
        f"the two subject aliases:\n{result.aql}"
    )


def test_bnode_same_label_different_bgps_no_cross_scope_join() -> None:
    """The SAME ``_:b0`` label in two SEPARATE BGPs (across a
    UNION arm boundary) is two distinct existential variables
    per SPARQL §17.4.1.10 — the substitution per-BGP scopes
    via ``bgp_counter`` so each BGP mints its own internal
    name.

    User-visible correctness check: each arm's BNode anchors a
    SEPARATE FOR (over a fresh doc alias), and no cross-arm
    FILTER joins the two anchors. The internal var name
    (``_bn_<bgp_id>_<label>``) may surface in the per-arm
    RETURN projection because the UNION emitter uses every
    bound variable as the union schema — that's cosmetic
    leakage, not a semantic violation, because the user's
    SELECT never references the BNode label.
    """
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    result = translate(
        "PREFIX : <http://ex.org/> SELECT ?x ?y WHERE { "
        "{ _:b0 :p ?x } UNION { _:b0 :q ?y } "
        "}",
        resolver=resolver,
    )
    # Two UNION arms; each carries one FOR for its BNode anchor.
    assert "UNION(" in result.aql
    # Each arm has its own doc alias (doc1, doc2) — no shared
    # anchor across arms.
    assert "FOR doc1 IN" in result.aql
    assert "FOR doc2 IN" in result.aql
    # No JOIN FILTER between the two arms' BNode anchors —
    # cross-BGP existentials are independent per spec.
    aql_lines = result.aql.splitlines()
    arm_filter_lines = [
        line for line in aql_lines
        if line.strip().startswith("FILTER doc")
    ]
    assert len(arm_filter_lines) == 0, (
        "cross-arm BNode existential incorrectly joined:\n"
        + "\n".join(arm_filter_lines)
    )


def test_sha256_refuses_with_clear_message() -> None:
    """SHA-256 has no native AQL builtin; silently substituting
    SHA-512 truncated to 256 bits would be a worse failure mode
    than translation failure. Surface a typed error so the
    operator sees the gap.
    """
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    with pytest.raises(UnsupportedSparqlError, match="SHA-256"):
        translate(
            "PREFIX : <http://ex.org/> SELECT (SHA256(?n) AS ?x) "
            "WHERE { ?s :n ?n }",
            resolver=resolver,
        )
