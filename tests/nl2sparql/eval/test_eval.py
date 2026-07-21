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
import pathlib
import re

import pytest

from tests.nl2sparql.eval.runner import EVAL_DIR, BaselineConfig, run

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


@pytest.mark.skipif(not _RUN_EVAL, reason="set RUN_EVAL=1 to run the NL eval gate")
def test_live_baseline_companion_structural() -> None:
    """No-network structural validation of the live `openai-gpt4o-mini` companion.

    This NEVER makes a network call and NEVER needs a provider key: it only
    inspects the checked-in `baseline.json`. The live companion is folded in by
    a MANUAL, human-reviewed step after a credentialed sweep (AI-SPEC Pitfall 2
    — CI never auto-regenerates it), so before that sweep the entry is absent
    and this test skips, keeping the CI gate green and key-free (SC4).

    Once the companion IS present, validate its shape via `BaselineConfig` and
    assert the reproducibility fields (`model`, `temperature==0.1`,
    `corpus_sha`) are recorded and that the live pass_rate shows genuine
    headroom `0.0 < pass_rate < 1.0` (AI-SPEC SC3 / Critical Failure Mode 2 —
    a near-ceiling live baseline leaves no measurable room for a Phase-7 lift).
    """
    configs = json.loads((EVAL_DIR / "baseline.json").read_text())["configs"]
    if "openai-gpt4o-mini" not in configs:
        pytest.skip(
            "live openai-gpt4o-mini baseline not yet folded into baseline.json "
            "(manual, human-reviewed step after a credentialed sweep; see README.md)"
        )

    entry = configs["openai-gpt4o-mini"]
    # BaselineConfig rejects a malformed companion at parse time (e.g. a
    # pass_rate outside [0, 1] or a missing required aggregate field).
    cfg = BaselineConfig(**entry)

    assert cfg.model, "live baseline must record `model` for reproducibility (Pitfall 6)"
    assert cfg.temperature == 0.1, (
        "live baseline must record temperature=0.1 (hardcoded in "
        f"OpenAICompatibleClient); got {cfg.temperature!r}"
    )
    assert cfg.corpus_sha, (
        "live baseline must pin `corpus_sha` — a pass_rate without a corpus "
        "revision is not reproducible (Critical Failure Mode 4)"
    )
    assert 0.0 < cfg.pass_rate < 1.0, (
        "live baseline must show genuine headroom so a Phase-7 few-shot lift is "
        f"measurable (Critical Failure Mode 2); got {cfg.pass_rate!r}"
    )

    # The companion must track exactly the corpus cases the scripted gate does —
    # a live entry missing cases would silently under-report coverage.
    scripted_cases = set(configs["scripted"]["cases"])
    assert set(cfg.cases) == scripted_cases, (
        "live companion `cases` must cover exactly the tracked corpus cases; "
        f"missing={scripted_cases - set(cfg.cases)!r} "
        f"extra={set(cfg.cases) - scripted_cases!r}"
    )


@pytest.mark.skipif(not _RUN_EVAL, reason="set RUN_EVAL=1 to run the NL eval gate")
def test_ci_gate_only_ever_runs_scripted() -> None:
    """Static, no-network guard: the eval gate may ONLY invoke `run("scripted")`.

    The live provider must never be reachable from the default (key-free) test
    path (AI-SPEC §6 "No network on the default test path"; T-06.2-11). This
    parses this module's own source and asserts every ``run(...)`` call targets
    the ``scripted`` config — so a future edit that wires the live
    ``openai-gpt4o-mini`` config into the CI gate fails loudly here rather than
    silently posting to a provider during CI. It makes no network call and
    needs no key.
    """
    source = pathlib.Path(__file__).read_text()
    run_targets = re.findall(r"""\brun\(\s*["']([^"']+)["']""", source)

    assert run_targets, "expected at least one run('scripted') call in the eval gate"
    non_scripted = sorted({t for t in run_targets if t != "scripted"})
    assert not non_scripted, (
        "the eval gate must only execute the scripted config on the default "
        f"path; found run() calls for non-scripted config(s): {non_scripted}. "
        "Live sweeps run OUT OF BAND (RUN_EVAL=1 + NL2SPARQL_API_KEY, manual) — "
        "see README.md."
    )
