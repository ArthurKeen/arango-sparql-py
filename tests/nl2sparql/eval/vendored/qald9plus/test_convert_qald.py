"""Authoring tests for ``convert_qald.py`` (the QALD-9-plus D-06 conversion).

Not gated behind ``RUN_EVAL`` — this exercises deterministic, no-network
conversion logic (rdflib parse + the transpiler's ``translate()``, never a
live LLM provider), so it belongs on the same always-on default path as
``test_gold_transpilable.py``'s underlying machinery. It DOES run the full
pipeline against the real vendored ``raw/*_en.json`` files (a few seconds),
matching this plan's own acceptance-criteria command
(``pytest .../test_convert_qald.py -x``, no ``RUN_EVAL=1`` prefix).

Exercises the four ``<behavior>`` guarantees from the PLAN:

1. Every emitted case constructs as a valid ``CorpusCase``.
2. filter_log kept + dropped counts sum to the total input question count.
3. Two runs over the same raw input produce an identical case count
   (deterministic FIRST-en selection).
4. ``dbpedia_subset.ttl`` resolves via ``SchemaResolver.from_turtle`` without
   error.

Plus a drift guard: the checked-in ``corpus.yml``/``dbpedia_subset.ttl`` must
match what ``convert()`` produces right now from the checked-in raw files —
otherwise a hand-edit to either output file could silently diverge from the
converter that is supposed to be its single source of truth.
"""

from __future__ import annotations

import yaml

from arango_sparql.translate.resolver import SchemaResolver
from tests.nl2sparql.eval.runner import CorpusCase
from tests.nl2sparql.eval.vendored.qald9plus.convert_qald import (
    CORPUS_PATH,
    ONTOLOGY_PATH,
    _first_english,
    convert,
    load_pool,
)

# ---------------------------------------------------------------------------
# Unit: FIRST-en selection (Pitfall 5) — synthetic fixture, no real data needed
# ---------------------------------------------------------------------------


def test_first_english_takes_first_and_counts_paraphrases() -> None:
    variants = [
        {"language": "de", "string": "German text"},
        {"language": "en", "string": "First English phrasing"},
        {"language": "en", "string": "Second English phrasing"},
        {"language": "ru", "string": "Russian text"},
    ]
    string, count = _first_english(variants)
    assert string == "First English phrasing"
    assert count == 2


def test_first_english_returns_none_when_absent() -> None:
    variants = [{"language": "de", "string": "German only"}]
    string, count = _first_english(variants)
    assert string is None
    assert count == 0


def test_first_english_single_entry_is_deterministic() -> None:
    variants = [{"language": "en", "string": "Only one"}]
    string, count = _first_english(variants)
    assert string == "Only one"
    assert count == 1


# ---------------------------------------------------------------------------
# Integration: the full D-06 pipeline against the real vendored raw pool
# ---------------------------------------------------------------------------


def test_convert_produces_valid_corpus_cases() -> None:
    """Behavior 1: every emitted case constructs as a valid `CorpusCase`."""
    result = convert()
    assert result.cases, "convert() kept zero cases from the real QALD-9-plus pool"
    for case in result.cases:
        # Raises pydantic ValidationError (incl. the gold-must-parse
        # validator) if malformed — this call IS the assertion.
        CorpusCase(name=case["name"], nl=case["nl"], expected=case["expected"])


def test_filter_log_counts_reconcile_with_total_input() -> None:
    """Behavior 2: kept + dropped == total input question count (no silent
    truncation, D-06)."""
    result = convert()
    assert len(result.cases) + result.outcome.total_dropped == result.total_input


def test_case_names_are_unique() -> None:
    """Train/test ids collide (verified against the real files) — every
    qualified case name must still be unique across the combined pool."""
    result = convert()
    names = [c["name"] for c in result.cases]
    assert len(names) == len(set(names))


def test_two_runs_produce_identical_case_count_and_names() -> None:
    """Behavior 3: deterministic FIRST-en selection -> same case count and
    same case-name set across independent runs over the same raw input."""
    first = convert()
    second = convert()
    assert len(first.cases) == len(second.cases)
    assert {c["name"] for c in first.cases} == {c["name"] for c in second.cases}


def test_ontology_subset_resolves_via_schema_resolver() -> None:
    """Behavior 4: the authored ontology subset must parse and resolve."""
    result = convert()
    # Raises if the Turtle is malformed or a term declaration is invalid.
    resolver = SchemaResolver.from_turtle(result.ontology_ttl)
    assert resolver is not None


def test_combined_pool_draws_from_both_train_and_test() -> None:
    """D-02: the pool must be the COMBINED train+test set, not one split."""
    pool = load_pool()
    sources = {q["_source"] for q in pool}
    assert sources == {"train", "test"}
    assert len(pool) == 558, f"expected 408 train + 150 test = 558, got {len(pool)}"


def test_no_yaml_load_in_converter() -> None:
    """Security guard (V5 input validation): only `yaml.safe_load`/`safe_dump`,
    never the unsafe `yaml.load`."""
    source = (ONTOLOGY_PATH.parent / "convert_qald.py").read_text(encoding="utf-8")
    non_comment_lines = [
        line for line in source.splitlines() if not line.strip().startswith("#")
    ]
    assert "yaml.load(" not in "\n".join(non_comment_lines)


# ---------------------------------------------------------------------------
# Drift guard: checked-in outputs must match the converter's current output
# ---------------------------------------------------------------------------


def test_committed_corpus_matches_regenerated_output() -> None:
    """The checked-in `corpus.yml` must equal what `convert()` produces right
    now from the checked-in `raw/*_en.json` files — guards against a stale or
    hand-edited output drifting from its generator."""
    result = convert()
    on_disk = yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))

    on_disk_names = [c["name"] for c in on_disk["cases"]]
    fresh_names = [c["name"] for c in result.cases]
    assert on_disk_names == fresh_names

    assert on_disk["ontology"] == result.ontology_ttl


def test_committed_ontology_file_matches_corpus_ontology_block() -> None:
    ttl_on_disk = ONTOLOGY_PATH.read_text(encoding="utf-8")
    corpus_on_disk = yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))
    assert ttl_on_disk == corpus_on_disk["ontology"]
