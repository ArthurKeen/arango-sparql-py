"""Fast, no-network unit tests for the canonical-algebra judge (`_canonical`).

These are deliberately NOT under `@pytest.mark.eval` / `RUN_EVAL`: the judge is
core evaluation logic and its correctness must be guarded on the default CI path,
not only during a slow live sweep. They make no network call and need no key.

The property under test is *alpha-equivalence*: `_canonical` must treat two
queries that differ only by a consistent bijective variable renaming (the gold's
`?s ?n` vs a real model's `?person ?name`) as EQUAL, while keeping genuinely
different queries (extra projected column, different predicate) DISTINCT. Without
this, a live model that answers correctly but names its variables differently
scores 0% — an artifact, not headroom — and a few-shot phase would "lift" the
score merely by teaching the model to copy the gold's variable names.
"""

from __future__ import annotations

from tests.nl2sparql.eval.runner import _canonical

_PREFIX = "PREFIX : <http://ex.org/>\n"


def test_variable_rename_is_alpha_equivalent() -> None:
    """`?s ?n` vs `?person ?name` — same structure, consistent renaming → EQUAL."""
    gold = _PREFIX + "SELECT ?s ?n WHERE { ?s a :Person ; :name ?n . }"
    renamed = _PREFIX + "SELECT ?person ?name WHERE { ?person a :Person . ?person :name ?name . }"
    assert _canonical(gold) is not None
    assert _canonical(gold) == _canonical(renamed)


def test_extra_projected_column_is_not_equivalent() -> None:
    """A model that adds a column/pattern is genuinely different → NOT equal."""
    gold = _PREFIX + "SELECT ?s WHERE { ?s a :Person ; :age 30 . }"
    with_extra = (
        _PREFIX + "SELECT ?person ?name WHERE { ?person a :Person . ?person :age 30 . "
        "OPTIONAL { ?person :name ?name } }"
    )
    assert _canonical(gold) != _canonical(with_extra)


def test_different_predicate_is_not_equivalent() -> None:
    """Renaming must not collapse structurally different queries."""
    by_age = _PREFIX + "SELECT ?x WHERE { ?x :age ?y }"
    by_name = _PREFIX + "SELECT ?x WHERE { ?x :name ?y }"
    assert _canonical(by_age) != _canonical(by_name)


def test_swapped_variable_names_round_trip_equal() -> None:
    """A pure bijection (swap two names) yields the same canonical form."""
    original = _PREFIX + "SELECT ?a ?b WHERE { ?a :knows ?b }"
    swapped = _PREFIX + "SELECT ?b ?a WHERE { ?b :knows ?a }"
    assert _canonical(original) == _canonical(swapped)


def test_identity_is_stable() -> None:
    q = _PREFIX + "SELECT ?s ?n WHERE { ?s a :Person ; :name ?n . }"
    assert _canonical(q) == _canonical(q)


def test_unparseable_returns_none() -> None:
    """A malformed query returns None (never raises) — the refusal signal."""
    assert _canonical(_PREFIX + "SELECT ?s WHERE { ?s a :Person ") is None
