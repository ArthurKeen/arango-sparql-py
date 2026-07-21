"""The `@pytest.mark.eval` regression gate for the NL -> SPARQL harness.

Runs the no-network `scripted` config through the eval runner and asserts
its live pass-rate meets the checked-in `baseline.json` gate — both the
aggregate pass_rate and, per-case, that nothing which passed at baseline
time now regresses.

Gated behind `RUN_EVAL=1` (rule 200: "eval" is slow and never runs from a
plain `pytest` invocation) so the default local/CI fast path stays quick.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.nl2sparql.eval.runner import EVAL_DIR, run

pytestmark = pytest.mark.eval

# `not os.getenv("RUN_EVAL")` is falsy for RUN_EVAL=0 (a non-empty string is
# truthy in Python), so a caller intending "eval off" via RUN_EVAL=0 would
# silently get "eval on" instead. Treat "", "0", "false", "no"
# (case-insensitive) as off.
_RUN_EVAL = os.getenv("RUN_EVAL", "").strip().lower() not in ("", "0", "false", "no")


@pytest.mark.skipif(not _RUN_EVAL, reason="set RUN_EVAL=1 to run the NL eval gate")
def test_scripted_pass_rate_meets_baseline() -> None:
    report = run("scripted")
    baseline = json.loads((EVAL_DIR / "baseline.json").read_text())["configs"]["scripted"]

    # Aggregate regression gate.
    assert report.pass_rate >= baseline["pass_rate"] - 1e-9, (
        f"scripted pass_rate regressed: live={report.pass_rate!r} "
        f"baseline={baseline['pass_rate']!r}"
    )

    # Per-case regression gate: any case that passed at baseline time must
    # still pass now, catching a swap that keeps the aggregate rate steady
    # while silently breaking a different case.
    live_by_name = {c.name: c.passed for c in report.cases}
    for name, was_passing in baseline["cases"].items():
        if was_passing:
            assert live_by_name.get(name) is True, (
                f"case {name!r} regressed: previously passing per baseline.json, "
                f"now {live_by_name.get(name)!r}"
            )

    # New-case gate: a case added to corpus.yml that isn't yet tracked in
    # baseline.json can't hide a regression behind aggregate dilution (the
    # per-case loop above only ever iterates *known* baseline cases). Every
    # untracked case must pass before it's added to the corpus, forcing the
    # author to consciously add it to baseline.json once green.
    corpus_names = {c.name for c in report.cases}
    baseline_names = set(baseline["cases"])
    new_names = corpus_names - baseline_names
    for name in new_names:
        assert live_by_name.get(name) is True, (
            f"new case {name!r} must pass before it's added to baseline.json "
            f"(got {live_by_name.get(name)!r})"
        )


@pytest.mark.skipif(not _RUN_EVAL, reason="set RUN_EVAL=1 to run the NL eval gate")
def test_scripted_headroom_invariant() -> None:
    """Scripted headroom SENTINEL + the deliberate-near-miss per-case guard.

    The `0.0 < pass_rate < 1.0` bound is a SENTINEL only: it proves the judge
    CAN fail something on the no-network, key-free path — it is NOT a
    difficulty/headroom measure (one near-miss in a ~25-case corpus is ≈ 0.96,
    so the aggregate bound stays weak as the corpus grows). Genuine headroom is
    a LIVE-config property (Plan 04).

    The REAL guard is the per-case assertion that `deliberate-near-miss` reports
    `passed is False` (AI-SPEC SC2): it fails if the near-miss is removed or
    flipped, so the sentinel cannot be trivially "fixed" by adding passing
    cases — do NOT delete the near-miss to make this go green.
    """
    report = run("scripted")

    # SENTINEL: the judge must be able to both pass and fail something.
    assert 0.0 < report.pass_rate < 1.0, (
        f"scripted pass_rate must stay strictly in (0, 1) as a headroom "
        f"sentinel; got {report.pass_rate!r}"
    )

    # REAL GUARD (AI-SPEC SC2): the deliberate near-miss must still fail.
    live_by_name = {c.name: c.passed for c in report.cases}
    assert live_by_name.get("deliberate-near-miss") is False, (
        "deliberate-near-miss must report passed=False — it is the real "
        "regression guard keeping baseline.json non-trivial (AI-SPEC SC2); "
        f"got {live_by_name.get('deliberate-near-miss')!r}"
    )
